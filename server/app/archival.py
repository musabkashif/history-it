"""Archival acceptance beside the strict workbook diff.

The strict comparison remains exact. This assessment does not use a similarity
threshold. A different record boundary, a missing or extra task, or Ready
instead of Blocked remains a material mismatch. Descriptive prose is reported
separately and is not turned into acceptance by a fuzzy score.
"""
from __future__ import annotations

import re

from client_reference.commit_engine import split_assets

from app.vocabulary import (
    ACTIONS,
    CROP_STANDARDS,
    QC_STATUSES,
    READINESS,
    TEMPLATES,
    canonicalize,
    collapse,
    crop_standard_key,
    unresolved_material_review,
)

POLICY = (
    'Archival acceptance compares source coverage, file groups, template, type, '
    'readiness, QC status, action, production tasks, and box closeout. '
    'QC status is Pass, Action Required, or Reference Only. '
    'Benchmark Crop is equivalent to Crop / Recrop only when neither side also requires '
    'Create Derivative Crop. A derivative task is never satisfied by a plain crop. '
    'Needs Adjustment is not Action Required. '
    'A file folder recorded as Metadata Only against a benchmark scrapbook page is a '
    'documented benchmark discrepancy and still blocks acceptance. '
    'Unresolved classification Review blocks acceptance. '
    'Equivalent prose does not decide acceptance. There is no similarity threshold.'
)

_DATE = re.compile(
    r'\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{5})\b'
)
_PHRASES = (
    ('front and verso', 'front and back'),
    ('front and back', 'front and verso'),
)


def _files(value) -> tuple[str, ...]:
    return tuple(sorted(name for name in split_assets(value) if name))


def _diff(material, prose, sheet, key, field, live, benchmark, canonical_live, canonical_benchmark, reason, evidence, discrepancy=''):
    return {
        'material': material,
        'discrepancy': discrepancy,
        'prose_class': prose,
        'sheet': sheet,
        'key': key,
        'field': field,
        'raw_live': live,
        'raw_benchmark': benchmark,
        'canonical_live': canonical_live,
        'canonical_benchmark': canonical_benchmark,
        'reason': reason,
        'evidence': evidence,
    }


def _prose_key(value) -> str:
    text = collapse(value).casefold()
    text = _DATE.sub(' ', text)
    for left, right in _PHRASES:
        text = text.replace(left, 'front-surface')
        text = text.replace(right, 'front-surface')
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return ' '.join(text.split())


def _controlled_mentions(value) -> set[str]:
    folded = collapse(value).casefold()
    found = set()
    terms = sorted(READINESS | ACTIONS | QC_STATUSES | CROP_STANDARDS | TEMPLATES, key=len, reverse=True)
    for term in terms:
        if term.casefold() in folded:
            found.add(term)
    return found


def classify_prose(live, benchmark) -> str:
    """equivalent, material, or needs_human_review. Exact text is equivalent."""
    if collapse(live) == collapse(benchmark):
        return 'equivalent'
    if _prose_key(live) == _prose_key(benchmark):
        return 'equivalent'
    live_terms = _controlled_mentions(live)
    benchmark_terms = _controlled_mentions(benchmark)
    if live_terms and benchmark_terms and live_terms != benchmark_terms:
        return 'material'
    return 'needs_human_review'


def _groups(tables):
    groups = []
    for sheet, key, kind in (
        ('Itemization', 'Item ID', 'record'),
        ('Metadata References', 'Reference ID', 'reference'),
    ):
        for row in tables.get(sheet) or []:
            files = _files(row.get('Digital Assets'))
            if not files:
                continue
            groups.append({
                'sheet': sheet,
                'kind': kind,
                'id': row.get(key) or '',
                'files': files,
                'template': row.get('Template') or '',
                'type': row.get('Type') or '',
                'readiness': row.get('Metadata Readiness') or '',
            })
    return groups


def _asset_index(tables):
    return {
        row.get('Digital Asset'): row
        for row in (tables.get('Asset QC') or [])
        if row.get('Digital Asset')
    }


