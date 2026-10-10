# 100-review cost and runtime report

Generated offline by `python cost/calculator.py` from saved measurements (no API key, no model calls). Rates file: `rates.csv`.

## 1. Measured on the supplied 100 reviews

- Input: `cost_100.csv`, sha256 `c884ac3b9be5066995d5063f96ad9af6e5e082975788c1684c4f6b6ea661dd0e` (manifest match: True); 100 saved record IDs vs source IDs: **match**.
- Records: **100 completed, 0 failed/quarantined**; 100 unique texts; 0 rows served by exact-text reuse inside the cold run.
- Executed 2026-10-07T20:02:18Z with provider `openai`, processing tier `standard`, 1 worker.
- `usage.csv` equals the sums recomputed from `pilot_calls.jsonl`: **True**.

### Stage settings

| Stage | Provider | Model | Effort | Prompt / schema version (`stage_config`) | Batch size | Worker limit |
|---|---|---|---|---|---|---|
| enrich | openai | `gpt-6-luna` | none | `gpt-6-luna@none|enrich_p1|schema-s1|4fe3fe6e` | 50 | 1 |
| verify | openai | `gpt-6-luna` | low | `gpt-6-luna@low|verify_v1|schema-s1|a240da73` | 20 | 1 |
| group | openai | `gpt-6-luna` | low | `gpt-6-luna@low|group_g1|schema-s1|1fbb6d6a` | 1 issue (12 quotes) | 1 |
| memo | openai | `gpt-6-luna` | medium | `gpt-6-luna@medium|memo_m1|schema-s1|09d5f306` | 1 | 1 |
| ingest, rank, export | local code / SQLite | - | - | - | CSV chunks of 20,000 rows | 1 |

### Cold run (empty result cache)

| Stage | Requests (attempts) | Succeeded | Failed | Invalid-output retries | Transient retries | Uncached input tok | Cached input tok | Output tok (incl. reasoning) | Reasoning tok | API cost |
|---|---|---|---|---|---|---|---|---|---|---|
| enrich | 4 | 4 | 0 | 2 | 0 | 4,176 | 4,659 | 1,678 | 0 | $0.0013 |
| verify | 1 | 1 | 0 | 0 | 0 | 937 | 0 | 590 | 401 | $0.0004 |
| group | 16 | 16 | 0 | 0 | 0 | 6,379 | 0 | 1,722 | 985 | $0.0015 |
| memo | 1 | 1 | 0 | 0 | 0 | 6,052 | 0 | 1,391 | 581 | $0.0013 |
| **total** | 22 | | | | | | | | | **$0.0045** |

- End-to-end wall-clock: **88.08 s** (stage clocks: ingest 0.00s, enrich 19.72s, verify 7.82s, group 43.45s, rank 0.00s, memo 16.84s, export 0.03s). Summed request durations: 87.62 s (not a wall-clock figure).
- Throughput: 1.14 input rows/s end-to-end. API cost per 1,000 input rows: **$0.0449**; per completed record: **$0.0000**.
- New enrichment calls: **4**; downstream model calls: 18. Calls with missing usage: 0.

### Warm run (saved result cache, unchanged settings)

| Stage | Requests (attempts) | Succeeded | Failed | Invalid-output retries | Transient retries | Uncached input tok | Cached input tok | Output tok (incl. reasoning) | Reasoning tok | API cost |
|---|---|---|---|---|---|---|---|---|---|---|
| enrich | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | $0.0000 |
| verify | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | $0.0000 |
| group | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | $0.0000 |
| memo | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | $0.0000 |
| **total** | 0 | | | | | | | | | **$0.0000** |

- End-to-end wall-clock: **0.18 s** (stage clocks: ingest 0.00s, enrich 0.00s, verify 0.00s, group 0.00s, rank 0.00s, memo 0.00s, export 0.01s). Summed request durations: 0.00 s (not a wall-clock figure).
- Throughput: 558.66 input rows/s end-to-end. API cost per 1,000 input rows: **$0.0000**; per completed record: **$0.0000**.
- New enrichment calls: **0**; downstream model calls: 0. Calls with missing usage: 0.

### Billing items (billed_units x price / per_units)

