from __future__ import annotations
import hashlib,json,threading,time
from collections import defaultdict
from app.models import Source
from app.commit import CommitService
from app.credits import CreditShortageError, credit_alert
from app.identity import assign_identity, folder_code
from client_reference.commit_engine import CommitError, validate_staging

TERMINAL={'Committed - Validated','Blocked - Human Review'}

def unit_size(requested, count):
    """Return the configured bounded batch size, clipped to the folder remainder.

    Batch size is an operational setting. It must never be changed because a
    folder happens to contain 25 or fewer files: the caller may deliberately
    use small batches for a smoke test or a large batch for continuity.
    """
    size = requested or 10
    if size < 1:
        raise ValueError("batch size must be positive")
    return min(size, max(count, 1))
class Orchestrator:
    def __init__(self,sheets,drive,inspector,settings):
        self.sheets=sheets;self.drive=drive;self.inspector=inspector;self.settings=settings
        self.committer=CommitService(sheets);self.threads={};self.lock=threading.RLock()
    def _queue(self,sid,job):
        rows=[r for r in self.sheets.rows(sid,'Batch Queue') if r.get('Job ID')==job]
        for r in rows:
            try:r['_manifest']=json.loads(r.get('Notes') or '{}')['manifest']
            except (KeyError,ValueError):raise ValueError('Invalid immutable manifest in Batch Queue')
        return rows
    def _locator(self,job):
        for row in self.sheets.rows(self.settings.job_index_spreadsheet_id,'HistoryIT Jobs'):
            if row.get('Job ID')==job:return row
        raise KeyError(job)
    def _find(self,job):
        # Spreadsheet ID is persisted in the locator; process memory is never authoritative.
        return self._locator(job)['Spreadsheet ID']
    def _job(self,job):
        sid=self._find(job);rows=self._queue(sid,job)
        if not rows:raise KeyError(job)
        return sid,rows
    def create(self,req):
        self.sheets.ensure_tab(self.settings.job_index_spreadsheet_id,'HistoryIT Jobs',['Job ID','Spreadsheet ID','Project','Source Folder ID'])
        self.sheets.ensure_tab(req.spreadsheet_id,'OpenAI Usage',['Call ID','Project','Job ID','Batch ID','Model','Source Image Count','Input Tokens','Cached Input Tokens','Output Tokens','Estimated Cost USD','Attempt','Elapsed Seconds','Status','Cumulative Job Cost USD','Response ID'])
        sources=self.drive.enumerate(req.source_folder_id)
        if not sources:raise ValueError('Authorized folder contains no images')
        existing={r.get('Digital Asset') for r in self.sheets.rows(req.spreadsheet_id,'Asset QC')}
        if existing & {s.name for s in sources}:raise ValueError('Authorized sources already exist in Asset QC')
        folders=defaultdict(list)
        for s in sources:folders[s.folder_id].append(s)
        requested=req.batch_size or self.settings.batch_size
        digest=hashlib.sha256((req.spreadsheet_id+'|'+req.source_folder_id+'|'+req.project).encode()).hexdigest()[:16]
        job='HIT-'+digest
        if any(r.get('Job ID')==job for r in self.sheets.rows(req.spreadsheet_id,'Batch Queue')):raise ValueError('Job already initialized; resume existing job')
        # Register is authoritative; the index only resolves opaque IDs to sheet IDs.
        self.sheets.write(self.settings.job_index_spreadsheet_id,'HistoryIT Jobs',{'Job ID':job,'Spreadsheet ID':req.spreadsheet_id,'Project':req.project,'Source Folder ID':req.source_folder_id},('Job ID',job))
        seq=0
        for fid,group in folders.items():
            step=unit_size(requested,len(group))
            for start in range(0,len(group),step):
                chunk=group[start:start+step];seq+=1;bid=f'{job}-B{seq:04d}'
                payload={'manifest':[x.model_dump() for x in chunk], 'folder_id':fid}
                note=json.dumps(payload,separators=(',',':'))
                if len(note)>45000:raise ValueError('Batch manifest exceeds one Sheet cell; lower BATCH_SIZE')
                self.sheets.write(req.spreadsheet_id,'Batch Queue',{'Project':req.project,'Job ID':job,'Batch ID':bid,'Box':req.box,'Source Group / Folder':chunk[0].folder_name,'Source Folder URL':'https://drive.google.com/drive/folders/'+fid,'Start Filename':chunk[0].name,'End Filename':chunk[-1].name,'Planned Asset Count':str(len(chunk)),'Batch Status':'Authorized / Pending Inspection','Prior Open Item':'None','Resulting Open Item':'None','Inspection Attempt':'0','Commit Attempt':'0','Validation Status':'Not Run','Error / Retry Note':'','Next Batch Start':'','Notes':note,'Staging Schema':'Lean Split v2'})
        return self.state(job)
    def state(self,job):
        sid,rows=self._job(job)
        pending=next((r for r in rows if r['Batch Status']!='Committed - Validated'),None)
        return {'job_id':job,'project':rows[0]['Project'],'spreadsheet_id':sid,'source_folder_id':self._locator(job).get('Source Folder ID',''),'batches':len(rows),'status':'Paused' if any(r['Batch Status']=='Paused' for r in rows) else ('Completed - Validated' if pending is None else pending['Batch Status']),'next_batch':pending['Batch ID'] if pending else None,'alert':self.alert(rows)}
    def alert(self,rows):
        for row in rows:
            note=(row.get('Error / Retry Note') or '').strip()
            if row.get('Batch Status')=='Blocked - Human Review' and note.startswith('OpenAI account is out of credits.'):
                return note
        pending=next((r for r in rows if r['Batch Status']!='Committed - Validated'),None)
        if pending and pending['Batch Status']=='Blocked - Human Review':
            return (pending.get('Error / Retry Note') or '').strip() or None
        return None
    def _credit_blocked(self,row):
        return row.get('Batch Status')=='Blocked - Human Review' and str(row.get('Error / Retry Note') or '').startswith('OpenAI account is out of credits.')
    def start(self,job):
        with self.lock:
            thread=self.threads.get(job)
            if thread and thread.is_alive():return self.state(job)
            t=threading.Thread(target=self.run,args=(job,),daemon=True,name='historyit-'+job)
            self.threads[job]=t;t.start()
        return self.state(job)
    def pause(self,job):
        sid,rows=self._job(job)
        for row in rows:
            if row['Batch Status'] in ('Authorized / Pending Inspection','Inspection Failed - Retryable'):
                self.sheets.write(sid,'Batch Queue',{'Batch Status':'Paused'},('Batch ID',row['Batch ID']))
        return self.state(job)
    def resume(self,job):
        sid,rows=self._job(job)
        for row in rows:
            if self._credit_blocked(row):
                self.sheets.write(sid,'Batch Queue',{'Batch Status':'Authorized / Pending Inspection','Error / Retry Note':''},('Batch ID',row['Batch ID']))
            elif row['Batch Status']=='Paused':
                self.sheets.write(sid,'Batch Queue',{'Batch Status':'Authorized / Pending Inspection'},('Batch ID',row['Batch ID']))
        return self.start(job)
    def retry(self,bid):
        for jobrow in self.sheets.rows(self.settings.job_index_spreadsheet_id,'HistoryIT Jobs'):
            sid=jobrow['Spreadsheet ID']
            matches=[r for r in self.sheets.rows(sid,'Batch Queue') if r['Batch ID']==bid]
            if matches:
                r=matches[0]
                if r['Batch Status'] not in ('Inspection Failed - Retryable','Commit Failed - Retryable','Blocked - Human Review'):
                    raise ValueError('Batch not retryable')
                self.sheets.write(sid,'Batch Queue',{'Batch Status':'Inspection Complete - Awaiting Commit' if self._complete(sid,r) else 'Authorized / Pending Inspection','Validation Status':'Not Run','Error / Retry Note':''},('Batch ID',bid))
                return self.start(r['Job ID'])
        raise KeyError(bid)
    def _complete(self,sid,b):
        rows=[r for r in self.sheets.rows(sid,'Asset Staging') if r['Batch ID']==b['Batch ID']]
        expected=json.loads(b['Notes'])['manifest']
        return len(rows)==len(expected) and {r['Digital Asset'] for r in rows}=={s['name'] for s in expected} and all(r['Inspection Complete']=='Yes' for r in rows)
    def _usage(self,sid,job):
        return [r for r in self.sheets.rows(sid,'OpenAI Usage') if r['Job ID']==job]
    def cost(self,sid,job):return sum(float(r.get('Estimated Cost USD') or 0) for r in self._usage(sid,job))
    def _record_usage(self,sid,job,b,attempt,info,success,status=None):
        cached=int(info.get('cached_input_tokens',0));inp=int(info.get('input_tokens',0));out=int(info.get('output_tokens',0))
        s=self.settings;cost=((inp-cached)*s.openai_input_usd_per_million+cached*s.openai_cached_input_usd_per_million+out*s.openai_output_usd_per_million)/1e6
        total=self.cost(sid,job)+cost
        call_id=info.get('call_id') or f'{b["Batch ID"]}-A{attempt:02d}'
        self.sheets.write(sid,'OpenAI Usage',{'Call ID':call_id,'Project':b['Project'],'Job ID':job,'Batch ID':b['Batch ID'],'Model':s.openai_model,'Source Image Count':b['Planned Asset Count'],'Input Tokens':str(inp),'Cached Input Tokens':str(cached),'Output Tokens':str(out),'Estimated Cost USD':str(cost),'Attempt':str(attempt),'Elapsed Seconds':str(info.get('elapsed_seconds',0)),'Status':status or ('Success' if success else 'Failure'),'Cumulative Job Cost USD':str(total),'Response ID':info.get('response_id','')},('Call ID',call_id))
    def _stage(self,sid,b,output,prior='None'):
        expected=[x['name'] for x in json.loads(b['Notes'])['manifest']]
        assets=[a.model_dump(by_alias=True) for a in output.assets];records=[r.model_dump(by_alias=True) for r in output.records]
        if [a['Digital Asset'] for a in assets]!=expected:raise ValueError('Staging filename/order mismatch')
        if len({a['Digital Asset'] for a in assets})!=len(assets):raise ValueError('Duplicate staging key')
        items=self.sheets.rows(sid,'Itemization')
        refs=self.sheets.rows(sid,'Metadata References')
        others=[r for r in self.sheets.rows(sid,'Record Staging') if r.get('Batch ID')!=b['Batch ID']]
        folder=assign_identity(b,assets,records,items,refs,others,prior)
        if b.get('Source Group / Folder')!=folder:
            b['Source Group / Folder']=folder
            self.sheets.write(sid,'Batch Queue',{'Source Group / Folder':folder},('Batch ID',b['Batch ID']))
        self._drop_stale(sid,b,assets,records)
        self._require_commit_contract(sid,b,assets,records)
        for collection,name,key in [(assets,'Asset Staging','Digital Asset'),(records,'Record Staging','Proposed Record ID')]:
            for r in collection:
                # Asset filenames are globally unique. Record IDs can recur when
                # an open intellectual item continues, so staging uses a
                # batch+record composite key and retains prior batch history.
                self._write_stage_row(sid, name, r, b['Batch ID'], key)
        if not self._complete(sid,b):raise ValueError('Staging readback mismatch')
    def _write_stage_row(self, sid, sheet, row, batch_id, key):
        # The production adapter supports composite matching. Small in-memory
        # test doubles from the client package only support a two-item key.
        if self.sheets.__class__.__module__ == 'app.adapters':
            identity=[('Digital Asset', row[key])] if sheet == 'Asset Staging' else [('Batch ID', batch_id), ('Proposed Record ID', row[key])]
        else:
            identity=(key, row[key])
        self.sheets.write(sid, sheet, row, identity)

    def _drop_stale(self,sid,b,assets,records):
        names={a['Digital Asset'] for a in assets}
        ids={r['Proposed Record ID'] for r in records}
        for sheet,key,keep in (('Asset Staging','Digital Asset',names),('Record Staging','Proposed Record ID',ids)):
            for row in self.sheets.rows(sid,sheet):
                if row.get('Batch ID')==b['Batch ID'] and row.get(key) not in keep:
                    self.sheets.clear(sid,sheet,row.get('_row'),(key,row.get(key)))
    def _require_commit_contract(self,sid,b,assets,records):
        queue=dict(b)
        queue['Batch Status']='Inspection Complete - Awaiting Commit'
        queue['Planned Asset Count']=queue.get('Planned Asset Count') or str(len(assets))
        validate_staging(queue,assets,records,self.sheets.rows(sid,'Asset QC'))
    def _staging_ready(self,sid,b):
        if not self._complete(sid,b):return False
        assets=[r for r in self.sheets.rows(sid,'Asset Staging') if r.get('Batch ID')==b['Batch ID']]
        records=[r for r in self.sheets.rows(sid,'Record Staging') if r.get('Batch ID')==b['Batch ID']]
        try:self._require_commit_contract(sid,b,assets,records)
        except CommitError:return False
        return True
    def run(self,job):
        sid,rows=self._job(job)
        for b in rows:
            b=next(r for r in self._queue(sid,job) if r['Batch ID']==b['Batch ID'])
            status=b['Batch Status']
            if status=='Committed - Validated':self.committer.validate(sid,b,already=True);continue
            if status in ('Paused','Blocked - Human Review'):return
            if status in ('Inspection In Progress','Inspection Failed - Retryable','Commit Failed - Retryable'):
                if self._staging_ready(sid,b):status='Inspection Complete - Awaiting Commit'
                else:status='Authorized / Pending Inspection'
            if status=='Authorized / Pending Inspection':
                if self.cost(sid,job)>=self.settings.job_spending_limit_usd:
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Blocked - Human Review','Error / Retry Note':'Job spending limit reached'},('Batch ID',b['Batch ID']));return
                attempt=int(float(b.get('Inspection Attempt') or 0))+1
                if attempt>self.settings.max_retries:
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Blocked - Human Review','Error / Retry Note':'Inspection retry limit reached'},('Batch ID',b['Batch ID']));return
                self.sheets.write(sid,'Batch Queue',{'Batch Status':'Inspection In Progress','Inspection Attempt':str(attempt)},('Batch ID',b['Batch ID']))
                usage={}
                try:
                    prior=self.sheets.field(sid,'Open Intellectual Item')
                    sources=[Source.model_validate(s) for s in b['_manifest']]
                    rules=self.sheets.rows(sid,'Rules')+self.sheets.rows(sid,'Project Classification Rules')
                    output,usage=self.inspector.inspect(job,b['Batch ID'],sources,{'prior_open_item':prior,'folder_code':folder_code(b),'continuation':self.sheets.rows(sid,'Continuation State'),'existing_open_record':[r for r in self.sheets.rows(sid,'Itemization') if r['Item ID']==prior]+[r for r in self.sheets.rows(sid,'Metadata References') if r.get('Reference ID')==prior], 'inspection_attempt': attempt},rules)
                    self._stage(sid,b,output,prior)
                    self._record_usage(sid,job,b,attempt,usage,True)
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Inspection Complete - Awaiting Commit','Prior Open Item':prior,'Inspection Date':time.strftime('%Y-%m-%d')},('Batch ID',b['Batch ID']))
                except CreditShortageError as exc:
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Blocked - Human Review','Inspection Attempt':str(max(attempt-1,0)),'Error / Retry Note':credit_alert(job)},('Batch ID',b['Batch ID']))
                    try:
                        self._record_usage(sid,job,b,attempt,{'elapsed_seconds':getattr(exc,'elapsed_seconds',0),'call_id':f'{b["Batch ID"]}-CREDIT-{time.time_ns()}'},False,'Blocked - OpenAI Credits')
                    except Exception:
                        pass
                    return
                except Exception as exc:
                    # Ambiguous failure: a complete readback is eligible for commit on resume.
                    info=dict(getattr(exc, 'inspection_usage', {}) or usage)
                    info.setdefault('elapsed_seconds',getattr(exc,'elapsed_seconds',0))
                    try:
                        self._record_usage(sid,job,b,attempt,info,False)
                    except Exception:
                        pass
                    note=str(exc)
                    path=getattr(exc, 'diagnostics_path', '')
                    if path:note=f'{note[:180]} [Diagnostic: {path}]'
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Inspection Failed - Retryable','Error / Retry Note':note[:300]},('Batch ID',b['Batch ID']))
                    return
            try:
                b=next(r for r in self._queue(sid,job) if r['Batch ID']==b['Batch ID'])
                self.committer.commit(sid,b)
            except Exception as exc:
                latest=next(r for r in self._queue(sid,job) if r['Batch ID']==b['Batch ID'])
                if latest['Batch Status']!='Blocked - Human Review':
                    # Preserve Pending Validation when writes might have reached Sheets.
                    if latest['Batch Status']!='Commit Written - Pending Validation':
                        self.sheets.write(sid,'Batch Queue',{'Batch Status':'Commit Failed - Retryable','Error / Retry Note':str(exc)[:300]},('Batch ID',b['Batch ID']))
                return
        self._close_folders(sid,job)
    def _close_folders(self,sid,job):
        groups=defaultdict(list)
        for b in self._queue(sid,job):groups[b['Source Group / Folder']].append(b)
        for folder,bs in groups.items():
            names=[s['name'] for b in bs for s in b['_manifest']]
            qc=[r for r in self.sheets.rows(sid,'Asset QC') if r['Digital Asset'] in names]
            # A validated folder may leave an intellectual item open for an
            # adjacent authorized folder. Continuation State carries it forward.
            if all(b['Batch Status']=='Committed - Validated' for b in bs) and len(qc)==len(names) and {r['Digital Asset'] for r in qc}==set(names):
                for r in self.sheets.rows(sid,'Folder Processing Unit Summary'):
                    if r['Source Group / Folder']==folder and r.get('Notes','').startswith(job):
                        self.sheets.write(sid,'Folder Processing Unit Summary',{'Processing Status':'Completed - Validated','Completed Date':time.strftime('%Y-%m-%d')},('Processing Unit / Source Folder',r['Processing Unit / Source Folder']))
