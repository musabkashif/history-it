"""Worksheet grid expansion for Drive identity columns. No live Sheets or model calls."""
from __future__ import annotations

import re
import threading
import unittest

from app.adapters import SheetResizeError, Sheets, sheet_resize_denied
from app.inspection import transient_inspection_failure
from app.models import ASSET_HEADERS, RECORD_HEADERS, AssetRow, InspectionOutput, RecordRow
from app.orchestrator import Orchestrator
from tests.test_workflow import CapturingInspector, MemorySheets, Settings, filled, install_job, queue_row


IDENTITY = ('Drive File ID', 'Source Folder ID')
ASSET_QC_HEADERS = (
    'Project', 'Processing Unit / Source Folder', 'Digital Asset', 'Box', 'Source Group / Folder',
    'Assigned Record ID', 'Template', 'Type', 'Asset Role', 'Required Crop Standard', 'QC Status',
    'Action Needed', 'Expected Output Asset Count', 'QC Confidence', 'QC Note',
    'Odyssey Ingest / Visibility Note', 'Production Queue Eligible', 'Exception / Duplicate Note',
)
QUEUE_HEADERS = (
    'Project', 'Processing Unit / Source Folder', 'Task ID', 'Assigned Record ID', 'Digital Asset',
    'Box', 'Source Group / Folder', 'Template', 'Type', 'Action Needed', 'Task Scope',
    'Expected Output Asset Count', 'Task Status', 'QC Confidence', 'Production Note',
    'Carried Forward From',
)
RANGE = re.compile(r"^'(?P<sheet>(?:[^']|'')*)'!(?P<body>[A-Z0-9:]+)$")


class Denied(Exception):
    def __init__(self, status, message):
        self.resp = type('Resp', (), {'status': status})()
        super().__init__(message)


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


def _column_index(letters):
    number = 0
    for character in letters:
        number = number * 26 + ord(character) - 64
    return number - 1


def _split_ref(text):
    letters = ''.join(character for character in text if character.isalpha())
    digits = ''.join(character for character in text if character.isdigit())
    column = _column_index(letters) if letters else 0
    row = int(digits) if digits else None
    return row, column, bool(letters)


class Workbook:
    def __init__(self):
        self.sheets = {}
        self.log = []
        self.fail_resize_status = None
        self.fail_read_status = None
        self.fail_write_at = None

    def add(self, sid, title, headers, sheet_id, column_count=None, cells=None, header_row=4):
        spec = {
            'sheet_id': sheet_id,
            'column_count': len(headers) if column_count is None else column_count,
            'header_row': header_row,
            'cells': {},
        }
        for index, name in enumerate(headers):
            spec['cells'][(header_row, index)] = name
        for key, value in (cells or {}).items():
            spec['cells'][key] = value
        self.sheets[(sid, title)] = spec
        return spec


class Api:
    def __init__(self, book):
        self.book = book

    def get(self, spreadsheetId, fields):
        def run():
            if self.book.fail_read_status:
                raise Denied(self.book.fail_read_status, 'grid read denied')
            self.book.log.append(('grid', spreadsheetId))
            found = []
            for (sid, title), spec in self.book.sheets.items():
                if sid != spreadsheetId:
                    continue
                found.append({'properties': {
                    'sheetId': spec['sheet_id'],
                    'title': title,
                    'gridProperties': {'columnCount': spec['column_count']},
                }})
            return {'sheets': found}
        return _Call(run)

    def batchUpdate(self, spreadsheetId, body):
        def run():
            if self.book.fail_resize_status:
                raise Denied(self.book.fail_resize_status, 'resize denied')
            requests = body.get('requests') or []
            if len(requests) != 1 or set(requests[0]) != {'updateSheetProperties'}:
                raise AssertionError(body)
            update = requests[0]['updateSheetProperties']
            if update.get('fields') != 'gridProperties.columnCount':
                raise AssertionError(update)
            props = update['properties']
            grid = props['gridProperties']
            if set(grid) != {'columnCount'}:
                raise AssertionError(grid)
            matches = [
                spec for (sid, _title), spec in self.book.sheets.items()
                if sid == spreadsheetId and spec['sheet_id'] == props['sheetId']
            ]
            if len(matches) != 1:
                raise AssertionError(props['sheetId'])
            spec = matches[0]
            if grid['columnCount'] < spec['column_count']:
                raise AssertionError('grid shrink')
            spec['column_count'] = grid['columnCount']
            self.book.log.append(('expand', spreadsheetId, props['sheetId'], grid['columnCount']))
            return {}
        return _Call(run)

    def values(self):
        return _Values(self.book)


