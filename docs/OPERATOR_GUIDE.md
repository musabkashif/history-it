# Odyssey operator guide

Plain-language setup and runbook for the Odyssey Preservation Software console (HistoryIT archive itemization). This is the operator-facing guide. Developer Tri Delta Box 19 verification is documented separately and stays off by default.

## Access the service account needs

| Resource | Permission | Why |
|---|---|---|
| Google Drive **source folders** (box parent or F-folder) | **Viewer** | The app only lists and reads images. |
| Production Register / Developer Test **Google Sheets** | **Editor** | The app writes Batch Queue, staging, validation, and usage rows. |
| Frozen Benchmark spreadsheet (developer harness only) | **Viewer** | Read-only comparison. Never grant Editor to a frozen benchmark. |

### How to find the service-account email

1. Open the service-account JSON your deployer mounted as `GOOGLE_APPLICATION_CREDENTIALS`.
2. Copy the `client_email` value (ends with `.iam.gserviceaccount.com`).
3. Share Drive folders and Sheets with that email using the permissions above.
4. Never paste the private key, the full JSON, or password hashes into chat, tickets, or the UI.

Possessing a Drive/Sheets **link** does not grant the service account access. Share the resource explicitly.

## Supported folder hierarchy

Verified from `server/app/box_folders.py` and Drive enumeration:

- Authorize a **parent box folder** whose **immediate children** are named like `F01`, `F02`, … `F10`, … (letter `F` + digits).
- Those F-folders must contain **images directly** (JPEG/PNG/TIFF/WebP). Deeper nesting under an F-folder is not treated as a separate queue unit.
- Sibling folders that are **not** F-named are **not** queued.
- You may instead authorize a **single F-folder** (or a leaf folder of images). That queues only that folder.
- Empty or ambiguous F-folders (for example both `F1` and `F01` as siblings) fail clearly; they are not skipped.

There is **no** hard-coded F01–F06 limit. Discovery uses whatever immediate F* children exist.

## Links or IDs

Create Job, Authorize folder, and developer verification fields accept:

- Raw Drive folder IDs / Sheets spreadsheet IDs
- Drive folder URLs such as `https://drive.google.com/drive/folders/<ID>` (including `/drive/u/0/folders/…` and `?usp=sharing`)
- Sheets URLs such as `https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0` (the **spreadsheet** ID is kept; numeric `gid` tab ids are ignored)
- Surrounding whitespace is trimmed

Not supported:

- Lookalike hosts, `user:pass@` URLs, arbitrary non-Google URLs
- Published `/spreadsheets/d/e/…` export links
- Links that rely only on a Google `resourcekey` without direct service-account access

The backend normalizes to a canonical ID before any job/register writes. URLs are not stored.

## One Start for the authorized scope

1. Create a workspace (or open an existing Job ID in Settings). Authorization queues batches; it does **not** start inspection.
2. Press **Start / continue** once. The backend runner recovers pending commit/validation, then advances inspection → commit → validation → next batch/folder for the whole authorized scope.
3. The browser does not need to stay open for the worker to continue, but refresh/polling keeps the UI truthful while a worker is live.

## Status meanings

| Status | Meaning |
|---|---|
| Authorized / Pending Inspection | Queued and idle — press Start. |
| Running / Running - Inspecting | Live worker inspecting. |
| Running - Committing | Live worker writing commit. |
| Running - Validating / Commit Written - Pending Validation | Commit written; readback validation not finished — not final PASS. |
| Inspection Complete - Awaiting Commit | Inspection finished; commit still needed (Start recovers this). |
| Committed - Validated | That batch passed readback. |
| Completed - Validated | Every batch in the authorized scope is Committed - Validated. |
| Paused | Operator pause requested; worker stops at the next safe checkpoint. |
| Blocked - Human Review / Failed - Retryable | Genuine blocker or retryable failure — fix the cause, then Resume/Retry/Start as appropriate. |

Crop / QC / Rotate **tasks recorded in the register** describe recommended production work. They do **not** mean Odyssey has already edited the image pixels.

Mechanical readback validation proves staging/register consistency. It does **not** prove archival judgment quality. Tri Delta QC/readiness/rotation judgment defects remain under separate supervision until those AI fixes ship.

## Pause, Resume, and restart

- **Pause** sets pause intent and stops at the next safe batch checkpoint.
- **Resume** clears pause so Start can continue.
- After a **backend restart**, in-memory workers stop. Batch Queue and staging remain authoritative. Call **Start** (or Resume then Start) again — recovery replays pending commit/validation before new inspections. There is no silent auto-resume without Start.

## Reopening an existing job

Today you reopen a job by pasting its **Job ID** in Settings. There is **no** database-backed workspace browser yet (that backlog item is still pending).

## Developer Tri Delta Box 19 verification

Hidden and API-blocked unless the server sets `ENABLE_DEVELOPER_VERIFICATION=true` (default `false`). When enabled, the console shows **Developer: Tri Delta Box 19 verification** for the F01 checkpoint and Frozen Benchmark harness only. It is **not** generic project verification.

## Safe defaults

- Use a clean Developer Test register copy for acceptance runs.
- Keep the Frozen Benchmark read-only.
- Do not treat F01 intermediate checkpoint success as whole-box acceptance.
