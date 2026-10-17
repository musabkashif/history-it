"""Derived queue, box, continuation, and staging-range state.

Inspection judgments are not rewritten here. Asset QC Confidence stays the
staged asset value. Record Confidence stays on the record. The client commit
engine blanks Metadata Only Asset QC Type; Reference Role carries the role.
"""
from __future__ import annotations

import re
import time

ASSET_RANGE_END = 'X'
RECORD_RANGE_END = 'AH'
RANGE_RE = re.compile(
    r"^(?:'?(?P<sheet>[^'!]+)'?!)?(?P<start_col>[A-Z]+)(?P<start>\d+):(?P<end_col>[A-Z]+)(?P<end>\d+)$"
)


class ReconciliationConflict(ValueError):
    def __init__(self, conflicts):
        self.conflicts = list(conflicts)
        super().__init__('Reconciliation stopped on conflicts: ' + '; '.join(self.conflicts))


def canon(value) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    if text.endswith('.0') and text[:-2].isdigit():
        return text[:-2]
    return text


def completed_units(queue_rows, folder_rows):
    units = []
    boxes = {}
    for row in queue_rows:
        unit = canon(row.get('Source Group / Folder'))
        if unit and unit not in units:
            units.append(unit)
            boxes[unit] = canon(row.get('Box'))
    done = []
    for unit in units:
        for row in folder_rows:
            folder = canon(row.get('Processing Unit / Source Folder') or row.get('Source Group / Folder'))
            if folder != unit or canon(row.get('Processing Status')) != 'Completed - Validated':
                continue
            row_box = canon(row.get('Box'))
            if row_box and boxes.get(unit) and row_box != boxes[unit]:
                continue
            done.append(unit)
            break
    return done


def staging_ranges(asset_rows, record_rows):
    """A1 ranges in the client write-map shape, or empty when rows cannot prove one."""
    asset = _span(asset_rows, 'Asset Staging', ASSET_RANGE_END)
    record = _span(record_rows, 'Record Staging', RECORD_RANGE_END)
    out = {}
    if asset:
        out['Asset Staging Row Range'] = asset
    if record:
        out['Record Staging Row Range'] = record
    return out


def _span(rows, sheet, end_column):
    numbers = []
    for row in rows:
        try:
            numbers.append(int(row.get('_row')))
        except (TypeError, ValueError):
            return ''
    if not numbers:
        return ''
    start, end = min(numbers), max(numbers)
    if end - start + 1 != len(numbers) or len(numbers) != len(rows):
        return ''
    return f'{sheet}!A{start}:{end_column}{end}'


def range_matches(value, rows, sheet, end_column) -> bool:
    expected = _span(rows, sheet, end_column)
    return bool(expected) and canon(value) == expected


def _queued_units(queue_rows):
    units = []
    for row in queue_rows:
        unit = canon(row.get('Source Group / Folder'))
        if unit and unit not in units:
            units.append(unit)
    return units


def source_scope_closed(queue_rows, done, open_item) -> bool:
    """True only when every authorized queued folder is validated and nothing is open.

    Scope is the frozen Batch Queue authorization, not a hard-coded F01–F06 range.
    A smaller authorization stays open because another folder can still be added
    explicitly; whole-box completion requires the authorized queue to be finished.
    """
    units = _queued_units(queue_rows)
    if not units or not queue_rows:
        return False
    if any(canon(row.get('Batch Status')) != 'Committed - Validated' for row in queue_rows):
        return False
    if any(folder not in set(done) for folder in units):
        return False
    return canon(open_item) in ('', 'None')


def next_unprocessed_source(queue_rows, done, open_item='None'):
    for row in queue_rows:
        if canon(row.get('Batch Status')) != 'Committed - Validated':
            return canon(row.get('Start Filename'))
    if not done:
        return ''
    if source_scope_closed(queue_rows, done, open_item):
        return 'None - authorized source exhausted'
    if len(done) == 1:
        return f'None - {done[0]} source exhausted; next folder is not yet authorized'
    return 'None - authorized source exhausted (' + ', '.join(done) + '); next folder is not yet authorized'


