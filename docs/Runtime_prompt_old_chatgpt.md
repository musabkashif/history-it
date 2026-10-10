# HistoryIT Archive Itemization & Imaging QC
## Runtime Inspection Prompt — Lean Split v2



Resume the project from the **authoritative Production Register**.



The Production Register is authoritative persistent state. Do not rely on prior chat history where it conflicts with, duplicates, or is superseded by the register.



This run is **INSPECTION PHASE ONLY using Lean Split v2**.



**Do not commit production state.**



### 1. Recover current state



Read Run Context!A1:E63 first.



Then read only the minimum additional Production Register detail required to process the current batch safely, including any detail required for:



- duplicate prevention;
- an existing open intellectual item;
- applicable project classification rules; or
- current batch authorization.



Do not reread large portions of the register unnecessarily.



### 2. Identify the authorized batch



Locate the **single Batch Queue row with Batch Status = Pending Authorization**.



There must be exactly one such batch.



If there are zero or more than one Pending Authorization batches, **STOP and report the condition. Do not choose a batch yourself.**



The matching Batch Queue row defines the authorized processing scope.



### 3. Use the exact filename manifest



Process **only** the source files explicitly authorized for that Batch ID.



Use the exact filename manifest recorded in the Batch Queue authorization/Notes.



Do not infer authorization from numeric filename ranges.



Do not process neighboring files, related files, files from another folder, ZIP files, or files that merely appear logically connected unless they are explicitly present in the authorized manifest.



Preserve exact filenames, including suffixes such as _01, _02, _a, _b, or other compound filename elements.



Every authorized filename must be reconciled exactly once.



### 4. Read Prior Open Item



Read the Prior Open Item value from the Batch Queue row.



If Prior Open Item = None, do not invent or carry forward an item from prior chat context.



If a Prior Open Item exists:



- read only the authoritative rows necessary to understand that existing record;
- preserve its existing record identity and already committed asset list;
- determine from the newly inspected visual evidence whether the item continues, closes, or transitions within this batch;
- distinguish **new batch assets** from **existing committed assets**; and
- never duplicate already committed assets.



A technical batch boundary does not force an intellectual-item boundary.



A folder boundary does not automatically force closure unless the governing workflow and visual/context evidence support closure.



### 5. Inspect the authorized images



Visually inspect **every newly authorized source image individually**.



Do not use sampling or assume that visually similar neighboring files are identical.



Apply:



- HistoryIT_Archive_Itemization_Imaging_QC_Master_Instructions_v4.2_Staging_Orchestration_Workflow;
- the Odyssey Template Reference when Template/Type guidance is required;
- the Cropping Reference when crop/QC rules are required; and
- the Scrapbook Cropping Reference only when applicable.



For each source asset and intellectual record, determine the applicable:



- intellectual-item grouping and boundaries;
- Normal Odyssey vs. Metadata Only status;
- Template and Type;
- asset role;
- crop standard;
- QC status and required action;
- expected output asset count;
- metadata readiness;
- confidence/review state;
- production-task requirement;
- parent/child relationship when applicable; and
- resulting open/closed boundary state.



Do not create descriptive metadata outside the governing scope.



### 6. Write Lean Split v2 staging only



Write inspection results only to:



1. Asset Staging
2. Record Staging
3. the matching Batch Queue control row



Do **not** write to or modify authoritative production-state tabs during this inspection phase.



In particular, do not perform the deterministic commit and do not update production records in:



- Project Register;
- Box Summary;
- Folder Processing Unit Summary;
- Itemization;
- Asset QC;
- Production Queue;
- Ready for Metadata;
- Metadata References;
- Continuation State;
- Run Context; or
- Validation.



#### Asset Staging



Write **exactly one Asset Staging row per newly inspected authorized source asset**.



Each row must conform to the current Lean Split v2 Asset Staging schema.



#### Record Staging



Write **exactly one Record Staging row per intellectual Odyssey record or Metadata Only reference touched by the batch**.



For an existing open record, explicitly distinguish:



- new batch assets;
- existing committed assets; and
- the complete asset list after proposed commit.



Each row must conform to the current Lean Split v2 Record Staging schema.



### 7. Reconcile before declaring inspection complete



Before changing the Batch Queue status, read back the staging rows and confirm:



- every authorized filename is present exactly once in Asset Staging;
- no unauthorized filename is present;
- Asset Staging count equals the authorized/planned source count;
- exact filenames have been preserved;
- every staged asset is assigned to an appropriate staged or supported existing record;
- Record Staging contains every intellectual record or Metadata Only reference touched;
- existing committed assets have not been duplicated;
- open-item state is explicit; and
- all required staging rows show inspection complete.



If reconciliation fails, **STOP / FLAG the batch**.



Do not attempt to repair a reconciliation problem by improvising, expanding scope, or silently changing archival decisions.



### 8. Complete the inspection phase



Only after successful staging reconciliation:



- set the matching Batch Queue row to
*Inspection Complete - Awaiting Commit**;
- record the resulting open item;
- record the Asset Staging range;
- record the Record Staging range;
- record the inspection attempt/date and any appropriate inspection note.



Do **not** run the deterministic commit.



Do **not** advance the next batch into production state.



### 9. Stop conditions



Stop and report rather than improvise if any of the following occurs:



- zero or multiple Pending Authorization batches;
- authorized manifest cannot be determined exactly;
- source files are missing or cannot be accessed;
- an authorized filename appears to have already been processed contrary to expected state;
- duplicate or extra source assets are encountered;
- staging counts do not reconcile;
- required existing-open-item state cannot be determined;
- a required governing reference cannot be applied confidently;
- the material requires human Review under the governing instructions; or
- any other reconciliation or state conflict prevents a safe staging result.



### 10. Report completion



At the end of the inspection run, report:



- Project / Batch ID;
- authorized source count;
- source filenames reconciled;
- intellectual records created or updated;
- Metadata Only references created or updated;
- key QC/actions identified;
- Ready vs. Blocked/Review outcomes;
- production tasks identified;
- resulting open item;
- whether the authorized batch/folder scope is exhausted;
- Asset Staging row range;
- Record Staging row range; and
- final Batch Queue status.



The expected successful endpoint for this worker is:



*Inspection Complete - Awaiting Commit**



The deterministic commit and validation phase occurs separately.