import time
import unittest
from pathlib import Path
from unittest import mock

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

import app.main as main
from app import auth
from app.accounts.store import MemoryAccountsStore, set_accounts_store


ORIGIN = 'http://testserver'


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        auth.sessions.clear()
        auth.throttle.clear()
        set_accounts_store(MemoryAccountsStore())
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
            mock.patch.object(main.settings, 'login_max_failures', 3),
            mock.patch.object(main.settings, 'login_window_seconds', 300),
            mock.patch.object(main.settings, 'login_lockout_seconds', 300),
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
        set_accounts_store(None)

    def _login(self, username=None, password=None):
        return self.client.post(
            '/api/auth/login',
            json={'username': username or self.username, 'password': password or self.password},
            headers={'Origin': ORIGIN},
        )

    def _csrf(self):
        return self.client.cookies.get(auth.CSRF_COOKIE)

    def test_wrong_credentials(self):
        response = self._login(password='wrong-password')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['detail'], 'Invalid credentials')
        session = self.client.get('/api/auth/session')
        self.assertFalse(session.json().get('authenticated'))

    def test_login_session_and_protected_route(self):
        response = self._login()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['authenticated'])
        self.assertTrue(self.client.cookies.get(auth.SESSION_COOKIE))
        self.assertTrue(self._csrf())
        session = self.client.get('/api/auth/session')
        self.assertEqual(session.json()['authenticated'], True)
        with mock.patch.object(main, '_require_job_access', return_value=''):
            with mock.patch.object(main, 'live') as live:
                live.return_value.state.return_value = {
                    'job_id': 'HIT-1', 'project': 'P', 'spreadsheet_id': 's',
                    'source_folder_id': 'f', 'authorized_source_folder_ids': ['f'],
                    'batches': 0, 'status': 'Authorized / Pending Inspection',
                }
                denied = self.client.get('/api/jobs/HIT-1')
                # still authenticated via cookie from login
                self.assertEqual(denied.status_code, 200)

    def test_operational_api_requires_auth(self):
        response = self.client.get('/api/jobs/HIT-1')
        self.assertEqual(response.status_code, 401)

    def test_empty_api_token_never_opens_api(self):
        response = self.client.get('/api/jobs/HIT-1', headers={'X-API-Token': ''})
        self.assertEqual(response.status_code, 401)
        with mock.patch.object(main.settings, 'api_token', ''):
            response = self.client.get('/api/jobs/HIT-1', headers={'X-API-Token': 'anything'})
            self.assertEqual(response.status_code, 401)

    def test_service_token_bypasses_session_when_configured(self):
        with mock.patch.object(main.settings, 'api_token', 'server-only-token'):
            with mock.patch.object(main, 'live') as live:
                live.return_value.state.return_value = {
                    'job_id': 'HIT-1', 'project': 'P', 'spreadsheet_id': 's',
                    'source_folder_id': 'f', 'authorized_source_folder_ids': ['f'],
                    'batches': 0, 'status': 'Authorized / Pending Inspection',
                }
                ok = self.client.get('/api/jobs/HIT-1', headers={'X-API-Token': 'server-only-token'})
                self.assertEqual(ok.status_code, 200)
                bad = self.client.get('/api/jobs/HIT-1', headers={'X-API-Token': 'wrong'})
                self.assertEqual(bad.status_code, 401)

    def test_csrf_required_for_mutations(self):
        self.assertEqual(self._login().status_code, 200)
        with mock.patch.object(main, '_require_job_access', return_value=''):
            with mock.patch.object(main, 'live') as live:
                live.return_value.pause.return_value = {
                    'job_id': 'HIT-1', 'project': 'P', 'spreadsheet_id': 's',
                    'source_folder_id': 'f', 'authorized_source_folder_ids': ['f'],
                    'batches': 0, 'status': 'Paused',
                }
                missing = self.client.post('/api/jobs/HIT-1/pause', headers={'Origin': ORIGIN})
                self.assertEqual(missing.status_code, 403)
                ok = self.client.post(
                    '/api/jobs/HIT-1/pause',
                    headers={'Origin': ORIGIN, auth.CSRF_HEADER: self._csrf()},
                )
                self.assertEqual(ok.status_code, 200)

    def test_origin_required_for_mutations(self):
        self.assertEqual(self._login().status_code, 200)
        with mock.patch.object(main, '_require_job_access', return_value=''):
            with mock.patch.object(main, 'live') as live:
                live.return_value.pause.return_value = {
                    'job_id': 'HIT-1', 'project': 'P', 'spreadsheet_id': 's',
                    'source_folder_id': 'f', 'authorized_source_folder_ids': ['f'],
                    'batches': 0, 'status': 'Paused',
                }
                bad = self.client.post(
                    '/api/jobs/HIT-1/pause',
                    headers={auth.CSRF_HEADER: self._csrf(), 'Origin': 'https://evil.example'},
                )
                self.assertEqual(bad.status_code, 403)

    def test_logout_invalidates_session(self):
        self.assertEqual(self._login().status_code, 200)
        logout = self.client.post(
            '/api/auth/logout',
            headers={'Origin': ORIGIN, auth.CSRF_HEADER: self._csrf()},
        )
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get('/api/auth/session').json()['authenticated'], False)
        self.assertEqual(self.client.get('/api/jobs/HIT-1').status_code, 401)

    def test_session_expiry(self):
        with mock.patch.object(main.settings, 'session_ttl_seconds', 1):
            self.assertEqual(self._login().status_code, 200)
            token = self.client.cookies.get(auth.SESSION_COOKIE)
            record = auth.sessions.get(token)
            self.assertIsNotNone(record)
            record.expires_at = time.time() - 1
            self.assertEqual(self.client.get('/api/auth/session').json()['authenticated'], False)
            self.assertEqual(self.client.get('/api/jobs/HIT-1').status_code, 401)

    def test_login_throttling(self):
        for _ in range(3):
            self.assertEqual(self._login(password='bad').status_code, 401)
        locked = self._login(password='bad')
        self.assertEqual(locked.status_code, 429)

    def test_missing_login_config_fails_closed(self):
        with mock.patch.object(main.settings, 'app_login_username', ''):
            with mock.patch.object(main.settings, 'app_login_password_hash', ''):
                response = self._login()
                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.client.get('/api/jobs/HIT-1').status_code, 401)


class FrontendSecretRemovalTests(unittest.TestCase):
    def test_client_source_has_no_vite_api_token(self):
        root = Path(__file__).resolve().parents[2] / 'client' / 'src'
        offenders = []
        for path in root.rglob('*'):
            if path.suffix not in {'.ts', '.tsx', '.js', '.jsx'}:
                continue
            text = path.read_text(encoding='utf-8')
            if 'VITE_API_TOKEN' in text:
                offenders.append(str(path.relative_to(root.parent)))
        self.assertEqual(offenders, [])


if __name__ == '__main__':
    unittest.main()
