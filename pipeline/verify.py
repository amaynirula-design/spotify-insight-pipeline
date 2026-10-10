"""Stage 3 - Verify. A separate model task, with its own instructions, re-labels a declared random
sample from the original text only (it never sees the enricher's answer). Code compares the two."""

import csv
import hashlib
import json
from collections import Counter

from . import labels
from .control import Rejected, Runner
from .util import chunks, write_json, write_text_atomic


def sample_ids(db, cfg, input_id):
    """Declared sample: completed review IDs ordered by SHA-256(seed:review_id), first n."""
    ids = db.completed_ids(input_id, cfg.label_config)
    seed = cfg.raw["verify_sample"]["seed"]
    n = cfg.verify_sample_size(len(ids))
    return sorted(ids, key=lambda rid: hashlib.sha256(f"{seed}:{rid}".encode()).hexdigest())[:n]


def parse_verify(output, refs):
    good = {}
    for line in output.splitlines():
        parts = [p.strip() for p in line.strip().split("|")]
        if len(parts) < 4 or parts[0] not in refs or parts[0] in good:
            continue
        topic, intent = parts[1].lower(), parts[2].lower()
        if topic in labels.TOPICS and intent in labels.INTENTS and parts[3] in "12345" and len(parts[3]) == 1:
            good[parts[0]] = (topic, intent, int(parts[3]))
    return good


def compare(enriched, verified):
    """enriched/verified: {review_id: {topic, intent, severity}} -> agreement summary and disagreements."""
    shared = [rid for rid in verified if rid in enriched]
    stats, disagreements, pairs = Counter(n=len(shared)), [], Counter()
    for rid in shared:
        e, v = enriched[rid], verified[rid]
        fields = [f for f in ("topic", "intent", "severity") if e[f] != v[f]]
        for f in ("topic", "intent", "severity"):
            stats[f + "_agree"] += e[f] == v[f]
        stats["all_agree"] += not fields
        stats["severity_abs_error"] += abs(e["severity"] - v["severity"])
        for f in fields:
            pairs[f"{f}: enricher={e[f]} verifier={v[f]}"] += 1
        if fields:
            disagreements.append({"review_id": rid, "fields": fields, "enricher": e, "verifier": v})
    n = max(len(shared), 1)
    summary = {"compared": len(shared),
               "topic_agreement": round(stats["topic_agree"] / n, 4), "intent_agreement": round(stats["intent_agree"] / n, 4),
               "severity_exact_agreement": round(stats["severity_agree"] / n, 4),
               "severity_mean_abs_difference": round(stats["severity_abs_error"] / n, 4),
               "all_three_agree": round(stats["all_agree"] / n, 4), "disagreeing_records": len(disagreements),
               "most_common_disagreements": pairs.most_common(15)}
    return summary, disagreements


