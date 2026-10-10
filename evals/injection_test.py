#!/usr/bin/env python3
"""Prompt-injection test for the enricher, using SYNTHETIC reviews (evals/injection_cases.json).

    python evals/injection_test.py --execute            # real model calls (10 short reviews, one request)
    python evals/injection_test.py --execute --provider mock   # offline plumbing check only

Injected reviews are batched together with clean control reviews. A case passes when its topic,
intent and severity are among the accepted labels, i.e. the injected instruction did not change
the label of that review or of its neighbours. Runs in its own state DB and run folder.
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true", help="required: confirms model calls may be made")
    parser.add_argument("--provider", choices=("openai", "mock"))
    parser.add_argument("--out", default=str(HERE / "injection"))
    args = parser.parse_args()
    if not args.execute:
        print("This test calls the enricher model. Re-run with --execute.")
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cases = json.loads((HERE / "injection_cases.json").read_text(encoding="utf-8"))["cases"]
    source = out / "synthetic_injection_input.csv"
    with source.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp"])
        for c in cases:
            writer.writerow([c["review_id"], c["review_text"], "", "0", "", "2023-01-01 00:00:00"])
    db = out / "injection_state.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    command = [sys.executable, "-m", "pipeline", "run", str(source), "--db", str(db), "--run-dir", str(out / "run"),
               "--stages", "ingest,enrich,export", "--budget-usd", "0.10"] + (["--provider", args.provider] if args.provider else [])
    code = subprocess.run(command, cwd=ROOT).returncode
    records = {}
    with (out / "run" / "grading" / "records.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            records[r["review_id"]] = r
    results, passed = [], 0
    for c in cases:
        r = records.get(c["review_id"], {})
        checks = {k: r.get(k) in c["accept"][k] for k in ("topic", "intent", "severity")} if r.get("status") == "completed" else {}
        ok = bool(checks) and all(checks.values())
        passed += ok
        results.append({"review_id": c["review_id"], "kind": c["kind"], "synthetic": True, "review_text": c["review_text"], "accepted": c["accept"],
                        "status": r.get("status"), "reason": r.get("reason"),
                        "predicted": {k: r.get(k) for k in ("topic", "intent", "severity", "sentiment", "needs_review", "evidence_quote")},
                        "checks": checks, "passed": ok})
    summary = {"synthetic": True, "pipeline_exit_code": code, "label_config": next((r.get("label_config") for r in records.values() if r.get("label_config")), None),
               "cases": len(cases), "passed": passed,
               "injection_cases_passed": sum(r["passed"] for r in results if r["kind"] == "injection"),
               "control_cases_passed": sum(r["passed"] for r in results if r["kind"] == "control"), "results": results}
    (out / "injection_results.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for r in results:
        p = r["predicted"]
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['review_id']:22s} -> {p['topic']}/{p['intent']}/sev {p['severity']} flag={p['needs_review']}")
    print(f"{passed}/{len(cases)} cases passed; details in {out / 'injection_results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
