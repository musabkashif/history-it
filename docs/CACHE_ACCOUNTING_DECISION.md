# Cache and accounting decision — 29 Oct 2026

Offline only. Staging remains on `0f5fe59` (29 Oct production fixes) with the
deployed **system-role** request format from `ee1f7d4`. The local explicit
prompt-cache change is **not** deployed and defaults **off** behind
`openai_prompt_cache_optimization_enabled`.

No new provider call, token-count call, retry, register write, commit, push, or
deployment was made for this decision update.

---

## Local comparison evidence (`compare-20261029T072738Z-0453f327`)

Sources:

- `server/harness/reports/manifests/compare-20261029T072738Z-0453f327.json`
- `server/harness/reports/cache_comparison_report.json`
- `server/harness/reports/cache_comparison_events.jsonl`

### What was observed

| Fact | Evidence |
|---|---|
| Six generation calls completed | `generation_calls_sent=6`, `generation_calls_with_usage=6` |
| Cache reuse on optimized follow-ups | Warm-up wrote `ttl_30m_tokens=12713`; calls 2–3 returned `cached_tokens=12713` and `cache_write_tokens=0` |
| All three quality fixtures passed | `regressions=[]`; 004 Metadata Only; 002 Archival Border Crop; 016b Rotate + Archival Border Crop |
| Savings | Report field `savings: "inconclusive"` |
| No percentage reduction claimable | Both arms have `application_estimate_usd: null`; ranges are not a proven saving |
| Baseline warm-up contaminated | `warm_up_contamination`: baseline `"warm-up returned cache-read tokens"` (`cached_tokens=12713` on baseline warm-up) |
| Cache-write retention on some baseline rows | Baseline generations returned `cache_write_tokens` **without** `cache_write_tokens_details` → retention stays **unknown** for those rows |
| Failed 26 Oct authorized run spend | Still **unknown** (crash while saving; no events jsonl; `spend_usd: null`) |
| Register writes | `writes_registers: false` |

Optimized arm token sketch (generation only):

| Order | Frame | cached | cache write (named 30m) | output |
|---:|---|---:|---:|---:|
| 1 warm-up | 004 | 0 | 12713 | 1305 |
| 2 | 002 | 12713 | 0 | 1013 |
| 3 | 016b | 12713 | 0 | 1588 |

Baseline arm: warm-up already had cache **reads**, so that arm is not a clean control.

### What this does **not** prove

- A bill reduction percentage for production or staging jobs.
- That deploying the developer/`prompt_cache_options` format is safe or cheaper under `service_tier=auto` (comparison pinned `default`).
- The OpenAI invoice for 16–23 Oct.
- Spend or call count for the failed 26 Oct run.

---

## Usage accounting review

### Already on staging (`ee1f7d4` → `0f5fe59`)

Each billed response can record, when the provider returns them:

- uncached input (derived when identity holds)
- cached input
- cache writes (unscoped and/or named 30m/12h/24h columns)
- output
- response ID
- attempt / part / status
- failure reason
- billed flag

Unknown stays unknown: unscoped `cache_write_tokens` are **not** priced as ordinary input; a missing rate is **not** $0.

### Local offline fix (this pass, not deployed)

Live comparison payloads nest named retention under
`input_tokens_details.cache_write_tokens_details.ttl_30m_tokens` /
`ttl_12h_tokens`. The local parser now reads those fields so a named 30-minute
write is stored as `cache_write_retention=30m`, not as an unscoped unknown.

Additional local (still undeployed) tightening from earlier WIP:

- missing input/cached/output rate → unknown, not $0
- input above 272,000 tokens on `gpt-5.6-sol` → unknown (short-context card)

**Do not ship** the request-format / `prompt_cache.py` change with accounting.
Accounting can ship later without enabling cache optimization.

---

## Reconciliation vs Kristen’s reported export (no provider contact)

Client-reported (24 Oct): ~429 GPT-5.6 Sol requests; ~33.77M input; ~28.80M
30-minute cache-write; ~4.97M cached-read; ~3.44M output; ~$214.80 actual;
~\$175.31 identifiable register; ~\$39.50 gap.

