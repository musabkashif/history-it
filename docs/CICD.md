# HistoryIT CI/CD

Workflow files and the host script are in the infrastructure commit. A push of that commit runs CI. No deployment in this change replaced containers on either host. `STAGING_AUTODEPLOY_ENABLED` is not set, so a staging CI completion does not select the self-hosted runner.

Safe unattended deployment is not enabled. Both running processes admit work only in memory. `/health` does not report live inspections, commits, or validation. The deploy script fails closed unless the running process grants `historyit.deployment-admission.v1`.

## Workflows

| File | Trigger | Runner |
|---|---|---|
| `.github/workflows/ci.yml` | push and pull request to `master` or `staging` | `ubuntu-24.04` only |
| `.github/workflows/deploy-staging.yml` | `workflow_run` after CI completes on `staging`, and `workflow_dispatch` with the `deploy` choice | gate on `ubuntu-24.04`; activation on `[self-hosted, linux, x64, historyit-staging]` |
| `.github/workflows/deploy-production.yml` | `workflow_dispatch` with the `deploy` choice, `master` only | gate on `ubuntu-24.04`; activation on `[self-hosted, linux, x64, historyit-prod]` |

CI installs `server/requirements.txt` on Python 3.12 and runs `pytest`. The frontend job runs `npm ci` and `npm run build` (TypeScript plus the production bundle) on Node 22. Nothing in CI reads a production `.env`, Google credentials, or calls OpenAI.

Automatic staging deployment starts from `workflow_run` when the CI workflow completes. GitHub reads that workflow file from the default branch (`master`). The self-hosted job is skipped unless all of these are true: this repository is `musabkashif/history-it`, the CI event is `push` (not a pull request), the conclusion is `success`, the head branch is `staging`, the head repository is this repository and is not a fork, and the SHA is the CI run's `head_sha`. A pull request or fork CI run does not match that condition, so it is not assigned to either self-hosted runner.

The repository variable `STAGING_AUTODEPLOY_ENABLED` must be the string `true` before that automatic job runs. An absent value, or any other value, skips the automatic gate and the self-hosted job. The variable is not a credential and must not contain a token, password, or key. This change does not set it.

Manual staging deployment is `workflow_dispatch` on `refs/heads/staging` with `confirm=deploy`. It does not require `STAGING_AUTODEPLOY_ENABLED`. It still waits for a successful CI run of that same SHA and then uses the same host checks. Production deployment is `workflow_dispatch` on `refs/heads/master` only. A push to `master` runs CI and does not deploy.

The host script fetches `origin/<branch>` and continues only when the SHA is still the branch tip. A newer queued or in-progress deploy run for the same workflow is rejected before `docker compose up`. An activation that has already started is not cancelled: both deploy workflows set `cancel-in-progress: false`, and each environment has its own concurrency group. A second deploy waits on the host lock instead of replacing containers in the middle of the first activation.

`workflow_dispatch` has one choice input, `confirm=deploy`. The host script does not receive a path, host, or command input. Automatic deployment uses `github.event.workflow_run.head_sha`. Manual deployment uses `github.sha` of the selected branch.

Permissions on every workflow are `contents: read`. Deploy workflows also have `actions: read` so they can see CI and newer deploy runs. No GitHub secret is required.

Third-party actions are pinned to commits from the official `actions` repositories:

- `actions/checkout` `3d3c42e5aac5ba805825da76410c181273ba90b1` (v7.0.1)
- `actions/setup-python` `5fda3b95a4ea91299a34e894583c3862153e4b97` (v7.0.0)
- `actions/setup-node` `249970729cb0ef3589644e2896645e5dc5ba9c38` (v6.5.0)

## Hosts

| | Production | Staging |
|---|---|---|
| Branch | `master` | `staging` |
| Hostname | `ip-172-31-5-212` | `ip-172-31-6-193` |
| Public HTTPS | `https://13.62.111.185` | `https://56.228.63.165` |
| Checkout | `/home/ubuntu/history-it` | `/home/ubuntu/history-it-staging` |
| Compose project | `history-it` | `historyit-staging` |
| Runner service | `/home/ubuntu/actions-runner` | `/home/ubuntu/actions-runner` |
| Runner labels | `self-hosted`, `linux`, `x64`, `historyit-prod` | `self-hosted`, `linux`, `x64`, `historyit-staging` |

The script refuses to run when `hostname` does not match the profile. A staging runner invoked with `HISTORYIT_ENV=production` stops before git or Compose. Build uses Compose project `<project>-candidate` from a detached worktree under `/home/ubuntu/historyit-release-builds/<environment>/<sha>`. Activation uses the live project, `--env-file` pointed at the live checkout `.env`, and `--no-build`.

Images are tagged `historyit-api:cand-<sha>` and `historyit-web:cand-<sha>`. The previous container image ids are tagged `historyit-api:keep-<previous sha>` and `historyit-web:keep-<previous sha>` before the build. The build is refused if those keep tags change. `docker-compose.prod.yml` still names `historyit-api:prod` and `historyit-web:prod` for a manual start; the deploy override replaces the image for that activation only.

