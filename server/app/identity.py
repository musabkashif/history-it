"""Mechanical record identity. Visual grouping stays with inspection."""
from __future__ import annotations
import json, re
from client_reference.commit_engine import CommitError, split_assets

FOLDER = re.compile(r'^(F\d+)(?:\s+-.*)?$')
FILENAME_FOLDER = re.compile(r'_(F\d+)_\d+\.[^.]+$', re.IGNORECASE)
# ROOT is the processing-unit code for images authorized directly in a non-F source folder.
CANON = re.compile(r'^(?P<prefix>[A-Z0-9]+-B\d+)-(?P<folder>F\d+|ROOT)(?P<ref>-REF)?-(?P<seq>\d+)$')
ROOT_FOLDER = 'ROOT'


def project_prefix(project, box):
    words = re.findall(r'[A-Za-z0-9]+', project or '')
    initials = ''.join(word[0] for word in words).upper()
    number = re.search(r'(\d+)\s*$', (box or '').strip())
    if not initials or not number:
        raise CommitError(f'Cannot derive record prefix from project {project!r} and box {box!r}')
    return f'{initials}-B{number.group(1)}'


def folder_code(batch):
    """Return the processing-unit folder code for this batch.

    F-folder jobs keep the existing F## rule. A root-only batch, where every
    manifest row shares one non-F folder_name and no row encodes an F-folder,
    uses ROOT. Ambiguous or empty evidence still raises CommitError.
    """
    group = (batch.get('Source Group / Folder') or '').strip()
    match = FOLDER.match(group)
    if match:
        return match.group(1)
    try:
        manifest = json.loads(batch.get('Notes') or '{}').get('manifest') or []
    except json.JSONDecodeError as exc:
        raise CommitError(f'Cannot determine source folder for batch {batch.get("Batch ID")}') from exc
    found = []
    root_names = []
    for item in manifest:
        name = (item.get('folder_name') or '').strip()
        if FOLDER.fullmatch(name):
            found.append(FOLDER.match(name).group(1))
            continue
        filename = FILENAME_FOLDER.search(item.get('name') or '')
        if filename:
            found.append(filename.group(1).upper())
            continue
        if name:
            root_names.append(name)
    unique = set(found)
    if len(unique) == 1:
        return unique.pop()
    if unique:
        raise CommitError(f'Cannot determine source folder for batch {batch.get("Batch ID")}')
    if not manifest or len(root_names) != len(manifest) or len(set(root_names)) != 1:
        raise CommitError(f'Cannot determine source folder for batch {batch.get("Batch ID")}')
    root_name = root_names[0]
    if group and group != root_name:
        raise CommitError(f'Cannot determine source folder for batch {batch.get("Batch ID")}')
    return ROOT_FOLDER


def prefix_for(project, box, ids):
    found = set()
    for value in ids:
        match = CANON.match(value or '')
        if match:
            found.add(match.group('prefix'))
    if len(found) > 1:
        raise CommitError('Conflicting record prefixes: ' + ', '.join(sorted(found)))
    if len(found) == 1:
        return found.pop()
    return project_prefix(project, box)


def _ids(rows, key):
    return [row.get(key) or '' for row in rows]


def _next(used, prefix, folder, reference):
    highest = 0
    for value in used:
        match = CANON.match(value or '')
        if not match:
            continue
        if match.group('prefix') == prefix and match.group('folder') == folder and bool(match.group('ref')) == reference:
            highest = max(highest, int(match.group('seq')))
    return highest + 1


def _format(prefix, folder, reference, seq):
    middle = '-REF' if reference else ''
    return f'{prefix}-{folder}{middle}-{seq:03d}'


def _known(value, items, refs):
    return any(row.get('Item ID') == value for row in items) or any(row.get('Reference ID') == value for row in refs)


def _committed_assets(record_id, items, refs):
    for row in items:
        if row.get('Item ID') == record_id:
            return split_assets(row.get('Digital Assets'))
    for row in refs:
        if row.get('Reference ID') == record_id:
            return split_assets(row.get('Digital Assets'))
    return []


def _stamp(row, batch, folder):
    row['Project'] = batch['Project']
    row['Job ID'] = batch['Job ID']
    row['Batch ID'] = batch['Batch ID']
    row['Box'] = batch.get('Box') or ''
    row['Source Group / Folder'] = folder
    row['Processing Unit / Source Folder'] = folder
    row['Inspection Complete'] = 'Yes'
    row['Commit Status'] = 'Pending Commit'
    return row


# Master Instructions section 11 and the action table. Review has no production
# action yet. The other names are the actions that create a queue row.
NO_QUEUE = frozenset({'no adjustment', 'n/a', 'review'})
QUEUE = frozenset({
    'crop / recrop', 'deskew', 'rotate', 'split spread', 'split spread + publication crop',
    'publication crop', 'archival border crop', 'create derivative crop',
    'crop / recrop + create derivative crop', 'reshoot',
    'additional imaging required', 'additional imaging',
    'rotate + archival border crop',
})


