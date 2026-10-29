"""McCallie Drive download retries. Offline only; no provider or register I/O."""
from __future__ import annotations

import ssl
import tempfile
import unittest
from http.client import IncompleteRead
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.adapters import IncompleteDriveDownload
from app.inspection import (
    DRIVE_DOWNLOAD_ATTEMPTS,
    Inspector,
    _is_connection_error,
    _is_drive_download_error,
)


def _jpeg_bytes(size=(64, 48), color='white'):
    out = BytesIO()
    Image.new('RGB', size, color).save(out, format='JPEG')
    return out.getvalue()


class SourceFile:
    def __init__(self, name='McCallie_Year1_Box11_F06_091.jpg', file_id='drive-91'):
        self.name = name
        self.id = file_id
        self.mime_type = 'image/jpeg'


class Settings:
    governing_docs_dir = ''
    openai_api_key = 'test'
    openai_model = 'gpt-test'
    openai_timeout_seconds = 30
    diagnostics_dir = './logs'
    image_original_max_bytes = 4_000_000
    image_original_max_edge = 2048
    image_resize_max_edge = 2048
    image_jpeg_quality = 95


class FlakyDrive:
    def __init__(self, errors, final_bytes):
        self.errors = list(errors)
        self.final_bytes = final_bytes
        self.calls = 0

    def image(self, source):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.final_bytes


class CapturingClient:
    def __init__(self):
        self.responses = self
        self.files = None
        self.parsed = 0

    def parse(self, **kwargs):
        self.parsed += 1
        raise RuntimeError('model-not-reached-for-failure-tests')


class DriveDownloadClassificationTests(unittest.TestCase):
    def test_incomplete_read_is_drive_retryable_not_openai_connection(self):
        exc = IncompleteRead(b'x' * 10, 5)
        self.assertTrue(_is_drive_download_error(exc))
        self.assertFalse(_is_connection_error(exc))

    def test_ssl_eof_is_drive_retryable(self):
        exc = ssl.SSLEOFError('EOF occurred in violation of protocol (_ssl.c:2427)')
        self.assertTrue(_is_drive_download_error(exc))

    def test_openai_timeout_is_not_a_drive_download_failure(self):
        class APITimeoutError(Exception):
            pass

        self.assertFalse(_is_drive_download_error(APITimeoutError('request timed out')))


