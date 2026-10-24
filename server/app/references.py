"""Approved project references. Calibration examples are not commit rules."""
from __future__ import annotations

import json
from pathlib import Path


class ReferenceError(ValueError):
    """An approved reference is required and cannot be loaded, or staging contradicts it."""


_NO_TASK = frozenset({'', 'no adjustment', 'n/a', 'review'})
_CALIBRATION_CLASS = 'client-approved-calibration-example'


def references_dir(settings) -> Path:
    configured = getattr(settings, 'project_references_dir', None) or ''
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / 'project_references'


def benchmark_answer_reason(data) -> str:
    """Return why this object is an unapproved answer key, or an empty string.

    A client-approved calibration example may describe known filenames. Any other
    filename-to-decision mapping, record-group list, or benchmark extract is refused.
    """
    if _is_approved_calibration(data):
        if _contains_key(data, 'groups') or _contains_key(data, 'frozen_benchmark') or _contains_key(data, 'benchmark_rows'):
            return 'approved calibration must not contain executable groups or benchmark rows'
        return ''
    return _unapproved_answer(data)


def resolve_reference(directory, project, box, folder):
    """Load generic guidance, plus a listed calibration example when the folder is in scope."""
    root = Path(directory)
    index = _read_index(root)
    generic = _generic(root, index)
    match = None
    for entry in index.get('approved_calibration') or []:
        folders = [str(item).strip() for item in (entry.get('folders') or [])]
        if _same(entry.get('project'), project) and _same(entry.get('box'), box) and str(folder or '').strip() in folders:
            match = entry
            break
    if match is None:
        for entry in index.get('required') or []:
            if _same(entry.get('project'), project) and _same(entry.get('box'), box) and _same(entry.get('folder'), folder):
                loaded = _load_json(root, str(entry.get('file') or '').strip(), project, box, folder)
                if _is_approved_calibration(loaded):
                    raise ReferenceError(
                        f'{entry.get("file")} is calibration material and is not listed in approved_calibration. '
                        'It will not be sent to inspection.'
                    )
                reason = benchmark_answer_reason(loaded)
                if reason:
                    raise ReferenceError(
                        f'Reference {entry.get("file")} contains benchmark answer material ({reason}). '
                        'It will not be sent to inspection.'
                    )
                loaded = dict(loaded)
                loaded['source_file'] = str(entry.get('file') or '')
                return loaded
        return generic
    loaded = _load_json(root, str(match.get('file') or '').strip(), project, box, folder)
    if not _is_approved_calibration(loaded):
        raise ReferenceError(
            f'{match.get("file")} is listed as calibration but is not an explicitly approved calibration example. '
            'Refusing to send it to inspection.'
        )
    reason = benchmark_answer_reason(loaded)
    if reason:
        raise ReferenceError(
            f'Reference {match.get("file")} contains benchmark answer material ({reason}). '
            'It will not be sent to inspection.'
        )
    data = dict(loaded)
    data['source_file'] = str(match.get('file') or '')
    if generic:
        data['generic_method'] = generic.get('statement') or ''
    return data


def reference_mismatches(assets, records, reference, filenames) -> list[str]:
    """Report an unapproved answer key, or a contradiction of an approved calibration.

    An empty asset list only checks that the reference itself may be sent. It does
    not invent staging. A contradiction is reported and must not be repaired here.
    """
    if not reference:
        return []
    reason = benchmark_answer_reason(reference)
    if reason:
        return [f'Inspection reference contains benchmark answer material ({reason}) and will not be applied']
    if not assets and not records:
        return []
    return evidence_gaps(records) + calibration_conflicts(assets, records, reference, filenames)


def evidence_gaps(records) -> list[str]:
    """Require Review Note evidence for a Review or Open decision. Do not change the decision."""
    problems = []
    for record in records:
        boundary = _field(record, 'boundary_state', 'Boundary State After Record')
        readiness = _field(record, 'metadata_readiness', 'Metadata Readiness')
        note = _field(record, 'review_note', 'Review Note')
        needs_evidence = boundary.casefold().startswith('open') or readiness == 'Review'
        if needs_evidence and not note:
            label = _field(record, 'proposed_record_id', 'Proposed Record ID') or 'record'
            problems.append(f'{label} is Review or Open and Review Note has no evidence')
    return problems


