# HistoryIT verification report

Project root: `D:\Integer\HistoryIT\history-it-project`

Git root: `D:\Integer\HistoryIT`. Branch `master`. HEAD `2d70fac`.

No live job was started. Google Drive, Google Sheets, and OpenAI were not called. Credential values are not in this report.

This file replaces the earlier read-only audit. The code corrections below were made after that audit.

## 1. Git status and diff summary

`git status --short --branch` from `D:\Integer\HistoryIT`:

```
## master
 M history-it-project/.env.example
 M history-it-project/.gitignore
 M history-it-project/README.md
 M history-it-project/app/config.py
 M history-it-project/app/diagnostics.py
 M history-it-project/app/inspection.py
 M history-it-project/app/main.py
 M history-it-project/app/orchestrator.py
 M history-it-project/docs/acceptance-testing.md
 M history-it-project/docs/requirements-mapping.md
 M history-it-project/tests/test_stage.py
?? history-it-project/VERIFICATION_REPORT.md
?? history-it-project/app/authorization.py
?? history-it-project/app/benchmark.py
?? history-it-project/app/images.py
?? history-it-project/app/references.py
?? history-it-project/project_references/
?? history-it-project/tests/test_workflow.py
```

`git diff --stat` for tracked files: 11 files changed, 424 insertions, 158 deletions.

`git diff --stat` for `history-it-project/app/commit.py`, `history-it-project/client_reference/commit_engine.py`, and `history-it-project/client_reference/test_commit_engine.py` printed no diff. Those files were not modified.

Untracked files are not in `git diff`. `project_references/tri-delta-chapters-f01.json` was deleted during this correction. It had contained the F01 filename-to-type answer key. It is gone from disk.

## 2. Changed files

Tracked modifications:

- `.env.example`, `.gitignore`, `README.md`
- `app/config.py`, `app/diagnostics.py`, `app/inspection.py`, `app/main.py`, `app/orchestrator.py`
- `docs/acceptance-testing.md`, `docs/requirements-mapping.md`
- `tests/test_stage.py`

Untracked application files:

- `app/authorization.py`, `app/benchmark.py`, `app/images.py`, `app/references.py`
- `project_references/index.json`, `project_references/generic-archival-method.json`
- `tests/test_workflow.py`
- this report

Not modified: `app/commit.py`, `client_reference/commit_engine.py`, `client_reference/test_commit_engine.py`, `Runtime_prompt_old_chatgpt.md`, the Frozen Benchmark workbook.

## 3. Prior Open Item

The current batch value is the Batch Queue cell. Continuation State is read only by the first-batch clean check. It is not copied onto the queue.

First batch of a new job:

```31:38:app/authorization.py
def initial_queue_prior(continuation_open, unresolved, asset_qc, itemization, metadata_refs) -> str:
    """Return None for a new first batch, or raise when the register is not clean.

    Continuation State is read only to reject a dirty register. It is never copied
    onto Batch Queue.
    """
    assert_clean_first_batch('None', continuation_open, unresolved, asset_qc, itemization, metadata_refs)
    return 'None'
```

A later batch with a blank Prior Open Item writes the previous batch's Resulting Open Item, or `None` when that field is empty. The previous batch must already be `Committed - Validated`. Continuation State is not in this branch:

```216:229:app/orchestrator.py
    def _queue_prior(self,sid,rows,index,continuation,unresolved,asset_qc,items,refs):
        batch=rows[index]
        if index==0:
            return assert_batch_prior(batch.get('Prior Open Item'),continuation,unresolved,asset_qc,items,refs,first_batch=True)
        explicit=batch.get('Prior Open Item')
        if str(explicit or '').strip():
            return assert_batch_prior(explicit,'None','None',[],[],[],first_batch=False)
        previous=rows[index-1]
        if previous.get('Batch Status')!='Committed - Validated':
            raise BatchAuthorizationError('Previous batch is not Committed - Validated. Refusing to start the next batch.')
        resulting=str(previous.get('Resulting Open Item') or '').strip() or 'None'
        self.sheets.write(sid,'Batch Queue',{'Prior Open Item':resulting},('Batch ID',batch['Batch ID']))
        batch['Prior Open Item']=resulting
        return resulting, 'previous Batch Queue Resulting Open Item'
```

An explicit non-blank cell, including the string `None`, is returned as the Batch Queue value (`authorization.py` lines 67-71). A later explicit id is not replaced.

