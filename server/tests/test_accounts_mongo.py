"""Durable Mongo accounts store, restart survival, and register tenancy."""
from __future__ import annotations

import unittest
from unittest import mock

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

import app.main as main
from app import auth
from app.accounts.mongo_store import ACCOUNT_INDEX_SPECS, MongoAccountsStore
from app.accounts.service import AccountsService, open_register_conflict, stable_user_id
from app.accounts.store import MemoryAccountsStore, reset_accounts_runtime, set_accounts_store
from app.control_plane.memory import MemoryStore
from app.control_plane.runtime import clear_store, set_store


class _FakeResult:
    def __init__(self, deleted=0, matched=0):
        self.deleted_count = deleted
        self.matched_count = matched


class _FakeCollection:
    def __init__(self):
        self.docs = []
        self.indexes = []

    def create_index(self, keys, unique=False, name=None):
        self.indexes.append({'keys': list(keys), 'unique': unique, 'name': name})
        return name

    def find_one(self, query, projection=None):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return dict(doc)
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                yield dict(doc)

    def insert_one(self, doc):
        # Mirror unique indexes: users by user_id/username_key; members by pair.
        for existing in self.docs:
            if 'username_key' in doc:
                if existing.get('user_id') == doc.get('user_id'):
                    raise _Dup()
                if existing.get('username_key') == doc.get('username_key'):
                    raise _Dup()
            elif 'workspace_id' in doc:
                if (
                    existing.get('user_id') == doc.get('user_id')
                    and existing.get('workspace_id') == doc.get('workspace_id')
                ):
                    raise _Dup()
            elif existing.get('user_id') and existing.get('user_id') == doc.get('user_id'):
                raise _Dup()
        self.docs.append(dict(doc))
        return self

    def replace_one(self, query, doc, upsert=False):
        for index, existing in enumerate(self.docs):
            if all(existing.get(k) == v for k, v in query.items()):
                self.docs[index] = dict(doc)
                return _FakeResult(matched=1)
        if upsert:
            self.docs.append(dict(doc))
        return _FakeResult(matched=0)

    def delete_many(self, query):
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not all(doc.get(k) == v for k, v in query.items())] if query else []
        if query == {}:
            self.docs = []
        return _FakeResult(deleted=before - len(self.docs))

    def delete_one(self, query):
        for index, existing in enumerate(self.docs):
            if all(existing.get(k) == v for k, v in query.items()):
                del self.docs[index]
                return _FakeResult(deleted=1)
        return _FakeResult(deleted=0)


class _Dup(Exception):
    pass


class _FakeDB:
    def __init__(self):
        self._cols = {}

    def __getitem__(self, name):
        return self._cols.setdefault(name, _FakeCollection())

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return self[name]


# Make DuplicateKeyError detection work for fake inserts.
import app.accounts.mongo_store as mongo_mod


def _fake_duplicate(exc):
    return isinstance(exc, _Dup)


mongo_mod._duplicate = _fake_duplicate

ORIGIN = 'http://testserver'


