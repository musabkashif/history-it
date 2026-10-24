"""Deterministic register mutation and readback; deliberately has no Drive access."""
from __future__ import annotations
from collections import Counter
from datetime import date
from dataclasses import asdict
from client_reference.commit_engine import plan_commit, calculate_metrics, split_assets, asset_identity, CommitError
from app.vocabulary import unresolved_material_review

DETAIL=('Asset QC','Itemization','Metadata References','Production Queue','Ready for Metadata')

def folder_summary_key(project, box, unit):
    """One summary row per project, box, and folder. Folder code alone is not identity."""
    return [('Project', project), ('Box', box), ('Processing Unit / Source Folder', unit)]
KEYS={'Asset QC':'Digital Asset','Itemization':'Item ID','Metadata References':'Reference ID','Production Queue':'Task ID','Ready for Metadata':'Item ID'}
METRICS={'Source Assets':'source_assets','Odyssey Metadata Records':'odyssey_records','Metadata Only References':'metadata_references','Ready Records':'ready_records','Blocked - Cropping/QC':'blocked_crop_qc','Blocked - Additional Imaging':'blocked_additional_imaging','Review Records':'review_records','Content-Linked Assets':'content_linked_assets','Logical Components':'logical_components','Production Tasks':'production_tasks','Crop/QC Tasks':'crop_qc_tasks','Additional Imaging Tasks':'additional_imaging_tasks'}
BOX_METRICS=('Source Assets','Odyssey Metadata Records','Metadata Only References','Ready Records','Blocked - Cropping/QC','Blocked - Additional Imaging','Review Records','Content-Linked Assets','Logical Components','Production Tasks')

def scoped_tables(tables, *, names=None, project='', box=''):
    """Keep production rows for one batch, or for one box when names is omitted."""
    selected = set(names) if names is not None else None

    def project_box(row):
        row_project = (row.get('Project') or '').strip()
        row_box = (row.get('Box') or '').strip()
        if project and row_project and row_project != project:
            return False
        if box and row_box and row_box != box:
            return False
        return True

    def asset_ok(row):
        if not project_box(row):
            return False
        return selected is None or (row.get('Digital Asset') or '') in selected

    def record_ok(row):
        if not project_box(row):
            return False
        if selected is None:
            return True
        return bool(set(split_assets(row.get('Digital Assets'))) & selected)

    return {
        'Asset QC': [row for row in tables.get('Asset QC', []) if asset_ok(row)],
        'Production Queue': [row for row in tables.get('Production Queue', []) if asset_ok(row)],
        'Itemization': [row for row in tables.get('Itemization', []) if record_ok(row)],
        'Metadata References': [row for row in tables.get('Metadata References', []) if record_ok(row)],
        'Ready for Metadata': [row for row in tables.get('Ready for Metadata', []) if record_ok(row)],
    }

def scoped_metrics(tables, **kwargs):
    return calculate_metrics(scoped_tables(tables, **kwargs))

def metric_fields(metrics, labels=None):
    chosen = METRICS if labels is None else {label: METRICS[label] for label in labels}
    return {label: str(getattr(metrics, attr)) for label, attr in chosen.items()}

