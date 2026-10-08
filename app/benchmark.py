"""Read-only row comparison against the Frozen Benchmark."""
from __future__ import annotations

import json

from client_reference.commit_engine import calculate_metrics, split_assets

ADMINISTRATIVE = frozenset({
    'Ready Date', 'Committed Date', 'Completed Date', 'Inspection Date', 'Commit Date',
    'Last Updated', 'Last Checked', 'Open Context',
})

SPECS = {
    'Itemization': ('Item ID', [
        'Item ID', 'Digital Assets', 'Digital Asset Count', 'Logical Component Count', 'Template', 'Type',
        'Metadata Readiness', 'Production Task Count', 'Further Detail', 'Notes', 'Source Group / Folder',
    ]),
    'Asset QC': ('Digital Asset', [
        'Digital Asset', 'Assigned Record ID', 'Template', 'Type', 'Asset Role', 'Required Crop Standard',
        'QC Status', 'Action Needed', 'Expected Output Asset Count', 'Production Queue Eligible',
        'QC Note', 'Exception / Duplicate Note',
    ]),
    'Metadata References': ('Reference ID', [
        'Reference ID', 'Digital Assets', 'Digital Asset Count', 'Reference Role', 'Template', 'QC Status',
        'Action Needed', 'Metadata Readiness', 'Notes', 'Source Group / Folder',
    ]),
    'Ready for Metadata': ('Item ID', [
        'Item ID', 'Digital Assets', 'Digital Asset Count', 'Template', 'Type', 'Metadata Readiness', 'Ready Date', 'Notes',
    ]),
    'Production Queue': ('Task ID', [
        'Task ID', 'Assigned Record ID', 'Digital Asset', 'Action Needed', 'Expected Output Asset Count', 'Task Status',
        'Template', 'Type', 'Task Scope',
    ]),
    'Continuation State': ('State Field', ['State Field', 'Current Value', 'Last Updated']),
    'Box Summary': ('Box', [
        'Box', 'Source Assets', 'Odyssey Metadata Records', 'Metadata Only References', 'Ready Records',
        'Blocked - Cropping/QC', 'Blocked - Additional Imaging', 'Review Records', 'Content-Linked Assets',
        'Logical Components', 'Production Tasks', 'Open Item', 'Open Context',
    ]),
    'Folder Processing Unit Summary': ('Processing Unit / Source Folder', [
        'Processing Unit / Source Folder', 'Source Group / Folder', 'Enumerated New Source Count',
        'Processed New Source Count', 'Asset QC Rows', 'Odyssey Metadata Records', 'Metadata Only References',
        'Ready Records', 'Blocked Records', 'Production Tasks', 'Open Item', 'Validation Status', 'Completed Date',
    ]),
    'Project Register': ('Notes', [
        'Project', 'Processing Unit / Source Folder', 'Status', 'Box', 'Source Group / Folder',
        'Source Assets', 'Odyssey Metadata Records', 'Metadata Only References', 'Ready Records',
        'Open Item', 'Validation Result', 'Committed Date', 'Notes',
    ]),
}

COUNT_FIELDS = (
    'source_assets', 'odyssey_records', 'metadata_references', 'ready_records', 'blocked_crop_qc',
    'blocked_additional_imaging', 'review_records', 'content_linked_assets', 'logical_components',
    'production_tasks', 'crop_qc_tasks', 'additional_imaging_tasks',
)


def compare_value(value) -> str:
    if value is None:
        return ''
    text = ' '.join(str(value).strip().split())
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    return str(int(number)) if number.is_integer() else str(number)


def row_diff(actual, expected, fields):
    differences = {}
    for field in fields:
        if compare_value(actual.get(field)) != compare_value(expected.get(field)):
            differences[field] = {'live': actual.get(field, ''), 'benchmark': expected.get(field, '')}
    return differences


