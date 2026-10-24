"""Control-plane HTTP API.

List endpoints stay readable when the database is off or down so the console
can show a warning. They do not change the Google Sheets workflow. Import
reads register tabs and never starts inspection.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.control_plane.catalog import KNOWN_STAGING_JOBS
from app.control_plane.config import redact
from app.control_plane.errors import (
    Conflict,
    ControlPlaneDisabled,
    ControlPlaneUnavailable,
    DuplicateRecord,
    LeaseHeld,
    NotAllowed,
    NotFound,
    WorkspaceIsolationError,
)
from app.control_plane.identity import workspace_id_for
from app.control_plane.importing import operator_note_extract, plan_from_extract, read_register
from app.control_plane.models import JobSource
from app.control_plane.runtime import get_store, health_fields
from app.control_plane.usage_events import empty_totals, rates_from_settings
from app.control_plane.service import ControlService
from app.google_ids import GoogleIdError, parse_spreadsheet_id

_JOB_ID = re.compile(r'^HIT-[0-9a-fA-F]{8,64}$')
_FILE_REF = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$')
_CORRELATION = re.compile(r'^[A-Za-z0-9._:-]{1,80}$')

DISABLED = 'Database control plane is disabled. The Google Sheets workflow is unchanged.'
BACKFILL_DISABLED = 'Database backfill is disabled. Nothing was imported and inspection was not started.'
UNAVAILABLE = 'MongoDB is configured but unavailable. The Google Sheets workflow is still usable.'
LOAD_ERROR = 'The control plane could not load records.'


class ManualImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str
    register_url: str | None = None
    register_file_reference: str | None = None
    source: str = 'imported'
    deployed_release: str | None = Field(default=None, max_length=64)

    @model_validator(mode='after')
    def _reference(self):
        if not (self.register_url or self.register_file_reference):
            raise ValueError('A register URL or file reference is required.')
        if self.source not in ('historical', 'imported'):
            raise ValueError('Source must be historical or imported.')
        if not _JOB_ID.fullmatch(self.job_id or ''):
            raise ValueError('Job ID must look like HIT- followed by hexadecimal characters.')
        if self.register_file_reference and not _FILE_REF.fullmatch(self.register_file_reference):
            raise ValueError('Register file reference must be a file name or id, not a path or URL.')
        if self.deployed_release and not _FILE_REF.fullmatch(self.deployed_release):
            raise ValueError('Release must be a short label.')
        return self


class UsageImportRequest(BaseModel):
    """Rows already read from a register or a diagnostic file. Nothing here contacts Google."""

    model_config = ConfigDict(extra='forbid')
    register_rows: list[dict] = Field(default_factory=list)
    diagnostics: list[dict] = Field(default_factory=list)


class BackfillRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    include_known_staging_jobs: bool = True
    registers: list[ManualImportRequest] = Field(default_factory=list)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workspace_id: str | None = None
    batch_id: str | None = None
    evidence: str | None = Field(default=None, max_length=200)


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_name: str = Field(min_length=1, max_length=200)
    client_organization: str | None = Field(default=None, max_length=200)

    @model_validator(mode='after')
    def _names(self):
        self.project_name = self.project_name.strip()
        if not self.project_name or any(ch in self.project_name for ch in '\r\n\t'):
            raise ValueError('Project name is invalid.')
        if self.client_organization is not None:
            self.client_organization = self.client_organization.strip()
            if not self.client_organization:
                self.client_organization = None
            elif any(ch in self.client_organization for ch in '\r\n\t'):
                raise ValueError('Organization is invalid.')
        return self


def build_router(settings, guard, sheets_provider):
    router = APIRouter(prefix='/api/control')

    def actor(request: Request) -> str | None:
        try:
            from app.auth import session_from_request
            _token, record = session_from_request(request)
        except Exception:
            return None
        return record.username if record else None

    def correlation(request: Request) -> str | None:
        raw = request.headers.get('idempotency-key') or request.headers.get('x-correlation-id') or ''
        raw = raw.strip()
        if not raw:
            return None
        if not _CORRELATION.fullmatch(raw):
            raise HTTPException(400, 'Correlation id is invalid.')
        return raw

    def enabled() -> bool:
        return bool(settings.database_control_plane_enabled)

    def backfill_enabled() -> bool:
        return bool(settings.database_backfill_enabled)

    def service() -> ControlService:
        try:
            store = get_store(settings)
        except ControlPlaneDisabled as exc:
            raise HTTPException(409, DISABLED) from exc
        except ControlPlaneUnavailable as exc:
            raise HTTPException(503, UNAVAILABLE) from exc
        except Exception as exc:
            from logging import getLogger
            getLogger('historyit.control').warning('MongoDB is configured but unavailable (%s)', redact(str(exc)))
            raise HTTPException(503, UNAVAILABLE) from None
        return ControlService(store, getattr(settings, 'control_plane_environment', 'staging') or 'staging')

    def call(fn):
        try:
            return fn()
        except HTTPException:
            raise
        except (NotFound, WorkspaceIsolationError):
            raise HTTPException(404, 'Job was not found in this workspace.')
        except LeaseHeld:
            raise HTTPException(409, 'Another worker holds this batch.')
        except (Conflict, NotAllowed, DuplicateRecord, ValueError) as exc:
            raise HTTPException(409, redact(str(exc)))
        except (ControlPlaneDisabled, ControlPlaneUnavailable):
            raise HTTPException(503, UNAVAILABLE)
        except Exception as exc:
            from logging import getLogger
            getLogger('historyit.control').warning('Control plane request failed (%s)', redact(str(exc)))
            raise HTTPException(503, LOAD_ERROR) from None

    def disabled_list(key: str) -> dict:
        return {
            'state': 'disabled',
            'enabled': False,
            'available': False,
            'warning': DISABLED,
            key: [],
            'inspection_started': False,
        }

    def frame(key: str, items: list) -> dict:
        state = 'empty' if not items else 'ready'
        return {
            'state': state,
            'enabled': True,
            'available': True,
            'warning': None,
            key: items,
            'inspection_started': False,
        }

    @router.get('/status')
    def status(request: Request, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token)
        body = health_fields(settings)
        body['inspection_started'] = False
        if not enabled():
            body['state'] = 'disabled'
            body['warning'] = DISABLED
        elif body.get('database_available') is False:
            body['state'] = 'offline'
            body['warning'] = body.get('database_message') or UNAVAILABLE
        else:
            body['state'] = 'ready'
            body['warning'] = None
        return body

    @router.get('/workspaces')
    def workspaces(request: Request, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token)
        if not enabled():
            return disabled_list('workspaces')
        try:
            rows = service().list_workspaces()
        except HTTPException as exc:
            return _from_http('workspaces', exc)
        except Exception as exc:
            return _error('workspaces', exc)
        return frame('workspaces', rows)

    @router.post('/workspaces')
    def create_project(body: CreateProjectRequest, request: Request, x_api_token: str | None = Header(default=None)):
        """Record a project name and optional client. Does not create a register or start inspection."""
        guard(request, x_api_token, mutating=True)
        if not enabled():
            raise HTTPException(409, DISABLED)
        ctrl = service()
        environment = getattr(settings, 'control_plane_environment', 'staging') or 'staging'
        workspace_id = workspace_id_for(body.project_name, environment)
        current = ctrl.store.get_workspace(workspace_id)
        if current:
            if body.client_organization and not current.get('client_organization'):
                current = call(lambda: ctrl.create_workspace(
                    project_name=current.get('project_name') or body.project_name,
                    client_organization=body.client_organization,
                ))
            return {'state': 'ready', 'inspection_started': False, 'already_applied': True, 'workspace': current}
        created = call(lambda: ctrl.create_workspace(
            project_name=body.project_name,
            client_organization=body.client_organization,
        ))
        return {'state': 'ready', 'inspection_started': False, 'already_applied': False, 'workspace': created}

    @router.get('/jobs')
    def jobs(
        request: Request,
        workspace_id: str | None = None,
        project: str | None = None,
        box: str | None = None,
        status: str | None = None,
        source: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        x_api_token: str | None = Header(default=None),
    ):
        guard(request, x_api_token)
        if not enabled():
            return disabled_list('jobs')
        try:
            rows = service().list_jobs(
                workspace_id=workspace_id, project=project, box=box, status=status,
                source=source, created_from=created_from, created_to=created_to,
            )
        except HTTPException as exc:
            return _from_http('jobs', exc)
        except Exception as exc:
            return _error('jobs', exc)
        return frame('jobs', rows)

    @router.get('/jobs/{job_id}')
    def job_detail(job_id: str, request: Request, workspace_id: str | None = None, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token)
        if not enabled():
            raise HTTPException(409, DISABLED)
        detail = call(lambda: service().get_detail(job_id, workspace_id))
        detail.update({'state': 'ready', 'enabled': True, 'available': True, 'warning': None})
        return detail

    @router.post('/jobs/{job_id}/reopen')
    def reopen(job_id: str, request: Request, body: ActionRequest | None = None, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token, mutating=True)
        if not enabled():
            raise HTTPException(409, DISABLED)
        payload = body or ActionRequest()
        detail = call(lambda: service().reopen_job(job_id, payload.workspace_id, actor=actor(request), correlation_id=correlation(request)))
        detail.update({'state': 'ready', 'enabled': True, 'available': True, 'warning': None})
        return detail

    @router.post('/jobs/{job_id}/resume')
    def resume(job_id: str, request: Request, body: ActionRequest | None = None, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token, mutating=True)
        if not enabled():
            raise HTTPException(409, DISABLED)
        payload = body or ActionRequest()
        detail = call(lambda: service().resume_job(job_id, payload.workspace_id, actor=actor(request), correlation_id=correlation(request)))
        detail.update({'state': 'ready', 'enabled': True, 'available': True, 'warning': None})
        return detail

    @router.post('/jobs/{job_id}/pause')
    def pause(job_id: str, request: Request, body: ActionRequest | None = None, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token, mutating=True)
        if not enabled():
            raise HTTPException(409, DISABLED)
        payload = body or ActionRequest()
        detail = call(lambda: service().pause_job(job_id, payload.workspace_id, actor=actor(request), correlation_id=correlation(request)))
        detail.update({'state': 'ready', 'enabled': True, 'available': True, 'warning': None})
        return detail

    @router.post('/jobs/{job_id}/start')
    def start(job_id: str, request: Request, body: ActionRequest | None = None, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token, mutating=True)
        if not enabled():
            raise HTTPException(409, DISABLED)
        payload = body or ActionRequest()
        detail = call(lambda: service().start_job(job_id, payload.workspace_id, actor=actor(request), correlation_id=correlation(request)))
        detail.update({'state': 'ready', 'enabled': True, 'available': True, 'warning': None})
        return detail

    @router.post('/jobs/{job_id}/retry')
    def retry(job_id: str, request: Request, body: ActionRequest, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token, mutating=True)
        if not enabled():
            raise HTTPException(409, DISABLED)
        if not body.batch_id:
            raise HTTPException(422, 'batch_id is required.')
        detail = call(lambda: service().retry_batch(job_id, body.batch_id, body.workspace_id, actor=actor(request), correlation_id=correlation(request)))
        detail.update({'state': 'ready', 'enabled': True, 'available': True, 'warning': None})
        return detail

    @router.post('/jobs/{job_id}/complete')
    def complete(job_id: str, request: Request, body: ActionRequest, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token, mutating=True)
        if not enabled():
            raise HTTPException(409, DISABLED)
        detail = call(lambda: service().complete_job(
            job_id, body.workspace_id, evidence=body.evidence, actor=actor(request), correlation_id=correlation(request),
        ))
        detail.update({'state': 'ready', 'enabled': True, 'available': True, 'warning': None})
        return detail

    @router.post('/jobs/{job_id}/recover')
    def recover(job_id: str, request: Request, body: ActionRequest | None = None, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token, mutating=True)
        if not enabled():
            raise HTTPException(409, DISABLED)
        payload = body or ActionRequest()
        detail = call(lambda: service().recover_job(job_id, payload.workspace_id, actor=actor(request)))
        detail.update({'state': 'ready', 'enabled': True, 'available': True, 'warning': None})
        return detail

    @router.post('/import')
    def import_job(body: ManualImportRequest, request: Request, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token, mutating=True)
        _require_import()
        detail = call(lambda: _import_one(body, actor(request), correlation(request)))
        detail.setdefault('warnings', [])
        detail.update({'state': 'ready', 'enabled': True, 'available': True, 'warning': None})
        return detail

    @router.post('/backfill')
    def backfill(body: BackfillRequest, request: Request, x_api_token: str | None = Header(default=None)):
        guard(request, x_api_token, mutating=True)
        _require_import()
        def run():
            results = []
            if body.include_known_staging_jobs:
                for record in KNOWN_STAGING_JOBS:
                    plan = plan_from_extract(operator_note_extract(record), source=JobSource.historical, job_id=record['job_id'])
                    results.append(service().import_record(plan, actor=actor(request), correlation_id=f"catalog:{record['job_id']}"))
            for item in body.registers:
                results.append(_import_one(item, actor(request), correlation(request)))
            return {
                'imported': len(results),
                'job_ids': [item['job']['job_id'] for item in results],
                'inspection_started': False,
                'results': results,
            }
        payload = call(run)
        payload.update({'state': 'ready', 'enabled': True, 'available': True, 'warning': None})
        return payload

    def _require_import() -> None:
        if not enabled():
            raise HTTPException(409, DISABLED)
        if not backfill_enabled():
            raise HTTPException(409, BACKFILL_DISABLED)
        service()

    def _import_one(body: ManualImportRequest, who: str | None, correlation_id: str | None) -> dict:
        register_id = None
        warnings = []
        if body.register_url:
            try:
                register_id = parse_spreadsheet_id(body.register_url)
            except GoogleIdError as exc:
                raise HTTPException(422, str(exc)) from exc
        elif body.register_file_reference:
            try:
                register_id = parse_spreadsheet_id(body.register_file_reference)
            except GoogleIdError:
                register_id = None
        extract = {
            'summary_origin': 'unknown',
            'register_was_read': False,
            'register_url': body.register_url,
            'register_id': register_id,
            'register_file_reference': body.register_file_reference,
            'deployed_release': body.deployed_release,
            'warnings': warnings,
            'message': 'Imported from the supplied job id and register reference. Inspection was not started.',
        }
        if register_id:
            loaded, read_warning = _read(register_id, body.job_id)
            if loaded is None:
                warnings.append(read_warning or 'Register was not read. Missing values were left unknown.')
            else:
                extract.update(loaded)
                extract['register_url'] = body.register_url or extract.get('register_url')
                extract['register_file_reference'] = body.register_file_reference
                extract['deployed_release'] = body.deployed_release
                extract['warnings'] = list(loaded.get('warnings') or []) + warnings
        plan = plan_from_extract(extract, source=JobSource(body.source), job_id=body.job_id)
        detail = service().import_record(plan, actor=who, correlation_id=correlation_id or f'import:{body.job_id}:{register_id or body.register_file_reference}')
        detail['usage_import'] = service().import_usage_observations(
            list(extract.get('usage_detail_rows') or []),
            [],
            rates_from_settings(settings),
        )
        detail['inspection_started'] = False
        detail['sheets_written'] = False
        return detail

    def _usage_filters(workspace_id, project, job_id, model, status, observed_from, observed_to):
        for label, value in (('observed_from', observed_from), ('observed_to', observed_to)):
            if value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise HTTPException(400, f'{label} must be YYYY-MM-DD.')
        return {
            'workspace_id': workspace_id or None,
            'project_name': project or None,
            'job_id': job_id or None,
            'model': model or None,
            'status': status or None,
            'observed_from': observed_from or None,
            'observed_to': observed_to or None,
        }

    @router.get('/usage/summary')
    def usage_summary(
        request: Request,
        workspace_id: str | None = None,
        project: str | None = None,
        job_id: str | None = None,
        model: str | None = None,
        status: str | None = None,
        observed_from: str | None = None,
        observed_to: str | None = None,
        x_api_token: str | None = Header(default=None),
    ):
        """Derived usage totals. Missing historical cost stays null."""
        guard(request, x_api_token)
        filters = _usage_filters(workspace_id, project, job_id, model, status, observed_from, observed_to)
        if not enabled():
            return {
                'state': 'disabled',
                'warning': DISABLED,
                'inspection_started': False,
                'sheets_written': False,
                'summary': empty_totals(),
                'by_workspace': [],
                'by_project': [],
                'by_job': [],
            }
        data = call(lambda: service().usage_summary(**filters))
        return {
            'state': 'ready',
            'warning': None,
            'inspection_started': False,
            'sheets_written': False,
            **data,
        }

    @router.post('/usage/import')
    def usage_import(body: UsageImportRequest, request: Request, x_api_token: str | None = Header(default=None)):
        """Import usage rows the caller already read. Requires backfill and does not write Sheets."""
        guard(request, x_api_token, mutating=True)
        _require_import()
        result = call(lambda: service().import_usage_observations(
            body.register_rows,
            body.diagnostics,
            rates_from_settings(settings),
        ))
        result['state'] = 'ready'
        return result

    def _read(spreadsheet_id: str, job_id: str):
        try:
            sheets = sheets_provider()
        except Exception as exc:
            return None, 'Google Sheets is not available. Missing values were left unknown. ' + redact(str(exc))
        if sheets is None:
            return None, 'Google Sheets is not available. Missing values were left unknown.'
        return read_register(sheets, spreadsheet_id, job_id, getattr(settings, 'job_index_spreadsheet_id', '') or None), None

    return router


def _offline(key: str) -> dict:
    return {
        'state': 'offline',
        'enabled': True,
        'available': False,
        'warning': UNAVAILABLE,
        key: [],
        'inspection_started': False,
    }


def _error(key: str, exc: Exception) -> dict:
    from logging import getLogger
    getLogger('historyit.control').warning('Control plane request failed (%s)', redact(str(exc)))
    return {
        'state': 'error',
        'enabled': True,
        'available': False,
        'warning': LOAD_ERROR,
        key: [],
        'inspection_started': False,
    }


def _from_http(key: str, exc: HTTPException) -> dict:
    if exc.status_code == 409:
        return {
            'state': 'disabled',
            'enabled': False,
            'available': False,
            'warning': DISABLED,
            key: [],
            'inspection_started': False,
        }
    if exc.status_code == 503:
        return _offline(key)
    raise exc
