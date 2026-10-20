# HistoryIT Archive Itemization Automation MVP

FastAPI service that orchestrates exact Drive manifests, isolated OpenAI visual inspection, Lean Split v2 staging, the supplied deterministic commit planner, header-based Google Sheets writes, readback validation, retries, continuation state, idempotency, usage reporting and benchmark comparison.

## Local setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Swagger is at `http://localhost:8000/docs`. Required live configuration is documented in `.env.example`. Set `JOB_INDEX_SPREADSHEET_ID` to a small spreadsheet/tab where the service stores only job-to-register locators; the register remains authoritative.

## Credentials

Create a Google service account. Share **Drive source folders** with its `client_email` as **Viewer**. Share the **Production Register / Developer Test spreadsheet** as **Editor** (the app writes). Set `GOOGLE_APPLICATION_CREDENTIALS` to the mounted JSON file. Put the governing packet documents in `GOVERNING_DOCS_DIR`. Set `OPENAI_API_KEY` and a deployable `OPENAI_MODEL`; never commit either secret. Set model pricing per million tokens so spending limits are enforceable.

Operator-facing detail: [`../docs/OPERATOR_GUIDE.md`](../docs/OPERATOR_GUIDE.md).

Create Job and Authorize Folder accept Drive/Sheets **links or IDs**; the API normalizes to canonical IDs before writes (`app/google_ids.py`).

`ENABLE_DEVELOPER_VERIFICATION` (default `false`) gates the Tri Delta Box 19 F01 checkpoint and Frozen Benchmark endpoints. Leave it off for normal operator deployments.

## API examples

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/jobs -H 'Content-Type: application/json' \
  -d '{"project":"Tri Delta Chapters","source_folder_id":"DRIVE_FOLDER_ID","spreadsheet_id":"REGISTER_ID","batch_size":10,"box":"Chapters Box 19"}'
curl -X POST http://localhost:8000/api/jobs/HIT-.../start
curl -X POST http://localhost:8000/api/jobs/HIT-.../folders -H 'Content-Type: application/json' \
  -d '{"source_folder_id":"F02_FOLDER_ID"}'
curl http://localhost:8000/api/jobs/HIT-.../usage
curl -X POST http://localhost:8000/api/benchmark/f01-checkpoint -H 'Content-Type: application/json' \
  -d '{"spreadsheet_id":"DEVELOPER_ID"}'
curl -X POST http://localhost:8000/api/benchmark/compare -H 'Content-Type: application/json' \
  -d '{"spreadsheet_id":"DEVELOPER_ID","benchmark_spreadsheet_id":"FROZEN_ID"}'
```

## Recovery

Restart the API and call `/start` again. Batch Queue and staging are checked before another inspection call. `Committed - Validated` is a verified no-op. `Commit Written - Pending Validation` replays deterministic expected rows, rejects conflicts, and validates readback. Use `/api/batches/{batch_id}/retry` only for retryable/blocked batches after reviewing the recorded error.

## Diagnostics

Each inspection attempt writes a safe JSON diagnostic file under `DIAGNOSTICS_DIR` (default `./logs`). The file records the model, response ID/status, token usage, elapsed time, authorized filenames, returned filenames, returned record IDs, missing/extra filenames, and the exact validation error. It never records API keys, prompts, or image bytes. Attach the matching `logs/HIT-..._B..._attempt-....json` file when reporting a failure.

If the OpenAI account is out of credits, the current batch becomes `Blocked - Human Review` and later batches are not sent. Completed batches stay as they are. This does not count against `MAX_RETRIES`. `GET /api/jobs/{job_id}`, `/batches`, and `/usage` return `alert` with the reason and the resume instruction. Add credits, then call `POST /api/jobs/{job_id}/resume`. A normal rate limit stays `Inspection Failed - Retryable`.

## Current limits

The governing DOCX packet is included under `governing/`. The Developer Test and Frozen Benchmark spreadsheet IDs, Google credentials, and OpenAI credentials are deployment inputs, so live Box 19/Box 30 runs still require client access and acceptance review. Google Sheets tab creation for the auxiliary `HistoryIT Jobs` and `OpenAI Usage` tabs should be performed before the first live job (or enabled in the adapter).


## Repair notes

The orchestrator honors `BATCH_SIZE` exactly, including for folders with fewer than 25 files. Inspection reads `Prior Open Item` from the current Batch Queue row. Continuation State is updated only after a successful commit and does not override that row. A model response is rejected before staging unless it contains one ordered asset row for every authorized filename and every asset links to a returned record row. Original Drive bytes are sent when they fit the API limits; a required resize keeps the aspect ratio and a high-quality encoding.

`project_references/tri-delta-chapters-f01-calibration.json` (`tri-delta-chapters-box19-f01-calibration`, version `2026-10-07.1`) is a client-approved calibration example for Tri Delta Chapters, Chapters Box 19, folders F01 and F02. `project_references/tri-delta-chapters-box31-f04-calibration.json` (`tri-delta-chapters-box31-f04-calibration`, version `2026-10-20.1`) is the client's explicit correction for Chapters Box 31 folder F04: `_004` and `_005` are one Metadata Only descriptive reference and its blank verso, and Photograph_009a/b/c stay a normal collection record. Inspection loads a listed example only for its project, box, and folder, and logs that reference ID and version. Neither file is an executable rule: commit, identity assignment, and Sheets writes do not copy its examples onto staging. If a model answer contradicts an example on the same known filenames, including by placing the Box 31 sheet on the Photograph 009 record, the batch stops before commit and staging is left unchanged. Unapproved answer files are refused.

The benchmark compare endpoint reports row-level differences and classifies the result as an exact match, internally consistent only, or a semantic mismatch. It does not write either workbook. A register that contains only F01 will not be an exact match to the six-folder Frozen Benchmark. The frozen workbook is the final Box 19 state after F02 resolved the F01 letter, so a correct F01-only Review/Open register is a `semantic_benchmark_mismatch` against that letter even when `folder` is `F01`. Use `POST /api/benchmark/f01-checkpoint` for the client-approved intermediate F01 outcome. Its outcomes are `f01_intermediate_match` and `f01_intermediate_mismatch`. It reads only the developer register and does not open the Frozen Benchmark. Whole-workbook `exact_match` waits until F01–F06 have been processed.

After F01 is `Committed - Validated`, authorize F02 on the same job with `POST /api/jobs/{job_id}/folders`. That call enumerates only the supplied folder, queues it, and does not start inspection. A second `POST /api/jobs` for the F02 folder is a different job and is rejected by the clean-first-batch guard. `Committed - Validated` means the batch's own staging, source count, and readback passed. It is not by itself F02–F06 acceptance or a whole-workbook benchmark match. F01 source processing may be `Completed - Validated` while the letter remains open for F02.

Use a clean copy of the Developer Test register for every acceptance attempt. Do not reuse a sheet containing a previously committed wrong classification. The Frozen Benchmark is read-only. Rotate any service-account or OpenAI secrets that were ever stored in a ZIP, Cursor export, chat, or source repository.
