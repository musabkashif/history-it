# Kristen — new failures and tasks

Recorded 29 Oct 2026. Message dates are not independently established; screenshot clock labels are not treated as event dates.

Engineering investigation (not raw client text): `docs/Kristen_2026-10-29_Investigation.md`. Task rows are linked from `Tasks.md`. Client hypotheses in this file stay unverified until that report says otherwise.

## Source and transcription notes

- Box 35: raw text pasted by the user.
- Gamma Phi Beta: image(20261029-064447).png.
- Raiders: image(20261029-064511).png.
- McCallie: image(20261029-064538).png.
- Client message bodies below are transcribed in visible order. Screenshot line wrapping is normalized. Emphasis is not reproduced. Musa's reply and unrelated earlier acknowledgements are excluded.
- Findings in the raw sections below are client-reported evidence, not independently verified backend diagnoses.
- These client messages do not authorize paid testing, job retries, or register writes. A later, separate authorization covered one replacement cache comparison only; that does not resume general paid testing.

## Raw client text — Box 35 (user-pasted)

I've started the testing since your release and here is the initial finding:
Box 35 regression failed again, but importantly it is a different failure than before.

Fresh run:
- discovery correct: 35 assets / 4 batches
- B0001/F01 committed and validated PASS
- prior open item after B0001: TDC-B35-F01-001
- B0002/F02 inspection failed before staging/commit with:

More than one staging record resolved to TDC-B35-F01-001

There are no B0002 Asset Staging or Record Staging rows and validation did not run, so B0001 remains the authoritative state. I am leaving the job frozen and will not retry.

This looks like the cross-folder closure itself is now being recognized, but the new split-response aggregation is resolving/updating the same prior open record more than once.

The new usage logging also exposed something significant: B0002 generated 9 separate billed response records (Parts 1–9) for the 14-image batch before the inspection failed.

Those nine responses total approximately:
- 249,609 input tokens
- 114,417 cached-input tokens
- 135,165 cache-write tokens
- 20,266 output tokens

B0001 required only one billed response.

Can you check whether each split part is independently emitting the prior-open-record closure/update? It seems that closure should be represented exactly once in the final aggregated record decision.

I’d also like you to look at whether nine billed responses for this 14-image batch is expected. This may help explain some of the higher API spend we were seeing; the old logging would not have made these split calls visible.

## Raw client text — Gamma Phi Beta / root-level scrapbook

Thanks. In the meantime I am going to set up a couple of projects that differ considerably from Tri Delta and see if we hit different kinds of snags. I want to be sure we aren't fine tuning too much for only this type of project.

I set up a separate Gamma Phi Beta Canadian Scrapbooks project to test generalization beyond Tri Delta.

Scrapbook 4 is intentionally structurally different:
- 46 JPEGs directly in the authorized root folder
- no F01/F02 or child source folders
- expected 3 batches at batch size 20

Discovery worked correctly. The app created:
- B0001: 20 assets
- B0002: 20 assets
- B0003: 6 assets

However, the job will not actually start inspection.

When I click Start / continue, the UI says Running for about 20 seconds and then returns to the same Start / continue state. I tried this several times.

The Production Register confirms nothing is actually starting:
- B0001 remains Authorized / Pending Inspection
- Inspection Attempt = 0
- Commit Attempt = 0
- Validation = Not Run
- OpenAI Usage = no calls at all

So this appears to fail between the Start action and launching the inspection worker, before any OpenAI request is made.

Job ID: HIT-4435b42e0c217f99
First batch: HIT-4435b42e0c217f99-B0001

The source service account has Reader access and the Production Register service account access is Writer, so discovery and manifest creation succeeded.

Could you check the backend logs for what happens when Start is invoked on this root-folder processing unit? My concern is that execution may still assume an F-folder/child-folder structure even though discovery now correctly supports root-level assets.

I am holding on any additional processing until this is complete

## Raw client text — Raiders Credentials Boxes

The opening line repeats the preceding screenshot: “I am holding on any additional processing until this is complete”.