def action_signature(value) -> str:
    """Crop matches Crop / Recrop. A derivative requirement stays distinct."""
    text = collapse(value).casefold()
    derivative = 'derivative' in text
    if text in {'crop', 'crop / recrop'}:
        return 'base-crop'
    if derivative and ('crop / recrop' in text or text.startswith('crop +') or text.startswith('crop /')):
        return 'base-crop+derivative'
    canonical = canonicalize(value, ACTIONS)
    return canonical or collapse(value)


def _task_pairs(tables):
    pairs = []
    for row in tables.get('Production Queue') or []:
        filename = row.get('Digital Asset') or ''
        pairs.append((filename, action_signature(row.get('Action Needed'))))
    return sorted(pairs)


def _folder_evidence(tables, files) -> str:
    parts = []
    for row in (tables.get('Metadata References') or []) + (tables.get('Itemization') or []) + (tables.get('Asset QC') or []):
        row_files = set(_files(row.get('Digital Assets')))
        asset = row.get('Digital Asset') or ''
        if not (set(files) & row_files or asset in files):
            continue
        parts.append(collapse(' '.join(str(row.get(name) or '') for name in (
            'Notes', 'Further Detail', 'Review Note', 'QC Note', 'Asset Role', 'Reference Role',
        ))))
    text = ' '.join(parts).casefold()
    if any(phrase in text for phrase in ('file folder', 'manila', 'organizational enclosure', 'blank folder')):
        return text[:240]
    return ''


def _closeout_state(tables) -> str:
    status = ''
    for row in tables.get('Box Summary') or []:
        status = collapse(row.get('Box Status'))
    folded = status.casefold()
    if 'completed' in folded and 'validated' in folded:
        return 'complete'
    if folded in ('', 'in progress') or 'in progress' in folded:
        return 'in_progress' if status else ''
    return folded


