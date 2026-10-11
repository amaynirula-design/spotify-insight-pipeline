"""Stage 6 - Recommend. The memo model receives only saved aggregates and a bounded evidence pack.
It never types statistics: it writes placeholders ({{C001}}, {{F003}}) that code replaces with the
saved value and its citation. Code then checks every placeholder, review ID, issue ID and any
stray number; a person inspects the final argument."""

import json
import re
from pathlib import Path

from . import rank
from .control import Rejected, Runner
from .util import ROOT, read_json, sha_text, write_json, write_text_atomic

METRICS = ("complaint_count", "severity_sum", "mean_severity", "priority_score")
MONTH_NAMES = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def fmt_int(v):
    return f"{int(v):,}"


def fmt_pct(num, den):
    return f"{100 * num / den:.1f}%" if den else "n/a"


def month_label(m):
    return f"{MONTH_NAMES[int(m[5:7]) - 1]} {m[:4]}"


def build_pack(db, cfg, input_id, run_dir):
    lc, role = cfg.label_config, cfg.role("memo")
    ranking = rank.compute(db, input_id, lc)
    issues = {r["issue_id"]: r for r in db.q("SELECT * FROM issues WHERE input_id=? AND label_config=?", (input_id, lc))}
    top = ranking[:role["top_issues"]]
    claims, facts = [], []

    for issue in top:
        for metric in METRICS:
            claims.append({"claim_id": f"C{len(claims) + 1:03d}", "issue_id": issue["issue_id"], "metric": metric,
                           "value": str(issue[metric])})

    def fact(label, value, formula):
        facts.append({"fact_id": f"F{len(facts) + 1:03d}", "label": label, "value": value, "formula": formula})

    counts = db.status_counts(input_id, lc)
    nonempty = counts["n_rows"] - counts["empty_text"]
    unresolved = counts["quarantined_nonempty"] + counts["pending"]
    complaints = sum(r["complaint_count"] for r in ranking)
    fact("rows in the source file", fmt_int(counts["n_rows"]), "COUNT(*) of ingested rows")
    fact("reviews with a completed classification", fmt_int(counts["completed"]), "rows joined to a completed result")
    fact("share of nonempty reviews classified", fmt_pct(counts["completed"], nonempty), "completed / nonempty rows")
    fact("empty-text reviews quarantined", fmt_int(counts["empty_text"]), "rows whose text is empty or whitespace")
    fact("nonempty reviews left unclassified (quarantined or pending)", fmt_int(unresolved), "nonempty rows without a completed result")
    fact("completed rows served by exact-duplicate text reuse", fmt_int(counts["cache_reuse_rows"]), "completed rows whose result came from an identical text")
    fact("complaint or cancellation reviews (ranking population)", fmt_int(complaints), "completed rows with intent complaint or cancellation")
    fact("complaint or cancellation reviews as a share of completed reviews", fmt_pct(complaints, counts["completed"]), "ranking population / completed")
    flagged = db.one("""SELECT COUNT(*) AS n FROM reviews r JOIN results s ON s.text_key=r.text_key AND s.label_config=?
                        WHERE r.input_id=? AND s.status='completed' AND s.needs_review=1""", (lc, input_id))["n"]
    fact("completed reviews flagged needs_review", fmt_int(flagged), "completed rows with needs_review = true")

    by_topic = {}
    for r in ranking:
        t = by_topic.setdefault(r["topic"], [0, 0])
        t[0] += r["complaint_count"]
        t[1] += r["severity_sum"]
    for topic, (n, sev) in sorted(by_topic.items(), key=lambda kv: -kv[1][1]):
        fact(f"topic {topic}: complaint/cancellation reviews", fmt_int(n), "sum of complaint_count over the topic's issues")
        fact(f"topic {topic}: severity sum", fmt_int(sev), "sum of severity_sum over the topic's issues")
        fact(f"topic {topic}: share of all complaint/cancellation reviews", fmt_pct(n, complaints), "topic complaint_count / ranking population")

    months = rank.monthly(db, input_id, lc)
    full = months[1:-1] if len(months) >= 3 else months  # first and last calendar months are partial
    recent, prior = [], []
    if len(full) >= 15:
        recent = full[-3:]
        wanted = {f"{int(m['month'][:4]) - 1}{m['month'][4:]}" for m in recent}
        prior = [m for m in full if m["month"] in wanted]
        if len(prior) != 3:
            recent, prior = [], []
    for issue in top:
        iid = issue["issue_id"]
        fact(f"{iid}: share of all complaint/cancellation reviews", fmt_pct(issue["complaint_count"], complaints), "issue complaint_count / ranking population")
        fact(f"{iid}: reviews expressing cancellation intent", fmt_int(issue["cancellation_count"]), "members with intent = cancellation (expressed intent, not observed churn)")
        peak = max(months, key=lambda m: m["issues"].get(iid, 0))
        fact(f"{iid}: complaints in its peak month ({month_label(peak['month'])})", fmt_int(peak["issues"].get(iid, 0)), "max monthly member count")
        if recent:
            for label, window in (("prior", prior), ("recent", recent)):
                num, den = sum(m["issues"].get(iid, 0) for m in window), sum(m["completed"] for m in window)
                span = f"{month_label(window[0]['month'])}-{month_label(window[-1]['month'])}"
                fact(f"{iid}: complaints as a share of completed reviews, {span} ({label} comparable period, denominator {fmt_int(den)})",
                     fmt_pct(num, den), f"{fmt_int(num)} issue complaints / {fmt_int(den)} completed reviews in the period")

    verify_path = run_dir / "verify_report.json"
    if verify_path.exists():
        v = read_json(verify_path)
        fact("independent verifier: reviews re-labelled", fmt_int(v["compared"]), "seeded random sample of completed rows")
        fact("independent verifier: topic agreement with the enricher", f"{100 * v['topic_agreement']:.1f}%", "matching topic / compared")
        fact("independent verifier: intent agreement with the enricher", f"{100 * v['intent_agreement']:.1f}%", "matching intent / compared")
        fact("independent verifier: exact severity agreement with the enricher", f"{100 * v['severity_exact_agreement']:.1f}%", "matching severity / compared")
    golden_path = ROOT / "evals" / "golden_results.json"
    if golden_path.exists():
        g = read_json(golden_path)
        if g.get("label_config") == lc:
            fact("hand-labelled golden reviews compared", fmt_int(g["n"]), "golden set size")
            fact("golden set: topic agreement", f"{100 * g['topic_agreement']:.1f}%", "matching topic / golden reviews")
            fact("golden set: intent agreement", f"{100 * g['intent_agreement']:.1f}%", "matching intent / golden reviews")
            fact("golden set: exact severity agreement", f"{100 * g['severity_exact_agreement']:.1f}%", "matching severity / golden reviews")

    evidence = {}
    for issue in top:
        row = issues.get(issue["issue_id"])
        skip = set(json.loads(row["off_topic_ids"])) if row else set()
        picked = db.q("""SELECT r.review_id, s.evidence_quote, s.severity, substr(r.review_timestamp, 1, 7) AS month
                         FROM reviews r JOIN results s ON s.text_key = r.text_key AND s.label_config = ?
                         WHERE r.input_id = ? AND s.status = 'completed' AND s.intent IN ('complaint', 'cancellation')
                           AND s.topic = ? AND s.code = ? AND s.needs_review = 0 AND length(s.evidence_quote) BETWEEN 25 AND 220
                         ORDER BY r.source_sha256 LIMIT ?""",
                      (lc, input_id, issue["topic"], issue["code"], role["quotes_per_issue"] + len(skip)))
        evidence[issue["issue_id"]] = [{"review_id": r["review_id"], "quote": r["evidence_quote"], "severity": r["severity"], "month": r["month"]}
                                       for r in picked if r["review_id"] not in skip][:role["quotes_per_issue"]]

    pack = {
        "question": "Where should the next quarter of product effort go: access, usability, playback, or billing/support?",
        "ranking": [{"rank": r["rank"], "issue_id": r["issue_id"], "topic": r["topic"],
                     "name": issues[r["issue_id"]]["name"] if r["issue_id"] in issues else r["code"],
                     "description": issues[r["issue_id"]]["description"] if r["issue_id"] in issues else r["definition"],
                     "actionable_product_area": r["topic"] != "other"} for r in ranking[:max(role["top_issues"], 12)]],
        "claims": claims, "facts": [{k: f[k] for k in ("fact_id", "label", "value")} for f in facts], "evidence": evidence,
        "limits": [
            "Self-selected public Google Play reviews from a fixed historical window; not the customer population.",
            "No revenue, plan tier or confirmed cancellation data: cancellation language is expressed intent only.",
            "Labels come from a small model; verifier and golden-set agreement facts show their reliability.",
            "The first and last calendar months of the window are partial.",
            "The baseline score is complaint_count x mean_severity (= severity_sum); it measures review volume weighted by described impact, not business value.",
        ],
    }
    return pack, claims, facts, ranking