class _Values:
    def __init__(self, book):
        self.book = book

    def _bounds(self, spreadsheet_id, range_):
        match = RANGE.match(range_)
        if match is None:
            raise AssertionError(range_)
        spec = self.book.sheets[(spreadsheet_id, match.group('sheet'))]
        body = match.group('body')
        if ':' not in body:
            row, column, _named = _split_ref(body)
            return spec, row, column, row, column
        left, right = body.split(':')
        start_row, start_column, start_named = _split_ref(left)
        end_row, end_column, end_named = _split_ref(right)
        if start_row is None:
            start_row = 1
        if not start_named:
            start_column = 0
        if end_row is None:
            occupied = [row for (row, _column) in spec['cells'] if row >= start_row]
            end_row = max(occupied) if occupied else start_row - 1
        if not end_named:
            end_column = spec['column_count'] - 1
        return spec, start_row, start_column, end_row, end_column

    def _matrix(self, spec, start_row, start_column, end_row, end_column):
        if end_row < start_row:
            return []
        rows = []
        for row_number in range(start_row, end_row + 1):
            line = [
                spec['cells'].get((row_number, column), '')
                for column in range(start_column, end_column + 1)
            ]
            while line and line[-1] == '':
                line.pop()
            rows.append(line)
        while rows and not rows[-1]:
            rows.pop()
        return rows

    def get(self, spreadsheetId, range, valueRenderOption):
        def run():
            spec, start_row, start_column, end_row, end_column = self._bounds(spreadsheetId, range)
            self.book.log.append(('read', spreadsheetId, range))
            return {'values': self._matrix(spec, start_row, start_column, end_row, end_column)}
        return _Call(run)

    def update(self, spreadsheetId, range, valueInputOption, body):
        def run():
            spec, start_row, start_column, end_row, end_column = self._bounds(spreadsheetId, range)
            values = body['values']
            width = max((len(row) for row in values), default=0)
            last_column = start_column + max(width - 1, 0)
            if last_column >= spec['column_count']:
                raise Denied(400, f'Range ({range}) exceeds grid limits. Max columns: {spec["column_count"]}')
            if self.book.fail_write_at is not None and last_column >= self.book.fail_write_at:
                raise RuntimeError('header write interrupted')
            for row_offset, row in enumerate(values):
                for column_offset, value in enumerate(row):
                    spec['cells'][(start_row + row_offset, start_column + column_offset)] = value
            self.book.log.append(('write', spreadsheetId, range))
            return {}
        return _Call(run)


def adapter(book):
    sheets = Sheets.__new__(Sheets)
    sheets.api = Api(book)
    sheets._headers = {}
    sheets._cache = {}
    sheets._lock = threading.RLock()
    return sheets


def header_names(spec):
    row = spec['header_row']
    indexes = [column for (cell_row, column) in spec['cells'] if cell_row == row]
    if not indexes:
        return []
    return [spec['cells'].get((row, column), '') for column in range(max(indexes) + 1)]


def register(book, sid, sheets):
    book.add(sid, 'Asset Staging', ASSET_HEADERS, sheet_id=10, cells={(1, 0): '=SUM(1,1)', (5, 23): 'kept-note'})
    book.add(sid, 'Asset QC', ASSET_QC_HEADERS, sheet_id=11, cells={(5, 0): 'qc-row'})
    book.add(sid, 'Production Queue', QUEUE_HEADERS, sheet_id=12, cells={(5, 15): 'carried'})
    book.add(sid, 'Record Staging', RECORD_HEADERS, sheet_id=13, cells={(5, 33): 'record-note'})
    return sheets


