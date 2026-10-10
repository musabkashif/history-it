# F01 → F02 handoff verification

This report covers a local code and test verification. It is not a live F01 acceptance result. No job was started, and Google Drive, Google Sheets, and OpenAI were not called.

## Could an F01-only job continue to F02?

No. Before this change, the only create path was `POST /api/jobs`.

A job id is `HIT-` plus the first 16 hex characters of `sha256(spreadsheet_id|source_folder_id|project)`. Creating a job with the F02 folder id therefore builds a different job. After F01 commits, the Developer Test register is no longer a clean project start: Continuation State, Asset QC, Itemization, and Metadata References have rows. `initial_queue_prior` raises `Clean first-batch validation failed` and the new job is rejected.

`create()` also listed Drive before that check, so a mistaken second create could enumerate F02 even though the job was then rejected. Pre-seeded tests that already contained both F01 and F02 Batch Queue rows did not exercise this API path. There was no operation that added a folder to the existing job.

The clean-first guard still applies to `POST /api/jobs`. It does not apply to the new folder operation below.

## API request that authorizes F02 after F01

After the F01 batches are `Committed - Validated`, and without calling `/start` in the same request:

```http
POST /api/jobs/{job_id}/folders
Content-Type: application/json

{"source_folder_id":"F02_FOLDER_ID"}
```

`batch_size` is optional (1–100). `{job_id}` is the id returned when the F01 job was created. The job locator `Source Folder ID` stays the original F01 folder id.