def internally_consistent(live) -> bool:
    asset_qc = live.get('Asset QC') or []
    names = [row.get('Digital Asset') or '' for row in asset_qc if row.get('Digital Asset')]
    if len(names) != len(set(names)):
        return False
    for row in live.get('Itemization') or []:
        if not row.get('Item ID'):
            continue
        try:
            count = int(float(row.get('Digital Asset Count') or 0))
        except ValueError:
            return False
        if len(split_assets(row.get('Digital Assets'))) != count:
            return False
    for row in live.get('Metadata References') or []:
        if not row.get('Reference ID'):
            continue
        try:
            count = int(float(row.get('Digital Asset Count') or 0))
        except ValueError:
            return False
        if len(split_assets(row.get('Digital Assets'))) != count:
            return False
    for row in live.get('Ready for Metadata') or []:
        if row.get('Item ID') and compare_value(row.get('Metadata Readiness')) != 'Ready':
            return False
    return True


def _counts(tables):
    metrics = calculate_metrics({
        'Asset QC': tables.get('Asset QC') or [],
        'Itemization': tables.get('Itemization') or [],
        'Metadata References': tables.get('Metadata References') or [],
        'Ready for Metadata': tables.get('Ready for Metadata') or [],
        'Production Queue': tables.get('Production Queue') or [],
    })
    return {name: getattr(metrics, name) for name in COUNT_FIELDS}


def _row_in_folder(row, folder) -> bool:
    folder = str(folder or '').strip()
    for key in ('Source Group / Folder', 'Processing Unit / Source Folder'):
        value = str(row.get(key) or '').strip()
        if value == folder or value.startswith(folder + ' '):
            return True
    blob = ' '.join(str(row.get(key) or '') for key in (
        'Digital Asset', 'Digital Assets', 'Assigned Record ID', 'Item ID', 'Reference ID', 'Task ID',
    ))
    return f'_{folder}_' in blob or f'-{folder}-' in blob


def _scope_folder(tables, folder):
    """Copy folder detail rows. Box-level sheets are omitted from the checkpoint."""
    scoped = {}
    for sheet in SPECS:
        if sheet in {'Continuation State', 'Box Summary', 'Project Register'}:
            scoped[sheet] = []
            continue
        scoped[sheet] = [dict(row) for row in (tables.get(sheet) or []) if _row_in_folder(row, folder)]
    return scoped


def compare_workbooks(live, benchmark, box='', folder=''):
    """Compare sheet rows. This function does not write either workbook.

    folder limits the comparison to that source folder's detail rows. Continuation
    State and box-level summaries stay out of a folder checkpoint because the
    frozen workbook is the finished six-folder register.
    """
    scoped = bool(str(folder or '').strip())
    if scoped:
        live = _scope_folder(live, folder)
        benchmark = _scope_folder(benchmark, folder)
    sheets = {}
    semantic = False
    administrative = False
    for sheet, (key, fields) in SPECS.items():
        live_rows = live.get(sheet) or []
        benchmark_rows = benchmark.get(sheet) or []
        live_map = {row.get(key): row for row in live_rows if row.get(key)}
        benchmark_map = {row.get(key): row for row in benchmark_rows if row.get(key)}
        missing = sorted(set(benchmark_map) - set(live_map))
        unexpected = sorted(set(live_map) - set(benchmark_map))
        mismatches = []
        if missing or unexpected:
            semantic = True
        for row_key in sorted(set(live_map) & set(benchmark_map)):
            diff = row_diff(live_map[row_key], benchmark_map[row_key], fields)
            if not diff:
                continue
            mismatches.append({'key': row_key, 'fields': diff})
            for field in diff:
                if field in ADMINISTRATIVE:
                    administrative = True
                else:
                    semantic = True
        sheets[sheet] = {
            'live_count': len(live_map),
            'benchmark_count': len(benchmark_map),
            'missing': missing,
            'unexpected': unexpected,
            'value_mismatches': mismatches,
            'matches': not missing and not unexpected and not mismatches,
        }
    live_counts = _counts(live)
    benchmark_counts = _counts(benchmark)
    count_differences = {
        name: {'live': live_counts[name], 'benchmark': benchmark_counts[name]}
        for name in COUNT_FIELDS if live_counts[name] != benchmark_counts[name]
    }
    if count_differences:
        semantic = True
    consistent = internally_consistent(live)
    exact = not semantic and not administrative and not count_differences
    if exact:
        outcome = 'exact_match'
    elif semantic or not consistent:
        outcome = 'semantic_benchmark_mismatch'
    else:
        outcome = 'deterministic_internal_consistency_only'
    return {
        'box': box,
        'matches': exact,
        'outcome': outcome,
        'internally_consistent': consistent,
        'sheets': sheets,
        'counts': {
            'live': live_counts,
            'benchmark': benchmark_counts,
            'differences': count_differences,
            'matches': not count_differences,
        },
        'summaries': {
            'Box Summary': sheets['Box Summary'],
            'Folder Processing Unit Summary': sheets['Folder Processing Unit Summary'],
            'Project Register': sheets['Project Register'],
        },
        'folder': folder or '',
        'whole_workbook': not scoped,
    }