def completed_processing_unit(queue_rows, done):
    if not done:
        return ''
    last = None
    for row in queue_rows:
        if canon(row.get('Source Group / Folder')) in done and canon(row.get('Batch Status')) == 'Committed - Validated':
            last = row
    if not last:
        return ''
    return f"{canon(last.get('Source Group / Folder'))} (Completed - Validated) through {canon(last.get('End Filename'))}"


def authorization_scope(queue_rows):
    parts = []
    for row in queue_rows:
        if canon(row.get('Batch Status')) != 'Committed - Validated':
            continue
        parts.append(
            f"{canon(row.get('Batch ID'))} Committed - Validated for {canon(row.get('Source Group / Folder'))} "
            f"({canon(row.get('Start Filename'))} through {canon(row.get('End Filename'))}, "
            f"{canon(row.get('Planned Asset Count'))} assets)"
        )
    if not parts:
        return ''
    return 'Batched - ' + '; '.join(parts) + '.'


def resume_instruction(done, open_item, queue_rows=None):
    if not done:
        return ''
    listed = ', '.join(done)
    if queue_rows is not None and source_scope_closed(queue_rows, done, open_item):
        return (
            f'Do not reprocess {listed}. No intellectual item is open. '
            'Authorized source processing is complete.'
        )
    if open_item and open_item != 'None':
        return (
            f'Do not reprocess {listed}. Do not begin {done[0]} again and do not reappend its committed assets. '
            f'Open intellectual item {open_item} remains open for the next authorized folder. '
            'Authorize the next source folder on this job before inspection.'
        )
    return (
        f'Do not reprocess {listed}. No intellectual item is open. '
        'Authorize the next source folder on this job before inspection.'
    )


def last_closed_item(record_rows, open_item):
    closed = []
    for row in record_rows:
        boundary = canon(row.get('Boundary State After Record'))
        if row.get('Record Kind') == 'Normal Odyssey' and boundary.startswith('Closed'):
            record_id = canon(row.get('Proposed Record ID'))
            if record_id and record_id != open_item:
                closed.append(record_id)
    return closed[-1] if closed else ''


def _committed_filenames(queue_rows):
    names = set()
    for row in queue_rows:
        if canon(row.get('Batch Status')) != 'Committed - Validated':
            continue
        for key in ('Start Filename', 'End Filename'):
            name = canon(row.get(key))
            if name:
                names.add(name)
    return names


def _stale_next(current, queue_rows):
    text = canon(current)
    if text in ('', 'None'):
        return True
    folded = text.casefold()
    for name in _committed_filenames(queue_rows):
        if name and name in text:
            return True
    return 'f01_001' in folded and any(
        'F01_001' in canon(row.get('Start Filename'))
        and canon(row.get('Batch Status')) == 'Committed - Validated'
        for row in queue_rows
    )


def _owned_continuation(name, current):
    """Earlier derived text from this module can advance when the queue advances."""
    text = canon(current)
    if name == 'Current Completed Processing Unit':
        return '(Completed - Validated) through ' in text
    if name == 'Next Authorized / Unprocessed Folder':
        return 'source exhausted' in text or 'not yet authorized' in text
    if name == 'Source Processing Authorization':
        return text.startswith('Batched - ')
    if name == 'Resume Instruction':
        return text.startswith('Do not reprocess ')
    if name == 'Last Closed Item':
        return re.fullmatch(r'[A-Z0-9]+-B\d+-F\d+(?:-REF)?-\d+', text) is not None
    return False


def _stale_resume(current):
    text = canon(current)
    folded = text.casefold()
    if folded in ('', 'none'):
        return True
    if 'begin chapters box 19 at f01' in folded:
        return True
    if 'enumerate the source folder once' in folded:
        return True
    return re.search(r'(?<!do not )begin f01', folded) is not None


def _continuation_value(rows, name):
    found = [row for row in rows if canon(row.get('State Field')) == name]
    if not found:
        return ''
    return canon(found[-1].get('Current Value'))


def _numbered(rows):
    if not rows:
        return False
    for row in rows:
        try:
            int(row.get('_row'))
        except (TypeError, ValueError):
            return False
    return True


def _batch_rows(rows, batch_id, key='Batch ID'):
    return [row for row in rows if canon(row.get(key)) == batch_id]


