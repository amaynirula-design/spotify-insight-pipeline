#!/usr/bin/env python3
"""100-review cost and runtime calculator.

Default (offline replay, no API key, no network, no model calls):
    python cost/calculator.py                       # recompute costs + projections from saved files
    python cost/calculator.py --rate-multiplier 2   # doubles API spend, leaves time/local unchanged
    python cost/calculator.py --set projected.distinct_nonempty_texts=600000 --set budget_usd=10
    python cost/calculator.py --basis refresh_500   # project from a later checkpoint measurement

Explicit paid pilot (separate command; needs OPENAI_API_KEY in .env):
    python cost/calculator.py pilot --execute

Refresh the projection basis from a saved checkpoint run (no model calls):
    python cost/calculator.py refresh --name refresh_500 --run-dir runs/checkpoint_500__...

Arithmetic: item_cost = billed_units x (price / per_units); total = sum of mutually exclusive items
(uncached input, cached input, output; reasoning tokens are already inside output).
"""

import argparse
import csv
import json
import math
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ITEMS = ("input_uncached", "input_cached", "output")
ROLES = ("enrich", "verify", "group", "memo")
LOCAL_STAGES = ("ingest", "rank", "export")
USAGE_COLUMNS = ("run_id", "role", "provider", "model", "effort", "tier", "item", "unit", "billed_units", "attempts", "source")


# ---------- loading ---------------------------------------------------------------------------
def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, columns, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def load_rates(path, multiplier=1.0):
    rates = {}
    for row in read_csv(path):
        rates[(row["model"], row["tier"], row["item"])] = dict(row, unit_price=float(row["price"]) * multiplier / float(row["per_units"]))
    return rates


def billed(call):
    cached = call.get("cached_input_tokens") or 0
    return {"input_uncached": max((call.get("input_tokens") or 0) - cached, 0), "input_cached": cached,
            "output": call.get("output_tokens") or 0}


def usage_from_calls(calls):
    """Aggregate the per-attempt call log into billed units per run/role/model/item."""
    table = defaultdict(lambda: [0, 0])
    for c in calls:
        for item, units in billed(c).items():
            cell = table[(c["run_id"], c["role"], c["provider"], c["model"], c["effort"], c["tier"], item)]
            cell[0] += units
            cell[1] += 1
    return [dict(zip(USAGE_COLUMNS, (*key, "token", units, attempts,
                                      f"sum over pilot_calls.jsonl where run_id={key[0]} and role={key[1]}")))
            for key, (units, attempts) in sorted(table.items())]


def priced(usage_rows, rates):
    out = []
    for u in usage_rows:
        rate = rates[(u["model"], u["tier"], u["item"])]
        out.append(dict(u, billed_units=int(u["billed_units"]), unit_price=rate["unit_price"], currency=rate["currency"],
                        cost=int(u["billed_units"]) * rate["unit_price"]))
    return out


# ---------- measurement -----------------------------------------------------------------------
def summarize(calls, run_meta):
    """Turn one run's call log + timing into the quantities the projection needs."""
    m = {"roles": {}, "wall_seconds_end_to_end": run_meta.get("wall_seconds_end_to_end"),
         "stage_wall_seconds": run_meta.get("stage_wall_seconds", {}), "workers": run_meta.get("workers", 1)}
    for role in ROLES:
        rc = [c for c in calls if c["role"] == role]
        ok = [c for c in rc if c["outcome"] == "succeeded"]
        first = [c for c in ok if c["attempt_kind"].startswith("first")]
        retry = [c for c in ok if c["attempt_kind"].startswith("retry")]
        units = {item: sum(billed(c)[item] for c in rc) for item in ITEMS}
        m["roles"][role] = {
            "attempts": len(rc), "succeeded": len(ok), "failed": len(rc) - len(ok),
            "first_pass_requests": len(first), "invalid_output_retry_requests": len(retry),
            "transient_retry_attempts": sum("transient" in c["attempt_kind"] for c in rc),
            "items_first_pass": sum(c["n_items"] for c in first), "items_in_retries": sum(c["n_items"] for c in retry),
            "units": units, "first_units": {item: sum(billed(c)[item] for c in first) for item in ITEMS},
            "reasoning_tokens": sum(c.get("reasoning_tokens") or 0 for c in rc),
            "max_output_tokens_in_one_request": max((c.get("output_tokens") or 0 for c in rc), default=0),
            "missing_usage_calls": sum(c.get("usage_source") in ("missing", None) for c in ok),
            "call_seconds_sum": round(sum(c.get("duration_s") or 0 for c in rc), 3),
            "model": rc[0]["model"] if rc else None, "tier": rc[0]["tier"] if rc else None,
        }
    return m


