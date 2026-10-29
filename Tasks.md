P0 = urgent blockers · P1 = essential reliability · P2 = cleanup/UX · P3 = later optimization

## Current status — 29 Oct 2026

Recorded staging deploy and `origin/staging` are `ee1f7d4`. That commit includes the 22–23 Oct acceptance fixes (`8e72ef3`) and the usage-event accounting (`314647c`, `43b720c`). Local `feed5e2` only records the usage import. It is not pushed. Production is `4bbf3d7` on `master`. That line reverts `702e229`, so the one-Start console and everything after it are not production-complete.

Paid testing remains paused except for the one authorized replacement cache comparison already completed locally. Do not run further model calls, retry frozen jobs, or change production. A push to `staging` can auto-deploy. The uncommitted cache and accounting edits are not on staging.

Kristen has not re-accepted `ee1f7d4`. Her OpenAI export is not reconciled to the imported usage rows.

Sources: her 22 Oct messages, her 23 Oct messages in `docs/Kristen_2026-10-23.md`, her 24 Oct cost message in `docs/Kristen_2026-10-24-api-cost-concerns.md`, and her 29 Oct failure messages in `docs/Kristen_2026-10-29_Concerns_and_Tasks.md` (investigation: `docs/Kristen_2026-10-29_Investigation.md`).

