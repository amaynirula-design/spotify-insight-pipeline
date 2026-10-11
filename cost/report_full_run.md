# 100-review cost and runtime report

Generated offline by `python cost/calculator.py` from saved measurements (no API key, no model calls). Rates file: `rates.csv`.

## 1. Measured on the supplied 100 reviews

- Input: `cost_100.csv`, sha256 `c884ac3b9be5066995d5063f96ad9af6e5e082975788c1684c4f6b6ea661dd0e` (manifest match: True); 100 saved record IDs vs source IDs: **match**.
- Records: **100 completed, 0 failed/quarantined**; 100 unique texts; 0 rows served by exact-text reuse inside the cold run.
- Executed 2026-10-07T20:05:25Z with provider `openai`, processing tier `standard`, 1 worker.
- `usage.csv` equals the sums recomputed from `pilot_calls.jsonl`: **True**.

### Stage settings

| Stage | Provider | Model | Effort | Prompt / schema version (`stage_config`) | Batch size | Worker limit |
|---|---|---|---|---|---|---|
| enrich | openai | `gpt-6-luna` | none | `gpt-6-luna@none|enrich_p2|schema-s1|4aab826f` | 50 | 1 |
| verify | openai | `gpt-6-luna` | low | `gpt-6-luna@low|verify_v1|schema-s1|a240da73` | 20 | 1 |
| group | openai | `gpt-6-luna` | low | `gpt-6-luna@low|group_g1|schema-s1|1fbb6d6a` | 1 issue (12 quotes) | 1 |
| memo | openai | `gpt-6-luna` | medium | `gpt-6-luna@medium|memo_m1|schema-s1|09d5f306` | 1 | 1 |
| ingest, rank, export | local code / SQLite | - | - | - | CSV chunks of 20,000 rows | 1 |

### Cold run (empty result cache)

| Stage | Requests (attempts) | Succeeded | Failed | Invalid-output retries | Transient retries | Uncached input tok | Cached input tok | Output tok (incl. reasoning) | Reasoning tok | API cost |
|---|---|---|---|---|---|---|---|---|---|---|
| enrich | 3 | 3 | 0 | 1 | 0 | 4,085 | 3,376 | 1,637 | 0 | $0.001261 |
| verify | 1 | 1 | 0 | 0 | 0 | 937 | 0 | 772 | 582 | $0.000480 |
| group | 14 | 14 | 0 | 0 | 0 | 5,928 | 0 | 1,466 | 794 | $0.001326 |
| memo | 1 | 1 | 0 | 0 | 0 | 6,064 | 0 | 1,531 | 713 | $0.001372 |
| **total** | 19 | | | | | | | | | **$0.004438** |

- End-to-end wall-clock: **84.41 s** (stage clocks: ingest 0.00s, enrich 15.55s, verify 7.66s, group 46.06s, rank 0.01s, memo 14.80s, export 0.10s). Summed request durations: 83.94 s (not a wall-clock figure).
- Throughput: 1.18 input rows/s end-to-end. API cost per 1,000 input rows: **$0.0444**; per completed record: **$0.000044**.
- Result-cache hits: **0 of 100** distinct texts were served from saved results (100 were sent to the enrichment model).
- New enrichment calls: **3**; downstream model calls: 16. Calls with missing usage: 0.

### Warm run (saved result cache, unchanged settings)

| Stage | Requests (attempts) | Succeeded | Failed | Invalid-output retries | Transient retries | Uncached input tok | Cached input tok | Output tok (incl. reasoning) | Reasoning tok | API cost |
|---|---|---|---|---|---|---|---|---|---|---|
| enrich | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | $0.0000 |
| verify | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | $0.0000 |
| group | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | $0.0000 |
| memo | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | $0.0000 |
| **total** | 0 | | | | | | | | | **$0.0000** |

- End-to-end wall-clock: **0.38 s** (stage clocks: ingest 0.00s, enrich 0.00s, verify 0.00s, group 0.00s, rank 0.00s, memo 0.00s, export 0.01s). Summed request durations: 0.00 s (not a wall-clock figure).
- Throughput: 264.55 input rows/s end-to-end. API cost per 1,000 input rows: **$0.0000**; per completed record: **$0.0000**.
- Result-cache hits: **100 of 100** distinct texts were served from saved results (0 were sent to the enrichment model).
- New enrichment calls: **0**; downstream model calls: 0. Calls with missing usage: 0.

### Billing items (billed_units x price / per_units)

