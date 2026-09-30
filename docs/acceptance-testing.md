# Acceptance testing

1. Create a clean copy of `Tri Delta Chapters Production Register - Developer Test` and share it with the service account.
2. Put the client governing packet in `GOVERNING_DOCS_DIR`.
3. Create a small `HistoryIT Jobs` tab and an `OpenAI Usage` tab (the service can create these tabs before job creation if your Sheets adapter is extended).
4. Set `GOOGLE_APPLICATION_CREDENTIALS`, `JOB_INDEX_SPREADSHEET_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, pricing and limits.
5. `uvicorn app.main:app --reload`; create a job with Box 19/F01 folder ID and start it.
6. Monitor `/api/jobs/{id}`, `/batches`, `/usage`, and `/validation`. Interrupt the process after staging or after commit writes, restart, and verify no duplicate rows.
7. Run `/api/benchmark/compare` against the read-only Frozen Benchmark. Expected completed Box 19 totals are 293 assets, 91 Odyssey records, 6 Metadata Only, 79 Ready, 12 Blocked-Cropping/QC, 280 logical components and 77 production tasks; the endpoint compares detail keys and counts rather than hardcoding them.
8. After Box 19 is validated, use the Box 30 folder and a new job.

No real Google/OpenAI acceptance execution was possible without the client's credentials and missing governing packet.