After a validated commit, the next blank cell is filled from Resulting Open Item only:

```199:206:app/orchestrator.py
    def _authorize_following(self,sid,job,batch_id):
        rows=self._queue(sid,job)
        index=next(i for i,row in enumerate(rows) if row['Batch ID']==batch_id)
        if index+1>=len(rows) or rows[index].get('Batch Status')!='Committed - Validated':return
        nxt=rows[index+1]
        if str(nxt.get('Prior Open Item') or '').strip():return
        resulting=str(rows[index].get('Resulting Open Item') or '').strip() or 'None'
        self.sheets.write(sid,'Batch Queue',{'Prior Open Item':resulting},('Batch ID',nxt['Batch ID']))
```

`None` does not attach an open record:

```207:211:app/orchestrator.py
    def _open_details(self,prior,items,refs,asset_qc):
        if prior=='None':return []
        rows=[r for r in items if r.get('Item ID')==prior]+[r for r in refs if r.get('Reference ID')==prior]
        if not rows:
            raise RegisterStateError(f'Prior Open Item {prior} has no Itemization or Metadata References row. Refusing to invent an open item.')
```

Search of `app/orchestrator.py` for `Open Intellectual Item` finds two reads: job creation and `_inspection_inputs`. Both pass the value into `assert_clean_first_batch` / `initial_queue_prior`. Neither assigns it to Prior Open Item.

Continuation State writes remain in `CommitService._summaries`, which `commit()` calls only after the validation checks pass (`app/commit.py` lines 96-107, then line 102 calls `_summaries`, and lines 123-124 call `set_field`).

## 4. Clean first-batch validation

All six conditions are required. Any failure raises `RegisterStateError`:

```41:63:app/authorization.py
def assert_clean_first_batch(prior, continuation_open, unresolved, asset_qc, itemization, metadata_refs):
    queue_prior = normalize_prior(prior) or 'None'
    continuation = normalize_prior(continuation_open) or 'None'
    unresolved_value = normalize_prior(unresolved) or 'None'
    problems = []
    if queue_prior != 'None':
        problems.append(f'Batch Queue Prior Open Item is {queue_prior}, required None')
    if continuation != 'None':
        problems.append(f'Continuation State Open Intellectual Item is {continuation}, required None')
    if unresolved_value != 'None':
        problems.append(f'Continuation State Unresolved Boundary Item is {unresolved_value}, required None')
    if asset_qc:
        problems.append(f'Asset QC has {len(asset_qc)} data rows, required none')
    if itemization:
        problems.append(f'Itemization has {len(itemization)} data rows, required none')
    if metadata_refs:
        problems.append(f'Metadata References has {len(metadata_refs)} data rows, required none')
    if problems:
        raise RegisterStateError(
            'Clean first-batch validation failed: ' + '; '.join(problems) + '. '
            'Refusing to repair Continuation State or production rows. Inspection was not started.'
        )
    return 'None', 'Batch Queue Prior Open Item'
```

`Orchestrator.run` catches that error before `inspector.inspect` and calls `_block` (`orchestrator.py` around the `Authorized / Pending Inspection` branch). `_block` sets `Blocked - Human Review`, writes `Error / Retry Note`, and writes a diagnostics file when the inspector has a diagnostics object. It does not call `clear` on Asset QC, Itemization, or Metadata References.

`create()` calls `initial_queue_prior` before writing Batch Queue rows. A dirty register raises and does not create the job.

## 5. Runtime prompt and benchmark exclusion

`_runtime_prompt` (`app/inspection.py` lines 13-24) reads `RUNTIME_PROMPT_PATH` when set, otherwise `D:\Integer\HistoryIT\Runtime_prompt_old_chatgpt.md`, otherwise `prompts/Runtime_prompt_old_chatgpt.md`.

The system string is the short schema preamble, that file, the governing documents, then `OUTPUT_SCHEMA`:

```113:121:app/inspection.py
        instruction = (
            'The client runtime prompt below is the canonical inspection workflow. '
            'Return Lean Split v2 Asset Staging and Record Staging in the structured object. '
            'Do not update Google Sheets.\n\n'
            + prompt_text
            + '\n\n'
            + '\n\n'.join(references)
            + '\n\n'
            + OUTPUT_SCHEMA
        )
```

