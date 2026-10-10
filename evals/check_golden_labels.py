#!/usr/bin/env python3
"""Rule check of the hand labels against the shared definitions. Offline; no model, no predictions.

    python evals/check_golden_labels.py

It only reports places where a label conflicts with a rule stated in GRADING_CONTRACT.md or with the
labeller's own choices elsewhere in the file. It never changes a label and never suggests one.
"""

import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from pipeline import labels  # noqa: E402

LEAVING = re.compile(r"uninstall|unstall|delet(e|ing|ed) (this|the) app|cancel(l?ing|l?ed)? (my )?(premium|subscription)|switch(ing|ed)? to|boycott", re.I)


def main(path=HERE / "golden_50_labeled.csv"):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    with (HERE.parent / "data" / "golden_50_to_label.csv").open(encoding="utf-8-sig", newline="") as f:
        source = {r["review_id"]: r["review_text"] for r in csv.DictReader(f)}
    findings = defaultdict(list)
    for n, r in enumerate(rows, 1):
        tag = f"#{n} {r['review_id'][:8]}"
        if r["review_id"] not in source or source[r["review_id"]] != r["review_text"]:
            findings["review text or ID differs from the supplied golden file"].append(tag)
        if r["topic"] not in labels.TOPICS or r["intent"] not in labels.INTENTS or r["severity"] not in list("12345") or r["sentiment"] == "":
            findings["missing or invalid label"].append(tag)
            continue
        sev = int(r["severity"])
        if r["evidence_quote"] not in r["review_text"] or not r["evidence_quote"].strip():
            findings["evidence is not an exact piece of the review"].append(tag)
        if r["intent"] == "complaint" and sev == 1:
            findings["complaint with severity 1 (contract: severity 1 = no reported problem; generic criticism is 2)"].append(tag)
        if r["intent"] in ("praise", "unclear", "request") and sev > 1:
            findings[f"{r['intent']} with severity above 1 (contract: praise, unclear and pure requests are severity 1)"].append(tag)
        if r["intent"] != "cancellation" and LEAVING.search(r["review_text"]):
            findings["text mentions uninstalling/deleting/boycotting but intent is not cancellation (check the precedence rule; a bare boycott slogan is unclear)"].append(tag)
        if r["intent"] == "praise" and float(r["sentiment"]) <= 0 or r["intent"] == "complaint" and float(r["sentiment"]) > 0:
            findings["sentiment sign does not match the intent"].append(tag)
    praise_topics = Counter(r["topic"] for r in rows if r["intent"] == "praise" and len(r["review_text"]) <= 60)
    print(f"{len(rows)} rows read from {Path(path).name}")
    print(f"needs_review ticked: {sum(r['needs_review'].strip().lower() == 'true' for r in rows)}")
    print(f"topics used for short praise (contract: general praise is 'other'): {dict(praise_topics)}")
    if not findings:
        print("No rule conflicts found.")
    for name, tags in findings.items():
        print(f"\n{len(tags)} x {name}:\n   " + ", ".join(tags))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