| Run | Stage | Model | Tier | Item | Billed units | Price | Per units | Currency | Cost | Price source (date checked) |
|---|---|---|---|---|---|---|---|---|---|---|
| cold | enrich | `gpt-6-luna` | standard | input_cached | 3,376 | 0.01 | 1,000,000 tokens | USD | $0.000034 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | enrich | `gpt-6-luna` | standard | input_uncached | 4,085 | 0.1 | 1,000,000 tokens | USD | $0.000409 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | enrich | `gpt-6-luna` | standard | output | 1,637 | 0.5 | 1,000,000 tokens | USD | $0.000819 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | group | `gpt-6-luna` | standard | input_cached | 0 | 0.01 | 1,000,000 tokens | USD | $0.0000 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | group | `gpt-6-luna` | standard | input_uncached | 5,928 | 0.1 | 1,000,000 tokens | USD | $0.000593 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | group | `gpt-6-luna` | standard | output | 1,466 | 0.5 | 1,000,000 tokens | USD | $0.000733 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | memo | `gpt-6-luna` | standard | input_cached | 0 | 0.01 | 1,000,000 tokens | USD | $0.0000 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | memo | `gpt-6-luna` | standard | input_uncached | 6,064 | 0.1 | 1,000,000 tokens | USD | $0.000606 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | memo | `gpt-6-luna` | standard | output | 1,531 | 0.5 | 1,000,000 tokens | USD | $0.000766 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | verify | `gpt-6-luna` | standard | input_cached | 0 | 0.01 | 1,000,000 tokens | USD | $0.0000 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | verify | `gpt-6-luna` | standard | input_uncached | 937 | 0.1 | 1,000,000 tokens | USD | $0.000094 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | verify | `gpt-6-luna` | standard | output | 772 | 0.5 | 1,000,000 tokens | USD | $0.000386 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |

**API subtotal, cold + warm: $0.004438.** Local compute (this laptop): measured wall-clock only; hardware/electricity cost = **unknown (not measured)**. Provider prompt-cache savings are the cached-input rows above (measured, not assumed); they are separate from the saved-result cache.

## 2. Estimated before the full run

Projection basis: **`full_run` (660,622 rows, 484,189 texts, 24 worker(s))**. These are estimates, not measurements. Editable inputs: `cost/assumptions.json` or `--set key=value`.

- Rows accounted for: **660,622** = 660,609 nonempty outputs + 13 empty-text quarantines.
- First-pass model work with valid exact-text reuse: **484,189** distinct texts (no-reuse comparison: 660,609).
- Controls: budget **$15.0000**, output cap 2,000 tokens per enrichment request, max 24 workers, stronger-model fallback fraction 0% (no fallback model is used; invalid items get one same-model retry, then quarantine). Verification: 2,000 reviews; grouping: 22 issues; memo: once.

| Scenario | Texts sent | Enrich | Verify | Group | Memo | **API total** | Per 1,000 rows | Requests | Elapsed (est.) | vs budget |
|---|---|---|---|---|---|---|---|---|---|---|
| base | 484,189 | $6.4857 | $0.0447 | $0.003970 | $0.001358 | **$6.5357** | $0.009893 | 12,630 | 71.2 min | within budget |
| conservative | 484,189 | $11.9695 | $0.0532 | $0.003970 | $0.002716 | **$12.0294** | $0.0182 | 24,359 | 3.37 h | within budget |
| base, no exact-text reuse | 660,609 | $8.8489 | $0.0447 | $0.003970 | $0.001358 | **$8.8989** | $0.0135 | 17,185 | 90.9 min | within budget |
| base, Batch API prices (hypothetical tier, not used) | 484,189 | $3.2429 | $0.0223 | $0.001985 | $0.000679 | **$3.2679** | $0.004947 | 12,630 | not projected | within budget |

- **base**: 60.12 input + 16.85 output tokens per text (measured), cached-input share 56% (measured 56%), enrichment retry share 2.9% (measured 2.9%), 24 workers at 100% of the measured per-worker speed (100% = this worker count was measured; lower = modelled) = 153.77 texts/s; stage seconds {'enrich': 3240.4, 'verify': 708.4, 'group': 157.2, 'memo': 47.6, 'local_fixed_overhead': 120}.
- **conservative**: 60.12 input + 16.85 output tokens per text (measured), cached-input share 0% (measured 56%), enrichment retry share 15.0% (measured 2.9%), 8 workers at 100% of the measured per-worker speed (100% = this worker count was measured; lower = modelled) = 51.26 texts/s; stage seconds {'enrich': 10863.4, 'verify': 708.4, 'group': 157.2, 'memo': 95.3, 'local_fixed_overhead': 300}.

> **WARNING: an enrichment request used 2,000 output tokens, above 80% of the cap.**

Formulas: enrich = texts x (input/text x [(1-cached) x p_in + cached x p_cached] + output/text x p_out) + retry_share x texts x (request_input/5 x p_in + output/text x p_out); verify = sample x measured cost per verified review x (1 + retry); group = issues x measured cost per naming call; memo = attempts x measured cost per memo call (charged once, never per review). Time = texts x (1 + retry) / min(measured texts/s per worker x workers x efficiency, token-per-minute limit, request-per-minute limit) + verify + group + memo at measured per-item time + fixed local overhead once.

The first 100 reviews are an initial estimate, not a guarantee about rare failures or sustained throughput; refresh with `python cost/calculator.py refresh` after the 500 and 10,000 checkpoints before starting the full run.