class MongoAccountsStoreTests(unittest.TestCase):
    def test_indexes_and_restart_persistence(self):
        db = _FakeDB()
        store = MongoAccountsStore(db)
        applied = store.ensure_indexes()
        names = {item['name'] for item in applied}
        self.assertIn('uniq_user_id', names)
        self.assertIn('uniq_username_key', names)
        self.assertIn('uniq_user_workspace', names)
        self.assertEqual(len(ACCOUNT_INDEX_SPECS), 4)

        accounts = AccountsService(store)
        env_hash = PasswordHasher().hash('correct-horse-battery')
        with mock.patch.object(main.settings, 'app_login_username', 'persist.admin'):
            with mock.patch.object(main.settings, 'app_login_password_hash', env_hash):
                first = accounts.bootstrap_from_settings(main.settings)
                self.assertTrue(first['user_id'].startswith('usr-'))
                store.upsert_membership({
                    'user_id': first['user_id'],
                    'workspace_id': 'ws-alpha',
                    'role': 'admin',
                    'created_at': first['created_at'],
                })

                # Simulate process restart: new store instance on same durable DB.
                revived = MongoAccountsStore(db)
                again = AccountsService(revived).bootstrap_from_settings(main.settings)
                self.assertEqual(again['user_id'], first['user_id'])
                self.assertEqual(again['username'], 'persist.admin')
                self.assertIsNotNone(revived.get_membership(first['user_id'], 'ws-alpha'))
                self.assertNotIn('password_hash', AccountsService(revived).list_admins()[0])

    def test_two_admins_and_memberships_survive_restart(self):
        db = _FakeDB()
        store = MongoAccountsStore(db)
        store.ensure_indexes()
        accounts = AccountsService(store)
        now = '2026-01-01T00:00:00Z'
        admin_a = {
            'user_id': stable_user_id('admin.a'),
            'username': 'admin.a',
            'display_name': 'Admin A',
            'password_hash': PasswordHasher().hash('correct-horse-battery'),
            'active': True,
            'role': 'admin',
            'created_at': now,
            'updated_at': now,
        }
        admin_b = {
            'user_id': stable_user_id('admin.b'),
            'username': 'admin.b',
            'display_name': 'Admin B',
            'password_hash': PasswordHasher().hash('correct-horse-battery'),
            'active': True,
            'role': 'admin',
            'created_at': now,
            'updated_at': now,
        }
        store.upsert_user(admin_a)
        store.upsert_user(admin_b)
        store.upsert_membership({
            'user_id': admin_a['user_id'], 'workspace_id': 'ws-a', 'role': 'admin', 'created_at': now,
        })
        store.upsert_membership({
            'user_id': admin_b['user_id'], 'workspace_id': 'ws-b', 'role': 'admin', 'created_at': now,
        })
        # Idempotent membership re-create
        again_member = accounts.ensure_membership(admin_a['user_id'], 'ws-a')
        self.assertEqual(again_member['workspace_id'], 'ws-a')
        self.assertEqual(len(store.list_memberships(user_id=admin_a['user_id'])), 1)

        revived = MongoAccountsStore(db)
        self.assertEqual(revived.get_user_by_username('admin.a')['user_id'], admin_a['user_id'])
        self.assertEqual(revived.get_user_by_username('admin.b')['user_id'], admin_b['user_id'])
        self.assertIsNotNone(revived.get_membership(admin_a['user_id'], 'ws-a'))
        self.assertIsNotNone(revived.get_membership(admin_b['user_id'], 'ws-b'))
        self.assertIsNone(revived.get_membership(admin_a['user_id'], 'ws-b'))
        self.assertFalse(AccountsService(revived).is_member(admin_b['user_id'], 'ws-a'))

    def test_memory_restart_snapshot_round_trip(self):
        store = MemoryAccountsStore()
        set_accounts_store(store)
        accounts = AccountsService(store)
        user = {
            'user_id': stable_user_id('admin.a'),
            'username': 'admin.a',
            'display_name': 'Admin A',
            'password_hash': PasswordHasher().hash('correct-horse-battery'),
            'active': True,
            'role': 'admin',
            'created_at': '2026-01-01T00:00:00Z',
            'updated_at': '2026-01-01T00:00:00Z',
        }
        store.upsert_user(user)
        store.upsert_membership({
            'user_id': user['user_id'],
            'workspace_id': 'ws-1',
            'role': 'admin',
            'created_at': user['created_at'],
        })
        snapshot_users = store.list_users()
        snapshot_members = store.list_memberships()

        reset_accounts_runtime()
        set_accounts_store(None)
        fresh = MemoryAccountsStore()
        set_accounts_store(fresh)
        for row in snapshot_users:
            fresh.upsert_user(row)
        for row in snapshot_members:
            fresh.upsert_membership(row)
        self.assertEqual(fresh.get_user_by_username('admin.a')['user_id'], user['user_id'])
        self.assertIsNotNone(fresh.get_membership(user['user_id'], 'ws-1'))
        set_accounts_store(None)

    def test_shared_register_rejected_for_non_member(self):
        conflict = open_register_conflict(
            register_id='sheet-1',
            jobs=[{
                'job_id': 'HIT-aaaaaaaaaaaaaaaa',
                'workspace_id': 'ws-a',
                'register_id': 'sheet-1',
                'job_status': 'running',
                'created_by': 'usr-a',
            }],
            actor_user_id='usr-b',
            memberships_by_workspace={'ws-a': ['usr-a'], 'ws-b': ['usr-b']},
        )
        self.assertIsNotNone(conflict)
        allowed = open_register_conflict(
            register_id='sheet-1',
            jobs=[{
                'job_id': 'HIT-aaaaaaaaaaaaaaaa',
                'workspace_id': 'ws-a',
                'register_id': 'sheet-1',
                'job_status': 'running',
                'created_by': 'usr-a',
            }],
            actor_user_id='usr-a',
            memberships_by_workspace={'ws-a': ['usr-a']},
        )
        self.assertIsNone(allowed)
        historical_open = open_register_conflict(
            register_id='sheet-1',
            jobs=[{
                'job_id': 'HIT-bbbbbbbbbbbbbbbb',
                'workspace_id': 'ws-orphan',
                'register_id': 'sheet-1',
                'job_status': 'paused',
                'created_by': 'shared-demo',
            }],
            actor_user_id='usr-b',
            memberships_by_workspace={},
        )
        self.assertIsNotNone(historical_open)
        completed_ok = open_register_conflict(
            register_id='sheet-1',
            jobs=[{
                'job_id': 'HIT-cccccccccccccccc',
                'workspace_id': 'ws-a',
                'register_id': 'sheet-1',
                'job_status': 'completed',
                'created_by': 'shared-demo',
            }],
            actor_user_id='usr-b',
            memberships_by_workspace={'ws-a': ['usr-a']},
        )
        self.assertIsNone(completed_ok)


