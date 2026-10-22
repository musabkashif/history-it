"""Read-only historical import.

The functions in this module never call Sheets write, Drive, or inspection.
They turn register rows the caller already loaded into an ImportPlan.
Missing cells stay null. Completion is claimed only from batch or validation evidence.
"""
from __future__ import annotations

import hashlib
import json
import re

from app.control_plane.models import (
    ImportPlan,
    JobSource,
    JobStatus,
    PlanBatch,
    PlanFolder,
    PlanSnapshot,
    SummaryCounts,
)
from app.google_ids import GoogleIdError, parse_drive_folder_id
from app.register_state import parse_run_context

_BATCH_NUMBER = re.compile(r'-B(\d+)$', re.IGNORECASE)
_RESPONSE_ID = re.compile(r'^[A-Za-z0-9._:-]{1,80}$')
_READ_TABS = (
    'Batch Queue',
    'Continuation State',
    'Folder Processing Unit Summary',
    'Box Summary',
    'Validation',
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def maybe_int(value) -> int | None:
    text = clean(value)
    if text is None:
        return None
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit() or (text.startswith('-') and text[1:].isdigit()):
        return int(text)
    return None


def short_error(value) -> str | None:
    text = clean(value)
    if text is None or len(text) > 500:
        return None
    if text[0] in '{[':
        return None
    return text


def response_id(value) -> str | None:
    text = clean(value)
    if text is None or not _RESPONSE_ID.fullmatch(text):
        return None
    return text


def batch_number(batch_id: str) -> int | None:
    match = _BATCH_NUMBER.search(batch_id or '')
    if not match:
        return None
    return int(match.group(1))


def commit_status_of(batch_status: str | None) -> str | None:
    if batch_status and 'Commit' in batch_status:
        return batch_status
    return None


def status_from_evidence(batches: list[dict], validation_rows: list[dict], folder_rows: list[dict]) -> tuple[JobStatus, str | None]:
    """Map recorded register rows to a control status.

    Operator notes are not rows and must not be passed here. An empty extract
    is unknown. Committed - Validated on every batch is register evidence of
    completion. A FAIL result blocks that claim.
    """
    results = []
    for row in validation_rows:
        result = clean(row.get('Result') or row.get('Validation Result') or row.get('result'))
        if result:
            results.append(result.upper())
    if any(item == 'FAIL' or item.startswith('FAIL') for item in results):
        return JobStatus.failed, 'validation_result'
    statuses = [clean(row.get('status') or row.get('Batch Status')) for row in batches]
    statuses = [item for item in statuses if item]
    if not statuses:
        folder_done = [
            clean(row.get('Processing Status')) == 'Completed - Validated' for row in folder_rows
        ]
        if folder_rows and all(folder_done) and results and all(item == 'PASS' for item in results):
            return JobStatus.validated, 'folder_summary_and_validation'
        return JobStatus.unknown, None
    if any('Blocked' in item for item in statuses):
        return JobStatus.blocked, 'batch_status'
    if any('Failed' in item for item in statuses):
        return JobStatus.failed, 'batch_status'
    if any(item == 'Paused' for item in statuses):
        return JobStatus.paused, 'batch_status'
    if any(item.startswith('Running') or item == 'Inspection In Progress' for item in statuses):
        return JobStatus.running, 'batch_status'
    if all(item == 'Committed - Validated' for item in statuses):
        if results and all(item == 'PASS' for item in results):
            return JobStatus.completed, 'batch_status_and_validation'
        if results:
            return JobStatus.unknown, None
        return JobStatus.completed, 'batch_status'
    if any(item == 'Authorized / Pending Inspection' for item in statuses):
        return JobStatus.queued, 'batch_status'
    if any('Pending Validation' in item or 'Awaiting Commit' in item for item in statuses):
        return JobStatus.queued, 'batch_status'
    return JobStatus.unknown, None


def folder_identity(url: str | None, name: str | None, order: int) -> tuple[str, str | None, str]:
    if url:
        try:
            return parse_drive_folder_id(url), url, 'drive_url'
        except GoogleIdError:
            pass
    if name:
        return f'name:{name}', None, 'folder_name'
    return f'unknown:{order}', None, 'unknown'


def _sum_if_complete(values: list[int | None]) -> int | None:
    if not values or any(item is None for item in values):
        return None
    return sum(values)


def _attempt(inspection: int | None, commit: int | None) -> int | None:
    present = [item for item in (inspection, commit) if item is not None]
    if not present:
        return None
    return max(present)


def _checksum(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _validation_view(rows: list[dict], batch_ids: set[str], job_id: str) -> list[dict]:
    viewed = []
    for row in rows:
        check_id = clean(row.get('Check ID'))
        scope = clean(row.get('Scope'))
        result = clean(row.get('Result') or row.get('Validation Result'))
        belongs = scope == job_id or scope in batch_ids or any(
            check_id and check_id.startswith(batch_id) for batch_id in batch_ids
        )
        if not belongs:
            continue
        if check_id is None and result is None:
            continue
        viewed.append({'check_id': check_id, 'scope': scope, 'result': result})
    return viewed[:200]


def _response_ids_by_batch(usage_rows: list[dict] | None) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for row in usage_rows or []:
        batch_id = clean(row.get('Batch ID'))
        identifier = response_id(row.get('Response ID'))
        if not batch_id or not identifier:
            continue
        found.setdefault(batch_id, [])
        if identifier not in found[batch_id]:
            found[batch_id].append(identifier)
    return found


def plan_from_extract(extract: dict, *, source: JobSource, job_id: str) -> ImportPlan:
    """Build a plan from an already-read extract. Does not contact Google."""
    warnings = list(extract.get('warnings') or [])
    queue = [row for row in (extract.get('batch_queue') or []) if clean(row.get('Job ID')) == job_id]
    skipped = len(extract.get('batch_queue') or []) - len(queue)
    if skipped:
        warnings.append('Batch Queue rows for other job ids were ignored.')
    if extract.get('batch_queue') is not None and not queue and extract.get('batch_queue'):
        warnings.append('Batch Queue has no rows for this job id. Completion was not claimed from the queue.')

    folder_rows = list(extract.get('folder_rows') or [])
    validation_rows = list(extract.get('validation_rows') or [])
    box_rows = list(extract.get('box_rows') or [])
    context = extract.get('run_context') or {}
    index = extract.get('job_index') or {}
    usage_ids = _response_ids_by_batch(extract.get('usage_rows'))

    project = clean(extract.get('project_name'))
    if project is None and queue:
        projects = {clean(row.get('Project')) for row in queue}
        projects.discard(None)
        if len(projects) == 1:
            project = next(iter(projects))
        elif len(projects) > 1:
            warnings.append('Batch Queue contains more than one project for this job. Project was left unknown.')
    if project is None:
        project = clean(index.get('Project'))
    context_job = clean(context.get('Job ID'))
    if context_job and context_job != job_id:
        warnings.append('Run Context job id does not match this job. Run Context values were not copied.')
    elif project is None:
        project = clean(context.get('Project'))

    boxes = {clean(row.get('Box')) for row in queue}
    boxes.discard(None)
    box = clean(extract.get('box_label'))
    if box is None and len(boxes) == 1:
        box = next(iter(boxes))
    elif box is None and len(boxes) > 1:
        warnings.append('Batch Queue contains more than one box for this job. Box was left unknown.')

    parent_id = clean(extract.get('parent_folder_id')) or clean(index.get('Source Folder ID'))
    parent_url = clean(extract.get('parent_folder_url'))
    register_id = clean(extract.get('register_id')) or clean(index.get('Spreadsheet ID'))
    register_url = clean(extract.get('register_url'))

    folders: list[PlanFolder] = []
    folder_ids: dict[str, str] = {}
    order = 0
    for row in queue:
        name = clean(row.get('Source Group / Folder'))
        url = clean(row.get('Source Folder URL'))
        key = name or url or ''
        if key in folder_ids:
            continue
        order += 1
        folder_id, folder_url, kind = folder_identity(url, name, order)
        folder_ids[key] = folder_id
        summary = _folder_summary(folder_rows, name, box)
        asset_count = maybe_int((summary or {}).get('Enumerated New Source Count'))
        processing = clean((summary or {}).get('Processing Status'))
        authorization = clean((summary or {}).get('Authorization Status'))
        completed = clean((summary or {}).get('Completed Date')) if processing == 'Completed - Validated' else None
        folders.append(PlanFolder(
            folder_id=folder_id,
            folder_url=folder_url,
            folder_name=name,
            folder_identifier_source=kind,  # type: ignore[arg-type]
            box_label=clean((summary or {}).get('Box')) or box,
            discovery_order=order,
            authorization_status=authorization,
            processing_status=processing,
            asset_count=asset_count,
            completed_at=completed,
        ))

    batches: list[PlanBatch] = []
    seen_batches: set[str] = set()
    for row in queue:
        batch_id = clean(row.get('Batch ID'))
        if not batch_id:
            warnings.append('A Batch Queue row without a Batch ID was skipped.')
            continue
        if batch_id in seen_batches:
            warnings.append(f'Duplicate Batch Queue id {batch_id} was kept once.')
            continue
        seen_batches.add(batch_id)
        name = clean(row.get('Source Group / Folder'))
        url = clean(row.get('Source Folder URL'))
        folder_id = folder_ids.get(name or url or '')
        if folder_id is None:
            folder_id, _, _ = folder_identity(url, name, len(folders) + 1)
        status = clean(row.get('Batch Status'))
        inspection = maybe_int(row.get('Inspection Attempt'))
        commit = maybe_int(row.get('Commit Attempt'))
        batches.append(PlanBatch(
            folder_id=folder_id,
            batch_id=batch_id,
            batch_number=batch_number(batch_id),
            asset_count=maybe_int(row.get('Planned Asset Count')),
            start_filename=clean(row.get('Start Filename')),
            end_filename=clean(row.get('End Filename')),
            status=status,
            attempt_count=_attempt(inspection, commit),
            inspection_attempt=inspection,
            commit_attempt=commit,
            validation_status=clean(row.get('Validation Status')),
            commit_status=commit_status_of(status),
            prior_open_item=clean(row.get('Prior Open Item')),
            resulting_open_item=clean(row.get('Resulting Open Item')),
            error_information=short_error(row.get('Error / Retry Note')),
            response_ids=usage_ids.get(batch_id, []),
            completed_at=clean(row.get('Commit Date')) if status == 'Committed - Validated' else None,
        ))

    for folder in folders:
        queued = [item.asset_count for item in batches if item.folder_id == folder.folder_id]
        queued_sum = _sum_if_complete(queued)
        if folder.asset_count is None:
            folder.asset_count = queued_sum
        elif queued_sum is not None and queued_sum != folder.asset_count:
            warnings.append(
                f'Folder {folder.folder_name or folder.folder_id} asset count {folder.asset_count} '
                f'does not match the queued sum {queued_sum}. The folder-summary count was kept.'
            )

    batch_ids = {item.batch_id for item in batches}
    validation = _validation_view(validation_rows, batch_ids, job_id) if batch_ids or validation_rows else []
    if validation_rows and not batch_ids:
        validation = _validation_view(validation_rows, set(), job_id)
    job_status, evidence = status_from_evidence(
        [item.model_dump() for item in batches],
        validation,
        folder_rows if batches else [],
    )
    if extract.get('summary_origin') == 'operator_notes':
        job_status = JobStatus.unknown
        evidence = None

    asset_count = _sum_if_complete([item.asset_count for item in batches]) if batches else maybe_int(extract.get('asset_count'))
    last_batch = batches[-1].asset_count if batches else maybe_int(extract.get('last_batch_asset_count'))
    completed_batches = sum(1 for item in batches if item.status == 'Committed - Validated') if batches else None
    failed_batches = sum(1 for item in batches if item.status and 'Failed' in item.status) if batches else None
    counts = SummaryCounts(
        asset_count=asset_count if batches or extract.get('asset_count') is not None else None,
        batch_count=len(batches) if batches else maybe_int(extract.get('batch_count')),
        folder_count=len(folders) if folders else maybe_int(extract.get('folder_count')),
        completed_batch_count=completed_batches if batches else None,
        failed_batch_count=failed_batches if batches else None,
        last_batch_asset_count=last_batch,
    )
    if not batches and extract.get('summary_origin') == 'operator_notes':
        counts = SummaryCounts(
            asset_count=maybe_int(extract.get('asset_count')),
            batch_count=maybe_int(extract.get('batch_count')),
            folder_count=maybe_int(extract.get('folder_count')),
            completed_batch_count=maybe_int(extract.get('completed_batch_count')),
            last_batch_asset_count=maybe_int(extract.get('last_batch_asset_count')),
        )

    errors = [item.error_information for item in batches if item.error_information]
    pass_count = sum(1 for item in validation if (item.get('result') or '').upper() == 'PASS')
    fail_count = sum(1 for item in validation if (item.get('result') or '').upper().startswith('FAIL'))
    if fail_count:
        validation_result = 'fail'
    elif pass_count and job_status == JobStatus.completed:
        validation_result = 'pass'
    elif pass_count and job_status == JobStatus.validated:
        validation_result = 'pass'
    else:
        validation_result = None if not validation else 'unknown'
    snapshot_counts = {
        'batch_count': counts.batch_count,
        'asset_count': counts.asset_count,
        'folder_count': counts.folder_count,
        'validation_pass': pass_count,
        'validation_fail': fail_count,
        'box_rows': len([row for row in box_rows if not box or clean(row.get('Box')) in (None, box)]),
    }
    response_ids = sorted({identifier for values in usage_ids.values() for identifier in values})
    continuation: dict[str, str | None] = {}
    for row in extract.get('continuation_rows') or []:
        field = clean(row.get('State Field'))
        if field and field not in continuation:
            continuation[field] = clean(row.get('Current Value'))
    box_status = None
    matched_boxes = [row for row in box_rows if not box or clean(row.get('Box')) in (None, box)]
    if len(matched_boxes) == 1:
        box_status = clean(matched_boxes[0].get('Box Status'))
    snapshot = None
    if extract.get('register_was_read') or batches or validation:
        snapshot = PlanSnapshot(
            register_url=register_url,
            register_id=register_id,
            source_folder=parent_id,
            counts=snapshot_counts,
            validation_result=validation_result,
            validation_results=validation,
            continuation=continuation,
            box_status=box_status,
            readback_checksum=_checksum(snapshot_counts),
            response_ids=response_ids,
        )

    origin = extract.get('summary_origin') or ('register' if batches or validation else 'unknown')
    if origin not in ('register', 'operator_notes', 'unknown'):
        origin = 'unknown'
    completed_at = None
    if job_status == JobStatus.completed:
        dates = [item.completed_at for item in batches if item.completed_at]
        completed_at = dates[-1] if dates else None
        folder_dates = [item.completed_at for item in folders if item.completed_at]
        if completed_at is None and folder_dates:
            completed_at = folder_dates[-1]

    return ImportPlan(
        job_id=job_id,
        project_name=project,
        client_organization=clean(extract.get('client_organization')),
        box_label=box,
        parent_folder_url=parent_url,
        parent_folder_id=parent_id,
        register_url=register_url,
        register_id=register_id,
        register_file_reference=clean(extract.get('register_file_reference')),
        batch_size=maybe_int(extract.get('batch_size')),
        job_status=job_status,
        sheets_status=clean(extract.get('sheets_status')),
        source=source,
        summary_origin=origin,
        deployed_release=clean(extract.get('deployed_release')),
        summary_counts=counts,
        last_error=errors[-1] if errors else None,
        completion_evidence=evidence,
        completed_at=completed_at,
        folders=folders,
        batches=batches,
        snapshot=snapshot,
        warnings=warnings,
        message=clean(extract.get('message')),
        inspection_started=False,
    )


def _folder_summary(rows: list[dict], name: str | None, box: str | None) -> dict | None:
    if not name:
        return None
    matches = []
    for row in rows:
        folder = clean(row.get('Processing Unit / Source Folder') or row.get('Source Group / Folder'))
        if folder != name:
            continue
        row_box = clean(row.get('Box'))
        if box and row_box and row_box != box:
            continue
        matches.append(row)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None
    return None


def read_register(sheets, spreadsheet_id: str, job_id: str, index_id: str | None = None) -> dict:
    """Read register tabs. Callers must not pass an object whose rows() writes."""
    warnings = []

    def rows(name: str) -> list[dict]:
        try:
            loaded = sheets.rows(spreadsheet_id, name)
        except Exception:
            warnings.append(f'{name} could not be read. Missing values were left unknown.')
            return []
        return list(loaded or [])

    context = None
    grid = getattr(sheets, 'grid', None)
    if grid is not None:
        try:
            context = parse_run_context(grid(spreadsheet_id, 'Run Context', 'A1:E80'))
        except Exception:
            warnings.append('Run Context could not be read. Missing values were left unknown.')
            context = None
    else:
        warnings.append('Run Context grid is not available. Run Context values were left unknown.')

    index_row = None
    if index_id:
        try:
            for row in sheets.rows(index_id, 'HistoryIT Jobs') or []:
                if clean(row.get('Job ID')) == job_id:
                    index_row = row
                    break
        except Exception:
            warnings.append('Job index could not be read. Missing values were left unknown.')

    usage_rows = []
    try:
        for row in sheets.rows(spreadsheet_id, 'OpenAI Usage') or []:
            identifier = response_id(row.get('Response ID'))
            batch_id = clean(row.get('Batch ID'))
            if identifier and batch_id:
                usage_rows.append({'Batch ID': batch_id, 'Response ID': identifier})
    except Exception:
        warnings.append('OpenAI Usage could not be read. Response ids were left unknown.')

    return {
        'register_was_read': True,
        'summary_origin': 'register',
        'batch_queue': rows('Batch Queue'),
        'folder_rows': rows('Folder Processing Unit Summary'),
        'box_rows': rows('Box Summary'),
        'validation_rows': rows('Validation'),
        'continuation_rows': rows('Continuation State'),
        'run_context': context or {},
        'job_index': index_row or {},
        'usage_rows': usage_rows,
        'warnings': warnings,
        'register_id': spreadsheet_id,
    }


def operator_note_extract(record: dict) -> dict:
    """Known identifiers only. Status is left for the plan builder to force unknown."""
    return {
        'summary_origin': 'operator_notes',
        'register_was_read': False,
        'project_name': record.get('project_name'),
        'box_label': record.get('box_label'),
        'folder_name': record.get('folder_name'),
        'folder_count': 1 if record.get('folder_name') else None,
        'asset_count': record.get('asset_count'),
        'batch_count': record.get('batch_count'),
        'completed_batch_count': record.get('completed_batch_count'),
        'last_batch_asset_count': record.get('last_batch_asset_count'),
        'deployed_release': record.get('deployed_release'),
        'message': record.get('message'),
        'warnings': [],
    }


READ_TABS = _READ_TABS
