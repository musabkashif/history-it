# Clean cache savings comparison — preparation (not executed)

Prepared offline. **No provider calls, token counts, paid tests, commits, pushes, or deployments** were run for this prep.

Staging remains at `0f5fe59e8b359809b75c31663243c0a4a54dc641` (system-role baseline; no undeployed developer breakpoint).

## Isolation method

Exact mechanism:

1. Allocate a fresh `run_id` (`clean-<UTC>-<hex>`).
2. Set a distinct Responses `prompt_cache_key` per arm, always ≤64 characters:
   - `hitcc-o-<sha256(run_id|optimized)[:40]>`
   - `hitcc-b-<sha256(run_id|baseline)[:40]>`
3. Write evidence only under `server/harness/reports/clean-runs/<run_id>/` (empty folder required).
4. Prove a cold start on each arm: warm-up must report `cached_input_tokens = 0`; any warm-up cache read is contamination.
5. Do **not** reuse the failed 26 Oct run (`unknown_evidence`), `compare-20261029T072738Z-0453f327` (contaminated baseline), or `clean-20261029T174759Z-d99cf263` (incomplete; key-length 400; unknown outcome).

Production inspection still sends **no** `prompt_cache_key`.

## Pricing inputs (application estimates only)

`COMPARISON_APPLICATION_ESTIMATE_RATES` in `server/harness/cache_comparison.py`:

| Category | USD per million tokens |
| --- | ---: |
| Uncached input | 4 |
| Cached input | 0.40 |
| Output | 20 |
| Cache write 30m | 5 |
| Cache write 12h | 10 |

Label: `application_estimate_not_invoice_charge`.  
Basis: OpenAI Standard short-context card for `gpt-5.6-sol` (≤272k input), copied for this comparison only — **not** staging Settings and **not** invoice charges.  
Unscoped cache writes are **never** given an application estimate (conservative bound may use the 12h rate as an upper bound only).

Local usage parsing stores: uncached input, cached input, `cache_write_tokens_details.ttl_30m_tokens`, `ttl_12h_tokens`, output tokens, response ID, attempt/status, failure reason, billed/unknown.

## Comparison command

```text
D:\Integer\HistoryIT\history-it\server\.venv\Scripts\python.exe -m harness.cache_comparison --allow-paid-calls
```

Without `--allow-paid-calls` the module exits and makes no provider call.

## Arms and sequence

| Arm | Request format |
| --- | --- |
| Baseline | System-role string (deployed/staging shape) |
| Optimized | Developer role + explicit 30m breakpoint |

Shared: three approved F04 reviewed images; model `gpt-5.6-sol`; detail `high`; reasoning `high`; `InspectionOutput`; `max_output_tokens` 60000; `service_tier=default`.

Per arm: warm-up → follow-up 1 → follow-up 2. Order: optimized arm first, then baseline.

Stop before the next provider call if evidence cannot be saved.

## Acceptance rule

Pass only when:

1. All three quality outcomes pass in **both** arms.
2. Baseline and optimized warm-ups are **uncontaminated**.
3. Optimized follow-ups show **cache reads**.
4. Optimized total **application-estimate** cost is lower than baseline.
5. The difference is **larger than documented uncertainty** (overlapping estimate/bound ranges, or any unknown estimate).

Otherwise the result is **inconclusive**.

## Authorization still needed

Written permission to count and generate the six requests for the three local Box 31/F04 frames, with no client register write, no frozen-job retry, no staging deploy of the optimized format, and acknowledgment that dollars are application estimates — not invoice charges.

**This document does not authorize execution.**
