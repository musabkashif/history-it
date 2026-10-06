from __future__ import annotations
import os, hmac
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header, Request
from fastapi.responses import JSONResponse
from googleapiclient.errors import HttpError
from app.config import Settings
from app.models import JobCreate, JobState, BenchmarkRequest
from app.adapters import Drive, Sheets
from app.inspection import Inspector
from app.orchestrator import Orchestrator

DIAGNOSTICS_BUILD='2026-10-06-1'

settings=Settings()
app=FastAPI(title='HistoryIT Archive Itemization Automation MVP',version='0.1.0')
_runtime=None

def runtime():
 global _runtime
 if _runtime:return _runtime
 if not settings.google_application_credentials or not settings.job_index_spreadsheet_id:
  raise RuntimeError('Set GOOGLE_APPLICATION_CREDENTIALS and JOB_INDEX_SPREADSHEET_ID')
 settings.validate_live()
 sheets=Sheets(); drive=Drive(); _runtime=Orchestrator(sheets,drive,Inspector(settings,drive),settings)
 return _runtime

def live():
 try:return runtime()
 except RuntimeError as e:raise HTTPException(503,str(e))

def guard(token):
 if settings.api_token and not hmac.compare_digest(settings.api_token,token or ''):raise HTTPException(401,'Unauthorized')

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
def health():return {'status':'ok','service':'historyit-archive-mvp','configured':bool(settings.openai_model and settings.google_application_credentials),'diagnostics_build':DIAGNOSTICS_BUILD,'diagnostics_dir':str(__import__('pathlib').Path(settings.diagnostics_dir).resolve())}

@app.post('/api/jobs',response_model=JobState)
def create(req:JobCreate,x_api_token:str|None=Header(default=None)):
 guard(x_api_token);return call(live().create,req)
@app.post('/api/jobs/{job_id}/start',response_model=JobState)
def start(job_id:str,background:BackgroundTasks,x_api_token:str|None=Header(default=None)):
 guard(x_api_token);o=live();state=call(o.state,job_id);background.add_task(o.start,job_id);return state
@app.get('/api/jobs/{job_id}',response_model=JobState)
def get_job(job_id:str,x_api_token:str|None=Header(default=None)):guard(x_api_token);return call(live().state,job_id)
@app.get('/api/jobs/{job_id}/batches')
def batches(job_id:str,x_api_token:str|None=Header(default=None)):
 guard(x_api_token);o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'batches':rows,'alert':o.alert(rows)}
@app.get('/api/jobs/{job_id}/usage')
def usage(job_id:str,x_api_token:str|None=Header(default=None)):
 guard(x_api_token);o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'usage':o._usage(sid,job_id),'total_cost_usd':o.cost(sid,job_id),'alert':o.alert(rows)}
@app.post('/api/jobs/{job_id}/pause',response_model=JobState)
def pause(job_id:str,x_api_token:str|None=Header(default=None)):guard(x_api_token);return call(live().pause,job_id)
@app.post('/api/jobs/{job_id}/resume',response_model=JobState)
def resume(job_id:str,x_api_token:str|None=Header(default=None)):guard(x_api_token);return call(live().resume,job_id)
@app.post('/api/batches/{batch_id}/retry')
def retry(batch_id:str,x_api_token:str|None=Header(default=None)):guard(x_api_token);return call(live().retry,batch_id)
@app.post('/api/jobs/{job_id}/validate')
def validate(job_id:str,x_api_token:str|None=Header(default=None)):
 guard(x_api_token);o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'results':[o.committer.validate(sid,r,already=r['Batch Status']=='Committed - Validated') for r in rows]}
@app.get('/api/jobs/{job_id}/validation')
def validations(job_id:str,x_api_token:str|None=Header(default=None)):
 guard(x_api_token);o=live();sid,rows=call(o._job,job_id);return {'job_id':job_id,'validation':[r for r in o.sheets.rows(sid,'Validation') if r.get('Scope')==job_id or any(r.get('Check ID','').startswith(b['Batch ID']) for b in rows)]}
def _compare_value(value):
    if value is None:return ''
    text=' '.join(str(value).strip().split())
    try:
        number=float(text)
        return str(int(number)) if number.is_integer() else str(number)
    except (TypeError,ValueError):
        return text

def _row_diff(actual, expected, fields):
    return {field:{'live':actual.get(field,''),'benchmark':expected.get(field,'')}
            for field in fields if _compare_value(actual.get(field)) != _compare_value(expected.get(field))}

@app.post('/api/benchmark/compare')
def benchmark(req:BenchmarkRequest,x_api_token:str|None=Header(default=None)):
    guard(x_api_token);o=live();benchmark_id=req.benchmark_spreadsheet_id or settings.benchmark_spreadsheet_id
    if not benchmark_id:raise HTTPException(400,'benchmark spreadsheet ID required')
    specs={
      'Asset QC':('Digital Asset',['Digital Asset','Assigned Record ID','Template','Type','Asset Role','Required Crop Standard','QC Status','Action Needed','Expected Output Asset Count','Production Queue Eligible']),
      'Itemization':('Item ID',['Item ID','Digital Assets','Digital Asset Count','Logical Component Count','Template','Type','Metadata Readiness','Production Task Count']),
      'Metadata References':('Reference ID',['Reference ID','Digital Assets','Digital Asset Count','Reference Role','Template','QC Status','Action Needed','Metadata Readiness']),
      'Ready for Metadata':('Item ID',['Item ID','Digital Assets','Digital Asset Count','Template','Type','Metadata Readiness']),
      'Production Queue':('Task ID',['Task ID','Assigned Record ID','Digital Asset','Action Needed','Expected Output Asset Count','Task Status']),
    }
    result={};all_match=True
    for sheet,(key,fields) in specs.items():
        live_rows=o.sheets.rows(req.spreadsheet_id,sheet);target_rows=o.sheets.rows(benchmark_id,sheet)
        lm={r.get(key):r for r in live_rows if r.get(key)};tm={r.get(key):r for r in target_rows if r.get(key)}
        missing=sorted(set(tm)-set(lm));unexpected=sorted(set(lm)-set(tm));mismatches=[]
        for k in sorted(set(lm)&set(tm)):
            diff=_row_diff(lm[k],tm[k],fields)
            if diff:mismatches.append({'key':k,'fields':diff})
        matched=not missing and not unexpected and not mismatches
        all_match=all_match and matched
        result[sheet]={'live_count':len(lm),'benchmark_count':len(tm),'missing':missing[:100],'unexpected':unexpected[:100],'value_mismatches':mismatches[:100],'matches':matched}
    return {'box':req.box,'matches':all_match,'sheets':result}