I ran a second cross-client test using a completely separate project, Raiders Credentials Boxes, and it exposed another failure in what looks like the same split-response aggregation/state layer.

Source structure is intentionally familiar this time:
- Credentials Binders Box 1
- F01 = 143 images
- F02 = 353 images
- 496 assets / 26 batches

Discovery was completely correct.

B0001/F01 (first 20 assets) launched inspection but failed before staging or commit with:

Update Existing Open Record has no committed row: HIT-ed5fd9772d72a6b9-R0001

Important difference from Box 35: B0001 has Prior Open Item = None, so this is not a cross-folder closure case.

There are no Asset Staging or Record Staging rows for B0001, Commit Attempt = 0, and Validation did not run.

My reading is that one split response created new record R0001, and another split response within the same batch then tried to Update Existing Open Record against that record before it had been committed. The aggregator appears to require an already committed row rather than recognizing a record created earlier within the same aggregated inspection.

This also reproduced the API-call pattern we saw in Box 35. The single 20-image B0001 inspection generated 9 billed OpenAI responses before failing:

- 275,745 input tokens
- 101,704 cached-input tokens
- 174,014 cache-write tokens
- 20,657 output tokens

I am leaving the job frozen and will not retry.

This seems important because Raiders is a completely different client/content corpus, so the split-response/state issue does not appear to be Tri Delta-specific.

## Raw client text — McCallie

Hi Musa. I am trying to run a project in staging because my team urgently needs itemization conducted for two boxes. It seems to be spinning on the setup job function though.

It is a large folder so it may just be taking a while. I'll let you know if it doesn't work

Yeah, never mind. It is just that the folders contain far more assets than previously processed. I'm going to run it now and see what happens.

Well... I wasn't able to process that box for my team. McCallie Box 11 stopped at batch HIT-39d15697ce63fbf7-B0024, F06 images 080–099. It has failed twice before staging or commit.

Attempt 1: IncompleteRead(16777216 bytes read, 4530908 more expected)

Attempt 2: EOF occurred in violation of protocol (_ssl.c:2427)

The first error totals 21,308,124 bytes, which exactly matches McCallie_Year1_Box11_F06_091.jpg.

I independently downloaded that source file from Drive successfully and verified the JPEG: 21,308,124 bytes, 4880×6128, valid image. So this appears to be a download/stream handling problem in the inspection worker rather than a corrupt source file.

B0024 remains Inspection Failed - Retryable, inspection attempt 2, commit attempt 0, validation Not Run, with no Asset Staging or Record Staging written.

## Simple problem summary

1. Box 35: split outputs may close/update the same prior record multiple times; batch aggregation fails.
2. Raiders: a record created inside this batch is treated as though it must already exist in committed Sheets data; aggregation fails.
3. Split-call spending: both failed inspections returned nine billed responses; investigate the split/retry tree and duplicated context.
4. Gamma Phi Beta: discovery supports images directly in the root, but Start apparently never launches inspection.
5. McCallie: a valid large Drive JPEG fails mid-transfer/SSL; inspect transport handling and safe recovery.
6. Generalization: fixes must work across projects, without Tri Delta-specific patches.

## Task register — investigation first

Statuses after the 29 Oct investigation and local offline fixes: see rows below. Fixes are local only (not committed/deployed; frozen jobs not retried). Offline coverage: `docs/Kristen_2026-10-29_Investigation.md`, `server/tests/test_client_2026_10_29_failures.py`, `server/tests/test_provider_split_safety.py`, and related.

