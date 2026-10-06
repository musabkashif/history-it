# MVP requirements mapping

| Acceptance requirement | Implementation | Tests/status |
|---|---|---|
| Exact one-time Drive manifest, hierarchy, filenames | `app/adapters.py:Drive.enumerate`; immutable JSON manifest in Batch Queue | Drive integration test requires credentials; filename/order checks in orchestrator |
| Bounded queue and lifecycle | `app/orchestrator.py` | Queue lifecycle exercised with mocked Sheets/Drive |
| Isolated OpenAI structured inspection | `app/inspection.py:Inspector`; Pydantic `InspectionOutput` | Mock OpenAI required for integration |
| Lean Split v2 staging | `app/models.py`, `_stage` | Identity, count, status readback checks |
| Deterministic commit | `app/commit.py`, unchanged `client_reference/commit_engine.py` | Original 3 client tests pass |
| 15 validation checks | `CommitService.validate` | Validation rows written; live register test required |
| Recovery/idempotency | pending-validation recovery, upsert conflict detection, committed no-op | Real register crash simulation required |
| Continuation state | summary/checkpoint writes in `_summaries` | Open item carried by register field |
| Usage/cost/limits | `OpenAI Usage` writes and settings | Requires live/mocked API response |
| OpenAI credit shortage stop and alert | `app/credits.py`, `Inspector`, `Orchestrator.run` / `resume`; `alert` on job, batches, and usage | `tests/test_credits.py` |
| REST API | `app/main.py` | OpenAPI available at `/docs` |
| Benchmark comparison | `/api/benchmark/compare` read-only key diff | Requires both register IDs |
| Frozen benchmark | only read in compare endpoint | No benchmark write path |

## Live-only material
The four governing packet documents are included in `governing/` and are copied by the updater when missing. Google Drive/Sheets IDs, service-account credentials, OpenAI credentials, and a live Developer Test register are not available in this build environment, so live Box 19 execution and benchmark reconciliation remain deployment gates. No archival rules were invented to replace client material.
