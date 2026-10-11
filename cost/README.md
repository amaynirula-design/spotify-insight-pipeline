# 100-review cost and runtime calculator

Everything here can be recomputed from saved measurements **without an API key, network access or model calls**.
Opening or importing `calculator.py` never starts a paid call; the pilot is a separate, explicit command.

Run all commands from the repository root. Python 3.10+, standard library only (use `python3` if `python` is not
on your PATH).

## Offline replay (default)

```bash
python cost/calculator.py                        # recompute the measured pilot costs and the full-run projection
python cost/calculator.py --rate-multiplier 2    # doubles every API price: API subtotal doubles, measured time does not
python cost/calculator.py --set projected.distinct_nonempty_texts=600000   # projection changes, pilot results do not
python cost/calculator.py --set budget_usd=5     # prints EXCEEDS BUDGET warnings
python cost/calculator.py --set scenarios.base.workers=8 --set max_fallback_fraction=0
python cost/calculator.py --rates my_rates.csv --assumptions my_assumptions.json
python cost/calculator.py --basis refresh_500    # project from the 500-review checkpoint
python cost/calculator.py --basis refresh_10000  # ... from the 10,000-review checkpoint
python cost/calculator.py --basis full_run       # ... from the completed full run (estimate vs actual)
```

What-if runs (`--set`, `--rate-multiplier`, `--no-write`) only print; they never overwrite a saved report.

## Explicit paid pilot (needs `OPENAI_API_KEY` in `.env`)

```bash
python cost/calculator.py pilot --execute        # refuses to run without --execute
```

This deletes `cost/pilot_state.db` (empty result cache), runs the whole pipeline on `data/cost_100.csv` unchanged
with one worker (cold), runs it again on the saved cache (warm), and writes the evidence files below. Hard spend
cap: `--budget-usd` (default $0.50).

## Files

| File | Contents |
|---|---|
| `calculator.py` | the calculator (offline replay, `pilot`, `refresh`) |
| `pilot_records.jsonl` | one final record per pilot review ID: original row hash, status and the common labels |
| `pilot_calls.jsonl` | every attempted model call in the cold and warm runs: run ID, request ID, role, model, effort, tier, attempt kind, outcome, review IDs, raw token usage, duration. No keys |
| `usage.csv` | billed units per run / stage / model / billing item, each row naming the call-log rows it sums. The calculator re-derives it from `pilot_calls.jsonl` and reports whether they match |
| `rates.csv` | editable prices: unit, price, `per_units`, currency, tier, source URL and the date checked. Batch-tier rows are marked `scenario_only` |
| `assumptions.json` | editable projection inputs: budget, output-token cap, maximum workers, fallback fraction, projected row counts, retry and cache assumptions for the base and conservative scenarios, rate limits, local-compute cost (unknown) |
| `pilot_meta.json` | input checksum, executed-at time, configuration snapshot, end-to-end wall-clock and per-stage clocks for both runs |
| `report.md` | **the measured 100-review report and full-run projection** |
| `pilot_run_cold/`, `pilot_run_warm/` | the pipeline's own outputs for the two runs (enriched records, verifier report, issues, ranking, memo, call log) |
| `refresh_500_*`, `refresh_10000_*`, `full_run_*` and `report_*.md` | later measurements used to refresh the projection, and their reports |
| `archive_p1_first_pilot/` | evidence from the first pilot, run under the earlier prompt version `enrich_p1` |

## Arithmetic

`item_cost = billed_units × (price / per_units)`, summed over three mutually exclusive items per call: uncached
input (`input_tokens − cached_tokens`), cached input, and output. Reasoning tokens are already inside the
provider's output count and are not added again. Only the standard tier was used; Batch API prices appear in one
clearly labelled hypothetical scenario. Projection formulas are printed at the end of each report. Wall-clock time
is measured around the whole run; summed request durations are reported separately and are not used as elapsed
time. Local compute is reported as measured seconds with cost **unknown**, not zero.

## Measured pilot (2026-10-07, `gpt-6-luna`, 1 worker)

| | Cold (empty cache) | Warm (saved results) |
|---|---|---|
| Records completed / failed | 100 / 0 | 100 / 0 |
| Model call attempts | 19 (3 enrich incl. 1 retry, 1 verify, 14 group, 1 memo) | 0 |
| API cost | $0.004438 | $0.00 |
| End-to-end wall-clock | 84.41 s | 0.38 s |

## Budget, concurrency, fallback limit and scaling decision

- **Budget:** $15 hard cap for the full run (`--budget-usd 15`), against a base estimate of about $6.50 and a
  conservative estimate of about $11.70. The orchestrator reserves each request's worst-case cost before sending it
  and stops admitting work when spent + reserved + next reservation would exceed the cap.
- **Output cap:** 2,000 output tokens per enrichment request (measured typical: about 820 for 50 reviews).
- **Fallback:** no stronger-model fallback (`max_fallback_fraction: 0`). Invalid output gets one same-model retry in
  batches of five, then a deterministic quote or quarantine.
- **Concurrency:** 1 worker (pilot) → 2 (500 reviews) → 8 (10,000 reviews) → 24 (full run). Per-worker speed stayed
  near 6 texts/s at each step with no rate-limit errors. The account's limits, read from response headers on
  2026-10-07, are 5,000 requests and 2,000,000 tokens per minute; 24 workers used roughly half of the token limit.
- **Decision:** run the full corpus on the standard tier with exact-text reuse (484,189 texts instead of 660,609)
  and 24 workers. The Batch API would have roughly halved the cost but was not measured and has up-to-24-hour
  turnaround, so it stays a labelled scenario.
- **Outcome:** $5.91 actual API cost for all calls behind the full export; enrichment took 51 minutes at about 154
  texts/s (`report_full_run.md`, `../runs/spotify_reviews_18months__gpt-6-luna-none-4aab826f/run_summary.json`).

Estimates made at each step (base / conservative): pilot $6.66 / $11.54; 500 reviews $5.72 / $11.48;
10,000 reviews $6.48 / $11.68.