class DriveDownloadRetryTests(unittest.TestCase):
    def setUp(self):
        self.jpeg = _jpeg_bytes()
        self.docs = tempfile.TemporaryDirectory()
        Path(self.docs.name, 'rules.md').write_text('Inspect every authorized image.\n', encoding='utf-8')
        self.settings = Settings()
        self.settings.governing_docs_dir = self.docs.name
        self.diag_dir = tempfile.TemporaryDirectory()
        self.settings.diagnostics_dir = self.diag_dir.name

    def tearDown(self):
        self.docs.cleanup()
        self.diag_dir.cleanup()

    def _inspect(self, drive, client=None):
        client = client or CapturingClient()
        inspector = Inspector(self.settings, drive, client=client)
        inspector._sleep = lambda _seconds: None
        return inspector, client

    def test_partial_download_then_success_reaches_model_path(self):
        drive = FlakyDrive(
            [IncompleteRead(b'x' * 16, 4530908)],
            self.jpeg,
        )

        class RaiseAfterDownload(CapturingClient):
            def parse(self, **kwargs):
                self.parsed += 1
                self.kwargs = kwargs
                raise RuntimeError('downloaded-ok')

        inspector, client = self._inspect(drive, client=RaiseAfterDownload())
        with self.assertRaisesRegex(RuntimeError, 'downloaded-ok'):
            inspector.inspect('HIT-39d15697ce63fbf7', 'HIT-39d15697ce63fbf7-B0024', [SourceFile()], {
                'folder_code': 'F06', 'prior_open_item': 'None', 'inspection_attempt': 1,
            }, [])
        self.assertEqual(drive.calls, 2)
        self.assertEqual(client.parsed, 1)
        content = client.kwargs['input'][1]['content']
        image = next(item for item in content if item.get('type') == 'input_image')
        self.assertTrue(image['image_url'].startswith('data:image/jpeg;base64,'))

    def test_ssl_eof_then_success(self):
        drive = FlakyDrive(
            [ssl.SSLEOFError('EOF occurred in violation of protocol (_ssl.c:2427)')],
            self.jpeg,
        )

        class RaiseClient(CapturingClient):
            def parse(self, **kwargs):
                self.parsed += 1
                raise RuntimeError('downloaded-ok')

        inspector, client = self._inspect(drive, client=RaiseClient())
        with self.assertRaisesRegex(RuntimeError, 'downloaded-ok'):
            inspector.inspect('HIT-1', 'B1', [SourceFile()], {
                'folder_code': 'F06', 'prior_open_item': 'None', 'inspection_attempt': 1,
            }, [])
        self.assertEqual(drive.calls, 2)
        self.assertEqual(client.parsed, 1)

    def test_repeated_failure_stops_at_limit_and_names_file(self):
        drive = FlakyDrive(
            [IncompleteRead(b'x' * 16, 4530908)] * DRIVE_DOWNLOAD_ATTEMPTS,
            self.jpeg,
        )
        inspector, client = self._inspect(drive)
        with self.assertRaisesRegex(
            RuntimeError,
            r'Drive download failed for McCallie_Year1_Box11_F06_091\.jpg after 3 attempts: IncompleteRead',
        ):
            inspector.inspect('HIT-39d15697ce63fbf7', 'HIT-39d15697ce63fbf7-B0024', [SourceFile()], {
                'folder_code': 'F06', 'prior_open_item': 'None', 'inspection_attempt': 2,
            }, [])
        self.assertEqual(drive.calls, DRIVE_DOWNLOAD_ATTEMPTS)
        self.assertEqual(client.parsed, 0)
        events = []
        for path in Path(self.diag_dir.name).glob('*.json'):
            import json
            events.extend(json.loads(path.read_text(encoding='utf-8')))
        retries = [row for row in events if row.get('event') == 'drive_download_retry']
        self.assertEqual(len(retries), DRIVE_DOWNLOAD_ATTEMPTS)
        self.assertTrue(all(row.get('filename') == 'McCallie_Year1_Box11_F06_091.jpg' for row in retries))
        errors = [row for row in events if row.get('event') == 'inspection_error']
        self.assertEqual(len(errors), 1)
        self.assertIn('McCallie_Year1_Box11_F06_091.jpg', errors[0]['error'])

    def test_incomplete_content_never_reaches_model(self):
        drive = FlakyDrive(
            [IncompleteDriveDownload('McCallie_Year1_Box11_F06_091.jpg: downloaded 10 bytes, expected 100')],
            self.jpeg,
        )
        # Exhaust retries with size mismatch each time.
        drive.errors = [
            IncompleteDriveDownload('McCallie_Year1_Box11_F06_091.jpg: downloaded 10 bytes, expected 100')
        ] * DRIVE_DOWNLOAD_ATTEMPTS
        inspector, client = self._inspect(drive)
        with self.assertRaisesRegex(RuntimeError, 'McCallie_Year1_Box11_F06_091.jpg'):
            inspector.inspect('HIT-1', 'B1', [SourceFile()], {
                'folder_code': 'F06', 'prior_open_item': 'None', 'inspection_attempt': 1,
            }, [])
        self.assertEqual(client.parsed, 0)

    def test_normal_successful_download_unchanged(self):
        class OnceDrive:
            def __init__(self):
                self.calls = 0

            def image(self, source):
                self.calls += 1
                return _jpeg_bytes()

        drive = OnceDrive()

        class RaiseClient(CapturingClient):
            def parse(self, **kwargs):
                self.parsed += 1
                raise RuntimeError('downloaded-ok')

        inspector, client = self._inspect(drive, client=RaiseClient())
        with self.assertRaisesRegex(RuntimeError, 'downloaded-ok'):
            inspector.inspect('HIT-1', 'B1', [SourceFile('ok.jpg')], {
                'folder_code': 'F06', 'prior_open_item': 'None', 'inspection_attempt': 1,
            }, [])
        self.assertEqual(drive.calls, 1)
        self.assertEqual(client.parsed, 1)


if __name__ == '__main__':
    unittest.main()
