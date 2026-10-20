# MVP requirements mapping

| Acceptance requirement | Implementation | Tests/status |
|---|---|---|
| Exact one-time Drive manifest, hierarchy, filenames | `app/adapters.py:Drive.enumerate`; immutable JSON manifest in Batch Queue; `authorized_manifest` refuses filename-range inference | `tests/test_workflow.py` |
| Bounded queue and lifecycle | `app/orchestrator.py` | Queue lifecycle exercised with mocked Sheets/Drive |
| Isolated OpenAI structured inspection | `app/inspection.py`; canonical `Runtime_prompt_old_chatgpt.md`; Pydantic `InspectionOutput`; model does not write Sheets | Mock OpenAI plus runtime-prompt assertion |
| Lean Split v2 staging | `app/models.py`, `_stage` | Identity, count, status readback checks |
| Deterministic commit | `app/commit.py`, unchanged `client_reference/commit_engine.py` | Original 3 client tests pass |
| 15 validation checks | `CommitService.validate` | Validation rows written; live register test required |
| Recovery/idempotency | pending-validation recovery, upsert conflict detection, committed no-op | Real register crash simulation required |
| Continuation state | Commit updates Continuation State only after a successful commit. The current batch prior item is Batch Queue `Prior Open Item`. | `tests/test_workflow.py` |
| Usage/cost/limits | `OpenAI Usage` writes and settings | Requires live/mocked API response |
| OpenAI credit shortage stop and alert | `app/credits.py`, `Inspector`, `Orchestrator.run` / `resume`; `alert` on job, batches, and usage | `tests/test_credits.py` |
| REST API | `app/main.py` | OpenAPI available at `/docs` |
| Benchmark comparison | `/api/benchmark/compare` read-only final Box 19 row diff with outcome `exact_match`, `deterministic_internal_consistency_only`, or `semantic_benchmark_mismatch` | `tests/test_workflow.py` |
| F01 intermediate checkpoint | `/api/benchmark/f01-checkpoint` reads the developer register only; outcomes `f01_intermediate_match` and `f01_intermediate_mismatch` | `tests/test_workflow.py` |
| Next authorized folder | `POST /api/jobs/{job_id}/folders` enumerates one folder onto the existing job and does not start inspection | `tests/test_workflow.py` |
| Frozen benchmark | read only by the compare endpoint; F01 calibration is a labeled example, not a workbook extract | No benchmark write path |
| F01 calibration | `project_references/tri-delta-chapters-f01-calibration.json` version `2026-10-07.1`, loaded for F01 and F02 only; contradictions stop before commit | `tests/test_workflow.py` |
| Box 31 F04 calibration | `project_references/tri-delta-chapters-box31-f04-calibration.json` version `2026-10-20.1`, loaded for Tri Delta Chapters / Chapters Box 31 / F04 only; `_004`/`_005` guidance, Photograph 009 stays a normal record; a combined record stops before commit | `tests/test_workflow.py` |

## Live-only material
The four governing packet documents are included in `governing/` and are copied by the updater when missing. Google Drive/Sheets IDs, service-account credentials, OpenAI credentials, and a live Developer Test register are not available in this build environment, so live Box 19 execution and benchmark reconciliation remain deployment gates. No archival rules were invented to replace client material.