def role_cost(units, model, tier, rates):
    return sum(units[item] * rates[(model, tier, item)]["unit_price"] for item in ITEMS)


# ---------- projection ------------------------------------------------------------------------
def project(basis, a, scenario, rates, reuse=True, tier=None):
    s = a["scenarios"][scenario]
    tier = tier or s["tier"]
    p = a["projected"]
    texts = p["distinct_nonempty_texts"] if reuse else p["nonempty_rows"]
    out = {"scenario": scenario, "tier": tier, "exact_text_reuse": reuse, "texts_sent": texts, "notes": []}
    e = basis["roles"]["enrich"]
    n_first, req_first = e["items_first_pass"], e["first_pass_requests"]
    if not n_first:
        raise SystemExit("The measurement basis has no successful enrichment calls to project from.")
    fu = e["first_units"]
    in_per_text = (fu["input_uncached"] + fu["input_cached"]) / n_first
    out_per_text = fu["output"] / n_first
    measured_cached = fu["input_cached"] / max(fu["input_uncached"] + fu["input_cached"], 1)
    cached = measured_cached if s["cached_input_fraction"] is None else s["cached_input_fraction"]
    measured_retry = e["items_in_retries"] / n_first
    retry = max(measured_retry, 0.02) if s["enrich_retry_fraction"] is None else s["enrich_retry_fraction"]
    model = e["model"]
    r = {item: rates[(model, tier, item)]["unit_price"] for item in ITEMS}
    retry_in_per_text = (fu["input_uncached"] + fu["input_cached"]) / max(req_first, 1) / 5  # upper bound: a 5-review retry costs at most a full request's input
    enrich_units = {
        "input_uncached": texts * in_per_text * (1 - cached) + texts * retry * retry_in_per_text,
        "input_cached": texts * in_per_text * cached,
        "output": texts * out_per_text * (1 + retry),
    }
    costs = {"enrich": sum(enrich_units[i] * r[i] for i in ITEMS)}
    requests = {"enrich": math.ceil(texts / max(n_first / max(req_first, 1), 1)) + math.ceil(texts * retry / 5)}

    def per_unit(role, n_units, count, extra=0.0):
        b = basis["roles"][role]
        done = n_units(b)
        if not done or not b["model"]:
            out["notes"].append(f"{role}: not measured in this basis, projected cost unknown (shown as 0.00, NOT zero-cost)")
            return 0.0, 0
        cost_each = role_cost(b["units"], b["model"], tier, rates) / done
        return cost_each * count * (1 + extra), math.ceil(count * (1 + extra) * b["attempts"] / done)

    costs["verify"], requests["verify"] = per_unit("verify", lambda b: b["items_first_pass"], p["verify_sample_reviews"], s["verify_retry_fraction"])
    costs["group"], requests["group"] = per_unit("group", lambda b: b["first_pass_requests"], p["issues_to_name"])
    costs["memo"], requests["memo"] = per_unit("memo", lambda b: b["succeeded"], p["memo_calls"] * s["memo_attempts"])
    out.update(costs_by_stage=costs, requests_by_stage=requests, api_total=sum(costs.values()),
               enrich_units={k: round(v) for k, v in enrich_units.items()},
               assumptions_used={"enrich_retry_fraction": round(retry, 4), "measured_enrich_retry_fraction": round(measured_retry, 4),
                                 "cached_input_fraction": round(cached, 4), "measured_cached_input_fraction": round(measured_cached, 4),
                                 "input_tokens_per_text": round(in_per_text, 2), "output_tokens_per_text": round(out_per_text, 2)})

    # time: each stage extrapolated with its own work count and observed throughput; fixed overhead once
    sw = basis["stage_wall_seconds"]
    enrich_wall = sw.get("enrich")
    if enrich_wall and tier != "batch":
        per_worker = n_first / enrich_wall / max(basis["workers"], 1)
        workers = min(s["workers"], a["max_workers"])
        # The efficiency discount applies only when projecting beyond the worker count that was measured.
        efficiency = 1.0 if workers <= max(basis["workers"], 1) else s["parallel_efficiency"]
        rate = per_worker * workers * efficiency
        tpm_rate = a["tier_limits"]["tokens_per_minute"] / max(in_per_text + out_per_text, 1) / 60
        rpm_rate = a["tier_limits"]["requests_per_minute"] * (n_first / max(req_first, 1)) / 60
        bound = min(rate, tpm_rate, rpm_rate)
        if bound < rate:
            out["notes"].append(f"throughput capped by rate limits ({bound:.0f} texts/s instead of {rate:.0f})")
        seconds = {"enrich": texts * (1 + retry) / bound}
        for role, count in (("verify", p["verify_sample_reviews"]), ("group", p["issues_to_name"]), ("memo", p["memo_calls"] * s["memo_attempts"])):
            b = basis["roles"][role]
            done = b["items_first_pass"] if role == "verify" else b["succeeded"]
            if sw.get(role) is None:
                out["notes"].append(f"{role}: stage time excluded from this basis (see its meta file), counted as 0 s")
            seconds[role] = ((sw.get(role) or 0) / done * count) if done else 0.0
        seconds["local_fixed_overhead"] = s["fixed_local_overhead_seconds"]
        out.update(seconds_by_stage={k: round(v, 1) for k, v in seconds.items()}, wall_seconds=sum(seconds.values()),
                   texts_per_second=round(bound, 2), workers=workers, parallel_efficiency=efficiency)
    else:
        out.update(seconds_by_stage=None, wall_seconds=None, texts_per_second=None, workers=None)
        out["notes"].append("elapsed time not projected: Batch API turnaround (up to 24 h per batch) was not measured"
                            if tier == "batch" else "elapsed time not projected: no enrichment timing in this basis")
    out["exceeds_budget"] = out["api_total"] > a["budget_usd"]
    out["cost_per_1000_rows"] = out["api_total"] / p["rows"] * 1000
    out["cost_per_completed_record"] = out["api_total"] / p["nonempty_rows"]
    return out


