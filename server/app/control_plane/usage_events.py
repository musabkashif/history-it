"""Immutable usage events derived from registers and saved diagnostics.

Google Sheets stays the register. This module only reads rows it is given.
It does not start inspection, write a spreadsheet, or enable backfill.
A historical category that was not recorded stays null. A null cost is unknown.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.control_plane.models import UsageEventRecord

_MILLION = Decimal(1_000_000)
_MONEY = Decimal('0.000001')
_TOKEN_FIELDS = (
    'input_tokens',
    'uncached_input_tokens',
    'cached_input_tokens',
    'cache_write_30m_tokens',
    'cache_write_12h_tokens',
    'cache_write_24h_tokens',
    'cache_write_tokens',
    'cache_write_retention',
    'output_tokens',
    'cost_basis',
)
_PRICE_FIELDS = (
    ('uncached_input_tokens', 'input'),
    ('cached_input_tokens', 'cached'),
    ('cache_write_30m_tokens', 'write30'),
    ('cache_write_12h_tokens', 'write12'),
    ('output_tokens', 'output'),
)
_MERGE_FIELDS = _TOKEN_FIELDS + (
    'attempt',
    'part',
    'model',
    'status',
    'billed',
    'register_estimate_usd',
    'recorded_estimate_usd',
    'request_count',
    'observed_at',
)
_ATTRIBUTION = ('job_id', 'workspace_id', 'project_name', 'batch_id')


def empty_totals() -> dict:
    return {
        'proven_cost_usd': None,
        'unknown_event_count': 0,
        'request_count': 0,
        'cache_write_tokens': None,
        'cache_read_tokens': None,
        'cache_write_known_tokens': None,
        'cache_read_known_tokens': None,
        'cache_write_unknown_count': 0,
        'cache_read_unknown_count': 0,
    }


def rates_from_settings(settings) -> dict:
    return {
        'input': getattr(settings, 'openai_input_usd_per_million', 0) or 0,
        'cached': getattr(settings, 'openai_cached_input_usd_per_million', 0) or 0,
        'write30': getattr(settings, 'openai_cache_write_30m_usd_per_million', 0) or 0,
        'write12': getattr(settings, 'openai_cache_write_12h_usd_per_million', 0) or 0,
        'output': getattr(settings, 'openai_output_usd_per_million', 0) or 0,
    }


def plan_usage_import(register_rows: list[dict], diagnostics: list[dict], *, rates: dict | None = None) -> dict:
    """Build one historical event per provider response. Identical copies collapse."""
    rates = rates or {}
    by_id: dict[str, dict] = {}
    conflicts = []
    duplicates = []
    skipped_joined = []
    without_response = 0

    def consider(incoming: dict, *, kind: str) -> None:
        response_id = incoming['response_id']
        current = by_id.get(response_id)
        if current is None:
            by_id[response_id] = incoming
            return
        merged, conflict_fields, attribution = _merge(current, incoming, rates)
        if conflict_fields:
            conflicts.append({'response_id': response_id, 'fields': conflict_fields, 'kind': kind})
            return
        by_id[response_id] = merged
        if attribution:
            duplicates.append({'response_id': response_id, 'kind': kind, 'fields': attribution})
            conflicts.append({'response_id': response_id, 'fields': attribution, 'kind': 'attribution'})

    for event in diagnostics or []:
        if not isinstance(event, dict) or event.get('event') not in (None, 'response_received'):
            continue
        parsed = _from_diagnostic(event, rates)
        if parsed is None:
            without_response += 1
            continue
        consider(parsed, kind='diagnostic')

    for row in register_rows or []:
        if not isinstance(row, dict):
            continue
        parts = _response_ids(row.get('Response ID') or row.get('response_id'))
        if not parts:
            without_response += 1
            continue
        if len(parts) > 1:
            if all(part in by_id for part in parts):
                skipped_joined.append({'response_ids': parts, 'batch_id': _text(row, 'Batch ID', 'batch_id')})
                continue
            if any(part in by_id for part in parts):
                skipped_joined.append({'response_ids': parts, 'batch_id': _text(row, 'Batch ID', 'batch_id'), 'partial': True})
                continue
            parsed = _from_register(row, rates, response_id=','.join(sorted(parts)), request_count=len(parts))
            consider(parsed, kind='joined')
            continue
        parsed = _from_register(row, rates, response_id=parts[0], request_count=1)
        consider(parsed, kind='register')

    events = [UsageEventRecord.model_validate(item).model_dump(mode='json') for item in by_id.values()]
    events.sort(key=lambda item: item['response_id'])
    return {
        'events': events,
        'conflicts': conflicts,
        'duplicates': duplicates,
        'skipped_joined': skipped_joined,
        'rows_without_response': without_response,
        'source': 'historical',
        'inspection_started': False,
    }


def apply_usage_plan(store, plan: dict) -> dict:
    """Insert a plan. The same payload is a no-op. A conflicting payload is refused."""
    from app.control_plane.errors import UsageConflict

    created = 0
    existing = 0
    covered = []
    conflicts = list(plan.get('conflicts') or [])
    stored_parts = {}
    for row in store.list_usage_events():
        for part in _response_ids(row.get('response_id')):
            stored_parts[part] = row.get('response_id')
    for event in plan.get('events') or []:
        parts = _response_ids(event['response_id'])
        owners = {stored_parts.get(part) for part in parts if part in stored_parts}
        owners.discard(None)
        if owners and owners != {event['response_id']}:
            covered.append({'response_id': event['response_id'], 'covered_by': sorted(owners)})
            continue
        try:
            _stored, status = store.insert_usage_event(event)
        except UsageConflict as exc:
            conflicts.append({'response_id': event['response_id'], 'fields': ['stored'], 'kind': str(exc)})
            continue
        if status == 'created':
            created += 1
            for part in parts:
                stored_parts[part] = event['response_id']
        else:
            existing += 1
    summary = summarize(store.list_usage_events())
    return {
        'created': created,
        'existing': existing,
        'conflicts': conflicts,
        'duplicates': list(plan.get('duplicates') or []),
        'covered': covered,
        'skipped_joined': list(plan.get('skipped_joined') or []),
        'rows_without_response': plan.get('rows_without_response') or 0,
        'source': 'historical',
        'inspection_started': False,
        'sheets_written': False,
        **summary,
    }


def summarize(events: list[dict]) -> dict:
    return {
        'summary': usage_totals(events),
        'by_workspace': _groups(events, ('workspace_id',)),
        'by_project': _groups(events, ('workspace_id', 'project_name')),
        'by_job': _groups(events, ('workspace_id', 'project_name', 'job_id')),
    }


def usage_totals(events: list[dict]) -> dict:
    totals = empty_totals()
    proven = Decimal(0)
    proven_seen = False
    write_known = 0
    write_any = False
    read_known = 0
    read_any = False
    write_unknown = 0
    read_unknown = 0
    for event in events:
        totals['request_count'] += int(event.get('request_count') or 1)
        if event.get('cost_state') == 'proven' and event.get('calculated_cost_usd') not in (None, ''):
            proven += Decimal(str(event['calculated_cost_usd']))
            proven_seen = True
        else:
            totals['unknown_event_count'] += 1
        write = event.get('cache_write_30m_tokens')
        write_12 = event.get('cache_write_12h_tokens')
        if write is None or write_12 is None:
            write_unknown += 1
        else:
            write_known += int(write) + int(write_12)
            write_any = True
        cached = event.get('cached_input_tokens')
        if cached is None:
            read_unknown += 1
        else:
            read_known += int(cached)
            read_any = True
    totals['proven_cost_usd'] = str(proven.quantize(_MONEY, rounding=ROUND_HALF_UP)) if proven_seen else None
    totals['cache_write_unknown_count'] = write_unknown
    totals['cache_read_unknown_count'] = read_unknown
    totals['cache_write_known_tokens'] = write_known if write_any else None
    totals['cache_read_known_tokens'] = read_known if read_any else None
    totals['cache_write_tokens'] = write_known if write_any and write_unknown == 0 else None
    totals['cache_read_tokens'] = read_known if read_any and read_unknown == 0 else None
    return totals


def event_matches(event: dict, *, workspace_id=None, project_name=None, job_id=None,
                  model=None, status=None, observed_from=None, observed_to=None) -> bool:
    if workspace_id and event.get('workspace_id') != workspace_id:
        return False
    if project_name and str(event.get('project_name') or '').casefold() != project_name.casefold():
        return False
    if job_id and event.get('job_id') != job_id:
        return False
    if model and event.get('model') != model:
        return False
    if status and event.get('status') != status:
        return False
    if observed_from or observed_to:
        observed = str(event.get('observed_at') or '')
        if not observed:
            return False
        day = observed[:10]
        if observed_from and day < observed_from:
            return False
        if observed_to and day > observed_to:
            return False
    return True


def _groups(events: list[dict], keys: tuple[str, ...]) -> list[dict]:
    buckets: dict[tuple, list] = {}
    for event in events:
        identity = tuple(event.get(key) for key in keys)
        buckets.setdefault(identity, []).append(event)
    grouped = []
    for identity, rows in buckets.items():
        item = {key: value for key, value in zip(keys, identity)}
        item.update(usage_totals(rows))
        grouped.append(item)
    grouped.sort(key=lambda item: tuple(str(item.get(key) or '') for key in keys))
    return grouped


def _from_diagnostic(event: dict, rates: dict) -> dict | None:
    response_id = _response_ids(event.get('response_id'))
    if len(response_id) != 1:
        return None
    record = _blank(response_id[0])
    record['job_id'] = _text(event, 'job_id', 'Job ID')
    record['project_name'] = _text(event, 'project', 'project_name', 'Project')
    record['workspace_id'] = _text(event, 'workspace_id')
    record['batch_id'] = _text(event, 'batch_id', 'Batch ID')
    record['attempt'] = _optional_int(event, 'attempt', 'Attempt')
    record['part'] = _optional_int(event, 'part', 'Part')
    record['model'] = _text(event, 'model', 'Model')
    record['status'] = _text(event, 'status', 'Status')
    record['observed_at'] = _text(event, 'observed_at', 'timestamp')
    record['input_tokens'] = _optional_int(event, 'input_tokens', 'Input Tokens')
    record['uncached_input_tokens'] = _optional_int(event, 'uncached_input_tokens', 'Uncached Input Tokens')
    record['cached_input_tokens'] = _optional_int(event, 'cached_input_tokens', 'cached_tokens', 'Cached Input Tokens')
    record['cache_write_30m_tokens'] = _optional_int(event, 'cache_write_30m_tokens', 'Cache Write 30m Tokens')
    record['cache_write_12h_tokens'] = _optional_int(event, 'cache_write_12h_tokens', 'Cache Write 12h Tokens')
    record['output_tokens'] = _optional_int(event, 'output_tokens', 'Output Tokens')
    record['recorded_estimate_usd'] = _money(event.get('estimated_cost_usd') if 'estimated_cost_usd' in event else event.get('Estimated Cost USD') if 'Estimated Cost USD' in event else None)
    record['billed'] = _billed(event.get('billed') if 'billed' in event else event.get('Billed') if 'Billed' in event else None)
    _finish(record, rates)
    return record


def _from_register(row: dict, rates: dict, *, response_id: str, request_count: int) -> dict:
    record = _blank(response_id)
    record['request_count'] = request_count
    record['job_id'] = _text(row, 'Job ID', 'job_id') or _text(row, '_job_id')
    record['project_name'] = _text(row, 'Project', 'project_name')
    record['workspace_id'] = _text(row, 'workspace_id')
    record['batch_id'] = _text(row, 'Batch ID', 'batch_id')
    record['attempt'] = _optional_int(row, 'Attempt', 'attempt')
    record['part'] = _optional_int(row, 'Part', 'part')
    record['model'] = _text(row, 'Model', 'model')
    record['status'] = _text(row, 'Status', 'status')
    record['observed_at'] = _text(row, 'observed_at')
    record['input_tokens'] = _optional_int(row, 'Input Tokens', 'input_tokens')
    record['uncached_input_tokens'] = _optional_int(row, 'Uncached Input Tokens', 'uncached_input_tokens')
    record['cached_input_tokens'] = _optional_int(row, 'Cached Input Tokens', 'cached_input_tokens')
    record['cache_write_30m_tokens'] = _optional_int(row, 'Cache Write 30m Tokens', 'cache_write_30m_tokens')
    record['cache_write_12h_tokens'] = _optional_int(row, 'Cache Write 12h Tokens', 'cache_write_12h_tokens')
    record['output_tokens'] = _optional_int(row, 'Output Tokens', 'output_tokens')
    estimate = row.get('Estimated Cost USD') if 'Estimated Cost USD' in row else row.get('register_estimate_usd') if 'register_estimate_usd' in row else None
    record['register_estimate_usd'] = _money(estimate)
    record['billed'] = _billed(row.get('Billed') if 'Billed' in row else row.get('billed') if 'billed' in row else None)
    _finish(record, rates)
    return record


def _finish(record: dict, rates: dict) -> None:
    record['calculated_cost_usd'] = _price(record, rates)
    record['cost_state'] = 'proven' if record['calculated_cost_usd'] is not None else 'unknown'


def _merge(current: dict, incoming: dict, rates: dict) -> tuple[dict, list[str], list[str]]:
    merged = dict(current)
    conflict_fields = []
    for key in _MERGE_FIELDS:
        left = merged.get(key)
        right = incoming.get(key)
        if right is None:
            continue
        if left is None:
            merged[key] = right
            continue
        if left != right:
            conflict_fields.append(key)
    attribution = []
    for key in _ATTRIBUTION:
        left = current.get(key)
        right = incoming.get(key)
        if left and right and left != right:
            attribution.append(key)
    if conflict_fields:
        return current, conflict_fields, attribution
    _finish(merged, rates)
    return merged, [], attribution


def _price(record: dict, rates: dict) -> str | None:
    if any(record.get(name) is None for name, _rate_key in _PRICE_FIELDS):
        return None
    total = Decimal(0)
    for name, rate_key in _PRICE_FIELDS:
        tokens = int(record[name])
        rate = Decimal(str(rates.get(rate_key) or 0))
        if tokens and rate == 0:
            return None
        total += (Decimal(tokens) * rate) / _MILLION
    return str(total.quantize(_MONEY, rounding=ROUND_HALF_UP))


def _blank(response_id: str) -> dict:
    return {
        'response_id': response_id,
        'workspace_id': None,
        'project_name': None,
        'job_id': None,
        'batch_id': None,
        'attempt': None,
        'part': None,
        'model': None,
        'status': None,
        'source': 'historical',
        'input_tokens': None,
        'uncached_input_tokens': None,
        'cached_input_tokens': None,
        'cache_write_30m_tokens': None,
        'cache_write_12h_tokens': None,
        'output_tokens': None,
        'calculated_cost_usd': None,
        'register_estimate_usd': None,
        'recorded_estimate_usd': None,
        'cost_state': 'unknown',
        'billed': None,
        'request_count': 1,
        'observed_at': None,
    }


def _response_ids(value) -> list[str]:
    return [part.strip() for part in str(value or '').split(',') if part.strip()]


def _text(row: dict, *keys: str) -> str | None:
    for key in keys:
        if key not in row:
            continue
        raw = row.get(key)
        if raw is None:
            return None
        text = str(raw).strip()
        return text or None
    return None


def _optional_int(row: dict, *keys: str) -> int | None:
    for key in keys:
        if key not in row:
            continue
        raw = row.get(key)
        if raw is None or str(raw).strip() == '':
            return None
        return int(float(raw))
    return None


def _money(value) -> str | None:
    if value is None or str(value).strip() == '':
        return None
    return str(Decimal(str(value)).quantize(_MONEY, rounding=ROUND_HALF_UP))


def _billed(value) -> bool | None:
    if value is None or str(value).strip() == '':
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in ('yes', 'true', '1'):
        return True
    if text in ('no', 'false', '0'):
        return False
    return None
