from __future__ import annotations
import hashlib,json,threading,time
from collections import defaultdict
from app.box_folders import select_authorized_sources
from app.models import Source
from app.commit import CommitService
from app.credits import CreditShortageError, credit_alert
from app.diagnostics import exception_chain
from app.identity import assign_identity, folder_code
from app.authorization import (
    BatchAuthorizationError, RegisterStateError, assert_batch_prior, assert_single_active_batch,
    authorized_manifest, initial_queue_prior,
)
from app.references import ReferenceError, order_records, reference_mismatches, references_dir, resolve_reference
from client_reference.commit_engine import CommitError, split_assets, validate_staging

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
        self.committer=CommitService(sheets, getattr(inspector, 'diagnostics', None));self.threads={};self.lock=threading.RLock()
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
        digest=hashlib.sha256((req.spreadsheet_id+'|'+req.source_folder_id+'|'+req.project).encode()).hexdigest()[:16]
        job='HIT-'+digest
        if any(r.get('Job ID')==job for r in self.sheets.rows(req.spreadsheet_id,'Batch Queue')):raise ValueError('Job already initialized; resume existing job')
        # A new job is a clean project start. Do not list Drive until that check passes,
        # so a later folder is not enumerated by a second create call.
        initial_queue_prior(self._continuation(req.spreadsheet_id,'Open Intellectual Item'),self._continuation(req.spreadsheet_id,'Unresolved Boundary Item'),self.sheets.rows(req.spreadsheet_id,'Asset QC'),self.sheets.rows(req.spreadsheet_id,'Itemization'),self.sheets.rows(req.spreadsheet_id,'Metadata References'))
        sources=select_authorized_sources(self.drive.enumerate(req.source_folder_id))
        if not sources:raise ValueError('Authorized folder contains no images')
        existing={r.get('Digital Asset') for r in self.sheets.rows(req.spreadsheet_id,'Asset QC')}
        if existing & {s.name for s in sources}:raise ValueError('Authorized sources already exist in Asset QC')
        folders=defaultdict(list)
        for s in sources:folders[s.folder_id].append(s)
        requested=req.batch_size or self.settings.batch_size
        # Register is authoritative; the index only resolves opaque IDs to sheet IDs.
        self.sheets.write(self.settings.job_index_spreadsheet_id,'HistoryIT Jobs',{'Job ID':job,'Spreadsheet ID':req.spreadsheet_id,'Project':req.project,'Source Folder ID':req.source_folder_id},('Job ID',job))
        self._append_batches(req.spreadsheet_id,job,req.project,req.box,folders,requested,'None',0,req.source_folder_id)
        return self.state(job)
    def state(self,job):
        sid,rows=self._job(job)
        pending=next((r for r in rows if r['Batch Status']!='Committed - Validated'),None)
        roots=[]
        for row in rows:
            root=self._batch_root(row)
            if root and root not in roots:roots.append(root)
        original=self._locator(job).get('Source Folder ID','')
        if original and original not in roots:roots.insert(0,original)
        return {'job_id':job,'project':rows[0]['Project'],'spreadsheet_id':sid,'source_folder_id':original,'authorized_source_folder_ids':roots,'batches':len(rows),'status':'Paused' if any(r['Batch Status']=='Paused' for r in rows) else ('Completed - Validated' if pending is None else pending['Batch Status']),'next_batch':pending['Batch ID'] if pending else None,'alert':self.alert(rows)}
    def authorize_folder(self,job,req):
        """Queue one explicitly authorized folder on an existing job. Do not start inspection."""
        sid,rows=self._job(job)
        if any(self._batch_root(row)==req.source_folder_id for row in rows):
            return self.state(job)
        thread=self.threads.get(job)
        if thread and thread.is_alive():
            raise ValueError('Job is still running. Authorize the next folder after it stops.')
        if any(row.get('Batch Status')!='Committed - Validated' for row in rows):
            raise ValueError('Existing batches are not Committed - Validated. Refusing to enumerate or queue the next folder.')
        sources=select_authorized_sources(self.drive.enumerate(req.source_folder_id))
        if not sources:raise ValueError('Authorized folder contains no images')
        known=set()
        for row in rows:known.update(self._manifest_names(row))
        known.update(r.get('Digital Asset') for r in self.sheets.rows(sid,'Asset QC'))
        names=[s.name for s in sources]
        overlap=sorted(set(names) & {n for n in known if n})
        if overlap:raise ValueError('Authorized folder repeats filenames already on this job: '+', '.join(overlap[:5]))
        if len(names)!=len(set(names)):raise ValueError('Authorized folder contains duplicate filenames')
        folders=defaultdict(list)
        for s in sources:folders[s.folder_id].append(s)
        last=rows[-1]
        prior=str(last.get('Resulting Open Item') or '').strip() or 'None'
        start=max(int(row['Batch ID'].rsplit('-B',1)[-1]) for row in rows)
        requested=req.batch_size or self.settings.batch_size
        self._append_batches(sid,job,rows[0]['Project'],last.get('Box') or 'Chapters Box 19',folders,requested,prior,start,req.source_folder_id)
        return self.state(job)
    @staticmethod
    def _batch_root(row):
        try:payload=json.loads(row.get('Notes') or '{}')
        except (TypeError,ValueError):return ''
        return str(payload.get('authorized_root') or payload.get('folder_id') or '')
    @staticmethod
    def _manifest_names(row):
        try:payload=json.loads(row.get('Notes') or '{}')
        except (TypeError,ValueError):return []
        return [item.get('name') for item in (payload.get('manifest') or []) if isinstance(item,dict)]
    def _append_batches(self,sid,job,project,box,folders,requested,prior_for_first,start_seq,authorized_root):
        seq=start_seq
        first=True
        for fid,group in folders.items():
            step=unit_size(requested,len(group))
            for start in range(0,len(group),step):
                chunk=group[start:start+step];seq+=1;bid=f'{job}-B{seq:04d}'
                prior=prior_for_first if first else ''
                first=False
                payload={'manifest':[x.model_dump() for x in chunk],'folder_id':fid,'authorized_root':authorized_root}
                note=json.dumps(payload,separators=(',',':'))
                if len(note)>45000:raise ValueError('Batch manifest exceeds one Sheet cell; lower BATCH_SIZE')
                self.sheets.write(sid,'Batch Queue',{'Project':project,'Job ID':job,'Batch ID':bid,'Box':box,'Source Group / Folder':chunk[0].folder_name,'Source Folder URL':'https://drive.google.com/drive/folders/'+fid,'Start Filename':chunk[0].name,'End Filename':chunk[-1].name,'Planned Asset Count':str(len(chunk)),'Batch Status':'Authorized / Pending Inspection','Prior Open Item':prior,'Resulting Open Item':'None','Inspection Attempt':'0','Commit Attempt':'0','Validation Status':'Not Run','Error / Retry Note':'','Next Batch Start':'','Notes':note,'Staging Schema':'Lean Split v2'})
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
    def reconcile(self, job):
        """Fill derived register fields for an already committed job. Does not inspect or authorize a folder."""
        sid, rows = self._job(job)
        from app.register_state import reconcile_register
        return reconcile_register(self.sheets, sid, job, rows)

    def _record_staging_ranges(self, sid, batch_id):
        from app.register_state import staging_ranges
        assets = [row for row in self.sheets.rows(sid, 'Asset Staging') if row.get('Batch ID') == batch_id]
        records = [row for row in self.sheets.rows(sid, 'Record Staging') if row.get('Batch ID') == batch_id]
        ranges = staging_ranges(assets, records)
        if ranges:
            self.sheets.write(sid, 'Batch Queue', ranges, ('Batch ID', batch_id))

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
        self._record_staging_ranges(sid, b['Batch ID'])
    def _write_stage_row(self, sid, sheet, row, batch_id, key):
        # The production adapter supports composite matching. Small in-memory
        # test doubles from the client package only support a two-item key.
        if sheet == 'Asset Staging':
            identity = [('Digital Asset', row[key])]
        else:
            identity = [('Batch ID', batch_id), ('Proposed Record ID', row[key])]
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
    def _continuation(self,sid,name):
        try:return self.sheets.field(sid,name)
        except (ValueError,KeyError,AttributeError):return 'None'
    def _safe_rows(self,sid,sheet):
        try:return self.sheets.rows(sid,sheet)
        except (ValueError,KeyError):return []
    def _block(self,sid,batch,exc,attempt):
        message=str(exc);path=''
        diag=getattr(self.inspector,'diagnostics',None)
        if diag is not None:
            try:path=str(diag.write(batch.get('Job ID') or '',batch.get('Batch ID') or '',int(attempt or 0),'batch_blocked',error=message,error_chain=exception_chain(exc),error_type=type(exc).__name__,success=False))
            except Exception:path=''
        suffix=f' [Diagnostic: {path}]' if path else ''
        note=(message[:max(0,300-len(suffix))]+suffix)[:300]
        self.sheets.write(sid,'Batch Queue',{'Batch Status':'Blocked - Human Review','Error / Retry Note':note},('Batch ID',batch['Batch ID']))
    def _authorize_following(self,sid,job,batch_id):
        rows=self._queue(sid,job)
        index=next(i for i,row in enumerate(rows) if row['Batch ID']==batch_id)
        if index+1>=len(rows) or rows[index].get('Batch Status')!='Committed - Validated':return
        nxt=rows[index+1]
        if str(nxt.get('Prior Open Item') or '').strip():return
        resulting=str(rows[index].get('Resulting Open Item') or '').strip() or 'None'
        self.sheets.write(sid,'Batch Queue',{'Prior Open Item':resulting},('Batch ID',nxt['Batch ID']))
    def _open_details(self,prior,items,refs,asset_qc):
        if prior=='None':return []
        rows=[r for r in items if r.get('Item ID')==prior]+[r for r in refs if r.get('Reference ID')==prior]
        if not rows:
            raise RegisterStateError(f'Prior Open Item {prior} has no Itemization or Metadata References row. Refusing to invent an open item.')
        names=[]
        for row in rows:names.extend(split_assets(row.get('Digital Assets')))
        linked=[r for r in asset_qc if r.get('Digital Asset') in set(names)]
        return {'records':rows,'committed_assets':linked}
    def _queue_prior(self,sid,rows,index,continuation,unresolved,asset_qc,items,refs):
        batch=rows[index]
        if index==0:
            return assert_batch_prior(batch.get('Prior Open Item'),continuation,unresolved,asset_qc,items,refs,first_batch=True)
        explicit=batch.get('Prior Open Item')
        if str(explicit or '').strip():
            return assert_batch_prior(explicit,'None','None',[],[],[],first_batch=False)
        previous=rows[index-1]
        if previous.get('Batch Status')!='Committed - Validated':
            raise BatchAuthorizationError('Previous batch is not Committed - Validated. Refusing to start the next batch.')
        resulting=str(previous.get('Resulting Open Item') or '').strip() or 'None'
        self.sheets.write(sid,'Batch Queue',{'Prior Open Item':resulting},('Batch ID',batch['Batch ID']))
        batch['Prior Open Item']=resulting
        return resulting, 'previous Batch Queue Resulting Open Item'
    def _inspection_inputs(self,sid,batch):
        # Run Context is read before any other register detail for this batch.
        run_context=self._safe_rows(sid,'Run Context')
        rows=self._queue(sid,batch['Job ID'])
        index=next(i for i,row in enumerate(rows) if row['Batch ID']==batch['Batch ID'])
        current=rows[index]
        continuation=self._continuation(sid,'Open Intellectual Item')
        unresolved=self._continuation(sid,'Unresolved Boundary Item')
        asset_qc=self.sheets.rows(sid,'Asset QC')
        items=self.sheets.rows(sid,'Itemization')
        refs=self.sheets.rows(sid,'Metadata References')
        prior,source=self._queue_prior(sid,rows,index,continuation,unresolved,asset_qc,items,refs)
        manifest=authorized_manifest(current)
        folder=folder_code(current)
        reference=resolve_reference(references_dir(self.settings),current.get('Project'),current.get('Box'),folder)
        details=self._open_details(prior,items,refs,asset_qc)
        rules=self._safe_rows(sid,'Rules')+self._safe_rows(sid,'Project Classification Rules')
        visible={k:v for k,v in current.items() if k!='_manifest'}
        context={
            'project':current.get('Project') or '',
            'folder_code':folder,
            'prior_open_item':prior,
            'prior_open_item_source':source,
            'run_context':run_context,
            'batch_queue_row':visible,
            'manifest':manifest,
            'existing_open_record':details if prior!='None' else [],
            'prior_open_item_context':details if prior!='None' else [],
            'approved_project_reference':reference,
            'authorized_project_state':{'project':current.get('Project') or '','box':current.get('Box') or '','folder':folder,'batch_status':current.get('Batch Status') or '','planned_asset_count':current.get('Planned Asset Count') or '','prior_open_item':prior,'prior_open_item_source':source},
            'inspection_attempt':0,
        }
        return [Source.model_validate(item) for item in manifest],context,rules,reference,prior
    def run(self,job):
        sid,rows=self._job(job)
        try:assert_single_active_batch(rows)
        except BatchAuthorizationError as exc:
            for row in rows:
                if row.get('Batch Status') in ('Inspection In Progress','Inspection Complete - Awaiting Commit','Commit Written - Pending Validation'):
                    self._block(sid,row,exc,0)
            return
        for b in rows:
            b=next(r for r in self._queue(sid,job) if r['Batch ID']==b['Batch ID'])
            status=b['Batch Status']
            if status=='Committed - Validated':
                self.committer.validate(sid,b,already=True);self._authorize_following(sid,job,b['Batch ID']);continue
            if status in ('Paused','Blocked - Human Review'):return
            if status in ('Inspection In Progress','Inspection Failed - Retryable','Commit Failed - Retryable'):
                if self._staging_ready(sid,b):status='Inspection Complete - Awaiting Commit'
                else:status='Authorized / Pending Inspection'
            if status=='Authorized / Pending Inspection':
                try:
                    sources,context,rules,reference,prior=self._inspection_inputs(sid,b)
                except (RegisterStateError,BatchAuthorizationError,ReferenceError) as exc:
                    self._block(sid,b,exc,int(float(b.get('Inspection Attempt') or 0)));return
                if self.cost(sid,job)>=self.settings.job_spending_limit_usd:
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Blocked - Human Review','Error / Retry Note':'Job spending limit reached'},('Batch ID',b['Batch ID']));return
                attempt=int(float(b.get('Inspection Attempt') or 0))+1
                if attempt>self.settings.max_retries:
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Blocked - Human Review','Error / Retry Note':'Inspection retry limit reached'},('Batch ID',b['Batch ID']));return
                self.sheets.write(sid,'Batch Queue',{'Batch Status':'Inspection In Progress','Inspection Attempt':str(attempt)},('Batch ID',b['Batch ID']))
                context['inspection_attempt']=attempt
                usage={}
                try:
                    answer_problems=reference_mismatches([],[],reference,[source.name for source in sources])
                    if answer_problems:
                        raise ReferenceError(answer_problems[0])
                    output,usage=self.inspector.inspect(job,b['Batch ID'],sources,context,rules)
                    problems=reference_mismatches(output.assets,output.records,reference,[source.name for source in sources])
                    if problems:
                        raise ReferenceError('Staging was not changed. Calibration conflict: '+'; '.join(problems))
                    if reference:output.records=order_records(output.records,output.assets,reference)
                    self._stage(sid,b,output,prior)
                    self._record_usage(sid,job,b,attempt,usage,True)
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Inspection Complete - Awaiting Commit','Inspection Date':time.strftime('%Y-%m-%d')},('Batch ID',b['Batch ID']))
                except (RegisterStateError,BatchAuthorizationError,ReferenceError) as exc:
                    self._block(sid,b,exc,attempt);return
                except CreditShortageError as exc:
                    diag=getattr(self.inspector,'diagnostics',None)
                    if diag is not None:
                        try:diag.write(job,b['Batch ID'],attempt,'credit_shortage',error=str(exc),error_chain=exception_chain(exc),success=False)
                        except Exception:pass
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
                self._authorize_following(sid,job,b['Batch ID'])
            except Exception as exc:
                latest=next(r for r in self._queue(sid,job) if r['Batch ID']==b['Batch ID'])
                if latest['Batch Status']!='Blocked - Human Review':
                    # Preserve Pending Validation when writes might have reached Sheets.
                    if latest['Batch Status']!='Commit Written - Pending Validation':
                        diag=getattr(self.inspector,'diagnostics',None)
                        note=str(exc)
                        if diag is not None:
                            try:path=diag.write(job,b['Batch ID'],int(float(b.get('Inspection Attempt') or 0)),'commit_failed',error=note,error_chain=exception_chain(exc),error_type=type(exc).__name__,batch_status=latest['Batch Status'],success=False);note=f'{note[:180]} [Diagnostic: {path}]'
                            except Exception:pass
                        self.sheets.write(sid,'Batch Queue',{'Batch Status':'Commit Failed - Retryable','Error / Retry Note':note[:300]},('Batch ID',b['Batch ID']))
                return
        self._close_folders(sid,job)
    def _close_folders(self,sid,job):
        groups=defaultdict(list)
        for b in self._queue(sid,job):groups[b['Source Group / Folder']].append(b)
        for folder,bs in groups.items():
            names=[s['name'] for b in bs for s in b['_manifest']]
            qc=[r for r in self.sheets.rows(sid,'Asset QC') if r['Digital Asset'] in names]
            # Source completion does not require Resulting Open Item or Continuation
            # State to be None. An intellectual item may stay open for the next folder.
            if all(b['Batch Status']=='Committed - Validated' for b in bs) and len(qc)==len(names) and {r['Digital Asset'] for r in qc}==set(names):
                for r in self.sheets.rows(sid,'Folder Processing Unit Summary'):
                    if r['Source Group / Folder']==folder and r.get('Notes','').startswith(job):
                        self.sheets.write(sid,'Folder Processing Unit Summary',{'Processing Status':'Completed - Validated','Completed Date':time.strftime('%Y-%m-%d')},('Processing Unit / Source Folder',r['Processing Unit / Source Folder']))
        from app.register_state import reconcile_register
        reconcile_register(self.sheets, sid, job, self._queue(sid, job))
