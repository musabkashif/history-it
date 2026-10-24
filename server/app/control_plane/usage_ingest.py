"""Live usage ingestion.

This path is separate from historical backfill and from control-plane shadow
writes. A MongoDB failure is written to the local usage-ingest outbox and is
not raised, so the Sheets workflow keeps the accounting evidence.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.control_plane.config import redact
from app.control_plane.models import UsageEventRecord


def ingest_directory(settings) -> Path:
    root = Path(getattr(settings, 'diagnostics_dir', None) or './logs')
    folder = root / 'usage-ingest'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def ingest_live_usage(settings, record: dict, batch: dict, cost: str) -> dict:
    """Insert one live usage event. MongoDB errors stay in the outbox."""
    event = live_usage_event(record, batch, cost)
    try:
        from app.control_plane.runtime import get_store
        store = get_store(settings)
        replay_usage_ingestion(settings, store)
        stored, status = store.insert_usage_event(event)
        return {'stored': True, 'status': status, 'response_id': stored.get('response_id')}
    except Exception as exc:
        path = write_usage_ingest_outbox(settings, event, exc)
        return {'stored': False, 'outbox': str(path), 'response_id': event.get('response_id')}


def replay_usage_ingestion(settings, store=None) -> dict:
    """Replay outbox files. A file is marked replayed only after a successful insert."""
    if store is None:
        from app.control_plane.runtime import get_store
        store = get_store(settings)
    replayed = 0
    pending = 0
    conflicts = []
    folder = ingest_directory(settings)
    for path in sorted(folder.glob('*.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pending += 1
            continue
        if payload.get('replayed'):
            continue
        event = payload.get('event') or {}
        try:
            store.insert_usage_event(event)
        except Exception as exc:
            pending += 1
            conflicts.append({'response_id': event.get('response_id'), 'error': redact(str(exc))})
            continue
        payload['replayed'] = True
        payload['error'] = ''
        _replace_json(path, payload)
        replayed += 1
    return {'replayed': replayed, 'pending': pending, 'conflicts': conflicts}


def live_usage_event(record: dict, batch: dict, cost: str) -> dict:
    priced = bool(cost) and record.get('cost_basis', 'application estimate' if cost else 'unknown') == 'application estimate'
    if cost and record.get('cost_basis') in (None, ''):
        priced = True
    event = {
        'response_id': str(record.get('response_id') or ''),
        'workspace_id': _text(batch.get('workspace_id')),
        'project_name': _text(batch.get('Project') or record.get('project_name')),
        'job_id': _text(batch.get('Job ID') or record.get('job_id')),
        'batch_id': _text(batch.get('Batch ID') or record.get('batch_id')),
        'attempt': _int(record.get('attempt')),
        'part': _int(record.get('part')),
        'model': _text(record.get('model') or batch.get('Model')),
        'status': _text(record.get('status')),
        'source': 'live',
        'input_tokens': _int(record.get('input_tokens')),
        'uncached_input_tokens': _int(record.get('uncached_input_tokens')),
        'cached_input_tokens': _int(record.get('cached_input_tokens')),
        'cache_write_30m_tokens': _int(record.get('cache_write_30m_tokens')),
        'cache_write_12h_tokens': _int(record.get('cache_write_12h_tokens')),
        'cache_write_24h_tokens': _int(record.get('cache_write_24h_tokens')),
        'cache_write_tokens': _int(record.get('cache_write_tokens')),
        'cache_write_retention': _text(record.get('cache_write_retention')),
        'output_tokens': _int(record.get('output_tokens')),
        'calculated_cost_usd': cost or None,
        'cost_basis': 'application estimate' if priced and cost else 'unknown',
        'register_estimate_usd': None,
        'recorded_estimate_usd': None,
        'cost_state': 'proven' if priced and cost else 'unknown',
        'billed': True if record.get('billed') is True else None if record.get('billed') in (None, '') else bool(record.get('billed')),
        'request_count': 1,
        'observed_at': _text(record.get('observed_at')),
    }
    if not priced:
        event['calculated_cost_usd'] = None
        event['cost_state'] = 'unknown'
        event['cost_basis'] = 'unknown'
    return UsageEventRecord.model_validate(event).model_dump(mode='json')


def write_usage_ingest_outbox(settings, event: dict, error: Exception) -> Path:
    folder = ingest_directory(settings)
    name = ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in str(event.get('response_id') or 'usage'))[:120]
    path = folder / f'{name}.json'
    payload = {'event': event, 'error': redact(str(error)), 'replayed': False, 'recoverable': True}
    _replace_json(path, payload)
    return path


def _replace_json(path: Path, payload: dict) -> None:
    folder = path.parent
    descriptor, temp_name = tempfile.mkstemp(prefix=path.name + '.', dir=folder)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value) -> int | None:
    if value is None or value == '':
        return None
    return int(value)