def calibration_conflicts(assets, records, reference, filenames) -> list[str]:
    """Compare a fully present approved example with model staging. Do not rewrite staging."""
    if not _is_approved_calibration(reference):
        return []
    authorized = set(filenames)
    problems = []
    for example in reference.get('examples') or []:
        names = [str(name) for name in (example.get('filenames') or [])]
        if not names or not set(names).issubset(authorized):
            continue
        label = example.get('example_id') or 'calibration example'
        outcome = example.get('outcome') or {}
        present = [asset for asset in assets if _field(asset, 'digital_asset', 'Digital Asset') in set(names)]
        if len(present) != len(names):
            problems.append(f'{label} is missing a staging row for an approved calibration filename')
            continue
        ids = {_field(asset, 'assigned_record_id', 'Assigned Record ID') for asset in present}
        if len(ids) != 1 or '' in ids:
            problems.append(f'{label} assets were not kept on one record')
            continue
        record_id = ids.pop()
        extras = [
            _field(asset, 'digital_asset', 'Digital Asset')
            for asset in assets
            if _field(asset, 'assigned_record_id', 'Assigned Record ID') == record_id
            and _field(asset, 'digital_asset', 'Digital Asset') in authorized
            and _field(asset, 'digital_asset', 'Digital Asset') not in names
        ]
        if extras:
            problems.append(f'{label} record also contains {", ".join(extras)}')
        record = next((row for row in records if _field(row, 'proposed_record_id', 'Proposed Record ID') == record_id), None)
        if record is None:
            problems.append(f'{label} has no Record Staging row')
            continue
        problems.extend(_outcome_conflicts(label, record, present, outcome))
    problems.extend(_filename_rule_conflicts(assets, records, reference, authorized))
    return problems


def _filename_rule_conflicts(assets, records, reference, authorized) -> list[str]:
    """Check filenames named by this folder's calibration. Do not rewrite staging.

    A longer match wins, so Photograph_016b is not judged by the Photograph_016 rule.
    Rules apply only when that filename is in the authorized batch.
    """
    rules = reference.get('filename_rules') or []
    if not rules:
        return []
    problems = []
    grouped = {}
    for asset in assets:
        name = _field(asset, 'digital_asset', 'Digital Asset')
        if name not in authorized:
            continue
        matches = [rule for rule in rules if rule.get('contains') and str(rule['contains']) in name]
        if not matches:
            continue
        rule = max(matches, key=lambda item: len(str(item.get('contains') or '')))
        label = str(rule.get('contains'))
        expected_action = str(rule.get('action') or '')
        actual_action = _field(asset, 'action_needed', 'Action Needed')
        if expected_action and actual_action != expected_action:
            problems.append(
                f'{name} Action Needed is {actual_action or "blank"}; approved calibration example is {expected_action}'
            )
        if str(rule.get('production_tasks') or '').casefold() == 'none' and actual_action.casefold() not in _NO_TASK:
            problems.append(f'{name} has production action {actual_action or "blank"}; approved calibration example has no crop')
        blob = ' '.join((
            _field(asset, 'qc_note', 'QC Note'),
            _field(asset, 'task_note', 'Production Task Note'),
            _field(asset, 'exception_note', 'Exception / Duplicate Note'),
        )).casefold()
        for phrase in rule.get('note_must_include') or []:
            if str(phrase).casefold() not in blob:
                problems.append(f'{name} does not record the required calibration note {phrase}')
        expected_kind = str(rule.get('record_kind') or '')
        record_id = _field(asset, 'assigned_record_id', 'Assigned Record ID')
        record = next((row for row in records if _field(row, 'proposed_record_id', 'Proposed Record ID') == record_id), None)
        actual_kind = _field(record, 'record_kind', 'Record Kind') if record is not None else ''
        if expected_kind and actual_kind != expected_kind:
            problems.append(
                f'{name} Record Kind is {actual_kind or "blank"}; approved calibration example is {expected_kind}'
            )
        group = str(rule.get('group') or '')
        if group:
            grouped.setdefault(group, []).append(record_id)
    for group, ids in grouped.items():
        if len(set(ids)) > 1:
            problems.append(f'{group} assets were not kept on one record')
    return problems


def order_records(records, assets, reference):
    """Keep the model's record order. Calibration does not reorder staging."""
    del assets, reference
    return list(records)


