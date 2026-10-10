# HistoryIT Deterministic Commit Engine v0.1

## Purpose

The commit engine converts completed `Asset Staging` + `Record Staging` rows into the authoritative Production Register **without any model reasoning and without any source-image or source-folder access**.

The engine is the mechanical half of the scalable workflow:

`folder job -> queued micro-batch -> image inspection worker -> staging -> deterministic commit -> deterministic validation -> next batch`

The LLM/image worker owns judgment. The commit engine owns state mutation, counts, reconciliation, validation, retries, and queue advancement.

## Hard invariant

The commit engine must have **no source-image or source-folder connector**. Its only inputs are:

1. Production Register state.
2. `Batch Queue` row.
3. `Asset Staging` rows for the Batch ID.
4. `Record Staging` rows for the Batch ID.
5. A folder/job manifest created by the orchestration layer before inspection begins.

This prevents the commit phase from silently reinterpreting image evidence.

## Required batch state

A batch is eligible for deterministic commit only when:

- `Batch Status = Inspection Complete - Awaiting Commit`
- `Inspection Attempt >= 1`
- `Validation Status = Not Run`
- every staged row has `Inspection Complete = Yes`
- every staged row has `Commit Status = Pending Commit`
- Asset Staging count equals `Planned Asset Count`
- source filenames are unique within the staged batch
- no staged source filename already exists in authoritative `Asset QC`
- every Asset Staging `Assigned Record ID` exists in Record Staging or is an explicitly supported existing record being updated

If any gate fails, the engine writes no production-state changes.

## Lean Split v2 staging contract

### Asset Staging

Exactly one row per newly inspected source asset.

Required fields:

- Project
- Job ID
- Batch ID
- Processing Unit / Source Folder
- Asset Sequence
- Digital Asset
- Box
- Source Group / Folder
- Assigned Record ID
- Asset Role
- Required Crop Standard
- QC Status
- Action Needed
- Expected Output Asset Count
- QC Confidence
- QC Note
- Odyssey Ingest / Visibility Note
- Production Queue Eligible
- Production Task Scope
- Production Task Note
- Exception / Duplicate Note
- Inspection Complete
- Commit Status
- Commit Note

### Record Staging

Exactly one row per intellectual record or Metadata Only reference touched by the batch.

Required fields:

- Project
- Job ID
- Batch ID
- Processing Unit / Source Folder
- Record Sequence
- Proposed Record ID
- Record Kind
- Record Operation
- Box
- Source Group / Folder
- Parent Scrapbook
- Parent Scrapbook Page
- New Batch Assets
- Existing Committed Assets
- Full Record Assets After Commit
- Digital Asset Count
- Logical Component Count
- Template
- Type
- Reference Role
- Further Detail
- Required Crop Standard
- Record QC Status
- Record Action Needed
- Metadata Readiness
- Confidence
- Review Note
- Production Task Count
- Odyssey Ingest / Visibility Note
- Notes
- Boundary State After Record
- Inspection Complete
- Commit Status
- Commit Note

Supported `Record Kind` values initially:

- `Normal Odyssey`
- `Metadata Only`

Supported `Record Operation` values initially:

- `Create New`
- `Update Existing Open Record`

Unknown values are blocking errors, never guessed.

## Deterministic transformations

### Asset Staging -> Asset QC

One authoritative `Asset QC` row is generated for every staged asset.

| Asset Staging | Asset QC |
| --- | --- |
| Project | Project |
| Processing Unit / Source Folder | Processing Unit / Source Folder |
| Digital Asset | Digital Asset |
| Box | Box |
| Source Group / Folder | Source Group / Folder |
| Assigned Record ID | Assigned Record ID |
| Record Staging Template* | Template |
| Record Staging Type* | Type |
| Asset Role | Asset Role |
| Required Crop Standard | Required Crop Standard |
| QC Status | QC Status |
| Action Needed | Action Needed |
| Expected Output Asset Count | Expected Output Asset Count |
| QC Confidence | QC Confidence |
| QC Note | QC Note |
| Odyssey Ingest / Visibility Note | Odyssey Ingest / Visibility Note |
| Production Queue Eligible | Production Queue Eligible |
| Exception / Duplicate Note | Exception / Duplicate Note |

