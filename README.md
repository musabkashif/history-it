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

Create a Google service account with read-only Drive access to the authorized source folder and Sheets read/write access to the developer register. Set `GOOGLE_APPLICATION_CREDENTIALS` to the mounted JSON file. Put the governing packet documents in `GOVERNING_DOCS_DIR`. Set `OPENAI_API_KEY` and a deployable `OPENAI_MODEL`; never commit either secret. Set model pricing per million tokens so spending limits are enforceable.

## API examples

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/jobs -H 'Content-Type: application/json' \
  -d '{"project":"Tri Delta Chapters","source_folder_id":"DRIVE_FOLDER_ID","spreadsheet_id":"REGISTER_ID","batch_size":10,"box":"Chapters Box 19"}'
curl -X POST http://localhost:8000/api/jobs/HIT-.../start
curl http://localhost:8000/api/jobs/HIT-.../usage
curl -X POST http://localhost:8000/api/benchmark/compare -H 'Content-Type: application/json' \
  -d '{"spreadsheet_id":"DEVELOPER_ID","benchmark_spreadsheet_id":"FROZEN_ID"}'
```

## Recovery

Restart the API and call `/start` again. Batch Queue and staging are checked before another inspection call. `Committed - Validated` is a verified no-op. `Commit Written - Pending Validation` replays deterministic expected rows, rejects conflicts, and validates readback. Use `/api/batches/{batch_id}/retry` only for retryable/blocked batches after reviewing the recorded error.

## Current limits

The uploaded packet does not contain the four governing DOCX references or zipped developer reference named by the client, so live OpenAI inspection is intentionally blocked until they are supplied. Google Sheets tab creation for the auxiliary `HistoryIT Jobs` and `OpenAI Usage` tabs should be performed before the first live job (or enabled in the adapter). Real Box 19/Box 30 runs still require client credentials and acceptance review.
