"""Shadow writes.

A failure here is logged without the connection string and never raised.
The Google Sheets result is returned either way. The reader is not called
when shadow writes are off.
"""
from __future__ import annotations

import hashlib
import logging

from app.control_plane.config import redact
from app.control_plane.importing import plan_from_extract
from app.control_plane.models import JobSource
from app.control_plane.runtime import get_store
from app.control_plane.service import ControlService

_LOG = logging.getLogger('historyit.control')


def preserve_sheets_result(result, shadow):
    """Run a shadow write and always return the Sheets result."""
    try:
        shadow()
    except Exception as exc:
        _LOG.warning('MongoDB shadow write failed; Google Sheets workflow continues (%s)', redact(str(exc)))
    return result


def guarded_shadow(settings, operation: str, job_id: str | None, state, runtime_reader) -> None:
    if not getattr(settings, 'database_shadow_writes_enabled', False):
        return
    try:
        _shadow(settings, operation, job_id, state, runtime_reader)
    except Exception as exc:
        _LOG.warning('MongoDB shadow write failed; Google Sheets workflow continues (%s)', redact(str(exc)))


def _shadow(settings, operation: str, job_id: str | None, state, runtime_reader) -> None:
    payload = _as_dict(state)
    job_id = job_id or payload.get('job_id')
    spreadsheet_id = payload.get('spreadsheet_id')
    if not job_id or not spreadsheet_id:
        return
    runtime = runtime_reader()
    sheets = getattr(runtime, 'sheets', None)
    if sheets is None:
        return
    from app.control_plane.importing import read_register
    extract = read_register(sheets, spreadsheet_id, job_id, getattr(settings, 'job_index_spreadsheet_id', '') or None)
    extract['sheets_status'] = payload.get('status')
    extract['register_id'] = spreadsheet_id
    plan = plan_from_extract(extract, source=JobSource.live, job_id=job_id)
    digest = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()[:16]
    service = ControlService(get_store(settings), getattr(settings, 'control_plane_environment', 'staging'))
    service.import_record(plan, actor=None, correlation_id=f'shadow:{operation}:{digest}')


def _as_dict(state) -> dict:
    if state is None:
        return {}
    if isinstance(state, dict):
        return state
    if hasattr(state, 'model_dump'):
        return state.model_dump()
    return {}
