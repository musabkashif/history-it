# API cost optimization

This note records the caching contract, what the saved logs can and cannot show, and the local request change. It does not reconcile the OpenAI bill, and it does not claim that the change saves money. The local request change remains **uncommitted and undeployed**. Staging runs `0f5fe59` (29 Oct fixes) with the **system-role** request format from `ee1f7d4`; production was not changed for cache.

Authoritative decision after the completed local comparison: `docs/CACHE_ACCOUNTING_DECISION.md` (**Option A — keep original request format for now**).

## Provider contract

The app constructs `OpenAI(api_key=..., max_retries=0, timeout=settings.openai_timeout_seconds)` and does not set `base_url`. The installed SDK `openai` 2.54.0 defaults that client to `https://api.openai.com/v1/`. The call is `responses.parse`. The configured model is `gpt-5.6-sol`, with reasoning effort `high`, a 900-second timeout, and `max_output_tokens` 60000. Image parts stay `detail: high`.

That is the official OpenAI Responses API, not an OpenAI-compatible proxy. Caching behavior below is from OpenAI's prompt-caching guide and from the types this SDK will actually send.

Official source: [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), read 24 Oct 2026. The installed client adds a tighter limit than that page: `PromptCacheOptions.ttl` is typed only as `"30m"`, and the SDK text says `"30m"` is currently the only supported value.

### What this model family supports

`gpt-5.6-sol` is treated as GPT-5.6 or later because the model id starts with `gpt-5.6` and the SDK says `prompt_cache_options` is supported for `gpt-5.6` and later. A paid call has not confirmed the server-side family. If the provider rejects the new field, inspection must stop and this change must not be deployed.

| Item | Contract |
|---|---|
| Modes | Implicit is the default. `prompt_cache_options.mode = "explicit"` disables the automatic breakpoint. With no explicit breakpoint, explicit mode does not cache. |
| Breakpoint | `prompt_cache_breakpoint: { "mode": "explicit" }` on an `input_text` block. The installed SDK has no per-breakpoint `ttl`. The request `ttl` applies. |
| Where the breakpoint may sit | On a content block inside a developer message. A top-level `instructions` string cannot carry one. |
| TTL this client can send | `"30m"` only. The guide also describes `"12h"` for the Responses API. This SDK cannot send `"12h"`. An unsupported `ttl` can return HTTP 400. `"30m"` is a minimum lifetime, not a deletion deadline. |
| Older retention | `prompt_cache_retention` values `in_memory` and `24h` belong to earlier models. `24h` is not a GPT-5.6 Responses TTL and is not priced as 12 hours. |
| Cache key | `prompt_cache_key` is optional on GPT-5.6 and later. OpenAI routes without it. A key separates accounting and can prevent reuse of an identical prefix across groups. It does not pin a machine and does not guarantee a hit. |
| Minimum prefix | 1,024 visible input tokens. Hidden OpenAI system tokens do not count. |
| What else changes the rendered prefix | Model, tools, structured-output schema, reasoning effort, verbosity, and context management. This change does not alter those. |

### Usage fields and prices

The Responses usage object on this SDK exposes `input_tokens`, `output_tokens`, `total_tokens`, and `input_tokens_details.cached_tokens` plus `input_tokens_details.cache_write_tokens`. It has no retention or TTL field.

The guide's pricing rule: cache-write pricing is not an additive fee. Each input token uses the uncached-input rate, the cached-input rate, or the cache-write rate. Within one request, each token is billed once, at the longest lifetime of the selected writes that cover it. The accounting identity `uncached = input - cached reads - cache writes` matches that partition when the provider counts do not overlap. A response that breaks the identity stays an identity gap and is not given a dollar figure.