| ID | Priority | Task | Status | Acceptance target |
|---|---|---|---|---|
| N01 | P0 | Preserve frozen-job evidence and identify exact deployed SHA, job/register identities, batch diagnostics, and response IDs. | Investigating — staging `ee1f7d4`; jobs `HIT-a915dbc3c4ab3d8a`, `HIT-ed5fd9772d72a6b9`, `HIT-4435b42e0c217f99`, `HIT-39d15697ce63fbf7` frozen. | No source/register mutation, job retry, or loss of last validated state. |
| N02 | P0 | Trace Box 35 split-state aggregation and prior-record identity resolution. | Offline-tested locally — merge collapses duplicate prior Updates; `id`/`id~N` coalesce only onto the same committed prior; Box 37 tests still pass. Not committed/deployed; frozen job not retried. Live second F01 emission still not listed in diagnostics. | One coherent prior-record closure/update in the final aggregate; preserve kind/template/type and Review/incomplete status; conflicts remain visible. |
| N03 | P0 | Trace Raiders within-batch create/update dependency handling. | Offline-tested locally — Create-then-Update across split parts stays Create New; cross-batch Update still requires a committed Sheets row. Not committed/deployed; frozen job not retried. | New records can continue across split parts without requiring a committed row; final aggregate has consistent identity and create semantics. |
| N04 | P0 | Reconstruct the nine-response request trees for both failed batches. | Offline-tested locally — explained as 9 billed leaves + 8 unbilled HTTP parent failures (not job retries); `inspect_resilient` now records parent/child node ids, depth, HTTP status, billed vs unbilled, response ids, and token usage when known. Not committed/deployed; frozen jobs not retried. OpenAI-side charges for empty `response_id` parents remain unknown. | Explain initial failures, split depth, part order, retries, usage, and repeated context; account for each provider response exactly once. |
| N05 | P0 | Investigate Gamma root-level Start-to-worker path. | Offline-tested locally — `folder_code` returns `ROOT` for root-only manifests; Start/run catches `CommitError` and persists `Blocked - Human Review` with the reason before In Progress. Not committed/deployed; frozen Gamma job not retried. | A 46-image root-only manifest launches 20/20/6 processing without requiring F folders; failures are visible, not a silent idle transition. |
| N06 | P0 | Investigate McCallie partial-download/SSL failure. | Offline-tested locally — Drive download retries IncompleteRead/SSL EOF (3 attempts, 1–4s backoff); size mismatch rejected; filename in diagnostics; incomplete bytes never sent to the model. Not committed/deployed; frozen McCallie job not retried. | Verify reported size/hash when available; reject incomplete bytes; recover transport safely before paid inspection and avoid rerunning validated batches. |
| N07 | P1 | Add representative no-cost regression coverage after diagnosis. | Offline-tested locally for split safety (`tests/test_provider_split_safety.py`) plus prior Gamma/Raiders/Box 35/McCallie fixtures. Broader acceptance still pending. | Root-only and F-folder projects, duplicate closure, within-batch continuation, split failure handling, and partial transfers are covered. |
| N08 | P1 | Define safe split amplification and spend controls after the request-tree audit. | Offline-tested locally — max depth 6, min images to split 2; exhaustion stops without staging; quality settings unchanged; diagnostics distinguish unsuccessful parents, billed leaves, app retries, provider retries, and child splits. Not committed/deployed. | Bound unnecessary calls, expose reason and uncertainty, preserve quality and recovery; no arbitrary normal-job cap introduced. |
| N09 | P1 | Improve operational failure visibility where evidence shows gaps. | Reported / investigating. | Distinguish start failure, download failure, provider failure, aggregation failure, staging failure, and commit failure. |
| N10 | P1 | Update Tasks.md and client issue notes with evidence-linked statuses. | Investigating — this pass. | Separate reported, reproduced, implemented, offline-tested, deployed, and client-accepted states. |
| N11 | P1 | Prepare targeted acceptance/recovery plan for frozen jobs. | Reported / investigating — unexecuted. | Remains unexecuted until authorized; protects Box 35 B0001 and McCallie B0023/last validated checkpoint, confirmed from register. |

## Important uncertainties

- Duplicate prior-record updates and within-batch create/update handling are client hypotheses until logs confirm them.
- Nine billed responses do not alone establish nine retries; distinguish split parts, successful responses, failed requests, and retries.
- Generalization across two corpora suggests a shared implementation issue but does not identify its exact code location.
- Client token numbers are reported totals; do not derive an invoice or silently reclassify their categories.
- McCallie setup slowness was explicitly retracted as a failure; the actionable issue is B0024's transfer error.
- The current test freeze and lack of replacement-comparison approval remain in effect. Read-only investigation and offline tests can proceed.
