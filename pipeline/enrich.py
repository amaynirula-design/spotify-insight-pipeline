"""Stage 2 - Classify. The enricher model reads at most 50 distinct review texts per request and
returns one compact line per review; code validates every returned ref, derives the topic from the
code, checks the quote is an exact source substring, extracts entities deterministically and saves
each batch atomically. Invalid items get exactly one retry in small batches, then are saved with
a deterministic quote (labels valid, quote unverifiable) or quarantined with a reason."""

import json
import threading
import time
from collections import Counter

from . import labels
from .control import BudgetExceeded, Deferred, Rejected, Runner
from .util import chunks, now_iso, write_json


class Fatal(Exception):
    """Configuration-level failure (bad key, model or request shape): stop instead of quarantining."""


def build_input(items):
    lines, refs = [], {}
    for n, item in enumerate(items, 1):
        ref, text = f"{n:02d}", item["review_text"]
        needs_quote = len(text) > labels.SHORT_QUOTE_MAX
        lines.append(f"{ref}{'q' if needs_quote else ''}\t{json.dumps(text[:labels.MAX_TEXT_CHARS], ensure_ascii=False)}")
        refs[ref] = {"text_key": item["text_key"], "review_id": item["review_id"], "text": text, "needs_quote": needs_quote}
    return "\n".join(lines), refs


def parse_output(output, refs):
    """-> (good {ref: labels}, bad {ref: (reason, labels or None)}, stray_ref_count)."""
    good, bad, seen, stray = {}, {}, set(), 0
    for line in output.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|", 6)]
        ref = parts[0].strip("[]`* ").rstrip("q")
        if ref not in refs:
            stray += 1  # a returned ID that was never sent is rejected
            continue
        if ref in seen:
            good.pop(ref, None)
            bad[ref] = ("duplicate_ref", None)
            continue
        seen.add(ref)
        if len(parts) < 6:
            bad[ref] = ("too_few_fields", None)
            continue
        code, intent = parts[1].lower(), parts[2].lower()
        try:
            sentiment, severity, flag = int(parts[3]), int(parts[4]), int(parts[5])
        except ValueError:
            bad[ref] = ("non_integer_field", None)
            continue
        if code not in labels.SUBTOPICS:
            bad[ref] = ("unknown_code", None)
        elif intent not in labels.INTENTS:
            bad[ref] = ("unknown_intent", None)
        elif not (-2 <= sentiment <= 2 and 1 <= severity <= 5 and flag in (0, 1)):
            bad[ref] = ("value_out_of_range", None)
        else:
            lab = {"code": code, "intent": intent, "sentiment": sentiment, "severity": severity, "flag": flag}
            item = refs[ref]
            if item["needs_quote"]:
                raw = parts[6] if len(parts) > 6 else ""
                found = labels.locate_quote(item["text"], raw)
                if found is None:
                    bad[ref] = ("quote_not_in_text", lab)
                    continue
                lab["evidence_quote"], lab["quote_origin"] = found, ("model" if found == raw else "model_repaired")
            else:
                lab["evidence_quote"], lab["quote_origin"] = item["text"], "full_text"
            good[ref] = lab
    for ref in refs:
        if ref not in seen:
            bad[ref] = ("missing_from_output", None)
    return good, bad, stray


def completed_row(lab, item, label_config, call_id, attempts, extra_note=None):
    """Map validated model fields into the shared schema. Severity is reconciled with intent by the
    shared definitions (praise/unclear/request = 1, complaint >= 2); any such change is noted and
    flagged for review rather than applied silently."""
    notes = [extra_note] if extra_note else []
    severity, intent, needs_review = lab["severity"], lab["intent"], bool(lab["flag"])
    if intent in ("praise", "unclear", "request") and severity != 1:
        notes.append(f"severity {severity}->1 (intent {intent})")
        severity, needs_review = 1, True
    elif intent == "complaint" and severity == 1:
        notes.append("severity 1->2 (complaint)")
        severity, needs_review = 2, True
    if lab["quote_origin"] == "fallback_prefix":
        needs_review = True
    return (item["text_key"], label_config, "completed", None, item["review_id"], call_id, attempts,
            lab["code"], labels.SUBTOPICS[lab["code"]][0], intent, lab["sentiment"] / 2, severity, int(needs_review),
            lab["evidence_quote"], lab["quote_origin"], json.dumps(labels.extract_entities(item["text"])),
            "; ".join(notes) or None, time.time())