State, including the deployed SHA and the rollback tags, is written outside the checkout:

```text
/home/ubuntu/historyit-deploy-state/production/release.json
/home/ubuntu/historyit-deploy-state/production/deployed-sha
/home/ubuntu/historyit-deploy-state/staging/release.json
/home/ubuntu/historyit-deploy-state/staging/deployed-sha
```

The script does not delete `.env`, `server/credentials`, `server/governing`, `server/logs`, certificate volumes, or the renewal cron. It does not run `git reset --hard`, `git clean`, `git push`, `docker compose down`, volume deletion, or `docker system prune`. A dirty checkout (anything `git status --porcelain` prints; ignored secrets do not) stops the deploy.

HTTPS is checked with curl's default certificate verification. Smoke checks are `GET /health` (`login_configured` true), `GET /` (200), and `GET /api/jobs/HIT-DEPLOY-PROBE` (401). They do not start an inspection or write a register. The candidate `docker-compose.prod.yml` and `deploy/nginx/https.conf` must still mount the existing certificate volume and the HistoryIT certificate paths.

## One-time host setup

Runners, Compose v5, certificate volumes, and renewal cron are already installed. On each host, as `ubuntu`:

```bash
hostname
python3 --version
sudo -n docker compose version
git -C /home/ubuntu/history-it rev-parse --abbrev-ref HEAD
git -C /home/ubuntu/history-it-staging rev-parse --abbrev-ref HEAD
```

Use the checkout path that exists on that host. `hostname` must be `ip-172-31-5-212` or `ip-172-31-6-193`. `python3` must be 3.10 or newer and does not need project packages. `sudo -n docker compose` must succeed. The checkout's `origin` must already be able to fetch; do not put a deploy key or `.env` in GitHub.

Confirm the runner service is the one under `/home/ubuntu/actions-runner` and that its labels include the environment label above. No further runner install is required for this change.

## GitHub configuration

Repository: `musabkashif/history-it`.

No Actions secrets are required. Do not upload the production SSH key, `.env`, Google credential, or OpenAI key.

One repository variable controls automatic staging deployment: `STAGING_AUTODEPLOY_ENABLED`. Set it to the string `true` only after the admission guard is running on the staging host and an operator wants unattended deploys. Leave it unset to keep automatic deployment off. It is not a secret and must not hold a credential. Manual `workflow_dispatch` does not read it.

`workflow_run` uses the deploy workflow on the default branch, `master`. Pull-request CI uses the workflow in the pull request and always stays on GitHub-hosted runners. Neither pull-request CI nor fork CI selects `historyit-staging` or `historyit-prod`.

Recommended, not required for the script itself: restrict who can run `workflow_dispatch`, and require the CI check before merging to `master` or `staging`.

## Active jobs and the first deploy

Production (`159d872`, application tree `faa561a`) and the running staging checkout (`2fcc9d3`) both keep runners in `Orchestrator.threads` inside one Uvicorn process.

- Start holds that process lock and will not start a second thread for the same job.
- The lock is not visible outside the process. There is no freeze that blocks a different job, or a Start that arrives after an external idle check.
- Pause does not cancel an inspection, commit, or validation that has already begun.
- Sheet labels and `/health` can both look idle while a thread is in a model call, and a sheet can still say `Inspection In Progress` after the thread is gone.
- Staging's `job.running` flag is that same in-memory thread, returned only for one authenticated job. It is not a deploy lock.

`server/app/deployment_admission.py` is a separate staging application change. `POST /internal/deployment-admission/hold` shares the orchestrator lock. It returns `state=idle`, `hold=held`, and `active_jobs=[]` only when no thread is alive and no Start/Resume/Retry is between its lock and its sheet writes. Otherwise it returns `state=active` and does not take the hold. The route is not under `/health` or `/api`. Nginx does not proxy it. The script reaches it with `docker exec` to `http://127.0.0.1:8000` inside the API container. A non-loopback peer receives 404.

Replacement containers set `HISTORYIT_BOOT_DEPLOY_HOLD=1` in the Compose override, not in the host `.env`. The first process in that container writes `/tmp/historyit-boot-hold-consumed` and refuses Start until smoke checks pass and the script calls the release route. A later restart of the same container keeps that file, so the old boot hold is not applied again. A newly created container has an empty filesystem and can hold. The script does not pause, resume, or start a client job. Installing `deploy_release.py` does not mean the running process has the guard; only the in-container admission response does.

The process running today does not have this route. The script treats HTTP 404 from the in-container probe as an absent guard. A known active job, HTTP 500, non-JSON, or a connection or exec failure is unknown or active and stops activation. The first-install acknowledgement is accepted only for that 404, and only when `HISTORYIT_GITHUB_RUN_ID` is absent. Both deploy workflows set the run id and never set `HISTORYIT_ADMISSION_FIRST_INSTALL`. Actions cannot bypass the guard.

