"""Choose which enumerated images may enter a job.

A folder authorized by itself is unchanged. A parent that contains F-folder
children discovers every immediate F* child (F01, F02, … F10, …), queues them
in natural numeric order, and omits sibling folders that are not F-folders.
"""
from __future__ import annotations

import re
from collections import defaultdict

# Immediate child folders named F + digits (F01, F1, F10, …). Not a fixed range.
F_FOLDER_RE = re.compile(r'^F(\d+)$', re.IGNORECASE)


def f_folder_number(name) -> int | None:
    match = F_FOLDER_RE.fullmatch(str(name or '').strip())
    return int(match.group(1)) if match else None


def is_f_folder_name(name) -> bool:
    return f_folder_number(name) is not None


def natural_f_key(name):
    number = f_folder_number(name)
    if number is None:
        return (1, 0, str(name or ''))
    return (0, number, str(name or ''))


def canonical_f_label(name) -> str:
    """Normalize F01/F1-style names to zero-padded F## labels for display/identity."""
    number = f_folder_number(name)
    if number is None:
        return str(name or '')
    return f'F{number:02d}' if number < 100 else f'F{number}'


def detect_ambiguous_f_folders(folder_names):
    """Return human-readable conflicts when more than one child maps to the same F number."""
    by_number = defaultdict(list)
    for name in folder_names:
        number = f_folder_number(name)
        if number is None:
            continue
        by_number[number].append(str(name))
    conflicts = []
    for number, names in sorted(by_number.items()):
        unique = sorted(set(names))
        if len(unique) > 1:
            conflicts.append(
                f"F{number:02d} is ambiguous across folder names: " + ', '.join(unique)
            )
    return conflicts


def select_authorized_sources(sources):
    """Filter an already-enumerated tree into the authorized scope.

    Leaf authorization (images directly under the authorized root, or only a
    single relative folder segment) is returned unchanged. Parent authorization
    keeps only immediate F* children and refuses an empty or ambiguous set.
    """
    if not sources:
        return []
    nested = {}
    direct = []
    for source in sources:
        relative = _relative_folders(getattr(source, 'path', ''))
        if not relative:
            direct.append(source)
            continue
        nested.setdefault(tuple(relative), []).append(source)
    if not nested:
        return list(sources)

    f_keys = [key for key in nested if len(key) == 1 and is_f_folder_name(key[0])]
    if not f_keys:
        raise ValueError(
            'Authorized folder contains subfolders, but none are named like F01, F02, …. '
            'Sibling non-F folders are not queued.'
        )
    conflicts = detect_ambiguous_f_folders([key[0] for key in f_keys])
    if conflicts:
        raise ValueError('Ambiguous F-folder identities: ' + '; '.join(conflicts))

    selected = []
    empty = []
    for key in sorted(f_keys, key=lambda item: natural_f_key(item[0])):
        group = sorted(nested.get(key, []), key=lambda item: (item.path, item.id))
        if not group:
            empty.append(key[0])
            continue
        selected.extend(group)
    if empty:
        raise ValueError(
            'Authorized F-folders contain no images and cannot be skipped: ' + ', '.join(empty)
        )
    if not selected:
        raise ValueError(
            'Authorized folder contains F-subfolders, but none exposed image sources.'
        )
    return selected


def discovered_f_folders(sources):
    """Ordered unique F-folder labels present in an authorized source list."""
    labels = []
    seen = set()
    for source in sources:
        relative = _relative_folders(getattr(source, 'path', ''))
        name = relative[0] if len(relative) == 1 and is_f_folder_name(relative[0]) else getattr(source, 'folder_name', '')
        if not is_f_folder_name(name):
            continue
        label = canonical_f_label(name)
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return sorted(labels, key=natural_f_key)


def partition_immediate_folders(folders):
    """Split immediate F folders into processable images and empty folders.

    An empty folder is Skipped / Empty. The parent run fails only when every
    eligible folder has no processable images. Inaccessible folders are not
    represented here; the caller still refuses those.
    """
    ordered = sorted(folders, key=lambda item: natural_f_key(item.get('folder_name')))
    sources = []
    skipped = []
    for item in ordered:
        images = list(item.get('images') or [])
        name = str(item.get('folder_name') or '')
        if not images:
            skipped.append({
                'folder_id': str(item.get('folder_id') or ''),
                'folder_name': name,
                'event': 'Skipped / Empty',
                'detail': f'Skipped / Empty: {name} contains no processable images.',
            })
            continue
        sources.extend(sorted(images, key=lambda source: (getattr(source, 'path', ''), getattr(source, 'id', ''))))
    if not sources:
        names = ', '.join(item['folder_name'] for item in skipped) or 'authorized folder'
        raise ValueError(f'No eligible source folder contains processable images. Skipped / Empty: {names}')
    return sources, skipped


def flag_probable_duplicates(sources):
    """Warn when different Drive files share content. Do not fail the job."""
    groups = defaultdict(list)
    for source in sources:
        digest = str(getattr(source, 'content_hash', '') or '').strip()
        if digest:
            groups[digest].append(source)
    note = (
        'Probable duplicate: another Drive file has identical content. '
        'Both files remain independently addressable.'
    )
    for group in groups.values():
        if len({source.id for source in group}) < 2:
            continue
        for source in group:
            source.duplicate_warning = note
    return sources


def group_sources_by_folder(sources):
    """Group sources by Drive folder_id, preserving natural F-folder order."""
    folders = defaultdict(list)
    order = []
    for source in sources:
        if source.folder_id not in folders:
            order.append(source.folder_id)
        folders[source.folder_id].append(source)
    ordered = []
    for folder_id in order:
        group = folders[folder_id]
        label = group[0].folder_name if group else ''
        ordered.append((natural_f_key(label), folder_id, group))
    ordered.sort(key=lambda item: item[0])
    return [(folder_id, group) for _, folder_id, group in ordered]


def _relative_folders(path):
    segments = [part for part in str(path or '').replace('\\', '/').split('/') if part]
    # "F01/file.jpg" and "/file.jpg" are images in the authorized folder itself.
    # "Chapters Box 19/F01/file.jpg" has one child folder. Deeper paths stay nested.
    if len(segments) <= 2:
        return []
    return segments[1:-1]