The call enumerates only that folder, once. It writes new Batch Queue rows on the same job. The first new row's `Prior Open Item` is the last batch's `Resulting Open Item` (the F01 letter's record id when that letter is still open). It does not read Continuation State. It does not call `/start`, so F02 is not inspected until a later `POST /api/jobs/{job_id}/start`.

A repeated call for a folder already stored as `authorized_root` in Batch Queue Notes returns the current job state and does not enumerate or add rows.

Calling `POST /api/jobs` again with the F02 folder id is still a different job. On a register that already contains F01 production rows it returns HTTP 409 and does not enumerate Drive.

## Code changes

- `app/orchestrator.py`: `create()` rejects an existing job and runs the clean-first check before `Drive.enumerate`. New `authorize_folder()` queues one explicit folder onto an existing job. Batch Notes now include `authorized_root`. `state()` returns `authorized_source_folder_ids` from those notes. The clean-first guard is not used on this path.
- `app/models.py`: `JobState.authorized_source_folder_ids`, `FolderAuthorize`, and `F01CheckpointRequest`.
- `app/main.py`: `POST /api/jobs/{job_id}/folders` and `POST /api/benchmark/f01-checkpoint`. The checkpoint route reads the developer spreadsheet only.
- `app/benchmark.py`: `f01_calibration_checkpoint()`. Outcomes are `f01_intermediate_match` and `f01_intermediate_mismatch`. It does not read or write the Frozen Benchmark and is not used by commit. `compare_workbooks()` is unchanged and remains the read-only final Box 19 comparison.
- `tests/test_workflow.py`: API handoff test with mocked Drive, Sheets, and OpenAI, plus checkpoint tests.
- `README.md`, `docs/acceptance-testing.md`, and `docs/requirements-mapping.md`: F02 is authorized with the folders route; the F01 checkpoint is `/api/benchmark/f01-checkpoint`. A folder-scoped compare against the final workbook is not that checkpoint.

`client_reference/commit_engine.py` and `client_reference/test_commit_engine.py` were not modified. The Frozen Benchmark was not modified.

## Test evidence

`F02AuthorizationTests.test_f02_is_authorized_on_the_same_job_after_f01_commits` does all of the following through `TestClient` and `Orchestrator.run` with mocked Drive, Sheets, and a scripted inspector:

- `POST /api/jobs` for F01 enumerates the F01 folder once and queues it.
- A second `POST /api/jobs` for the same F01 folder returns 409 and does not enumerate again.
- `POST /api/jobs/{id}/folders` before F01 is validated returns 409 and does not enumerate F02 or call the inspector.
- After F01 commits Review/Open, the F01 folder summary is `Completed - Validated` while the letter's record id remains the open item.
- `POST /api/jobs` with the F02 folder id returns 409 `Clean first-batch validation failed` and does not enumerate F02.
- Continuation State was then set to `WRONG-ITEM`. `POST /api/jobs/{id}/folders` still wrote the F01 letter's record id into the F02 row's `Prior Open Item`.
- The F02 batch stayed `Authorized / Pending Inspection` until `run()` was called. The inspector was not called for F02 by the folders route.
- A second folders call did not enumerate again and did not add a batch.
- F02 inspection received only `TriDelta_Year2_Chapters_Box19_F02_001.jpg`, with the existing letter id and the committed F01 letter assets as context.
- After F02 closed the letter there was one letter row, 22 unique Asset QC filenames, and the F01 letter asset appeared once.

`F01CheckpointTests` distinguishes:

- a correct intermediate register: `f01_intermediate_match`;
- four normal records, or 20 assets: `f01_intermediate_mismatch`;
- the letter Ready/Closed with continuation `None` and 3 Ready rows: `f01_intermediate_mismatch`;
- folder-scoped `compare_workbooks(..., folder='F01')` of that correct intermediate register against a final Ready/Closed F01 letter: `semantic_benchmark_mismatch`, `matches` false, `review_records` live 1 versus benchmark 0;
- the same final workbook compared with itself, whole workbook: `exact_match`.

The checkpoint route test reads only spreadsheet id `register`.

## Expected response for a correct F01 intermediate checkpoint

`POST /api/benchmark/f01-checkpoint` with `{"spreadsheet_id":"DEVELOPER_ID"}`.

```json
{
  "checkpoint": "f01_intermediate",
  "outcome": "f01_intermediate_match",
  "matches": true,
  "expected": {
    "assets": 21,
    "metadata_only_references": 2,
    "normal_records": 3,
    "ready_records": 2,
    "review_open_letters": 1,
    "production_tasks": 0
  },
  "actual": {
    "assets": 21,
    "metadata_only_references": 2,
    "normal_records": 3,
    "ready_records": 2,
    "review_open_letters": 1,
    "production_tasks": 0
  },
  "problems": [],
  "open_intellectual_item": "TDC-B19-F01-003",
  "whole_workbook": false,
  "final_box19_match": false
}
```

`open_intellectual_item` is the live letter's Item ID. The value above is the id used by the checkpoint fixture. A live register can have a different id if identity assignment differs; the checkpoint requires that id to be a Type `Letter` row with Metadata Readiness `Review`, and that Continuation State `Open Intellectual Item` equal that id.

This is not an exact final-workbook match. `final_box19_match` is always false. A correct F01-only register compared with the Frozen Benchmark, including with `"folder": "F01"`, returns `semantic_benchmark_mismatch` because the frozen letter is Ready/Closed after F02.

## How F02 stays unprocessed until it is authorized

- `Drive.enumerate` lists only the folder id it is given. Creating the F01 job does not list sibling folders.
- `POST /api/jobs/{job_id}/folders` is the only way to add a later folder. It enumerates that one id, and only after every existing batch is `Committed - Validated` and the job thread is not running.
- The folders route does not call `start()`.
- A second `POST /api/jobs` for F02 is rejected by the clean-first guard before Drive is listed.
- Authorizing F02 before F01 is validated is rejected before Drive is listed.
- Inspection downloads only filenames stored in the batch manifest.

## Test output

```text
.\.venv\Scripts\python.exe -m pytest -q
.................................................................. [ 94%]
....                                                                     [100%]
============================== warnings summary ===============================
tests/test_workflow.py::F01CheckpointTests::test_f01_checkpoint_route_does_not_open_the_frozen_benchmark
  D:\Integer\HistoryIT\history-it-project\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
70 passed, 1 warning, 6 subtests passed in 7.55s

.\.venv\Scripts\python.exe -m pytest -q client_reference/test_commit_engine.py
...                                                                      [100%]
3 passed in 0.04s

.\.venv\Scripts\python.exe -m compileall app client_reference tests
Listing 'app'...
Listing 'client_reference'...
Listing 'tests'...
Compiling 'tests\test_workflow.py'...
exit code 0
```

`compileall` wrote bytecode under `__pycache__` for `tests/test_workflow.py`.

## Remaining blocker before a fresh paid F01 run

The local handoff and checkpoint tests passed. Live F01 acceptance is not established. No Developer Test job has been run, and this register has not been compared with the Frozen Benchmark.

Before spending on the run:

- Use a clean Developer Test copy. Create one job for the F01 folder only.
- After F01 commits, call `POST /api/benchmark/f01-checkpoint` on that register. Do not treat `POST /api/benchmark/compare`, with or without `"folder": "F01"`, as the F01 result. A correct Review/Open letter mismatches the frozen final letter.
- Do not create a second job for F02. After F01 is `Committed - Validated`, authorize F02 with `POST /api/jobs/{job_id}/folders`, then call `/start` only when that folder should be inspected.
- `Runtime_prompt_old_chatgpt.md` is present at the repository parent and is the default prompt when `RUNTIME_PROMPT_PATH` is unset. Keep that file in place for the live process.