def assess_archival(live, benchmark, scoped=False):
    differences = []
    live_assets = _asset_index(live)
    benchmark_assets = _asset_index(benchmark)
    live_names = set(live_assets)
    benchmark_names = set(benchmark_assets)
    for name in sorted(benchmark_names - live_names):
        differences.append(_diff(
            True, '', 'Asset QC', name, 'Digital Asset', '', name, '', name,
            'Source image is absent from the live register.', name,
        ))
    for name in sorted(live_names - benchmark_names):
        differences.append(_diff(
            True, '', 'Asset QC', name, 'Digital Asset', name, '', name, '',
            'Source image is absent from the benchmark.', name,
        ))

    live_groups = _groups(live)
    benchmark_groups = _groups(benchmark)
    live_by_files = {frozenset(group['files']): group for group in live_groups}
    benchmark_by_files = {frozenset(group['files']): group for group in benchmark_groups}
    for files, group in sorted(benchmark_by_files.items(), key=lambda item: sorted(item[0])):
        if files not in live_by_files:
            differences.append(_diff(
                True, '', group['sheet'], group['id'], 'Digital Assets', '', ', '.join(group['files']),
                '', ', '.join(group['files']),
                'Benchmark file group has no live record with the same files.',
                ', '.join(group['files']),
            ))
    for files, group in sorted(live_by_files.items(), key=lambda item: sorted(item[0])):
        if files not in benchmark_by_files:
            differences.append(_diff(
                True, '', group['sheet'], group['id'], 'Digital Assets', ', '.join(group['files']), '',
                ', '.join(group['files']), '',
                'Live file group has no benchmark record with the same files.',
                ', '.join(group['files']),
            ))
    for files in sorted(set(live_by_files) & set(benchmark_by_files), key=sorted):
        left = live_by_files[files]
        right = benchmark_by_files[files]
        evidence = ', '.join(left['files'])
        for field, approved in (
            ('template', TEMPLATES | {'Metadata Only'}),
            ('readiness', READINESS),
        ):
            raw_live = left[field]
            raw_benchmark = right[field]
            canonical_live = canonicalize(raw_live, approved) or collapse(raw_live)
            canonical_benchmark = canonicalize(raw_benchmark, approved) or collapse(raw_benchmark)
            if canonical_live == canonical_benchmark:
                continue
            folder_evidence = _folder_evidence(live, files)
            if field == 'template' and {canonical_live, canonical_benchmark} == {'Metadata Only', 'Scrapbook Page'} and folder_evidence:
                differences.append(_diff(
                    True, '', left['sheet'], left['id'], 'Template',
                    raw_live, raw_benchmark, canonical_live, canonical_benchmark,
                    'Documented benchmark discrepancy: the capture is a file folder, so Metadata Only is required. The benchmark calls the same file a scrapbook page. The mismatch is reported and blocks acceptance.',
                    folder_evidence,
                    discrepancy='benchmark_discrepancy',
                ))
                continue
            known = (canonical_live in approved or canonical_live == '') and (canonical_benchmark in approved or canonical_benchmark == '')
            differences.append(_diff(
                known, '' if known else 'needs_human_review', left['sheet'], left['id'],
                'Template' if field == 'template' else 'Metadata Readiness',
                raw_live, raw_benchmark, canonical_live, canonical_benchmark,
                'Approved controlled values differ.' if known else 'One or both values are outside the governing vocabulary.',
                evidence,
            ))
        raw_type_live = left['type']
        raw_type_benchmark = right['type']
        if collapse(raw_type_live).casefold() != collapse(raw_type_benchmark).casefold():
            differences.append(_diff(
                True, '', left['sheet'], left['id'], 'Type',
                raw_type_live, raw_type_benchmark, collapse(raw_type_live), collapse(raw_type_benchmark),
                'Type differs after whitespace and case normalization. Type has no fuzzy synonym list.',
                evidence,
            ))

    _attach_prose(live, benchmark, live_by_files, benchmark_by_files, differences)

    for name in sorted(live_names & benchmark_names):
        left = live_assets[name]
        right = benchmark_assets[name]
        live_action = action_signature(left.get('Action Needed'))
        benchmark_action = action_signature(right.get('Action Needed'))
        if live_action != benchmark_action:
            approved = live_action in ACTIONS or live_action.startswith('base-crop')
            benchmark_approved = benchmark_action in ACTIONS or benchmark_action.startswith('base-crop')
            if approved and benchmark_approved:
                differences.append(_diff(
                    True, '', 'Asset QC', name, 'Action Needed',
                    left.get('Action Needed') or '', right.get('Action Needed') or '',
                    live_action, benchmark_action,
                    'Approved actions differ. Crop matches Crop / Recrop only when neither side requires a derivative.',
                    name,
                ))
            else:
                differences.append(_diff(
                    False, 'needs_human_review', 'Asset QC', name, 'Action Needed',
                    left.get('Action Needed') or '', right.get('Action Needed') or '',
                    live_action or collapse(left.get('Action Needed')),
                    benchmark_action or collapse(right.get('Action Needed')),
                    'Action is outside the governing action table, so it is not scored as a different approved action.',
                    name,
                ))
        live_status = canonicalize(left.get('QC Status'), QC_STATUSES)
        benchmark_status = canonicalize(right.get('QC Status'), QC_STATUSES)
        if collapse(left.get('QC Status')).casefold() != collapse(right.get('QC Status')).casefold():
            if live_status and benchmark_status and live_status != benchmark_status:
                differences.append(_diff(
                    True, '', 'Asset QC', name, 'QC Status',
                    left.get('QC Status') or '', right.get('QC Status') or '',
                    live_status, benchmark_status,
                    'Approved QC statuses differ. The vocabulary is Pass, Action Required, and Reference Only.',
                    name,
                ))
            else:
                differences.append(_diff(
                    False, 'needs_human_review', 'Asset QC', name, 'QC Status',
                    left.get('QC Status') or '', right.get('QC Status') or '',
                    live_status or collapse(left.get('QC Status')),
                    benchmark_status or collapse(right.get('QC Status')),
                    'QC status is outside Pass and Reference Only. It is not mapped to another status.',
                    name,
                ))
        live_crop = crop_standard_key(left.get('Required Crop Standard'))
        benchmark_crop = crop_standard_key(right.get('Required Crop Standard'))
        if (live_crop or '') != (benchmark_crop or ''):
            if live_crop is not None and benchmark_crop is not None:
                differences.append(_diff(
                    True, '', 'Asset QC', name, 'Required Crop Standard',
                    left.get('Required Crop Standard') or '', right.get('Required Crop Standard') or '',
                    live_crop, benchmark_crop,
                    'Crop standards differ after the approved tight-border synonym.',
                    name,
                ))
            else:
                differences.append(_diff(
                    False, 'needs_human_review', 'Asset QC', name, 'Required Crop Standard',
                    left.get('Required Crop Standard') or '', right.get('Required Crop Standard') or '',
                    live_crop or collapse(left.get('Required Crop Standard')),
                    benchmark_crop or collapse(right.get('Required Crop Standard')),
                    'Crop standard text is outside the named governing standards.',
                    name,
                ))
        for field in ('QC Note', 'Asset Role', 'Exception / Duplicate Note'):
            prose_class = classify_prose(left.get(field) or '', right.get(field) or '')
            if prose_class == 'equivalent' and collapse(left.get(field)) == collapse(right.get(field)):
                continue
            if collapse(left.get(field)) == collapse(right.get(field)):
                continue
            differences.append(_diff(
                prose_class == 'material', prose_class, 'Asset QC', name, field,
                left.get(field) or '', right.get(field) or '',
                _prose_key(left.get(field) or ''), _prose_key(right.get(field) or ''),
                'Descriptive prose is not a controlled decision.',
                name,
            ))

    live_tasks = _task_pairs(live)
    benchmark_tasks = _task_pairs(benchmark)
    from collections import Counter
    live_count = Counter(live_tasks)
    benchmark_count = Counter(benchmark_tasks)
    for pair in sorted((benchmark_count - live_count).elements()):
        differences.append(_diff(
            True, '', 'Production Queue', pair[0], 'Action Needed', '', pair[1], '', pair[1],
            'Benchmark task is missing from the live queue.', f'{pair[0]} / {pair[1]}',
        ))
    for pair in sorted((live_count - benchmark_count).elements()):
        differences.append(_diff(
            True, '', 'Production Queue', pair[0], 'Action Needed', pair[1], '', pair[1], '',
            'Live task is not in the benchmark queue.', f'{pair[0]} / {pair[1]}',
        ))

    if not scoped:
        live_close = _closeout_state(live)
        benchmark_close = _closeout_state(benchmark)
        if live_close and benchmark_close and live_close != benchmark_close:
            differences.append(_diff(
                True, '', 'Box Summary', 'Box Status', 'Box Status',
                live_close, benchmark_close, live_close, benchmark_close,
                'Closeout differs. A validated batch is not box acceptance.',
                'F01-F06 completion is represented by Box Status.',
            ))
        live_rows = len(live.get('Folder Processing Unit Summary') or [])
        benchmark_rows = len(benchmark.get('Folder Processing Unit Summary') or [])
        if live_rows != benchmark_rows:
            differences.append(_diff(
                False, 'equivalent', 'Folder Processing Unit Summary', 'row-count', 'Processing Unit / Source Folder',
                str(live_rows), str(benchmark_rows), str(live_rows), str(benchmark_rows),
                'Technical-batch row count is not an archival decision. Final file groups are compared instead.',
                f'{live_rows} live rows and {benchmark_rows} benchmark rows',
            ))

    for row in (live.get('Itemization') or []) + (live.get('Metadata References') or []):
        if unresolved_material_review(row):
            differences.append(_diff(
                True, '', row.get('Item ID') and 'Itemization' or 'Metadata References',
                row.get('Item ID') or row.get('Reference ID') or '',
                'Metadata Readiness', row.get('Metadata Readiness') or '', '',
                'Review', '',
                'Unresolved classification Review blocks archival acceptance and box completion.',
                collapse(row.get('Review Note') or row.get('Notes') or ''),
            ))
    material = [item for item in differences if item['material'] and item.get('discrepancy') != 'benchmark_discrepancy']
    discrepancies = [item for item in differences if item.get('discrepancy') == 'benchmark_discrepancy']
    review = [item for item in differences if item['prose_class'] == 'needs_human_review']
    if material:
        outcome = 'archival_material_mismatch'
    elif discrepancies:
        outcome = 'archival_benchmark_discrepancy'
    elif review:
        outcome = 'archival_needs_human_review'
    else:
        outcome = 'archival_accept'
    return {
        'outcome': outcome,
        'matches': outcome == 'archival_accept',
        'policy': POLICY,
        'material_count': len(material),
        'human_review_count': len(review),
        'differences': differences,
        'match': match_summary(live, benchmark),
    }