The only first install is a manual command on the staging host, after the operator has confirmed in the product UI that no inspection, commit, or validation is running. The acknowledgement does not discover a live thread and does not override an active or unexplained response. The old process cannot report a live thread, which is why 404 is the only continuable response.

```bash
cd /home/ubuntu/history-it-staging
HISTORYIT_ENV=staging \
HISTORYIT_SHA=<40-character-tested-sha> \
HISTORYIT_ADMISSION_FIRST_INSTALL=I_CONFIRMED_THE_HOST_IS_IDLE \
python3 deploy/scripts/deploy_release.py deploy
```

Run that as `ubuntu`, not under `sudo`. The script calls `sudo -n docker` only when the daemon is not directly available, and it runs git as the current user so the checkout does not become root-owned.

The SHA must already be the tip of `origin/staging`, CI for it must have succeeded, and the candidate tree must contain `server/app/deployment_admission.py`. After that process is up, a later automatic deployment runs only when `STAGING_AUTODEPLOY_ENABLED` is `true` and the running guard reports idle and the hold is acquired.

Until that manual install has happened, leave the variable unset. A staging CI completion then skips the deploy jobs and does not replace containers. A manual staging dispatch still fails closed before replacing containers.

Production `workflow_dispatch` is implemented and will also fail closed. The guard is not in the `159d872` tree. Do not cherry-pick the application commit onto `master`. Production stays manual, and it stays blocked, until a separate port of the guard has been tested and is running on `ip-172-31-5-212`.

## Rollback

On a failed smoke check the script starts the `keep-<previous sha>` images again, moves the clean checkout with `git switch -C <branch> <previous sha>` (not `reset --hard`), and reruns the HTTPS checks. It does not change Sheets data. The release record stays at `/home/ubuntu/historyit-deploy-state/<environment>/release.json` with `activation` still `pending` and the previous image tags. If restoration fails, the error includes `rollback failed`, the rollback command, and that state path. The keep tags are the recovery point.

If the restored process has no admission route, the script says `restored application has no admission route; guarded automatic deployment is not available`. That is the current pre-guard release. The message comes from the live probe returning 404. It is not inferred from the deploy script being installed. Leave `STAGING_AUTODEPLOY_ENABLED` unset until a guarded release is running again.

After a successful deploy, or to undo it once the new process is idle:

```bash
HISTORYIT_ENV=staging python3 /home/ubuntu/history-it-staging/deploy/scripts/deploy_release.py rollback
```

Production uses `HISTORYIT_ENV=production` and `/home/ubuntu/history-it/deploy/scripts/deploy_release.py`. Rollback also asks the running guard for a hold. An active worker or an unknown guard refuses the rollback.

If a deploy stops after taking a hold and before replacing containers, Start stays refused until:

```bash
HISTORYIT_ENV=staging python3 /home/ubuntu/history-it-staging/deploy/scripts/deploy_release.py release-hold
```

`release-hold` does not recreate containers. It refuses while a worker is active.

Exit codes: `0` deployed or restored as requested, `2` identity or usage refusal, `3` superseded or not the branch tip, `4` admission refusal, `1` activation or rollback failure.

## Installing this on both branches

Do not merge `staging` into `master`. `master` is `159d872`, which reverts `702e229`. That merge will not put the staging application back in a controlled way.

The installation is two commits, both based on `staging` `2fcc9d3`:

1. Infrastructure only: `.github/workflows/`, `.gitattributes`, `deploy/scripts/deploy-release.sh`, `deploy/scripts/deploy_release.py`, `deploy/scripts/tests/`, `docs/CICD.md`, and the CI/CD section appended to `Tasks.md`. Cherry-pick this commit onto `master`. If `Tasks.md` conflicts, keep that branch's existing task rows and append the CI/CD section unchanged.
2. Staging application guard only: `server/app/deployment_admission.py`, the admission hooks in `server/app/orchestrator.py` and `server/app/main.py`, and `server/tests/test_deployment_admission.py`. This commit stays on the staging line. Do not cherry-pick it onto `master`, and do not restore release `702e229` there.

`master` then contains the workflows and the host script, so a push to `master` runs CI only. Production containers stay on the `159d872` application tree until a separate, coordinated deployment. Do not set `STAGING_AUTODEPLOY_ENABLED` and do not dispatch either deploy workflow as part of installing these commits.

## Later application promotion

When the staging release is ready for production, do not merge the old `staging` branch into `master`. Create a release branch, revert `159d872` there, then bring across the tested staging commits, including the admission guard once staging has exercised it. Deploy that production SHA with `deploy-production.yml` only after the guard is already running on the production host and the operator has an idle window. The first production process that contains the guard still needs the same manual acknowledgement; the process it replaces does not implement the route.