def box_status(queue_rows, done, open_item, item_rows=None) -> str:
    """Close the box only after the authorized queue is validated, nothing is open, and no classification is unresolved.

    Scope follows the frozen authorized Batch Queue, not a hard-coded F01–F06 range.
    A batch validation PASS is not whole-box completion. An empty queue is not a
    finished box. Unresolved Review is not completion. Ordinary Cropping/QC
    production tasks do not by themselves keep the box open once source scope is closed.
    """
    from app.vocabulary import unresolved_material_review
    if any(unresolved_material_review(row) for row in (item_rows or [])):
        return 'In Progress'
    if source_scope_closed(queue_rows, done, open_item):
        return 'Completed - Validated'
    return 'In Progress'


def plan_reconciliation(queue_rows, folder_rows, asset_rows, record_rows, continuation_rows, box_rows, item_rows=None):
    """Classify derived writes. Inspection fields are not in the plan."""
    decisions = []
    batch_ids = {canon(row.get('Batch ID')) for row in queue_rows}
    record_rows = [row for row in record_rows if canon(row.get('Batch ID')) in batch_ids]
    done = completed_units(queue_rows, folder_rows)
    committed = [row for row in queue_rows if canon(row.get('Batch Status')) == 'Committed - Validated']
    open_item = canon(committed[-1].get('Resulting Open Item')) if committed else ''
    if not open_item:
        open_item = 'None'
    current_open = _continuation_value(continuation_rows, 'Open Intellectual Item') or 'None'
    current_unresolved = _continuation_value(continuation_rows, 'Unresolved Boundary Item') or 'None'
    if current_open != open_item:
        decisions.append(_decision(
            'Continuation State', 'Open Intellectual Item', open_item, current_open, 'conflict',
            'Open Intellectual Item disagrees with the committed Resulting Open Item',
        ))
    else:
        decisions.append(_decision(
            'Continuation State', 'Open Intellectual Item', open_item, current_open, 'ok',
            'Open intellectual item is already the committed letter or None',
        ))
    if current_unresolved != open_item:
        decisions.append(_decision(
            'Continuation State', 'Unresolved Boundary Item', open_item, current_unresolved, 'conflict',
            'Unresolved Boundary Item disagrees with the committed open item',
        ))

    box = canon(queue_rows[-1].get('Box')) if queue_rows else ''
    box_row = next((row for row in box_rows if canon(row.get('Box')) == box), None)
    current_count = canon(box_row.get('Completed - Validated Folders')) if box_row else ''
    desired_count = str(len(done))
    if current_count == desired_count:
        count_action = 'ok'
    elif current_count == '' or (current_count.isdigit() and desired_count.isdigit() and int(current_count) <= int(desired_count)):
        count_action = 'write'
    else:
        count_action = 'conflict'
    decisions.append(_decision(
        'Box Summary', 'Completed - Validated Folders', desired_count, current_count, count_action,
        'Completed folder count is derived from Folder Processing Unit Summary',
    ))
    status_current = canon(box_row.get('Box Status')) if box_row else ''
    status_desired = box_status(queue_rows, done, open_item, item_rows)
    if status_current == status_desired:
        status_action = 'ok'
    elif status_current in ('', 'In Progress') and status_desired == 'In Progress':
        status_action = 'write' if status_current == '' else 'ok'
    elif status_current == 'Completed - Validated' and status_desired == 'In Progress':
        status_action = 'conflict'
    else:
        status_action = 'write' if status_current != status_desired else 'ok'
    decisions.append(_decision(
        'Box Summary', 'Box Status', status_desired, status_current, status_action,
        'Box status is Completed - Validated only when every authorized queued folder is validated and no intellectual item is open',
    ))

    derived = {
        'Current Completed Processing Unit': completed_processing_unit(queue_rows, done),
        'Next Authorized / Unprocessed Folder': next_unprocessed_source(queue_rows, done, open_item),
        'Source Processing Authorization': authorization_scope(queue_rows),
        'Resume Instruction': resume_instruction(done, open_item if open_item != 'None' else '', queue_rows),
        'Last Closed Item': last_closed_item(record_rows, open_item if open_item != 'None' else ''),
    }
    stale = {
        'Current Completed Processing Unit': lambda current: canon(current).casefold() in ('', 'none') or canon(current).casefold().startswith('none'),
        'Next Authorized / Unprocessed Folder': lambda current: _stale_next(current, queue_rows),
        'Source Processing Authorization': lambda current: canon(current).casefold() in ('', 'none') or 'not yet batched' in canon(current).casefold(),
        'Resume Instruction': _stale_resume,
        'Last Closed Item': lambda current: canon(current).casefold() in ('', 'none') or canon(current).casefold().startswith('none'),
    }
    for name, desired in derived.items():
        if not desired:
            continue
        current = _continuation_value(continuation_rows, name)
        if current == desired:
            action = 'ok'
        elif stale[name](current) or _owned_continuation(name, current):
            action = 'write'
        else:
            action = 'conflict'
        decisions.append(_decision('Continuation State', name, desired, current, action, 'Derived from Batch Queue and committed production rows'))

    for batch in committed:
        batch_id = canon(batch.get('Batch ID'))
        assets = _batch_rows(asset_rows, batch_id)
        records = _batch_rows(record_rows, batch_id)
        expected = staging_ranges(assets, records)
        for column, sheet, end in (
            ('Asset Staging Row Range', 'Asset Staging', ASSET_RANGE_END),
            ('Record Staging Row Range', 'Record Staging', RECORD_RANGE_END),
        ):
            desired = expected.get(column, '')
            current = canon(batch.get(column))
            source_rows = assets if column.startswith('Asset') else records
            if not source_rows:
                decisions.append(_decision('Batch Queue', column, desired, current, 'conflict', f'{batch_id} has no {sheet} rows'))
                continue
            if not _numbered(source_rows):
                if current == '':
                    continue
                decisions.append(_decision('Batch Queue', column, '', current, 'conflict', f'{batch_id} {sheet} range cannot be verified'))
                continue
            if not desired:
                decisions.append(_decision('Batch Queue', column, desired, current, 'conflict', f'{batch_id} {sheet} rows are not a contiguous range'))
                continue
            if current == desired:
                action = 'ok'
            elif current == '':
                action = 'write'
            else:
                action = 'conflict'
            decisions.append(_decision('Batch Queue', column, desired, current, action, batch_id))
    return decisions