| Priority | Task | Source | Current status |
|---|---|---|---|
| **P0** | Paid testing paused | Client, 24 Oct 2026 | Paused for general testing. One replacement cache comparison was run under separate authorization; savings remain inconclusive. Do not retry frozen jobs. |
| **P0** | N01–N06 Kristen 29 Oct failures (Box 35, Raiders, nine-part splits, Gamma Start, McCallie download) | Client, 29 Oct 2026 | **N02/N03/N04/N05/N06/N08 fixed locally only** (prior-open coalesce; within-batch Create/Update; Gamma `ROOT`; Drive download retries; provider-split depth/min-size guards + diagnostic tree). Offline: `tests/test_provider_split_safety.py`, `tests/test_mccallie_download.py`, and related. Not committed/pushed/deployed. No frozen-job retry. See `docs/Kristen_2026-10-29_Investigation.md`. Frozen: `HIT-a915dbc3c4ab3d8a`, `HIT-ed5fd9772d72a6b9`, `HIT-4435b42e0c217f99`, `HIT-39d15697ce63fbf7`. Staging SHA `ee1f7d4`. |
| **P1** | N07–N11 regression coverage, split spend controls, failure visibility, recovery plan | Client, 29 Oct 2026 | **N08 offline-tested locally** (`PROVIDER_SPLIT_MAX_DEPTH=6`, `PROVIDER_SPLIT_MIN_IMAGES=2`, split-tree diagnostics). N07 partially covered by new split tests. N09–N11 still open. Not committed/pushed/deployed. |
| **P0** | OpenAI usage under-reports cost and omits some requests | Client, 24 Oct 2026 | On staging `ee1f7d4`. Each response is a usage event. An unscoped `cache_write_tokens` count stays unknown and is not priced as a 30-minute write. Named categories are an application estimate, not an OpenAI charge. 47 historical jobs and 512 usage rows are imported and read-only. Proven cost is null. Her 429 requests and about $214.80 are not reconciled. See `docs/Kristen_2026-10-24-api-cost-concerns.md`. |
| **P0** | Cost calculated from returned usage categories, including failed and retried requests | Client, 24 Oct 2026 | On staging `ee1f7d4` for new requests. A later local change is not deployed: a 0 rate with a positive token count stays unknown, input above 272,000 tokens stays unknown, and a timeout is `usage_outcome_unknown` with billed null. |
| **P0** | Historical reconciliation against her OpenAI export | Client, 24 Oct 2026 | Pending. The export is not loaded. The bill is not reconciled. |
| **P0** | Do not change staging during an active box; tell her before the build changes. Do not promote unresolved crop behavior to production | Client, 22 Oct 2026 | Standing rule. She is not in an active run; testing is paused. Staging was moved to `ee1f7d4` after that pause. Production was not promoted. |
| **P0** | Box parent ek baar authorize karna; saare F-folders discover karna, aur empty F-folders skip karna | Client, 22 Oct 2026, Box 33 | On staging `ee1f7d4`. A parent run discovers immediate F folders, records an empty folder as Skipped / Empty, continues when another folder has images, and fails only when no eligible folder has processable images. Inaccessible folders still fail. Sibling non-F folders are not queued. Live acceptance on this build has not been run. |
| **P0** | Duplicate filenames must not block the job. Identity is Drive file ID + filename + parent folder. Identical content is a probable-duplicate warning | Client, 22 Oct 2026, Box 34 | On staging `ee1f7d4`. Sheet grids for Asset Staging, Asset QC, and Production Queue widen before `Drive File ID` and `Source Folder ID` are written. Record Staging is unchanged. A service account that cannot resize the sheet stops as a non-retryable error. Live acceptance has not been run. Do not retry `HIT-2b95d2adea3e0a81` while testing is paused. |
| **P0** | Finished box updates Continuation State and one authoritative Box Summary. Task totals come from the Production Queue. Additional Imaging Required is not Crop/QC | Client, 22 Oct 2026, Box 34 | On staging `ee1f7d4`. A finished folder replaces stale continuation values. When no authorized folder remains, Next Authorized / Unprocessed Folder becomes `None - authorized source exhausted`. Batch rows keep their own counts. Existing completed registers were not rewritten. Live acceptance of the corrected totals is pending. |
| **P0** | Caption/reference sheet + blank verso ko Metadata Only classify karna | Client | Passed on QC Test 02 and QC Test 03 for Chapters Box 31 F04 `_004`/`_005`. Her 23 Oct list keeps `F04_001` and `F04_004-005` as Metadata Only with no crop. Do not rewrite `HIT-ea97b58c8e4a8c58`, `HIT-b646d242c6175cdb`, or `HIT-36381173e12dba3e`. |
| **P0** | Excess black background, slight archival edge, and Box 31/F04 file-by-file crop decisions | Client, 22–23 Oct 2026 | On staging `ee1f7d4`. General rule: a slight border can pass, excess capture background is an Archival Border Crop, and there is no pixel threshold. The 75 px figure is the finished-crop deliverable border, not a detector. The F04 list is folder-scoped calibration: questionnaires `_002-003` and `_006-007`, and photographs `008` through `021`, are Archival Border Crops. `017c` must record `attached note`, `019b` must record `white physical border`, and `020b` must record `dark edge`. A missing phrase stays blocked. Local job `HIT-fe3b819451ab26c3` passed inspection, commit, and validation with 37 production tasks. Do not retry it. She has not accepted this build on staging. |
| **P0** | Photograph_016b: 180° Rotate and Archival Border Crop | Client, 23 Oct 2026 | On staging `ee1f7d4`. QC Test 02 queued Rotate only. Test 03 on `5e4f67e` dropped Rotate; that commit was reverted. The current prompt requires Rotate first and the crop second, and blocks a Rotate-only answer for this folder. `HIT-fe3b819451ab26c3` queued both. One asset can carry both tasks. Staging re-acceptance has not been run. |
| **P0** | Publication crop must follow physical format: unbound page edge, bound item keeps its binding | Client, 22 Oct 2026 | On staging `ee1f7d4`. Live acceptance has not been run. |
| **P0** | Consecutive report sections with the same chapter/term stay one report packet | Client, 22 Oct 2026, Box 30 | On staging `ee1f7d4`. Live acceptance has not been run. |
| **P0** | Cross-folder close keeps the prior record’s kind, template, and type, then leaves one open item | Client, 22 Oct 2026 Box 35; 23 Oct 2026 Box 37 | On staging `ee1f7d4`. Box 35’s F01 Publication/Program close keeps that kind, template, and type. When Box 37 F03 proves F02 cannot continue, F02 closes and keeps its Review status, and F03 is the only open item. Box 37 stays frozen. B0029 remains the last validated batch. Do not ask her to retry it. The failed registers were not rewritten. |
| **P0** | **Start once:** batches → inspection → commit → validation → next folder automatically | Client | On staging `ee1f7d4`, from `702e229`. Production reverts that release. She ran earlier boxes on a previous staging build. This build has no new whole-box acceptance. |
| **P0** | Sirf genuine blockers par stop: review, reconciliation failure, inaccessible files, spend limit, operator pause | Client | On staging `ee1f7d4`, from `702e229`. Production reverts that release. Cropping/QC production tasks do not halt the box. Live acceptance of this build is pending. |
| **P1** | Cache the stable governing text so cache writes are reused | Client, 24 Oct 2026 | Local only, uncommitted, not deployed. Explicit 30-minute breakpoint after the governing text, sent as a developer block. Same characters are not the same precedence. No production cache key. See `docs/API_COST_OPTIMIZATION.md`. |
| **P1** | Measure whether that cache change reduces cost | Usage investigation | 26 Oct attempt failed while saving (unknown spend/calls). Replacement run `compare-20261029T072738Z-0453f327` completed 6/6 locally: quality passed on the three F04 frames; savings **inconclusive** (unknown write retention; baseline warm-up cache-read contamination). General paid testing stays paused. |
| **Proposed** | Cost aggregation dashboard | Usage investigation | On staging `ee1f7d4`. Job, project, and workspace views show a calculated estimate, unknown usage, and billing reconciliation pending. Missing historical cost stays unknown. Live ingestion is on. Shadow writes stay off. |
| **Proposed** | Historical usage backfill | Usage investigation | Staging import is complete and backfill is off again. 19 jobs came from the staging host index and 28 from the local index. Original IDs and historical read-only status were kept. A repeated import did not change job or usage totals. Google Sheets was not written. |
| **P1** | Start button par Starting/Running state; repeat submissions prevent karna | Client | On staging `ee1f7d4`, from `702e229`. Production reverts that release. Idle `Authorized / Pending Inspection` stays startable. |
| **P1** | Reliable automatic UI polling/refresh | Client | On staging `ee1f7d4`, from `702e229`. Production reverts that release. Poll only while Starting… or the backend worker is live. |
| **P1** | Readback validation complete hone se pehle final Validated/PASS na dikhana | Client | On staging `ee1f7d4`, from `702e229`. Production reverts that release. Whole-job complete only when the authorized scope is finished. |
| **P1** | Successful commit/validation ke baad staging rows ka Pending Commit status update karna | Client | On staging `ee1f7d4`, from `702e229`. After successful readback, Asset/Record Staging `Commit Status` becomes `Committed - Validated`. |
| **P1** | Database-backed workspace/job list, browse/reopen, and incomplete project switch | Proposal, client approved as essential | MongoDB control plane is on for staging `ee1f7d4`. Sheets stay authoritative. Shadow writes and backfill stay off. Historical jobs are imported read-only. Her later request to move the database to SQL in HistoryIT’s AWS environment is not started. |
| **P1** | Existing jobs ko updates ke baad safely resume karna; cross-project isolation verify karna | Client | Isolation partly tested. McCallie return/resume is pending. |
| **P1** | Multiple Admins, each running a different project or job, with no shared session effects | Client, 23 Oct 2026 | Not built. She asked for Admin-only access and concurrent isolated jobs, and she said Operator/Viewer roles should not be built yet. UI polling isolation from `702e229` is on staging. Role and multi-admin session work is later. |
| **P1** | Har project ka separate authoritative Production Register, project location/Shared Drive mein | Client | Shared Drive setup worked. The app workflow is not re-verified on `ee1f7d4`. |
| **P2** | Hard-coded Tri Delta titles aur “F01–F06 discovered” text ko actual workspace/discovery se replace karna | Client | On staging `ee1f7d4`, from `702e229`. Production reverts that release. |
| **P2** | Box 19-specific verification ko developer-only label karna | Client | On staging `ee1f7d4`, from `702e229`. Default-off `ENABLE_DEVELOPER_VERIFICATION`. |
| **P2** | Guide correct karna: Sheets Editor, source Drive folders Viewer | Client | On staging `ee1f7d4`, from `702e229`. Production reverts that release. |
| **P2** | Frontend ko Odyssey Preservation branding par align karna | Client | On staging `ee1f7d4`, from `702e229`. Font is Montserrat because the Neue Kabel license files are not in the repo. Production reverts that release. |
| **P2** | App on a HistoryIT subdomain | Client, 23 Oct 2026 | Not started. She said this is the next iteration after the current work. |
| **P3** | Batch-size/cost optimization, quality stable hone ke baad | Client | Deferred. The cache investigation above is separate and is not a measured saving. |
| **Proposed** | Folder/Sheet links accept karke IDs automatically extract karna | Chosen improvement, authorized | On staging `ee1f7d4`, from `702e229`. Production reverts that release. |
| **Proposed** | Per-run approved model selection aur saved run configuration | Next-phase proposal | Client approval is not explicit. Not started. |

