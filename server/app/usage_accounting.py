"""Accurate OpenAI usage accounting.

The Responses API reports one input total plus cached-read and one
cache_write_tokens count. That count does not say whether the write was
retained for 30 minutes, 12 hours, or 24 hours. A 30-minute or 12-hour column
is filled only when the response names that category. Otherwise the write
stays unscoped and the application estimate stays unknown.

OpenAI Usage keeps its existing columns. These columns are added at the right
edge when a usage row is written:

- Uncached Input Tokens
- Cache Write 30m Tokens
- Cache Write 12h Tokens
- Part
- Failure Reason
- Billed

Call ID is response:{response_id}. A second write of the same response with
the same token counts is a no-op. A different token count for that response
is refused. Attempt numbers are not reused as Call IDs.

Configured rates, per million tokens:

- OPENAI_INPUT_USD_PER_MILLION for uncached input
- OPENAI_CACHED_INPUT_USD_PER_MILLION for cached reads
- OPENAI_CACHE_WRITE_30M_USD_PER_MILLION for 30-minute cache writes
- OPENAI_CACHE_WRITE_12H_USD_PER_MILLION for 12-hour cache writes
- OPENAI_OUTPUT_USD_PER_MILLION for output

A missing cache-write rate does not fall back to the ordinary input rate.
Requesting a cache TTL does not name the retention of cache_write_tokens.
That count stays unknown unless the response itself names 30m, 12h, or 24h.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from app.diagnostics import _safe

ADDED_USAGE_COLUMNS = (
    'Uncached Input Tokens',
    'Cache Write 30m Tokens',
    'Cache Write 12h Tokens',
    'Cache Write Tokens',
    'Cache Write 24h Tokens',
    'Cache Write Retention',
    'Cost Basis',
    'Part',
    'Failure Reason',
    'Billed',
)

_MILLION = Decimal(1_000_000)
_MONEY = Decimal('0.000001')
_WRITE_30_KEYS = (
    'cache_write_30m_tokens',
    'cache_write_tokens_30m',
    'thirty_minute_cache_write_tokens',
)
_WRITE_12_KEYS = (
    'cache_write_12h_tokens',
    'cache_write_tokens_12h',
    'twelve_hour_cache_write_tokens',
)
_TOKEN_COLUMNS = (
    ('Input Tokens', 'input_tokens'),
    ('Uncached Input Tokens', 'uncached_input_tokens'),
    ('Cached Input Tokens', 'cached_input_tokens'),
    ('Cache Write 30m Tokens', 'cache_write_30m_tokens'),
    ('Cache Write 12h Tokens', 'cache_write_12h_tokens'),
    ('Cache Write Tokens', 'cache_write_tokens'),
    ('Cache Write 24h Tokens', 'cache_write_24h_tokens'),
    ('Output Tokens', 'output_tokens'),
)
_WRITE_24_KEYS = (
    'cache_write_24h_tokens',
    'cache_write_tokens_24h',
)


class UsageAccountingError(RuntimeError):
    """A provider response could not be priced or stored exactly."""


def usage_call_id(response_id: str) -> str:
    text = str(response_id or '').strip()
    if not text:
        raise UsageAccountingError('A billed usage record requires a provider response ID')
    return f'response:{text}'


def _mapping(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    for method in ('model_dump', 'to_dict'):
        fn = getattr(value, method, None)
        if not callable(fn):
            continue
        try:
            dumped = fn()
        except Exception:
            continue
        if isinstance(dumped, dict):
            return dumped
    found = {}
    names = (
        'input_tokens', 'output_tokens', 'input_tokens_details', 'output_tokens_details',
        'cached_tokens', 'cache_write_tokens', *_WRITE_30_KEYS, *_WRITE_12_KEYS,
    )
    for name in names:
        if hasattr(value, name):
            found[name] = getattr(value, name)
    return found


def _present(mapping: dict, names: tuple[str, ...]):
    for name in names:
        if name in mapping and mapping[name] is not None and mapping[name] != '':
            return int(mapping[name])
    return None


def _whole(value, default=0) -> int:
    if value is None or value == '':
        return default
    return int(value)


def parse_provider_usage(usage, *, response_id: str, attempt: int, part: int, status: str, failure_reason: str = '') -> dict:
    """Read the categories the provider returned. Do not assign a retention the response did not name."""
    if isinstance(usage, dict) and 'cache_write_retention' in usage and 'input_tokens' in usage:
        record = dict(usage)
        record['response_id'] = str(record.get('response_id') or response_id)
        record['attempt'] = int(record.get('attempt') or attempt)
        record['part'] = int(record.get('part') or part)
        record['status'] = str(record.get('status') or status)
        record['failure_reason'] = str(record.get('failure_reason') or failure_reason)
        record['billed'] = True
        return record
    raw = _mapping(usage)
    details = _mapping(raw.get('input_tokens_details'))
    write_details = _mapping(details.get('cache_write_tokens_details'))
    if not write_details:
        write_details = _mapping(raw.get('cache_write_tokens_details'))
    input_tokens = _whole(_present(raw, ('input_tokens',)))
    output_tokens = _whole(_present(raw, ('output_tokens',)))
    cached = _present(details, ('cached_tokens',))
    if cached is None:
        cached = _present(raw, ('cached_tokens', 'cached_input_tokens')) or 0
    write_30 = _present(details, _WRITE_30_KEYS)
    if write_30 is None:
        write_30 = _present(raw, _WRITE_30_KEYS)
    if write_30 is None:
        write_30 = _present(write_details, ('ttl_30m_tokens', 'cache_write_30m_tokens', 'thirty_minute_cache_write_tokens'))
    write_12 = _present(details, ('cache_write_12h_tokens', 'cache_write_tokens_12h', 'twelve_hour_cache_write_tokens'))
    if write_12 is None:
        write_12 = _present(raw, ('cache_write_12h_tokens', 'cache_write_tokens_12h', 'twelve_hour_cache_write_tokens'))
    if write_12 is None:
        write_12 = _present(write_details, ('ttl_12h_tokens', 'cache_write_12h_tokens', 'twelve_hour_cache_write_tokens'))
    write_24 = _present(details, _WRITE_24_KEYS)
    if write_24 is None:
        write_24 = _present(raw, _WRITE_24_KEYS)
    if write_24 is None:
        write_24 = _present(write_details, ('ttl_24h_tokens', 'cache_write_24h_tokens'))
    generic = _present(details, ('cache_write_tokens',))
    if generic is None:
        generic = _present(raw, ('cache_write_tokens',))
    # Named TTLs under cache_write_tokens_details are provider-reported retention.
    # Providers often return every TTL key with zeros; only positive counts name the write.
    named_present = any(value is not None for value in (write_30, write_12, write_24))
    positive = [
        name for name, value in (('30m', write_30), ('12h', write_12), ('24h', write_24))
        if value not in (None, 0)
    ]
    if not named_present or (not positive and (generic in (None, 0))):
        if not named_present:
            retention = 'unknown'
            unscoped = '' if generic is None else generic
            write_30_out = ''
            write_12_out = ''
            write_24_out = ''
            accounted = None if unscoped == '' else cached + int(unscoped)
        else:
            # TTL fields were present but empty, and there is no unscoped write.
            retention = 'none'
            unscoped = ''
            write_30_out = 0 if write_30 is None else write_30
            write_12_out = 0 if write_12 is None else write_12
            write_24_out = 0 if write_24 is None else write_24
            accounted = cached + _whole(write_30_out) + _whole(write_12_out) + _whole(write_24_out)
    elif not positive and generic not in (None, 0):
        # Nested TTL keys are all zero while an unscoped write remains. Keep unknown.
        retention = 'unknown'
        unscoped = generic
        write_30_out = ''
        write_12_out = ''
        write_24_out = ''
        accounted = cached + int(unscoped)
    else:
        retention = positive[0] if len(positive) == 1 else 'mixed'
        unscoped = ''
        write_30_out = 0 if write_30 is None else write_30
        write_12_out = 0 if write_12 is None else write_12
        write_24_out = 0 if write_24 is None else write_24
        accounted = cached + _whole(write_30_out) + _whole(write_12_out) + _whole(write_24_out)
    gap = ''
    if accounted is None:
        uncached = ''
    elif input_tokens >= accounted:
        uncached = input_tokens - accounted
    else:
        uncached = ''
        gap = f'input {input_tokens} is less than cached plus cache-write {accounted}'
    return {
        'input_tokens': input_tokens,
        'uncached_input_tokens': uncached,
        'cached_input_tokens': cached,
        'cache_write_30m_tokens': write_30_out,
        'cache_write_12h_tokens': write_12_out,
        'cache_write_24h_tokens': write_24_out,
        'cache_write_tokens': unscoped,
        'cache_write_retention': retention,
        'output_tokens': output_tokens,
        'response_id': str(response_id or ''),
        'attempt': int(attempt),
        'part': int(part),
        'status': gap and 'Usage Identity Gap' or status,
        'failure_reason': gap or str(failure_reason or ''),
        'usage_identity_gap': gap,
        'billed': True,
    }


def _rate(settings, name: str) -> Decimal:
    return Decimal(str(getattr(settings, name, 0) or 0))


def usage_categories_complete(record: dict) -> bool:
    """True when every returned category can be priced. This is not an invoice."""
    if record.get('usage_identity_gap') or record.get('uncached_input_tokens') == '':
        return False
    retention = str(record.get('cache_write_retention') or '')
    unscoped = record.get('cache_write_tokens')
    write_24 = record.get('cache_write_24h_tokens')
    if retention == 'unknown' and unscoped not in (None, '', 0):
        return False
    if retention in {'24h', 'mixed'} or write_24 not in (None, '', 0):
        return False
    return True


def usage_cost(record: dict, settings) -> str:
    """Price each named category. An unnamed cache write has no estimate."""
    if record.get('usage_identity_gap') or record.get('uncached_input_tokens') == '':
        raise UsageAccountingError(record.get('usage_identity_gap') or 'A usage category was not returned. The application cannot estimate this cost.')
    retention = str(record.get('cache_write_retention') or '')
    unscoped = record.get('cache_write_tokens')
    write_24 = record.get('cache_write_24h_tokens')
    if retention == 'unknown' and unscoped not in (None, '', 0):
        raise UsageAccountingError('Cache-write retention was not returned. The application cannot estimate this cost.')
    if retention == '24h' or write_24 not in (None, '', 0):
        raise UsageAccountingError('Cache-write retention is not a confirmed 30-minute or 12-hour category. The cost is unknown.')
    write_30 = _whole(record.get('cache_write_30m_tokens'))
    write_12 = _whole(record.get('cache_write_12h_tokens'))
    rate_30 = _rate(settings, 'openai_cache_write_30m_usd_per_million')
    rate_12 = _rate(settings, 'openai_cache_write_12h_usd_per_million')
    if write_30 and rate_30 == 0:
        raise UsageAccountingError('Set OPENAI_CACHE_WRITE_30M_USD_PER_MILLION. Cache writes are not priced as ordinary input.')
    if write_12 and rate_12 == 0:
        raise UsageAccountingError('Set OPENAI_CACHE_WRITE_12H_USD_PER_MILLION. Cache writes are not priced as ordinary input.')
    uncached = _whole(record.get('uncached_input_tokens'))
    cached = _whole(record.get('cached_input_tokens'))
    output = _whole(record.get('output_tokens'))
    rate_input = _rate(settings, 'openai_input_usd_per_million')
    rate_cached = _rate(settings, 'openai_cached_input_usd_per_million')
    rate_output = _rate(settings, 'openai_output_usd_per_million')
    if uncached and rate_input == 0:
        raise UsageAccountingError('Set OPENAI_INPUT_USD_PER_MILLION. A missing rate is not a $0 cost. The cost is unknown.')
    if cached and rate_cached == 0:
        raise UsageAccountingError('Set OPENAI_CACHED_INPUT_USD_PER_MILLION. A missing rate is not a $0 cost. The cost is unknown.')
    if output and rate_output == 0:
        raise UsageAccountingError('Set OPENAI_OUTPUT_USD_PER_MILLION. A missing rate is not a $0 cost. The cost is unknown.')
    if str(getattr(settings, 'openai_model', '') or '') == 'gpt-5.6-sol' and _whole(record.get('input_tokens')) > 272_000:
        raise UsageAccountingError('Input is above 272,000 tokens. The configured rates are the short-context card for gpt-5.6-sol. The cost is unknown.')
    total = (
        Decimal(uncached) * rate_input
        + Decimal(cached) * rate_cached
        + Decimal(write_30) * rate_30
        + Decimal(write_12) * rate_12
        + Decimal(output) * rate_output
    ) / _MILLION
    return format(total.quantize(_MONEY, rounding=ROUND_HALF_UP), 'f')


def _same_tokens(row: dict, record: dict) -> bool:
    if str(row.get('Response ID') or '') != str(record.get('response_id') or ''):
        return False
    for column, key in _TOKEN_COLUMNS:
        actual = str(row.get(column) or '').strip()
        expected = record.get(key, '')
        if expected == '':
            if actual:
                return False
            continue
        if not actual or int(float(actual)) != int(expected):
            return False
    return True


def usage_row(record: dict, *, project: str, job: str, batch: str, model: str, source_count: str, elapsed, cost: str, cumulative: str) -> dict:
    reason = str(_safe(record.get('failure_reason') or ''))[:300]
    return {
        'Call ID': usage_call_id(record['response_id']),
        'Project': project,
        'Job ID': job,
        'Batch ID': batch,
        'Model': model,
        'Source Image Count': str(source_count),
        'Input Tokens': str(record.get('input_tokens', '')),
        'Uncached Input Tokens': '' if record.get('uncached_input_tokens') == '' else str(record.get('uncached_input_tokens')),
        'Cached Input Tokens': str(record.get('cached_input_tokens', '')),
        'Cache Write 30m Tokens': '' if record.get('cache_write_30m_tokens') == '' else str(record.get('cache_write_30m_tokens', '')),
        'Cache Write 12h Tokens': '' if record.get('cache_write_12h_tokens') == '' else str(record.get('cache_write_12h_tokens', '')),
        'Cache Write Tokens': '' if record.get('cache_write_tokens') == '' else str(record.get('cache_write_tokens', '')),
        'Cache Write 24h Tokens': '' if record.get('cache_write_24h_tokens') == '' else str(record.get('cache_write_24h_tokens', '')),
        'Cache Write Retention': str(record.get('cache_write_retention') or ''),
        'Output Tokens': str(record.get('output_tokens', '')),
        'Estimated Cost USD': cost,
        'Cost Basis': 'application estimate' if cost else 'unknown',
        'Attempt': str(record.get('attempt', '')),
        'Part': str(record.get('part', '')),
        'Elapsed Seconds': str(elapsed),
        'Status': str(record.get('status') or ''),
        'Failure Reason': reason,
        'Billed': 'Yes',
        'Cumulative Job Cost USD': cumulative,
        'Response ID': str(record.get('response_id') or ''),
    }


def store_usage_row(sheets, sid: str, record: dict, row: dict) -> str:
    """Insert one response row. An identical response is kept. A conflict is refused."""
    call_id = row['Call ID']
    ensure = getattr(sheets, 'ensure_columns', None)
    if callable(ensure):
        ensure(sid, 'OpenAI Usage', list(ADDED_USAGE_COLUMNS))
    existing = [
        item for item in sheets.rows(sid, 'OpenAI Usage')
        if str(item.get('Call ID') or '') == call_id or str(item.get('Response ID') or '') == str(record.get('response_id') or '')
    ]
    if len(existing) > 1:
        raise UsageAccountingError(f'More than one usage row exists for response {record.get("response_id")}')
    if existing:
        if not _same_tokens(existing[0], record):
            raise UsageAccountingError(f'Usage for response {record.get("response_id")} already exists and does not match this response')
        return 'existing'
    sheets.write(sid, 'OpenAI Usage', row, ('Call ID', call_id))
    return 'created'


def annotate_usage(sheets, sid: str, response_id: str, status: str, failure_reason: str = '') -> None:
    """Change only the status of an existing response. Token counts stay as stored."""
    call_id = usage_call_id(response_id)
    ensure = getattr(sheets, 'ensure_columns', None)
    if callable(ensure):
        ensure(sid, 'OpenAI Usage', list(ADDED_USAGE_COLUMNS))
    matches = [item for item in sheets.rows(sid, 'OpenAI Usage') if str(item.get('Call ID') or '') == call_id]
    if len(matches) != 1:
        raise UsageAccountingError(f'Cannot annotate response {response_id}: expected one usage row, found {len(matches)}')
    sheets.write(sid, 'OpenAI Usage', {
        'Status': status,
        'Failure Reason': str(_safe(failure_reason or ''))[:300],
    }, ('Call ID', call_id))


def write_usage_outbox(directory: str | os.PathLike[str], record: dict, error: BaseException) -> Path:
    """Keep a recoverable copy when the register write fails. No credentials are stored."""
    root = Path(directory)
    folder = root / 'usage-outbox'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    response_id = re.sub(r'[^A-Za-z0-9._-]+', '-', str(record.get('response_id') or 'no-response'))[:80] or 'no-response'
    path = folder / f'{response_id}.json'
    payload = {
        'record': _safe(record),
        'error': str(_safe(str(error)))[:500],
        'recoverable': True,
    }
    fd, temp_name = tempfile.mkstemp(prefix=path.name + '.', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return path


def billed_cost(rows: list[dict]) -> Decimal:
    total = Decimal(0)
    for row in rows:
        if str(row.get('Billed') or 'Yes') == 'No':
            continue
        total += Decimal(str(row.get('Estimated Cost USD') or 0))
    return total


def reconcile_usage(*, diagnostics: list[dict], register_rows: list[dict]) -> dict:
    """Compare saved diagnostic responses with register rows. This does not call a provider."""
    events = [item for item in diagnostics if item.get('event') == 'response_received' and item.get('response_id')]
    billed = [row for row in register_rows if str(row.get('Billed') or 'Yes') != 'No' and str(row.get('Response ID') or '').strip()]
    by_id: dict[str, list[dict]] = {}
    for row in billed:
        by_id.setdefault(str(row.get('Response ID')), []).append(row)
    duplicates = sorted(key for key, rows in by_id.items() if len(rows) > 1)
    seen = {str(item.get('response_id')) for item in events}
    missing = sorted(str(item.get('response_id')) for item in events if str(item.get('response_id')) not in by_id)
    unmatched_rows = sorted(key for key in by_id if key not in seen)
    mismatches = []
    for event in events:
        rows = by_id.get(str(event.get('response_id'))) or []
        if len(rows) != 1:
            continue
        row = rows[0]
        for column, key in _TOKEN_COLUMNS:
            if key not in event or event.get(key) == '':
                continue
            actual = str(row.get(column) or '').strip()
            if not actual or int(float(actual)) != int(event[key]):
                mismatches.append({'response_id': event.get('response_id'), 'field': column, 'diagnostic': event.get(key), 'register': row.get(column)})
        if event.get('estimated_cost_usd') not in (None, '') and str(row.get('Estimated Cost USD') or '') != str(event.get('estimated_cost_usd')):
            mismatches.append({
                'response_id': event.get('response_id'),
                'field': 'Estimated Cost USD',
                'diagnostic': event.get('estimated_cost_usd'),
                'register': row.get('Estimated Cost USD'),
            })
    return {
        'diagnostic_responses': len(events),
        'register_responses': len(by_id),
        'missing_from_register': missing,
        'duplicate_response_ids': duplicates,
        'register_rows_without_diagnostic': unmatched_rows,
        'mismatches': mismatches,
        'ok': not missing and not duplicates and not mismatches,
    }


def record_live_usage(settings, record: dict, batch: dict, cost: str) -> None:
    """Usage-event ingestion only. This does not enable shadow writes or backfill."""
    if not getattr(settings, 'database_usage_ingestion_enabled', False):
        return
    from app.control_plane.usage_ingest import ingest_live_usage
    ingest_live_usage(settings, record, batch, cost)
