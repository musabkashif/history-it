"""Admin accounts, membership isolation, and concurrent Admin sessions."""
from __future__ import annotations

import unittest
from unittest import mock

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

import app.main as main
from app import auth
from app.accounts.access import AuthPrincipal, stamp_actor
from app.accounts.service import AccountsService, open_register_conflict, stable_user_id
from app.accounts.store import MemoryAccountsStore, set_accounts_store
from app.control_plane.memory import MemoryStore
from app.control_plane.runtime import clear_store, set_store
from app.control_plane.service import ControlService

ORIGIN = 'http://testserver'


class AdminAccountTests(unittest.TestCase):
    def setUp(self):
        auth.sessions.clear()
        auth.throttle.clear()
        self.store = MemoryAccountsStore()
        set_accounts_store(self.store)
        self.password = 'correct-horse-battery'
        self.username = 'demo'
        self.hash = PasswordHasher().hash(self.password)
        self._patchers = [
            mock.patch.object(main.settings, 'app_login_username', self.username),
            mock.patch.object(main.settings, 'app_login_password_hash', self.hash),
            mock.patch.object(main.settings, 'allowed_origins', ORIGIN),
            mock.patch.object(main.settings, 'api_token', ''),
            mock.patch.object(main.settings, 'cookie_secure', 'false'),
            mock.patch.object(main.settings, 'session_ttl_seconds', 3600),
            mock.patch.object(main.settings, 'login_max_failures', 5),
            mock.patch.object(main.settings, 'login_window_seconds', 300),
            mock.patch.object(main.settings, 'login_lockout_seconds', 300),
            mock.patch.object(main.settings, 'database_control_plane_enabled', True),
            mock.patch.object(main.settings, 'database_backfill_enabled', True),
            mock.patch.object(main.settings, 'control_plane_environment', 'staging'),
        ]
        for patcher in self._patchers:
            patcher.start()
        self.cp = MemoryStore()
        set_store(self.cp)
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        for patcher in self._patchers:
            patcher.stop()
        auth.sessions.clear()
        auth.throttle.clear()
        clear_store()
        set_accounts_store(None)

    def _login(self, username=None, password=None, client=None):
        http = client or self.client
        return http.post(
            '/api/auth/login',
            json={'username': username or self.username, 'password': password or self.password},
            headers={'Origin': ORIGIN},
        )

    def _csrf(self, client=None):
        http = client or self.client
        return http.cookies.get(auth.CSRF_COOKIE)

    def test_bootstrap_is_idempotent_and_stable(self):
        accounts = AccountsService(self.store)
        first = accounts.bootstrap_from_settings(main.settings)
        second = accounts.bootstrap_from_settings(main.settings)
        self.assertEqual(first['user_id'], second['user_id'])
        self.assertEqual(first['user_id'], stable_user_id(self.username))
        self.assertEqual(first['role'], 'admin')
        self.assertTrue(first['active'])

    def test_login_session_includes_user_id_and_role(self):
        response = self._login()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['authenticated'])
        self.assertEqual(body['username'], self.username)
        self.assertTrue(str(body['user_id']).startswith('usr-'))
        self.assertEqual(body['role'], 'admin')
        session = self.client.get('/api/auth/session')
        self.assertEqual(session.json()['user_id'], body['user_id'])

    def test_change_password_and_relogin(self):
        self._login()
        changed = self.client.post(
            '/api/auth/change-password',
            json={'current_password': self.password, 'new_password': 'new-correct-horse'},
            headers={'Origin': ORIGIN, 'X-CSRF-Token': self._csrf()},
        )
        self.assertEqual(changed.status_code, 200)
        auth.sessions.clear()
        denied = self._login(password=self.password)
        self.assertEqual(denied.status_code, 401)
        ok = self._login(password='new-correct-horse')
        self.assertEqual(ok.status_code, 200)

    def test_admin_reset_and_disable(self):
        self._login()
        created = self.client.post(
            '/api/auth/admins',
            json={'username': 'staff.two', 'password': 'staff-two-password', 'display_name': 'Staff Two'},
            headers={'Origin': ORIGIN, 'X-CSRF-Token': self._csrf()},
        )
        self.assertEqual(created.status_code, 200)
        target = created.json()['user_id']
        reset = self.client.post(
            f'/api/auth/admins/{target}/reset-password',
            json={'new_password': 'reset-staff-password'},
            headers={'Origin': ORIGIN, 'X-CSRF-Token': self._csrf()},
        )
        self.assertEqual(reset.status_code, 200)
        disabled = self.client.post(
            f'/api/auth/admins/{target}/active',
            json={'active': False},
            headers={'Origin': ORIGIN, 'X-CSRF-Token': self._csrf()},
        )
        self.assertEqual(disabled.status_code, 200)
        other = TestClient(main.app)
        blocked = self._login('staff.two', 'reset-staff-password', client=other)
        self.assertEqual(blocked.status_code, 403)
        other.close()

    def test_stamp_actor_uses_user_id(self):
        principal = AuthPrincipal(user_id='usr-abc', username='demo', role='admin', display_name='Demo')
        self.assertEqual(stamp_actor(principal), 'usr-abc')
        historical = AuthPrincipal(user_id=None, username='shared-admin', role='admin')
        self.assertEqual(stamp_actor(historical), 'shared-admin')


