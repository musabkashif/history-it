"""Read-only row comparison against the Frozen Benchmark."""
from __future__ import annotations

import json
from pathlib import Path

from client_reference.commit_engine import calculate_metrics, split_assets

ADMINISTRATIVE = frozenset({
    'Ready Date', 'Committed Date', 'Completed Date', 'Inspection Date', 'Commit Date',
    'Last Updated', 'Last Checked', 'Open Context',
})

FREE_TEXT = frozenset({
    'QC Note', 'Asset Role', 'Exception / Duplicate Note', 'Notes', 'Further Detail',
    'Review Note', 'Open Context', 'Reference Role',
})

F01_LETTER_TRANSITION = (
    'F01 leaves the Correspondence/Letter Review/Open when the folder does not establish its end. '
    'F02, once authorized on the same job, decides whether that same record continues or closes. '
    'A folder-scoped F01 comparison leaves Chapters Box 19 unaccepted. '
    'Whole-workbook exact_match after F01 through F06 is the Box 19 acceptance gate.'
)

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

CHECKPOINT_SHEETS = tuple(SPECS) + ('Batch Queue', 'Asset Staging', 'Record Staging')

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


def difference_class(field, live_row, benchmark_row) -> str:
    if field in ADMINISTRATIVE:
        return 'administrative'
    if field == 'Type':
        templates = {compare_value(live_row.get('Template')), compare_value(benchmark_row.get('Template'))}
        if 'Metadata Only' in templates and compare_value(live_row.get('Type')) == '':
            return 'free_text'
    if field in FREE_TEXT:
        return 'free_text'
    return 'controlled'


def _confidence_differences(live, benchmark):
    """Asset QC Confidence is not the record Confidence field and is not a controlled compare column."""
    differences = []
    for sheet, key, field in (
        ('Asset QC', 'Digital Asset', 'QC Confidence'),
        ('Itemization', 'Item ID', 'Confidence'),
    ):
        live_map = {row.get(key): row for row in (live.get(sheet) or []) if row.get(key)}
        benchmark_map = {row.get(key): row for row in (benchmark.get(sheet) or []) if row.get(key)}
        for row_key in sorted(set(live_map) & set(benchmark_map)):
            live_value = compare_value(live_map[row_key].get(field))
            benchmark_value = compare_value(benchmark_map[row_key].get(field))
            if live_value != benchmark_value:
                differences.append({
                    'sheet': sheet,
                    'key': row_key,
                    'field': field,
                    'live': live_map[row_key].get(field, ''),
                    'benchmark': benchmark_map[row_key].get(field, ''),
                })
    return differences


def _f01_letter_present(tables) -> bool:
    for row in tables.get('Itemization') or []:
        folder = str(row.get('Source Group / Folder') or '')
        item = str(row.get('Item ID') or '')
        if row.get('Type') == 'Letter' and ('F01' in folder or '-F01-' in item):
            return True
    for row in tables.get('Folder Processing Unit Summary') or []:
        if str(row.get('Source Group / Folder') or row.get('Processing Unit / Source Folder') or '') == 'F01':
            return True
    return False


def _final_box19(live, exact, scoped) -> bool:
    if scoped or not exact:
        return False
    status = {}
    for row in live.get('Folder Processing Unit Summary') or []:
        unit = str(row.get('Processing Unit / Source Folder') or row.get('Source Group / Folder') or '')
        status[unit] = row.get('Processing Status')
    return all(status.get(f'F0{number}') == 'Completed - Validated' for number in range(1, 7))


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
    compared_live = live
    sheets = {}
    semantic = False
    controlled = False
    free_text = False
    administrative = False
    difference_classes = []
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
            controlled = True
            for row_key in missing:
                difference_classes.append({'sheet': sheet, 'key': row_key, 'class': 'controlled', 'presence': 'missing'})
            for row_key in unexpected:
                difference_classes.append({'sheet': sheet, 'key': row_key, 'class': 'controlled', 'presence': 'unexpected'})
        for row_key in sorted(set(live_map) & set(benchmark_map)):
            diff = row_diff(live_map[row_key], benchmark_map[row_key], fields)
            if not diff:
                continue
            field_classes = {}
            for field in diff:
                kind = difference_class(field, live_map[row_key], benchmark_map[row_key])
                field_classes[field] = kind
                difference_classes.append({
                    'sheet': sheet, 'key': row_key, 'field': field, 'class': kind,
                    'live': diff[field]['live'], 'benchmark': diff[field]['benchmark'],
                })
                if kind == 'administrative':
                    administrative = True
                elif kind == 'free_text':
                    free_text = True
                    semantic = True
                else:
                    controlled = True
                    semantic = True
            mismatches.append({'key': row_key, 'fields': diff, 'field_classes': field_classes})
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
        controlled = True
    consistent = internally_consistent(live)
    exact = not semantic and not administrative and not count_differences
    if exact:
        outcome = 'exact_match'
    elif semantic or not consistent:
        outcome = 'semantic_benchmark_mismatch'
    else:
        outcome = 'deterministic_internal_consistency_only'
    folder_name = str(folder or '')
    explain = folder_name.upper() == 'F01' or _f01_letter_present(compared_live)
    confidence = _confidence_differences(compared_live, benchmark)
    from app.archival import assess_archival
    archival = assess_archival(live, benchmark, scoped=scoped)
    return {
        'box': box,
        'matches': exact,
        'outcome': outcome,
        'internally_consistent': consistent,
        'controlled_mismatch': controlled or not consistent,
        'free_text_mismatch': free_text,
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
        'difference_classes': difference_classes,
        'distinctions': {
            'asset_qc_confidence_versus_record_confidence': confidence,
        },
        'folder': folder_name,
        'whole_workbook': not scoped,
        'final_box19_match': _final_box19(compared_live, exact, scoped),
        'f01_letter_transition': F01_LETTER_TRANSITION if explain else '',
        'strict_outcome': outcome,
        'archival_outcome': archival['outcome'],
        'archival_matches': archival['matches'],
        'archival_policy': archival['policy'],
        'archival_differences': archival['differences'],
        'archival_match': archival['match'],
    }