`OUTPUT_SCHEMA` (`inspection.py` lines 9-11) says it does not decide boundaries, types, templates, or readiness. The previous archival `INSPECTION_CONTRACT` is not in the file. A search of `app/inspection.py` found no `INSPECTION_CONTRACT`, `Frozen Benchmark`, `F01-Report`, or `benchmark_spreadsheet`.

`project_references/index.json`:

```json
{
  "required": [],
  "generic": "generic-archival-method.json"
}
```

`project_references/generic-archival-method.json` has `reference_id` `generic-archival-method` and one statement. It has no `groups`, no `filenames`, no record ids, and no statuses.

`resolve_reference` raises `ReferenceError` when a loaded object contains `groups`, a filename-to-decision mapping, or the text `Frozen Benchmark` (`app/references.py` `benchmark_answer_reason` and `_load_reference`). `reference_mismatches` returns that refusal and does not score filename groups. `order_records` returns the model record list unchanged.

`Orchestrator.run` calls `reference_mismatches` before `inspector.inspect`. A benchmark-answer reference becomes `ReferenceError`, which `_block` handles. The model is not called.

The compare endpoint is the only reader of the benchmark spreadsheet id (`app/main.py` lines 97-107). It calls `sheets.rows` twice and `compare_workbooks`. `app/benchmark.py` has no `.write(`.

## 6. Image payload

Original bytes are returned when the file is not TIFF, length is within `original_max_bytes`, and the long edge is within `original_max_edge` (`app/images.py` lines 49-51). Otherwise `thumbnail` preserves aspect ratio. The request object sets detail high:

```97:100:app/inspection.py
                images.append({
                    'type': 'input_image',
                    'image_url': f'data:{mime};base64,{base64.b64encode(data).decode()}',
                    'detail': 'high',
                })
```

`request_prepared` records `image_preparation` as filename, mode, and mime. Bytes are not a log field.

## 7. Tests

Working directory: `D:\Integer\HistoryIT\history-it-project`

```
.\.venv\Scripts\python.exe -m pytest -q
```

Exit code 0. Complete result line:

```
57 passed, 6 subtests passed in 8.68s
```

Failed: 0. The six subtests are the six dirty-register cases inside `test_each_dirty_first_batch_condition_blocks_without_openai`. No traceback.

```
.\.venv\Scripts\python.exe -m pytest -q client_reference/test_commit_engine.py
```

Exit code 0.

```
...                                                                      [100%]
3 passed in 0.08s
```

Client tests pass. Failed: 0. Skipped: 0.

```
.\.venv\Scripts\python.exe -m compileall app client_reference tests
```

Exit code 0. Output:

```
Listing 'app'...
Listing 'client_reference'...
Listing 'tests'...
Compiling 'tests\\test_stage.py'...
Compiling 'tests\\test_workflow.py'...
```

`compileall` wrote bytecode for the two test modules it recompiled.

Covered by these runs:

- first batch Prior Open Item `None` does not invent a record
- stale Continuation State does not override Batch Queue
- blank later-batch prior uses Resulting Open Item, or `None`, and never Continuation State
- next-batch propagation waits for `Committed - Validated`
- each dirty first-register condition blocks, writes a diagnostic, leaves production rows in place, and does not call the inspector
- a reference file with F01 filename groups is refused
- the inspection context for a 21-file model response has no `groups` and no `F01-Report`
- image `detail` is `high`
- benchmark compare reports exact, administrative-only, and semantic outcomes and does not mutate the input dicts
- already validated commit is a no-op
- crash after staging and crash after commit do not duplicate Asset QC rows
- retry writes a diagnostic file
- an existing open record on a later batch is the Batch Queue id, with its Itemization row attached

## 8. Search for the four prohibited patterns

| Pattern | Result |
|---|---|
| Frozen Benchmark data in the OpenAI prompt | Not found in `app/inspection.py`. The words appear in `app/references.py` as a refusal check, and in `app/benchmark.py` / `app/main.py` on the compare path. |
| Exact F01 benchmark grouping in inspection references | `project_references/tri-delta-chapters-f01.json` is deleted. `index.json` `required` is `[]`. The generic file has no groups or filenames. A planted answer file raises `benchmark answer material`. |
| Continuation State fills a blank current-batch Prior Open Item | Not found. Blank later batches use `Resulting Open Item` or `None` (`orchestrator.py` lines 205-206 and 226-227). |
| Dirty register silently accepted | Not found. `assert_clean_first_batch` raises when any of the six conditions fails. |

## 9. Configuration

