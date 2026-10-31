"""MongoDB accounts store on the control-plane database."""
from __future__ import annotations

from app.control_plane.errors import DuplicateRecord

ACCOUNT_INDEX_SPECS = (
    {'collection': 'users', 'keys': [('user_id', 1)], 'unique': True, 'name': 'uniq_user_id'},
    {'collection': 'users', 'keys': [('username_key', 1)], 'unique': True, 'name': 'uniq_username_key'},
    {
        'collection': 'workspace_members',
        'keys': [('user_id', 1), ('workspace_id', 1)],
        'unique': True,
        'name': 'uniq_user_workspace',
    },
    {'collection': 'workspace_members', 'keys': [('workspace_id', 1)], 'unique': False, 'name': 'member_workspace'},
)


def _plain(doc):
    if not doc:
        return None
    copied = dict(doc)
    copied.pop('_id', None)
    return copied


def _username_key(username: str) -> str:
    return (username or '').strip().casefold()


class MongoAccountsStore:
    """Durable Admin users and workspace memberships."""

    def __init__(self, database):
        self.db = database

    def ensure_indexes(self) -> list[dict]:
        applied = []
        for spec in ACCOUNT_INDEX_SPECS:
            self.db[spec['collection']].create_index(
                list(spec['keys']),
                unique=bool(spec['unique']),
                name=spec['name'],
            )
            applied.append(dict(spec))
        return applied

    def clear(self) -> None:
        self.db.users.delete_many({})
        self.db.workspace_members.delete_many({})

    def upsert_user(self, doc: dict) -> dict:
        stored = dict(doc)
        stored['username_key'] = _username_key(stored.get('username') or '')
        existing = self.get_user(stored['user_id'])
        by_name = self.get_user_by_username(stored.get('username') or '')
        if by_name and by_name.get('user_id') != stored['user_id']:
            raise ValueError(f'Username already registered: {stored["username"]}')
        if existing is None:
            try:
                self.db.users.insert_one(dict(stored))
            except Exception as exc:
                if _duplicate(exc):
                    raise DuplicateRecord(stored['user_id']) from None
                raise
        else:
            self.db.users.replace_one({'user_id': stored['user_id']}, stored, upsert=True)
        return self.get_user(stored['user_id'])

    def get_user(self, user_id: str) -> dict | None:
        return _plain(self.db.users.find_one({'user_id': user_id}, {'_id': 0}))

    def get_user_by_username(self, username: str) -> dict | None:
        return _plain(self.db.users.find_one({'username_key': _username_key(username)}, {'_id': 0}))

    def list_users(self) -> list[dict]:
        return [_plain(row) for row in self.db.users.find({}, {'_id': 0})]

    def upsert_membership(self, doc: dict) -> dict:
        stored = dict(doc)
        query = {'user_id': stored['user_id'], 'workspace_id': stored['workspace_id']}
        existing = self.get_membership(stored['user_id'], stored['workspace_id'])
        if existing is None:
            try:
                self.db.workspace_members.insert_one(dict(stored))
            except Exception as exc:
                if _duplicate(exc):
                    return self.get_membership(stored['user_id'], stored['workspace_id'])
                raise
        else:
            self.db.workspace_members.replace_one(query, stored, upsert=True)
        return self.get_membership(stored['user_id'], stored['workspace_id'])

    def get_membership(self, user_id: str, workspace_id: str) -> dict | None:
        return _plain(self.db.workspace_members.find_one(
            {'user_id': user_id, 'workspace_id': workspace_id},
            {'_id': 0},
        ))

    def list_memberships(self, *, user_id: str | None = None, workspace_id: str | None = None) -> list[dict]:
        query = {}
        if user_id:
            query['user_id'] = user_id
        if workspace_id:
            query['workspace_id'] = workspace_id
        return [_plain(row) for row in self.db.workspace_members.find(query, {'_id': 0})]

    def delete_membership(self, user_id: str, workspace_id: str) -> bool:
        result = self.db.workspace_members.delete_one({'user_id': user_id, 'workspace_id': workspace_id})
        return bool(getattr(result, 'deleted_count', 0))


def _duplicate(exc: Exception) -> bool:
    try:
        from pymongo.errors import DuplicateKeyError
    except ImportError:
        return False
    return isinstance(exc, DuplicateKeyError)