def quarantine_row(item, label_config, call_id, attempts, reason):
    return (item["text_key"], label_config, "quarantined", reason, item["review_id"], call_id, attempts,
            None, None, None, None, None, None, None, None, None, None, time.time())


INSERT = "INSERT OR IGNORE INTO results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"


def attempt(runner, items, kind, worker, stats, final, save_input):
    """One model request for `items`. Returns the items that still need the single retry."""
    lc = runner.cfg.label_config
    input_text, refs = build_input(items)
    attempts = 2 if final else 1
    leftover = []

    def on_success(conn, resp, call_id):
        good, bad, stray = parse_output(resp["text"], refs)
        rows = [completed_row(lab, refs[ref], lc, call_id, attempts) for ref, lab in good.items()]
        local = Counter(completed=len(good), stray_refs=stray)
        for ref, (reason, lab) in bad.items():
            local["invalid_" + reason] += 1
            if not final:
                leftover.append({"text_key": refs[ref]["text_key"], "review_id": refs[ref]["review_id"],
                                 "review_text": refs[ref]["text"]})
            elif lab is not None:  # labels valid, quote could not be verified twice
                lab["evidence_quote"], lab["quote_origin"] = labels.fallback_quote(refs[ref]["text"]), "fallback_prefix"
                rows.append(completed_row(lab, refs[ref], lc, call_id, attempts, "model quote not found in source twice"))
                local["completed"] += 1
                local["fallback_quote"] += 1
            else:
                rows.append(quarantine_row(refs[ref], lc, call_id, attempts, "invalid_output_after_retry:" + reason))
                local["quarantined"] += 1
        conn.executemany(INSERT, rows)
        with stats["lock"]:
            stats["n"].update(local)
            stats["ok_calls"] += 1

    try:
        runner.call("enrich", input_text, [it["review_id"] for it in items], attempt_kind=kind, worker=worker,
                    on_success=on_success, save_input=save_input)
    except Rejected as e:
        if stats["ok_calls"] == 0:
            raise Fatal(f"The provider rejected the request before any call succeeded: {e}") from None
        if not final:
            return list(items)  # isolate the offending text in the small-batch retry
        with runner.db.tx() as c:
            c.executemany(INSERT, [quarantine_row({"text_key": it["text_key"], "review_id": it["review_id"]}, lc, None, attempts,
                                                  "provider_rejected_input:" + str(e)[:120]) for it in items])
        with stats["lock"]:
            stats["n"]["quarantined"] += len(items)
    return leftover


