"""Admin account page APIs: directory, password, last-active, memberships."""
from __future__ import annotations

import unittest
from unittest import mock

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

import app.main as main
from app import auth
from app.accounts.store import MemoryAccountsStore, set_accounts_store
from app.control_plane.memory import MemoryStore
from app.control_plane.runtime import clear_store, set_store

ORIGIN = 'http://testserver'


class AccountManagementTests(unittest.TestCase):
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
            mock.patch.object(main.settings, 'app_login_username', 'historyit'),
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
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        for patcher in self._patchers:
            patcher.stop()
        auth.sessions.clear()
        auth.throttle.clear()
        clear_store()
        set_accounts_store(None)

    def _login(self, username='historyit', password=None, client=None):
        http = client or self.client
        return http.post(
            '/api/auth/login',
            json={'username': username, 'password': password or self.password},
            headers={'Origin': ORIGIN},
        )

    def _headers(self, client=None):
        http = client or self.client
        return {'Origin': ORIGIN, 'X-CSRF-Token': http.cookies.get(auth.CSRF_COOKIE)}

    def _assert_no_secrets(self, payload):
        text = str(payload)
        self.assertNotIn('password_hash', text)
        self.assertNotIn('$argon2', text)
        self.assertNotIn(self.password, text)

    def test_current_account_display(self):
        login = self._login()
        self.assertEqual(login.status_code, 200)
        account = self.client.get('/api/auth/account')
        self.assertEqual(account.status_code, 200)
        body = account.json()
        self.assertEqual(body['username'], 'historyit')
        self.assertEqual(body['role'], 'admin')
        self.assertTrue(body['active'])
        self.assertTrue(body['user_id'].startswith('usr-'))
        self.assertIn('created_at', body)
        self.assertEqual(body['session']['username'], 'historyit')
        self._assert_no_secrets(body)

    def test_change_password(self):
        self._login()
        changed = self.client.post(
            '/api/auth/change-password',
            json={'current_password': self.password, 'new_password': 'new-correct-horse'},
            headers=self._headers(),
        )
        self.assertEqual(changed.status_code, 200)
        self._assert_no_secrets(changed.json())
        auth.sessions.clear()
        self.assertEqual(self._login(password=self.password).status_code, 401)
        self.assertEqual(self._login(password='new-correct-horse').status_code, 200)

    def test_create_admin_and_list(self):
        self._login()
        created = self.client.post(
            '/api/auth/admins',
            json={'username': 'staff.two', 'password': 'staff-two-password', 'display_name': 'Staff Two'},
            headers=self._headers(),
        )
        self.assertEqual(created.status_code, 200)
        body = created.json()
        self.assertEqual(body['username'], 'staff.two')
        self.assertEqual(body['display_name'], 'Staff Two')
        self.assertTrue(body['active'])
        self._assert_no_secrets(body)
        listed = self.client.get('/api/auth/admins')
        self.assertEqual(listed.status_code, 200)
        usernames = {row['username'] for row in listed.json()['admins']}
        self.assertEqual(usernames, {'historyit', 'staff.two'})
        self._assert_no_secrets(listed.json())

    def test_disable_reenable_and_last_active_protection(self):
        self._login()
        created = self.client.post(
            '/api/auth/admins',
            json={'username': 'staff.two', 'password': 'staff-two-password'},
            headers=self._headers(),
        ).json()
        target = created['user_id']
        me = self.client.get('/api/auth/account').json()['user_id']

        # With two actives, self-disable is blocked as own-account (not last-active yet).
        self_disable = self.client.post(
            f'/api/auth/admins/{me}/active',
            json={'active': False},
            headers=self._headers(),
        )
        self.assertEqual(self_disable.status_code, 409)
        self.assertIn('your own', self_disable.json()['detail'].lower())

        disabled = self.client.post(
            f'/api/auth/admins/{target}/active',
            json={'active': False},
            headers=self._headers(),
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.json()['active'])

        # Only historyit remains active — last-active guard blocks disable.
        last = self.client.post(
            f'/api/auth/admins/{me}/active',
            json={'active': False},
            headers=self._headers(),
        )
        self.assertEqual(last.status_code, 409)
        self.assertIn('last active', last.json()['detail'].lower())

        enabled = self.client.post(
            f'/api/auth/admins/{target}/active',
            json={'active': True},
            headers=self._headers(),
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.json()['active'])

    def test_reset_password(self):
        self._login()
        created = self.client.post(
            '/api/auth/admins',
            json={'username': 'staff.two', 'password': 'staff-two-password'},
            headers=self._headers(),
        ).json()
        reset = self.client.post(
            f"/api/auth/admins/{created['user_id']}/reset-password",
            json={'new_password': 'reset-staff-password'},
            headers=self._headers(),
        )
        self.assertEqual(reset.status_code, 200)
        self._assert_no_secrets(reset.json())
        other = TestClient(main.app)
        self.assertEqual(self._login('staff.two', 'staff-two-password', client=other).status_code, 401)
        self.assertEqual(self._login('staff.two', 'reset-staff-password', client=other).status_code, 200)
        other.close()

    def test_grant_remove_membership_and_last_member_protection(self):
        self._login()
        created = self.client.post(
            '/api/auth/admins',
            json={'username': 'staff.two', 'password': 'staff-two-password'},
            headers=self._headers(),
        ).json()
        project = self.client.post(
            '/api/control/workspaces',
            json={'project_name': 'Membership Project'},
            headers=self._headers(),
        )
        self.assertEqual(project.status_code, 200)
        ws = project.json()['workspace']['workspace_id']

        granted = self.client.post(
            '/api/auth/memberships',
            json={'user_id': created['user_id'], 'workspace_id': ws},
            headers=self._headers(),
        )
        self.assertEqual(granted.status_code, 200)
        self.assertEqual(granted.json()['user_id'], created['user_id'])
        self._assert_no_secrets(granted.json())

        listed = self.client.get(f'/api/auth/memberships?workspace_id={ws}')
        self.assertEqual(listed.status_code, 200)
        ids = {row['user_id'] for row in listed.json()['memberships']}
        self.assertIn(created['user_id'], ids)
        self._assert_no_secrets(listed.json())

        # Non-member cannot list another workspace they do not belong to.
        other = TestClient(main.app)
        self._login('staff.two', 'staff-two-password', client=other)
        foreign = self.client.post(
            '/api/control/workspaces',
            json={'project_name': 'Private Project'},
            headers=self._headers(),
        )
        private_ws = foreign.json()['workspace']['workspace_id']
        denied = other.get(f'/api/auth/memberships?workspace_id={private_ws}')
        self.assertEqual(denied.status_code, 403)

        # Remove staff from shared workspace; cannot remove last remaining Admin later.
        removed = self.client.delete(
            f"/api/auth/memberships/{created['user_id']}/{ws}",
            headers=self._headers(),
        )
        self.assertEqual(removed.status_code, 200)
        me = self.client.get('/api/auth/account').json()['user_id']
        last = self.client.delete(
            f'/api/auth/memberships/{me}/{ws}',
            headers=self._headers(),
        )
        self.assertEqual(last.status_code, 409)
        self.assertIn('last admin', last.json()['detail'].lower())
        other.close()


if __name__ == '__main__':
    unittest.main()