F01_INTERMEDIATE = {
    'assets': 21,
    'metadata_only_references': 2,
    'normal_records': 3,
    'ready_records': 2,
    'review_open_letters': 1,
    'production_tasks': 0,
}


def _approved_f01_examples():
    path = Path(__file__).resolve().parents[1] / 'project_references' / 'tri-delta-chapters-f01-calibration.json'
    return json.loads(path.read_text(encoding='utf-8'))['examples']


def _continuation_value(rows, name):
    for row in rows or []:
        if row.get('State Field') == name:
            return str(row.get('Current Value') or '').strip()
    return ''


def _asset_names(row):
    return split_assets(row.get('Digital Assets'))


def f01_calibration_checkpoint(live):
    """Read-only F01 intermediate checkpoint. Does not read or write the Frozen Benchmark.

    Counts are necessary and not sufficient. The checkpoint also requires the
    approved filename-to-record assignment, Template/Type, readiness, the open
    letter, and queue, folder, box, continuation, and staging agreement.
    """
    before = json.dumps(live, sort_keys=True, default=str)
    assets = [row for row in (live.get('Asset QC') or []) if _row_in_folder(row, 'F01')]
    references = [row for row in (live.get('Metadata References') or []) if _row_in_folder(row, 'F01')]
    items = [row for row in (live.get('Itemization') or []) if _row_in_folder(row, 'F01')]
    ready = [row for row in (live.get('Ready for Metadata') or []) if _row_in_folder(row, 'F01')]
    tasks = [row for row in (live.get('Production Queue') or []) if _row_in_folder(row, 'F01')]
    continuation = live.get('Continuation State') or []
    open_id = _continuation_value(continuation, 'Open Intellectual Item') or 'None'
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
    problems.extend(_f01_assignment_problems(live, assets, references, items, ready, open_id))
    problems.extend(_f01_state_problems(live, open_id, review_letters))
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
    letter_assets = [row for row in assets if str(row.get('Digital Asset') or '').endswith(tuple(f'F01_{number:03d}.jpg' for number in range(16, 22)))]
    letter_confidence = sorted({str(row.get('QC Confidence') or '') for row in letter_assets})
    if json.dumps(live, sort_keys=True, default=str) != before:
        problems.append('Checkpoint mutated the live workbook')
    verified = {
        'filename_assignment': not any('assigned' in problem or 'filename' in problem or 'record' in problem for problem in problems),
        'template_type': not any('Template' in problem or 'Type' in problem for problem in problems),
        'readiness': not any('Readiness' in problem or 'Ready' in problem for problem in problems),
        'open_letter': not any('open' in problem.casefold() or 'letter' in problem.casefold() for problem in problems),
        'queue_folder_box_continuation_staging': not any(
            token in problem for problem in problems
            for token in ('Batch Queue', 'Folder Processing', 'Box Summary', 'Continuation', 'Staging')
        ),
    }
    return {
        'checkpoint': 'f01_intermediate',
        'outcome': 'f01_intermediate_match' if not problems else 'f01_intermediate_mismatch',
        'matches': not problems,
        'expected': dict(F01_INTERMEDIATE),
        'actual': actual,
        'problems': problems,
        'verified': verified,
        'open_intellectual_item': open_id,
        'whole_workbook': False,
        'final_box19_match': False,
        'distinctions': {
            'asset_qc_confidence': (
                'Asset QC Confidence is the asset-level QC Confidence column. '
                'The approved F01 letter confidence Medium is Record Staging and Itemization Confidence. '
                'Those fields are not rewritten to match each other.'
            ),
            'letter_asset_qc_confidence_values': letter_confidence,
            'metadata_only_type': (
                'Metadata Only Asset QC Type stays blank. The client commit engine blanks that Type, '
                'and the approved calibration type is blank. Reference Role holds Folder / Context or '
                'Descriptive Reference. Benchmark descriptive Type text is a free-text difference.'
            ),
        },
    }