def _metadata_defaults(record, assets):
    record['Template'] = 'Metadata Only'
    record['Metadata Readiness'] = 'Metadata Reference'
    record['Record QC Status'] = 'Reference Only'
    record['Record Action Needed'] = 'N/A'
    record['Required Crop Standard'] = 'N/A'
    record['Production Task Count'] = '0'
    for asset in assets:
        asset['QC Status'] = 'Reference Only'
        asset['Action Needed'] = 'N/A'
        asset['Required Crop Standard'] = 'N/A'
        asset['Expected Output Asset Count'] = '0'
        asset['Production Queue Eligible'] = 'No'
        asset['Production Task Scope'] = ''
        asset['Production Task Note'] = ''


def _align_queue(asset):
    """Queue eligibility follows Action Needed. Inspection does not choose the flag."""
    action = (asset.get('Action Needed') or '').strip().casefold()
    if action in NO_QUEUE:
        asset['Production Queue Eligible'] = 'No'
        asset['Production Task Scope'] = ''
        asset['Production Task Note'] = ''
    elif action in QUEUE:
        asset['Production Queue Eligible'] = 'Yes'


def _prior_task_count(record_id, items):
    for row in items:
        if row.get('Item ID') == record_id:
            raw = (row.get('Production Task Count') or '').strip()
            if not raw:
                return 0
            try:
                return int(float(raw))
            except ValueError:
                return 0
    return 0


def _preserve_committed_record(record, canon, items, refs):
    """A close keeps the committed kind, template, and type.

    The next folder having no continuation is not a reason to rewrite a Normal
    Odyssey record as Metadata Only.
    """
    canon = (canon or '').strip()
    if not canon:
        return
    item = next((row for row in items if (row.get('Item ID') or '').strip() == canon), None)
    ref = next((row for row in refs if (row.get('Reference ID') or '').strip() == canon), None)
    if item is not None:
        record['Record Kind'] = 'Normal Odyssey'
        record['Record Operation'] = 'Update Existing Open Record'
        if (item.get('Template') or '').strip():
            record['Template'] = item['Template']
        if 'Type' in item:
            record['Type'] = item.get('Type') or ''
        allowed = {'Ready', 'Blocked - Cropping/QC', 'Blocked - Additional Imaging', 'Review'}
        readiness = (record.get('Metadata Readiness') or '').strip()
        preserved = (item.get('Metadata Readiness') or '').strip()
        if readiness not in allowed:
            record['Metadata Readiness'] = preserved if preserved in allowed else 'Ready'
        return
    if ref is not None:
        record['Record Kind'] = 'Metadata Only'
        record['Record Operation'] = 'Update Existing Open Record'
        if (ref.get('Template') or '').strip():
            record['Template'] = ref['Template']
        if 'Type' in ref:
            record['Type'] = ref.get('Type') or ''


def _asset_labels(assets):
    names = [asset.get('Digital Asset') or '' for asset in assets]
    repeated = {name for name in names if name and names.count(name) > 1}
    labels = []
    for asset in assets:
        name = asset.get('Digital Asset') or ''
        file_id = (asset.get('Drive File ID') or '').strip()
        if file_id and name in repeated:
            labels.append(f'{name} [{file_id}]')
        else:
            labels.append(name)
    return labels


_SPLIT_ALIAS = re.compile(r'^(?P<base>.+)~(?P<n>\d+)$')


def _alias_base(local: str) -> str:
    match = _SPLIT_ALIAS.fullmatch(local or '')
    return match.group('base') if match else (local or '')


def _committed_prior_target(local, items, refs, prior):
    """Return the committed id when local or its split alias names that prior update."""
    prior = (prior or '').strip()
    if prior == 'None':
        prior = ''
    base = _alias_base(local)
    if _known(local, items, refs):
        return local
    if _known(base, items, refs):
        return base
    if prior and _known(prior, items, refs) and (local == prior or base == prior):
        return prior
    return ''


def _coalesce_prior_update_aliases(assets, records, items, refs, prior):
    """Collapse id / id~N Update rows that resolve to the same committed prior."""
    remap = {}
    for record in records:
        local = (record.get('Proposed Record ID') or '').strip()
        if (record.get('Record Operation') or '').strip() != 'Update Existing Open Record':
            continue
        target = _committed_prior_target(local, items, refs, prior)
        if not target or target == local:
            continue
        remap[local] = target
        record['Proposed Record ID'] = target
    if not remap:
        return
    for asset in assets:
        local = (asset.get('Assigned Record ID') or '').strip()
        if local in remap:
            asset['Assigned Record ID'] = remap[local]
    deduped = []
    seen = set()
    for record in reversed(records):
        local = (record.get('Proposed Record ID') or '').strip()
        op = (record.get('Record Operation') or '').strip()
        key = (local, op)
        if op == 'Update Existing Open Record' and local in seen:
            continue
        if op == 'Update Existing Open Record':
            seen.add(local)
        deduped.append(record)
    records[:] = list(reversed(deduped))


