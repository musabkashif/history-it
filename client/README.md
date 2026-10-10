# HistoryIT Frontend

A production-ready React + TypeScript + Tailwind operator console for the HistoryIT Archive Itemization Automation MVP.

The interface loads live archive data from the backend. It does not contain Google, OpenAI, or service-account credentials, and it does not include sample or preview data.

## Run it locally

```bash
npm install
npm run dev
```

Open `http://localhost:5173`.

The app always calls the backend. Set the address in `.env` (see `.env.example`):

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_API_TOKEN=
```

If `VITE_API_BASE_URL` is blank during local development, the Vite proxy sends `/health` and `/api/*` to `http://localhost:8000`.

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

If `VITE_API_TOKEN` is set, the console sends it as `X-API-Token`. For production, use HTTPS and a deployment setup appropriate for the client’s security policy.
