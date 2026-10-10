"""Saved outputs and the standard grading/ folder (GRADING_CONTRACT.md). Code only, no model calls."""

import csv
import gzip
import importlib.util
import json
import shutil
from collections import Counter
from pathlib import Path

from . import labels, rank
from .config import billed_units, BILLING_ITEMS
from .util import ROOT, now_iso, read_json, write_json

RECORDS_SQL = """
SELECT r.review_id, r.review_text, r.source_sha256, r.text_key, r.review_timestamp, r.review_rating,
       s.status, s.reason, s.source_review_id, s.code, s.topic, s.intent, s.sentiment, s.severity, s.needs_review,
       s.evidence_quote, s.quote_origin, s.entities, s.notes, s.attempts, o.review_id AS origin_present
FROM reviews r
LEFT JOIN results s ON s.text_key = r.text_key AND s.label_config = ?
LEFT JOIN reviews o ON o.input_id = r.input_id AND o.review_id = s.source_review_id
WHERE r.input_id = ? ORDER BY r.row_num
"""


def iter_records(db, input_id, label_config):
    """Exactly one final record per source ID: completed, or quarantined with a reason."""
    with db.lock:
        cursor = db.conn.execute(RECORDS_SQL, (label_config, input_id))
        while True:
            rows = cursor.fetchmany(20000)
            if not rows:
                break
            for r in rows:
                base = {"review_id": r["review_id"], "source_sha256": r["source_sha256"]}
                if r["text_key"] is None:
                    yield dict(base, status="quarantined", reason="empty_review_text"), None
                elif r["status"] is None:
                    yield dict(base, status="quarantined", reason="not_classified:pending_when_exported"), None
                elif r["status"] != "completed":
                    yield dict(base, status="quarantined", reason=r["reason"] or "unknown"), None
                elif r["origin_present"] is None:
                    yield dict(base, status="quarantined", reason="cache_source_outside_this_input"), None
                else:
                    rec = dict(base, status="completed", topic=r["topic"], intent=r["intent"], sentiment=r["sentiment"],
                               severity=r["severity"], entities=json.loads(r["entities"]), evidence_quote=r["evidence_quote"],
                               needs_review=bool(r["needs_review"]), label_config=label_config)
                    if r["source_review_id"] != r["review_id"]:
                        rec["cache_source_id"] = r["source_review_id"]
                    problems = labels.validate_record(rec, r["review_text"])
                    if problems:
                        yield dict(base, status="quarantined", reason="failed_export_validation:" + "+".join(problems)), None
                    else:
                        extra = {"subtopic": r["code"], "issue_id": f"{r['topic']}.{r['code']}", "quote_origin": r["quote_origin"],
                                 "notes": r["notes"], "attempts": r["attempts"], "review_month": r["review_timestamp"][:7]}
                        yield rec, extra


def open_out(path, compress):
    return gzip.open(str(path) + ".gz", "wt", encoding="utf-8", compresslevel=6) if compress else path.open("w", encoding="utf-8")