def _outcome_conflicts(label, record, assets, outcome) -> list[str]:
    problems = []
    checks = (
        ('record_kind', 'record_kind', 'Record Kind'),
        ('template', 'template', 'Template'),
        ('type', 'type', 'Type'),
        ('metadata_readiness', 'metadata_readiness', 'Metadata Readiness'),
        ('confidence', 'confidence', 'Confidence'),
        ('reference_role', 'reference_role', 'Reference Role'),
    )
    for outcome_key, attr, alias in checks:
        expected = str(outcome.get(outcome_key) or '')
        if outcome_key not in outcome:
            continue
        actual = _field(record, attr, alias)
        if actual != expected:
            problems.append(f'{label} {alias} is {actual or "blank"}; approved calibration example is {expected or "blank"}')
    expected_boundary = str(outcome.get('boundary_state') or '')
    if expected_boundary:
        actual_boundary = _field(record, 'boundary_state', 'Boundary State After Record')
        if not actual_boundary.startswith(expected_boundary):
            problems.append(
                f'{label} Boundary State After Record is {actual_boundary or "blank"}; '
                f'approved calibration example starts with {expected_boundary}'
            )
    if str(outcome.get('production_tasks') or '').casefold() == 'none':
        for asset in assets:
            action = _field(asset, 'action_needed', 'Action Needed').casefold()
            if action not in _NO_TASK:
                name = _field(asset, 'digital_asset', 'Digital Asset')
                problems.append(f'{label} asset {name} has production action {action or "blank"}')
        count = _field(record, 'production_task_count', 'Production Task Count')
        if count not in {'', '0'}:
            problems.append(f'{label} Production Task Count is {count}; approved calibration example has no production tasks')
    return problems


def _is_approved_calibration(data) -> bool:
    return (
        isinstance(data, dict)
        and data.get('material_class') == _CALIBRATION_CLASS
        and data.get('approval') == 'client-approved'
        and data.get('not_an_executable_rule') is True
        and str(data.get('reference_id') or '').strip()
        and str(data.get('version') or '').strip()
        and isinstance(data.get('examples'), list)
        and bool(data.get('examples'))
    )


def _unapproved_answer(data) -> str:
    if isinstance(data, dict):
        if 'groups' in data:
            return 'record groups'
        if 'examples' in data:
            return 'unapproved answer examples'
        if 'filenames' in data and any(key in data for key in ('type', 'record_kind', 'boundary_state', 'metadata_readiness', 'template', 'outcome')):
            return 'filename group mapped to a record decision'
        for key, value in data.items():
            if str(key).lower() in {'frozen_benchmark', 'benchmark_rows', 'expected_record_id'}:
                return str(key)
            found = _unapproved_answer(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _unapproved_answer(item)
            if found:
                return found
    elif isinstance(data, str) and 'frozen benchmark' in data.lower():
        return 'Frozen Benchmark'
    return ''


def _contains_key(data, key) -> bool:
    if isinstance(data, dict):
        if key in data:
            return True
        return any(_contains_key(value, key) for value in data.values())
    if isinstance(data, list):
        return any(_contains_key(item, key) for item in data)
    return False


def _read_index(root: Path):
    index_path = root / 'index.json'
    if not index_path.is_file():
        raise ReferenceError(f'Approved project reference index is missing: {index_path}')
    try:
        return json.loads(index_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ReferenceError(f'Approved project reference index cannot be read: {index_path}') from exc


def _generic(root, index):
    generic_name = str(index.get('generic') or '').strip()
    if not generic_name:
        return None
    loaded = _load_json(root, generic_name, '', '', '')
    reason = benchmark_answer_reason(loaded)
    if reason:
        raise ReferenceError(f'Generic reference {generic_name} contains benchmark answer material ({reason}).')
    loaded = dict(loaded)
    loaded['source_file'] = generic_name
    loaded.setdefault('reference_id', generic_name)
    loaded.setdefault('version', '')
    return loaded


def _load_json(root, file_name, project, box, folder):
    path = Path(root) / file_name
    label = ' / '.join(part for part in (project, box, folder) if part) or file_name
    if not file_name or not path.is_file():
        raise ReferenceError(
            f'Approved project reference file is missing for {label}: {path}. '
            'Refusing to guess archival decisions.'
        )
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ReferenceError(f'Approved project reference file cannot be read: {path}') from exc


def _same(left, right) -> bool:
    return str(left or '').strip() == str(right or '').strip()


def _field(row, attr, alias) -> str:
    if isinstance(row, dict):
        value = row.get(alias, row.get(attr, ''))
    else:
        value = getattr(row, attr, '')
    return '' if value is None else str(value).strip()