| Run | Stage | Model | Tier | Item | Billed units | Price | Per units | Currency | Cost | Price source (date checked) |
|---|---|---|---|---|---|---|---|---|---|---|
| cold | enrich | `gpt-6-luna` | standard | input_cached | 4,659 | 0.01 | 1,000,000 tokens | USD | $0.0000 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | enrich | `gpt-6-luna` | standard | input_uncached | 4,176 | 0.1 | 1,000,000 tokens | USD | $0.0004 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | enrich | `gpt-6-luna` | standard | output | 1,678 | 0.5 | 1,000,000 tokens | USD | $0.0008 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | group | `gpt-6-luna` | standard | input_cached | 0 | 0.01 | 1,000,000 tokens | USD | $0.0000 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | group | `gpt-6-luna` | standard | input_uncached | 6,379 | 0.1 | 1,000,000 tokens | USD | $0.0006 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | group | `gpt-6-luna` | standard | output | 1,722 | 0.5 | 1,000,000 tokens | USD | $0.0009 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | memo | `gpt-6-luna` | standard | input_cached | 0 | 0.01 | 1,000,000 tokens | USD | $0.0000 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | memo | `gpt-6-luna` | standard | input_uncached | 6,052 | 0.1 | 1,000,000 tokens | USD | $0.0006 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | memo | `gpt-6-luna` | standard | output | 1,391 | 0.5 | 1,000,000 tokens | USD | $0.0007 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | verify | `gpt-6-luna` | standard | input_cached | 0 | 0.01 | 1,000,000 tokens | USD | $0.0000 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | verify | `gpt-6-luna` | standard | input_uncached | 937 | 0.1 | 1,000,000 tokens | USD | $0.0001 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |
| cold | verify | `gpt-6-luna` | standard | output | 590 | 0.5 | 1,000,000 tokens | USD | $0.0003 | https://developers.openai.com/api/docs/models/gpt-6-luna (2026-10-04) |

**API subtotal, cold + warm: $0.0045.** Local compute (this laptop): measured wall-clock only; hardware/electricity cost = **unknown (not measured)**. Provider prompt-cache savings are the cached-input rows above (measured, not assumed); they are separate from the saved-result cache.

## 2. Estimated before the full run

Projection basis: **the cold 100-review pilot**. These are estimates, not measurements. Editable inputs: `cost/assumptions.json` or `--set key=value`.

- Rows accounted for: **660,622** = 660,609 nonempty outputs + 13 empty-text quarantines.
- First-pass model work with valid exact-text reuse: **484,189** distinct texts (no-reuse comparison: 660,609).
- Controls: budget **$25.0000**, output cap 3,000 tokens per enrichment request, max 8 workers, stronger-model fallback fraction 0% (no fallback model is used; invalid items get one same-model retry, then quarantine). Verification: 2,000 reviews; grouping: 22 issues; memo: once.

| Scenario | Texts sent | Enrich | Verify | Group | Memo | **API total** | Per 1,000 rows | Requests | Elapsed (est.) | vs budget |
|---|---|---|---|---|---|---|---|---|---|---|
| base | 484,189 | $6.8147 | $0.0408 | $0.0021 | $0.0013 | **$6.8589** | $0.0104 | 12,718 | 4.54 h | within budget |
| conservative | 484,189 | $11.1505 | $0.0486 | $0.0021 | $0.0026 | **$11.2038** | $0.0170 | 24,359 | 15.57 h | within budget |
| base, no exact-text reuse | 660,609 | $9.2978 | $0.0408 | $0.0021 | $0.0013 | **$9.3419** | $0.0141 | 17,305 | 6.09 h | within budget |
| base, Batch API prices (hypothetical tier, not used) | 484,189 | $3.4074 | $0.0204 | $0.0010 | $0.0007 | **$3.4295** | $0.0052 | 12,718 | not projected | within budget |

- **base**: 54.88 input + 16.19 output tokens per text (measured), cached-input share 28% (measured 28%), enrichment retry share 3.0% (measured 3.0%), 8 workers at 80% parallel efficiency (modelled, not measured) = 32.46 texts/s; stage seconds {'enrich': 15363.5, 'verify': 782.0, 'group': 59.7, 'memo': 16.8, 'local_fixed_overhead': 120}.
- **conservative**: 54.88 input + 16.19 output tokens per text (measured), cached-input share 0% (measured 28%), enrichment retry share 15.0% (measured 3.0%), 4 workers at 50% parallel efficiency (modelled, not measured) = 10.14 texts/s; stage seconds {'enrich': 54891.1, 'verify': 782.0, 'group': 59.7, 'memo': 33.7, 'local_fixed_overhead': 300}.

Formulas: enrich = texts x (input/text x [(1-cached) x p_in + cached x p_cached] + output/text x p_out) + retry_share x texts x (request_input/5 x p_in + output/text x p_out); verify = sample x measured cost per verified review x (1 + retry); group = issues x measured cost per naming call; memo = attempts x measured cost per memo call (charged once, never per review). Time = texts x (1 + retry) / min(measured texts/s per worker x workers x efficiency, token-per-minute limit, request-per-minute limit) + verify + group + memo at measured per-item time + fixed local overhead once.

The first 100 reviews are an initial estimate, not a guarantee about rare failures or sustained throughput; refresh with `python cost/calculator.py refresh` after the 500 and 10,000 checkpoints before starting the full run.

