# HistoryIT Frontend

A production-ready React + TypeScript + Tailwind operator console for the HistoryIT Archive Itemization Automation MVP.

The interface is built against the current FastAPI routes in the supplied backend. It does not contain Google, OpenAI, or service-account credentials. It supports a safe Preview workspace for client demos before the backend is deployed, and a real API mode for running against your local FastAPI service.

## Run it locally

```bash
npm install
npm run dev
```

Open `http://localhost:5173`.

By default the app starts in **Preview mode**, which lets you demonstrate the UI with safe sample results. Go to **Settings → API mode** when the backend is running. If `VITE_API_BASE_URL` is blank, the Vite development proxy sends `/health` and `/api/*` to `http://localhost:8000`.

For a different backend URL, create `.env.local`:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_API_TOKEN=
```

Do not commit `.env.local` or any secret.

## Production build

```bash
npm run build
npm run preview
```

For a deployed static frontend, set `VITE_API_BASE_URL` to the hosted FastAPI origin. The backend must allow the deployed frontend origin with CORS, or both applications must be served behind the same origin/reverse proxy. The frontend never proxies secrets or stores them in source code.

## Included workflows

- Create a job with project, Drive folder, Developer Test register, batch size, and box.
- Open an existing job by Job ID.
- Poll job, Batch Queue, usage, and validation state while processing.
- Start/continue, pause, resume, reconcile, validate, retry, and authorize the next folder.
- Show prior open item and continuation evidence for F02 and later folders.
- Run the read-only F01 calibration checkpoint.
- Run the read-only final Frozen Benchmark comparison with folder scope support.
- Review classified controlled, free-text, administrative, and distinction-only differences.
- Use Preview mode for a Loom/demo walkthrough without making backend calls.

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

The UI keeps preview results visibly separate from API results. The frontend does not enumerate Drive folders itself, write Google Sheets, modify benchmark workbooks, or start an inspection during job creation or folder authorization. Those rules remain enforced by the backend.

The API token is held in browser local storage only when entered in Settings and is sent as `X-API-Token`. For production, use HTTPS and a deployment setup appropriate for the client’s security policy.