class GridExpansionTests(unittest.TestCase):
    def test_full_asset_staging_expands_before_columns_y_and_z(self):
        self.assertEqual(len(ASSET_HEADERS), 24)
        self.assertEqual(Sheets.col(23), 'X')
        self.assertEqual(Sheets.col(24), 'Y')
        self.assertEqual(Sheets.col(25), 'Z')
        book = Workbook()
        register(book, 'existing-register', None)
        sheets = adapter(book)
        spec = book.sheets[('existing-register', 'Asset Staging')]
        self.assertEqual(spec['column_count'], 24)

        sheets.ensure_columns('existing-register', 'Asset Staging', IDENTITY)

        self.assertEqual(spec['column_count'], 26)
        self.assertEqual(header_names(spec)[23:], ['Commit Note', 'Drive File ID', 'Source Folder ID'])
        self.assertEqual(spec['cells'][(1, 0)], '=SUM(1,1)')
        self.assertEqual(spec['cells'][(5, 23)], 'kept-note')
        self.assertNotIn((5, 24), spec['cells'])
        expand_at = next(index for index, item in enumerate(book.log) if item[0] == 'expand')
        write_at = next(index for index, item in enumerate(book.log) if item[0] == 'write')
        self.assertLess(expand_at, write_at)
        self.assertEqual(book.log[expand_at], ('expand', 'existing-register', 10, 26))
        self.assertEqual([item[2] for item in book.log if item[0] == 'write'], [
            "'Asset Staging'!Y4",
            "'Asset Staging'!Z4",
        ])

    def test_repeat_does_not_duplicate_headers(self):
        book = Workbook()
        register(book, 'existing-register', None)
        sheets = adapter(book)
        sheets.ensure_columns('existing-register', 'Asset Staging', IDENTITY)
        expands = [item for item in book.log if item[0] == 'expand']
        writes = [item for item in book.log if item[0] == 'write']
        sheets.ensure_columns('existing-register', 'Asset Staging', IDENTITY)
        spec = book.sheets[('existing-register', 'Asset Staging')]
        names = header_names(spec)
        self.assertEqual(names.count('Drive File ID'), 1)
        self.assertEqual(names.count('Source Folder ID'), 1)
        self.assertEqual(names.count('Project'), 1)
        self.assertEqual([item for item in book.log if item[0] == 'expand'], expands)
        self.assertEqual([item for item in book.log if item[0] == 'write'], writes)
        self.assertEqual(spec['column_count'], 26)

    def test_interrupted_append_retries_without_a_duplicate(self):
        book = Workbook()
        register(book, 'existing-register', None)
        sheets = adapter(book)
        book.fail_write_at = 25
        with self.assertRaises(RuntimeError):
            sheets.ensure_columns('existing-register', 'Asset Staging', IDENTITY)
        spec = book.sheets[('existing-register', 'Asset Staging')]
        self.assertEqual(header_names(spec).count('Drive File ID'), 1)
        self.assertNotIn('Source Folder ID', header_names(spec))
        self.assertEqual(spec['column_count'], 26)
        book.fail_write_at = None
        sheets.ensure_columns('existing-register', 'Asset Staging', IDENTITY)
        names = header_names(spec)
        self.assertEqual(names.count('Drive File ID'), 1)
        self.assertEqual(names.count('Source Folder ID'), 1)
        self.assertEqual([item for item in book.log if item[0] == 'expand'], [('expand', 'existing-register', 10, 26)])

    def test_all_three_worksheets_and_both_register_kinds(self):
        cases = (
            ('Asset Staging', 24, 26, 10, 'Y', 'Z'),
            ('Asset QC', 18, 20, 11, 'S', 'T'),
            ('Production Queue', 16, 18, 12, 'Q', 'R'),
        )
        for sid in ('existing-register', 'copied-register'):
            book = Workbook()
            register(book, sid, None)
            if sid == 'copied-register':
                for (_id, _title), spec in book.sheets.items():
                    spec['cells'] = {
                        key: value for key, value in spec['cells'].items() if key[0] == 4
                    }
            sheets = adapter(book)
            for title, before, after, sheet_id, first, second in cases:
                spec = book.sheets[(sid, title)]
                self.assertEqual(spec['column_count'], before, title)
                preserved = dict(spec['cells'])
                sheets.ensure_columns(sid, title, IDENTITY)
                self.assertEqual(spec['column_count'], after, title)
                for key, value in preserved.items():
                    self.assertEqual(spec['cells'][key], value, key)
                names = header_names(spec)
                self.assertEqual(names[-2:], list(IDENTITY))
                self.assertEqual(names.count('Drive File ID'), 1)
                self.assertEqual([item for item in book.log if item[0] == 'expand' and item[2] == sheet_id][-1], (
                    'expand', sid, sheet_id, after,
                ))
                written = [item[2] for item in book.log if item[0] == 'write' and item[2].startswith(f"'{title}'")]
                self.assertEqual(written[-2:], [f"'{title}'!{first}4", f"'{title}'!{second}4"])
            record = book.sheets[(sid, 'Record Staging')]
            before_cells = dict(record['cells'])
            sheets.write(sid, 'Record Staging', {
                'Project': 'Tri Delta Chapters',
                'Proposed Record ID': 'REC-1',
                'Record Kind': 'Normal Odyssey',
            }, ('Proposed Record ID', 'REC-1'))
            self.assertEqual(record['column_count'], 34)
            self.assertEqual(before_cells[(4, 0)], record['cells'][(4, 0)])
            self.assertFalse(any(item[0] == 'expand' and item[2] == 13 for item in book.log))

    def test_existing_headers_and_a_wider_grid_are_left_alone(self):
        book = Workbook()
        headers = list(ASSET_HEADERS) + list(IDENTITY)
        book.add('existing-register', 'Asset Staging', headers, sheet_id=10, column_count=30, cells={(5, 23): 'kept-note'})
        sheets = adapter(book)
        sheets.ensure_columns('existing-register', 'Asset Staging', IDENTITY)
        spec = book.sheets[('existing-register', 'Asset Staging')]
        self.assertEqual(spec['column_count'], 30)
        self.assertFalse(any(item[0] == 'expand' for item in book.log))
        self.assertFalse(any(item[0] == 'write' for item in book.log))
        self.assertEqual(header_names(spec).count('Drive File ID'), 1)
        self.assertEqual(spec['cells'][(5, 23)], 'kept-note')

        spare = Workbook()
        spare.add('copied-register', 'Asset Staging', ASSET_HEADERS, sheet_id=10, column_count=30)
        copied = adapter(spare)
        copied.ensure_columns('copied-register', 'Asset Staging', IDENTITY)
        copied_spec = spare.sheets[('copied-register', 'Asset Staging')]
        self.assertEqual(copied_spec['column_count'], 30)
        self.assertFalse(any(item[0] == 'expand' for item in spare.log))
        self.assertEqual(header_names(copied_spec)[24:26], list(IDENTITY))

    def test_resize_permission_is_not_retryable(self):
        book = Workbook()
        register(book, 'existing-register', None)
        book.fail_resize_status = 403
        sheets = adapter(book)
        with self.assertRaises(SheetResizeError) as caught:
            sheets.ensure_columns('existing-register', 'Asset Staging', IDENTITY)
        message = str(caught.exception)
        self.assertIn('Service account lacks permission to resize Asset Staging', message)
        self.assertIn('not retryable', message)
        self.assertIn('24 columns', message)
        self.assertIn('26 are required', message)
        self.assertTrue(sheet_resize_denied(message))
        self.assertFalse(transient_inspection_failure(message))
        spec = book.sheets[('existing-register', 'Asset Staging')]
        self.assertEqual(spec['column_count'], 24)
        self.assertNotIn('Drive File ID', header_names(spec))
        self.assertFalse(any(item[0] == 'write' for item in book.log))
        self.assertEqual(spec['cells'][(5, 23)], 'kept-note')

    def test_grid_read_permission_is_not_retryable(self):
        book = Workbook()
        register(book, 'existing-register', None)
        book.fail_read_status = 401
        sheets = adapter(book)
        with self.assertRaises(SheetResizeError) as caught:
            sheets.ensure_columns('existing-register', 'Production Queue', IDENTITY)
        self.assertIn('Production Queue', str(caught.exception))
        self.assertIn('not retryable', str(caught.exception))
        self.assertEqual(book.sheets[('existing-register', 'Production Queue')]['column_count'], 16)
        self.assertFalse(any(item[0] in ('expand', 'write') for item in book.log))