def _decision(sheet, field, desired, current, action, reason):
    return {
        'sheet': sheet,
        'field': field,
        'desired': desired,
        'current': current,
        'action': action,
        'reason': reason,
    }


def reconcile_register(sheets, sid, job, queue_rows):
    """Write only missing derived values. Stop before the first write when a value conflicts."""
    before_assets = [dict(row) for row in sheets.rows(sid, 'Asset QC')]
    folder_rows = sheets.rows(sid, 'Folder Processing Unit Summary')
    asset_rows = sheets.rows(sid, 'Asset Staging')
    record_rows = sheets.rows(sid, 'Record Staging')
    continuation_rows = sheets.rows(sid, 'Continuation State')
    box_rows = sheets.rows(sid, 'Box Summary')
    item_rows = sheets.rows(sid, 'Itemization')
    decisions = plan_reconciliation(queue_rows, folder_rows, asset_rows, record_rows, continuation_rows, box_rows, item_rows)
    conflicts = [item for item in decisions if item['action'] == 'conflict']
    if conflicts:
        raise ReconciliationConflict([
            f"{item['sheet']} {item['field']}: current {item['current']!r} desired {item['desired']!r} ({item['reason']})"
            for item in conflicts
        ])
    wrote = []
    for item in decisions:
        if item['action'] != 'write':
            continue
        if item['sheet'] == 'Continuation State':
            sheets.set_field(sid, item['field'], item['desired'])
        elif item['sheet'] == 'Box Summary':
            box = canon(queue_rows[-1].get('Box'))
            sheets.write(sid, 'Box Summary', {'Box': box, item['field']: item['desired']}, ('Box', box))
        elif item['sheet'] == 'Batch Queue':
            batch_id = item['reason']
            sheets.write(sid, 'Batch Queue', {item['field']: item['desired']}, ('Batch ID', batch_id))
        else:
            raise ReconciliationConflict([f'Refusing to write {item["sheet"]}'])
        wrote.append({'sheet': item['sheet'], 'field': item['field'], 'value': item['desired']})
    readback = _readback(sheets, sid, queue_rows, decisions)
    mismatches = [
        f"{item['sheet']} {item['field']} read back {item['actual']!r}, expected {item['expected']!r}"
        for item in readback if item['actual'] != item['expected']
    ]
    if mismatches:
        raise ReconciliationConflict(mismatches)
    context = _read_back_run_context(sheets, sid, readback)
    after_assets = sheets.rows(sid, 'Asset QC')
    if [row.get('Digital Asset') for row in after_assets] != [row.get('Digital Asset') for row in before_assets]:
        raise ReconciliationConflict(['Asset QC changed during derived reconciliation'])
    return {
        'job_id': job,
        'wrote': wrote,
        'unchanged': [{'sheet': item['sheet'], 'field': item['field'], 'value': item['desired']} for item in decisions if item['action'] == 'ok'],
        'readback': readback,
        'run_context_readback': context,
        'open_intellectual_item': _continuation_value(sheets.rows(sid, 'Continuation State'), 'Open Intellectual Item'),
        'asset_qc_count': len(after_assets),
        'assets_appended': 0,
    }


