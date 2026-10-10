"""Stages 4b and 5 - issue membership and the reproducible baseline ranking. SQL and integer
arithmetic only: no model call, so `python -m pipeline rank` regenerates identical files."""

import csv
from decimal import Decimal, ROUND_HALF_UP

from . import labels

MEMBERS_SQL = """
SELECT r.review_id AS review_id, s.topic || '.' || s.code AS issue_id, s.severity AS severity, s.intent AS intent,
       substr(r.review_timestamp, 1, 7) AS month, s.needs_review AS needs_review
FROM reviews r JOIN results s ON s.text_key = r.text_key AND s.label_config = ?
WHERE r.input_id = ? AND s.status = 'completed' AND s.intent IN ('complaint', 'cancellation')
"""


def mean_string(total, n):
    return str((Decimal(total) / Decimal(n)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def compute(db, input_id, label_config):
    """One issue per complaint/cancellation record: issue_id = '<topic>.<code>'. Every original
    review ID counts separately, including IDs served by exact-text reuse."""
    rows = db.q(f"""SELECT issue_id, COUNT(*) AS complaint_count, SUM(severity) AS severity_sum,
                           SUM(intent = 'cancellation') AS cancellation_count, SUM(needs_review) AS needs_review_count,
                           SUM(severity = 2) AS sev2, SUM(severity = 3) AS sev3, SUM(severity = 4) AS sev4, SUM(severity = 5) AS sev5,
                           SUM(severity = 1) AS sev1
                    FROM ({MEMBERS_SQL}) GROUP BY issue_id""", (label_config, input_id))
    ranking = []
    for r in rows:
        topic, code = r["issue_id"].split(".")
        ranking.append({"issue_id": r["issue_id"], "topic": topic, "code": code, "definition": labels.SUBTOPICS[code][1],
                        "complaint_count": r["complaint_count"], "severity_sum": r["severity_sum"],
                        "mean_severity": mean_string(r["severity_sum"], r["complaint_count"]),
                        "priority_score": r["severity_sum"], "cancellation_count": r["cancellation_count"],
                        "needs_review_count": r["needs_review_count"],
                        "severity_counts": {str(k): r[f"sev{k}"] for k in range(1, 6)}})
    ranking.sort(key=lambda x: (-x["priority_score"], x["issue_id"]))
    for rank, row in enumerate(ranking, 1):
        row["rank"] = rank
    return ranking


RANK_COLUMNS = ("rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score")


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_membership(db, input_id, label_config, path):
    rows = db.q(f"SELECT issue_id, review_id FROM ({MEMBERS_SQL}) ORDER BY issue_id, review_id", (label_config, input_id))
    write_csv(path, ("issue_id", "review_id"), [(r["issue_id"], r["review_id"]) for r in rows])
    return len(rows)


def write_ranking(ranking, path):
    write_csv(path, RANK_COLUMNS, [[row[c] for c in RANK_COLUMNS] for row in ranking])


def monthly(db, input_id, label_config):
    """Per calendar month: all rows, completed rows, complaint rows, and complaint rows per issue."""
    totals = {r["month"]: {"month": r["month"], "rows": r["n"], "completed": r["completed"] or 0, "complaints": 0, "issues": {}}
              for r in db.q("""SELECT substr(r.review_timestamp, 1, 7) AS month, COUNT(*) AS n, SUM(s.status = 'completed') AS completed
                               FROM reviews r LEFT JOIN results s ON s.text_key = r.text_key AND s.label_config = ?
                               WHERE r.input_id = ? GROUP BY month""", (label_config, input_id))}
    for r in db.q(f"SELECT month, issue_id, COUNT(*) AS n FROM ({MEMBERS_SQL}) GROUP BY month, issue_id", (label_config, input_id)):
        totals[r["month"]]["issues"][r["issue_id"]] = r["n"]
        totals[r["month"]]["complaints"] += r["n"]
    return [totals[m] for m in sorted(totals)]


def run_rank(db, cfg, input_id, run_dir, log=print):
    lc = cfg.label_config
    ranking = compute(db, input_id, lc)
    names = {r["issue_id"]: r["name"] for r in db.q("SELECT issue_id, name FROM issues WHERE input_id=? AND label_config=?", (input_id, lc))}
    members = write_membership(db, input_id, lc, run_dir / "membership.csv")
    write_ranking(ranking, run_dir / "ranking.csv")
    write_csv(run_dir / "aggregates.csv",
              ("rank", "issue_id", "issue_name", "topic", "complaint_count", "cancellation_count", "severity_sum", "mean_severity",
               "priority_score", "needs_review_count", "sev1", "sev2", "sev3", "sev4", "sev5"),
              [[r["rank"], r["issue_id"], names.get(r["issue_id"], ""), r["topic"], r["complaint_count"], r["cancellation_count"],
                r["severity_sum"], r["mean_severity"], r["priority_score"], r["needs_review_count"],
                *[r["severity_counts"][str(k)] for k in range(1, 6)]] for r in ranking])
    by_topic = {}
    for r in ranking:
        t = by_topic.setdefault(r["topic"], {"topic": r["topic"], "complaint_count": 0, "severity_sum": 0, "issues": 0})
        t["complaint_count"] += r["complaint_count"]
        t["severity_sum"] += r["severity_sum"]
        t["issues"] += 1
    topic_rows = sorted(by_topic.values(), key=lambda t: (-t["severity_sum"], t["topic"]))
    write_csv(run_dir / "aggregates_by_topic.csv", ("topic", "issues", "complaint_count", "severity_sum", "mean_severity"),
              [[t["topic"], t["issues"], t["complaint_count"], t["severity_sum"], mean_string(t["severity_sum"], t["complaint_count"])]
               for t in topic_rows])
    months = monthly(db, input_id, lc)
    issue_ids = [r["issue_id"] for r in ranking]
    write_csv(run_dir / "aggregates_by_month.csv", ("month", "rows", "completed", "complaints", *issue_ids),
              [[m["month"], m["rows"], m["completed"], m["complaints"], *[m["issues"].get(i, 0) for i in issue_ids]] for m in months])
    log(f"[rank] {len(ranking)} issues, {members} complaint/cancellation memberships; "
        f"top: {ranking[0]['issue_id'] if ranking else '-'}")
    return {"stage": "rank", "issues": len(ranking), "memberships": members, "model_calls": 0,
            "top5": [{k: r[k] for k in RANK_COLUMNS} for r in ranking[:5]]}