## Still pending

- Her written go-ahead before any further paid call, retry, or staging deploy.
- Implementation of fixes for the 29 Oct failures (N02–N06) after she reviews the investigation; do not retry the frozen jobs named above.
- Reconciliation of the imported 512 usage rows to her OpenAI export: 429 GPT-5.6 Sol requests, about $214.80, against about $175.31 in the registers she could identify.
- Deploy of the local accounting tightening and the explicit 30-minute cache breakpoint. Neither is committed.
- Live acceptance of `ee1f7d4`: empty-folder skip, Drive file identity, summary and task totals, publication crop, report grouping, F04 crop list, Rotate plus crop on `Photograph_016b`, and the Box 35 / Box 37 closes.
- Whole-box unattended acceptance on this build: one authorization, one Start, pause/resume, restart recovery, final readback PASS, and no duplicate processing.
- Box 37 remains frozen. B0029 is the last validated batch.
- Do not retry `HIT-fe3b819451ab26c3` or `HIT-2b95d2adea3e0a81`.
- Do not rewrite completed registers or the QC test jobs named above.
- SQL in HistoryIT’s AWS environment, a subdomain, and Admin-only concurrent users.
- Production promotion. `master` stays `4bbf3d7` until that is explicitly requested.

## CI/CD

- `origin/staging` is `ee1f7d44436e824eef9290a08db9269e093e716e`.
- Local `feed5e223aa5eafde2843ca15f7bae634b30d03a` is one commit ahead and changes only `Tasks.md`. It is not pushed.
- Production `master` is `4bbf3d7a3e9eb1a66010766c01d217034bdf8b05`. Commit `159d872` reverts `702e229`.
- `STAGING_AUTODEPLOY_ENABLED=true`. A push to `staging` can deploy immediately. The variable holds no credential.
- Admission guards are installed on both environments. Staging’s same-container restart check passed on the earlier deployment.
- `docs/CICD.md` on this branch still describes the earlier install procedure. The facts in this section supersede its status statements.

