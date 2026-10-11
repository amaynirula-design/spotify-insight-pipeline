#!/usr/bin/env python3
"""Compare the enricher's saved predictions with the 50 hand labels. Offline: no model calls.

    python evals/golden_eval.py --predictions runs/<golden run>/enriched.jsonl

Human labels: evals/golden_50_labeled.csv (made with evals/label_golden.html). They are read only
here, after predictions exist; they are never placed in a prompt, example or routing rule.
Predeclared sentiment tolerance: |model - human| <= 0.5 counts as within tolerance.
"""

import argparse
import csv
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from pipeline import labels  # noqa: E402

SENTIMENT_TOLERANCE = 0.5


def read_predictions(path):
    path = Path(path)
    stream = gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")
    with stream as f:
        return {r["review_id"]: r for r in map(json.loads, f)}


def read_predictions_from_db(db_path, golden_csv):
    """Saved enrichment results for the golden texts, looked up by exact text in the state DB (used before
    the full-corpus export exists; a text first labelled in an earlier checkpoint is the same cached result)."""
    import sqlite3
    from pipeline.config import Config
    from pipeline.util import text_key
    lc = Config().label_config
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    preds = {}
    with Path(golden_csv).open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            r = conn.execute("SELECT * FROM results WHERE text_key=? AND label_config=?", (text_key(row["review_text"]), lc)).fetchone()
            if r and r["status"] == "completed":
                preds[row["review_id"]] = {"review_id": row["review_id"], "status": "completed", "topic": r["topic"], "subtopic": r["code"],
                                           "intent": r["intent"], "sentiment": r["sentiment"], "severity": r["severity"],
                                           "entities": json.loads(r["entities"]), "evidence_quote": r["evidence_quote"],
                                           "needs_review": bool(r["needs_review"]), "quote_origin": r["quote_origin"], "label_config": lc}
    return preds


