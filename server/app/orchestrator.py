from __future__ import annotations
import hashlib,json,threading,time
from decimal import Decimal
from collections import Counter, defaultdict
from app.adapters import SheetResizeError, sheet_resize_denied
from app.box_folders import flag_probable_duplicates, group_sources_by_folder, select_authorized_sources
from app.google_ids import ensure_drive_folder, ensure_spreadsheet
from app.models import Source
from app.commit import CommitService, folder_summary_key
from app.credits import CreditShortageError, credit_alert
from app.decisions import apply_governing_constraints
from app.diagnostics import exception_chain
from app.inspection import inspection_attempt_blocked, transient_inspection_failure
from app.identity import assign_identity, folder_code
from app.authorization import (
    BatchAuthorizationError, RegisterStateError, assert_batch_prior, assert_single_active_batch,
    authorized_manifest, initial_queue_prior,
)
from app.references import ReferenceError, order_records, reference_mismatches, references_dir, resolve_reference
from client_reference.commit_engine import CommitError, asset_identity, split_assets, validate_staging
from app.deployment_admission import DeploymentHoldError, enter_admission, leave_admission
from app.usage_accounting import (
    ADDED_USAGE_COLUMNS, UsageAccountingError, annotate_usage, billed_cost, parse_provider_usage,
    store_usage_row, usage_cost, usage_row, write_usage_outbox,
)

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
        self.committer=CommitService(sheets, getattr(inspector, 'diagnostics', None))
        self.threads={};self.lock=threading.RLock();self._pause_requested=set();self._deploy_hold=False;self._admission_depth=0
        self._active_usage={}
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
        # Canonical IDs are already normalized by JobCreate. Confirm type/access before any writes.
        ensure_drive_folder(self.drive, req.source_folder_id)
        ensure_spreadsheet(self.sheets, req.spreadsheet_id)
        self.sheets.ensure_tab(self.settings.job_index_spreadsheet_id,'HistoryIT Jobs',['Job ID','Spreadsheet ID','Project','Source Folder ID'])
        self.sheets.ensure_tab(req.spreadsheet_id,'OpenAI Usage',['Call ID','Project','Job ID','Batch ID','Model','Source Image Count','Input Tokens','Cached Input Tokens','Output Tokens','Estimated Cost USD','Attempt','Elapsed Seconds','Status','Cumulative Job Cost USD','Response ID',*ADDED_USAGE_COLUMNS])
        digest=hashlib.sha256((req.spreadsheet_id+'|'+req.source_folder_id+'|'+req.project).encode()).hexdigest()[:16]
        job='HIT-'+digest
        if any(r.get('Job ID')==job for r in self.sheets.rows(req.spreadsheet_id,'Batch Queue')):raise ValueError('Job already initialized; resume existing job')
        # A new job is a clean project start. Do not list Drive until that check passes,
        # so a later folder is not enumerated by a second create call.
        initial_queue_prior(self._continuation(req.spreadsheet_id,'Open Intellectual Item'),self._continuation(req.spreadsheet_id,'Unresolved Boundary Item'),self.sheets.rows(req.spreadsheet_id,'Asset QC'),self.sheets.rows(req.spreadsheet_id,'Itemization'),self.sheets.rows(req.spreadsheet_id,'Metadata References'))
        sources,skipped=self._discover(req.source_folder_id)
        if not sources:raise ValueError('No eligible source folder contains processable images')
        self._reject_known_assets(self.sheets.rows(req.spreadsheet_id,'Asset QC'), sources)
        folders=dict(group_sources_by_folder(sources))
        requested=req.batch_size or self.settings.batch_size
        # Register is authoritative; the index only resolves opaque IDs to sheet IDs.
        self.sheets.write(self.settings.job_index_spreadsheet_id,'HistoryIT Jobs',{'Job ID':job,'Spreadsheet ID':req.spreadsheet_id,'Project':req.project,'Source Folder ID':req.source_folder_id},('Job ID',job))
        self._record_skipped(req.spreadsheet_id,job,req.project,req.box,skipped)
        self._append_batches(req.spreadsheet_id,job,req.project,req.box,folders,requested,'None',0,req.source_folder_id)
        return self.state(job)
    def state(self,job):
        sid,rows=self._job(job)
        pending=next((r for r in rows if r['Batch Status']!='Committed - Validated'),None)
        roots=[]
        folders=[]
        for row in rows:
            root=self._batch_root(row)
            if root and root not in roots:roots.append(root)
            folder=str(row.get('Source Group / Folder') or '').strip()
            if folder and folder not in folders:folders.append(folder)
        original=self._locator(job).get('Source Folder ID','')
        if original and original not in roots:roots.insert(0,original)
        running=self._running(job)
        pause_requested=job in self._pause_requested or any(r['Batch Status']=='Paused' for r in rows)
        if pending is None:
            status='Completed - Validated'
        elif pause_requested and not running:
            status='Paused'
        elif running:
            status=self._running_status(pending)
        elif any(r['Batch Status']=='Paused' for r in rows):
            status='Paused'
        else:
            status=pending['Batch Status']
        # Commit Written - Pending Validation is never final PASS for the job.
        scope_complete=pending is None
        return {
            'job_id':job,
            'project':rows[0]['Project'],
            'spreadsheet_id':sid,
            'source_folder_id':original,
            'authorized_source_folder_ids':roots,
            'discovered_folders':folders,
            'batches':len(rows),
            'status':status,
            'next_batch':pending['Batch ID'] if pending else None,
            'current_folder':(pending or rows[-1]).get('Source Group / Folder') if rows else None,
            'current_batch':pending['Batch ID'] if pending else None,
            'phase':pending['Batch Status'] if pending else 'Completed - Validated',
            'running':running,
            'already_running':False,
            'pause_requested':pause_requested,
            'authorized_scope_complete':scope_complete,
            'alert':self.alert(rows),
        }
    def _running(self,job):
        thread=self.threads.get(job)
        return bool(thread and thread.is_alive())
    @staticmethod
    def _running_status(pending):
        phase=str(pending.get('Batch Status') or '')
        if phase=='Commit Written - Pending Validation':
            return 'Running - Validating'
        if phase=='Inspection Complete - Awaiting Commit':
            return 'Running - Committing'
        if phase=='Inspection In Progress':
            return 'Running - Inspecting'
        return 'Running'
    def authorize_folder(self,job,req):
        """Queue an explicitly authorized folder or parent box on an existing job. Do not start inspection.

        Parent IDs discover every immediate F* child not already on the frozen queue.
        Individual folder IDs remain supported. Scope expansion is always explicit.
        """
        sid,rows=self._job(job)
        # Idempotent re-post of an already-queued root does not require the new batches to finish first.
        if any(self._batch_root(row)==req.source_folder_id for row in rows):
            return self.state(job)
        thread=self.threads.get(job)
        if thread and thread.is_alive():
            raise ValueError('Job is still running. Authorize the next folder after it stops.')
        if any(row.get('Batch Status')!='Committed - Validated' for row in rows):
            raise ValueError('Existing batches are not Committed - Validated. Refusing to enumerate or queue the next folder.')
        ensure_drive_folder(self.drive, req.source_folder_id)
        sources,skipped=self._discover(req.source_folder_id)
        if not sources:raise ValueError('No eligible source folder contains processable images')
        known_folders=set()
        known_ids=set()
        for row in rows:
            for item in (json.loads(row.get('Notes') or '{}').get('manifest') or []):
                if isinstance(item,dict) and item.get('folder_id'):
                    known_folders.add(item['folder_id'])
                if isinstance(item,dict) and item.get('id'):
                    known_ids.add(item['id'])
        for row in self.sheets.rows(sid,'Asset QC'):
            if row.get('Drive File ID'):
                known_ids.add(row['Drive File ID'])
        new_sources=[s for s in sources if s.folder_id not in known_folders and s.id not in known_ids]
        box=rows[-1].get('Box') or rows[0].get('Box') or ''
        self._record_skipped(sid,job,rows[0]['Project'],box,skipped)
        if not new_sources:
            return self.state(job)
        ids=[s.id for s in new_sources]
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate Drive file ID in the authorized source set')
        folders=dict(group_sources_by_folder(new_sources))
        last=rows[-1]
        prior=str(last.get('Resulting Open Item') or '').strip() or 'None'
        start=max(int(row['Batch ID'].rsplit('-B',1)[-1]) for row in rows)
        requested=req.batch_size or self.settings.batch_size
        self._append_batches(sid,job,rows[0]['Project'],box,folders,requested,prior,start,req.source_folder_id)
        return self.state(job)
    def _discover(self, folder_id):
        discover=getattr(self.drive, 'discover', None)
        if discover is not None:
            sources, skipped = discover(folder_id)
        else:
            sources, skipped = self.drive.enumerate(folder_id), []
        sources=flag_probable_duplicates(select_authorized_sources(sources))
        ids=[source.id for source in sources]
        if len(ids)!=len(set(ids)):
            raise ValueError('Duplicate Drive file ID in the authorized source set')
        return sources, skipped
    def _reject_known_assets(self, rows, sources):
        known={asset_identity(row) for row in rows}
        fresh=[asset_identity({'id':source.id,'name':source.name,'folder_id':source.folder_id,'Digital Asset':source.name}) for source in sources]
        # A legacy Asset QC row is only the filename. Do not treat that filename as the new file's ID.
        overlap=sorted(set(fresh) & known)
        if overlap:
            raise ValueError('Authorized sources already exist in Asset QC')
    def _record_skipped(self, sid, job, project, box, skipped):
        if not skipped:
            return
        self.sheets.ensure_tab(sid, 'Job Events', ['Event ID', 'Job ID', 'Event', 'Folder', 'Folder ID', 'Detail'])
        for item in skipped:
            event_id=f"{job}|skipped|{item.get('folder_id') or item.get('folder_name')}"
            self.sheets.write(sid, 'Job Events', {
                'Event ID': event_id,
                'Job ID': job,
                'Event': 'Skipped / Empty',
                'Folder': item.get('folder_name') or '',
                'Folder ID': item.get('folder_id') or '',
                'Detail': item.get('detail') or 'Skipped / Empty',
            }, ('Event ID', event_id))
            folder=item.get('folder_name') or ''
            self.sheets.write(sid, 'Folder Processing Unit Summary', {
                'Project': project,
                'Box': box,
                'Processing Unit / Source Folder': folder,
                'Source Group / Folder': folder,
                'Authorization Status': 'Authorized',
                'Processing Status': 'Skipped / Empty',
                'Enumerated New Source Count': '0',
                'Processed New Source Count': '0',
                'Notes': item.get('detail') or 'Skipped / Empty',
            }, folder_summary_key(project, box, folder))
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
        items=folders.items() if isinstance(folders,dict) else folders
        for fid,group in items:
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
        """Admit at most one runner per job for this process. Repeated Start is a no-op.

        Single-worker deployment safety: admission is atomic under self.lock. This does
        not claim multi-process locking across separate API workers.
        A deployment hold refuses new runners. It does not pause, resume, or cancel a
        runner that has already been admitted.
        After a process restart, call Start/Resume again; durable Batch Queue state is
        resumable and previously validated batches are not reinspected.
        """
        with self.lock:
            if self._deploy_hold:
                raise DeploymentHoldError('Deployment hold is active; refusing to start a job')
            thread=self.threads.get(job)
            if thread and thread.is_alive():
                state=self.state(job)
                state['already_running']=True
                state['running']=True
                return state
            self._pause_requested.discard(job)
            t=threading.Thread(target=self.run,args=(job,),daemon=True,name='historyit-'+job)
            self.threads[job]=t;t.start()
        state=self.state(job)
        state['running']=True
        return state
    def deployment_status(self):
        from app.deployment_admission import status
        return status(self)
    def deployment_hold(self):
        from app.deployment_admission import acquire
        return acquire(self)
    def deployment_release(self):
        from app.deployment_admission import release
        return release(self)
    def pause(self,job):
        """Persist pause intent and stop at the next safe batch checkpoint.

        In-flight inspection/commit/validation for the current batch is not cancelled;
        later Authorized / retryable batches become Paused so the runner halts before
        starting them.
        """
        with self.lock:
            self._pause_requested.add(job)
        sid,rows=self._job(job)
        for row in rows:
            if row['Batch Status'] in ('Authorized / Pending Inspection','Inspection Failed - Retryable'):
                self.sheets.write(sid,'Batch Queue',{'Batch Status':'Paused'},('Batch ID',row['Batch ID']))
        return self.state(job)
    def resume(self,job):
        enter_admission(self)
        try:
            sid,rows=self._job(job)
            with self.lock:
                self._pause_requested.discard(job)
            for row in rows:
                if self._credit_blocked(row):
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Authorized / Pending Inspection','Error / Retry Note':''},('Batch ID',row['Batch ID']))
                elif row['Batch Status']=='Paused':
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Authorized / Pending Inspection'},('Batch ID',row['Batch ID']))
            return self.start(job)
        finally:
            leave_admission(self)
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
        enter_admission(self)
        try:
            return self._retry_unlocked(bid)
        finally:
            leave_admission(self)
    def _retry_unlocked(self,bid):
        for jobrow in self.sheets.rows(self.settings.job_index_spreadsheet_id,'HistoryIT Jobs'):
            sid=jobrow['Spreadsheet ID']
            matches=[r for r in self.sheets.rows(sid,'Batch Queue') if r['Batch ID']==bid]
            if matches:
                r=matches[0]
                if sheet_resize_denied(r.get('Error / Retry Note') or ''):
                    raise SheetResizeError(r.get('Error / Retry Note') or 'Service account lacks permission to resize the worksheet. This failure is not retryable.')
                if r['Batch Status'] not in ('Inspection Failed - Retryable','Commit Failed - Retryable','Blocked - Human Review'):
                    raise ValueError('Batch not retryable')
                resumed = {'Batch Status':'Inspection Complete - Awaiting Commit' if self._complete(sid,r) else 'Authorized / Pending Inspection','Validation Status':'Not Run'}
                if transient_inspection_failure(r.get('Error / Retry Note') or ''):
                    resumed['Inspection Attempt'] = '0'
                self.sheets.write(sid,'Batch Queue',resumed,('Batch ID',bid))
                return self.start(r['Job ID'])
        raise KeyError(bid)
    def _complete(self,sid,b):
        rows=[r for r in self.sheets.rows(sid,'Asset Staging') if r['Batch ID']==b['Batch ID']]
        expected=json.loads(b['Notes'])['manifest']
        if rows and all(row.get('Drive File ID') for row in rows) and all(item.get('id') for item in expected):
            same=Counter(asset_identity(row) for row in rows)==Counter(asset_identity(item) for item in expected)
        else:
            same=Counter(row.get('Digital Asset') for row in rows)==Counter(item.get('name') for item in expected)
        return len(rows)==len(expected) and same and all(r['Inspection Complete']=='Yes' for r in rows)
    def _usage(self,sid,job):
        return [r for r in self.sheets.rows(sid,'OpenAI Usage') if r['Job ID']==job]
    def cost(self,sid,job):return float(billed_cost(self._usage(sid,job)))
    def _bind_usage_recorder(self):
        if hasattr(self.inspector,'usage_recorder'):
            self.inspector.usage_recorder=self.record_provider_usage
    def record_provider_usage(self,record):
        """Store one provider response before validation, staging, or commit."""
        active=self._active_usage.get(record.get('job_id'))
        if not active:
            raise UsageAccountingError('A provider response arrived without an open batch')
        return self._persist_usage(active['sid'], active['batch'], record)
    def _persist_returned_usage(self,sid,batch,attempt,usage):
        if not usage or usage.get('usage_recorded'):
            return
        parts=usage.get('responses') or [usage]
        for part in parts:
            item=dict(part)
            item.setdefault('attempt', attempt)
            item.setdefault('part', 1)
            if not str(item.get('response_id') or '').strip():
                continue
            self._persist_usage(sid, batch, item)
    def _persist_usage(self,sid,batch,info):
        attempt=int(info.get('attempt') or 0)
        record=parse_provider_usage(
            info,
            response_id=str(info.get('response_id') or ''),
            attempt=attempt,
            part=int(info.get('part') or 1),
            status=str(info.get('status') or 'Received'),
            failure_reason=str(info.get('failure_reason') or ''),
        )
        pricing_error=None
        try:
            cost='' if record.get('usage_identity_gap') else usage_cost(record, self.settings)
        except UsageAccountingError as exc:
            cost=''
            pricing_error=exc
            record=dict(record)
            record['status']='Usage Accounting Failed'
            record['failure_reason']=str(exc)
        total=str(billed_cost(self._usage(sid, batch['Job ID'])) + (Decimal(0) if cost == '' else Decimal(cost)))
        row=usage_row(
            record,
            project=batch.get('Project') or '',
            job=batch.get('Job ID') or '',
            batch=batch.get('Batch ID') or '',
            model=getattr(self.settings, 'openai_model', '') or '',
            source_count=batch.get('Planned Asset Count') or '',
            elapsed=info.get('elapsed_seconds', 0),
            cost=cost,
            cumulative=total,
        )
        try:
            store_usage_row(self.sheets, sid, record, row)
        except Exception as exc:
            path=write_usage_outbox(getattr(self.settings, 'diagnostics_dir', None) or './logs', record, exc)
            if isinstance(exc, UsageAccountingError):
                raise UsageAccountingError(f'{exc} Outbox: {path}') from exc
            raise UsageAccountingError(f'OpenAI usage was not written to the register. Outbox: {path}') from exc
        if pricing_error is not None:
            raise pricing_error
        if record.get('usage_identity_gap'):
            raise UsageAccountingError(record['usage_identity_gap'])
        return {'estimated_cost_usd': cost}
    def _accounting_failure(self,sid,batch,exc):
        self.sheets.write(sid,'Batch Queue',{'Batch Status':'Inspection Failed - Retryable','Error / Retry Note':str(exc)[:300]},('Batch ID',batch['Batch ID']))
    def _annotate_responses(self,sid,usage,status,reason):
        if not usage:
            return
        parts=usage.get('responses') or [usage]
        for part in parts:
            response_id=str(part.get('response_id') or '').strip()
            if not response_id or ',' in response_id:
                continue
            annotate_usage(self.sheets, sid, response_id, status, reason)
    def _record_unbilled(self,sid,job,batch,attempt,status,reason):
        """A provider attempt that returned no usage. Token cells stay empty."""
        call_id=f'unbilled:{batch.get("Batch ID")}:A{int(attempt)}:{status}'
        row={
            'Call ID':call_id,'Project':batch.get('Project') or '','Job ID':job,'Batch ID':batch.get('Batch ID') or '',
            'Model':getattr(self.settings,'openai_model','') or '','Source Image Count':batch.get('Planned Asset Count') or '',
            'Estimated Cost USD':'','Attempt':str(attempt),'Part':'','Elapsed Seconds':'','Status':status,
            'Failure Reason':str(reason or '')[:300],'Billed':'No','Cumulative Job Cost USD':str(self.cost(sid,job)),
            'Response ID':'',
        }
        ensure=getattr(self.sheets,'ensure_columns',None)
        if callable(ensure):
            ensure(sid,'OpenAI Usage',list(ADDED_USAGE_COLUMNS))
        existing=[item for item in self.sheets.rows(sid,'OpenAI Usage') if str(item.get('Call ID') or '')==call_id]
        if existing:
            return
        self.sheets.write(sid,'OpenAI Usage',row,('Call ID',call_id))
    def _record_usage(self,sid,job,b,attempt,info,success,status=None):
        response_id=str((info or {}).get('response_id') or '').strip()
        if not response_id:
            if status=='Blocked - OpenAI Credits':
                self._record_unbilled(sid,job,b,attempt,status,status)
            return
        item=dict(info or {})
        item['attempt']=attempt
        item['status']=status or ('Success' if success else 'Failure')
        self._persist_usage(sid,b,item)
    def _stage(self,sid,b,output,prior='None'):
        manifest=json.loads(b['Notes'])['manifest']
        expected=[x['name'] for x in manifest]
        assets=[a.model_dump(by_alias=True) for a in output.assets];records=[r.model_dump(by_alias=True) for r in output.records]
        if [a['Digital Asset'] for a in assets]!=expected:raise ValueError('Staging filename/order mismatch')
        for asset, item in zip(assets, manifest):
            asset['Drive File ID']=item.get('id') or ''
            asset['Source Folder ID']=item.get('folder_id') or ''
            warning=item.get('duplicate_warning') or ''
            note=asset.get('Exception / Duplicate Note') or ''
            if warning and warning.casefold() not in note.casefold():
                asset['Exception / Duplicate Note']=f'{note} {warning}'.strip()
        if len({asset_identity(a) for a in assets})!=len(assets):raise ValueError('Duplicate staging key')
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
        if sheet == 'Asset Staging' and row.get('Drive File ID'):
            identity = [
                ('Drive File ID', row.get('Drive File ID')),
                ('Digital Asset', row.get('Digital Asset')),
                ('Source Folder ID', row.get('Source Folder ID') or ''),
            ]
        elif sheet == 'Asset Staging':
            identity = [('Digital Asset', row[key])]
        else:
            identity = [('Batch ID', batch_id), ('Proposed Record ID', row[key])]
        self.sheets.write(sid, sheet, row, identity)

    def _drop_stale(self,sid,b,assets,records):
        names={a['Digital Asset'] for a in assets}
        identities={asset_identity(a) for a in assets}
        ids={r['Proposed Record ID'] for r in records}
        for row in self.sheets.rows(sid,'Asset Staging'):
            if row.get('Batch ID')!=b['Batch ID']:
                continue
            stale=asset_identity(row) not in identities if row.get('Drive File ID') or any(a.get('Drive File ID') for a in assets) else row.get('Digital Asset') not in names
            if not stale:
                continue
            if row.get('Drive File ID'):
                self.sheets.clear(sid,'Asset Staging',row.get('_row'),[
                    ('Drive File ID', row.get('Drive File ID')),
                    ('Digital Asset', row.get('Digital Asset')),
                    ('Source Folder ID', row.get('Source Folder ID') or ''),
                ])
            else:
                self.sheets.clear(sid,'Asset Staging',row.get('_row'),('Digital Asset', row.get('Digital Asset')))
        for row in self.sheets.rows(sid,'Record Staging'):
            if row.get('Batch ID')==b['Batch ID'] and row.get('Proposed Record ID') not in ids:
                self.sheets.clear(sid,'Record Staging',row.get('_row'),('Proposed Record ID', row.get('Proposed Record ID')))
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
        """Backend-owned runner: recover pending commit/validation, then advance batches and folders.

        Does not require frontend polling. Stops only for pause, blocked review, inaccessible
        required material surfaced as errors, spending limit, retry exhaustion, or validation
        failure.         Ordinary Cropping/QC production tasks on committed records do not halt the box.
        """
        self._bind_usage_recorder()
        sid,rows=self._job(job)
        try:assert_single_active_batch(rows)
        except BatchAuthorizationError as exc:
            for row in rows:
                if row.get('Batch Status') in ('Inspection In Progress','Inspection Complete - Awaiting Commit','Commit Written - Pending Validation'):
                    self._block(sid,row,exc,0)
            return
        for b in rows:
            usage={}
            b=next(r for r in self._queue(sid,job) if r['Batch ID']==b['Batch ID'])
            status=b['Batch Status']
            if status=='Committed - Validated':
                # Repair staging statuses from durable evidence; never reinspect.
                self.committer.validate(sid,b,already=True);self._authorize_following(sid,job,b['Batch ID']);continue
            if status=='Blocked - Human Review':
                return
            if status=='Paused' or (job in self._pause_requested and status in ('Authorized / Pending Inspection','Inspection Failed - Retryable')):
                if status!='Paused':
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Paused'},('Batch ID',b['Batch ID']))
                return
            if status in ('Inspection In Progress','Inspection Failed - Retryable','Commit Failed - Retryable'):
                # Prefer durable staging over repeating an apparently failed inspection.
                if self._staging_ready(sid,b):status='Inspection Complete - Awaiting Commit'
                elif status=='Commit Failed - Retryable' and self._staging_ready(sid,b):
                    status='Inspection Complete - Awaiting Commit'
                else:status='Authorized / Pending Inspection'
            if status=='Authorized / Pending Inspection':
                if job in self._pause_requested:
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Paused'},('Batch ID',b['Batch ID']))
                    return
                try:
                    sources,context,rules,reference,prior=self._inspection_inputs(sid,b)
                except (RegisterStateError,BatchAuthorizationError,ReferenceError) as exc:
                    self._block(sid,b,exc,int(float(b.get('Inspection Attempt') or 0)));return
                if self.cost(sid,job)>=self.settings.job_spending_limit_usd:
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Blocked - Human Review','Error / Retry Note':'Job spending limit reached'},('Batch ID',b['Batch ID']));return
                attempt=int(float(b.get('Inspection Attempt') or 0))+1
                if inspection_attempt_blocked(attempt, self.settings.max_retries, b.get('Error / Retry Note') or ''):
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Blocked - Human Review','Error / Retry Note':'Inspection retry limit reached'},('Batch ID',b['Batch ID']));return
                self.sheets.write(sid,'Batch Queue',{'Batch Status':'Inspection In Progress','Inspection Attempt':str(attempt),'Error / Retry Note':''},('Batch ID',b['Batch ID']))
                context['inspection_attempt']=attempt
                usage={}
                try:
                    answer_problems=reference_mismatches([],[],reference,[source.name for source in sources])
                    if answer_problems:
                        raise ReferenceError(answer_problems[0])
                    self._active_usage[job]={'sid':sid,'batch':b}
                    try:
                        output,usage=self.inspector.inspect(job,b['Batch ID'],sources,context,rules)
                    finally:
                        self._active_usage.pop(job,None)
                    self._persist_returned_usage(sid,b,attempt,usage)
                    output=apply_governing_constraints(output)
                    problems=reference_mismatches(output.assets,output.records,reference,[source.name for source in sources])
                    if problems:
                        raise ReferenceError('Staging was not changed. Calibration conflict: '+'; '.join(problems))
                    if reference:output.records=order_records(output.records,output.assets,reference)
                    self._stage(sid,b,output,prior)
                    self._annotate_responses(sid,usage,'Success','')
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Inspection Complete - Awaiting Commit','Inspection Date':time.strftime('%Y-%m-%d')},('Batch ID',b['Batch ID']))
                except UsageAccountingError as exc:
                    self._accounting_failure(sid,b,exc);return
                except (RegisterStateError,BatchAuthorizationError,ReferenceError,SheetResizeError) as exc:
                    label='Blocked - Calibration' if isinstance(exc,ReferenceError) else 'Blocked - Sheets'
                    try:
                        self._annotate_responses(sid,usage,label,str(exc))
                    except UsageAccountingError as accounting:
                        self._accounting_failure(sid,b,accounting);return
                    self._block(sid,b,exc,attempt);return
                except CreditShortageError as exc:
                    diag=getattr(self.inspector,'diagnostics',None)
                    if diag is not None:
                        try:diag.write(job,b['Batch ID'],attempt,'credit_shortage',error=str(exc),error_chain=exception_chain(exc),success=False)
                        except Exception:pass
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Blocked - Human Review','Inspection Attempt':str(max(attempt-1,0)),'Error / Retry Note':credit_alert(job)},('Batch ID',b['Batch ID']))
                    self._record_unbilled(sid,job,b,attempt,'Blocked - OpenAI Credits',str(exc))
                    return
                except Exception as exc:
                    # Ambiguous failure: a complete readback is eligible for commit on resume.
                    info=dict(getattr(exc, 'inspection_usage', {}) or usage)
                    info.setdefault('elapsed_seconds',getattr(exc,'elapsed_seconds',0))
                    info.setdefault('attempt',attempt)
                    if str(info.get('response_id') or '').strip() and not info.get('usage_recorded'):
                        try:
                            self._persist_usage(sid,b,info)
                        except UsageAccountingError as accounting:
                            self._accounting_failure(sid,b,accounting);return
                    if str(info.get('response_id') or '').strip() and ',' not in str(info.get('response_id')):
                        try:
                            self._annotate_responses(sid,{'responses':[info]},'Validation Failed',str(exc))
                        except UsageAccountingError as accounting:
                            self._accounting_failure(sid,b,accounting);return
                    note=str(exc)
                    path=getattr(exc, 'diagnostics_path', '')
                    if path:note=f'{note[:180]} [Diagnostic: {path}]'
                    self.sheets.write(sid,'Batch Queue',{'Batch Status':'Inspection Failed - Retryable','Error / Retry Note':note[:300]},('Batch ID',b['Batch ID']))
                    return
            # Commit Written - Pending Validation and Awaiting Commit continue here without reinspection.
            try:
                b=next(r for r in self._queue(sid,job) if r['Batch ID']==b['Batch ID'])
                self.committer.commit(sid,b)
                self._authorize_following(sid,job,b['Batch ID'])
            except SheetResizeError as exc:
                if usage:
                    try:
                        self._annotate_responses(sid,usage,'Blocked - Sheets',str(exc))
                    except UsageAccountingError as accounting:
                        self._accounting_failure(sid,b,accounting);return
                self._block(sid,b,exc,int(float(b.get('Inspection Attempt') or 0)));return
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
        self._pause_requested.discard(job)
    @staticmethod
    def _same_folder_scope(row, project, box, folder):
        if (row.get('Project') or '') != project or (row.get('Box') or '') != box:
            return False
        group = row.get('Source Group / Folder') or row.get('Processing Unit / Source Folder') or ''
        return group == folder
    def _folder_summary_counts(self, sid, project, box, folder, names):
        """Replace the batch-sized summary with totals from committed rows for this folder.

        Counts are recomputed from authoritative detail rows, so a repeated close
        does not add the same assets again. Project, box, and folder all have to match.
        """
        wanted = set(names)
        def named(row):
            return set(split_assets(row.get('Digital Assets'))) & wanted
        def scoped(row):
            return self._same_folder_scope(row, project, box, folder)
        qc = [row for row in self.sheets.rows(sid, 'Asset QC') if row.get('Digital Asset') in wanted and scoped(row)]
        items = [row for row in self.sheets.rows(sid, 'Itemization') if scoped(row) and named(row)]
        refs = [row for row in self.sheets.rows(sid, 'Metadata References') if scoped(row) and named(row)]
        ready = {
            row.get('Item ID') for row in self.sheets.rows(sid, 'Ready for Metadata')
            if scoped(row) and row.get('Item ID') and named(row)
        }
        blocked = [row for row in items if str(row.get('Metadata Readiness') or '').startswith('Blocked')]
        tasks = [
            row for row in self.sheets.rows(sid, 'Production Queue')
            if scoped(row) and row.get('Digital Asset') in wanted
            and str(row.get('Task Status') or 'Open').strip() not in {'Closed', 'Cancelled', 'Canceled'}
        ]
        exceptions = sum(1 for row in qc if str(row.get('Exception / Duplicate Note') or '').strip())
        count = str(len(wanted))
        return {
            'Enumerated New Source Count': count,
            'Processed New Source Count': count,
            'Asset QC Rows': str(len(qc)),
            'Odyssey Metadata Records': str(len(items)),
            'Metadata Only References': str(len(refs)),
            'Ready Records': str(len(ready)),
            'Blocked Records': str(len(blocked)),
            'Production Tasks': str(len(tasks)),
            'Duplicate Exceptions': str(exceptions),
        }
    def _close_folders(self,sid,job):
        groups=defaultdict(list)
        for b in self._queue(sid,job):
            groups[(b.get('Project') or '', b.get('Box') or '', b.get('Source Group / Folder') or '')].append(b)
        for (project, box, folder), bs in groups.items():
            names=[s['name'] for b in bs for s in b['_manifest']]
            qc=[
                r for r in self.sheets.rows(sid,'Asset QC')
                if r.get('Digital Asset') in set(names) and self._same_folder_scope(r, project, box, folder)
            ]
            # Source completion does not require Resulting Open Item or Continuation
            # State to be None. An intellectual item may stay open for the next folder.
            if all(b['Batch Status']=='Committed - Validated' for b in bs) and len(qc)==len(names) and {r['Digital Asset'] for r in qc}==set(names):
                counts=self._folder_summary_counts(sid, project, box, folder, names)
                for r in self.sheets.rows(sid,'Folder Processing Unit Summary'):
                    if (r.get('Project'), r.get('Box'), r.get('Source Group / Folder')) != (project, box, folder):
                        continue
                    if not str(r.get('Notes') or '').startswith(job):
                        continue
                    unit=r.get('Processing Unit / Source Folder') or folder
                    self.sheets.write(sid,'Folder Processing Unit Summary',{'Processing Status':'Completed - Validated','Completed Date':time.strftime('%Y-%m-%d'),**counts},folder_summary_key(project, box, unit))
        from app.register_state import finalize_completed_box, reconcile_register
        queue=self._queue(sid, job)
        if queue:
            finalize_completed_box(self.sheets, sid, queue[0].get('Project') or '', queue[0].get('Box') or '', queue)
        reconcile_register(self.sheets, sid, job, queue)