| Category | Official Standard rate for `gpt-5.6-sol` | How cost is reported |
|---|---|---|
| Uncached input, ≤272K input tokens | $4.00 / million | Application estimate only when `OPENAI_INPUT_USD_PER_MILLION` is set. A missing rate stays unknown. It is not $0. |
| Cache read, ≤272K | $0.40 / million | Same rule, using the cached-input setting. |
| Output, ≤272K | $20.00 / million | Same rule, using the output setting. Output includes reasoning tokens. |
| 30-minute cache write, ≤272K | $5.00 / million, which is 1.25× the $4 input rate | Estimated only when the response names a 30-minute write and the 30-minute rate is set. |
| 12-hour cache write, ≤272K | $10.00 / million, which is 2.5× the $4 input rate | Estimated only when the response names a 12-hour write and the 12-hour rate is set. |
| Same categories above 272K input tokens | $8 input, $0.80 cached, $30 output, $10 30-minute write, $20 12-hour write, for the whole request | The configured settings are the short-context card. Above 272,000 input tokens the application estimate stays unknown. |
| Unscoped `cache_write_tokens` | The usage object does not name a lifetime | Unknown. Not priced as 30 minutes because the request asked for 30 minutes. |
| 24-hour write | Not a GPT-5.6 Responses TTL | Unknown. Not priced as 12 hours. |

