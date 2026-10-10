# F01 calibration implementation report

Project root: `D:\Integer\HistoryIT\history-it-project`

No live job was started. Google Drive, Google Sheets, and OpenAI were not called. This report does not claim that a live register matches the Frozen Benchmark.

## Reference

- ID: `tri-delta-chapters-box19-f01-calibration`
- Version: `2026-10-07.1`
- File: `project_references/tri-delta-chapters-f01-calibration.json`
- Index scope: Tri Delta Chapters, Chapters Box 19, folders F01 and F02
- `material_class`: `client-approved-calibration-example`
- `approval`: `client-approved`
- `not_an_executable_rule`: true

The example states the approved outcomes and the visual reasoning: 001 Metadata Only folder/context; 002–003 Metadata Only descriptive sheet and blank verso; 004–013 one Document / Report, Ready / High; 014–015 one Document / List, Ready / High; 016–021 one Correspondence / Letter, Review / Medium / Open, no production tasks. It says the first authorized F02 images decide continuation or closure, and a folder boundary does not force closure.

## How the model receives it

`resolve_reference` returns that object for F01 and F02 only. F03 and other projects receive `generic-archival-method.json` and do not receive the examples.

`Orchestrator._inspection_inputs` puts the object on `approved_project_reference`. `Inspector.inspect` sends it as JSON in the user message and logs `reference_id` and `reference_version` on `request_prepared`. The system text says the example is calibration, not a filename rule. The filenames stay in the JSON payload, not in a second archival prompt.

A contradiction is reported before `_stage` and before commit:

```python
raise ReferenceError('Staging was not changed. Calibration conflict: ' + '; '.join(problems))
```

`order_records` returns the model list unchanged. `app/commit.py`, `app/identity.py`, and `client_reference/commit_engine.py` do not read the calibration file. `git diff --stat` for `app/commit.py`, `client_reference/commit_engine.py`, and `client_reference/test_commit_engine.py` was empty.

Unapproved material is still refused: a `groups` answer file, and a calibration-shaped file that is not listed under `approved_calibration`.

## Folder completion and F02

`_close_folders` marks a folder `Completed - Validated` when every batch for that folder is `Committed - Validated` and Asset QC contains each manifest filename once. It does not require `Resulting Open Item` or Continuation State to be `None`.

After F01 commits with the letter open, `_authorize_following` writes that record ID into the next blank Batch Queue `Prior Open Item`. F02 inspection receives that record and its committed assets, and its manifest is only the authorized F02 filenames.

## Changed files

- `project_references/tri-delta-chapters-f01-calibration.json` (added)
- `project_references/index.json`
- `app/references.py`
- `app/inspection.py`
- `app/orchestrator.py`
- `app/benchmark.py`
- `app/models.py`
- `app/main.py`
- `tests/test_workflow.py`
- `README.md`
- `docs/acceptance-testing.md`
- `docs/requirements-mapping.md`

Not modified: `client_reference/commit_engine.py`, `client_reference/test_commit_engine.py`, `app/commit.py`, `app/identity.py`, `Runtime_prompt_old_chatgpt.md`, the Frozen Benchmark workbook.

## Tests

Working directory: `D:\Integer\HistoryIT\history-it-project`

```
.\.venv\Scripts\python.exe -m pytest -q
```

Exit code 0.

```
................................................................   [100%]
64 passed, 6 subtests passed in 8.00s
```

Failed: 0. Skipped: 0.

```
.\.venv\Scripts\python.exe -m pytest -q client_reference/test_commit_engine.py
```

Exit code 0.

```
...                                                                      [100%]
3 passed in 0.05s
```

```
.\.venv\Scripts\python.exe -m compileall app client_reference tests
```

Exit code 0. It listed `app`, `client_reference`, and `tests`, and recompiled `tests\test_api_errors.py`, `tests\test_credits.py`, `tests\test_stage.py`, and `tests\test_workflow.py`.

The suite covers calibration loading for F01 and F02, exclusion for F03 and another project, rejection of an unapproved `groups` file, rejection of an unlisted calibration-shaped file, a Review/Open letter with evidence, a contradiction that blocks with Asset QC left empty, missing Review Note evidence, next-batch prior propagation, F02 continuation without a second letter row, F02 closure from its own images, folder `Completed - Validated` while the letter is open, and an F01-scoped benchmark checkpoint that does not require the whole six-folder workbook.

## Comparison limit

`POST /api/benchmark/compare` stays read-only. `folder` scopes Itemization, Asset QC, Metadata References, Ready for Metadata, Production Queue, and the folder summary. Continuation State, Box Summary, and Project Register are omitted from that checkpoint because the Frozen Benchmark is the finished six-folder workbook. An F01-only register is not an `exact_match` for the whole workbook. The correct F01 checkpoint is Review/Open. The frozen final letter may be closed or Ready because F02 resolved it. Whole-workbook `exact_match` waits until F01–F06 have been processed.

`Committed - Validated` means that batch's source count, staging, and readback passed. It is not Box 19 acceptance by itself.

## Remaining limitation

No clean Developer Test job was run, and the read-only compare endpoint was not executed against the Frozen Benchmark. Live F01 agreement is not claimed.