def run_verify(db, cfg, provider, input_id, run_dir, invocation, budget_usd=None, log=print):
    vc, role, lc = cfg.stage_config("verify"), cfg.role("verify"), cfg.label_config
    sample = sample_ids(db, cfg, input_id)
    have = {r["review_id"] for r in db.q("SELECT review_id FROM verify WHERE input_id=? AND verify_config=?", (input_id, vc))}
    todo = [rid for rid in sample if rid not in have]
    runner = Runner(db, cfg, provider, input_id, invocation, budget_usd=budget_usd)
    texts = {}
    for part in chunks(todo, 500):
        marks = ",".join("?" * len(part))
        for r in db.q(f"SELECT review_id, review_text FROM reviews WHERE input_id=? AND review_id IN ({marks})", (input_id, *part)):
            texts[r["review_id"]] = r["review_text"]
    events = Counter()

    def ask(ids, kind):
        refs = {f"{n:02d}": rid for n, rid in enumerate(ids, 1)}
        input_text = "\n".join(f"{ref}\t{json.dumps(texts[rid][:labels.MAX_TEXT_CHARS], ensure_ascii=False)}" for ref, rid in refs.items())
        answered = {}

        def on_success(conn, resp, call_id):
            good = parse_verify(resp["text"], refs)
            conn.executemany("INSERT OR REPLACE INTO verify VALUES(?,?,?,?,?,?,?)",
                             [(input_id, refs[ref], vc, t, i, s, call_id) for ref, (t, i, s) in good.items()])
            answered.update(good)
        try:
            runner.call("verify", input_text, ids, attempt_kind=kind, on_success=on_success)
        except Rejected:
            events["rejected_requests"] += 1
        return [rid for ref, rid in refs.items() if ref not in answered]

    for batch in chunks(todo, role["batch_size"]):
        missing = ask(batch, "first")
        if missing:
            events["retried_items"] += len(missing)
            events["unanswered_after_retry"] += len(ask(missing, "retry"))
    log(f"[verify] sample {len(sample)} of the completed rows (seeded), {len(todo)} newly sent, "
        f"{events['unanswered_after_retry']} unanswered after one retry")

    enriched, verified, text_of = {}, {}, {}
    for part in chunks(sample, 500):
        marks = ",".join("?" * len(part))
        for r in db.q(f"""SELECT r.review_id, r.review_text, s.topic, s.intent, s.severity, s.code, s.needs_review,
                                 v.topic AS vt, v.intent AS vi, v.severity AS vs
                          FROM reviews r JOIN results s ON s.text_key=r.text_key AND s.label_config=?
                          LEFT JOIN verify v ON v.input_id=r.input_id AND v.review_id=r.review_id AND v.verify_config=?
                          WHERE r.input_id=? AND r.review_id IN ({marks})""", (lc, vc, input_id, *part)):
            enriched[r["review_id"]] = {"topic": r["topic"], "intent": r["intent"], "severity": r["severity"],
                                        "code": r["code"], "needs_review": bool(r["needs_review"])}
            text_of[r["review_id"]] = r["review_text"]
            if r["vt"]:
                verified[r["review_id"]] = {"topic": r["vt"], "intent": r["vi"], "severity": r["vs"]}
    summary, disagreements = compare(enriched, verified)
    flagged = [rid for rid in verified if enriched[rid]["needs_review"]]
    disagree_ids = {d["review_id"] for d in disagreements}
    summary.update({
        "stage": "verify", "verify_config": vc, "label_config": lc, "sample_rule": cfg.raw["verify_sample"],
        "sample_size": len(sample), "verifier_answers": len(verified), "verifier_unanswered": len(sample) - len(verified),
        "needs_review_flagged_in_sample": len(flagged),
        "needs_review_flag_also_disagreed": len([rid for rid in flagged if rid in disagree_ids]),
        "note": "Verifier labels are a second opinion from a model, not ground truth; they never overwrite enrichment labels.",
    })
    write_json(run_dir / "verify_report.json", summary)
    write_text_atomic(run_dir / "verify_predictions.jsonl", "".join(
        json.dumps({"review_id": rid, "verifier": verified.get(rid), "enricher": enriched[rid]}, ensure_ascii=False) + "\n" for rid in sample))
    lines = [["review_id", "fields", "enricher_topic", "verifier_topic", "enricher_intent", "verifier_intent",
              "enricher_severity", "verifier_severity", "enricher_code", "review_text"]]
    for d in disagreements:
        e, v = d["enricher"], d["verifier"]
        lines.append([d["review_id"], "+".join(d["fields"]), e["topic"], v["topic"], e["intent"], v["intent"],
                      e["severity"], v["severity"], e["code"], text_of[d["review_id"]][:300]])
    with (run_dir / "verify_disagreements.csv").open("w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(lines)
    log(f"[verify] agreement topic {summary['topic_agreement']:.1%}, intent {summary['intent_agreement']:.1%}, "
        f"severity {summary['severity_exact_agreement']:.1%} (n={summary['compared']})")
    return summary