Earlier staging history, still true as history: automatic deploy of `b34f992` succeeded; production manual deploy of `4bbf3d7` succeeded. QC job `HIT-ea97b58c8e4a8c58` on `2044707` committed both batches and queued the 180-degree Rotate, and Metadata Only and crop/readiness failed. QC Test 02 on `c32563e` passed Metadata Only and Rotate. QC Test 03 on `5e4f67e` kept Metadata Only and dropped Rotate. That crop commit was reverted. Those registers were not rewritten.

## Crop evidence

Retained from the `HIT-ea97b58c8e4a8c58` investigation. These measurements describe the frames the model called a slight archival edge. They are not a failure threshold. Local views remain in `C:\Users\asa\AppData\Local\Temp\historyit_model_views\` as `*-original-view.jpg` and `*-model-input.jpg`. The model input is the original with its longest edge reduced to 2048 and the aspect ratio unchanged.

| File | Original frame and margins (L/T/R/B) | Model input and margins (L/T/R/B) |
|---|---|---|
| Photograph_008a | 7440×6720; 716 / 412 / 308 / 528 px | 2048×1850; 196 / 112 / 84 / 144 px |
| Photograph_009a | 7557×6616; 616 / 408 / 508 / 480 px | 2048×1793; 164 / 112 / 136 / 172 px |
| Photograph_013a | 8660×5773; 724 / 228 / 856 / 464 px | 2048×1365; 172 / 52 / 204 / 108 px |
| Photograph_016b | 8660×5773; 800 / 252 / 732 / 428 px | 2048×1365; 188 / 60 / 172 / 100 px |

`Photograph_016b` received the 180-degree Rotate task on that run, and that response left its surround in place. Her 23 Oct list requires both Rotate and Archival Border Crop for that file. That rule is on staging `ee1f7d4`. `_004` is the Metadata Only sheet, not a crop example.

The next live check, when she authorizes a paid run, is a new disposable register for Box 31 / F04, compared with her file list, then a second folder that was not used to write the example. Leave Box 19 calibration unchanged. After that, a separate multi-folder box confirms one Start, genuine blockers only, pause/resume, restart recovery, and final readback PASS.
