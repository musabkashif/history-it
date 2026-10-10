# HistoryIT Deterministic Commit Engine - Reference Package

This package is the first implementation-ready version of the non-LLM commit layer for the Archive Itemization workflow.

## Files

- `COMMIT_ENGINE_SPEC.md` — production behavior, invariants, validation, retry/idempotency, and large-folder orchestration.
- `commit_engine.py` — pure-Python deterministic planner. It has no image/folder access and no Google dependency.
- `test_commit_engine.py` — offline acceptance tests including the F05-B01 transformation.

## What this code does now

The planner validates a commit-ready batch and deterministically builds:

- Asset QC rows
- Itemization create/update operations
- Metadata Reference create/update operations
- Production Queue tasks
- Ready for Metadata create/update operations
- open-item state

It also includes canonical cumulative metric calculation and idempotent/no-op handling for already-committed batches.

## What remains before production deployment

1. Add a Google Sheets storage adapter that reads/writes by header names and Batch ID rather than hard-coded rows.
2. Add the queue/job manifest builder that enumerates an authorized folder once and creates all micro-batches.
3. Add deterministic Project Register / Box Summary / Folder Processing Unit Summary / Continuation State row builders.
4. Add the 15-check validation writer and readback verifier around the pure planner.
5. Add retry/recovery behavior for `Commit Written - Pending Validation`.
6. Run shadow tests against additional F05 batches before enabling unattended auto-commit.

## Local test

```bash
cd historyit_commit_engine
python -m unittest -v test_commit_engine.py
```

The reference code deliberately has no Google credentials or source-image permissions.
