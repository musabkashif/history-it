"""In-memory control store used by tests and by local runs before MongoDB connects.

Unique indexes from indexes.INDEX_SPECS are enforced here. Documents are copied
on the way in and out so callers cannot mutate the store.
"""
from __future__ import annotations

import copy

from app.control_plane.errors import DuplicateRecord
from app.control_plane.indexes import INDEX_SPECS


class MemoryStore:
    def __init__(self):
        self.workspaces: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.folders: dict[tuple[str, str], dict] = {}
        self.batches: dict[tuple[str, str], dict] = {}
        self.events: dict[str, dict] = {}
        self.checkpoints: list[dict] = []
        self.snapshots: list[dict] = []
        self.usage_events: dict[str, dict] = {}
        self.applied_indexes: list[dict] = []

    def ensure_indexes(self) -> list[dict]:
        self.applied_indexes = [dict(item) for item in INDEX_SPECS]
        return list(self.applied_indexes)

    def upsert_workspace(self, doc: dict) -> dict:
        current = self.workspaces.get(doc['workspace_id'])
        stored = _copy(doc)
        if current is None:
            stored['version'] = int(stored.get('version') or 1)
            self.workspaces[stored['workspace_id']] = stored
            return _copy(stored)
        stored['version'] = int(current.get('version') or 1) + 1
        stored['created_at'] = current.get('created_at') or stored.get('created_at')
        self.workspaces[stored['workspace_id']] = stored
        return _copy(stored)

    def list_workspaces(self) -> list[dict]:
        return [_copy(item) for item in self.workspaces.values()]

    def get_workspace(self, workspace_id: str) -> dict | None:
        doc = self.workspaces.get(workspace_id)
        return _copy(doc) if doc else None

    def get_job(self, job_id: str) -> dict | None:
        doc = self.jobs.get(job_id)
        return _copy(doc) if doc else None

    def insert_job(self, doc: dict) -> dict:
        if doc['job_id'] in self.jobs:
            raise DuplicateRecord(doc['job_id'])
        stored = _copy(doc)
        stored['version'] = int(stored.get('version') or 1)
        self.jobs[stored['job_id']] = stored
        return _copy(stored)

    def update_job(self, job_id: str, workspace_id: str, expected_version: int, fields: dict) -> dict | None:
        current = self.jobs.get(job_id)
        if current is None or current.get('workspace_id') != workspace_id:
            return None
        if int(current.get('version') or 0) != int(expected_version):
            return None
        updated = _copy(current)
        updated.update(_copy(fields))
        updated['job_id'] = job_id
        updated['workspace_id'] = workspace_id
        updated['version'] = int(expected_version) + 1
        self.jobs[job_id] = updated
        return _copy(updated)

    def list_jobs(self, workspace_id: str | None = None) -> list[dict]:
        rows = list(self.jobs.values())
        if workspace_id:
            rows = [row for row in rows if row.get('workspace_id') == workspace_id]
        return [_copy(row) for row in rows]

    def move_job(self, job_id: str, new_workspace_id: str) -> dict:
        current = self.jobs.get(job_id)
        if current is None:
            raise KeyError(job_id)
        old = current['workspace_id']
        updated = _copy(current)
        updated['workspace_id'] = new_workspace_id
        updated['version'] = int(updated.get('version') or 1) + 1
        self.jobs[job_id] = updated
        self._retarget(self.folders, job_id, old, new_workspace_id)
        self._retarget(self.batches, job_id, old, new_workspace_id)
        for event in self.events.values():
            if event.get('job_id') == job_id and event.get('workspace_id') == old:
                event['workspace_id'] = new_workspace_id
        for row in self.checkpoints + self.snapshots:
            if row.get('job_id') == job_id and row.get('workspace_id') == old:
                row['workspace_id'] = new_workspace_id
        for row in self.usage_events.values():
            if row.get('job_id') == job_id and row.get('workspace_id') == old:
                row['workspace_id'] = new_workspace_id
        return _copy(updated)

    def upsert_folder(self, doc: dict) -> dict:
        key = (doc['job_id'], doc['folder_id'])
        current = self.folders.get(key)
        stored = _copy(doc)
        if current is None:
            if any(item.get('folder_id') == doc['folder_id'] and item.get('job_id') == doc['job_id'] for item in self.folders.values()):
                raise DuplicateRecord(doc['folder_id'])
            stored['version'] = 1
            self.folders[key] = stored
            return _copy(stored)
        stored['version'] = int(current.get('version') or 1) + 1
        self.folders[key] = stored
        return _copy(stored)

    def list_folders(self, job_id: str, workspace_id: str) -> list[dict]:
        rows = [
            row for row in self.folders.values()
            if row.get('job_id') == job_id and row.get('workspace_id') == workspace_id
        ]
        rows.sort(key=lambda row: (row.get('discovery_order') is None, row.get('discovery_order') or 0, row.get('folder_id') or ''))
        return [_copy(row) for row in rows]

    def upsert_batch(self, doc: dict) -> dict:
        key = (doc['job_id'], doc['batch_id'])
        current = self.batches.get(key)
        stored = _copy(doc)
        if current is None:
            stored['version'] = 1
            self.batches[key] = stored
            return _copy(stored)
        stored['version'] = int(current.get('version') or 1) + 1
        stored['lease_owner'] = current.get('lease_owner')
        stored['lease_until'] = current.get('lease_until')
        if 'lease_owner' in doc:
            stored['lease_owner'] = doc.get('lease_owner')
            stored['lease_until'] = doc.get('lease_until')
        self.batches[key] = stored
        return _copy(stored)

    def get_batch(self, job_id: str, batch_id: str) -> dict | None:
        doc = self.batches.get((job_id, batch_id))
        return _copy(doc) if doc else None

    def update_batch(self, job_id: str, batch_id: str, expected_version: int, fields: dict) -> dict | None:
        current = self.batches.get((job_id, batch_id))
        if current is None or int(current.get('version') or 0) != int(expected_version):
            return None
        updated = _copy(current)
        updated.update(_copy(fields))
        updated['job_id'] = job_id
        updated['batch_id'] = batch_id
        updated['version'] = int(expected_version) + 1
        self.batches[(job_id, batch_id)] = updated
        return _copy(updated)

    def list_batches(self, job_id: str, workspace_id: str) -> list[dict]:
        rows = [
            row for row in self.batches.values()
            if row.get('job_id') == job_id and row.get('workspace_id') == workspace_id
        ]
        rows.sort(key=lambda row: (row.get('batch_number') is None, row.get('batch_number') or 0, row.get('batch_id') or ''))
        return [_copy(row) for row in rows]

    def insert_event(self, doc: dict) -> dict:
        existing = self.events.get(doc['event_id'])
        if existing:
            return _copy(existing)
        stored = _copy(doc)
        self.events[stored['event_id']] = stored
        return _copy(stored)

    def list_events(self, job_id: str, workspace_id: str) -> list[dict]:
        rows = [
            row for row in self.events.values()
            if row.get('job_id') == job_id and row.get('workspace_id') == workspace_id
        ]
        rows.sort(key=lambda row: row.get('timestamp') or '')
        return [_copy(row) for row in rows]

    def insert_checkpoint(self, doc: dict) -> dict:
        for row in self.checkpoints:
            if row.get('job_id') == doc['job_id'] and row.get('checkpoint_version') == doc['checkpoint_version']:
                raise DuplicateRecord(doc['checkpoint_id'])
        stored = _copy(doc)
        self.checkpoints.append(stored)
        return _copy(stored)

    def list_checkpoints(self, job_id: str, workspace_id: str) -> list[dict]:
        rows = [
            row for row in self.checkpoints
            if row.get('job_id') == job_id and row.get('workspace_id') == workspace_id
        ]
        rows.sort(key=lambda row: row.get('checkpoint_version') or 0)
        return [_copy(row) for row in rows]

    def insert_snapshot(self, doc: dict) -> dict:
        stored = _copy(doc)
        self.snapshots.append(stored)
        return _copy(stored)

    def list_snapshots(self, job_id: str, workspace_id: str) -> list[dict]:
        rows = [
            row for row in self.snapshots
            if row.get('job_id') == job_id and row.get('workspace_id') == workspace_id
        ]
        rows.sort(key=lambda row: row.get('captured_at') or '')
        return [_copy(row) for row in rows]

    def get_usage_event(self, response_id: str) -> dict | None:
        doc = self.usage_events.get(response_id)
        return _copy(doc) if doc else None

    def insert_usage_event(self, doc: dict) -> tuple[dict, str]:
        from app.control_plane.errors import UsageConflict
        from app.control_plane.usage_events import _MERGE_FIELDS

        existing = self.usage_events.get(doc['response_id'])
        if existing:
            if all(existing.get(key) == doc.get(key) for key in _MERGE_FIELDS):
                return _copy(existing), 'existing'
            raise UsageConflict(doc['response_id'])
        stored = _copy(doc)
        self.usage_events[stored['response_id']] = stored
        return _copy(stored), 'created'

    def list_usage_events(self, **filters) -> list[dict]:
        from app.control_plane.usage_events import event_matches

        rows = [row for row in self.usage_events.values() if event_matches(row, **filters)]
        rows.sort(key=lambda row: row.get('response_id') or '')
        return [_copy(row) for row in rows]

    def job_count_in_workspace(self, workspace_id: str) -> int:
        return sum(1 for row in self.jobs.values() if row.get('workspace_id') == workspace_id)

    @staticmethod
    def _retarget(table: dict, job_id: str, old: str, new: str) -> None:
        for key, row in list(table.items()):
            if row.get('job_id') == job_id and row.get('workspace_id') == old:
                row['workspace_id'] = new
                table[key] = row


def _copy(doc):
    return copy.deepcopy(doc)
