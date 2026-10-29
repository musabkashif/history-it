# Kristen 29 Oct 2026 — investigation report

Investigation only. No application fix, paid call, job retry, register write, commit, push, or staging restart was performed for this report.

Source of client statements: `docs/Kristen_2026-10-29_Concerns_and_Tasks.md`. Raw client text stays there. Conclusions below are engineering findings and must not be mixed into that raw section.

Deployed staging at investigation time: host checkout and running tree at `ee1f7d44436e824eef9290a08db9269e093e716e` (container image created 2026-10-24T12:29:06Z). Affected run timestamps fall after that deploy. Per-job image digest was not stamped into diagnostics; treat **staging `ee1f7d4`** as the SHA for these runs unless a later note proves otherwise. Production was not inspected and was not changed.

Frozen jobs (do not retry or rewrite):

| Job | Project / box | Last validated / hold |
|---|---|---|
| `HIT-a915dbc3c4ab3d8a` | Tri Delta Chapters Box 35 | B0001 validated; B0002 failed before staging. Client holding. |
| `HIT-ed5fd9772d72a6b9` | Raiders Credentials Box 1 | B0001 failed before staging. Client holding. |
| `HIT-4435b42e0c217f99` | Gamma Phi Beta Canadian Scrapbooks | Discovery only; never inspected. Client holding. |
| `HIT-39d15697ce63fbf7` | McCallie Box 11 | B0023 validated (single successful response in logs); B0024 failed twice. Client holding. |

---

## N01 — Evidence inventory

| Artifact | Location | Notes |
|---|---|---|
| Box 35 B0001/B0002 diagnostics | staging `server/logs/HIT-a915dbc3c4ab3d8a_*.json` | B0002 has 9 `response_received`, 8 `provider_split`, 8 unsuccessful `inspection_error` parents (empty `response_id`). |
| Raiders B0001 diagnostics | staging `server/logs/HIT-ed5fd9772d72a6b9_*.json` | Same 9/8/8 pattern. |
| McCallie B0024 | staging `server/logs/HIT-39d15697ce63fbf7_*B0024_attempt-*.json` | Attempt 1 `IncompleteRead`; attempt 2 `SSLEOFError`. |
| Gamma | API container logs only | No inspection diagnostic JSON (never reached provider). |
| Full model `InspectionOutput` | **Missing** | Diagnostics store `returned_record_ids` / filenames, not full record operations. |
| Batch Queue Error / Retry Note | Client-reported; Sheets not rewritten | Exact strings below. |

---

## Box 35 — duplicate prior-open resolution (`HIT-a915dbc3c4ab3d8a`)

### Confirmed evidence
- Job id from staging logs (not in the pasted client text): `HIT-a915dbc3c4ab3d8a`.
- B0001: one billed response, validated.
- B0002: 14-image batch; binary split on OpenAI HTTP 500 / upstream connect failures; **9 billed** `response_received` parts; token totals match the client exactly (249,609 / 114,417 / 135,165 / 20,266).
- Part 1 prior = `TDC-B35-F01-001` (Batch Queue Prior Open Item). Parts 2–9 use within-batch priors (`TDC-B35-F02-001` …).
- `TDC-B35-F01-001` appears in `returned_record_ids` **only on part 1**, together with `TDC-B35-F02-001`.
- Client final error: `More than one staging record resolved to TDC-B35-F01-001`. That string is raised only in `server/app/identity.py` (`assign_identity`).
- No Asset/Record Staging rows per client; B0001 remains authoritative.

### Exact failure path
1. `Inspector.inspect` → `inspect_resilient` splits on `provider_server_error` (500/502/…).
2. Parts succeed and merge via `merge_inspections` / `continuation_context`.
3. `_stage` → `assign_identity` raises `CommitError`.
4. Orchestrator generic `except Exception` marks `Inspection Failed - Retryable` (no staging write).

Offline: when two Update rows for the same prior survive merge as `TDC-B35-F01-001` and `TDC-B35-F01-001~1`, `assign_identity` maps both to the committed prior and raises the **exact** client error. A single prior-close plus new F02 create does **not** raise.

### Reproduced offline?
**Partially (pre-fix).** The CommitError path was reproduced. Live diagnostics do **not** show a second part listing `TDC-B35-F01-001`, so the claim that *every* split part re-emits the close remains unproven.

### Local fix status (29 Oct, uncommitted)
- `merge_inspections` collapses repeated `Update Existing Open Record` rows that share the same local id.
- `assign_identity` coalesces `id` / `id~N` only when they resolve to the same committed prior, and preserves kind/template/type via `_preserve_committed_record`.
- Single-close path and Box 37 cross-folder close tests still pass.
- Frozen Box 35 job was not retried. Not deployed.

