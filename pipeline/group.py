"""Stage 4 - Group. Membership is decided by code (one issue per complaint: topic.code from the
enriched record). The grouping model only names and describes each issue from a bounded sample of
quotes, and may point out sampled quotes that do not fit. The accepted mapping is saved, so
ranking never needs a model."""

import json
import re

from . import labels, rank
from .control import Rejected, Runner


def parse_json_object(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def run_group(db, cfg, provider, input_id, run_dir, invocation, budget_usd=None, log=print):
    lc, gc, role = cfg.label_config, cfg.stage_config("group"), cfg.role("group")
    ranking = rank.compute(db, input_id, lc)
    done = {r["issue_id"] for r in db.q("SELECT issue_id FROM issues WHERE input_id=? AND label_config=? AND group_config=?",
                                        (input_id, lc, gc))}
    runner = Runner(db, cfg, provider, input_id, invocation, budget_usd=budget_usd)
    named = fallback = 0
    for issue in ranking:
        if issue["issue_id"] in done:
            continue
        # Bounded evidence pack: a fixed pseudo-random sample (ordered by source row hash).
        sample = db.q("""SELECT r.review_id, s.evidence_quote FROM reviews r
                         JOIN results s ON s.text_key = r.text_key AND s.label_config = ?
                         WHERE r.input_id = ? AND s.status = 'completed' AND s.intent IN ('complaint', 'cancellation')
                           AND s.topic = ? AND s.code = ? ORDER BY r.source_sha256 LIMIT ?""",
                      (lc, input_id, issue["topic"], issue["code"], role["examples_per_issue"]))
        ids = [r["review_id"] for r in sample]
        pack = {"issue_id": issue["issue_id"], "topic": issue["topic"], "definition": issue["definition"],
                "complaint_count": issue["complaint_count"], "mean_severity": issue["mean_severity"],
                "examples": [{"review_id": r["review_id"], "quote": r["evidence_quote"][:240]} for r in sample]}
        accepted = None
        for kind in ("first", "retry"):
            holder = {}

            def on_success(conn, resp, call_id):
                try:
                    obj = parse_json_object(resp["text"])
                    name, description = str(obj["name"]).strip(), str(obj["description"]).strip()
                    off = [x for x in obj.get("off_topic_ids", []) if x in ids]  # unknown IDs are dropped
                    if not name or not description or len(name.split()) > 12:
                        raise ValueError("empty or over-long name")
                except (ValueError, KeyError, TypeError):
                    return
                holder["row"] = (input_id, lc, issue["issue_id"], name, description, json.dumps(ids), json.dumps(off), gc, call_id)
                conn.execute("INSERT OR REPLACE INTO issues VALUES(?,?,?,?,?,?,?,?,?)", holder["row"])
            try:
                runner.call("group", json.dumps(pack, ensure_ascii=False), ids, attempt_kind=kind, on_success=on_success,
                            artifacts="evidence pack built from saved enriched records")
            except Rejected:
                pass
            if holder:
                accepted = holder["row"]
                break
        if accepted:
            named += 1
        else:  # keep the issue with its code definition so ranking and export stay complete
            fallback += 1
            with db.tx() as c:
                c.execute("INSERT OR REPLACE INTO issues VALUES(?,?,?,?,?,?,?,?,?)",
                          (input_id, lc, issue["issue_id"], f"(unnamed) {issue['code']}", issue["definition"],
                           json.dumps(ids), "[]", gc, None))
    rows = db.q("SELECT * FROM issues WHERE input_id=? AND label_config=? ORDER BY issue_id", (input_id, lc))
    rank.write_csv(run_dir / "issues.csv",
                   ("issue_id", "topic", "code", "issue_name", "description", "code_definition", "name_source", "sample_review_ids", "off_topic_sample_ids"),
                   [[r["issue_id"], *r["issue_id"].split("."), r["name"], r["description"], labels.SUBTOPICS[r["issue_id"].split(".")[1]][1],
                     "group_model" if r["call_id"] else "code_fallback", " ".join(json.loads(r["sample_ids"])),
                     " ".join(json.loads(r["off_topic_ids"]))] for r in rows])
    members = rank.write_membership(db, input_id, lc, run_dir / "membership.csv")
    log(f"[group] {len(rows)} issues ({named} newly named by the model, {fallback} fell back to the code definition), "
        f"{members} memberships saved")
    return {"stage": "group", "group_config": gc, "issues": len(rows), "named_now": named, "fallback_names": fallback, "memberships": members}