def _readback(sheets, sid, queue_rows, decisions):
    continuation = sheets.rows(sid, 'Continuation State')
    box_rows = sheets.rows(sid, 'Box Summary')
    queue = sheets.rows(sid, 'Batch Queue')
    by_batch = {canon(row.get('Batch ID')): row for row in queue}
    out = []
    box = canon(queue_rows[-1].get('Box')) if queue_rows else ''
    for item in decisions:
        if item['action'] == 'conflict':
            continue
        if item['sheet'] == 'Continuation State':
            actual = _continuation_value(continuation, item['field'])
        elif item['sheet'] == 'Box Summary':
            row = next((candidate for candidate in box_rows if canon(candidate.get('Box')) == box), {})
            actual = canon(row.get(item['field']))
        else:
            batch = by_batch.get(item['reason'], {})
            actual = canon(batch.get(item['field']))
        out.append({
            'sheet': item['sheet'],
            'field': item['field'],
            'expected': item['desired'],
            'actual': actual,
        })
    return out


def _context_expected(readback):
    mapping = {
        ('Continuation State', 'Current Completed Processing Unit'): 'Current Completed Processing Unit',
        ('Continuation State', 'Next Authorized / Unprocessed Folder'): 'Next Authorized / Unprocessed Folder',
        ('Continuation State', 'Source Processing Authorization'): 'Current Authorization Scope',
        ('Continuation State', 'Resume Instruction'): 'Resume Instruction',
        ('Continuation State', 'Open Intellectual Item'): 'Open Intellectual Item',
        ('Box Summary', 'Completed - Validated Folders'): 'Completed - Validated Folders',
    }
    expected = {}
    for item in readback:
        label = mapping.get((item['sheet'], item['field']))
        if label:
            expected[label] = item['expected']
    return expected


def parse_run_context(grid):
    header = None
    field_idx = value_idx = None
    for index, row in enumerate(grid or []):
        labels = [canon(cell) for cell in row]
        if 'Field' in labels and 'Current Value' in labels:
            header = index
            field_idx = labels.index('Field')
            value_idx = labels.index('Current Value')
            break
    if header is None:
        return None
    parsed = {}
    for row in grid[header + 1:]:
        if len(row) <= field_idx:
            continue
        field = canon(row[field_idx])
        if not field or field == 'Field':
            continue
        value = row[value_idx] if len(row) > value_idx else ''
        if field not in parsed:
            parsed[field] = canon(value)
    return parsed


def _read_back_run_context(sheets, sid, readback):
    grid = getattr(sheets, 'grid', None)
    if grid is None:
        return None
    expected = _context_expected(readback)
    last = None
    for attempt in range(3):
        last = parse_run_context(grid(sid, 'Run Context', 'A1:E80'))
        if last is not None and all(canon(last.get(field)) == value for field, value in expected.items()):
            return {'values': {field: canon(last.get(field)) for field in expected}, 'matches': True}
        if attempt < 2:
            time.sleep(0.5)
    observed = {} if last is None else {field: canon(last.get(field)) for field in expected}
    raise ReconciliationConflict([
        f'Run Context {field} read back {observed.get(field)!r}, expected {value!r}'
        for field, value in expected.items() if observed.get(field) != value
    ] or ['Run Context could not be read back'])
