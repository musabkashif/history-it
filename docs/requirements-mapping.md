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
| REST API | `app/main.py` | OpenAPI available at `/docs` |
| Benchmark comparison | `/api/benchmark/compare` read-only key diff | Requires both register IDs |
| Frozen benchmark | only read in compare endpoint | No benchmark write path |

## Unavailable supplied material
The four governing packet documents listed by the client were not present in the upload: master orchestration instructions, Odyssey template reference, cropping reference, scrapbook cropping reference, and the zipped developer reference. The service therefore requires those documents in `GOVERNING_DOCS_DIR` before a live inspection job can run. No archival rules were invented to replace them.
