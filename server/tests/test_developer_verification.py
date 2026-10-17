"""Developer Tri Delta Box 19 verification is off by default and API-guarded."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, settings


class DeveloperVerificationGateTests(unittest.TestCase):
    def test_health_reports_flag_default_false(self):
        client = TestClient(app)
        with patch.object(settings, 'enable_developer_verification', False):
            res = client.get('/health')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json().get('developer_verification'))

    def test_benchmark_endpoints_blocked_when_disabled(self):
        client = TestClient(app)
        with patch.object(settings, 'enable_developer_verification', False):
            with patch('app.main.guard', lambda *a, **k: None):
                with patch('app.main.live', lambda: object()):
                    denied = client.post('/api/benchmark/f01-checkpoint', json={'spreadsheet_id': 'register'})
                    self.assertEqual(denied.status_code, 403)
                    self.assertIn('disabled', denied.json()['detail'].lower())
                    denied2 = client.post(
                        '/api/benchmark/compare',
                        json={'spreadsheet_id': 'register', 'benchmark_spreadsheet_id': 'bench'},
                    )
                    self.assertEqual(denied2.status_code, 403)

    def test_settings_default(self):
        self.assertFalse(Settings().enable_developer_verification)


if __name__ == '__main__':
    unittest.main()
