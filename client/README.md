# Odyssey frontend (HistoryIT archive workspace)

Operator console for Odyssey Preservation Software — the HistoryIT Archive Itemization Automation MVP UI.

Branding follows the Odyssey logo usage guide (teal/lime palette). **Neue Kabel** is the guide’s primary typeface; this build uses **Montserrat** as a freely licensed geometric sans fallback because licensed Neue Kabel files are not in the repo.

The interface loads live archive data from the backend. It does not contain Google, OpenAI, or service-account credentials, and it does not include sample or preview data.

Operator setup (Drive Viewer vs Sheets Editor, folder hierarchy, Start/Pause, links/IDs) is documented in [`../docs/OPERATOR_GUIDE.md`](../docs/OPERATOR_GUIDE.md).

## Run it locally

```bash
npm install
npm run dev
```

Open `http://localhost:5173`.

The app always calls the backend. Set the address in `.env` (see `.env.example`):

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

If `VITE_API_BASE_URL` is blank during local development, the Vite proxy sends `/health` and `/api/*` to `http://localhost:8000`.

Browser authentication uses `POST /api/auth/login` and HttpOnly session cookies (`credentials: 'include'`). Do not set `VITE_API_TOKEN`. Do not commit `.env.local` or any secret.

## Production build

```bash
npm ci
npm run build
npm run preview
```

Docker Compose production (`docker-compose.prod.yml`) builds with `VITE_API_BASE_URL` empty so the browser uses the same origin; Nginx serves the static UI over HTTPS and proxies `/api` and `/health` to FastAPI. See `deploy/PUBLIC_ACCESS.md` for login, CSRF, Elastic IP certificates, and migration steps.

The frontend never stores Google, OpenAI, or API token secrets.

## Included workflows

- Create a job with project, Drive folder, Developer Test register, batch size, and box.
- Open an existing job by Job ID.
- Poll job, Batch Queue, usage, and validation state while processing.
- Start/continue, pause, resume, reconcile, validate, retry, and authorize the next folder.
- Show prior open item and continuation evidence for F02 and later folders.
- Developer-only Tri Delta Box 19 F01 checkpoint / Frozen Benchmark comparison when `ENABLE_DEVELOPER_VERIFICATION=true` (hidden and API-blocked by default).
- Paste Google Drive folder / Sheets links or raw IDs (normalized on the backend).
- Review classified controlled, free-text, administrative, and distinction-only differences.
- The console shows only data returned by the backend.

## Backend contract used

| UI action | FastAPI route |
|---|---|
| Health | `GET /health` |
| Create job | `POST /api/jobs` |
| Open job | `GET /api/jobs/{job_id}` |
| Batch queue | `GET /api/jobs/{job_id}/batches` |
| Usage | `GET /api/jobs/{job_id}/usage` |
| Validation | `GET /api/jobs/{job_id}/validation` |
| Start / pause / resume | `POST /api/jobs/{job_id}/start`, `/pause`, `/resume` |
| Authorize next folder | `POST /api/jobs/{job_id}/folders` |
| Reconcile / validate | `POST /api/jobs/{job_id}/reconcile`, `/validate` |
| Retry batch | `POST /api/batches/{batch_id}/retry` |
| F01 checkpoint | `POST /api/benchmark/f01-checkpoint` |
| Final comparison | `POST /api/benchmark/compare` |

## Safe operating behavior

The frontend does not enumerate Drive folders itself, write Google Sheets, modify benchmark workbooks, or start an inspection during job creation or folder authorization. Those rules remain enforced by the backend.

Operational API calls require a valid browser session cookie (or a server-only `API_TOKEN` for trusted clients). Production access uses HTTPS; see `deploy/PUBLIC_ACCESS.md`.
