P0 = urgent blockers · P1 = essential reliability · P2 = cleanup/UX · P3 = later optimization

## Release

Feature release `702e229` was rolled back from production. Its features are on staging and are not approved as production-complete. Separate staging is deployed. Staging automatic Actions deployment succeeded for `b34f99278fd204288198a2a4cfc504b281af074a`. Production manual Actions deployment succeeded for `4bbf3d7a3e9eb1a66010766c01d217034bdf8b05`. `STAGING_AUTODEPLOY_ENABLED=true`. Admission guards are installed on both environments. Staging's same-container restart check passed. The three QC corrections below are on staging `2044707581382add501f3b6b934583282742b28b`. Job `HIT-ea97b58c8e4a8c58` observed the target 180-degree rotation. Metadata Only and crop/readiness acceptance failed on that job. QC Test 02 on release `c32563e` passed Metadata Only for `_004`/`_005` and the 180-degree Rotate for `Photograph_016b`. Crop/QC stayed unresolved. QC Test 03 on `5e4f67e` kept Metadata Only and dropped the Rotate task. That crop commit was reverted. Staging returns to the Test 02 inspection behavior. Crop/QC remains unresolved. The completed-folder summary correction is included for staging. Live acceptance of the corrected totals remains pending, and the existing test register was not rewritten. Whole-box acceptance remains pending. Database workspace work is not complete.