PLACEHOLDER = re.compile(r"\{\{\s*([CF]\d{3})\s*\}\}")
CITATION = re.compile(r"\[review:\s*([^\]\s]+)\s*\]")
ALLOWED_NUMBERS = {"2022", "2023", "18"} | {str(n) for n in range(0, 11)}


def validate(draft, claims, facts, evidence, ranking_ids):
    known = {c["claim_id"] for c in claims} | {f["fact_id"] for f in facts}
    evidence_ids = {q["review_id"] for quotes in evidence.values() for q in quotes}
    problems = []
    used = PLACEHOLDER.findall(draft)
    problems += [f"unknown placeholder {{{{{p}}}}}" for p in used if p not in known]
    if len({p for p in used if p.startswith("C") and p in known}) < 3:
        problems.append("fewer than three distinct claim placeholders are cited")
    cited = CITATION.findall(draft)
    problems += [f"review ID {rid} is not in the evidence pack" for rid in cited if rid not in evidence_ids]
    if not cited:
        problems.append("no customer evidence is cited with [review:<id>]")
    for issue_id in re.findall(r"`([a-z]+\.[a-z]+)`", draft):
        if issue_id not in ranking_ids:
            problems.append(f"issue id {issue_id} is not in the ranking")
    stripped = re.sub(r"`[^`]*`", " ", CITATION.sub(" ", PLACEHOLDER.sub(" ", draft)))
    stripped = re.sub(r"\bQ[1-4]\b", " ", stripped)
    for number in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?", stripped):
        if number.rstrip("%").replace(",", "") not in ALLOWED_NUMBERS or number.endswith("%"):
            problems.append(f"number {number!r} was typed instead of using a placeholder")
    return problems, used, cited