`*` For a Metadata Only reference, use `Template = Metadata Only` and blank Type unless Record Staging supplies a valid type.

### Record Staging -> Itemization

For `Record Kind = Normal Odyssey`:

- `Create New`: append one Itemization row.
- `Update Existing Open Record`: locate the unique current Itemization row by Item ID and update it in place.

Use `Full Record Assets After Commit`, never only `New Batch Assets`, as the canonical record asset list.

### Record Staging -> Metadata References

For `Record Kind = Metadata Only`:

- `Create New`: append one Metadata References row.
- `Update Existing Open Record`: update the unique existing reference row.

Metadata Only rows never create normal Odyssey Itemization rows.

### Asset Staging -> Production Queue

For every asset where `Production Queue Eligible = Yes`:

- create exactly one production task unless the staging schema explicitly supports a multi-task asset in a later version;
- Task ID is generated deterministically from the folder prefix and next available integer sequence;
- Assigned Record ID comes from Asset Staging;
- Template/Type come from the corresponding Record Staging row;
- Action Needed, Task Scope, Expected Output Asset Count, QC Confidence, and Production Note come from staging.

No queue row is created for `Production Queue Eligible = No`.

### Record Staging -> Ready for Metadata

For `Record Kind = Normal Odyssey` and `Metadata Readiness = Ready`:

- ensure exactly one Ready for Metadata row exists for the Item ID;
- append if absent;
- update if an existing Ready row must reflect a completed asset list;
- never create a Ready row for Blocked or Review records.

### Record readiness transitions

The engine does not infer readiness. It applies the staged value exactly.

Supported initial values:

- Ready
- Blocked - Cropping/QC
- Blocked - Additional Imaging
- Review

Unknown readiness values block commit.

## Canonical cumulative metrics

After detail writes, cumulative project metrics are recalculated from authoritative detail tables rather than incremented from memory:

- Source Assets = unique Digital Asset filenames in Asset QC
- Odyssey Metadata Records = unique Item IDs in Itemization
- Metadata Only References = unique Reference IDs in Metadata References
- Ready Records = unique Item IDs in Ready for Metadata
- Blocked - Cropping/QC = Itemization rows whose Metadata Readiness equals that value
- Blocked - Additional Imaging = same rule
- Review Records = Itemization rows whose Metadata Readiness is Review
- Content-Linked Assets = sum of Itemization Digital Asset Count
- Logical Components = sum of Itemization Logical Component Count
- Production Tasks = open Production Queue task count
- Crop/QC Tasks = open tasks whose action is not Additional Imaging
- Additional Imaging Tasks = open tasks whose action is Additional Imaging

These values populate the next cumulative Project Register row and the active Box Summary row.

## Folder/micro-batch metrics

`Folder Processing Unit Summary` is derived from the committed batch itself:

- Enumerated New Source Count = planned asset count
- Processed New Source Count = committed staged asset count
- Asset QC Rows = committed staged asset count
- Odyssey Metadata Records = count of new Normal Odyssey records created in this batch
- Metadata Only References = count of new Metadata Only references created in this batch
- Ready Records = count of records newly transitioning to Ready in this batch
- Blocked Records = count of records newly committed or updated to a Blocked readiness state
- Production Tasks = new task rows generated by this batch
- Duplicate Exceptions = count of staged duplicate exceptions
- Open Item = resulting open intellectual item or None

## Folder completion

The commit engine must not infer folder completion from a filename pattern alone.

The orchestration layer should enumerate the authorized folder once at job creation and create either:

1. all Batch Queue rows up front, or
2. an immutable job manifest with ordered filenames and total count.

A folder can be marked `Completed - Validated` only when:

- every manifest source asset is represented exactly once in committed Asset QC rows for that folder;
- every Batch Queue row for the job is `Committed - Validated`;
- there is no unresolved intellectual item at the folder boundary;
- all folder-level validation checks pass.

Otherwise the folder remains In Progress.

