"""Choose which enumerated images may enter a job.

A folder authorized by itself is unchanged. A parent that contains the
Chapters Box 19 children queues F01 through F06 in that order and omits
every other child, including Delhivery and any copies nested inside it.
"""
from __future__ import annotations

BOX_FOLDERS = ("F01", "F02", "F03", "F04", "F05", "F06")


def select_authorized_sources(sources):
    if not sources:
        return []
    nested = {}
    direct = []
    for source in sources:
        relative = _relative_folders(getattr(source, "path", ""))
        if not relative:
            direct.append(source)
            continue
        nested.setdefault(tuple(relative), []).append(source)
    if not nested:
        return list(sources)
    selected = []
    for folder in BOX_FOLDERS:
        selected.extend(sorted(nested.get((folder,), []), key=lambda item: (item.path, item.id)))
    if not selected:
        raise ValueError(
            "Authorized folder contains subfolders, but none are named F01 through F06. "
            "Delhivery and other sibling folders are not queued."
        )
    return selected


def _relative_folders(path):
    segments = [part for part in str(path or "").replace("\\", "/").split("/") if part]
    # "F01/file.jpg" and "/file.jpg" are images in the authorized folder itself.
    # "Chapters Box 19/F01/file.jpg" has one child folder. Deeper paths stay nested.
    if len(segments) <= 2:
        return []
    return segments[1:-1]