# ---------- report ----------------------------------------------------------------------------
def money(x):
    if 0 < abs(x) < 0.01:
        return f"${x:.6f}"
    return f"${x:,.4f}" if abs(x) < 100 else f"${x:,.2f}"


def duration(seconds):
    if seconds is None:
        return "not projected"
    return f"{seconds:,.1f} s" if seconds < 120 else (f"{seconds / 60:,.1f} min" if seconds < 7200 else f"{seconds / 3600:,.2f} h")


def build_report(folder, rates, rates_path, a, basis_name, multiplier):
    meta = json.loads((folder / "pilot_meta.json").read_text(encoding="utf-8"))
    calls = read_jsonl(folder / "pilot_calls.jsonl")
    records = read_jsonl(folder / "pilot_records.jsonl")
    usage_saved = read_csv(folder / "usage.csv")
    recomputed = usage_from_calls(calls)
    key = lambda u: tuple(str(u[c]) for c in USAGE_COLUMNS[:7])
    usage_matches = {key(u): int(u["billed_units"]) for u in usage_saved} == {key(u): int(u["billed_units"]) for u in recomputed}
    rows = priced(usage_saved, rates)
    L = []
    w = L.append
    w("# 100-review cost and runtime report")
    w("")
    w(f"Generated offline by `python cost/calculator.py` from saved measurements (no API key, no model calls). "
      f"Rates file: `{Path(rates_path).name}`" + (f" **x {multiplier:g} (what-if multiplier)**" if multiplier != 1 else "") + ".")
    w("")
    w("## 1. Measured on the supplied 100 reviews")
    w("")
    source = ROOT / "data" / meta["input_file"]
    id_check = "source CSV not present, IDs not rechecked"
    if source.exists():
        with source.open(encoding="utf-8-sig", newline="") as f:
            ids = [r["review_id"] for r in csv.DictReader(f)]
        id_check = "match" if sorted(ids) == sorted(r["review_id"] for r in records) else "MISMATCH"
    completed = sum(r["status"] == "completed" for r in records)
    w(f"- Input: `{meta['input_file']}`, sha256 `{meta['input_sha256']}` (manifest match: {meta.get('manifest_sha256_match')}); "
      f"{len(records)} saved record IDs vs source IDs: **{id_check}**.")
    w(f"- Records: **{completed} completed, {len(records) - completed} failed/quarantined**; {meta['distinct_texts']} unique texts; "
      f"{meta['rows'] - meta['distinct_texts']} rows served by exact-text reuse inside the cold run.")
    w(f"- Executed {meta['executed_at']} with provider `{meta['provider']}`, processing tier `{meta['tier']}`, {meta['runs']['cold']['workers']} worker.")
    w(f"- `usage.csv` equals the sums recomputed from `pilot_calls.jsonl`: **{usage_matches}**.")
    w("")
    w("### Stage settings")
    w("")
    w("| Stage | Provider | Model | Effort | Prompt / schema version (`stage_config`) | Batch size | Worker limit |")
    w("|---|---|---|---|---|---|---|")
    for role in ROLES:
        cfg = meta["config"]["roles"][role]
        batch = cfg.get("batch_size", 1 if role != "group" else f"1 issue ({cfg['examples_per_issue']} quotes)")
        w(f"| {role} | {meta['provider']} | `{cfg['model']}` | {cfg['effort']} | `{meta['stage_configs'][role]}` | {batch} | 1 |")
    w("| ingest, rank, export | local code / SQLite | - | - | - | CSV chunks of 20,000 rows | 1 |")
    w("")
    measured = {}
    for run_id in ("cold", "warm"):
        run_calls = [c for c in calls if c["run_id"] == run_id]
        m = summarize(run_calls, meta["runs"][run_id])
        measured[run_id] = m
        run_rows = [u for u in rows if u["run_id"] == run_id]
        total = sum(u["cost"] for u in run_rows)
        m["api_total"] = total
        wall = meta["runs"][run_id]["wall_seconds_end_to_end"]
        w(f"### {run_id.capitalize()} run ({'empty result cache' if run_id == 'cold' else 'saved result cache, unchanged settings'})")
        w("")
        w("| Stage | Requests (attempts) | Succeeded | Failed | Invalid-output retries | Transient retries | Uncached input tok | Cached input tok | Output tok (incl. reasoning) | Reasoning tok | API cost |")
        w("|---|---|---|---|---|---|---|---|---|---|---|")
        for role in ROLES:
            x = m["roles"][role]
            cost = sum(u["cost"] for u in run_rows if u["role"] == role)
            w(f"| {role} | {x['attempts']} | {x['succeeded']} | {x['failed']} | {x['invalid_output_retry_requests']} | {x['transient_retry_attempts']} | "
              f"{x['units']['input_uncached']:,} | {x['units']['input_cached']:,} | {x['units']['output']:,} | {x['reasoning_tokens']:,} | {money(cost)} |")
        w(f"| **total** | {len(run_calls)} | | | | | | | | | **{money(total)}** |")
        w("")
        sw = meta["runs"][run_id]["stage_wall_seconds"]
        w(f"- End-to-end wall-clock: **{wall:.2f} s** (stage clocks: " + ", ".join(f"{k} {v:.2f}s" for k, v in sw.items()) + "). "
          f"Summed request durations: {sum(x['call_seconds_sum'] for x in m['roles'].values()):.2f} s (not a wall-clock figure).")
        w(f"- Throughput: {meta['rows'] / wall:.2f} input rows/s end-to-end. API cost per 1,000 input rows: **{money(total / meta['rows'] * 1000)}**; "
          f"per completed record: **{money(total / max(completed, 1))}**.")
        missing = sum(x["missing_usage_calls"] for x in m["roles"].values())
        w(f"- New enrichment calls: **{m['roles']['enrich']['attempts']}**; downstream model calls: "
          f"{sum(m['roles'][r]['attempts'] for r in ('verify', 'group', 'memo'))}. Calls with missing usage: {missing}"
          + (" (their cost is unresolved, not zero)." if missing else "."))
        w("")
    w("### Billing items (billed_units x price / per_units)")
    w("")
    w("| Run | Stage | Model | Tier | Item | Billed units | Price | Per units | Currency | Cost | Price source (date checked) |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for u in rows:
        rate = rates[(u["model"], u["tier"], u["item"])]
        w(f"| {u['run_id']} | {u['role']} | `{u['model']}` | {u['tier']} | {u['item']} | {u['billed_units']:,} | {float(rate['price']) * multiplier:g} | "
          f"{int(float(rate['per_units'])):,} {rate['unit']}s | {rate['currency']} | {money(u['cost'])} | {rate['source_url']} ({rate['checked_on']}) |")
    api_total = sum(u["cost"] for u in rows)
    w("")
    w(f"**API subtotal, cold + warm: {money(api_total)}.** Local compute (this laptop): measured wall-clock only; "
      f"hardware/electricity cost = **{a['local_compute']['usd_per_hour'] if a['local_compute']['usd_per_hour'] is not None else 'unknown (not measured)'}**. "
      "Provider prompt-cache savings are the cached-input rows above (measured, not assumed); they are separate from the saved-result cache.")
    w("")

    # projections
    if basis_name == "pilot":
        basis, basis_label = measured["cold"], "the cold 100-review pilot"
    else:
        bmeta = json.loads((folder / f"{basis_name}_meta.json").read_text(encoding="utf-8"))
        bcalls = [dict(c, run_id=basis_name) for c in read_jsonl(folder / f"{basis_name}_calls.jsonl")]
        basis, basis_label = summarize(bcalls, bmeta), f"`{basis_name}` ({bmeta['rows']:,} rows, {bmeta['distinct_texts']:,} texts, {bmeta['workers']} worker(s))"
    p = a["projected"]
    w("## 2. Estimated before the full run")
    w("")
    w(f"Projection basis: **{basis_label}**. These are estimates, not measurements. Editable inputs: `cost/assumptions.json` or `--set key=value`.")
    w("")
    w(f"- Rows accounted for: **{p['rows']:,}** = {p['nonempty_rows']:,} nonempty outputs + {p['empty_text_quarantines']} empty-text quarantines.")
    w(f"- First-pass model work with valid exact-text reuse: **{p['distinct_nonempty_texts']:,}** distinct texts (no-reuse comparison: {p['nonempty_rows']:,}).")
    w(f"- Controls: budget **{money(a['budget_usd'])}**, output cap {a['output_token_cap_per_enrich_request']:,} tokens per enrichment request, "
      f"max {a['max_workers']} workers, stronger-model fallback fraction {a['max_fallback_fraction']:.0%} (no fallback model is used; "
      f"invalid items get one same-model retry, then quarantine). Verification: {p['verify_sample_reviews']:,} reviews; grouping: {p['issues_to_name']} issues; memo: once.")
    w("")
    scenarios = [("base", True, None), ("conservative", True, None), ("base", False, None), ("base", True, "batch")]
    results = []
    w("| Scenario | Texts sent | Enrich | Verify | Group | Memo | **API total** | Per 1,000 rows | Requests | Elapsed (est.) | vs budget |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, reuse, tier in scenarios:
        if tier == "batch" and (basis["roles"]["enrich"]["model"], "batch", "output") not in rates:
            continue
        r = project(basis, a, name, rates, reuse=reuse, tier=tier)
        results.append(r)
        label = name + ("" if reuse else ", no exact-text reuse") + (", Batch API prices (hypothetical tier, not used)" if tier == "batch" else "")
        c = r["costs_by_stage"]
        w(f"| {label} | {r['texts_sent']:,} | {money(c['enrich'])} | {money(c['verify'])} | {money(c['group'])} | {money(c['memo'])} | **{money(r['api_total'])}** | "
          f"{money(r['cost_per_1000_rows'])} | {sum(r['requests_by_stage'].values()):,} | {duration(r['wall_seconds'])} | "
          f"{'**EXCEEDS BUDGET**' if r['exceeds_budget'] else 'within budget'} |")
    w("")
    for r in results[:2]:
        u = r["assumptions_used"]
        w(f"- **{r['scenario']}**: {u['input_tokens_per_text']} input + {u['output_tokens_per_text']} output tokens per text (measured), "
          f"cached-input share {u['cached_input_fraction']:.0%} (measured {u['measured_cached_input_fraction']:.0%}), "
          f"enrichment retry share {u['enrich_retry_fraction']:.1%} (measured {u['measured_enrich_retry_fraction']:.1%})"
          + (f", {r['workers']} workers at {r['parallel_efficiency']:.0%} of the measured per-worker speed (100% = this worker count was measured; lower = modelled) = {r['texts_per_second']} texts/s; "
             f"stage seconds {r['seconds_by_stage']}" if r["wall_seconds"] else "") + "."
          + ("".join(f" Note: {n}." for n in r["notes"])))
    if any(r["exceeds_budget"] for r in results):
        w("")
        w(f"> **WARNING: at least one scenario exceeds the declared budget of {money(a['budget_usd'])}.**")
    e = basis["roles"]["enrich"]
    if e["max_output_tokens_in_one_request"] > 0.8 * a["output_token_cap_per_enrich_request"]:
        w("")
        w(f"> **WARNING: an enrichment request used {e['max_output_tokens_in_one_request']:,} output tokens, above 80% of the cap.**")
    w("")
    w("Formulas: enrich = texts x (input/text x [(1-cached) x p_in + cached x p_cached] + output/text x p_out) "
      "+ retry_share x texts x (request_input/5 x p_in + output/text x p_out); verify = sample x measured cost per verified review x (1 + retry); "
      "group = issues x measured cost per naming call; memo = attempts x measured cost per memo call (charged once, never per review). "
      "Time = texts x (1 + retry) / min(measured texts/s per worker x workers x efficiency, token-per-minute limit, request-per-minute limit) "
      "+ verify + group + memo at measured per-item time + fixed local overhead once.")
    w("")
    w("The first 100 reviews are an initial estimate, not a guarantee about rare failures or sustained throughput; "
      "refresh with `python cost/calculator.py refresh` after the 500 and 10,000 checkpoints before starting the full run.")
    w("")
    return "\n".join(L), {"api_subtotal_measured": api_total, "measured_wall_seconds": {k: meta["runs"][k]["wall_seconds_end_to_end"] for k in ("cold", "warm")},
                          "usage_matches_call_log": usage_matches, "projections": results}


def set_path(obj, dotted, raw):
    *parents, leaf = dotted.split(".")
    for key in parents:
        obj = obj[key]
    try:
        obj[leaf] = json.loads(raw)
    except ValueError:
        obj[leaf] = raw


def cmd_replay(args):
    folder = Path(args.dir)
    if not (folder / "pilot_calls.jsonl").exists():
        print(f"No pilot measurements in {folder} yet. Run the explicit paid pilot first:\n  python cost/calculator.py pilot --execute")
        return 1
    a = json.loads(Path(args.assumptions).read_text(encoding="utf-8"))
    for item in args.set or []:
        key, _, value = item.partition("=")
        set_path(a, key, value)
    rates = load_rates(args.rates, args.rate_multiplier)
    report, summary = build_report(folder, rates, args.rates, a, args.basis, args.rate_multiplier)
    if not (args.set or args.rate_multiplier != 1 or args.no_write):
        name = "report.md" if args.basis == "pilot" else f"report_{args.basis}.md"  # the pilot report is never overwritten by a refresh
        (folder / name).write_text(report + "\n", encoding="utf-8")
    print(report)
    print("\n---")
    print(f"API subtotal (measured usage x rates): {money(summary['api_subtotal_measured'])}")
    print(f"Measured wall-clock: cold {summary['measured_wall_seconds']['cold']:.2f} s, warm {summary['measured_wall_seconds']['warm']:.2f} s (independent of rates)")
    for r in summary["projections"]:
        print(f"Projected {r['scenario']:12s} tier={r['tier']:8s} reuse={str(r['exact_text_reuse']):5s} -> {money(r['api_total'])}, "
              f"{duration(r['wall_seconds'])}{'  ** EXCEEDS BUDGET **' if r['exceeds_budget'] else ''}")
    if args.set or args.rate_multiplier != 1 or args.no_write:
        print("(what-if run: report.md was not overwritten)")
    return 0


# ---------- explicit paid pilot ---------------------------------------------------------------
def stage_seconds(run_dir, invocation_filter=None):
    seconds, events, workers = defaultdict(float), {}, 1
    for line in (run_dir / "stage_log.jsonl").read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        seconds[s["stage"]] += s.get("wall_seconds", 0)
        if s["stage"] == "enrich":
            events, workers = s.get("events", {}), s.get("workers", 1)
    return {k: round(v, 3) for k, v in seconds.items()}, events, workers


def db_calls(db_path, where="1=1", args=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT * FROM calls WHERE {where} ORDER BY call_id", args).fetchall()
    conn.close()
    return rows


def call_record(r, run_id):
    return {"run_id": run_id, "request_id": r["request_id"] or f"local-{r['call_id']}", "role": r["role"],
            "review_ids": json.loads(r["review_ids"]), "n_items": r["n_items"], "provider": r["provider"], "model": r["model"],
            "effort": r["effort"], "tier": r["tier"], "phase": r["phase"], "attempt_kind": r["attempt_kind"],
            "outcome": "succeeded" if r["outcome"] == "succeeded" else "failed", "raw_outcome": r["outcome"], "error": r["error"],
            "label_config": r["stage_config"], "input_tokens": r["input_tokens"], "cached_input_tokens": r["cached_input_tokens"],
            "output_tokens": r["output_tokens"], "reasoning_tokens": r["reasoning_tokens"], "usage_source": r["usage_source"],
            "started_at_unix": round(r["started_at"], 3), "duration_s": None if r["duration_s"] is None else round(r["duration_s"], 3),
            "worker": r["worker"]}


def cmd_pilot(args):
    if not args.execute:
        print("Refusing to start: the pilot makes real (paid) model calls. Re-run with --execute to confirm.")
        return 2
    out = Path(args.out_dir)
    if args.provider == "mock" and out.resolve() == HERE:
        print("Mock pilots must not overwrite real pilot evidence: pass --out-dir <scratch folder>.")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    from pipeline.config import Config
    from pipeline.util import sha_file, load_dotenv
    load_dotenv()
    cfg = Config(provider=args.provider)
    source = Path(args.input)
    db_path = out / "pilot_state.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)  # empty result cache for this experiment
    runs, windows = {}, {}
    for run_id in ("cold", "warm"):
        run_dir = out / f"pilot_run_{run_id}"
        shutil.rmtree(run_dir, ignore_errors=True)
        command = [sys.executable, "-m", "pipeline", "run", str(source), "--db", str(db_path), "--run-dir", str(run_dir),
                   "--workers", "1", "--budget-usd", str(args.budget_usd)] + (["--provider", args.provider] if args.provider else [])
        print(f"\n=== {run_id} run: {' '.join(command[1:])}")
        t0 = time.time()
        code = subprocess.run(command, cwd=ROOT).returncode
        t1 = time.time()
        windows[run_id] = (t0, t1)
        seconds, events, workers = stage_seconds(run_dir)
        runs[run_id] = {"wall_seconds_end_to_end": round(t1 - t0, 3), "stage_wall_seconds": seconds, "workers": workers,
                        "enrich_events": events, "exit_code": code}
    calls = []
    for run_id, (t0, t1) in windows.items():
        calls += [call_record(r, run_id) for r in db_calls(db_path, "started_at >= ? AND started_at <= ?", (t0, t1))]
    with (out / "pilot_calls.jsonl").open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(c, ensure_ascii=False) + "\n" for c in calls)
    cold_records = (out / "pilot_run_cold" / "grading" / "records.jsonl").read_text(encoding="utf-8")
    warm_records = (out / "pilot_run_warm" / "grading" / "records.jsonl").read_text(encoding="utf-8")
    (out / "pilot_records.jsonl").write_text(warm_records, encoding="utf-8")
    write_csv(out / "usage.csv", USAGE_COLUMNS, usage_from_calls(calls))
    report = json.loads((out / "pilot_run_cold" / "ingestion_report.json").read_text(encoding="utf-8"))
    meta = {
        "input_file": source.name, "input_sha256": sha_file(source), "manifest_sha256_match": report.get("manifest_check", {}).get("sha256_matches"),
        "rows": report["counts"]["records"], "distinct_texts": report["classification_scope"]["distinct_nonempty_texts"],
        "provider": cfg.provider, "tier": cfg.tier, "executed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(windows["cold"][0])),
        "stage_configs": {role: cfg.stage_config(role) for role in ROLES}, "config": cfg.raw, "runs": runs,
        "records_identical_cold_vs_warm": cold_records == warm_records,
        "budget_cap_usd_for_pilot": args.budget_usd,
    }
    (out / "pilot_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    print(f"\nSaved pilot_calls.jsonl ({len(calls)} attempts), pilot_records.jsonl, usage.csv, pilot_meta.json in {out}")
    args.dir, args.set, args.no_write, args.basis = str(out), None, False, "pilot"
    return cmd_replay(args)


def cmd_refresh(args):
    """Save a later checkpoint run (500, 10,000 ...) as a projection basis. Reads saved files only."""
    run_dir = Path(args.run_dir)
    summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    seconds, events, workers = stage_seconds(run_dir)
    # Keep only calls made by this run folder's own invocations, so usage and stage clocks describe the
    # same work (texts cached from an earlier checkpoint were not re-sent and are not counted here).
    own = {json.loads(line)["invocation"] for line in (run_dir / "stage_log.jsonl").read_text(encoding="utf-8").splitlines()}
    calls = [c for c in read_jsonl(run_dir / "run_log.jsonl") if c["invocation"] in own]
    out = Path(args.dir)
    with (out / f"{args.name}_calls.jsonl").open("w", encoding="utf-8") as f:
        for c in calls:
            c["outcome"] = "succeeded" if c["outcome"] == "succeeded" else "failed"
            f.write(json.dumps(c) + "\n")
    excluded = {}
    for item in args.exclude_stage_time or []:
        stage, _, reason = item.partition("=")
        excluded[stage] = {"measured_wall_seconds": seconds.get(stage), "reason": reason}
        seconds[stage] = None
    meta = {"source_run_dir": run_dir.name, "input_file": summary["input_file"], "input_sha256": summary["input_sha256"],
            "stage_times_excluded_from_projection": excluded,
            "rows": summary["counts"]["n_rows"], "distinct_texts": summary["counts"]["distinct_texts"], "workers": workers,
            "stage_wall_seconds": seconds, "label_config": summary["label_config"],
            "note": "Enrichment timing covers the invocations logged in this run folder; texts already cached from an earlier "
                    "checkpoint were not re-sent."}
    (out / f"{args.name}_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {args.name}_calls.jsonl and {args.name}_meta.json. Project with: python cost/calculator.py --basis {args.name}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default=str(HERE), help="folder holding pilot_*.jsonl, usage.csv, pilot_meta.json")
    parser.add_argument("--rates", default=str(HERE / "rates.csv"))
    parser.add_argument("--assumptions", default=str(HERE / "assumptions.json"))
    parser.add_argument("--rate-multiplier", type=float, default=1.0, help="what-if: multiply every API price")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE", help="what-if override of an assumptions.json entry (dotted key)")
    parser.add_argument("--basis", default="pilot", help="measurement used for projections: pilot (default) or a saved refresh name")
    parser.add_argument("--no-write", action="store_true", help="print only; do not rewrite report.md")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("pilot", help="EXPLICIT PAID COMMAND: run the cold and warm 100-review pilot")
    p.add_argument("--execute", action="store_true", help="required confirmation that paid calls may be made")
    p.add_argument("--input", default=str(ROOT / "data" / "cost_100.csv"))
    p.add_argument("--provider", default=None, choices=("openai", "mock"))
    p.add_argument("--budget-usd", type=float, default=0.50)
    p.add_argument("--out-dir", default=str(HERE))
    p = sub.add_parser("refresh", help="save a checkpoint run as a projection basis (no model calls)")
    p.add_argument("--name", required=True)
    p.add_argument("--run-dir", required=True)
    p.add_argument("--exclude-stage-time", action="append", metavar="STAGE=REASON",
                   help="keep a stage's measured time out of the projection (the measured value and reason are saved)")
    args = parser.parse_args(argv)
    if args.command == "pilot":
        return cmd_pilot(args)
    if args.command == "refresh":
        return cmd_refresh(args)
    return cmd_replay(args)


if __name__ == "__main__":
    sys.exit(main())
