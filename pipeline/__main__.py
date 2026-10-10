"""Spotify review insight pipeline - orchestrator CLI.

  python -m pipeline run INPUT.csv [--provider mock] [--workers N] [--budget-usd X] [--stages a,b]
  python -m pipeline status INPUT.csv
  python -m pipeline rerank --grading grading          # ranking from saved files, no model, no DB
  python -m pipeline requeue-quarantined INPUT.csv

Stages (fixed order, chosen by code): ingest -> enrich -> verify -> group -> rank -> memo -> export.
Model roles: enrich, verify, group, memo. Everything else is code and SQL.
"""

import argparse
import csv
import gzip
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import enrich, export, group, ingest, memo, rank, verify
from .config import Config
from .db import DB
from .providers import make_provider
from .util import ROOT, load_dotenv, now_iso

STAGES = ("ingest", "enrich", "verify", "group", "rank", "memo", "export")


def run_name(path, cfg):
    role = cfg.role("enrich")
    return f"{Path(path).stem}__{role['model']}-{role['effort']}-{cfg.label_config.rsplit('|', 1)[-1]}"


def cmd_run(args):
    cfg = Config(args.config, provider=args.provider)
    db = DB(args.db)
    stages = [s.strip() for s in args.stages.split(",")] if args.stages else list(STAGES)
    unknown = [s for s in stages if s not in STAGES]
    if unknown:
        raise SystemExit(f"Unknown stages {unknown}; choose from {STAGES}")
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "runs" / run_name(args.input, cfg)
    run_dir.mkdir(parents=True, exist_ok=True)
    invocation = f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    stop = threading.Event()

    def on_signal(signum, frame):
        if stop.is_set():
            raise KeyboardInterrupt
        print("\n[run] interrupt received: finishing in-flight requests and saving progress (press again to abort)")
        stop.set()
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    if sys.platform == "darwin" and shutil.which("caffeinate"):
        # Keep macOS from idle-sleeping mid-run (a sleeping laptop freezes in-flight requests).
        # This cannot override a closed lid on battery: keep the lid open and the charger connected.
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = time.time()
    print(f"[run] invocation {invocation}  provider={cfg.provider}  run_dir={run_dir}")
    input_id, report = ingest.ingest(db, args.input)  # idempotent: reuses saved rows when the file hash is known
    provider = None
    need_model = any(s in stages for s in ("enrich", "verify", "group", "memo"))
    if need_model:
        provider = make_provider(cfg)

    def record(summary, t0):
        summary = dict(summary, invocation=invocation, wall_seconds=summary.get("wall_seconds", round(time.time() - t0, 3)),
                       finished_at=now_iso())
        with (run_dir / "stage_log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
        return summary

    incomplete = False
    for stage in STAGES:
        if stage not in stages:
            continue
        t0 = time.time()
        if stage == "ingest":
            ingest.save_reports(run_dir, report, cfg)
            scope = report["classification_scope"]
            print(f"[ingest] {report['counts']['records']} rows, {report['counts']['empty_review_text']} empty texts, "
                  f"{scope['distinct_nonempty_texts']} distinct nonempty texts, sha256 {report['file_sha256'][:12]}…")
            record({"stage": "ingest", "counts": report["counts"], "classification_scope": {k: v for k, v in scope.items() if k != "largest_duplicate_groups"}}, t0)
        elif stage == "enrich":
            s = record(enrich.run_enrich(db, cfg, provider, input_id, run_dir, invocation, workers=args.workers,
                                         budget_usd=args.budget_usd, stop_after_batches=args.stop_after_batches,
                                         max_seconds=args.max_seconds, stop=stop), t0)
            incomplete = s["counts_after"]["pending"] > 0
            if incomplete and not args.allow_partial:
                print(f"[run] enrichment stopped with {s['counts_after']['pending']} rows pending ({s['stop_reason']}). "
                      "Progress is saved; rerun the same command to resume. Later model stages were skipped.")
                stages = [x for x in stages if x == "export"]
        elif stage == "verify":
            record(verify.run_verify(db, cfg, provider, input_id, run_dir, invocation, budget_usd=args.budget_usd), t0)
        elif stage == "group":
            record(group.run_group(db, cfg, provider, input_id, run_dir, invocation, budget_usd=args.budget_usd), t0)
        elif stage == "rank":
            record(rank.run_rank(db, cfg, input_id, run_dir), t0)
        elif stage == "memo":
            record(memo.run_memo(db, cfg, provider, input_id, run_dir, invocation, budget_usd=args.budget_usd), t0)
        elif stage == "export":
            record(export.run_export(db, cfg, input_id, run_dir, grading_dir=args.grading_dir, full_path=args.full), t0)
    print(f"[run] finished in {time.time() - started:.1f}s wall-clock" + (" (INCOMPLETE: work pending)" if incomplete else ""))
    return 2 if incomplete else 0


def cmd_status(args):
    cfg = Config(args.config, provider=args.provider)
    db = DB(args.db)
    input_id, _ = ingest.ingest(db, args.input)
    print(json.dumps({"label_config": cfg.label_config, **db.status_counts(input_id, cfg.label_config)}, indent=2))
    return 0


def cmd_requeue(args):
    cfg = Config(args.config, provider=args.provider)
    n = enrich.requeue_quarantined(DB(args.db), cfg)
    print(f"{n} quarantined texts returned to pending for label_config {cfg.label_config}")
    return 0


def cmd_rerank(args):
    """Recompute the baseline ranking from the saved grading files only (no DB, no model, no key)."""
    folder = Path(args.grading)
    plain, zipped = folder / "records.jsonl", folder / "records.jsonl.gz"
    stream = plain.open(encoding="utf-8") if plain.exists() else gzip.open(zipped, "rt", encoding="utf-8")
    severity = {}
    with stream as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("status") == "completed" and rec["intent"] in ("complaint", "cancellation"):
                severity[rec["review_id"]] = rec["severity"]
    totals = {}
    with (folder / "membership.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            t = totals.setdefault(row["issue_id"], [0, 0])
            t[0] += 1
            t[1] += severity[row["review_id"]]
    rows = sorted(totals.items(), key=lambda kv: (-kv[1][1], kv[0]))
    table = [[str(i), iid, str(n), str(s), rank.mean_string(s, n), str(s)] for i, (iid, (n, s)) in enumerate(rows, 1)]
    out = Path(args.out) if args.out else folder / "ranking.recomputed.csv"
    rank.write_csv(out, rank.RANK_COLUMNS, table)
    saved = folder / "ranking.csv"
    if saved.exists():
        with saved.open(encoding="utf-8", newline="") as f:
            same = list(csv.reader(f))[1:] == table
        print(f"recomputed {len(table)} issues from {len(severity)} complaint records -> {out}; "
              f"{'IDENTICAL to' if same else 'DIFFERENT from'} saved ranking.csv")
        return 0 if same else 1
    print(f"recomputed {len(table)} issues -> {out}")
    return 0


def main(argv=None):
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # progress stays visible when output is piped to a file
    parser = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("input", help="input CSV with the six source columns")
        p.add_argument("--db", default=str(ROOT / "state" / "pipeline.db"), help="SQLite state/result-cache file")
        p.add_argument("--config", default=None, help="config JSON (default pipeline_config.json)")
        p.add_argument("--provider", default=None, choices=("openai", "mock"), help="override the configured provider")

    p = sub.add_parser("run", help="run stages on an input CSV (resumes saved progress)")
    common(p)
    p.add_argument("--stages", help="comma list; default all: " + ",".join(STAGES))
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--budget-usd", type=float, default=None, help="cumulative API spend cap for this state DB")
    p.add_argument("--stop-after-batches", type=int, help="stop enrichment after N requests (interruption demo)")
    p.add_argument("--max-seconds", type=float, help="stop admitting enrichment work after this many seconds")
    p.add_argument("--allow-partial", action="store_true", help="run later stages even if enrichment is incomplete")
    p.add_argument("--run-dir", help="output folder (default runs/<input>__<model>-<effort>-<config hash>)")
    p.add_argument("--grading-dir", help="grading export folder (default <run-dir>/grading)")
    p.add_argument("--full", help="full source CSV to profile for grading/ingestion.json (default: the input)")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("status", help="show completed/pending/quarantined counts")
    common(p)
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("requeue-quarantined", help="return quarantined texts to pending after fixing a systematic failure")
    common(p)
    p.set_defaults(func=cmd_requeue)
    p = sub.add_parser("rerank", help="recompute ranking from saved grading files; no model calls")
    p.add_argument("--grading", default=str(ROOT / "grading"))
    p.add_argument("--out")
    p.set_defaults(func=cmd_rerank)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
