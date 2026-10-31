# Admin isolation (production-ready foundation)

Concurrent Admin users with project/job membership isolation. Operator and
Viewer roles are **not** implemented and remain deferred.

## Deployment process model

`server/Dockerfile` runs **exactly one** Uvicorn worker:

```text
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

No `--workers` flag. Cookie sessions stay **in-memory for that single process**.
That matches the existing orchestrator assumption (one process-local runner map).

**Limitation:** a process restart clears sessions (Admins must sign in again).
Accounts and memberships survive restart in MongoDB. Multi-process session
sharing is **not** required by the current deployment; do not scale Uvicorn
workers without adding shared session storage.

## Mongo collections and indexes

Accounts use the **same MongoDB database** as the control plane.

### `users`
| Field | Notes |
|---|---|
| `user_id` | Stable `usr-` + SHA-256 of casefolded username |
| `username` | Login name |
| `username_key` | Casefolded unique key |
| `display_name` | UI label |
| `password_hash` | Argon2; **never** returned by API |
| `role` | Always `admin` |
| `active` | Disable without deleting |
| `created_at`, `updated_at` | ISO timestamps |

Indexes: `uniq_user_id`, `uniq_username_key`.

### `workspace_members`
| Field | Notes |
|---|---|
| `user_id` | Admin |
| `workspace_id` | Control-plane workspace |
| `role` | Always `admin` |
| `created_at` | ISO timestamp |

Indexes: `uniq_user_workspace` (`user_id`+`workspace_id`), `member_workspace`.

These specs are included in `control_plane.indexes.INDEX_SPECS` and
`accounts.mongo_store.ACCOUNT_INDEX_SPECS`. `ensure_indexes()` runs on connect /
startup when the accounts store opens the control-plane database. No separate
migration script is required.

## Bootstrap procedure

1. Configure `APP_LOGIN_USERNAME` and `APP_LOGIN_PASSWORD_HASH` (existing env).
2. Configure `MONGODB_URI` and enable `DATABASE_CONTROL_PLANE_ENABLED`.
3. On application startup (and again on login), bootstrap creates the Admin if
   missing with a stable `user_id`, using the env password hash.
4. On **first create only**, bootstrap assigns **all existing workspaces** to
   that Admin (idempotent unique memberships). Later ensure_bootstrap calls do
   not grant the env Admin membership on workspaces created by other Admins.
5. Create additional Admins with `POST /api/auth/admins` (requires Admin session).
   Additional Admins receive membership only for workspaces they create (or
   that an operator assigns explicitly).

Re-running bootstrap never duplicates the user or memberships. Login never
assigns every workspace to every Admin.

## Admin create / reset / disable

| Endpoint | Purpose |
|---|---|
| `POST /api/auth/change-password` | Signed-in Admin changes own password |
| `GET /api/auth/admins` | List Admins (no password hashes) |
| `POST /api/auth/admins` | Create Admin |
| `POST /api/auth/admins/{user_id}/reset-password` | Reset another Admin |
| `POST /api/auth/admins/{user_id}/active` | Disable / re-enable |

Disabled Admins cannot authenticate; existing cookies fail closed on next request.

## Session storage

- Cookie names unchanged: `historyit_session`, `historyit_csrf`
- Session record includes `user_id`, `role`, `display_name`, CSRF, expiry
- CSRF + origin checks on mutating routes unchanged
- Single-process memory store (see deployment model above)

## Authorization rules

1. Every signed-in user is `role=admin` (`require_admin`).
2. Control-plane list/detail/mutations are filtered or enforced by
   `workspace_members`.
3. Live `/api/jobs/*` mutate/detail paths map the job to a workspace via:
   - control-plane job row, else
   - Sheets job-index `Project` / spreadsheet identity, then `workspace_id_for`.
4. If mapping fails, the Admin receives **403** (fail closed). Knowing a Job ID
   alone is not enough.
5. Creating a job requires membership (created on project create) and rejects
   another Admin’s **open** job on the same Production Register when
   `register_id` is known.
6. Historical `created_by` / event `actor` values that are plain usernames remain
   readable. New stamps use `user_id`.

## Service-token exceptions

When `API_TOKEN` is configured, `X-API-Token` authenticates as a **service**
principal:

- Bypasses workspace membership checks
- Intended for trusted server-side automation only
- Must never be shipped to the browser (`VITE_API_TOKEN` remains forbidden)
- Normal Admin cookie sessions never receive this bypass

Patched null `guard` returns in unit tests also bypass membership; that path is
test-only.

## Restart and rollback

| Concern | Behavior |
|---|---|
| App restart | Mongo users/memberships persist; in-memory sessions clear |
| Bootstrap | Idempotent; safe after restart |
| Membership assign | Idempotent unique `(user_id, workspace_id)` |
| Disable flag / remove code | Revert branch; Mongo collections can remain unused |
| Control plane off | Live Admin job APIs return 503 until Mongo control plane is enabled |
| `CONTROL_PLANE_ENVIRONMENT=production` | Existing control-plane gate refuses Mongo (accounts included). Deploy with a non-`production` label (e.g. `staging`) as today. |

## Exact deployment requirements

1. One Uvicorn process (Dockerfile default; no `--workers`).
2. `MONGODB_URI` set to the control-plane database.
3. `DATABASE_CONTROL_PLANE_ENABLED=true`.
4. `APP_LOGIN_USERNAME` and `APP_LOGIN_PASSWORD_HASH` for bootstrap Admin.
5. `CONTROL_PLANE_ENVIRONMENT=staging` (the literal label `production` is refused).
6. No browser API token (`VITE_API_TOKEN` forbidden; do not ship `API_TOKEN` to the client).
7. Indexes are created on connect (`ensure_indexes`); no separate migration job.

## Operator / Viewer deferral

Do **not** add Operator or Viewer roles in this release. Persist `role=admin`
only. Future roles can extend the same `users` / `workspace_members` documents
without changing Job ID or Sheets payloads.

## Unit tests

`MemoryAccountsStore` is for isolated unit tests via `set_accounts_store`.
Runtime opens `MongoAccountsStore` on the control-plane database.