### Missing evidence
- Full merged `InspectionOutput` and Record Operation fields from the live B0002 run.
- Batch Queue Error cell verbatim from Sheets (client quote only).

---

## Raiders — within-batch create/update (`HIT-ed5fd9772d72a6b9`)

### Confirmed evidence
- B0001, Prior Open Item = `None` on the batch; 20 images; same 9 billed / 8 unsuccessful parent split pattern.
- Token totals match client (275,745 / 101,704 / 174,014 / 20,657).
- Model-invented ids appear in parts, including `HIT-ed5fd9772d72a6b9-R0001` (client error id) and also `HIT-ed5fd9772d72a6b9-B0001-R0001`.
- Parts 1–4 keep Batch Queue prior `None` (continuation did not always chain across sibling split branches). Parts 5–8 continue `HIT-ed5fd9772d72a6b9-R0001`; part 9 continues `…-R0002`.
- Client error: `Update Existing Open Record has no committed row: HIT-ed5fd9772d72a6b9-R0001` — same raise site in `assign_identity`.

### Exact failure path
1. Split tree creates a new open record in an early part (`Create New`).
2. A later part (or the merged survivor) uses `Update Existing Open Record` for that same local id via `continuation_context`.
3. `merge_inspections` keeps one row with operation **Update**.
4. `assign_identity` requires a committed Itemization/Metadata References row → CommitError.
5. No staging written.

### Reproduced offline?
**Yes (pre-fix).** Orphan Update without a committed row still fails. Create-then-Update across split parts previously failed because the merged survivor stayed Update.

### Local fix status (29 Oct, uncommitted)
- `merge_inspections` keeps `Create New` when a continued later part sends `Update Existing Open Record` for the same in-batch id.
- True cross-batch Update Existing still requires a committed Sheets row.
- Unrelated sibling id clashes still rename (`id~N`); they are not merged as one record.
- Frozen Raiders job was not retried. Not deployed. Continuation gaps on parts that kept prior `None` are unchanged.

### Missing evidence
- Full per-part Record Operation fields (only ids saved).
- Whether Sheets Error note matches client text character-for-character.

---

## Nine billed responses (Box 35 B0002 and Raiders B0001)

### Confirmed evidence
These are **split parts after unsuccessful parent requests**, not nine independent job retries.

| Batch | Billed parts | Unsuccessful parents (`inspection_error`, empty `response_id`) | `provider_split` events |
|---|---:|---:|---:|
| Box 35 B0002 | 9 | 8 | 8 |
| Raiders B0001 | 9 | 8 | 8 |

Parent failures were OpenAI/proxy `InternalServerError` (upstream connect / connection refused / Cloudflare 502). Each failure with `len(sources) >= 2` triggers binary split in `inspect_resilient`. Successful leaves become Parts 1–9. B0001 on Box 35 needed only one response (no split).

Unsuccessful parents are **not** billed in diagnostics (`response_id` empty, `estimated_cost_usd` 0). The nine `response_received` rows are `billed: true`.

### Reproduced offline?
Split accounting is explained by code + logs. No new provider call was made.

### Missing evidence
- Whether any unsuccessful parent was still charged on OpenAI’s side despite empty local `response_id` (unknown; do not invent).

### Local fix status (29 Oct, uncommitted)
- `inspect_resilient` keeps binary split and existing quality settings (detail/reasoning/schema/limits unchanged).
- Guards: `PROVIDER_SPLIT_MAX_DEPTH = 6`, `PROVIDER_SPLIT_MIN_IMAGES = 2`. Reaching max depth raises `ProviderSplitExhausted` (retryable note); a single-image part still raises the existing rejected-image error. Neither path stages or commits.
- Every split node is recorded on `context['_provider_split_tree']` and via diagnostics (`provider_split`, `provider_split_leaf`, `provider_split_exhausted`, `provider_split_tree`) with node/parent ids, depth, reason, HTTP status, billed/unbilled/unknown, response id, and token usage when present. Failed parents do not invent tokens or billing.
- Distinguishes unsuccessful parent requests, successful billed leaves, application retries (false on split nodes), provider/API retries (false on split nodes), and child split requests.
- Offline: `server/tests/test_provider_split_safety.py`. Frozen jobs were not retried. Not deployed.

### Remaining billing limitation
- Whether an unsuccessful parent with empty local `response_id` was still charged on OpenAI’s side remains **unknown**; cost reconciliation stays pending. Do not invent billing data.

---

## Gamma — root-level Start never inspects (`HIT-4435b42e0c217f99`)

