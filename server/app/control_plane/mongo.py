"""PyMongo repository.

The client is opened only by runtime.connect. This module does not read
MONGODB_URI and does not log command arguments.
"""
from __future__ import annotations

from app.control_plane.errors import DuplicateRecord
from app.control_plane.indexes import INDEX_SPECS


class MongoStore:
    def __init__(self, database):
        self.db = database

    def ensure_indexes(self) -> list[dict]:
        applied = []
        for spec in INDEX_SPECS:
            self.db[spec['collection']].create_index(list(spec['keys']), unique=bool(spec['unique']), name=spec['name'])
            applied.append(dict(spec))
        return applied

    def upsert_workspace(self, doc: dict) -> dict:
        current = self.get_workspace(doc['workspace_id'])
        stored = dict(doc)
        if current is None:
            stored['version'] = int(stored.get('version') or 1)
            self._insert('workspaces', stored)
        else:
            stored['version'] = int(current.get('version') or 1) + 1
            stored['created_at'] = current.get('created_at') or stored.get('created_at')
            self.db.workspaces.replace_one({'workspace_id': stored['workspace_id']}, stored, upsert=True)
        return self.get_workspace(stored['workspace_id'])

    def list_workspaces(self) -> list[dict]:
        return [_plain(row) for row in self.db.workspaces.find({}, {'_id': 0})]

    def get_workspace(self, workspace_id: str) -> dict | None:
        return _plain(self.db.workspaces.find_one({'workspace_id': workspace_id}, {'_id': 0}))

    def get_job(self, job_id: str) -> dict | None:
        return _plain(self.db.jobs.find_one({'job_id': job_id}, {'_id': 0}))

    def insert_job(self, doc: dict) -> dict:
        stored = dict(doc)
        stored['version'] = int(stored.get('version') or 1)
        self._insert('jobs', stored)
        return self.get_job(stored['job_id'])

    def update_job(self, job_id: str, workspace_id: str, expected_version: int, fields: dict) -> dict | None:
        result = self.db.jobs.update_one(
            {'job_id': job_id, 'workspace_id': workspace_id, 'version': int(expected_version)},
            {'$set': {**fields, 'version': int(expected_version) + 1}},
        )
        if getattr(result, 'matched_count', 0) != 1:
            return None
        return self.get_job(job_id)

    def list_jobs(self, workspace_id: str | None = None) -> list[dict]:
        query = {'workspace_id': workspace_id} if workspace_id else {}
        return [_plain(row) for row in self.db.jobs.find(query, {'_id': 0}).limit(500)]

    def move_job(self, job_id: str, new_workspace_id: str) -> dict:
        current = self.get_job(job_id)
        if current is None:
            raise KeyError(job_id)
        old = current['workspace_id']
        self.db.jobs.update_one({'job_id': job_id}, {'$set': {'workspace_id': new_workspace_id}, '$inc': {'version': 1}})
        for name in ('folders', 'batches', 'job_events', 'checkpoints', 'register_snapshots'):
            self.db[name].update_many({'job_id': job_id, 'workspace_id': old}, {'$set': {'workspace_id': new_workspace_id}})
        return self.get_job(job_id)

    def upsert_folder(self, doc: dict) -> dict:
        current = self.db.folders.find_one({'job_id': doc['job_id'], 'folder_id': doc['folder_id']}, {'_id': 0})
        stored = dict(doc)
        if current is None:
            stored['version'] = 1
            self._insert('folders', stored)
        else:
            stored['version'] = int(current.get('version') or 1) + 1
            self.db.folders.replace_one({'job_id': doc['job_id'], 'folder_id': doc['folder_id']}, stored)
        return _plain(self.db.folders.find_one({'job_id': doc['job_id'], 'folder_id': doc['folder_id']}, {'_id': 0}))

    def list_folders(self, job_id: str, workspace_id: str) -> list[dict]:
        rows = [
            _plain(row) for row in self.db.folders.find(
                {'job_id': job_id, 'workspace_id': workspace_id}, {'_id': 0},
            )
        ]
        rows.sort(key=lambda row: (row.get('discovery_order') is None, row.get('discovery_order') or 0))
        return rows

    def upsert_batch(self, doc: dict) -> dict:
        current = self.get_batch(doc['job_id'], doc['batch_id'])
        stored = dict(doc)
        if current is None:
            stored['version'] = 1
            self._insert('batches', stored)
        else:
            stored['version'] = int(current.get('version') or 1) + 1
            if 'lease_owner' not in doc:
                stored['lease_owner'] = current.get('lease_owner')
                stored['lease_until'] = current.get('lease_until')
            self.db.batches.replace_one({'job_id': doc['job_id'], 'batch_id': doc['batch_id']}, stored)
        return self.get_batch(doc['job_id'], doc['batch_id'])

    def get_batch(self, job_id: str, batch_id: str) -> dict | None:
        return _plain(self.db.batches.find_one({'job_id': job_id, 'batch_id': batch_id}, {'_id': 0}))

    def update_batch(self, job_id: str, batch_id: str, expected_version: int, fields: dict) -> dict | None:
        result = self.db.batches.update_one(
            {'job_id': job_id, 'batch_id': batch_id, 'version': int(expected_version)},
            {'$set': {**fields, 'version': int(expected_version) + 1}},
        )
        if getattr(result, 'matched_count', 0) != 1:
            return None
        return self.get_batch(job_id, batch_id)

    def list_batches(self, job_id: str, workspace_id: str) -> list[dict]:
        rows = [
            _plain(row) for row in self.db.batches.find(
                {'job_id': job_id, 'workspace_id': workspace_id}, {'_id': 0},
            )
        ]
        rows.sort(key=lambda row: (row.get('batch_number') is None, row.get('batch_number') or 0, row.get('batch_id') or ''))
        return rows

    def insert_event(self, doc: dict) -> dict:
        existing = _plain(self.db.job_events.find_one({'event_id': doc['event_id']}, {'_id': 0}))
        if existing:
            return existing
        self._insert('job_events', dict(doc))
        return _plain(self.db.job_events.find_one({'event_id': doc['event_id']}, {'_id': 0}))

    def list_events(self, job_id: str, workspace_id: str) -> list[dict]:
        rows = [
            _plain(row) for row in self.db.job_events.find(
                {'job_id': job_id, 'workspace_id': workspace_id}, {'_id': 0},
            )
        ]
        rows.sort(key=lambda row: row.get('timestamp') or '')
        return rows

    def insert_checkpoint(self, doc: dict) -> dict:
        self._insert('checkpoints', dict(doc))
        return dict(doc)

    def list_checkpoints(self, job_id: str, workspace_id: str) -> list[dict]:
        rows = [
            _plain(row) for row in self.db.checkpoints.find(
                {'job_id': job_id, 'workspace_id': workspace_id}, {'_id': 0},
            )
        ]
        rows.sort(key=lambda row: row.get('checkpoint_version') or 0)
        return rows

    def insert_snapshot(self, doc: dict) -> dict:
        self.db.register_snapshots.insert_one(dict(doc))
        stored = dict(doc)
        stored.pop('_id', None)
        return stored

    def list_snapshots(self, job_id: str, workspace_id: str) -> list[dict]:
        rows = [
            _plain(row) for row in self.db.register_snapshots.find(
                {'job_id': job_id, 'workspace_id': workspace_id}, {'_id': 0},
            )
        ]
        rows.sort(key=lambda row: row.get('captured_at') or '')
        return rows

    def job_count_in_workspace(self, workspace_id: str) -> int:
        return len(self.list_jobs(workspace_id))

    def _insert(self, collection: str, doc: dict) -> None:
        try:
            self.db[collection].insert_one(dict(doc))
        except Exception as exc:
            if _duplicate(exc):
                raise DuplicateRecord(doc.get('job_id') or doc.get('event_id') or doc.get('folder_id') or collection) from None
            raise


def _plain(doc):
    if not doc:
        return None
    copied = dict(doc)
    copied.pop('_id', None)
    return copied


def _duplicate(exc: Exception) -> bool:
    try:
        from pymongo.errors import DuplicateKeyError
    except ImportError:
        return False
    return isinstance(exc, DuplicateKeyError)