F01_INTERMEDIATE = {
    'assets': 21,
    'metadata_only_references': 2,
    'normal_records': 3,
    'ready_records': 2,
    'review_open_letters': 1,
    'production_tasks': 0,
}


def f01_calibration_checkpoint(live):
    """Read-only F01 intermediate checkpoint. Does not read or write the Frozen Benchmark.

    The expected state is the client-approved F01 outcome before F02 resolves the letter:
    21 assets, 2 Metadata Only references, 3 normal records, 2 Ready, 1 Review/Open letter,
    and 0 production tasks. This is not a final Box 19 workbook match.
    """
    before = json.dumps(live, sort_keys=True, default=str)
    assets = [row for row in (live.get('Asset QC') or []) if _row_in_folder(row, 'F01')]
    references = [row for row in (live.get('Metadata References') or []) if _row_in_folder(row, 'F01')]
    items = [row for row in (live.get('Itemization') or []) if _row_in_folder(row, 'F01')]
    ready = [row for row in (live.get('Ready for Metadata') or []) if _row_in_folder(row, 'F01')]
    tasks = [row for row in (live.get('Production Queue') or []) if _row_in_folder(row, 'F01')]
    open_id = 'None'
    for row in live.get('Continuation State') or []:
        if row.get('State Field') == 'Open Intellectual Item':
            open_id = str(row.get('Current Value') or '').strip() or 'None'
    review_letters = [
        row for row in items
        if row.get('Type') == 'Letter' and row.get('Metadata Readiness') == 'Review' and row.get('Item ID') and row.get('Item ID') == open_id and open_id != 'None'
    ]
    actual = {
        'assets': len(assets),
        'metadata_only_references': len(references),
        'normal_records': len(items),
        'ready_records': len(ready),
        'review_open_letters': len(review_letters),
        'production_tasks': len(tasks),
    }
    problems = []
    for name, expected in F01_INTERMEDIATE.items():
        if actual[name] != expected:
            problems.append(f'{name} is {actual[name]}, F01 intermediate checkpoint expects {expected}')
    if any(str(row.get('Metadata Readiness') or '') != 'Ready' for row in ready):
        problems.append('Ready for Metadata contains a row that is not Ready')
    letter_ids = {row.get('Item ID') for row in review_letters}
    if any(row.get('Item ID') in letter_ids for row in ready):
        problems.append('The Review/Open letter is also on Ready for Metadata')
    others = [row for row in items if row.get('Item ID') not in letter_ids]
    if review_letters and any(row.get('Metadata Readiness') != 'Ready' for row in others):
        problems.append('A non-letter F01 record is not Ready')
    if any(str(row.get('Production Task Count') or '0') not in {'0', ''} for row in items):
        problems.append('An F01 record has a production task count')
    if json.dumps(live, sort_keys=True, default=str) != before:
        problems.append('Checkpoint mutated the live workbook')
    return {
        'checkpoint': 'f01_intermediate',
        'outcome': 'f01_intermediate_match' if not problems else 'f01_intermediate_mismatch',
        'matches': not problems,
        'expected': dict(F01_INTERMEDIATE),
        'actual': actual,
        'problems': problems,
        'open_intellectual_item': open_id,
        'whole_workbook': False,
        'final_box19_match': False,
    }