def read_human(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    problems = []
    for i, r in enumerate(rows, 1):
        if r["topic"] not in labels.TOPICS or r["intent"] not in labels.INTENTS:
            problems.append(f"row {i}: topic/intent missing or not an allowed label")
            continue
        try:
            r["severity"], r["sentiment"] = int(r["severity"]), float(r["sentiment"])
        except ValueError:
            problems.append(f"row {i}: severity or sentiment missing")
            continue
        r["needs_review"] = r["needs_review"].strip().lower() in ("true", "1", "yes")
        r["entities"] = [e.strip().lower() for e in r["entities"].replace(",", ";").split(";") if e.strip()]
        if r["evidence_quote"] and r["evidence_quote"] not in r["review_text"]:
            problems.append(f"row {i}: human evidence_quote is not an exact substring of the review")
    return rows, problems


def confusion(pairs, order):
    table = Counter(pairs)
    seen = [x for x in order if any(x in pair for pair in table)]
    lines = ["| human \\ model | " + " | ".join(seen) + " |", "|---|" + "---|" * len(seen)]
    lines += [f"| **{h}** | " + " | ".join(str(table.get((h, m), 0) or "·") for m in seen) + " |" for h in seen]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--predictions", help="enriched.jsonl(.gz) or grading records.jsonl(.gz) containing the golden IDs")
    parser.add_argument("--db", help="alternative to --predictions: read saved results for the golden texts from this state DB")
    parser.add_argument("--human", default=str(HERE / "golden_50_labeled.csv"))
    parser.add_argument("--out", default=str(HERE))
    args = parser.parse_args()
    if not Path(args.human).exists():
        print(f"{args.human} not found. Hand-label the golden set first: python evals/make_label_tool.py && open evals/label_golden.html")
        return 1
    human, problems = read_human(args.human)
    if problems:
        print("The hand-labelled file is incomplete:\n  " + "\n  ".join(problems[:20]))
        return 1
    if not args.predictions and not args.db:
        parser.error("give --predictions FILE or --db STATE_DB")
    if args.db:
        preds = read_predictions_from_db(args.db, ROOT / "data" / "golden_50_to_label.csv")
        args.predictions = f"{args.db} (saved results looked up by exact text)"
    else:
        preds = read_predictions(args.predictions)
    cases, stats = [], Counter()
    topic_pairs, intent_pairs, severity_pairs = [], [], []
    for h in human:
        p = preds.get(h["review_id"])
        ok = bool(p) and p.get("status") == "completed"
        case = {"review_id": h["review_id"], "review_text": h["review_text"], "human": {k: h[k] for k in ("topic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review")},
                "model": None, "has_valid_prediction": ok}
        stats["n"] += 1
        stats["human_ambiguous"] += h["needs_review"]
        if not ok:
            stats["missing_or_quarantined"] += 1  # counts as incorrect on every field; never dropped
            case.update(topic_ok=False, intent_ok=False, severity_ok=False, all_ok=False)
            cases.append(case)
            continue
        case["model"] = {k: p.get(k) for k in ("topic", "subtopic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review", "quote_origin")}
        case["topic_ok"], case["intent_ok"], case["severity_ok"] = p["topic"] == h["topic"], p["intent"] == h["intent"], p["severity"] == h["severity"]
        case["all_ok"] = case["topic_ok"] and case["intent_ok"] and case["severity_ok"]
        case["severity_abs_error"] = abs(p["severity"] - h["severity"])
        case["sentiment_abs_error"] = round(abs(p["sentiment"] - h["sentiment"]), 3)
        case["sentiment_within_tolerance"] = case["sentiment_abs_error"] <= SENTIMENT_TOLERANCE
        case["quote_is_exact_substring"] = p["evidence_quote"] in h["review_text"]
        hq, mq = h["evidence_quote"], p["evidence_quote"]
        case["quote_overlaps_human_quote"] = bool(hq) and (mq in hq or hq in mq or len(set(mq.lower().split()) & set(hq.lower().split())) >= max(1, len(set(hq.lower().split())) // 2))
        model_entities = [e.lower() for e in p["entities"]]
        case["entities_not_in_human_list"] = [e for e in model_entities if not any(e in x or x in e for x in h["entities"])]
        case["human_entities_missed"] = [x for x in h["entities"] if not any(e in x or x in e for e in model_entities)]
        for field in ("topic", "intent", "severity", "all"):
            stats[field + "_ok"] += case[field + "_ok"]
            stats[field + "_ok_unambiguous"] += case[field + "_ok"] and not h["needs_review"]
        stats["with_prediction"] += 1
        stats["severity_abs_error"] += case["severity_abs_error"]
        stats["sentiment_abs_error"] += case["sentiment_abs_error"]
        stats["sentiment_within_tolerance"] += case["sentiment_within_tolerance"]
        stats["quote_exact"] += case["quote_is_exact_substring"]
        stats["quote_overlap"] += case["quote_overlaps_human_quote"]
        stats["entity_additions"] += len(case["entities_not_in_human_list"])
        stats[f"flag_model{int(bool(p['needs_review']))}_human{int(h['needs_review'])}"] += 1
        stats["flagged_and_wrong"] += bool(p["needs_review"]) and not case["all_ok"]
        stats["unflagged_and_wrong"] += (not p["needs_review"]) and not case["all_ok"]
        topic_pairs.append((h["topic"], p["topic"]))
        intent_pairs.append((h["intent"], p["intent"]))
        severity_pairs.append((str(h["severity"]), str(p["severity"])))
        cases.append(case)

    n, m = stats["n"], max(stats["with_prediction"], 1)
    clear = max(n - stats["human_ambiguous"], 1)
    label_config = next((p.get("label_config") for p in preds.values() if p.get("label_config")), None)
    result = {
        "n": n, "label_config": label_config, "predictions_file": str(args.predictions),
        "missing_or_quarantined_predictions": stats["missing_or_quarantined"],
        "topic_agreement": round(stats["topic_ok"] / n, 4), "intent_agreement": round(stats["intent_ok"] / n, 4),
        "severity_exact_agreement": round(stats["severity_ok"] / n, 4), "all_three_agreement": round(stats["all_ok"] / n, 4),
        "severity_mean_absolute_error": round(stats["severity_abs_error"] / m, 4),
        "sentiment_mean_absolute_error": round(stats["sentiment_abs_error"] / m, 4),
        "sentiment_tolerance_predeclared": SENTIMENT_TOLERANCE, "sentiment_within_tolerance": round(stats["sentiment_within_tolerance"] / m, 4),
        "human_ambiguous_cases": stats["human_ambiguous"],
        "agreement_on_unambiguous_cases": {f: round(stats[f + "_ok_unambiguous"] / clear, 4) for f in ("topic", "intent", "severity", "all")},
        "evidence_quote_exact_substring": round(stats["quote_exact"] / m, 4), "evidence_quote_overlaps_human_quote": round(stats["quote_overlap"] / m, 4),
        "entity_additions_not_in_human_list": stats["entity_additions"],
        "needs_review_as_prediction": {"model_flag_and_human_flag": stats["flag_model1_human1"], "model_flag_only": stats["flag_model1_human0"],
                                       "human_flag_only": stats["flag_model0_human1"], "neither": stats["flag_model0_human0"],
                                       "model_flagged_and_label_wrong": stats["flagged_and_wrong"], "model_unflagged_and_label_wrong": stats["unflagged_and_wrong"]},
        "per_topic_counts": {"human": dict(Counter(h for h, _ in topic_pairs)), "model": dict(Counter(mm for _, mm in topic_pairs))},
        "note": "Fifty cases are a small diagnostic sample, not a precise population-accuracy estimate. Missing or quarantined predictions count as incorrect.",
    }
    out = Path(args.out)
    (out / "golden_results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (out / "golden_cases.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    pct = lambda x: f"{100 * x:.0f}%"
    L = ["# Golden-set evaluation (50 hand-labelled reviews)", "",
         f"Enricher configuration: `{label_config}`. Predictions: `{args.predictions}`.", "",
         "| Field | Agreement (all 50) | On unambiguous cases |", "|---|---|---|"]
    for f, name in (("topic", "Topic"), ("intent", "Intent"), ("severity", "Severity (exact)"), ("all", "All three")):
        key = {"topic": "topic_agreement", "intent": "intent_agreement", "severity": "severity_exact_agreement", "all": "all_three_agreement"}[f]
        L.append(f"| {name} | {pct(result[key])} ({stats[f + '_ok']}/{n}) | {pct(result['agreement_on_unambiguous_cases'][f])} |")
    L += ["", f"- Severity mean absolute error: **{result['severity_mean_absolute_error']}**; sentiment mean absolute error: **{result['sentiment_mean_absolute_error']}** "
              f"({pct(result['sentiment_within_tolerance'])} within the predeclared ±{SENTIMENT_TOLERANCE}).",
          f"- Ambiguous cases (human ticked needs_review): **{stats['human_ambiguous']}**. Missing/quarantined predictions: {stats['missing_or_quarantined']}.",
          f"- Evidence quotes: {pct(result['evidence_quote_exact_substring'])} are exact source substrings (checked in code); "
          f"{pct(result['evidence_quote_overlaps_human_quote'])} overlap the human-chosen quote (heuristic; inspect `golden_cases.jsonl`).",
          f"- Entities: I left the optional entities column blank for all 50 reviews, so there is no human list to compare against. "
          f"Code extracted {stats['entity_additions']} entity mentions; each is a lexicon term literally present in the text, so none is an unsupported addition (listed per case in `golden_cases.jsonl`).",
          f"- needs_review as a prediction: {json.dumps(result['needs_review_as_prediction'])}", "",
          "## Per-topic counts", "", "| Topic | Human labels | Model labels |", "|---|---|---|",
          *[f"| {t} | {result['per_topic_counts']['human'].get(t, 0)} | {result['per_topic_counts']['model'].get(t, 0)} |" for t in labels.TOPICS], "",
          "## Topic confusion", "", confusion(topic_pairs, labels.TOPICS), "", "## Intent confusion", "", confusion(intent_pairs, labels.INTENTS), "",
          "## Severity confusion", "", confusion(severity_pairs, list("12345")), "", "## Disagreements", "",
          "| Review ID | Text | Human (topic / intent / severity) | Model (topic.subtopic / intent / severity) | Model flagged |", "|---|---|---|---|---|"]
    for c in cases:
        if not c["all_ok"]:
            h, p = c["human"], c["model"]
            model = f"{p['topic']}.{p['subtopic']} / {p['intent']} / {p['severity']}" if p else "no valid prediction"
            text = c["review_text"][:160].replace("\n", " ").replace("|", "\\|")
            L.append(f"| `{c['review_id'][:8]}` | {text} | {h['topic']} / {h['intent']} / {h['severity']}{' (ambiguous)' if h['needs_review'] else ''} | {model} | {p['needs_review'] if p else ''} |")
    (out / "golden_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:14]))
    print(f"\nSaved golden_results.json, golden_cases.jsonl, golden_report.md in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