| Priority | Task | Kis ki request? | Current status |
|---|---|---|---|
| **P0** | Separate **staging environment**; live deployment/jobs/registers preserve karna | Waleed + client | Deployed. Staging automatic Actions deployment succeeded for `b34f992`. Admission guard installed. Same-container restart check passed. |
| **P0** | Box parent ek baar authorize karna; saare F-folders aur unki IDs automatically discover karna | Client | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: parent root discovers all immediate F* children (F01…F10…) in natural order; ambiguous/inaccessible/empty F-folders fail clearly; sibling non-F folders are not queued. Files: `server/app/box_folders.py`, `server/app/adapters.py` (`Drive.enumerate`), `Orchestrator.create` / `authorize_folder`. |
| **P0** | **Start once:** batches → inspection → commit → validation → next folder automatically | Client | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: one Start admits a backend runner that recovers pending commit/validation, then advances every authorized batch/folder without frontend polling. Files: `server/app/orchestrator.py` (`start`, `run`), `server/app/main.py` (`/start`). |
| **P0** | Sirf genuine blockers par stop: review, reconciliation failure, inaccessible files, spend limit, operator pause | Client | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: runner stops for Blocked/Pause/spend/retry exhaustion/validation failure; Cropping/QC production tasks do not halt the box; pause persists and stops at the next safe checkpoint. Files: `orchestrator.run` / `pause` / `resume`. |
| **P0** | Caption/reference sheet + blank verso ko **Metadata Only** classify karna; incorrect document/Rotate tasks fix karna | Client | Real-image acceptance passed on QC Test 02 and QC Test 03. Chapters Box 31 F04 `_004`/`_005` are one Metadata Only descriptive reference and its blank verso, with no production task. Photograph_009a/b/c stayed a normal collection record. The example remains inspection guidance for that folder only. Box 19 calibration is unchanged. Do not rewrite `HIT-ea97b58c8e4a8c58`, `HIT-b646d242c6175cdb`, or `HIT-36381173e12dba3e`. |
| **P0** | Excess black background aur archival-border **crop/QC/readiness** decisions correct karna | Client | Unresolved. Test 02 and Test 03 did not match the approved crop outcome. The experimental crop change `5e4f67e` was reverted because Test 03 dropped the Photograph_016b Rotate task. No new crop rule is in the restored Test 02 code. |
| **P0** | Missed **180° rotation** detect karna | Client | Passed on QC Test 02: `Photograph_016b` received the 180-degree Rotate task. Test 03 on `5e4f67e` replaced that task with Archival Border Crop. The inspection code is restored to the Test 02 release `c32563e`. One asset still cannot queue both Rotate and a crop. |
| **P1** | Start button par Starting/Running state; repeat submissions prevent karna | Client | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: Running/Startable are driven by `job.running` + `Running-*` labels only — idle `Authorized / Pending Inspection` stays startable (no false Running). Shared helpers in `types.ts`; Start/Resume/badge/Overview use the same rules. Files: `client/src/lib/types.ts`, `Job.tsx`, `Overview.tsx`, `ui.tsx`, `orchestrator.start`, `models.JobState`. |
| **P1** | Reliable automatic UI polling/refresh | Client | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: poll only while Starting… or backend worker is live (`jobIsActive`); idle Authorized jobs do not poll. Refresh on return to job view; ignore stale/overlapping responses; workspace switch isolation. Files: `client/src/main.tsx`, `client/src/lib/types.ts`. |
| **P1** | Readback validation complete hone se pehle final **Validated/PASS** na dikhana | Client | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: job status distinguishes `Commit Written - Pending Validation` / Running - Validating from final PASS; whole-job complete only when authorized scope is finished. Files: `orchestrator.state`, `Job.tsx` batch validation display, `register_state.source_scope_closed`. |
| **P1** | Successful commit/validation ke baad staging rows ka **Pending Commit** status update karna | Client | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: after successful readback, Asset/Record Staging `Commit Status` → `Committed - Validated`; idempotent repair on already-validated recovery. Files: `server/app/commit.py` (`_mark_staging_committed`). |
| **P1** | Database-backed **workspace/job list**, browse/reopen aur incomplete projects switch karna | Aapka proposal → **client approved as essential** | Pending |
| **P1** | Existing jobs ko updates ke baad safely resume karna; cross-project isolation verify karna | Client | Isolation partly tested; McCallie return/resume pending |
| **P1** | Har project ka separate authoritative Production Register, project location/Shared Drive mein | Client | Shared Drive setup worked; app workflow verify |
| **P2** | Hard-coded Tri Delta titles aur “F01–F06 discovered” text ko actual workspace/discovery se replace karna | Client | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: shell/header/browser title and job/overview copy use selected `job.project` and live `discovered_folders` (neutral “Odyssey console” when none). Removed Tri Delta / F01–F06 form defaults. Files: `layout.tsx`, `main.tsx`, `Job.tsx`, `Overview.tsx`, `NewJob.tsx`. |
| **P2** | Box 19-specific verification ko developer-only label karna **ya generic verification** dena | Client | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: default-off `ENABLE_DEVELOPER_VERIFICATION`; health exposes flag; nav/Job button hidden unless enabled; `/api/benchmark/*` returns 403 when off; labeled “Developer: Tri Delta Box 19 verification”. Files: `config.py`, `main.py`, `layout.tsx`, `Job.tsx`, `Benchmark.tsx`, `test_developer_verification.py`. |
| **P2** | Guide correct karna: **Sheets Editor**, source Drive folders Viewer | Client | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: operator guide covers Viewer vs Editor, service-account email (not key), F* hierarchy, One Start, statuses, Pause/Resume, restart needs Start, Job ID reopen only, links/IDs, QC supervision caveats. Files: `docs/OPERATOR_GUIDE.md`, `Settings.tsx`, `client/README.md`, `server/README.md`. |
| **P2** | Frontend ko **Odyssey Preservation** branding, logos/fonts/colors par align karna | Client | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: Odyssey palette tokens, mark SVGs from usage guide, favicon, login/shell branding. Font: Montserrat fallback (Neue Kabel license files not in repo). Files: `tailwind.config.js`, `index.css`, `index.html`, `public/odyssey-*.svg`, `Login.tsx`, `layout.tsx`. |
| **P3** | Batch-size/cost optimization, **quality stable hone ke baad** | Client | Deferred; 27-image call tested |
| **Proposed** | Folder/Sheet **links accept karke IDs automatically extract** karna | Aapka chosen improvement (now authorized to implement) | On staging, including 702e229. Rolled back from production; not approved as production-complete. Staging acceptance pending. Change: authoritative backend `google_ids` normalization for Drive folders + Sheets; create/authorize/benchmark models; accessibility/type gates before writes; resourcekey/spoof/export rejected. Files: `server/app/google_ids.py`, `models.py`, `orchestrator.py`, `adapters.py`, `NewJob.tsx`, `Job.tsx`, `test_google_ids.py`. |
| **Proposed** | Per-run approved model selection aur saved run configuration | Aapka next-phase proposal | Client approval not explicit |