class FailClosedLiveAccessTests(unittest.TestCase):
    def setUp(self):
        auth.sessions.clear()
        auth.throttle.clear()
        self.store = MemoryAccountsStore()
        set_accounts_store(self.store)
        self.cp = MemoryStore()
        set_store(self.cp)
        self.password = 'correct-horse-battery'
        self.hash = PasswordHasher().hash(self.password)
        self._patchers = [
            mock.patch.object(main.settings, 'app_login_username', 'admin.a'),
            mock.patch.object(main.settings, 'app_login_password_hash', self.hash),
            mock.patch.object(main.settings, 'allowed_origins', ORIGIN),
            mock.patch.object(main.settings, 'api_token', ''),
            mock.patch.object(main.settings, 'cookie_secure', 'false'),
            mock.patch.object(main.settings, 'session_ttl_seconds', 3600),
            mock.patch.object(main.settings, 'login_max_failures', 5),
            mock.patch.object(main.settings, 'login_window_seconds', 300),
            mock.patch.object(main.settings, 'login_lockout_seconds', 300),
            mock.patch.object(main.settings, 'database_control_plane_enabled', True),
            mock.patch.object(main.settings, 'control_plane_environment', 'staging'),
        ]
        for patcher in self._patchers:
            patcher.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        for patcher in self._patchers:
            patcher.stop()
        auth.sessions.clear()
        auth.throttle.clear()
        clear_store()
        set_accounts_store(None)

    def test_unknown_job_id_is_forbidden_for_admin_session(self):
        login = self.client.post(
            '/api/auth/login',
            json={'username': 'admin.a', 'password': self.password},
            headers={'Origin': ORIGIN},
        )
        self.assertEqual(login.status_code, 200)
        with mock.patch.object(main, 'live') as live:
            live.side_effect = Exception('no sheets')
            denied = self.client.get('/api/jobs/HIT-unknownunknown00')
        self.assertEqual(denied.status_code, 403)

    def test_service_token_still_bypasses_membership(self):
        with mock.patch.object(main.settings, 'api_token', 'server-only-token'):
            with mock.patch.object(main, 'live') as live:
                live.return_value.state.return_value = {
                    'job_id': 'HIT-1', 'project': 'P', 'spreadsheet_id': 's',
                    'source_folder_id': 'f', 'authorized_source_folder_ids': ['f'],
                    'batches': 0, 'status': 'Authorized / Pending Inspection',
                }
                ok = self.client.get('/api/jobs/HIT-1', headers={'X-API-Token': 'server-only-token'})
                self.assertEqual(ok.status_code, 200)


if __name__ == '__main__':
    unittest.main()