## Continuation State and Run Context

After detail-table writes and metric recalculation, update Continuation State deterministically from:

- active job/batch
- last committed source filename
- resulting open intellectual item
- resulting boundary state
- cumulative open-task summary
- next queued source filename or next folder authorization state

Run Context remains derivative and should recalculate from authoritative tabs/formulas.

## Validation gates

The initial engine implements the 15 production checks already proven in F04/F05:

1. Source reconciliation
2. Asset QC one-row-per-new-source
3. Duplicate prevention
4. Prior completed work protection
5. Scope / ZIP exclusion at commit boundary
6. Filename integrity
7. Itemization asset-count integrity
8. Normal Odyssey record reconciliation
9. Metadata Reference reconciliation
10. Production Queue reconciliation
11. Ready for Metadata reconciliation
12. Cumulative totals reconciliation
13. Carry-forward task preservation
14. Continuation State correctness
15. Commit gate / readback reconciliation

All 15 must PASS before queue closeout.

## Idempotency and retry behavior

### Already committed batch

If Batch Queue says `Committed - Validated`, return success/no-op after verifying the recorded production state. Never append the batch again.

### Partial commit

The engine uses three states:

- `Inspection Complete - Awaiting Commit`
- `Commit Written - Pending Validation`
- `Committed - Validated`

If a process stops after production writes but before validation, rerun in recovery mode:

- compare expected deterministic rows with actual production rows;
- write only missing deterministic rows;
- never duplicate matching rows;
- if conflicting rows exist, stop and surface a conflict rather than overwriting silently.

### Atomicity strategy

Within one sheet mutation pass, batch related Google Sheets requests together. Because a multi-tab commit cannot be made transactionally atomic across arbitrary external failures, idempotency and readback are mandatory.

## Queue lifecycle

Recommended status lifecycle:

- Pending Authorization
- Authorized / Pending Inspection
- Inspection In Progress
- Inspection Complete - Awaiting Commit
- Commit Written - Pending Validation
- Committed - Validated
- Inspection Failed - Retryable
- Commit Failed - Retryable
- Blocked - Human Review

## Large-folder orchestration

For hundreds or thousands of images:

1. User authorizes one folder/job.
2. Orchestrator enumerates the folder once and stores an immutable ordered manifest.
3. Orchestrator creates bounded Batch Queue work units (for example 10-25 images each; tuning remains empirical).
4. Each image-analysis worker runs in a fresh isolated context and writes only staging.
5. Commit engine runs automatically with no LLM.
6. Queue advances to the next batch.
7. Open intellectual-item context is carried through structured state, not conversation history.
8. At final batch, folder-level reconciliation closes the folder only if manifest and boundary checks pass.

## Service interface

A production service can expose:

```text
commit_batch(spreadsheet_id, batch_id, commit_date) -> CommitResult
```

`CommitResult` should contain:

- batch_id
- status
- rows_written_by_sheet
- rows_updated_by_sheet
- cumulative_metrics
- open_item
- next_batch_start
- validation_results
- conflicts
- idempotent_noop

## Security / operational controls

- Commit service needs Google Sheets write access only.
- It should not receive Drive image-read permissions.
- Log Batch ID, Job ID, target spreadsheet ID, row IDs/ranges, and validation outcome.
- Never log source-image bytes.
- Never delete staging history automatically.
- Preserve the prior committed state on conflict.

## Acceptance fixture: F05-B01

The reference implementation includes an offline acceptance test based on the proven F05-B01 staging result.

Expected deterministic result:

- 10 new Asset QC rows
- 1 new normal Odyssey record
- 1 new Metadata Only reference
- 9 new Crop tasks
- 0 Ready rows
- open item `TDC-B19-F05-001`
- cumulative project totals after commit:
  - Source Assets: 160
  - Odyssey Metadata Records: 56
  - Metadata Only References: 5
  - Ready Records: 51
  - Blocked - Cropping/QC: 5
  - Blocked - Additional Imaging: 0
  - Review Records: 0
  - Content-Linked Assets: 154
  - Logical Components: 150
  - Production Tasks: 17