## CI/CD

Verified deployment facts:

- Separate staging environment is deployed.
- Staging automatic Actions deployment succeeded for `b34f99278fd204288198a2a4cfc504b281af074a`.
- Production manual Actions deployment succeeded for `4bbf3d7a3e9eb1a66010766c01d217034bdf8b05`.
- `STAGING_AUTODEPLOY_ENABLED=true`. A push to `staging` can deploy immediately. The variable holds no credential.
- Admission guards are installed on both environments.
- Staging's same-container restart check passed.
- Feature release `702e229` was rolled back from production. Its features are available on staging and are not approved as production-complete.
- Production deployment remains manual `workflow_dispatch` on `master` only. Do not merge this feature branch into `master`.

`docs/CICD.md` on this branch still describes the earlier install procedure. The facts in this section supersede its status statements.

Local checks for `feature/archival-qc-corrections`, based on `f36c6ec`, passed on 2026-10-19 and were not a staging acceptance run: backend `pytest` 189 passed and 6 subtests; deployment contract tests 32; both deterministic commit-engine reference suites 3 each; frontend `tsc --noEmit` and Vite production build on Node 22.22.3. No paid model call and no Google Sheets or Drive write.

Staging job `HIT-ea97b58c8e4a8c58` on release `2044707` processed the 39-image folder. Both batches committed. `Photograph_016b` received the 180-degree Rotate task. Metadata Only and crop/readiness did not match the acceptance target. The completed folder summary kept the last batch's 19 assets. Completion now rebuilds that row from the committed rows for the same project, box, and folder. Live acceptance of those corrected totals remains pending, and that register was not rewritten. Whole-box acceptance was not part of this job.

After that rollup, local checks on 2026-10-19 passed: backend `pytest` 190 and 6 subtests; deployment contract tests 32; both commit-engine reference suites 3 each; frontend `tsc --noEmit` and Vite production build on Node 22.22.3. No paid inference call and no register write.

Local checks on 2026-10-20 for the Box 31 F04 Metadata Only calibration passed: backend `pytest` 195 and 6 subtests. No paid inspection call and no register write. Crop/readiness was not changed.

The crop experiment `5e4f67e2b0053a134bb992e167cffde143f66a20` is reverted. Test 02 inspections, taken from the response IDs in that workbook, were 2026-10-20 07:56:12Z and 08:01:46Z. The only staging release in that window was `c32563eb058ee08bfb3af016dcb7ccb6fbd38dda`, deployed at 07:21:09Z. The next deploy was `5e4f67e` at 13:53:52Z.

Local checks on 2026-10-21 for this restoration passed: backend `pytest` 195 and 6 subtests; deployment contract tests 32; both commit-engine reference suites 3 each; frontend `tsc --noEmit` and Vite production build on Node 22.22.3. No paid inspection call and no register write. These checks restore the Test 02 code. They do not by themselves prove the next image run will request Rotate.

## Still pending

- Crop/QC. It remains unresolved. Test 03 is not a crop acceptance run. Do not add a pixel threshold or another crop rule on top of the restored Test 02 code.
- Live acceptance of the corrected folder-summary rollup. The completed test register was not rewritten.
- Whole-box unattended acceptance. The two-batch mechanical path for one folder is verified; multi-folder acceptance is not.
- Restart/resume and project-switch acceptance.
- Database-backed workspace/job list and the other rows still marked pending above.
- Historical repair of already committed jobs or registers. Do not rewrite them from this change.

## Staging acceptance

Use a disposable register. Leave production registers and the frozen benchmark untouched. The repository has no Drive ID for Tri Delta Chapters Box 31 / F04; use the client's existing authorized source folder and do not invent an ID.