def render(draft, claims, facts):
    values = {f["fact_id"]: f["value"] for f in facts}
    for c in claims:
        values[c["claim_id"]] = f"{float(c['value']):.2f}" if c["metric"] == "mean_severity" else fmt_int(c["value"])
    return PLACEHOLDER.sub(lambda m: f"{values[m.group(1)]} [{m.group(1)}]" if m.group(1) in values else m.group(0), draft)


def run_memo(db, cfg, provider, input_id, run_dir, invocation, budget_usd=None, log=print):
    pack, claims, facts, ranking = build_pack(db, cfg, input_id, run_dir)
    if not ranking:
        log("[memo] no complaint records: nothing to rank or recommend")
        return {"stage": "memo", "status": "skipped_no_complaints"}
    write_json(run_dir / "memo_pack.json", pack)
    rank.write_csv(run_dir / "facts.csv", ("fact_id", "label", "value", "formula"),
                   [[f["fact_id"], f["label"], f["value"], f["formula"]] for f in facts])
    rank.write_csv(run_dir / "claims_offered.csv", ("claim_id", "issue_id", "metric", "value"),
                   [[c["claim_id"], c["issue_id"], c["metric"], c["value"]] for c in claims])
    runner = Runner(db, cfg, provider, input_id, invocation, budget_usd=budget_usd)
    ranking_ids = {r["issue_id"] for r in ranking}
    draft, problems, used, cited, attempts = "", ["no response"], [], [], 0
    feedback = None
    # Downstream artifact cache: the same evidence pack under the same memo config reuses the saved draft.
    cache_key = "memo|" + sha_text(json.dumps(pack, sort_keys=True, ensure_ascii=False) + cfg.stage_config("memo"))
    cached = db.get_meta(cache_key)
    if cached:
        draft = cached["draft"]
        problems, used, cited = validate(draft, claims, facts, pack["evidence"], ranking_ids)
    for kind in (() if cached else ("first", "retry")):
        attempts += 1
        payload = dict(pack, revision_request=feedback) if feedback else pack
        try:
            resp, _ = runner.call("memo", json.dumps(payload, ensure_ascii=False), [], attempt_kind=kind,
                                  artifacts="memo_pack.json (ranking, claims, facts, evidence quotes)")
        except Rejected as e:
            problems = [f"provider rejected the request: {e}"]
            break
        draft = resp["text"].strip()
        problems, used, cited = validate(draft, claims, facts, pack["evidence"], ranking_ids)
        if not problems:
            break
        feedback = {"problems_found_by_code": problems, "instruction": "Rewrite the memo fixing every problem. Follow the strict rules."}

    if draft and not cached and not problems:
        db.set_meta(cache_key, {"draft": draft, "attempts": attempts})
    cited_claims = [c for c in claims if c["claim_id"] in used]
    cited_facts = [f for f in facts if f["fact_id"] in used]
    status = "validated" if not problems else "needs_human_fix"
    by_label = {f["label"]: f for f in facts}
    cov = [by_label[k] for k in ("rows in the source file", "reviews with a completed classification",
                                 "empty-text reviews quarantined", "nonempty reviews left unclassified (quarantined or pending)")]
    coverage = (f"**Coverage (generated by code).** {cov[0]['value']} [{cov[0]['fact_id']}] source rows; "
                f"{cov[1]['value']} [{cov[1]['fact_id']}] classified; {cov[2]['value']} [{cov[2]['fact_id']}] empty-text reviews "
                f"quarantined; {cov[3]['value']} [{cov[3]['fact_id']}] nonempty reviews left unclassified after the single retry. "
                "Unclassified reviews are excluded from the ranking and from every count above.")
    lines = [render(draft, claims, facts), "", "---", "", coverage, "",
             "## Appendix: traceability (generated by code)", "",
             f"Memo status: **{status}**. Every number above was substituted by code from saved calculations; "
             "`[C…]` ids are rows of `claims.csv` (recomputed by `check_submission.py`), `[F…]` ids are rows of `facts.csv`. "
             "Mean severity is shown to two decimals; `claims.csv` holds the six-decimal value.", "",
             "| Claim | Issue | Metric | Exact value |", "|---|---|---|---|"]
    lines += [f"| {c['claim_id']} | `{c['issue_id']}` | {c['metric']} | {c['value']} |" for c in cited_claims]
    lines += ["", "| Fact | What it measures | Value | How it is computed |", "|---|---|---|---|"]
    lines += [f"| {f['fact_id']} | {f['label']} | {f['value']} | {f['formula']} |" for f in cited_facts + [c for c in cov if c not in cited_facts]]
    lines += ["", "Cited reviews (issue membership is in `membership.csv`): " + (", ".join(f"`{rid}`" for rid in dict.fromkeys(cited)) or "none"), ""]
    if problems:
        lines += ["**Unresolved validation problems:**", *[f"- {p}" for p in problems], ""]
    write_text_atomic(run_dir / "memo.md", "\n".join(lines))
    write_text_atomic(run_dir / "memo_draft_raw.md", draft + "\n")
    rank.write_csv(run_dir / "claims.csv", ("claim_id", "issue_id", "metric", "value"),
                   [[c["claim_id"], c["issue_id"], c["metric"], c["value"]] for c in cited_claims])
    result = {"stage": "memo", "memo_config": cfg.stage_config("memo"), "status": status, "attempts": attempts,
              "reused_saved_draft": bool(cached),
              "problems": problems, "claims_cited": [c["claim_id"] for c in cited_claims],
              "facts_cited": [f["fact_id"] for f in cited_facts], "reviews_cited": list(dict.fromkeys(cited))}
    write_json(run_dir / "memo_validation.json", result)
    log(f"[memo] status={status}, {len(cited_claims)} claims and {len(set(cited))} reviews cited, attempts={attempts}")
    return result
