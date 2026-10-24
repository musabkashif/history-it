"""Control-plane transitions.

Create, start, pause, resume, retry, and completion are idempotent.
None of these methods inspect images or write a Google Sheet. Resume of an
imported or historical job is refused. Completion without register or
validation evidence is refused.
"""
from __future__ import annotations

import uuid

from app.control_plane.clock import add_seconds, utcnow
from app.control_plane.errors import Conflict, LeaseHeld, NotAllowed, NotFound, WorkspaceIsolationError
from app.control_plane.identity import event_id_for, workspace_id_for
from app.control_plane.models import (
    BatchRecord,
    CheckpointRecord,
    EventRecord,
    FolderRecord,
    ImportPlan,
    JobRecord,
    JobSource,
    JobStatus,
    SnapshotRecord,
    WorkspaceRecord,
)


class ControlService:
    def __init__(self, store, environment: str = 'staging'):
        self.store = store
        self.environment = environment or 'staging'

    def create_workspace(
        self,
        *,
        project_name: str | None,
        client_organization: str | None = None,
        source_root_url: str | None = None,
        source_root_id: str | None = None,
        default_register_url: str | None = None,
        register_id: str | None = None,
        status: str = 'active',
        environment: str | None = None,
    ) -> dict:
        env = environment or self.environment
        workspace_id = workspace_id_for(project_name, env, None if project_name else register_id)
        return self._save_workspace(WorkspaceRecord(
            workspace_id=workspace_id,
            project_name=project_name,
            client_organization=client_organization,
            source_root_url=source_root_url,
            source_root_id=source_root_id,
            default_register_url=default_register_url,
            register_id=register_id,
            environment=env,
            status=status,
            created_at=utcnow(),
            updated_at=utcnow(),
        ))

    def create_job(
        self,
        *,
        job_id: str,
        workspace_id: str,
        project_name: str | None,
        box_label: str | None = None,
        parent_folder_url: str | None = None,
        parent_folder_id: str | None = None,
        register_url: str | None = None,
        register_id: str | None = None,
        batch_size: int | None = None,
        source: str = 'live',
        job_status: str = 'queued',
        created_by: str | None = None,
        deployed_release: str | None = None,
        correlation_id: str | None = None,
        actor: str | None = None,
    ) -> dict:
        if self.store.get_workspace(workspace_id) is None:
            raise NotFound(workspace_id)
        existing = self.store.get_job(job_id)
        desired = {
            'workspace_id': workspace_id,
            'project_name': project_name,
            'box_label': box_label,
            'register_id': register_id,
            'source': source,
        }
        if existing:
            if existing['workspace_id'] != workspace_id:
                raise WorkspaceIsolationError(job_id)
            if any(existing.get(key) != value for key, value in desired.items()):
                raise Conflict(f'Job {job_id} already exists with different workspace data.')
            return self.detail(existing)
        now = utcnow()
        record = JobRecord(
            job_id=job_id,
            workspace_id=workspace_id,
            project_name=project_name,
            box_label=box_label,
            parent_folder_url=parent_folder_url,
            parent_folder_id=parent_folder_id,
            register_url=register_url,
            register_id=register_id,
            batch_size=batch_size,
            job_status=JobStatus(job_status),
            source=JobSource(source),
            summary_origin='unknown',
            created_by=created_by or actor,
            created_at=now,
            updated_at=now,
            deployed_release=deployed_release,
        )
        stored = self.store.insert_job(record.model_dump(mode='json'))
        self._event(stored, 'created', None, stored['job_status'], 'Job recorded.', actor or created_by, correlation_id, None, None)
        self._checkpoint(stored, 'create', None, {'job_status': stored['job_status']}, None, None)
        return self.detail(stored)

    def add_folder(self, *, workspace_id: str, job_id: str, folder_id: str, folder_name: str | None = None,
                   folder_url: str | None = None, box_label: str | None = None, discovery_order: int | None = None,
                   authorization_status: str | None = None, processing_status: str | None = None,
                   asset_count: int | None = None, folder_identifier_source: str = 'drive_url') -> dict:
        job = self.require_job(job_id, workspace_id)
        record = FolderRecord(
            workspace_id=job['workspace_id'],
            job_id=job_id,
            folder_id=folder_id,
            folder_url=folder_url,
            folder_name=folder_name,
            folder_identifier_source=folder_identifier_source,  # type: ignore[arg-type]
            box_label=box_label if box_label is not None else job.get('box_label'),
            discovery_order=discovery_order,
            authorization_status=authorization_status,
            processing_status=processing_status,
            asset_count=asset_count,
        )
        self.store.upsert_folder(record.model_dump(mode='json'))
        return self.detail(self.require_job(job_id, workspace_id))

    def add_batch(self, *, workspace_id: str, job_id: str, folder_id: str, batch_id: str,
                  batch_number: int | None = None, asset_count: int | None = None,
                  start_filename: str | None = None, end_filename: str | None = None,
                  status: str | None = None, attempt_count: int | None = None,
                  validation_status: str | None = None, commit_status: str | None = None,
                  prior_open_item: str | None = None, resulting_open_item: str | None = None,
                  error_information: str | None = None) -> dict:
        job = self.require_job(job_id, workspace_id)
        now = utcnow()
        record = BatchRecord(
            workspace_id=job['workspace_id'],
            job_id=job_id,
            folder_id=folder_id,
            batch_id=batch_id,
            batch_number=batch_number,
            asset_count=asset_count,
            start_filename=start_filename,
            end_filename=end_filename,
            status=status,
            attempt_count=attempt_count,
            validation_status=validation_status,
            commit_status=commit_status,
            prior_open_item=prior_open_item,
            resulting_open_item=resulting_open_item,
            error_information=error_information,
            updated_at=now,
        )
        payload = record.model_dump(mode='json')
        payload.pop('lease_owner', None)
        payload.pop('lease_until', None)
        self.store.upsert_batch(payload)
        return self.detail(self.require_job(job_id, workspace_id))

    def start_job(self, job_id: str, workspace_id: str | None = None, *, actor: str | None = None,
                  correlation_id: str | None = None) -> dict:
        job = self.require_job(job_id, workspace_id)
        self._require_live(job, 'started')
        if job['job_status'] in ('running', 'completed', 'validated'):
            return self.detail(job)
        if job['job_status'] not in ('queued', 'paused', 'failed'):
            raise NotAllowed(f'Start is not available from {job["job_status"]}.')
        previous = job['job_status']
        fields = {'job_status': 'running', 'updated_at': utcnow()}
        if not job.get('started_at'):
            fields['started_at'] = fields['updated_at']
        updated = self._save_job(job, fields)
        self._event(updated, 'started', previous, 'running', 'Start recorded. Inspection was not started by the control plane.', actor, correlation_id, None, None)
        self._checkpoint(updated, 'start', None, {'job_status': 'running'}, None, None)
        return self.detail(updated)

    def pause_job(self, job_id: str, workspace_id: str | None = None, *, actor: str | None = None,
                  correlation_id: str | None = None, reason: str = 'operator') -> dict:
        job = self.require_job(job_id, workspace_id)
        if job['job_status'] == 'paused':
            return self.detail(job)
        if reason != 'lease_expired':
            self._require_live(job, 'paused')
        if job['job_status'] not in ('queued', 'running', 'failed'):
            raise NotAllowed(f'Pause is not available from {job["job_status"]}.')
        previous = job['job_status']
        updated = self._save_job(job, {'job_status': 'paused', 'updated_at': utcnow()})
        self._event(updated, 'paused', previous, 'paused', 'Pause recorded.', actor, correlation_id, None, None)
        self._checkpoint(
            updated, 'pause',
            {'paused': True, 'reason': reason},
            {'job_status': 'paused', 'resume_from': previous},
            None, None,
        )
        return self.detail(updated)

    def resume_job(self, job_id: str, workspace_id: str | None = None, *, actor: str | None = None,
                   correlation_id: str | None = None) -> dict:
        job = self.require_job(job_id, workspace_id)
        self._require_live(job, 'resumed')
        if job['job_status'] == 'running':
            return self.detail(job)
        if job['job_status'] not in ('paused', 'failed'):
            raise NotAllowed(f'Resume is not available from {job["job_status"]}.')
        previous = job['job_status']
        updated = self._save_job(job, {'job_status': 'running', 'updated_at': utcnow(), 'last_error': None})
        self._event(updated, 'resumed', previous, 'running', 'Resume recorded. Inspection was not started by the control plane.', actor, correlation_id, None, None)
        self._checkpoint(updated, 'resume', None, {'job_status': 'running', 'resumed_from': previous}, None, None)
        return self.detail(updated)

    def retry_batch(self, job_id: str, batch_id: str, workspace_id: str | None = None, *, actor: str | None = None,
                    correlation_id: str | None = None) -> dict:
        job = self.require_job(job_id, workspace_id)
        self._require_live(job, 'retried')
        batch = self.store.get_batch(job_id, batch_id)
        if batch is None or batch.get('workspace_id') != job['workspace_id']:
            raise NotFound(batch_id)
        if job['job_status'] == 'queued' and self._already(job, 'retried', correlation_id):
            return self.detail(job)
        if job['job_status'] != 'failed' and not _failed_batch(batch):
            raise NotAllowed('Retry is available after a failed batch.')
        attempt = batch.get('attempt_count')
        fields = {
            'status': 'queued',
            'attempt_count': (attempt + 1) if isinstance(attempt, int) else 1,
            'updated_at': utcnow(),
            'error_information': None,
        }
        updated_batch = self.store.update_batch(job_id, batch_id, batch['version'], fields)
        if updated_batch is None:
            raise Conflict('Batch was updated by another worker.')
        updated = self._save_job(self.require_job(job_id, job['workspace_id']), {
            'job_status': 'queued',
            'updated_at': utcnow(),
            'last_error': None,
        })
        self._event(updated, 'retried', job['job_status'], 'queued', 'Retry recorded. Inspection was not started by the control plane.', actor, correlation_id, batch.get('folder_id'), batch_id)
        self._checkpoint(updated, 'retry', None, {'job_status': 'queued', 'batch_id': batch_id}, batch.get('folder_id'), batch_id)
        return self.detail(updated)

    def complete_job(self, job_id: str, workspace_id: str | None = None, *, evidence: str | None,
                     actor: str | None = None, correlation_id: str | None = None,
                     completed_at: str | None = None) -> dict:
        job = self.require_job(job_id, workspace_id)
        if job['job_status'] == 'completed':
            return self.detail(job)
        if not evidence:
            raise NotAllowed('Completion requires register or validation evidence.')
        if job['job_status'] in ('blocked', 'failed'):
            raise NotAllowed(f'Completion is not available from {job["job_status"]}.')
        previous = job['job_status']
        updated = self._save_job(job, {
            'job_status': 'completed',
            'completion_evidence': evidence,
            'completed_at': completed_at,
            'updated_at': utcnow(),
            'summary_origin': job.get('summary_origin') or 'register',
        })
        self._event(updated, 'completed', previous, 'completed', 'Completion recorded from register evidence.', actor, correlation_id, None, None)
        self._checkpoint(updated, 'complete', None, None, None, None)
        return self.detail(updated)

    def reopen_job(self, job_id: str, workspace_id: str | None = None, *, actor: str | None = None,
                   correlation_id: str | None = None) -> dict:
        job = self.require_job(job_id, workspace_id)
        if self._already(job, 'reopened', correlation_id) or self._last_event_type(job) == 'reopened':
            detail = self.detail(job)
            detail['already_applied'] = True
            return detail
        self._event(job, 'reopened', job['job_status'], job['job_status'], 'Job reopened. Inspection was not started.', actor, correlation_id, None, None)
        detail = self.detail(self.require_job(job_id, job['workspace_id']))
        detail['already_applied'] = False
        return detail

    def claim_batch(self, *, workspace_id: str, job_id: str, batch_id: str, worker_id: str,
                    now: str | None = None, ttl_seconds: int = 120) -> dict:
        now_s = now or utcnow()
        job = self.require_job(job_id, workspace_id)
        batch = self.store.get_batch(job_id, batch_id)
        if batch is None or batch.get('workspace_id') != job['workspace_id']:
            raise NotFound(batch_id)
        owner = batch.get('lease_owner')
        until = batch.get('lease_until')
        if owner and owner != worker_id and until and until > now_s:
            raise LeaseHeld(batch_id)
        updated = self.store.update_batch(job_id, batch_id, batch['version'], {
            'lease_owner': worker_id,
            'lease_until': add_seconds(now_s, ttl_seconds),
            'updated_at': now_s,
        })
        if updated is None:
            raise Conflict('Batch was updated by another worker.')
        return updated

    def recover_job(self, job_id: str, workspace_id: str | None = None, *, now: str | None = None,
                    actor: str | None = None) -> dict:
        """Pause a running job whose batch lease has expired. Does not resume or inspect."""
        now_s = now or utcnow()
        job = self.require_job(job_id, workspace_id)
        batches = self.store.list_batches(job['job_id'], job['workspace_id'])
        expired = [
            batch for batch in batches
            if batch.get('lease_owner') and (not batch.get('lease_until') or batch['lease_until'] <= now_s)
        ]
        if not expired:
            return self.detail(job)
        for batch in expired:
            self.store.update_batch(job['job_id'], batch['batch_id'], batch['version'], {
                'lease_owner': None,
                'lease_until': None,
                'updated_at': now_s,
            })
        job = self.require_job(job_id, job['workspace_id'])
        if job['job_status'] == 'paused':
            return self.detail(job)
        if job['job_status'] not in ('running', 'queued'):
            return self.detail(job)
        previous = job['job_status']
        updated = self._save_job(job, {'job_status': 'paused', 'updated_at': now_s})
        self._event(updated, 'lease_expired', previous, 'paused', 'Batch lease expired. The job was paused for a safe resume.', actor, None, expired[0].get('folder_id'), expired[0]['batch_id'])
        self._checkpoint(
            updated, 'recover',
            {'paused': True, 'reason': 'lease_expired'},
            {'job_status': 'paused', 'resume_from': previous, 'batch_id': expired[0]['batch_id']},
            expired[0].get('folder_id'), expired[0]['batch_id'],
        )
        return self.detail(updated)

    def import_record(self, plan: ImportPlan, *, actor: str | None = None, correlation_id: str | None = None) -> dict:
        if plan.inspection_started:
            raise NotAllowed('Import cannot start inspection.')
        if plan.summary_origin == 'operator_notes':
            plan = plan.model_copy(update={'job_status': JobStatus.unknown, 'completion_evidence': None, 'completed_at': None})
        if plan.job_status == JobStatus.completed and not plan.completion_evidence:
            raise NotAllowed('Completion requires register or validation evidence.')
        existing = self.store.get_job(plan.job_id)
        if existing and existing.get('project_name') and plan.project_name:
            if existing['project_name'].casefold() != plan.project_name.casefold():
                raise WorkspaceIsolationError(plan.job_id)
        if existing and existing.get('source') == 'live' and plan.source != JobSource.live:
            plan = plan.model_copy(update={'source': JobSource.live})
        project = plan.project_name or (existing or {}).get('project_name')
        workspace = self._workspace_for_import(plan, project)
        if existing and existing['workspace_id'] != workspace['workspace_id']:
            scoped = workspace_id_for(None, self.environment, plan.job_id)
            if existing['workspace_id'] == scoped and self.store.job_count_in_workspace(existing['workspace_id']) == 1:
                self.store.move_job(plan.job_id, workspace['workspace_id'])
                existing = self.store.get_job(plan.job_id)
            else:
                raise WorkspaceIsolationError(plan.job_id)
        if existing and self._same_import(existing, plan, workspace['workspace_id']):
            detail = self.detail(existing)
            detail['already_applied'] = True
            detail['warnings'] = plan.warnings
            return detail
        now = utcnow()
        previous = existing.get('job_status') if existing else None
        status = plan.job_status
        if existing and plan.summary_origin == 'operator_notes':
            status = JobStatus(existing['job_status'])
        elif existing and plan.job_status == JobStatus.unknown and existing['job_status'] != 'unknown':
            status = JobStatus(existing['job_status'])
        counts = plan.summary_counts.model_dump()
        if existing and plan.summary_origin != 'register':
            counts = _fill_counts(existing.get('summary_counts') or {}, counts)
        fields = {
            'workspace_id': workspace['workspace_id'],
            'project_name': _prefer(plan.project_name, (existing or {}).get('project_name')),
            'box_label': _prefer(plan.box_label, (existing or {}).get('box_label')),
            'parent_folder_url': _prefer(plan.parent_folder_url, (existing or {}).get('parent_folder_url')),
            'parent_folder_id': _prefer(plan.parent_folder_id, (existing or {}).get('parent_folder_id')),
            'register_url': _prefer(plan.register_url, (existing or {}).get('register_url')),
            'register_id': _prefer(plan.register_id, (existing or {}).get('register_id')),
            'register_file_reference': _prefer(plan.register_file_reference, (existing or {}).get('register_file_reference')),
            'batch_size': plan.batch_size if plan.batch_size is not None else (existing or {}).get('batch_size'),
            'job_status': status.value,
            'sheets_status': _prefer(plan.sheets_status, (existing or {}).get('sheets_status')),
            'source': plan.source.value,
            'summary_origin': 'register' if plan.summary_origin == 'register' else (existing or {}).get('summary_origin') or plan.summary_origin,
            'deployed_release': _prefer(plan.deployed_release, (existing or {}).get('deployed_release')),
            'summary_counts': counts,
            'last_error': plan.last_error if plan.last_error is not None else (existing or {}).get('last_error'),
            'completion_evidence': plan.completion_evidence if plan.summary_origin == 'register' else (existing or {}).get('completion_evidence'),
            'completed_at': plan.completed_at if status == JobStatus.completed else (existing or {}).get('completed_at'),
            'updated_at': now,
        }
        if status != JobStatus.completed:
            fields['completed_at'] = (existing or {}).get('completed_at') if existing and existing.get('job_status') == 'completed' else None
        if existing:
            stored = self._save_job(existing, fields)
        else:
            record = JobRecord(
                job_id=plan.job_id,
                created_at=now,
                created_by=actor,
                job_status=status,
                source=plan.source,
                **{key: value for key, value in fields.items() if key not in ('job_status', 'source')},
            )
            stored = self.store.insert_job(record.model_dump(mode='json'))
        for folder in plan.folders:
            payload = FolderRecord(
                workspace_id=stored['workspace_id'],
                job_id=plan.job_id,
                **folder.model_dump(),
            ).model_dump(mode='json')
            self.store.upsert_folder(payload)
        for batch in plan.batches:
            payload = BatchRecord(
                workspace_id=stored['workspace_id'],
                job_id=plan.job_id,
                updated_at=now,
                **batch.model_dump(),
            ).model_dump(mode='json')
            payload.pop('lease_owner', None)
            payload.pop('lease_until', None)
            self.store.upsert_batch(payload)
        if plan.snapshot is not None:
            snapshot = SnapshotRecord(
                snapshot_id='snap-' + uuid.uuid4().hex[:16],
                job_id=plan.job_id,
                workspace_id=stored['workspace_id'],
                captured_at=now,
                **plan.snapshot.model_dump(),
            )
            self.store.insert_snapshot(snapshot.model_dump(mode='json'))
        message = plan.message or 'Historical record imported. Inspection was not started.'
        if plan.warnings:
            message = message + ' ' + ' '.join(plan.warnings)
        self._event(stored, 'imported', previous, stored['job_status'], message[:500], actor, correlation_id, None, None)
        detail = self.detail(self.require_job(plan.job_id, stored['workspace_id']))
        detail['already_applied'] = False
        detail['warnings'] = plan.warnings
        return detail

    def list_workspaces(self) -> list[dict]:
        rows = self.store.list_workspaces()
        rows.sort(key=lambda row: ((row.get('project_name') or '').casefold(), row.get('workspace_id') or ''))
        return rows

    def list_jobs(self, *, workspace_id: str | None = None, project: str | None = None, box: str | None = None,
                  status: str | None = None, source: str | None = None, created_from: str | None = None,
                  created_to: str | None = None) -> list[dict]:
        rows = [self._present(row) for row in self.store.list_jobs(workspace_id)]
        project_q = (project or '').strip().casefold()
        box_q = (box or '').strip().casefold()
        if project_q:
            rows = [row for row in rows if project_q in (row.get('project_name') or '').casefold()]
        if box_q:
            rows = [row for row in rows if box_q in (row.get('box_label') or '').casefold()]
        if status:
            rows = [row for row in rows if row.get('job_status') == status]
        if source:
            rows = [row for row in rows if row.get('source') == source]
        if created_from:
            rows = [row for row in rows if (row.get('created_at') or '') >= created_from]
        if created_to:
            end = created_to if 'T' in created_to else created_to + 'T23:59:59Z'
            rows = [row for row in rows if (row.get('created_at') or '') <= end]
        rows.sort(key=lambda row: row.get('updated_at') or '', reverse=True)
        return rows[:500]

    def get_detail(self, job_id: str, workspace_id: str | None = None) -> dict:
        return self.detail(self.require_job(job_id, workspace_id))

    def require_job(self, job_id: str, workspace_id: str | None) -> dict:
        job = self.store.get_job(job_id)
        if job is None:
            raise NotFound(job_id)
        if workspace_id and job.get('workspace_id') != workspace_id:
            raise WorkspaceIsolationError(job_id)
        return job

    def detail(self, job: dict) -> dict:
        workspace_id = job['workspace_id']
        job_id = job['job_id']
        folders = self.store.list_folders(job_id, workspace_id)
        batches = self.store.list_batches(job_id, workspace_id)
        checkpoints = self.store.list_checkpoints(job_id, workspace_id)
        events = self.store.list_events(job_id, workspace_id)
        snapshots = self.store.list_snapshots(job_id, workspace_id)
        validation = []
        if snapshots:
            validation = list(snapshots[-1].get('validation_results') or [])
        errors = []
        if job.get('last_error'):
            errors.append({'source': 'job', 'message': job['last_error']})
        for batch in batches:
            if batch.get('error_information'):
                errors.append({'source': 'batch', 'batch_id': batch['batch_id'], 'message': batch['error_information']})
        for event in events:
            if event.get('event_type') == 'error' and event.get('message'):
                errors.append({'source': 'event', 'message': event['message'], 'timestamp': event.get('timestamp')})
        presented = self._present(job)
        return {
            'inspection_started': False,
            'job': presented,
            'workspace': self.store.get_workspace(workspace_id),
            'folders': folders,
            'batches': batches,
            'checkpoints': checkpoints,
            'events': events,
            'snapshots': snapshots,
            'validation_results': validation,
            'errors': errors,
            'retries': [event for event in events if event.get('event_type') == 'retried'],
            'register': {
                'register_url': presented.get('register_url'),
                'register_id': presented.get('register_id'),
                'register_file_reference': presented.get('register_file_reference'),
            },
        }

    def _present(self, job: dict) -> dict:
        presented = dict(job)
        source = presented.get('source')
        status = presented.get('job_status')
        live = source == 'live'
        presented.update({
            'reopen_allowed': True,
            'sheets_reopen_allowed': live,
            'resume_allowed': live and status in ('paused', 'failed'),
            'pause_allowed': live and status in ('queued', 'running'),
            'retry_allowed': bool(live and status == 'failed'),
            'start_allowed': live and status == 'queued',
        })
        return presented

    def _save_workspace(self, record: WorkspaceRecord) -> dict:
        current = self.store.get_workspace(record.workspace_id)
        payload = record.model_dump(mode='json')
        if current:
            for key, value in current.items():
                if payload.get(key) in (None, '') and value not in (None, ''):
                    payload[key] = value
            if current.get('status') == 'active':
                payload['status'] = 'active'
            payload['created_at'] = current.get('created_at') or payload['created_at']
        return self.store.upsert_workspace(payload)

    def _workspace_for_import(self, plan: ImportPlan, project: str | None) -> dict:
        if project:
            workspace_id = workspace_id_for(project, self.environment)
        else:
            workspace_id = workspace_id_for(None, self.environment, plan.job_id)
        status = 'active' if plan.source == JobSource.live else 'unknown'
        return self._save_workspace(WorkspaceRecord(
            workspace_id=workspace_id,
            project_name=project,
            client_organization=plan.client_organization,
            source_root_url=plan.parent_folder_url,
            source_root_id=plan.parent_folder_id,
            default_register_url=plan.register_url,
            register_id=plan.register_id,
            environment=self.environment,
            status=status,
            created_at=utcnow(),
            updated_at=utcnow(),
        ))

    def _save_job(self, job: dict, fields: dict) -> dict:
        updated = self.store.update_job(job['job_id'], job['workspace_id'], job['version'], fields)
        if updated is None:
            raise Conflict('Job was updated by another worker.')
        return updated

    def _event(self, job, event_type, previous, new_status, message, actor, correlation_id, folder_id, batch_id) -> dict:
        record = EventRecord(
            event_id=event_id_for(job['job_id'], event_type, new_status, correlation_id),
            workspace_id=job['workspace_id'],
            job_id=job['job_id'],
            folder_id=folder_id,
            batch_id=batch_id,
            event_type=event_type,
            previous_status=previous,
            new_status=new_status,
            message=message,
            release=job.get('deployed_release'),
            actor=actor,
            timestamp=utcnow(),
            correlation_id=correlation_id,
        )
        return self.store.insert_event(record.model_dump(mode='json'))

    def _checkpoint(self, job, step, pause_state, resume_state, folder_id, batch_id) -> dict:
        version = 1
        existing = self.store.list_checkpoints(job['job_id'], job['workspace_id'])
        if existing:
            version = max(int(row.get('checkpoint_version') or 0) for row in existing) + 1
        checkpoint_id = f"ckpt-{job['job_id']}-{version}"
        record = CheckpointRecord(
            checkpoint_id=checkpoint_id,
            job_id=job['job_id'],
            workspace_id=job['workspace_id'],
            folder_id=folder_id,
            batch_id=batch_id,
            current_state=job['job_status'],
            last_successful_step=step,
            pause_state=pause_state,
            resume_state=resume_state,
            checkpoint_version=version,
            created_at=utcnow(),
        )
        stored = self.store.insert_checkpoint(record.model_dump(mode='json'))
        self.store.update_job(job['job_id'], job['workspace_id'], job['version'], {
            'last_checkpoint_id': checkpoint_id,
            'updated_at': job.get('updated_at') or utcnow(),
        })
        job['version'] = int(job.get('version') or 1) + 1
        job['last_checkpoint_id'] = checkpoint_id
        return stored

    def _same_import(self, existing: dict, plan: ImportPlan, workspace_id: str) -> bool:
        if existing.get('workspace_id') != workspace_id:
            return False
        if existing.get('job_status') != plan.job_status.value:
            return False
        if existing.get('source') != plan.source.value:
            return False
        if existing.get('summary_origin') != plan.summary_origin and plan.summary_origin == 'register':
            return False
        if (existing.get('summary_counts') or {}) != plan.summary_counts.model_dump():
            return False
        stored_batches = self.store.list_batches(existing['job_id'], workspace_id)
        current = sorted((row.get('batch_id'), row.get('asset_count'), row.get('status'), row.get('validation_status')) for row in stored_batches)
        desired = sorted((row.batch_id, row.asset_count, row.status, row.validation_status) for row in plan.batches)
        return current == desired

    def _already(self, job: dict, event_type: str, correlation_id: str | None) -> bool:
        if not correlation_id:
            return False
        wanted = event_id_for(job['job_id'], event_type, job.get('job_status'), correlation_id)
        return any(event.get('event_id') == wanted for event in self.store.list_events(job['job_id'], job['workspace_id']))

    def _last_event_type(self, job: dict) -> str | None:
        events = self.store.list_events(job['job_id'], job['workspace_id'])
        if not events:
            return None
        return events[-1].get('event_type')

    def import_usage_observations(self, register_rows: list[dict], diagnostics: list[dict], rates: dict) -> dict:
        """Store historical usage events. Does not write Sheets or start inspection."""
        from app.control_plane.usage_events import apply_usage_plan, plan_usage_import

        plan = plan_usage_import(register_rows, diagnostics, rates=rates)
        return apply_usage_plan(self.store, plan)

    def usage_summary(self, **filters) -> dict:
        from app.control_plane.usage_events import summarize

        return summarize(self.store.list_usage_events(**filters))

    @staticmethod
    def _require_live(job: dict, action: str) -> None:
        if job.get('source') != 'live':
            raise NotAllowed(f'Imported and historical jobs cannot be {action}. Reopen is read-only.')


def _prefer(new, old):
    return new if new is not None else old


def _fill_counts(existing: dict, incoming: dict) -> dict:
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if value is not None:
            merged[key] = value
    return merged


def _failed_batch(batch: dict) -> bool:
    status = str(batch.get('status') or '')
    return 'Failed' in status or status == 'failed'