class ResizeDeniedSheets(MemorySheets):
    def write(self, sid, sheet, row, key=None):
        if sheet == 'Asset Staging' and row.get('Drive File ID'):
            raise SheetResizeError(
                'Service account lacks permission to resize Asset Staging. '
                'The worksheet grid has 24 columns and 26 are required. '
                'This failure is not retryable until an editor grants access. Existing cells were not changed.'
            )
        return super().write(sid, sheet, row, key)


class ResizeAdmissionTests(unittest.TestCase):
    def _output(self, filename):
        asset = AssetRow.model_validate(filled(
            ASSET_HEADERS,
            **{
                'Project': 'Tri Delta Chapters',
                'Job ID': 'HIT-1',
                'Batch ID': 'HIT-1-B0001',
                'Processing Unit / Source Folder': 'F01',
                'Asset Sequence': '1',
                'Digital Asset': filename,
                'Box': 'Chapters Box 19',
                'Source Group / Folder': 'F01',
                'Assigned Record ID': 'REC-001',
                'Asset Role': 'Page',
                'Required Crop Standard': 'N/A',
                'QC Status': 'Pass',
                'Action Needed': 'No Adjustment',
                'Expected Output Asset Count': '1',
                'QC Confidence': 'High',
                'Production Queue Eligible': 'No',
                'Inspection Complete': 'Yes',
                'Commit Status': 'Pending Commit',
            },
        ))
        record = RecordRow.model_validate(filled(
            RECORD_HEADERS,
            **{
                'Project': 'Tri Delta Chapters',
                'Job ID': 'HIT-1',
                'Batch ID': 'HIT-1-B0001',
                'Processing Unit / Source Folder': 'F01',
                'Record Sequence': '1',
                'Proposed Record ID': 'REC-001',
                'Record Kind': 'Normal Odyssey',
                'Record Operation': 'Create New',
                'Box': 'Chapters Box 19',
                'Source Group / Folder': 'F01',
                'Digital Asset Count': '1',
                'Logical Component Count': '1',
                'Template': 'Document',
                'Type': 'Report',
                'Metadata Readiness': 'Ready',
                'Boundary State After Record': 'Closed',
                'New Batch Assets': filename,
                'Full Record Assets After Commit': filename,
                'Inspection Complete': 'Yes',
                'Commit Status': 'Pending Commit',
            },
        ))
        return InspectionOutput(assets=[asset], records=[record])

    def test_staging_resize_denial_is_blocked_not_retried(self):
        filename = 'page.jpg'
        sheets = ResizeDeniedSheets()
        install_job(sheets, [queue_row(names=[filename], **{
            'Project': 'Tri Delta Chapters',
            'Box': 'Chapters Box 19',
            'Source Group / Folder': 'F01',
        })])
        inspector = CapturingInspector(output=self._output(filename))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        row = sheets.rows('register', 'Batch Queue')[0]
        self.assertEqual(len(inspector.calls), 1)
        self.assertEqual(row['Batch Status'], 'Blocked - Human Review')
        self.assertNotIn('Retryable', row['Batch Status'])
        self.assertIn('not retryable', row['Error / Retry Note'])
        self.assertEqual(sheets.rows('register', 'Asset Staging'), [])

    def test_commit_resize_denial_is_blocked_not_retried(self):
        sheets = MemorySheets()
        install_job(sheets, [queue_row(**{'Batch Status': 'Inspection Complete - Awaiting Commit'})])
        orchestrator = Orchestrator(sheets, None, CapturingInspector(), Settings())

        def commit(_sid, _batch):
            raise SheetResizeError(
                'Service account lacks permission to resize Production Queue. '
                'The worksheet grid has 16 columns and 18 are required. '
                'This failure is not retryable until an editor grants access. Existing cells were not changed.'
            )

        orchestrator.committer.commit = commit
        orchestrator.run('HIT-1')
        row = sheets.rows('register', 'Batch Queue')[0]
        self.assertEqual(row['Batch Status'], 'Blocked - Human Review')
        self.assertIn('Production Queue', row['Error / Retry Note'])
        self.assertNotIn('Retryable', row['Batch Status'])

    def test_retry_refuses_a_resize_denial(self):
        sheets = MemorySheets()
        note = (
            'Service account lacks permission to resize Asset Staging. '
            'The worksheet grid has 24 columns and 26 are required. '
            'This failure is not retryable until an editor grants access. Existing cells were not changed.'
        )
        install_job(sheets, [queue_row(**{
            'Batch Status': 'Blocked - Human Review',
            'Error / Retry Note': note,
            'Inspection Attempt': '2',
        })])
        orchestrator = Orchestrator(sheets, None, CapturingInspector(), Settings())
        with self.assertRaises(SheetResizeError) as caught:
            orchestrator.retry('HIT-1-B0001')
        self.assertIn('not retryable', str(caught.exception))
        row = sheets.rows('register', 'Batch Queue')[0]
        self.assertEqual(row['Batch Status'], 'Blocked - Human Review')
        self.assertEqual(row['Inspection Attempt'], '2')
        self.assertEqual(row['Error / Retry Note'], note)


if __name__ == '__main__':
    unittest.main()
