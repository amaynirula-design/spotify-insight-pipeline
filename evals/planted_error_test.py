#!/usr/bin/env python3
"""Planted-error test for the verification stage. Offline: uses saved verifier predictions.

    python evals/planted_error_test.py --run-dir runs/<run folder>

Takes records where the independent verifier agreed with the enricher, writes a separate TEST COPY
with deliberately wrong labels planted in some of them, and checks that the code comparison against
the saved verifier answers flags every planted error and none of the untouched records.
The test copy is saved under evals/ and never replaces real pipeline output.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from pipeline import labels, verify  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--plant", type=int, default=6, help="number of wrong labels to plant")
    parser.add_argument("--out", default=str(HERE / "planted_error_results.json"))
    args = parser.parse_args()
    rows = [json.loads(line) for line in (Path(args.run_dir) / "verify_predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    agreed = [r for r in rows if r["verifier"] and all(r["enricher"][f] == r["verifier"][f] for f in ("topic", "intent", "severity"))]
    if len(agreed) < args.plant + 2:
        print(f"Only {len(agreed)} verifier-agreed records are available; need {args.plant + 2}.")
        return 1
    test_copy = {r["review_id"]: {f: r["enricher"][f] for f in ("topic", "intent", "severity")} for r in agreed}
    verified = {r["review_id"]: r["verifier"] for r in agreed}
    planted = []
    for n, r in enumerate(agreed[:args.plant]):
        original = dict(test_copy[r["review_id"]])
        field = ("topic", "severity", "intent")[n % 3]
        if field == "topic":
            wrong = labels.TOPICS[(labels.TOPICS.index(original["topic"]) + 3) % len(labels.TOPICS)]
        elif field == "severity":
            wrong = 5 if original["severity"] <= 2 else 1
        else:
            wrong = "praise" if original["intent"] != "praise" else "complaint"
        test_copy[r["review_id"]][field] = wrong
        planted.append({"review_id": r["review_id"], "field": field, "original": original[field], "planted_wrong_value": wrong})
    summary, disagreements = verify.compare(test_copy, verified)
    flagged = {d["review_id"]: d["fields"] for d in disagreements}
    for p in planted:
        p["detected"] = p["field"] in flagged.get(p["review_id"], [])
    false_alarms = [rid for rid in flagged if rid not in {p["review_id"] for p in planted}]
    result = {"synthetic_test_copy": True, "source_run": Path(args.run_dir).name, "records_in_test_copy": len(test_copy),
              "planted": planted, "planted_detected": sum(p["detected"] for p in planted), "planted_total": len(planted),
              "false_alarms_on_untouched_records": false_alarms, "passed": all(p["detected"] for p in planted) and not false_alarms,
              "note": "Detection relies on the verifier's independent label; an error the verifier would also make cannot be caught this way."}
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"planted {len(planted)} wrong labels in a test copy of {len(test_copy)} records: detected {result['planted_detected']}, "
          f"false alarms {len(false_alarms)} -> {'PASS' if result['passed'] else 'FAIL'}; saved {args.out}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