def _f01_assignment_problems(live, assets, references, items, ready, open_id):
    problems = []
    by_file = {}
    for row in assets:
        filename = str(row.get('Digital Asset') or '')
        by_file.setdefault(filename, []).append(row)
    seen = set()
    for example in _approved_f01_examples():
        filenames = list(example['filenames'])
        outcome = example['outcome']
        seen.update(filenames)
        rows = []
        for filename in filenames:
            found = by_file.get(filename) or []
            if len(found) != 1:
                problems.append(f'{filename} is assigned {len(found)} Asset QC rows, expected 1')
                continue
            rows.append(found[0])
        if len(rows) != len(filenames):
            continue
        record_ids = {row.get('Assigned Record ID') for row in rows}
        if len(record_ids) != 1 or not next(iter(record_ids)):
            problems.append(f'{filenames[0]} through {filenames[-1]} are not one record')
            continue
        record_id = next(iter(record_ids))
        extras = [
            row.get('Digital Asset') for row in assets
            if row.get('Assigned Record ID') == record_id and row.get('Digital Asset') not in filenames
        ]
        if extras:
            problems.append(f'{record_id} also contains {", ".join(extras)}')
        kind = outcome.get('record_kind')
        template = outcome.get('template') or ''
        type_name = outcome.get('type') or ''
        for row in rows:
            if row.get('Template') != template:
                problems.append(f'{row.get("Digital Asset")} Template is {row.get("Template") or "blank"}; approved example is {template}')
            if (row.get('Type') or '') != type_name:
                problems.append(f'{row.get("Digital Asset")} Type is {row.get("Type") or "blank"}; approved example is {type_name or "blank"}')
        if kind == 'Metadata Only':
            matched = [row for row in references if row.get('Reference ID') == record_id]
            if len(matched) != 1:
                problems.append(f'{record_id} has {len(matched)} Metadata References rows')
                continue
            reference = matched[0]
            if set(_asset_names(reference)) != set(filenames):
                problems.append(f'{record_id} filename assignment does not match the approved Metadata Only example')
            if reference.get('Template') != 'Metadata Only':
                problems.append(f'{record_id} Template is {reference.get("Template") or "blank"}; approved example is Metadata Only')
            if reference.get('Reference Role') != outcome.get('reference_role'):
                problems.append(f'{record_id} Reference Role is {reference.get("Reference Role") or "blank"}; approved example is {outcome.get("reference_role")}')
            if reference.get('Metadata Readiness') != outcome.get('metadata_readiness'):
                problems.append(f'{record_id} Metadata Readiness is {reference.get("Metadata Readiness") or "blank"}; approved example is {outcome.get("metadata_readiness")}')
        else:
            matched = [row for row in items if row.get('Item ID') == record_id]
            if len(matched) != 1:
                problems.append(f'{record_id} has {len(matched)} Itemization rows')
                continue
            item = matched[0]
            if set(_asset_names(item)) != set(filenames):
                problems.append(f'{record_id} filename assignment does not match {template} / {type_name}')
            for field, expected in (('Template', template), ('Type', type_name), ('Metadata Readiness', outcome.get('metadata_readiness'))):
                if item.get(field) != expected:
                    problems.append(f'{record_id} {field} is {item.get(field) or "blank"}; approved example is {expected}')
            if outcome.get('confidence') and item.get('Confidence') != outcome.get('confidence'):
                problems.append(f'{record_id} Confidence is {item.get("Confidence") or "blank"}; approved example is {outcome.get("confidence")}')
            if outcome.get('metadata_readiness') == 'Ready' and not any(row.get('Item ID') == record_id for row in ready):
                problems.append(f'{record_id} is Ready and missing from Ready for Metadata')
            if str(outcome.get('boundary_state') or '').startswith('Open') and record_id != open_id:
                problems.append(f'{record_id} is the open letter and Continuation State Open Intellectual Item is {open_id}')
        _boundary_problem(problems, live, record_id, outcome.get('boundary_state') or '')
    unexpected = sorted(set(by_file) - seen)
    if unexpected:
        problems.append('Unexpected F01 filename ' + ', '.join(unexpected[:5]))
    return problems