def _rate(matched: int, total: int) -> dict:
    percent = 100 if total <= 0 else round(100 * matched / total)
    return {'matched': matched, 'total': total, 'percent': percent}


def match_summary(live, benchmark) -> dict:
    """Scores only decisions that can change acceptance. Wording and dates are omitted."""
    live_assets = _asset_index(live)
    benchmark_assets = _asset_index(benchmark)
    both = set(live_assets) & set(benchmark_assets)
    live_groups = {frozenset(group['files']): group for group in _groups(live)}
    benchmark_groups = {frozenset(group['files']): group for group in _groups(benchmark)}
    joined = set(live_groups) & set(benchmark_groups)
    template = type_name = readiness = 0
    for files in joined:
        left = live_groups[files]
        right = benchmark_groups[files]
        if (canonicalize(left['template'], TEMPLATES | {'Metadata Only'}) or collapse(left['template'])) == (canonicalize(right['template'], TEMPLATES | {'Metadata Only'}) or collapse(right['template'])):
            template += 1
        if collapse(left['type']).casefold() == collapse(right['type']).casefold():
            type_name += 1
        if (canonicalize(left['readiness'], READINESS) or collapse(left['readiness'])) == (canonicalize(right['readiness'], READINESS) or collapse(right['readiness'])):
            readiness += 1
    qc = action = crop = 0
    for name in both:
        left = live_assets[name]
        right = benchmark_assets[name]
        if (canonicalize(left.get('QC Status'), QC_STATUSES) or collapse(left.get('QC Status')).casefold()) == (canonicalize(right.get('QC Status'), QC_STATUSES) or collapse(right.get('QC Status')).casefold()):
            qc += 1
        if action_signature(left.get('Action Needed')) == action_signature(right.get('Action Needed')):
            action += 1
        if (crop_standard_key(left.get('Required Crop Standard')) or '') == (crop_standard_key(right.get('Required Crop Standard')) or ''):
            crop += 1
    from collections import Counter
    live_tasks = Counter(_task_pairs(live))
    benchmark_tasks = Counter(_task_pairs(benchmark))
    files_in_group = sum(len(files) for files in benchmark_groups if files in live_groups)
    benchmark_files_grouped = sum(len(files) for files in benchmark_groups)
    return {
        'files': _rate(len(both), len(benchmark_assets)),
        'files_in_same_group': _rate(files_in_group, benchmark_files_grouped),
        'file_groups': _rate(len(joined), len(benchmark_groups)),
        'template': _rate(template, len(joined)),
        'type': _rate(type_name, len(joined)),
        'readiness': _rate(readiness, len(joined)),
        'qc_status': _rate(qc, len(both)),
        'action': _rate(action, len(both)),
        'crop_standard': _rate(crop, len(both)),
        'tasks': _rate(sum((live_tasks & benchmark_tasks).values()), sum(benchmark_tasks.values())),
    }