Names only. `.env` is missing `RUNTIME_PROMPT_PATH`, `PROJECT_REFERENCES_DIR`, and `DIAGNOSTICS_DIR`. Those stay at code defaults: empty prompt path (parent file `Runtime_prompt_old_chatgpt.md`), `./project_references`, and `./logs`.

Present names, values not printed: `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`, `OPENAI_MAX_OUTPUT_TOKENS`, `BATCH_SIZE`, `MAX_RETRIES`, `JOB_SPENDING_LIMIT_USD`, `GOOGLE_APPLICATION_CREDENTIALS`, `BENCHMARK_SPREADSHEET_ID`, `GOVERNING_DOCS_DIR`.

The prompt file used by the loader when `RUNTIME_PROMPT_PATH` is empty is `D:\Integer\HistoryIT\Runtime_prompt_old_chatgpt.md`. The copy inside `history-it-project\prompts\` is still absent. This checkout resolves the parent file.

## 10. Files to check manually before a live run

- The Developer Test register copy. Continuation State `Open Intellectual Item` and `Unresolved Boundary Item` must be `None`. Asset QC, Itemization, and Metadata References must have no data rows. The first Batch Queue row must say Prior Open Item `None`. A register that fails any of those conditions will block and will not call OpenAI.
- `D:\Integer\HistoryIT\Runtime_prompt_old_chatgpt.md` is present, because `.env` does not set `RUNTIME_PROMPT_PATH`.
- `governing/` contains the client packet. The service refuses to start inspection when that directory is empty.
- `project_references/` contains only `index.json` and `generic-archival-method.json`. Do not add the deleted F01 answer file back.
- The process working directory is `history-it-project`, so `./logs` and `./project_references` resolve. A different working directory will not see those defaults.
- The Frozen Benchmark spreadsheet is used only by `POST /api/benchmark/compare` after a completed clean run. Do not treat F01 as accepted until that comparison returns `exact_match` on a fresh register. This session did not run that comparison.

## 11. Final verdict

| Area | PASS/FAIL | Evidence |
|---|---|---|
| Client runtime prompt | PASS | `_runtime_prompt` reads `Runtime_prompt_old_chatgpt.md`. System content is that file, governing documents, and `OUTPUT_SCHEMA`. The archival `INSPECTION_CONTRACT` is gone. `tests/test_stage.py` asserts `INSPECTION PHASE ONLY` and rejects the old second-prompt sentence. |
| Batch Queue prior-item authority | PASS | `initial_queue_prior` returns only `None` or raises. `_queue_prior` and `_authorize_following` never read Continuation State to fill a blank cell. Tests: queue wins, blank later batch, unvalidated previous batch, propagation after commit. |
| Clean first-batch validation | PASS | `assert_clean_first_batch` requires all six conditions. `_block` sets `Blocked - Human Review` and writes a diagnostic. Six subtests passed with zero inspector calls. |
| Benchmark excluded from inspection | PASS | F01 answer file deleted. Generic reference has no groups. `resolve_reference` raises on `groups`. Compare id is used only in `app/main.py`. |
| Image fidelity | PASS | `images.py` returns original bytes inside the limits; otherwise aspect-preserving resize. `inspection.py` line 100 sets `detail` to `high`. `test_governing_tables_are_sent_to_inspection` asserts that field. |
| Deterministic commit unchanged | PASS | `git diff --stat` for `commit.py` and `client_reference/commit_engine.py` was empty. Client tests: 3 passed. |
| Benchmark compare read-only | PASS | `main.py` uses `sheets.rows` only. `compare_workbooks` returns missing, unexpected, value mismatches, counts, and `outcome`. The workflow test checks the input dict is unchanged. |
| Logging and diagnostics | PASS | `Diagnostics.write` path remains `{job}_{batch}_attempt-{attempt}.json` under `./logs` by default. `test_retry_records_the_diagnostic_file` passed. Blocked batches include `[Diagnostic: ...]`. |
| Client tests | PASS | `.\.venv\Scripts\python.exe -m pytest -q client_reference/test_commit_engine.py` exit 0, 3 passed. |
| Full tests | PASS | `.\.venv\Scripts\python.exe -m pytest -q` exit 0, 57 passed, 6 subtests passed, 0 failed. |
| Fresh F01 readiness | FAIL | State-handling tests passed, and no prohibited pattern remains in the inspection path. F01 acceptance is not claimed. No clean Developer Test job was run, and `/api/benchmark/compare` was not executed against the Frozen Benchmark. |