def _restore_within_batch_creates(records):
    """An Update of a local id that is also Create New in this aggregate is still Create."""
    created = {
        (record.get('Proposed Record ID') or '').strip()
        for record in records
        if (record.get('Record Operation') or '').strip() == 'Create New'
    }
    for record in records:
        local = (record.get('Proposed Record ID') or '').strip()
        if (record.get('Record Operation') or '').strip() == 'Update Existing Open Record' and local in created:
            record['Record Operation'] = 'Create New'


def assign_identity(batch, assets, records, items, refs, other_records, prior):
    """Replace model-local record keys with the register id pattern.

    Inspection still decides which assets belong together, whether the record is
    Metadata Only or Normal Odyssey, and whether an existing open item continues.
    A within-batch Create may be continued without a committed Sheets row. A true
    cross-batch Update Existing still requires the committed target.
    """
    folder = folder_code(batch)
    prior = (prior or '').strip()
    if prior == 'None':
        prior = ''
    _coalesce_prior_update_aliases(assets, records, items, refs, prior)
    _restore_within_batch_creates(records)
    known_ids = _ids(items, 'Item ID') + _ids(refs, 'Reference ID') + _ids(other_records, 'Proposed Record ID')
    prefix = prefix_for(batch.get('Project'), batch.get('Box'), known_ids)
    used = list(known_ids)
    local_to_canon = {}
    records.sort(key=lambda row: 0 if (row.get('Record Operation') == 'Update Existing Open Record' or (prior and (row.get('Proposed Record ID') or '').strip() == prior)) else 1)
    for index, record in enumerate(records, 1):
        local = (record.get('Proposed Record ID') or '').strip()
        if not local:
            raise CommitError('Record Staging row is missing Proposed Record ID')
        if local in local_to_canon:
            raise CommitError(f'Duplicate staging key {local}')
        operation = record.get('Record Operation')
        if operation == 'Update Existing Open Record' or (prior and local == prior):
            canon_hint = local if _known(local, items, refs) else prior
            _preserve_committed_record(record, canon_hint, items, refs)
        kind = record.get('Record Kind')
        reference = kind == 'Metadata Only'
        operation = record.get('Record Operation')
        if operation == 'Update Existing Open Record' or (prior and local == prior):
            if _known(local, items, refs):
                canon = local
            elif prior and _known(prior, items, refs):
                canon = prior
            else:
                raise CommitError(f'Update Existing Open Record has no committed row: {local}')
            record['Record Operation'] = 'Update Existing Open Record'
        elif operation == 'Create New':
            seq = _next(used, prefix, folder, reference)
            canon = _format(prefix, folder, reference, seq)
            used.append(canon)
        else:
            raise CommitError(f'Unsupported Record Operation for {local}: {operation!r}')
        if canon in local_to_canon.values():
            raise CommitError(f'More than one staging record resolved to {canon}')
        local_to_canon[local] = canon
        record['Proposed Record ID'] = canon
        record['Record Sequence'] = str(index)
        _stamp(record, batch, folder)
    linked = []
    for index, asset in enumerate(assets, 1):
        local = (asset.get('Assigned Record ID') or '').strip()
        if local not in local_to_canon:
            raise CommitError(f"Asset {asset.get('Digital Asset')} refers to unstaged record {local}")
        asset['Assigned Record ID'] = local_to_canon[local]
        asset['Asset Sequence'] = str(index)
        _stamp(asset, batch, folder)
        linked.append(asset)
    by_id = {}
    for asset in linked:
        by_id.setdefault(asset['Assigned Record ID'], []).append(asset)
    for record in records:
        group = by_id.get(record['Proposed Record ID'], [])
        if record['Record Kind'] == 'Metadata Only':
            _metadata_defaults(record, group)
        else:
            for asset in group:
                _align_queue(asset)
            queued = sum(asset.get('Production Queue Eligible') == 'Yes' for asset in group)
            already = _prior_task_count(record['Proposed Record ID'], items) if record['Record Operation'] == 'Update Existing Open Record' else 0
            record['Production Task Count'] = str(already + queued)
        if record['Record Operation'] == 'Update Existing Open Record' and not group:
            boundary = record.get('Boundary State After Record') or ''
            if not boundary.strip() or boundary.startswith('Open'):
                record['Boundary State After Record'] = 'Closed'
        labels = _asset_labels(group)
        new_names = labels
        old_names = _committed_assets(record['Proposed Record ID'], items, refs) if record['Record Operation'] == 'Update Existing Open Record' else []
        full = old_names + [name for name in new_names if name not in old_names]
        record['New Batch Assets'] = '; '.join(new_names)
        record['Existing Committed Assets'] = '; '.join(old_names)
        record['Full Record Assets After Commit'] = '; '.join(full)
        record['Digital Asset Count'] = str(len(full))
    return folder