class MembershipConcurrencyTests(unittest.TestCase):
    def setUp(self):
        auth.sessions.clear()
        auth.throttle.clear()
        self.accounts_store = MemoryAccountsStore()
        set_accounts_store(self.accounts_store)
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
            mock.patch.object(main.settings, 'database_backfill_enabled', True),
            mock.patch.object(main.settings, 'control_plane_environment', 'staging'),
        ]
        for patcher in self._patchers:
            patcher.start()
        self.client_a = TestClient(main.app)
        self.client_b = TestClient(main.app)

    def tearDown(self):
        self.client_a.close()
        self.client_b.close()
        for patcher in self._patchers:
            patcher.stop()
        auth.sessions.clear()
        auth.throttle.clear()
        clear_store()
        set_accounts_store(None)

    def _login(self, client, username, password=None):
        return client.post(
            '/api/auth/login',
            json={'username': username, 'password': password or self.password},
            headers={'Origin': ORIGIN},
        )

    def _headers(self, client):
        return {'Origin': ORIGIN, 'X-CSRF-Token': client.cookies.get(auth.CSRF_COOKIE)}

    def test_two_admins_isolated_workspaces_and_events(self):
        login_a = self._login(self.client_a, 'admin.a')
        self.assertEqual(login_a.status_code, 200)
        user_a = login_a.json()['user_id']

        created_b = self.client_a.post(
            '/api/auth/admins',
            json={'username': 'admin.b', 'password': self.password, 'display_name': 'Admin B'},
            headers=self._headers(self.client_a),
        )
        self.assertEqual(created_b.status_code, 200)
        user_b = created_b.json()['user_id']
        self.assertNotEqual(user_a, user_b)

        login_b = self._login(self.client_b, 'admin.b')
        self.assertEqual(login_b.status_code, 200)

        project_a = self.client_a.post(
            '/api/control/workspaces',
            json={'project_name': 'Project Alpha'},
            headers=self._headers(self.client_a),
        )
        project_b = self.client_b.post(
            '/api/control/workspaces',
            json={'project_name': 'Project Beta'},
            headers=self._headers(self.client_b),
        )
        self.assertEqual(project_a.status_code, 200)
        self.assertEqual(project_b.status_code, 200)
        ws_a = project_a.json()['workspace']['workspace_id']
        ws_b = project_b.json()['workspace']['workspace_id']

        listed_a = self.client_a.get('/api/control/workspaces')
        listed_b = self.client_b.get('/api/control/workspaces')
        ids_a = {row['workspace_id'] for row in listed_a.json()['workspaces']}
        ids_b = {row['workspace_id'] for row in listed_b.json()['workspaces']}
        self.assertIn(ws_a, ids_a)
        self.assertNotIn(ws_b, ids_a)
        self.assertIn(ws_b, ids_b)
        self.assertNotIn(ws_a, ids_b)

        service = ControlService(self.cp, 'staging')
        detail_a = service.create_job(
            job_id='HIT-aaaaaaaaaaaaaaaa',
            workspace_id=ws_a,
            project_name='Project Alpha',
            register_id='sheet-a',
            source='live',
            actor=user_a,
        )
        detail_b = service.create_job(
            job_id='HIT-bbbbbbbbbbbbbbbb',
            workspace_id=ws_b,
            project_name='Project Beta',
            register_id='sheet-b',
            source='live',
            actor=user_b,
        )
        job_a = detail_a['job']
        job_b = detail_b['job']
        self.assertEqual(job_a['created_by'], user_a)
        self.assertEqual(job_b['created_by'], user_b)
        status_b_before = job_b['job_status']

        pause_a = self.client_a.post(
            f"/api/control/jobs/{job_a['job_id']}/pause",
            json={'workspace_id': ws_a},
            headers=self._headers(self.client_a),
        )
        self.assertEqual(pause_a.status_code, 200)
        after_b = self.cp.get_job(job_b['job_id'])
        self.assertEqual(after_b['job_status'], status_b_before)

        denied = self.client_b.post(
            f"/api/control/jobs/{job_a['job_id']}/pause",
            json={'workspace_id': ws_a},
            headers=self._headers(self.client_b),
        )
        self.assertEqual(denied.status_code, 403)
        self.assertIn('not a member', denied.json()['detail'].lower())

        events_a = [row for row in self.cp.list_events(job_a['job_id'], ws_a) if row.get('actor')]
        events_b = [row for row in self.cp.list_events(job_b['job_id'], ws_b) if row.get('actor')]
        actors = {row.get('actor') for row in events_a + events_b}
        self.assertIn(user_a, actors)
        self.assertIn(user_b, actors)

        # Historical shared-username attribution remains readable.
        legacy = service.create_job(
            job_id='HIT-legacylegacylege',
            workspace_id=ws_a,
            project_name='Project Alpha',
            register_id='sheet-legacy',
            source='historical',
            actor='shared-demo',
        )
        self.assertEqual(legacy['job']['created_by'], 'shared-demo')
        detail = self.client_a.get(f"/api/control/jobs/{legacy['job']['job_id']}?workspace_id={ws_a}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()['job']['created_by'], 'shared-demo')

    def test_register_conflict_across_admins(self):
        memberships = {
            'ws-a': ['usr-a'],
            'ws-b': ['usr-b'],
        }
        jobs = [
            {
                'job_id': 'HIT-openopenopenopen',
                'workspace_id': 'ws-a',
                'register_id': 'sheet-shared',
                'job_status': 'running',
                'created_by': 'usr-a',
            }
        ]
        conflict = open_register_conflict(
            register_id='sheet-shared',
            jobs=jobs,
            actor_user_id='usr-b',
            memberships_by_workspace=memberships,
        )
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict['job_id'], 'HIT-openopenopenopen')
        none = open_register_conflict(
            register_id='sheet-shared',
            jobs=jobs,
            actor_user_id='usr-a',
            memberships_by_workspace=memberships,
        )
        self.assertIsNone(none)
        orphan = open_register_conflict(
            register_id='sheet-shared',
            jobs=[{
                'job_id': 'HIT-orphanorphanorph',
                'workspace_id': 'ws-orphan',
                'register_id': 'sheet-shared',
                'job_status': 'paused',
                'created_by': 'shared-demo',
            }],
            actor_user_id='usr-b',
            memberships_by_workspace={},
        )
        self.assertIsNotNone(orphan)

    def test_two_sessions_resolve_to_different_user_ids(self):
        login_a = self._login(self.client_a, 'admin.a')
        self.assertEqual(login_a.status_code, 200)
        created_b = self.client_a.post(
            '/api/auth/admins',
            json={'username': 'admin.b', 'password': self.password, 'display_name': 'Admin B'},
            headers=self._headers(self.client_a),
        )
        self.assertEqual(created_b.status_code, 200)
        login_b = self._login(self.client_b, 'admin.b')
        self.assertEqual(login_b.status_code, 200)
        session_a = self.client_a.get('/api/auth/session').json()
        session_b = self.client_b.get('/api/auth/session').json()
        self.assertNotEqual(session_a['user_id'], session_b['user_id'])
        self.assertEqual(session_a['username'], 'admin.a')
        self.assertEqual(session_b['username'], 'admin.b')

    def test_membership_persists_after_accounts_store_reload(self):
        login_a = self._login(self.client_a, 'admin.a')
        user_a = login_a.json()['user_id']
        created = self.client_a.post(
            '/api/control/workspaces',
            json={'project_name': 'Persist Project'},
            headers=self._headers(self.client_a),
        )
        self.assertEqual(created.status_code, 200)
        ws = created.json()['workspace']['workspace_id']
        self.assertIsNotNone(self.accounts_store.get_membership(user_a, ws))

        # Snapshot and reload into a fresh MemoryAccountsStore (simulates reconnect).
        users = self.accounts_store.list_users()
        members = self.accounts_store.list_memberships()
        fresh = MemoryAccountsStore()
        for row in users:
            fresh.upsert_user(row)
        for row in members:
            fresh.upsert_membership(row)
        set_accounts_store(fresh)
        self.accounts_store = fresh
        self.assertIsNotNone(fresh.get_membership(user_a, ws))
        listed = self.client_a.get('/api/control/workspaces')
        self.assertEqual(listed.status_code, 200)
        self.assertIn(ws, {row['workspace_id'] for row in listed.json()['workspaces']})

        # Non-member Admin B still cannot see or mutate.
        self.client_a.post(
            '/api/auth/admins',
            json={'username': 'admin.b', 'password': self.password},
            headers=self._headers(self.client_a),
        )
        self._login(self.client_b, 'admin.b')
        denied_list = self.client_b.get('/api/control/workspaces')
        ids_b = {row['workspace_id'] for row in denied_list.json()['workspaces']}
        self.assertNotIn(ws, ids_b)

    def test_require_admin_helper(self):
        from app.accounts.access import require_admin
        from fastapi import HTTPException
        ok = AuthPrincipal(user_id='usr-1', username='a', role='admin', active=True)
        self.assertEqual(require_admin(ok).user_id, 'usr-1')
        with self.assertRaises(HTTPException):
            require_admin(AuthPrincipal(user_id='usr-1', username='a', role='admin', active=False))


if __name__ == '__main__':
    unittest.main()