The rates are the Standard card on the [API pricing page](https://developers.openai.com/api/docs/pricing), read 24 Oct 2026, for model `gpt-5.6-sol`. The pricing page also publishes Fast rates at double the Standard card, Batch rates at half, and a 10% uplift for regional processing endpoints. This client does not set `service_tier` or a regional `base_url`. Omitted `service_tier` means `auto`, which follows the project setting. The project retrieve API returns no service tier. The comparison proposal pins `service_tier=default` on both harness arms so this Standard card applies. That pin is not in the application request. Promotional pricing for GPT-5.6 Sol is published as available at least through 21 Nov 2026. These figures are that published Standard card. The [images and vision guide](https://developers.openai.com/api/docs/guides/images-vision) publishes a high-detail patch formula. The comparison uses the count endpoint for the actual input, not that formula.

Local settings currently match the short-context input, cached-input, and output rates. Both cache-write settings are 0. Zero is not a price. A positive token count with a 0 rate stays unknown.

Proposed settings, and only for responses at or below 272,000 input tokens:

- `OPENAI_INPUT_USD_PER_MILLION=4`
- `OPENAI_CACHED_INPUT_USD_PER_MILLION=0.4`
- `OPENAI_OUTPUT_USD_PER_MILLION=20`
- `OPENAI_CACHE_WRITE_30M_USD_PER_MILLION=5`
- `OPENAI_CACHE_WRITE_12H_USD_PER_MILLION=10`

Do not put the long-context rates into those same settings. Do not apply the 30-minute rate to an unscoped write. These values are not written into the environment by this change.

A computed dollar amount is an application estimate. It is not an OpenAI invoice. Provider-confirmed dollars still require the billing export.

### Timeouts

A connect failure before the provider accepts the request is still logged as `usage_unbilled` with `billed: false`, and it is still retried. A read timeout, `APITimeoutError`, or other timeout that can occur after the request was sent is `usage_outcome_unknown` with `billed: null`. It is not retried, because a second call could be a second processed request. No token counts are invented for either case.

## What the saved logs show

Saved diagnostics under `server/logs` (106 `response_received` events, 18 jobs) were compared locally. They were not replayed against the API.

- Every saved response has `input_tokens`, `cached_input_tokens`, and `output_tokens`. The sum of logged cache reads is 874,795 tokens.
- No saved response has a `cache_write_tokens` field. Recorded cache-write volume is therefore zero in the logs, which means the field was not stored. It does not mean OpenAI wrote nothing.
- Across 82 gaps between consecutive batches of the same job, the median gap is 8.4 minutes, 71 are within 30 minutes, 11 are longer than 30 minutes, and 4 are longer than 60 minutes. The longest gap is about 17 hours.
- On 73 of 81 consecutive prepared requests, `prompt_character_count` and `prompt_identifiers` are unchanged. Real jobs record about 55,000 to 62,000 instruction characters. That is above a 1,024-token prefix under ordinary tokenization. The logs do not include a provider token count for the prefix alone.
- Fields that do change between those requests are `authorized_filenames` (64 pairs), `prior_open_item` (30), and, less often, the prompt identifiers, reference id, or reference version.

The logs do not contain the rendered prompt or image bytes, so they cannot show the exact first differing token. The request builder can. Before this change the whole instruction was one system string, then one user message: JSON payload, then each image. Payload keys start with `job_id`, then `batch_id`. Within a job the first changing value is `batch_id`, followed by folder, prior item, project, calibration, filenames, and the images.

That split matters because implicit caching on this model family puts its breakpoint at the end of the latest eligible message. The user message was that message, so the written prefix included the batch id, calibration, filenames, and images. Those change every batch. Putting them inside the written prefix is the avoidable part. The governing text itself is usually stable.

`OUTPUT_SCHEMA` is prose about how to fill the object. `text_format` is the parser schema. Both remain. Removing the prose would change the instructions, and the logs cannot prove the model treats them as duplicates.

Nothing in the logs shows that a cache key would have produced a hit. This change does not send `prompt_cache_key`. A key must not contain filenames, patient data, or secrets, and on this model family it is not required for routing.

## Instruction precedence

The [Responses API reference](https://developers.openai.com/api/reference/resources/responses/methods/create) says instructions with the `developer` or `system` role take precedence over `user` instructions. It does not say those two roles are equivalent to each other. The [prompt-engineering guide](https://developers.openai.com/api/docs/guides/prompt-engineering) priority table lists `developer`, `user`, and `assistant`. `system` is not in that table. The caching guide says a breakpoint has to sit on a content block, and the installed SDK places that block on a developer message.

The previous request sent the governing text as one `system` string. The new request sends the same characters as a `developer` message whose content is a one-block list, with a breakpoint marker on that block. The calibration example remains in the user JSON. The preamble still says that example is calibration for the named project and box, not an answer key. Moving that sentence into a different role can change how the model weighs it against the example. Unchanged characters are not evidence that the weighing is unchanged.

Regression coverage that exists today:

- `test_prompt_cache.py` checks that two batches share the governing text, that a governing-doc edit changes the fingerprint, that project name, calibration id, batch id, and filename stay out of that text, and that image detail stays `high`.
- `test_workflow.py` checks that the calibration id and version are in the user payload and that the word "calibration" remains in the instruction text.
- `test_stage.py` checks selected governing phrases and that an unapproved reference is null in the payload.
- Orchestrator calibration tests use a fake inspector. They check that a contradictory returned row stops the batch before commit. They do not check what the model returns.

No test calls the model. No test compares a `system` string with a `developer` block. A paid comparison can show whether one disposable image produces the same structured fields. It cannot certify the role change for client boxes.

## Change

`server/app/prompt_cache.py` keeps the previous instruction characters: preamble, runtime prompt, governing documents in sorted filename order, `OUTPUT_SCHEMA`, then `decision_instructions()`. Those characters are now a developer `input_text` block with one explicit breakpoint. The user message is the same JSON payload, then the same image parts at `detail: high`. `prompt_cache_options` is `{ "mode": "explicit", "ttl": "30m" }`. There is no cache key on the production request.

`"30m"` is the only TTL this SDK accepts, and it matches the observed batch spacing: most consecutive batches in the saved logs are inside 30 minutes. `"12h"` would be a 2.5× write and is not sendable by this client. Explicit mode is what keeps the changing suffix out of the cache write. The breakpoint does not guarantee a later hit. Routing, a gap longer than the retained lifetime, or a change to the governing text, schema, or reasoning effort can miss.

Expected effect, if the provider follows the guide: the first request in a window writes the stable text at the 30-minute write rate and does not write the images or batch JSON. A later request with the same governing text can read that prefix at the cache-read rate and pay uncached input only for the new batch and images. The first write can cost more than a fully uncached request. Net savings are not measured.

Request diagnostics now include the prompt fingerprint, reference version, cache mode, TTL, breakpoint placement, and `prompt_cache_key_sent: false`. Response diagnostics include the response id, the usage categories, whether those categories are complete, and `cost_basis` of `application_estimate` or `unknown`. Credentials and image payloads are not logged. Usage rows, response ids, the outbox, and Mongo ingestion are unchanged. An unscoped write is still unknown, so historical totals are not rewritten.

## Limitations

- The client's billing export is still unavailable. Historical proven cost remains null.
- Saved logs cannot separate cache writes by retention, and they cannot show the exact prefix bytes.
- Requesting 30 minutes does not make `cache_write_tokens` a confirmed 30-minute charge.
- Write rates in settings are 0. A positive write count with a 0 rate stays unknown. It is not stored as $0.
- Input above 272,000 tokens stays unknown because the configured rates are the short-context card.
- A timeout is unknown, not proven unbilled and not proven billed.
- `text_format` and reasoning effort can still sit outside the explicit breakpoint in the rendered prompt. They are unchanged, so they stay stable, but this process cannot see where the provider injects them without a paid call.
- Some batches are more than 30 minutes apart. Those can miss a 30-minute entry. The guide says the provider may retain an entry longer; that cannot be relied on.

## Targeted disable

To turn off only the cache optimization, restore the request inside `server/app/inspection.py` `_inspect_once` to the `ee1f7d4` shape: one `system` string, no `prompt_cache_options`, and no developer breakpoint. Leave `server/app/usage_accounting.py`, the zero-rate and 272,000-token guards, and the timeout `usage_outcome_unknown` path in place. Leave `docs/Kristen_2026-10-23.md`, `docs/Kristen_2026-10-24-api-cost-concerns.md`, and the historical-import notes alone. The harness in `server/harness/` is not imported by inspection, so deleting or ignoring it does not change a normal request. Do not use a blanket checkout of `inspection.py` from `ee1f7d4` if the timeout accounting in that file should stay. This disable has not been performed.

## Comparison harness

`server/harness/cache_comparison.py` is the only place that selects an arm. `server/app/inspection.py` and `server/app/orchestrator.py` do not import it. The six-call and $20 limits live only in that harness.

A normal job stops when its summed `Estimated Cost USD` reaches `JOB_SPENDING_LIMIT_USD`. The code default and `server/.env.example` are 100. The local `.env` sets 20. `EXPENSIVE_BATCH_WARNING_USD` is 10 in that file and is not read by the orchestrator. None of those settings were changed. The harness constant cannot change them.

`prepare()` reads the deployed `_inspect_once` source and builds the baseline from that source. The optimized arm uses the current developer breakpoint. Both arms keep `gpt-5.6-sol`, reasoning effort `high`, image detail `high`, `InspectionOutput`, and `max_output_tokens` 60000. The baseline role is `system`. The optimized role is `developer`. Those roles are not documented as equivalent. A **clean** comparison (see `docs/CLEAN_CACHE_COMPARISON_PREP.md`) isolates each arm with a fresh-run `prompt_cache_key`; production inspection still sends none. SDK retries, application retries, and batch splits are not on this path. A timeout or any exception stops the run and keeps the reservation. Evidence-save failure stops before the next provider call. `prepare()` does not place a call.

### Images

The three frames are the 2048-edge JPEGs already prepared for local Box 31/F04 inspection. Their dimensions match the recorded model inputs. They are read from `C:\Users\asa\AppData\Local\Temp\historyit_model_views\` and are not copied into the repo. The expected fields come from `tri-delta-chapters-box31-f04-calibration.json` version `2026-10-24.1` for those source filenames:

| Local file | Source filename | Approved result |
|---|---|---|
| `004-model-input.jpg` (2048×1536) | `TriDelta_Year2_Chapters_Box31_F04_004.jpg` | Metadata Only, no production task |
| `002-model-input.jpg` (2048×1536) | `TriDelta_Year2_Chapters_Box31_F04_002.jpg` | Normal Odyssey, Archival Border Crop |
| `016b-model-input.jpg` (2048×1365) | `TriDelta_Year2_Chapters_Box31_F04_Photograph_016b.jpg` | Normal Odyssey, Rotate + Archival Border Crop, note includes 180 |

The request scope stays Tri Delta Chapters, Chapters Box 31, folder F04. The harness writes no register. `F04_005` is not in the local views, so this set cannot check the 004/005 pair.

### Input count

The count is `POST /v1/responses/input_tokens` ([Counting tokens](https://developers.openai.com/api/docs/guides/token-counting), [count reference](https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count/)). It accepts the Responses input, including messages, images, reasoning, and the text schema, and returns `input_tokens`. Installed `openai` 2.54.0 exposes `responses.input_tokens.count` with those fields. It does not accept `prompt_cache_options` or `max_output_tokens`.

The content breakpoint is part of the input item, and the count reference accepts `prompt_cache_breakpoint` on that item. The reference says the boundary is not rounded to a token block. Request-level `prompt_cache_options` selects the TTL and the explicit-only mode. It is not required to count the semantic input, and this SDK method cannot send it. The counting guide says caching can change tokenization, which is why the count sends the breakpoint rather than a local estimate. It was not called.

The guide does not say the count is free, and it does not say the count incurs a model charge. That is undocumented billing, not a documented charge. Output remains the unchanged 60,000-token limit.

### Service tier

Deployed requests omit `service_tier`, so they use `auto` and follow the project. `GET /organization/projects` does not return a service tier, and this repository has no tier setting. The comparison pins `service_tier=default` on both arms through `with_comparison_tier`. That is Standard processing, and it is a controlled difference from deployment. It is not written into `inspection.py` or `.env`.

### Reservation

Each generation call reserves every counted input token at the Standard 12-hour write rate, plus 60,000 output tokens at the Standard output rate. At or below 272,000 input tokens those rates are $10 and $20 per million. Above that, the whole call uses the long-context card, $20 and $30 per million. An unknown outcome keeps its reservation. The harness will not reserve more than **$20** for the six calls. The counted input is not known yet, so no lower total was calculated. Six short-context output reservations are $7.20, which leaves $12.80 for the six input reservations.

## Paid comparison status

A replacement comparison was authorized once and completed locally as
`compare-20261029T072738Z-0453f327`. General paid testing was not resumed.
**Do not run another paid comparison without new authorization.**

### Completed run — facts only

- Cache reuse was observed on the optimized arm (warm-up `ttl_30m` write 12713; follow-ups read 12713 cached tokens with zero writes).
- All three quality fixtures passed (004 Metadata Only; 002 Archival Border Crop; 016b Rotate + crop). `regressions` empty.
- Savings were **inconclusive**. No percentage reduction may be claimed. Both arms recorded `application_estimate_usd: null`.
- Baseline warm-up was **contaminated** (cache-read tokens on the baseline warm-up).
- Cache-write retention for some baseline rows stayed **unknown** (unscoped `cache_write_tokens` without TTL details).
- Failed 26 Oct run spend and generation-call count remain **unknown**.

Evidence: `server/harness/reports/cache_comparison_report.json`, `cache_comparison_events.jsonl`, and `manifests/compare-20261029T072738Z-0453f327.json`.

### Original proposal (historical)

The text below was the pre-run plan. The completed run followed that plan.

The order is three optimized calls, then three baseline calls: `004`, then `002`, then `016b` in each arm. The governing text and calibration are the same on all six requests. Each baseline request uses the same user message and image bytes as the optimized request for that frame. The first call in each arm is the warm-up, and its cost stays in that arm's total. No retries, no splits, and no client register writes.

These six calls can show whether the optimized follow-ups reuse the stable prefix, the cache-read and cache-write token counts, an application estimate when every returned category has a rate, and a conservative range that includes the warm-up. They can also check the three approved outcomes for these frames. They cannot support a savings claim. The baseline prefix is the deployed system text, so cache-read tokens on its warm-up mean an existing entry contaminated that arm. An unscoped cache write has no application estimate. If either arm is unknown, or the estimate-to-bound ranges overlap, the result is inconclusive. `service_tier=default` is not the deployed `auto` setting. None of these figures is an invoice.

Each response records order, arm, warm-up, fingerprint, image hash, start and completion time, response id, returned service tier, usage, contamination, the application estimate, and the conservative bound.

`execute()` sends no generation call when any reservation is missing or the six generation reservations sum to more than $20. The token-count endpoint's charges are not documented, so that $20 stop is not a guarantee across every endpoint. The count calls, if they are billed, sit outside it.

The running staging API and `/home/ubuntu/history-it-staging/.env` both have `JOB_SPENDING_LIMIT_USD=20`. That is the job limit. It was not changed. The comparison cap is a separate $20 constant in the harness.

The execution command, from `server/`, is:

```text
D:\Integer\HistoryIT\history-it\server\.venv\Scripts\python.exe -m harness.cache_comparison --allow-paid-calls
```

The comparison was attempted on 26 Oct 2026. The command ran for about 143 seconds and then failed while saving `cache_comparison_report.json`. `_public_request` copied `text_format`, which was the `InspectionOutput` class, and `json.dumps` cannot serialize that class. The terminal record is `terminals/686459.txt`. It contains the traceback and no response ID. `server/harness/reports/` and `server/logs/` have no call record from that run, because nothing was flushed until the final summary. The exact billed amount and the number of completed generation calls are unknown. Savings and the three quality outcomes remain unverified. No automatic rerun is authorized.

A later run appends each request, and then each response, to `cache_comparison_events.jsonl` before quality scoring or the summary. The summary file is replaced atomically. If a record cannot be saved, the harness does not make the next provider call. A restart does not send an identity that already has a request record.

Acceptance, after a later authorized run:

- Six generation calls, or fewer if a reservation or a failure stopped the run before the rest.
- No generation call if the counted reservations sum to more than $20.
- Optimized `004`, `002`, `016b`, then baseline `004`, `002`, `016b`.
- One governing fingerprint. Matching image bytes between the two arms. Both warm-ups included in the arm totals.
- Recorded response ids, times, returned service tier, usage, and contamination.
- Unknown costs left unknown. Overlapping ranges recorded as inconclusive, not as savings.
- `F04_004` Metadata Only with no production task, `F04_002` Archival Border Crop, and `Photograph_016b` Rotate plus crop with a 180 note.
- No register write, no retry, and no resume of a frozen job.
- Not accepted: the 004/005 pair, the rest of F04, the role change for a whole box, invoice reconciliation, or a proven saving.

## Rollback

Leave the cache request-format change uncommitted, or restore those files in the working tree. Do not deploy cache optimization to staging or production. Staging acceptance of 29 Oct fixes is `0f5fe59` without cache.

- `server/app/prompt_cache.py`
- `server/app/inspection.py`
- `server/app/usage_accounting.py`
- `server/app/config.py`
- `server/app/comparison_budget.py`
- `server/harness/cache_comparison.py`
- `server/harness/fixtures/disposable_expectations.json`
- `server/tests/test_comparison_budget.py`
- `server/tests/test_cache_comparison_harness.py`
- `server/tests/test_prompt_cache.py`
- `server/tests/test_stage.py`
- `server/tests/test_workflow.py`
- `docs/API_COST_OPTIMIZATION.md`
- `Tasks.md`

## How cost is measured when retention is missing

The usage object can report `cache_write_tokens` without a lifetime. Three numbers stay separate:

- Provider-confirmed charge: none. Token counts and a retention name, when present, are provider-reported. Dollars are not. The billing export is still missing, so `provider_confirmed_usd` stays empty.
- Application estimate: the short-context Standard rates applied only to categories the response names. An unscoped write has no estimate. A request TTL is not a reported retention. Input above 272,000 tokens has no estimate from the short-context settings. A missing rate is not $0.
- Conservative bound: for an unscoped write, the short-context or long-context 12-hour Standard write rate, chosen from the actual input-token count. The bound uses that rate because it is the highest Standard write rate on the card. The field that records this says `12h_standard_rate_not_provider_reported`. The provider-reported retention remains `unknown`.

`server/app/comparison_budget.py` implements that split. It does not call the API.

## Local verification

No paid call and no input-token count call were made. `tests/test_cache_comparison_harness.py` passed 10 tests, including the local model-input check. Earlier full-backend, commit-engine, deployment-contract, and frontend results were not rerun for this fixture correction. None of these tests measure a cache hit or a lower bill.