def _attach_prose(live, benchmark, live_by_files, benchmark_by_files, differences):
    live_rows = {
        frozenset(_files(row.get('Digital Assets'))): row
        for row in (live.get('Itemization') or [])
        if _files(row.get('Digital Assets'))
    }
    benchmark_rows = {
        frozenset(_files(row.get('Digital Assets'))): row
        for row in (benchmark.get('Itemization') or [])
        if _files(row.get('Digital Assets'))
    }
    for files in sorted(set(live_rows) & set(benchmark_rows), key=sorted):
        left = live_rows[files]
        right = benchmark_rows[files]
        evidence = ', '.join(sorted(files))
        for field in ('Further Detail', 'Notes'):
            prose_class = classify_prose(left.get(field) or '', right.get(field) or '')
            if prose_class == 'equivalent' and collapse(left.get(field)) == collapse(right.get(field)):
                continue
            if collapse(left.get(field)) == collapse(right.get(field)):
                continue
            differences.append(_diff(
                prose_class == 'material', prose_class, 'Itemization', left.get('Item ID') or '', field,
                left.get(field) or '', right.get(field) or '',
                _prose_key(left.get(field) or ''), _prose_key(right.get(field) or ''),
                'Descriptive prose is classified separately from record boundaries.',
                evidence,
            ))