| Can reconcile from local evidence | Cannot reconcile without her export / more data |
|---|---|
| Mechanism for under-reporting: treating writes as ordinary input, and omitting some calls from registers | Exact match of 429 rows to OpenAI export line items |
| Staging import exists (47 jobs / 512 usage rows) as a **separate** ledger; not proven equal to the 405 register rows she counted | Whether 512 imported events cover the same 16–23 Oct window and key |
| Comparison run shows provider **can** return named `ttl_30m_tokens` | Mapping every historical unscoped write to 30m vs 12h on the old bill |
| Comparison shows cache **reads** after a stable prefix write (optimized arm) | Invoice dollars for the comparison or the failed 26 Oct run |
| Docs and code refuse unscoped write → ordinary input pricing | Proven \$ savings from deploying explicit cache |

Proven cost on staging remains **null** until export reconciliation is authorized and completed.

---

## Clean comparison follow-up (`clean-20261029T180136Z-9781941e`)

Sources:

- `server/harness/reports/clean-runs/clean-20261029T180136Z-9781941e/cache_comparison_report.json`
- `server/harness/reports/clean-runs/clean-20261029T180136Z-9781941e/cache_comparison_events.jsonl`

| Fact | Evidence |
|---|---|
| Cache reuse observed | Optimized warm-up: `cached=0`, `ttl_30m=12713`; follow-ups: `cached=12713` |
| Both warm-ups uncontaminated | `cached_input_tokens=0` on optimized and baseline warm-ups |
| Quality passed | All three F04 fixtures passed in both arms; `regressions=[]` |
| Savings not proven | `savings: "inconclusive"`; arm application estimates incomplete (baseline unscoped writes; some optimized follow-ups retention `none`) |
| Incomplete prior attempt | `clean-20261029T174759Z-d99cf263` remains `incomplete_unknown_outcome`; not reused |

Do **not** claim a percentage cost reduction from either comparison run.

---

## Decision options

### Option A — Keep the original request format as the default (current)

Default path stays **system-role** (`openai_prompt_cache_optimization_enabled=false`).
Staging/production remain on that shape until a staged test is explicitly enabled.

### Option B — Staging-only feature-flag rollout (proposed next)

Prepare (not deploy yet) the optimized developer / explicit 30m breakpoint behind
`openai_prompt_cache_optimization_enabled`:

1. Flag defaults **false** — identical to today’s request shape.
2. Enable **only** for an authorized staging test (env/setting on staging).
3. Immediate rollback: set the flag back to **false** (no code redeploy required
   if the flagged build is already on staging).
4. Every request records flag value, request shape, cache reads/writes, named TTL
   categories, response ID, and estimated/unknown cost.
5. Model, reasoning effort, image detail, schema, and output limits unchanged.
6. **Production deployment of the optimized shape remains blocked** until reliable
   cost evidence exists (complete application estimates and/or invoice reconciliation).

### Option C — Completed clean comparison (historical)

The authorized clean harness run above is recorded evidence for Option B. It does
**not** authorize production enablement.

---

## Recommendation

**Default remains Option A.** Propose a **staging-only, feature-flagged** rollout
(Option B) when operators authorize a staging test. Do **not** enable the flag in
production. Do **not** claim proven savings.

Rollback procedure for a staging test:

1. Set `OPENAI_PROMPT_CACHE_OPTIMIZATION_ENABLED=false` (or remove the override).
2. Restart/reload the staging process so Settings picks up the default.
3. Confirm diagnostics `request_shape=system_role` and no `prompt_cache_options`.

---

## Remaining blockers

1. Production enablement blocked until reliable cost evidence exists.
2. OpenAI export not loaded → no invoice reconciliation.
3. Staging cache-write rates still 0 → named writes stay unknown \$ until rates are set deliberately.
4. Application estimates still incomplete on some clean-comparison rows (unscoped / retention `none`).
5. Failed 26 Oct run spend/call count unknown forever unless OpenAI export covers that window.
6. Flagged code must not ship with the flag forced on; default must stay false.
7. UI work remains paused; not part of this decision.