def pack(pending, batch_size, max_chars):
    batches, current, size = [], [], 0
    for row in pending:
        item = {"text_key": row["text_key"], "review_id": row["review_id"], "review_text": row["review_text"]}
        length = min(len(item["review_text"]), labels.MAX_TEXT_CHARS)
        if current and (len(current) >= batch_size or size + length > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += length
    if current:
        batches.append(current)
    return batches


def run_enrich(db, cfg, provider, input_id, run_dir, invocation, workers=1, budget_usd=None,
               stop_after_batches=None, max_seconds=None, stop=None, log=print):
    role, limits, lc = cfg.role("enrich"), cfg.limits, cfg.label_config
    assert role["batch_size"] <= 50, "Enrichment requests are limited to 50 reviews"
    if workers > limits["max_workers"]:
        raise SystemExit(f"--workers {workers} exceeds limits.max_workers={limits['max_workers']} in the config")
    stop = stop or threading.Event()
    unknown = db.reconcile_in_flight()
    key_before, key_after = f"ckpt_before|{input_id}|{lc}", f"ckpt_after|{input_id}|{lc}"
    phase = "resume" if db.get_meta(key_before) else "initial"
    before = db.status_counts(input_id, lc)
    pending = db.pending_texts(input_id, lc)
    batches = pack(pending, role["batch_size"], role["max_batch_chars"])
    runner = Runner(db, cfg, provider, input_id, invocation, phase=phase, stop=stop, budget_usd=budget_usd)
    spent_before = runner.budget.spent
    stats = {"lock": threading.Lock(), "n": Counter(), "ok_calls": 0}
    state = {"done": 0, "deferred_streak": 0, "stop_reason": None}
    save_input = db.input_row(input_id)["n_rows"] <= 20000
    queue, qlock, started = iter(batches), threading.Lock(), time.time()
    log(f"[enrich] phase={phase} label_config={lc}")
    log(f"[enrich] {before['n_rows']} rows, {before['distinct_texts']} distinct texts, {len(pending)} pending texts "
        f"in {len(batches)} requests (<= {role['batch_size']} reviews each), workers={workers}, "
        f"budget cap ${runner.budget.cap:.2f} (already spent ${spent_before:.4f})")

    def halt(reason):
        with qlock:
            state["stop_reason"] = state["stop_reason"] or reason
        stop.set()

    def work(worker):
        while not stop.is_set():
            with qlock:
                batch = next(queue, None)
            if batch is None:
                return
            try:
                leftover = attempt(runner, batch, "first", worker, stats, final=limits["invalid_output_retries"] < 1, save_input=save_input)
                for chunk in chunks(leftover, role["retry_batch_size"]):
                    attempt(runner, chunk, "retry", worker, stats, final=True, save_input=save_input)
                with qlock:
                    state["deferred_streak"] = 0
            except BudgetExceeded as e:
                return halt(f"budget_cap: {e}")
            except Fatal as e:
                return halt(f"fatal: {e}")
            except Deferred as e:
                with stats["lock"]:
                    stats["n"]["deferred_batches"] += 1
                with qlock:
                    state["deferred_streak"] += 1
                    streak = state["deferred_streak"]
                if streak >= limits["max_consecutive_deferred"]:
                    return halt(f"provider_unavailable: {streak} consecutive requests failed after retries ({e})")
            with qlock:
                state["done"] += 1
                done = state["done"]
            if done % 20 == 0 or done == len(batches):
                rate = stats["n"]["completed"] / max(time.time() - started, 1e-9)
                log(f"[enrich] {done}/{len(batches)} requests, {stats['n']['completed']} texts labelled, "
                    f"{rate:.1f} texts/s, spent ${runner.budget.spent:.4f}")
            if stop_after_batches and done >= stop_after_batches:
                return halt(f"stop_after_batches={stop_after_batches} (simulated interruption)")
            if max_seconds and time.time() - started > max_seconds:
                return halt(f"max_seconds={max_seconds}")
            n = stats["n"]
            if done >= 5 and n["quarantined"] > 0.2 * (n["completed"] + n["quarantined"]):
                return halt("quarantine rate above 20%: systematic invalid output, stopping for inspection")

    threads = [threading.Thread(target=work, args=(w,), daemon=True) for w in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        while t.is_alive():
            t.join(0.2)  # short joins keep Ctrl-C responsive; in-flight requests finish and are saved
    if stop.is_set() and not state["stop_reason"]:
        state["stop_reason"] = "interrupted (signal)"

    after = db.status_counts(input_id, lc)
    checkpoint = None
    if after["pending"] and not db.get_meta(key_before) and after["completed"]:
        checkpoint = "checkpoint_before.json"
        write_json(run_dir / checkpoint, {"completed_ids": db.completed_ids(input_id, lc), "created_at": now_iso(),
                                          "label_config": lc, "note": "saved when the first run stopped with work pending"})
        db.set_meta(key_before, {"time": time.time(), "completed": after["completed"]})
    elif db.get_meta(key_before) and not db.get_meta(key_after) and after["completed"] > db.get_meta(key_before)["completed"]:
        checkpoint = "checkpoint_after.json"
        write_json(run_dir / checkpoint, {"completed_ids": db.completed_ids(input_id, lc), "created_at": now_iso(),
                                          "label_config": lc, "note": "saved at the end of the first resumed run"})
        db.set_meta(key_after, {"time": time.time(), "completed": after["completed"]})

    summary = {
        "stage": "enrich", "invocation": invocation, "phase": phase, "label_config": lc, "workers": workers,
        "wall_seconds": round(time.time() - started, 3), "stop_reason": state["stop_reason"],
        "requests_planned": len(batches), "first_pass_requests_done": state["done"],
        "pending_texts_at_start": len(pending), "interrupted_calls_reconciled": unknown,
        "counts_before": before, "counts_after": after, "events": dict(stats["n"]),
        "spent_usd_this_invocation": round(runner.budget.spent - spent_before, 6),
        "spent_usd_total_ledger": round(runner.budget.spent, 6), "budget_cap_usd": runner.budget.cap,
        "checkpoint_written": checkpoint,
    }
    log(f"[enrich] done: completed rows {before['completed']} -> {after['completed']}, pending {after['pending']}, "
        f"quarantined nonempty {after['quarantined_nonempty']}, stop_reason={state['stop_reason']}")
    return summary


def requeue_quarantined(db, cfg):
    """Explicit operator action after fixing a systematic failure: quarantined texts become pending again."""
    with db.tx() as c:
        cur = c.execute("DELETE FROM results WHERE label_config=? AND status='quarantined'", (cfg.label_config,))
        return cur.rowcount