A. Actual 39-image Box 31 / F04 QC comparison. One disposable register, the existing authorized folder, two batches as supplied. Compare with the client's approved result as evidence, not as hard-coded totals: descriptive caption/reference sheet plus its blank verso are one Metadata Only reference with no production tasks; a normal document and its blank verso stay a normal record; excess black capture background blocks normal records for Cropping/QC; a compliant slight archival edge does not create a crop task; `TriDelta_Year2_Chapters_Box31_F04_Photograph_016b.jpg` gets a 180-degree Rotate task; upright photographs, blank images, and intentional layouts do not. The first run, `HIT-ea97b58c8e4a8c58`, verified the two-batch commit and the 180-degree task. Metadata Only and crop/readiness failed. QC Test 02, `HIT-b646d242c6175cdb`, passed Metadata Only and the 180-degree Rotate and left crop/QC unresolved. QC Test 03, `HIT-36381173e12dba3e`, kept Metadata Only and lost the Rotate task. That regression is reverted. Do not rewrite those registers. Crop/QC is still unresolved. Whole-box orchestration remains a separate pending acceptance.

## Crop evidence

Retained from the `HIT-ea97b58c8e4a8c58` investigation. These measurements describe the frames the model called a slight archival edge. They are not a failure threshold. Local views remain in `C:\Users\asa\AppData\Local\Temp\historyit_model_views\` as `*-original-view.jpg` and `*-model-input.jpg`. The model input is the original with its longest edge reduced to 2048 and the aspect ratio unchanged.

| File | Original frame and margins (L/T/R/B) | Model input and margins (L/T/R/B) |
|---|---|---|
| Photograph_008a | 7440×6720; 716 / 412 / 308 / 528 px | 2048×1850; 196 / 112 / 84 / 144 px |
| Photograph_009a | 7557×6616; 616 / 408 / 508 / 480 px | 2048×1793; 164 / 112 / 136 / 172 px |
| Photograph_013a | 8660×5773; 724 / 228 / 856 / 464 px | 2048×1365; 172 / 52 / 204 / 108 px |
| Photograph_016b | 8660×5773; 800 / 252 / 732 / 428 px | 2048×1365; 188 / 60 / 172 / 100 px |

`Photograph_016b` received the 180-degree Rotate task, and that response left its surround in place. `Photograph_017c` is the one file the model marked for an archival-border crop. Its original is not in this package. `_002` and `_004` were also measured with wide dark margins; `_004` is the Metadata Only sheet, not a crop example.

Missing approved visual controls: the calibration set has no client-approved image of a compliant slight edge, and the client has not named which captures in this folder are excess capture background and which already meet the slight-edge standard. The governing 75-pixel figure is the finished-crop deliverable border when the cropping software can count pixels. It is not a detector for the original or the resized model input.

The crop experiment `5e4f67e` added Border Assessment and extra Review handling. QC Test 03 then produced no Rotate task. That commit is reverted. The inspection code again matches Test 02 release `c32563e`.

An eventual crop correction is not accepted by rechecking only Photograph_008a, Photograph_009a, Photograph_013a, Photograph_016b, and Photograph_017c. After the client names the excess captures and the compliant slight-edge captures, test it this way: send only those named files as the same kind of scoped inspection guidance, with no pixel threshold and no code that rewrites No Adjustment into a crop; repeat A on a new disposable register; then run a second folder that was not used to write the example and that contains at least one client-confirmed excess capture and one client-confirmed slight edge. Keep the existing negation cases and the slight-edge pass test. Leave Box 19 calibration and the supporting-sheet rule unchanged.

B. A separate multi-folder, multi-batch whole-box run, not the Box 31 comparison register. One authorization and one Start. Confirm automatic advancement, stop only on genuine blockers, pause/resume, restart recovery, final readback PASS, staging-row states after validation, and no duplicate processing.

Promotion after those passes: review this branch, then push or merge only to `staging`. That push can auto-deploy. Production stays a separate manual `workflow_dispatch` after staging acceptance, and it does not include this branch until that promotion is explicitly requested.