class CommitService:
    def __init__(self,sheets,diagnostics=None):
        self.sheets=sheets
        self.diagnostics=diagnostics
    def _log_validation(self,queue,event,**data):
        if self.diagnostics is None:return
        try:
            attempt=int(float(queue.get('Inspection Attempt') or 0))
        except (TypeError,ValueError):
            attempt=0
        try:
            self.diagnostics.write(queue.get('Job ID') or '',queue.get('Batch ID') or '',attempt,event,**data)
        except Exception:
            pass
    def tables(self,sid): return {name:self.sheets.rows(sid,name) for name in DETAIL}
    def _staging(self,sid,bid):
        a=[r for r in self.sheets.rows(sid,'Asset Staging') if r.get('Batch ID')==bid]
        r=[r for r in self.sheets.rows(sid,'Record Staging') if r.get('Batch ID')==bid]
        return a,r
    def _baseline(self,tables,assets,records):
        """Remove only this batch's already written creates for deterministic recovery."""
        names={a['Digital Asset'] for a in assets}
        identities={asset_identity(a) for a in assets}
        repeated={name for name in names if sum(a['Digital Asset']==name for a in assets)>1}
        def retired(row):
            if row.get('Drive File ID'):
                return asset_identity(row) in identities
            if row.get('Digital Asset') in repeated:
                return False
            return row.get('Digital Asset') in names
        ids={r['Proposed Record ID'] for r in records if r['Record Operation']=='Create New'}
        result={k:list(v) for k,v in tables.items()}
        result['Asset QC']=[r for r in result['Asset QC'] if not retired(r)]
        result['Production Queue']=[r for r in result['Production Queue'] if not retired(r)]
        for sheet,key in [('Itemization','Item ID'),('Metadata References','Reference ID'),('Ready for Metadata','Item ID')]:
            result[sheet]=[r for r in result[sheet] if r[key] not in ids]
        return result
    @staticmethod
    def same(actual,expected):
        return all(str(actual.get(k,''))==str(v) for k,v in expected.items())
    def _write_key(self,sheet,row):
        if sheet in ('Asset QC','Production Queue') and row.get('Drive File ID') and sheet=='Asset QC':
            return [
                ('Drive File ID', row.get('Drive File ID')),
                ('Digital Asset', row.get('Digital Asset')),
                ('Source Folder ID', row.get('Source Folder ID') or ''),
            ]
        key=KEYS[sheet]
        return (key, row[key])
    def _upsert_expected(self,sid,sheet,row):
        key=self._write_key(sheet,row)
        rows=self.sheets.rows(sid,sheet)
        if isinstance(key, list):
            found=[r for r in rows if all(str(r.get(name))==str(value) for name,value in key)]
            label='|'.join(str(value) for _,value in key)
        else:
            found=[r for r in rows if r.get(key[0])==row[key[0]]]
            label=row[key[0]]
        if len(found)>1:raise CommitError(f'Duplicate {sheet} key {label}')
        if found:
            if not self.same(found[0],row):raise CommitError(f'Conflicting {sheet} row {label}')
            return False
        self.sheets.write(sid,sheet,row,key);return True
    def _update_expected(self,sid,sheet,key,value,row):
        found=[r for r in self.sheets.rows(sid,sheet) if r.get(key)==value]
        if len(found)!=1:raise CommitError(f'Expected one {sheet} row: {value}')
        # Recovery may see the pre-commit or final state; other values are not guessed.
        self.sheets.write(sid,sheet,row,(key,value))
    def prepare(self,sid,queue):
        bid=queue['Batch ID'];assets,records=self._staging(sid,bid)
        actual=self.tables(sid)
        baseline=self._baseline(actual,assets,records) if queue['Batch Status']=='Commit Written - Pending Validation' else actual
        planning=dict(queue);planning['Batch Status']='Inspection Complete - Awaiting Commit'
        return plan_commit(planning,assets,records,baseline,date.today().strftime('%m/%d/%y')),assets,records,baseline
    def commit(self,sid,queue):
        if queue['Batch Status']=='Committed - Validated':
            return self.validate(sid,queue,already=True)
        plan,assets,records,before=self.prepare(sid,queue)
        # Persist recovery status before first production write.
        self.sheets.write(sid,'Batch Queue',{'Batch Status':'Commit Written - Pending Validation','Commit Attempt':str(int(float(queue.get('Commit Attempt') or 0))+1)},('Batch ID',queue['Batch ID']))
        for sheet,rows in plan.append_rows.items():
            for row in rows:self._upsert_expected(sid,sheet,row)
        for sheet,updates in plan.update_rows_by_key.items():
            for key,value,row in updates:self._update_expected(sid,sheet,key,value,row)
        return self.validate(sid,dict(queue,**{'Batch Status':'Commit Written - Pending Validation'}),plan,assets,records,before)
    def validate(self,sid,queue,plan=None,assets=None,records=None,before=None,already=False):
        bid=queue['Batch ID']
        # A committed batch stays committed. Later folders legally change continuation,
        # itemization, and ready rows, so replaying this batch's original plan against
        # the current register is not a reason to block it.
        if already:
            self._mark_staging_committed(sid, bid)
            return {'batch_id':bid,'status':'Committed - Validated','idempotent_noop':True}
        if assets is None:assets,records=self._staging(sid,bid)
        current=self.tables(sid)
        if before is None:before=self._baseline(current,assets,records)
        if plan is None:
            q=dict(queue);q['Batch Status']='Inspection Complete - Awaiting Commit'
            plan=plan_commit(q,assets,records,before,date.today().strftime('%m/%d/%y'))
        expected={k:list(v) for k,v in before.items()}
        for sheet,rows in plan.append_rows.items():expected[sheet].extend(rows)
        for sheet,updates in plan.update_rows_by_key.items():
            for key,value,row in updates:
                matches=[i for i,r in enumerate(expected[sheet]) if r.get(key)==value]
                if len(matches)!=1:raise CommitError(f'Update target {sheet}/{value} not unique')
                expected[sheet][matches[0]]=row
        manifest=queue.get('_manifest',[])
        names=[a['Digital Asset'] for a in assets]
        identified=bool(assets) and all(a.get('Drive File ID') for a in assets)
        if identified:
            staged_keys=[asset_identity(a) for a in assets]
            source_keys=[asset_identity(x) for x in manifest]
            qc=[r for r in current['Asset QC'] if asset_identity(r) in set(staged_keys)]
            qc_keys=[asset_identity(r) for r in qc]
        else:
            staged_keys=names
            source_keys=[x.get('name') for x in manifest]
            qc=[r for r in current['Asset QC'] if r.get('Digital Asset') in set(names)]
            qc_keys=[r.get('Digital Asset') for r in qc]
        checks={
            '01 Source reconciliation':len(names)==len(manifest)==int(float(queue['Planned Asset Count'])) and Counter(staged_keys)==Counter(source_keys),
            '02 Asset QC one per source':len(qc)==len(names) and Counter(qc_keys)==Counter(staged_keys),
            '03 Duplicate prevention':len({asset_identity(r) for r in current['Asset QC']})==len(current['Asset QC']),
            '04 Prior work protection':all(any(self.same(x,r) for x in current[sheet]) for sheet in DETAIL for r in before[sheet] if sheet not in ('Itemization','Ready for Metadata','Metadata References') or r.get(KEYS[sheet]) not in {x['Proposed Record ID'] for x in records}),
            '05 Scope and ZIP exclusion':all(x['name'].lower().endswith(('.jpg','.jpeg','.png','.tif','.tiff','.webp')) and not x['name'].lower().endswith('.zip') for x in manifest),
            '06 Filename integrity':Counter(names)==Counter(x.get('name') for x in manifest),
            '07 Itemization count integrity':all(len(split_assets(r['Digital Assets']))==int(float(r['Digital Asset Count'] or 0)) for r in current['Itemization']),
            '08 Odyssey reconciliation':self._equal(current['Itemization'],expected['Itemization'],'Item ID'),
            '09 Metadata reconciliation':self._equal(current['Metadata References'],expected['Metadata References'],'Reference ID'),
            '10 Production queue reconciliation':self._equal(current['Production Queue'],expected['Production Queue'],'Task ID'),
            '11 Ready reconciliation':self._equal(current['Ready for Metadata'],expected['Ready for Metadata'],'Item ID') and all(r['Metadata Readiness']=='Ready' for r in current['Ready for Metadata']),
            '12 Cumulative totals':asdict(calculate_metrics(current))==asdict(calculate_metrics(expected)),
            '13 Carry-forward tasks':all(any(x['Task ID']==r['Task ID'] and self.same(x,r) for x in current['Production Queue']) for r in before['Production Queue']),
            '14 Continuation state':not already or self.sheets.field(sid,'Open Intellectual Item')==plan.resulting_open_item,
            '15 Commit/readback':all(self._equal(current[s],expected[s],KEYS[s]) for s in DETAIL),
            '16 Unresolved classification review':not any(unresolved_material_review(record) for record in (records or [])),
        }
        if not all(checks.values()):
            failed=[k for k,v in checks.items() if not v]
            self._history(sid,bid,checks)
            self._log_validation(queue,'validation_failed',success=False,failed_checks=failed,checks=checks)
            self.sheets.write(sid,'Batch Queue',{'Batch Status':'Blocked - Human Review','Validation Status':'FAIL','Error / Retry Note':', '.join(failed)},('Batch ID',bid))
            raise CommitError('Validation failed: '+', '.join(failed))
        metrics=calculate_metrics(current)
        # Continuation fields and Batch Queue closeout must succeed before any PASS labels.
        self._apply_continuation(sid, queue, plan, metrics, assets)
        if self.sheets.field(sid,'Open Intellectual Item')!=plan.resulting_open_item:
            self._log_validation(queue,'validation_failed',success=False,failed_checks=['Continuation readback'],expected_open_item=plan.resulting_open_item)
            raise CommitError('Continuation readback mismatch')
        self.sheets.write(sid,'Batch Queue',{'Batch Status':'Committed - Validated','Resulting Open Item':plan.resulting_open_item,'Validation Status':'PASS','Commit Date':date.today().strftime('%m/%d/%y'),'Error / Retry Note':''},('Batch ID',bid))
        self._summaries(sid,queue,plan,metrics,assets)
        self._history(sid,bid,checks)
        # Staging Commit Status stays Pending Commit until readback validation succeeds.
        self._mark_staging_committed(sid, bid)
        return {'batch_id':bid,'status':'Committed - Validated','idempotent_noop':False,'checks':checks,'metrics':asdict(metrics)}

    def _mark_staging_committed(self, sid, bid):
        """Idempotently move this batch's staging rows off Pending Commit after validated commit."""
        for row in self.sheets.rows(sid, 'Asset Staging'):
            if row.get('Batch ID') != bid:
                continue
            status = str(row.get('Commit Status') or '').strip()
            if status == 'Committed - Validated':
                continue
            if status and status not in ('Pending Commit', 'Pending'):
                continue
            if row.get('Drive File ID'):
                staging_key = [
                    ('Drive File ID', row.get('Drive File ID')),
                    ('Digital Asset', row.get('Digital Asset')),
                    ('Source Folder ID', row.get('Source Folder ID') or ''),
                ]
            else:
                staging_key = ('Digital Asset', row.get('Digital Asset'))
            self.sheets.write(
                sid, 'Asset Staging',
                {'Commit Status': 'Committed - Validated', 'Commit Note': 'Committed - Validated'},
                staging_key,
            )
        for row in self.sheets.rows(sid, 'Record Staging'):
            if row.get('Batch ID') != bid:
                continue
            status = str(row.get('Commit Status') or '').strip()
            if status == 'Committed - Validated':
                continue
            if status and status not in ('Pending Commit', 'Pending'):
                continue
            self.sheets.write(
                sid, 'Record Staging',
                {'Commit Status': 'Committed - Validated', 'Commit Note': 'Committed - Validated'},
                [('Batch ID', bid), ('Proposed Record ID', row.get('Proposed Record ID'))],
            )

    @staticmethod
    def _equal(actual,expected,key):
        if len(actual)!=len(expected):return False
        a={r.get(key):r for r in actual};b={r.get(key):r for r in expected}
        return len(a)==len(actual) and len(b)==len(expected) and a.keys()==b.keys() and all(CommitService.same(a[k],v) for k,v in b.items())
    def _history(self,sid,bid,checks):
        for i,(name,ok) in enumerate(checks.items(),1):
            self.sheets.write(sid,'Validation',{'Check ID':f'{bid}-V{i:02d}','Scope':bid,'Requirement':name,'Method':'Deterministic staging/manifest/readback reconciliation','Result':'PASS' if ok else 'FAIL','Details':'' if ok else 'Mismatch; blocked','Last Checked':date.today().isoformat()},('Check ID',f'{bid}-V{i:02d}'))
    def _apply_continuation(self,sid,q,plan,m,a):
        group=q['Source Group / Folder']
        self.sheets.set_field(sid,'Open Intellectual Item',plan.resulting_open_item)
        self.sheets.set_field(sid,'Unresolved Boundary Item',plan.resulting_open_item)
        self.sheets.set_field(sid,'Last Source Filename Buffer',a[-1]['Digital Asset'])
        self.sheets.set_field(sid,'Open Production / QC Task Summary',f'{m.production_tasks} actionable production/QC tasks')
        self.sheets.set_field(sid,'Active Folder / Source Group',group)
        self.sheets.set_field(sid,'Last Updated',date.today().isoformat())

    def _summaries(self,sid,q,plan,m,a):
        project=q['Project'];box=q['Box'];group=q['Source Group / Folder'];bid=q['Batch ID'];unit=a[0]['Processing Unit / Source Folder']
        tables=self.tables(sid)
        names={row['Digital Asset'] for row in a}
        batch_metrics=metric_fields(scoped_metrics(tables, names=names, project=project, box=box))
        box_metrics=metric_fields(scoped_metrics(tables, project=project, box=box), BOX_METRICS)
        self.sheets.write(sid,'Project Register',{'Project':project,'Processing Unit / Source Folder':unit,'Status':'Committed - Validated','Box':box,'Source Group / Folder':group,**batch_metrics,'Open Item':plan.resulting_open_item,'Open Context':plan.resulting_open_item,'Committed Date':date.today().isoformat(),'Validation Result':'PASS','Notes':bid},('Notes',bid))
        self.sheets.write(sid,'Box Summary',{'Box':box,'Box Status':'In Progress',**box_metrics,'Open Item':plan.resulting_open_item,'Open Context':bid},('Box',box))
        self.sheets.write(sid,'Folder Processing Unit Summary',{'Project':project,'Processing Unit / Source Folder':unit,'Box':box,'Source Group / Folder':group,'Authorization Status':'Authorized','Processing Status':'In Progress','Enumerated New Source Count':str(len(a)),'Processed New Source Count':str(len(a)),'Asset QC Rows':str(len(a)),'Odyssey Metadata Records':str(plan.new_normal_record_count),'Metadata Only References':str(plan.new_reference_count),'Ready Records':str(plan.newly_ready_count),'Blocked Records':str(plan.blocked_record_count),'Production Tasks':str(plan.new_task_count),'Duplicate Exceptions':str(sum(bool(x.get('Exception / Duplicate Note')) for x in a)),'Open Item':plan.resulting_open_item,'Open Context':bid,'Validation Status':'PASS','Completed Date':date.today().isoformat(),'Notes':bid},folder_summary_key(project,box,unit))
        self._apply_continuation(sid,q,plan,m,a)
