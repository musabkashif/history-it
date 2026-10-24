# OpenAI API cost concerns — 24 Oct 2026

## Client message

Hi Musa. URGENT update needed as I now must pause testing because the API costs are extremely high - they are much higher than the reported amounts in the application, but more alarmingly more than they need to be because of caching.

Here's the summary:

The staging application is using API key [REDACTED]. All GPT-5.6 Sol activity in the relevant period is on that key, so there is no indication that another API key is causing the unexpected spend.

For Oct. 16–23, the latest OpenAI activity export shows:

- 429 GPT-5.6 Sol requests
- 33.77M total input tokens
- 28.80M 30-minute cache-write tokens
- 4.97M cached-read tokens
- 3.44M output tokens

Using actual OpenAI pricing, that usage is approximately $214.80.

I found two issues with our internal accounting:

1. Cache-write pricing is wrong. Our current per-call estimate appears to treat non-cached input as ordinary $4/M input. GPT-5.6 Sol 30-minute cache writes are $5/M. With 28.80M cache-write tokens, this causes about $28.80 of under-reporting during this period.

2. Some requests are missing from the register usage logs. OpenAI shows 429 model requests. Across the Production Registers/test registers I can identify, we currently have 405 usage rows/calls. Using our current pricing methodology, the 24-call difference represents approximately another $10.69 absent from the internal ledger.

Our identifiable register costs total about $175.31, versus approximately $214.80 actual OpenAI usage, so these two findings essentially explain the approximately $39.50 discrepancy.

There is also an optimization issue worth investigating: we wrote approximately 28.8M tokens into cache but only read approximately 5.0M tokens from cache. Since cache writes cost more than normal input and cache reads are where the savings occur, I would like you to review whether our stable governing instructions/context are being structured and keyed so they can actually be reused across batch calls rather than rewritten on most calls.

For the production version, I think OpenAI Usage logging should capture the actual usage categories returned for every request:

- uncached input
- cached input
- 30-minute cache writes
- 12-hour cache writes
- output
- response ID
- attempt/status

Cost should then be calculated from those categories rather than estimated from total input tokens.

## Exact problem

OpenAI billed about $214.80 for GPT-5.6 Sol on the staging key for 16–23 Oct 2026. Identifiable register usage totals about $175.31. The gap is about $39.50. About $28.80 of that gap is cache-write tokens priced as ordinary input. About $10.69 is about 24 model requests that are absent from the registers Kristen can identify. Cache writes also far exceed cache reads, so the stable prompt is being written again on most calls instead of being read back.

## Reported figures

- 429 GPT-5.6 Sol requests
- 33.77M total input tokens
- 28.80M 30-minute cache-write tokens
- 4.97M cached-read tokens
- 3.44M output tokens
- approximately $214.80 actual OpenAI usage
- approximately $175.31 identifiable register usage
- approximately $39.50 discrepancy
- approximately 24 missing calls
- approximately $28.80 cache-write under-reporting

## Testing status

Paid testing is paused by client request. Do not run model calls, retry jobs, or start a new inspection until this accounting gap is understood and Kristen agrees to resume.

## Investigation required

- Actual usage fields returned by the OpenAI API, including any cache-write fields beyond total input and cached-read tokens.
- Cache-write and cached-read pricing, including the 30-minute cache-write rate versus ordinary input.
- Missing usage rows: requests that were billed but never written to a register.
- Retries and failed attempts, including connection retries, provider splits, and blocked inspections.
- Response IDs and request correlation between the OpenAI export, local diagnostics, and register rows.
- Cache key and prompt-prefix reuse across batch calls.
- Duplicate or repeated governing instructions inside the prompt that is sent on every call.

## Phase 1 usage fields

Phase 1 records usage only. It does not change the prompt, crop decisions, or commit behavior, and it does not optimize caching.

The OpenAI Usage sheet keeps its existing columns. These columns are added at the right edge:

- Uncached Input Tokens
- Cache Write 30m Tokens
- Cache Write 12h Tokens
- Part
- Failure Reason
- Billed

Call ID is `response:{response id}` and is not reused for another response. The installed Responses usage object has one `cache_write_tokens` count and does not name a retention. That count stays unscoped. It is not stored or priced as a 30-minute write. A 30-minute, 12-hour, or 24-hour column is filled only when the response names that category. An unscoped write has no application estimate. Named cache-write tokens are priced with their own rates, and that number is an application estimate, not an OpenAI charge:

- `OPENAI_CACHE_WRITE_30M_USD_PER_MILLION`
- `OPENAI_CACHE_WRITE_12H_USD_PER_MILLION`

They are not priced as ordinary input. A missing rate refuses the cost instead of substituting the input rate. A register write failure is copied to `usage-outbox` under the diagnostics directory and the batch note names that file. Connection failures that return no usage stay unbilled and do not receive invented token counts.

## Acceptance criteria

- Every model request has a usage record.
- Failed and retried requests are accounted for.
- Actual usage categories are stored.
- Cost is calculated from those categories.
- OpenAI export totals reconcile with internal logs.
- Cache writes are minimized and cache reads are maximized where valid.
- No API key or credential is exposed.