def _boundary_problem(problems, live, record_id, expected):
    if not expected:
        return
    rows = [row for row in (live.get('Record Staging') or []) if row.get('Proposed Record ID') == record_id]
    if len(rows) != 1:
        problems.append(f'{record_id} has {len(rows)} Record Staging rows')
        return
    actual = str(rows[0].get('Boundary State After Record') or '')
    if not actual.startswith(expected):
        problems.append(f'{record_id} Boundary State After Record is {actual or "blank"}; approved example starts with {expected}')


def _f01_state_problems(live, open_id, review_letters):
    problems = []
    queue = [row for row in (live.get('Batch Queue') or []) if _row_in_folder(row, 'F01')]
    folders = [row for row in (live.get('Folder Processing Unit Summary') or []) if _row_in_folder(row, 'F01')]
    boxes = [row for row in (live.get('Box Summary') or []) if str(row.get('Box') or '') == 'Chapters Box 19']
    continuation = live.get('Continuation State') or []
    if len(queue) != 1:
        problems.append(f'Batch Queue has {len(queue)} F01 rows, expected 1')
    else:
        batch = queue[0]
        if batch.get('Batch Status') != 'Committed - Validated':
            problems.append(f'Batch Queue status is {batch.get("Batch Status") or "blank"}')
        if str(batch.get('Resulting Open Item') or '') != open_id:
            problems.append(f'Batch Queue Resulting Open Item is {batch.get("Resulting Open Item") or "blank"} and the open letter is {open_id}')
        if str(batch.get('Planned Asset Count') or '') not in {'21', '21.0'}:
            problems.append(f'Batch Queue planned count is {batch.get("Planned Asset Count") or "blank"}')
        _range_problem(problems, live, batch)
    if len(folders) != 1 or folders and folders[0].get('Processing Status') != 'Completed - Validated':
        status = folders[0].get('Processing Status') if folders else 'missing'
        problems.append(f'Folder Processing Unit Summary status is {status}')
    elif str(folders[0].get('Open Item') or '') != open_id:
        problems.append(f'Folder Open Item is {folders[0].get("Open Item") or "blank"} and the open letter is {open_id}')
    completed = [row for row in (live.get('Folder Processing Unit Summary') or []) if row.get('Processing Status') == 'Completed - Validated' and str(row.get('Box') or '') == 'Chapters Box 19']
    if len(boxes) != 1:
        problems.append(f'Box Summary has {len(boxes)} Chapters Box 19 rows')
    else:
        count = str(boxes[0].get('Completed - Validated Folders') or '').strip()
        if count in {'1.0'}:
            count = '1'
        if count != str(len(completed)) or count != '1':
            problems.append(f'Box Summary Completed - Validated Folders is {count or "blank"}, expected 1')
        if str(boxes[0].get('Open Item') or '') != open_id:
            problems.append(f'Box Open Item is {boxes[0].get("Open Item") or "blank"} and the open letter is {open_id}')
    completed_unit = _continuation_value(continuation, 'Current Completed Processing Unit')
    nxt = _continuation_value(continuation, 'Next Authorized / Unprocessed Folder')
    scope = _continuation_value(continuation, 'Source Processing Authorization')
    resume = _continuation_value(continuation, 'Resume Instruction')
    folded_resume = resume.casefold()
    begins_f01 = 'begin chapters box 19 at f01' in folded_resume or (
        'begin f01' in folded_resume and 'do not begin f01' not in folded_resume
    )
    if 'F01' not in completed_unit or completed_unit.casefold().startswith('none'):
        problems.append(f'Continuation Current Completed Processing Unit is {completed_unit or "blank"}')
    if 'F01_001' in nxt:
        problems.append(f'Continuation next unprocessed source is {nxt}')
    if 'not yet batched' in scope.casefold():
        problems.append(f'Continuation authorization scope is {scope}')
    if begins_f01 or not open_id or open_id == 'None' or open_id not in resume:
        problems.append(f'Continuation Resume Instruction does not preserve open item {open_id}')
    if not review_letters:
        problems.append('The open letter is not a Review Correspondence/Letter')
    return problems


def _range_problem(problems, live, batch):
    from app.register_state import range_matches
    batch_id = batch.get('Batch ID')
    assets = [row for row in (live.get('Asset Staging') or []) if row.get('Batch ID') == batch_id]
    records = [row for row in (live.get('Record Staging') or []) if row.get('Batch ID') == batch_id]
    if not range_matches(batch.get('Asset Staging Row Range'), assets, 'Asset Staging', 'X'):
        problems.append(f'Batch Queue Asset Staging Row Range is {batch.get("Asset Staging Row Range") or "blank"}')
    if not range_matches(batch.get('Record Staging Row Range'), records, 'Record Staging', 'AH'):
        problems.append(f'Batch Queue Record Staging Row Range is {batch.get("Record Staging Row Range") or "blank"}')
