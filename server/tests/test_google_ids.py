"""Unit tests for Drive/Sheets link and ID normalization."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.google_ids import (
    FOLDER_MIME,
    GoogleIdError,
    ensure_drive_folder,
    ensure_spreadsheet,
    parse_drive_folder_id,
    parse_spreadsheet_id,
)
from app.models import FolderAuthorize, JobCreate


FOLDER = '1AbCdefGhIjKlMnOpQrStUvWxYz012345'
SHEET = '1ZyXwVuTsRqPoNmLkJiHgFeDcBa987654'


class ParseDriveFolderTests(unittest.TestCase):
    def test_raw_id_and_whitespace(self):
        self.assertEqual(parse_drive_folder_id(f'  {FOLDER}  '), FOLDER)
        self.assertEqual(parse_drive_folder_id('box'), 'box')

    def test_folder_urls(self):
        self.assertEqual(
            parse_drive_folder_id(f'https://drive.google.com/drive/folders/{FOLDER}'),
            FOLDER,
        )
        self.assertEqual(
            parse_drive_folder_id(f'https://drive.google.com/drive/u/0/folders/{FOLDER}?usp=sharing'),
            FOLDER,
        )
        self.assertEqual(
            parse_drive_folder_id(f'https://drive.google.com/open?id={FOLDER}'),
            FOLDER,
        )

    def test_rejects_spoof_and_credentials(self):
        with self.assertRaises(GoogleIdError):
            parse_drive_folder_id(f'https://drive.google.com.evil.test/drive/folders/{FOLDER}')
        with self.assertRaises(GoogleIdError):
            parse_drive_folder_id(f'https://user:pass@drive.google.com/drive/folders/{FOLDER}')
        with self.assertRaises(GoogleIdError):
            parse_drive_folder_id(f'https://docs.google.com/spreadsheets/d/{SHEET}/edit')
        with self.assertRaises(GoogleIdError):
            parse_drive_folder_id(f'https://drive.google.com/drive/folders/{FOLDER}?resourcekey=abc')


class ParseSpreadsheetTests(unittest.TestCase):
    def test_raw_id_and_urls(self):
        self.assertEqual(parse_spreadsheet_id(f' {SHEET} '), SHEET)
        self.assertEqual(
            parse_spreadsheet_id(f'https://docs.google.com/spreadsheets/d/{SHEET}/edit#gid=12345'),
            SHEET,
        )
        self.assertEqual(
            parse_spreadsheet_id(f'https://docs.google.com/spreadsheets/d/{SHEET}/edit?usp=sharing&gid=0'),
            SHEET,
        )

    def test_rejects_gid_as_id_and_export_and_spoof(self):
        with self.assertRaises(GoogleIdError):
            parse_spreadsheet_id('https://docs.google.com/spreadsheets/d/e/2PACX-export/pub')
        with self.assertRaises(GoogleIdError):
            parse_spreadsheet_id(f'https://docs.google.evil.test/spreadsheets/d/{SHEET}/edit')
        with self.assertRaises(GoogleIdError):
            parse_spreadsheet_id(f'https://drive.google.com/drive/folders/{FOLDER}')
        # Numeric gid alone is not a spreadsheet ID shape we accept as a URL path
        with self.assertRaises(GoogleIdError):
            parse_spreadsheet_id('https://docs.google.com/spreadsheets/d/e/pubhtml')


class ModelNormalizationTests(unittest.TestCase):
    def test_job_create_normalizes_links(self):
        req = JobCreate(
            project='McCallie Archives',
            source_folder_id=f'https://drive.google.com/drive/folders/{FOLDER}?usp=share_link',
            spreadsheet_id=f'https://docs.google.com/spreadsheets/d/{SHEET}/edit#gid=9',
            batch_size=10,
            box='Box 1',
        )
        self.assertEqual(req.source_folder_id, FOLDER)
        self.assertEqual(req.spreadsheet_id, SHEET)
        self.assertEqual(req.project, 'McCallie Archives')

    def test_job_create_requires_box_and_project(self):
        with self.assertRaises(Exception):
            JobCreate(
                project='',
                source_folder_id=FOLDER,
                spreadsheet_id=SHEET,
                box='Box 1',
            )
        with self.assertRaises(Exception):
            JobCreate(
                project='Demo',
                source_folder_id=FOLDER,
                spreadsheet_id=SHEET,
                box='',
            )

    def test_folder_authorize_normalizes(self):
        req = FolderAuthorize(source_folder_id=f'https://drive.google.com/drive/u/1/folders/{FOLDER}/')
        self.assertEqual(req.source_folder_id, FOLDER)


class AccessibilityGateTests(unittest.TestCase):
    def test_wrong_mime_type(self):
        drive = MagicMock()
        drive.file_meta.return_value = {'id': FOLDER, 'mimeType': 'application/pdf', 'name': 'x'}
        with self.assertRaises(GoogleIdError) as ctx:
            ensure_drive_folder(drive, FOLDER)
        self.assertIn('not a folder', str(ctx.exception))

    def test_inaccessible_folder(self):
        class Resp:
            status = 403

        class Exc(Exception):
            resp = Resp()

        drive = MagicMock()
        drive.file_meta.side_effect = Exc('denied')
        with self.assertRaises(GoogleIdError) as ctx:
            ensure_drive_folder(drive, FOLDER)
        self.assertIn('inaccessible', str(ctx.exception).lower())

    def test_spreadsheet_ok_and_inaccessible(self):
        sheets = MagicMock()
        sheets.spreadsheet_meta.return_value = {'spreadsheetId': SHEET, 'properties': {'title': 'Reg'}}
        self.assertEqual(ensure_spreadsheet(sheets, SHEET)['spreadsheetId'], SHEET)

        class Resp:
            status = 404

        class Exc(Exception):
            resp = Resp()

        sheets.spreadsheet_meta.side_effect = Exc('missing')
        with self.assertRaises(GoogleIdError):
            ensure_spreadsheet(sheets, SHEET)

    def test_missing_meta_methods_are_noop_for_fakes(self):
        class FakeDrive:
            pass

        class FakeSheets:
            pass

        self.assertEqual(ensure_drive_folder(FakeDrive(), 'box')['mimeType'], FOLDER_MIME)
        self.assertEqual(ensure_spreadsheet(FakeSheets(), 'register')['spreadsheetId'], 'register')


if __name__ == '__main__':
    unittest.main()
