"""Batch Queue authorization. Continuation State never overrides the current batch."""
from __future__ import annotations

import json


class RegisterStateError(ValueError):
    """Contradictory register state. Callers must not repair it."""


class BatchAuthorizationError(ValueError):
    """The authorized batch or its manifest cannot be determined safely."""


HOT_STATUSES = frozenset({
    'Inspection In Progress',
    'Inspection Complete - Awaiting Commit',
    'Commit Written - Pending Validation',
})


def normalize_prior(value) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    if not text or text.lower() == 'none':
        return 'None' if text else ''
    return text


def initial_queue_prior(continuation_open, unresolved, asset_qc, itemization, metadata_refs) -> str:
    """Return None for a new first batch, or raise when the register is not clean.

    Continuation State is read only to reject a dirty register. It is never copied
    onto Batch Queue.
    """
    assert_clean_first_batch('None', continuation_open, unresolved, asset_qc, itemization, metadata_refs)
    return 'None'


def assert_clean_first_batch(prior, continuation_open, unresolved, asset_qc, itemization, metadata_refs):
    """Require every clean-start condition. Do not repair the register."""
    queue_prior = normalize_prior(prior) or 'None'
    continuation = normalize_prior(continuation_open) or 'None'
    unresolved_value = normalize_prior(unresolved) or 'None'
    problems = []
    if queue_prior != 'None':
        problems.append(f'Batch Queue Prior Open Item is {queue_prior}, required None')
    if continuation != 'None':
        problems.append(f'Continuation State Open Intellectual Item is {continuation}, required None')
    if unresolved_value != 'None':
        problems.append(f'Continuation State Unresolved Boundary Item is {unresolved_value}, required None')
    if asset_qc:
        problems.append(f'Asset QC has {len(asset_qc)} data rows, required none')
    if itemization:
        problems.append(f'Itemization has {len(itemization)} data rows, required none')
    if metadata_refs:
        problems.append(f'Metadata References has {len(metadata_refs)} data rows, required none')
    if problems:
        raise RegisterStateError(
            'Clean first-batch validation failed: ' + '; '.join(problems) + '. '
            'Refusing to repair Continuation State or production rows. Inspection was not started.'
        )
    return 'None', 'Batch Queue Prior Open Item'


def assert_batch_prior(prior, continuation_open, unresolved, asset_qc, itemization, metadata_refs, *, first_batch: bool):
    """Return the Batch Queue prior. A first batch must also be a clean register."""
    if first_batch:
        return assert_clean_first_batch(prior, continuation_open, unresolved, asset_qc, itemization, metadata_refs)
    return normalize_prior(prior) or 'None', 'Batch Queue Prior Open Item'


def assert_single_active_batch(rows) -> None:
    hot = [row.get('Batch ID') or '' for row in rows if row.get('Batch Status') in HOT_STATUSES]
    if len(hot) > 1:
        raise BatchAuthorizationError(
            'More than one batch is already in progress (' + ', '.join(hot) + '). '
            'Refusing to choose a batch.'
        )


def authorized_manifest(batch) -> list:
    """Return the immutable manifest. Never expand a filename range."""
    try:
        payload = json.loads(batch.get('Notes') or '{}')
    except (TypeError, ValueError) as exc:
        raise BatchAuthorizationError('Authorized manifest cannot be determined exactly') from exc
    manifest = payload.get('manifest') if isinstance(payload, dict) else None
    if not isinstance(manifest, list) or not manifest:
        raise BatchAuthorizationError('Authorized manifest cannot be determined exactly')
    names = []
    for item in manifest:
        if not isinstance(item, dict) or not str(item.get('name') or '').strip():
            raise BatchAuthorizationError('Authorized manifest contains an entry without an exact filename')
        names.append(item['name'])
    if len(names) != len(set(names)):
        raise BatchAuthorizationError('Authorized filename appears more than once in the manifest')
    planned = str(batch.get('Planned Asset Count') or '').strip()
    if planned:
        try:
            expected = int(float(planned))
        except ValueError as exc:
            raise BatchAuthorizationError('Planned Asset Count is not an integer') from exc
        if expected != len(names):
            raise BatchAuthorizationError(
                f'Manifest count {len(names)} does not match Planned Asset Count {expected}'
            )
    start = str(batch.get('Start Filename') or '').strip()
    end = str(batch.get('End Filename') or '').strip()
    if start and start != names[0]:
        raise BatchAuthorizationError('Start Filename does not match the first manifest filename')
    if end and end != names[-1]:
        raise BatchAuthorizationError('End Filename does not match the last manifest filename')
    return manifest


def manifest_filenames(batch) -> list[str]:
    return [item['name'] for item in authorized_manifest(batch)]
