from __future__ import annotations
import os
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from googleapiclient.errors import HttpError
from app.config import Settings
from app.models import JobCreate, JobState, BenchmarkRequest, FolderAuthorize, F01CheckpointRequest, LoginRequest
from app.adapters import Drive, Sheets
from app.benchmark import CHECKPOINT_SHEETS, SPECS, compare_workbooks, f01_calibration_checkpoint
from app.inspection import Inspector
from app.orchestrator import Orchestrator
from app.deployment_admission import apply_boot_hold
from app import auth

DIAGNOSTICS_BUILD='2026-10-06-1'

settings=Settings()
app=FastAPI(title='HistoryIT Archive Itemization Automation MVP',version='0.1.0')
_cors_origins = auth.allowed_origins(settings) or ['http://localhost:5173','http://127.0.0.1:5173']
app.add_middleware(CORSMiddleware,allow_origins=_cors_origins,allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
_runtime=None

def runtime():
 global _runtime
 if _runtime:return _runtime
 if not settings.google_application_credentials or not settings.job_index_spreadsheet_id:
  raise RuntimeError('Set GOOGLE_APPLICATION_CREDENTIALS and JOB_INDEX_SPREADSHEET_ID')
 os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = settings.google_application_credentials
 settings.validate_live()
 sheets=Sheets(); drive=Drive(); _runtime=Orchestrator(sheets,drive,Inspector(settings,drive),settings)
 apply_boot_hold(_runtime)
 return _runtime

def live():
 try:return runtime()
 except RuntimeError as e:raise HTTPException(503,str(e))

def guard(request: Request, x_api_token: str | None = None, *, mutating: bool = False):
 """Protect operational APIs. Session cookie or non-empty matching API_TOKEN only."""
 auth.require_auth(request, settings, x_api_token=x_api_token, mutating=mutating)

def google_http_exception(exc):
 status=int(exc.status_code)
 detail=exc.reason or 'Google API request failed'
 if 'Office file' in detail:
  detail+=' Open the file in Drive, choose File > Save as Google Sheets, share that new sheet with the service account as Editor, and use the new spreadsheet ID.'
 elif status==403:
  detail+=' Share this file with the service account. Sheets the app writes need Editor. Drive folders need Viewer.'
 code=status if status in (400,401,403,404,409,429) else 502
 return HTTPException(code,detail)

def call(fn,*args):
 try:return fn(*args)
 except KeyError as e:raise HTTPException(404,str(e))
 except ValueError as e:raise HTTPException(409,str(e))
 except RuntimeError as e:raise HTTPException(503,str(e))
 except HttpError as e:raise google_http_exception(e)

@app.exception_handler(HttpError)
def google_error(_request:Request,exc:HttpError):
 err=google_http_exception(exc)
 return JSONResponse(status_code=err.status_code,content={'detail':err.detail})

@app.get('/health')
def health():return {'status':'ok','service':'historyit-archive-mvp','configured':bool(settings.openai_model and settings.google_application_credentials),'diagnostics_build':DIAGNOSTICS_BUILD,'diagnostics_dir':str(__import__('pathlib').Path(settings.diagnostics_dir).resolve()),'login_configured':auth.login_configured(settings)}

def _require_loopback(request: Request) -> None:
 """Host deploy probe only. Nginx does not publish this path.

 docker exec reaches uvicorn as 127.0.0.1. Any other peer, including the
 Compose network, gets a 404 so worker state is not a public health field.
 """
 peer = request.client.host if request.client else ''
 if peer not in ('127.0.0.1', '::1'):
  raise HTTPException(404, 'Not Found')

@app.get('/internal/deployment-admission')
def deployment_admission_status(request: Request):
 _require_loopback(request)
 return call(live().deployment_status)
@app.post('/internal/deployment-admission/hold')
def deployment_admission_hold(request: Request):
 _require_loopback(request)
 return call(live().deployment_hold)
@app.post('/internal/deployment-admission/release')
def deployment_admission_release(request: Request):
 _require_loopback(request)
 return call(live().deployment_release)

@app.post('/api/auth/login')
def login(req: LoginRequest, request: Request, response: Response):
 if not auth.login_configured(settings):
  raise HTTPException(503, 'Login is not configured')
 key = auth.client_key(request)
 auth.throttle.assert_allowed(key, settings.login_max_failures, settings.login_window_seconds, settings.login_lockout_seconds)
 username_ok = hmac_compare(settings.app_login_username.strip(), req.username.strip())
 password_ok = auth.verify_password(settings.app_login_password_hash.strip(), req.password)
 if not (username_ok and password_ok):
  auth.throttle.record_failure(key, settings.login_max_failures, settings.login_window_seconds, settings.login_lockout_seconds)
  raise HTTPException(401, 'Invalid credentials')
 auth.throttle.record_success(key)
 session_token, record = auth.sessions.create(settings.app_login_username.strip(), settings.session_ttl_seconds)
 auth.set_auth_cookies(response, request, settings, session_token, record.csrf_token)
 return {'authenticated': True, 'username': record.username}

@app.get('/api/auth/session')
def session_check(request: Request):
 _token, record = auth.session_from_request(request)
 if record is None:
  return {'authenticated': False}
 return {'authenticated': True, 'username': record.username}

@app.post('/api/auth/logout')
def logout(request: Request, response: Response):
 token, record = auth.session_from_request(request)
 if record is not None:
  # Logout is a cookie mutation; require CSRF when a session exists.
  if not auth.origin_allowed(request, settings):
   raise HTTPException(403, 'Origin not allowed')
  auth.require_csrf(request, record)
 auth.sessions.delete(token)
 auth.clear_auth_cookies(response, request, settings)
 return {'authenticated': False}

def hmac_compare(expected: str, provided: str) -> bool:
 import hmac as _hmac
 return _hmac.compare_digest(expected, provided)

@app.post('/api/jobs',response_model=JobState)
def create(req:JobCreate,request:Request,x_api_token:str|None=Header(default=None)):
 guard(request,x_api_token,mutating=True);return call(live().create,req)
@app.post('/api/jobs/{job_id}/folders',response_model=JobState)
def authorize_folder(job_id:str,req:FolderAuthorize,request:Request,x_api_token:str|None=Header(default=None)):
 guard(request,x_api_token,mutating=True);return call(live().authorize_folder,job_id,req)
@app.post('/api/jobs/{job_id}/start',response_model=JobState)
def start(job_id:str,background:BackgroundTasks,request:Request,x_api_token:str|None=Header(default=None)):
 guard(request,x_api_token,mutating=True);o=live();state=call(o.state,job_id);background.add_task(o.start,job_id);return state
@app.get('/api/jobs/{job_id}',response_model=JobState)
def get_job(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):guard(request,x_api_token);return call(live().state,job_id)
@app.get('/api/jobs/{job_id}/batches')
def batches(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 guard(request,x_api_token);o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'batches':rows,'alert':o.alert(rows)}
@app.get('/api/jobs/{job_id}/usage')
def usage(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 guard(request,x_api_token);o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'usage':o._usage(sid,job_id),'total_cost_usd':o.cost(sid,job_id),'alert':o.alert(rows)}
@app.post('/api/jobs/{job_id}/pause',response_model=JobState)
def pause(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):guard(request,x_api_token,mutating=True);return call(live().pause,job_id)
@app.post('/api/jobs/{job_id}/resume',response_model=JobState)
def resume(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):guard(request,x_api_token,mutating=True);return call(live().resume,job_id)
@app.post('/api/batches/{batch_id}/retry')
def retry(batch_id:str,request:Request,x_api_token:str|None=Header(default=None)):guard(request,x_api_token,mutating=True);return call(live().retry,batch_id)
@app.post('/api/jobs/{job_id}/reconcile')
def reconcile(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
    guard(request,x_api_token,mutating=True)
    return call(live().reconcile, job_id)

@app.post('/api/jobs/{job_id}/validate')
def validate(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 guard(request,x_api_token,mutating=True);o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'results':[o.committer.validate(sid,r,already=r['Batch Status']=='Committed - Validated') for r in rows]}
@app.get('/api/jobs/{job_id}/validation')
def validations(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 guard(request,x_api_token);o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'validation':[r for r in o.sheets.rows(sid,'Validation') if r.get('Scope')==job_id or any(r.get('Check ID','').startswith(b['Batch ID']) for b in rows)]}
def _load_compare_sheets(sheets, spreadsheet_id, names=None):
    loaded = {}
    errors = []
    for name in (names or SPECS):
        try:
            loaded[name] = sheets.rows(spreadsheet_id, name)
        except (ValueError, KeyError) as exc:
            loaded[name] = []
            errors.append({'sheet': name, 'error': str(exc)})
    return loaded, errors

@app.post('/api/benchmark/compare')
def benchmark(req:BenchmarkRequest,request:Request,x_api_token:str|None=Header(default=None)):
    guard(request,x_api_token,mutating=True)
    o = live()
    benchmark_id = req.benchmark_spreadsheet_id or settings.benchmark_spreadsheet_id
    if not benchmark_id:
        raise HTTPException(400, 'benchmark spreadsheet ID required')
    # Read-only. Neither the developer workbook nor the Frozen Benchmark is written.
    live_sheets, live_errors = _load_compare_sheets(o.sheets, req.spreadsheet_id)
    benchmark_sheets, benchmark_errors = _load_compare_sheets(o.sheets, benchmark_id)
    result = compare_workbooks(live_sheets, benchmark_sheets, box=req.box, folder=req.folder)
    read_errors = [*live_errors, *[{**item, 'workbook': 'benchmark'} for item in benchmark_errors]]
    if read_errors:
        result['matches'] = False
        result['outcome'] = 'semantic_benchmark_mismatch'
        result['read_errors'] = read_errors
    diag = getattr(getattr(o, 'inspector', None), 'diagnostics', None)
    if diag is not None:
        try:
            path = diag.write_report(
                'benchmark-compare',
                spreadsheet_id=req.spreadsheet_id,
                benchmark_spreadsheet_id=benchmark_id,
                box=req.box,
                folder=req.folder or '',
                outcome=result.get('outcome'),
                strict_outcome=result.get('strict_outcome'),
                archival_outcome=result.get('archival_outcome'),
                archival_matches=result.get('archival_matches'),
                matches=result.get('matches'),
                final_box19_match=result.get('final_box19_match'),
                internally_consistent=result.get('internally_consistent'),
                counts=result.get('counts'),
                difference_classes=result.get('difference_classes'),
                read_errors=read_errors,
                sheets={name: {key: value for key, value in sheet.items() if key != 'value_mismatches'} | {'mismatch_count': len(sheet.get('value_mismatches') or [])} for name, sheet in (result.get('sheets') or {}).items()},
            )
            result['diagnostics_path'] = str(path)
        except Exception:
            pass
    return result

@app.post('/api/benchmark/f01-checkpoint')
def f01_checkpoint(req:F01CheckpointRequest,request:Request,x_api_token:str|None=Header(default=None)):
    guard(request,x_api_token,mutating=True)
    o = live()
    # Reads the developer register only. The Frozen Benchmark is not opened or written.
    live_sheets, live_errors = _load_compare_sheets(o.sheets, req.spreadsheet_id, CHECKPOINT_SHEETS)
    result = f01_calibration_checkpoint(live_sheets)
    if live_errors:
        result['matches'] = False
        result['outcome'] = 'f01_intermediate_mismatch'
        result['read_errors'] = live_errors
    diag = getattr(getattr(o, 'inspector', None), 'diagnostics', None)
    if diag is not None:
        try:
            path = diag.write_report(
                'benchmark-f01-checkpoint',
                spreadsheet_id=req.spreadsheet_id,
                outcome=result.get('outcome'),
                matches=result.get('matches'),
                expected=result.get('expected'),
                actual=result.get('actual'),
                problems=result.get('problems'),
                verified=result.get('verified'),
                open_intellectual_item=result.get('open_intellectual_item'),
                read_errors=live_errors,
            )
            result['diagnostics_path'] = str(path)
        except Exception:
            pass
    return result
