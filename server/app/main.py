from __future__ import annotations
import os
from fastapi import FastAPI, HTTPException, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from googleapiclient.errors import HttpError
from app.config import Settings
from app.control_plane.api import build_router
from app.control_plane.config import announce
from app.control_plane.runtime import health_fields
from app.control_plane.shadow import guarded_shadow
from pydantic import BaseModel, Field
from app.models import JobCreate, JobState, BenchmarkRequest, FolderAuthorize, F01CheckpointRequest, LoginRequest
from app.adapters import Drive, Sheets
from app.benchmark import CHECKPOINT_SHEETS, SPECS, compare_workbooks, f01_calibration_checkpoint
from app.google_ids import GoogleIdError
from app.inspection import Inspector
from app.orchestrator import Orchestrator
from app.deployment_admission import apply_boot_hold
from app.accounts.service import get_accounts
from app.accounts.job_access import (
    assert_job_access,
    check_register_conflict,
    ensure_workspace_member_for_create,
    actor_stamp,
    sheets_job_context,
)
from app.accounts.store import get_accounts_store
from app import auth

DIAGNOSTICS_BUILD='2026-10-06-1'

settings=Settings()
announce(settings)
app=FastAPI(title='HistoryIT Archive Itemization Automation MVP',version='0.1.0')
_cors_origins = auth.allowed_origins(settings) or ['http://localhost:5173','http://127.0.0.1:5173']
app.add_middleware(CORSMiddleware,allow_origins=_cors_origins,allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
_runtime=None

@app.on_event('startup')
def bootstrap_admin_accounts():
 """Idempotent Admin bootstrap into durable Mongo (or test store)."""
 try:
  get_accounts_store(settings)
  get_accounts(settings).ensure_bootstrap(settings)
 except Exception:
  # Login remains available; authenticate() will retry bootstrap.
  pass

def _require_job_access(principal, job_id: str):
 # Service tokens and patched null guards bypass membership (automation/tests only).
 if principal is None or getattr(principal, 'is_service', False):
  return ''
 project, register_id = sheets_job_context(live, job_id)
 return assert_job_access(settings, principal, job_id, project=project, register_id=register_id)

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
 """Protect operational APIs. Session cookie or non-empty matching API_TOKEN only.

 Returns AuthPrincipal for membership and actor stamping.
 """
 return auth.require_auth(request, settings, x_api_token=x_api_token, mutating=mutating)

def google_http_exception(exc):
 status=int(exc.status_code)
 detail=exc.reason or 'Google API request failed'
 if 'Office file' in detail:
  detail+=' Open the file in Drive, choose File > Save as Google Sheets, share that new sheet with the service account as Editor, and use the new spreadsheet ID.'
 elif status==403:
  detail+=' Share this file with the service account. Sheets the app writes need Editor. Drive folders need Viewer.'
 code=status if status in (400,401,403,404,409,429) else 502
 return HTTPException(code,detail)

def finish(operation, result, actor=None):
 """Shadow the Sheets result. A database failure must not change the Sheets response."""
 job_id = result.get('job_id') if isinstance(result, dict) else getattr(result, 'job_id', None)
 guarded_shadow(settings, operation, job_id, result, live, actor=actor)
 return result

def route_guard(request, token=None, mutating=False):
 """Look up guard at call time so tests can patch app.main.guard."""
 guard(request, token, mutating=mutating)

def call(fn,*args):
 try:return fn(*args)
 except KeyError as e:raise HTTPException(404,str(e))
 except GoogleIdError as e:raise HTTPException(400,str(e))
 except ValueError as e:raise HTTPException(409,str(e))
 except RuntimeError as e:raise HTTPException(503,str(e))
 except HttpError as e:raise google_http_exception(e)

def require_developer_verification():
 if not settings.enable_developer_verification:
  raise HTTPException(
   403,
   'Developer Tri Delta Box 19 verification is disabled. '
   'Set ENABLE_DEVELOPER_VERIFICATION=true on the server to enable this harness.',
  )

@app.exception_handler(HttpError)
def google_error(_request:Request,exc:HttpError):
 err=google_http_exception(exc)
 return JSONResponse(status_code=err.status_code,content={'detail':err.detail})

@app.get('/health')
def health():
 return {
  'status':'ok',
  'service':'historyit-archive-mvp',
  'configured':bool(settings.openai_model and settings.google_application_credentials),
  'diagnostics_build':DIAGNOSTICS_BUILD,
  'diagnostics_dir':str(__import__('pathlib').Path(settings.diagnostics_dir).resolve()),
  'login_configured':get_accounts().login_configured(settings),
  'developer_verification':bool(settings.enable_developer_verification),
  **health_fields(settings),
 }

class ChangePasswordRequest(BaseModel):
 current_password: str = Field(min_length=1, max_length=512)
 new_password: str = Field(min_length=10, max_length=200)

class AdminCreateRequest(BaseModel):
 username: str = Field(min_length=2, max_length=64)
 password: str = Field(min_length=10, max_length=200)
 display_name: str | None = Field(default=None, max_length=120)

class AdminPasswordResetRequest(BaseModel):
 new_password: str = Field(min_length=10, max_length=200)

class AdminActiveRequest(BaseModel):
 active: bool

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
 accounts = get_accounts()
 if not accounts.login_configured(settings):
  raise HTTPException(503, 'Login is not configured')
 key = auth.client_key(request)
 auth.throttle.assert_allowed(key, settings.login_max_failures, settings.login_window_seconds, settings.login_lockout_seconds)
 try:
  user = accounts.authenticate(settings, req.username, req.password)
 except HTTPException:
  auth.throttle.record_failure(key, settings.login_max_failures, settings.login_window_seconds, settings.login_lockout_seconds)
  raise
 auth.throttle.record_success(key)
 # Workspace assignment for the env bootstrap Admin happens in
 # ensure_bootstrap (via authenticate). Do not grant every Admin every
 # workspace on login — that would collapse membership isolation.
 session_token, record = auth.sessions.create(
  user['username'],
  settings.session_ttl_seconds,
  user_id=user['user_id'],
  role=user.get('role') or 'admin',
  display_name=user.get('display_name') or user['username'],
 )
 auth.set_auth_cookies(response, request, settings, session_token, record.csrf_token)
 return auth.session_payload(record)

@app.get('/api/auth/session')
def session_check(request: Request):
 _token, record = auth.session_from_request(request)
 if record is None:
  return {'authenticated': False}
 if record.user_id:
  user = get_accounts().get_user(record.user_id)
  if user is None or not user.get('active', True):
   return {'authenticated': False}
 return auth.session_payload(record)

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

@app.post('/api/auth/change-password')
def change_password(req: ChangePasswordRequest, request: Request, x_api_token: str | None = Header(default=None)):
 principal = guard(request, x_api_token, mutating=True)
 return get_accounts().change_password(principal, req.current_password, req.new_password)

@app.get('/api/auth/admins')
def list_admins(request: Request, x_api_token: str | None = Header(default=None)):
 guard(request, x_api_token)
 return {'admins': get_accounts().list_admins()}

@app.post('/api/auth/admins')
def create_admin(req: AdminCreateRequest, request: Request, x_api_token: str | None = Header(default=None)):
 principal = guard(request, x_api_token, mutating=True)
 return get_accounts().create_admin(principal, username=req.username, password=req.password, display_name=req.display_name)

@app.post('/api/auth/admins/{user_id}/reset-password')
def reset_admin_password(user_id: str, req: AdminPasswordResetRequest, request: Request, x_api_token: str | None = Header(default=None)):
 principal = guard(request, x_api_token, mutating=True)
 return get_accounts().reset_password(principal, user_id, req.new_password)

@app.post('/api/auth/admins/{user_id}/active')
def set_admin_active(user_id: str, req: AdminActiveRequest, request: Request, x_api_token: str | None = Header(default=None)):
 principal = guard(request, x_api_token, mutating=True)
 return get_accounts().set_active(principal, user_id, req.active)

def hmac_compare(expected: str, provided: str) -> bool:
 import hmac as _hmac
 return _hmac.compare_digest(expected, provided)

@app.post('/api/jobs',response_model=JobState)
def create(req:JobCreate,request:Request,x_api_token:str|None=Header(default=None)):
 principal = guard(request,x_api_token,mutating=True)
 ensure_workspace_member_for_create(settings, principal, req.project)
 check_register_conflict(settings, principal, getattr(req, 'spreadsheet_id', None))
 return finish('create', call(live().create,req), actor=actor_stamp(principal))
@app.post('/api/jobs/{job_id}/folders',response_model=JobState)
def authorize_folder(job_id:str,req:FolderAuthorize,request:Request,x_api_token:str|None=Header(default=None)):
 principal = guard(request,x_api_token,mutating=True)
 _require_job_access(principal, job_id)
 return finish('authorize', call(live().authorize_folder,job_id,req), actor=actor_stamp(principal))
@app.post('/api/jobs/{job_id}/start',response_model=JobState)
def start(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 # Synchronous admission so the response reflects running/already_running.
 # The worker thread owns batch/folder advancement; the browser need not stay open.
 principal = guard(request,x_api_token,mutating=True)
 _require_job_access(principal, job_id)
 return finish('start', call(live().start,job_id), actor=actor_stamp(principal))
@app.get('/api/jobs/{job_id}',response_model=JobState)
def get_job(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 principal = guard(request,x_api_token)
 _require_job_access(principal, job_id)
 return call(live().state,job_id)
@app.get('/api/jobs/{job_id}/batches')
def batches(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 principal = guard(request,x_api_token)
 _require_job_access(principal, job_id)
 o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'batches':rows,'alert':o.alert(rows)}
@app.get('/api/jobs/{job_id}/usage')
def usage(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 principal = guard(request,x_api_token)
 _require_job_access(principal, job_id)
 o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'usage':o._usage(sid,job_id),'total_cost_usd':o.cost(sid,job_id),'alert':o.alert(rows)}
@app.post('/api/jobs/{job_id}/pause',response_model=JobState)
def pause(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 principal = guard(request,x_api_token,mutating=True)
 _require_job_access(principal, job_id)
 return finish('pause', call(live().pause,job_id), actor=actor_stamp(principal))
@app.post('/api/jobs/{job_id}/resume',response_model=JobState)
def resume(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 principal = guard(request,x_api_token,mutating=True)
 _require_job_access(principal, job_id)
 return finish('resume', call(live().resume,job_id), actor=actor_stamp(principal))
@app.post('/api/batches/{batch_id}/retry')
def retry(batch_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 principal = guard(request,x_api_token,mutating=True)
 job_id = batch_id.rsplit('-B', 1)[0] if '-B' in batch_id else batch_id
 _require_job_access(principal, job_id)
 return finish('retry', call(live().retry,batch_id), actor=actor_stamp(principal))
@app.post('/api/jobs/{job_id}/reconcile')
def reconcile(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
    principal = guard(request,x_api_token,mutating=True)
    _require_job_access(principal, job_id)
    return call(live().reconcile, job_id)

@app.post('/api/jobs/{job_id}/validate')
def validate(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 principal = guard(request,x_api_token,mutating=True)
 _require_job_access(principal, job_id)
 o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'results':[o.committer.validate(sid,r,already=r['Batch Status']=='Committed - Validated') for r in rows]}
@app.get('/api/jobs/{job_id}/validation')
def validations(job_id:str,request:Request,x_api_token:str|None=Header(default=None)):
 principal = guard(request,x_api_token)
 _require_job_access(principal, job_id)
 o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'validation':[r for r in o.sheets.rows(sid,'Validation') if r.get('Scope')==job_id or any(r.get('Check ID','').startswith(b['Batch ID']) for b in rows)]}
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
    require_developer_verification()
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
    require_developer_verification()
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

app.include_router(build_router(settings, route_guard, lambda: live().sheets))