def ingestion_profile(full_path):
    spec = importlib.util.spec_from_file_location("check_submission", ROOT / "check_submission.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.profile(full_path)


def call_rows(db, input_id, label_config):
    """Call log rows relevant to this input: its own verify/group/memo calls plus every enrichment
    call under this label_config whose review IDs all belong to this input (earlier checkpoint runs
    on subsets of the same file are the cache originals and keep their evidence)."""
    ids = {r["review_id"] for r in db.q("SELECT review_id FROM reviews WHERE input_id=?", (input_id,))}
    before = db.get_meta(f"ckpt_before|{input_id}|{label_config}")
    for r in db.q("SELECT * FROM calls ORDER BY call_id"):
        review_ids = json.loads(r["review_ids"])
        if r["role"] == "enrich":
            if r["stage_config"] != label_config or not all(x in ids for x in review_ids):
                continue
        elif r["input_id"] != input_id:
            continue
        phase = "resume" if before and r["started_at"] > before["time"] else "initial"
        yield r, review_ids, phase


def run_export(db, cfg, input_id, run_dir, grading_dir=None, full_path=None, log=print):
    lc = cfg.label_config
    info = db.input_row(input_id)
    grading = Path(grading_dir) if grading_dir else run_dir / "grading"
    grading.mkdir(parents=True, exist_ok=True)
    compress = info["n_rows"] > 20000
    for name in ("records.jsonl", "calls.jsonl"):
        for stale in (grading / name, grading / (name + ".gz")):
            stale.unlink(missing_ok=True)
    for stale in (run_dir / "enriched.jsonl", run_dir / "enriched.jsonl.gz"):
        stale.unlink(missing_ok=True)

    status = Counter()
    with open_out(grading / "records.jsonl", compress) as rec_f, open_out(run_dir / "enriched.jsonl", compress) as enr_f, \
            (run_dir / "quarantine.jsonl").open("w", encoding="utf-8") as quar_f:
        for rec, extra in iter_records(db, input_id, lc):
            rec_f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            if extra is None:
                status["quarantined:" + rec["reason"].split(":")[0]] += 1
                quar_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            else:
                status["completed"] += 1
                status["cache_reuse"] += "cache_source_id" in rec
                enr_f.write(json.dumps({**rec, **extra}, ensure_ascii=False, separators=(",", ":")) + "\n")

    ranking = rank.compute(db, input_id, lc)
    rank.write_membership(db, input_id, lc, grading / "membership.csv")
    rank.write_ranking(ranking, grading / "ranking.csv")
    claims_src = run_dir / "claims.csv"
    if claims_src.exists():
        # Keep only claims that still match the saved ranking (they always do unless records changed after the memo).
        current = {(r["issue_id"], m): str(r[m]) for r in ranking for m in ("complaint_count", "severity_sum", "mean_severity", "priority_score")}
        with claims_src.open(encoding="utf-8", newline="") as f:
            claims = list(csv.DictReader(f))
        stale = [c["claim_id"] for c in claims if current.get((c["issue_id"], c["metric"])) != c["value"]]
        if stale:
            log(f"[export] WARNING: {len(stale)} memo claims no longer match the ranking; rerun the memo stage ({stale[:5]})")
        shutil.copyfile(claims_src, grading / "claims.csv")
    for name in ("checkpoint_before.json", "checkpoint_after.json"):
        if (run_dir / name).exists():
            snap = read_json(run_dir / name)
            write_json(grading / name, {"completed_ids": snap["completed_ids"]})

    usage = Counter()
    by_role = {}
    with open_out(grading / "calls.jsonl", compress) as calls_f, (run_dir / "run_log.jsonl").open("w", encoding="utf-8") as log_f:
        for r, review_ids, phase in call_rows(db, input_id, lc):
            outcome = "succeeded" if r["outcome"] == "succeeded" else "failed"  # 'unknown' (interrupted) is exported as failed
            calls_f.write(json.dumps({
                "request_id": r["request_id"] or f"local-{r['call_id']}", "role": r["role"], "review_ids": review_ids,
                "model": r["model"], "phase": phase, "outcome": outcome, "label_config": r["stage_config"],
                "input_tokens": r["input_tokens"] or 0, "output_tokens": r["output_tokens"] or 0}, separators=(",", ":")) + "\n")
            log_f.write(json.dumps({
                "call_id": r["call_id"], "request_id": r["request_id"], "invocation": r["invocation"], "role": r["role"],
                "phase": phase, "attempt_kind": r["attempt_kind"], "provider": r["provider"], "model": r["model"],
                "effort": r["effort"], "tier": r["tier"], "stage_config": r["stage_config"], "n_items": r["n_items"],
                "outcome": r["outcome"], "error": r["error"], "input_tokens": r["input_tokens"],
                "cached_input_tokens": r["cached_input_tokens"], "output_tokens": r["output_tokens"],
                "reasoning_tokens": r["reasoning_tokens"], "usage_source": r["usage_source"], "started_at": now_iso(r["started_at"]),
                "duration_s": None if r["duration_s"] is None else round(r["duration_s"], 3), "worker": r["worker"],
                "reserved_usd": r["reserved_usd"], "cost_usd": r["cost_usd"], "artifacts": r["artifacts"]}, separators=(",", ":")) + "\n")
            role = by_role.setdefault(r["role"], Counter())
            role["attempts"] += 1
            role[r["outcome"]] += 1
            role["retry_attempts"] += r["attempt_kind"] != "first"
            role["cost_usd"] += r["cost_usd"] or 0
            role["unresolved_reserved_usd"] += (r["reserved_usd"] or 0) if r["cost_usd"] is None else 0
            role["call_seconds_sum"] += r["duration_s"] or 0
            for item, units in billed_units({"input_tokens": r["input_tokens"], "cached_input_tokens": r["cached_input_tokens"],
                                             "output_tokens": r["output_tokens"]}).items():
                role[item + "_tokens"] += units
            role["reasoning_tokens"] += r["reasoning_tokens"] or 0

    write_json(grading / "run.json", {
        "version": "a5-audit-v1", "analysis_count": info["n_rows"], "analysis_sha256": info["file_sha256"],
        "classification_input_fields": ["review_text"], "allow_multi_issue": False,
        "label_config": lc, "exported_at": now_iso(), "input_file": Path(info["path"]).name})
    full = Path(full_path) if full_path else Path(info["path"])
    write_json(grading / "ingestion.json", ingestion_profile(full))

    counts = db.status_counts(input_id, lc)
    stage_log = run_dir / "stage_log.jsonl"
    stages = [json.loads(line) for line in stage_log.read_text(encoding="utf-8").splitlines()] if stage_log.exists() else []
    summary = {
        "run_dir": run_dir.name, "input_file": Path(info["path"]).name, "input_sha256": info["file_sha256"],
        "label_config": lc, "provider": cfg.provider, "tier": cfg.tier, "exported_at": now_iso(),
        "stage_configs": {name: cfg.stage_config(name) for name in ("enrich", "verify", "group", "memo")},
        "limits": cfg.limits, "counts": counts, "export_status_counts": dict(status),
        "calls_by_role": {role: {k: (round(v, 6) if isinstance(v, float) else v) for k, v in c.items()} for role, c in by_role.items()},
        "api_cost_usd_measured": round(sum(c["cost_usd"] for c in by_role.values()), 6),
        "api_cost_usd_unresolved_estimate": round(sum(c["unresolved_reserved_usd"] for c in by_role.values()), 6),
        "cost_note": "cost_usd = provider-reported usage x cost/rates.csv; calls without usage are listed as unresolved reserved estimates",
        "stage_log": stages,
        "is_complete": counts["pending"] == 0 and counts["quarantined_nonempty"] == 0,
    }
    write_json(run_dir / "run_summary.json", summary)
    log(f"[export] {dict(status)} -> {grading}")
    return {"stage": "export", "grading_dir": str(grading), "status_counts": dict(status)}