### Confirmed evidence
- Repeated `POST /api/jobs/HIT-4435b42e0c217f99/start` → HTTP 200, then:
  - `Exception in thread historyit-HIT-4435b42e0c217f99`
  - `CommitError: Cannot determine source folder for batch HIT-4435b42e0c217f99-B0001`
- No diagnostic JSON, no OpenAI usage (matches client).
- Batch remains `Authorized / Pending Inspection` because the error is raised in `_inspection_inputs` → `folder_code()` **before** status flips to In Progress, and `CommitError` is **not** caught by the narrow `except (RegisterStateError, BatchAuthorizationError, ReferenceError)` around `_inspection_inputs`. The thread dies; UI returns to Start.

### Exact failure path
`folder_code` in `server/app/identity.py` only accepts `Source Group / Folder` matching `F\d+` or a manifest whose folder/filename yields exactly one `F\d+`. Root-level assets use the root folder name (e.g. scrapbook title) → CommitError.

### Local fix status (29 Oct, uncommitted)
- `folder_code` returns `ROOT` when every manifest row shares one non-F `folder_name`, no F-folder evidence exists, and Source Group matches that name (or is empty).
- Record canon accepts `ROOT` beside `F\d+`.
- `orchestrator.run` catches `CommitError` from `_inspection_inputs` via `_block` → `Blocked - Human Review` + Error/Retry Note + diagnostic, without marking In Progress.
- Offline: `server/tests/test_gamma_root_folder.py`. Frozen Gamma job was not retried. Not deployed.

### Missing evidence
- Exact `Source Group / Folder` string and Notes manifest JSON from the Gamma register (not pulled from Sheets this pass).

---

## McCallie — partial download / SSL (`HIT-39d15697ce63fbf7-B0024`)

### Confirmed evidence
- Attempt 1 (2026-10-27T23:06:42Z): after preparing F06_080–090, `IncompleteRead(16777216 bytes read, 4530908 more expected)` during `drive.image` / httplib2 read. 16,777,216 + 4,530,908 = **21,308,124** — matches client size for `McCallie_Year1_Box11_F06_091.jpg`.
- Attempt 2 (2026-10-28T02:35:12Z): `SSLEOFError: EOF occurred in violation of protocol (_ssl.c:2427)` with **empty** `image_preparation` (failed before/at first transfer).
- B0023 log: single successful inspect (last validated checkpoint in logs).
- `IncompleteRead` / `SSLEOFError` are **not** classified as connection retries in `_is_connection_error` (OpenAI path). Drive download uses the same `_call` wrapper; these error names are absent, so the download is not retried as a connection error.

### Exact failure path
`Inspector._inspect_once` → `self.drive.image(src)` → Google media download → incomplete SSL/HTTP body → inspection_error → `Inspection Failed - Retryable`. No staging/commit.

### Reproduced offline?
Transport errors were not re-run against live Drive. Size arithmetic and error types confirmed from logs. Offline fixtures now cover IncompleteRead / SSLEOFError retries and size-mismatch rejection.

### Local fix status (29 Oct, uncommitted)
- `Inspector._download_drive_image` retries only Drive media: **3 attempts**, backoff **1s → 2s → 4s**.
- Retryable: IncompleteRead, IncompleteDriveDownload, SSLEOFError/SSLError, and related transport errors. OpenAI timeouts are not treated as Drive failures.
- `Drive.image` returns complete bytes and rejects bodies shorter than Drive `size` metadata.
- Failures name the filename in diagnostics / raised error; incomplete bytes never reach `prepare_model_image` or the model.
- Orchestrator still marks a failed inspect as retryable without staging/commit. Validated batches are not auto-resumed. Frozen McCallie job was not retried. Not deployed.

### Missing evidence
- Drive md5 of `…_091.jpg` from API metadata at the original failure time.
- Whether attempt 2 hit the same file or failed earlier in the TLS handshake.

---

## Offline fixture status

Added (documenting current broken behavior; not a product fix): `server/tests/test_client_2026_10_29_failures.py`.

| Case | Offline result |
|---|---|
| Gamma `folder_code` | Reproduced |
| Raiders Update without committed row | Reproduced |
| Raiders create→update merge survivor | Reproduced |
| Box 35 dual prior Update after merge rename | Reproduced (mechanism); live second Update not listed in diagnostics |
| Nine-call split tree | Explained from logs; not re-executed against OpenAI |
| McCallie transport | Not re-executed |

---

## Task status (investigation)

Local offline fixes (uncommitted, not deployed, frozen jobs not retried): **N02, N03, N04/N08 (split safety + diagnostics), N05, N06**. **N01** evidence preserved. **N07** partially covered. **N09–N11** still open. None are client-accepted.

General paid-testing freeze remains. Do not retry the frozen jobs above. Do not claim invoice reconciliation or proven API-cost savings from this investigation.
