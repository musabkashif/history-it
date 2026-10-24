import json
import unittest
from pathlib import Path

from app.box_folders import flag_probable_duplicates, partition_immediate_folders
from app.decisions import apply_governing_constraints, decision_instructions
from app.identity import assign_identity
from app.models import ASSET_HEADERS, RECORD_HEADERS, AssetRow, InspectionOutput, JobCreate, RecordRow, Source
from app.commit import scoped_metrics
from app.orchestrator import Orchestrator
from app.references import reference_mismatches, resolve_reference
from app.register_state import finalize_completed_box, plan_reconciliation, reconcile_register
from client_reference.commit_engine import build_task_rows, calculate_metrics, plan_commit
from tests.test_stage import MemorySheets as StageSheets
from tests.test_workflow import MemorySheets, Settings as WorkflowSettings, filled


def source(folder_id, name, path, file_id=None, content_hash=''):
    return Source(
        id=file_id or (folder_id + '-' + name),
        name=name,
        folder_id=folder_id,
        folder_name=path.split('/')[-2],
        path=path,
        mime_type='image/jpeg',
        content_hash=content_hash,
    )


def asset(name, record_id, action='No Adjustment', note=''):
    row = AssetRow.model_validate(filled(ASSET_HEADERS, **{
        'Digital Asset': name,
        'Assigned Record ID': record_id,
        'Action Needed': action,
        'QC Status': 'Pass',
        'Required Crop Standard': 'Archival Border',
        'Inspection Complete': 'Yes',
        'QC Note': note,
    }))
    return row


def record(record_id, template='Document', type_name='Report', kind='Normal Odyssey', readiness='Ready', note=''):
    return RecordRow.model_validate(filled(RECORD_HEADERS, **{
        'Proposed Record ID': record_id,
        'Record Kind': kind,
        'Record Operation': 'Create New',
        'Template': template,
        'Type': type_name,
        'Metadata Readiness': readiness,
        'Boundary State After Record': 'Closed',
        'Production Task Count': '0',
        'Inspection Complete': 'Yes',
        'Review Note': note,
        'Further Detail': note,
    }))


class EmptyFolderTests(unittest.TestCase):
    def test_empty_f01_is_skipped_and_populated_f05_continues(self):
        page = source('f05', 'page.jpg', 'Chapters Box 33/F05/page.jpg', file_id='drive-f05')
        sources, skipped = partition_immediate_folders([
            {'folder_id': 'f01', 'folder_name': 'F01', 'images': []},
            {'folder_id': 'f05', 'folder_name': 'F05', 'images': [page]},
        ])
        self.assertEqual([item.folder_name for item in sources], ['F05'])
        self.assertEqual(skipped[0]['event'], 'Skipped / Empty')
        self.assertIn('F01', skipped[0]['detail'])

        class Drive:
            def discover(self, root):
                return sources, skipped

        class Settings:
            job_index_spreadsheet_id = 'index'
            batch_size = 10

        sheets = StageSheets()
        Orchestrator(sheets, Drive(), None, Settings()).create(JobCreate(
            project='Tri Delta Chapters',
            source_folder_id='box-33',
            spreadsheet_id='register',
            batch_size=10,
            box='Chapters Box 33',
        ))
        queued = sheets.rows('register', 'Batch Queue')
        self.assertEqual([row['Source Group / Folder'] for row in queued], ['F05'])
        events = sheets.rows('register', 'Job Events')
        self.assertEqual(events[0]['Event'], 'Skipped / Empty')
        self.assertEqual(events[0]['Folder'], 'F01')
        summary = sheets.rows('register', 'Folder Processing Unit Summary')
        self.assertEqual(summary[0]['Processing Status'], 'Skipped / Empty')

    def test_a_parent_with_only_empty_folders_fails(self):
        with self.assertRaisesRegex(ValueError, 'processable images'):
            partition_immediate_folders([
                {'folder_id': 'f01', 'folder_name': 'F01', 'images': []},
            ])


class DuplicateFilenameTests(unittest.TestCase):
    def test_same_filename_and_different_drive_ids_both_commit(self):
        first = source('f01', 'same.jpg', 'Box/F01/same.jpg', file_id='drive-a', content_hash='abc')
        second = source('f01', 'same.jpg', 'Box/F01/same.jpg', file_id='drive-b', content_hash='abc')
        flag_probable_duplicates([first, second])
        self.assertIn('Probable duplicate', first.duplicate_warning)
        queue = {
            'Batch ID': 'HIT-1-B0001',
            'Batch Status': 'Inspection Complete - Awaiting Commit',
            'Planned Asset Count': '2',
        }
        assets = []
        for file_id in ('drive-a', 'drive-b'):
            assets.append({
                'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-1', 'Batch ID': 'HIT-1-B0001',
                'Processing Unit / Source Folder': 'F01', 'Digital Asset': 'same.jpg',
                'Box': 'Chapters Box 34', 'Source Group / Folder': 'F01',
                'Assigned Record ID': 'local-1', 'Asset Role': 'Document',
                'Required Crop Standard': 'N/A', 'QC Status': 'Pass', 'Action Needed': 'No Adjustment',
                'Expected Output Asset Count': '1', 'QC Confidence': 'High', 'QC Note': '',
                'Odyssey Ingest / Visibility Note': '', 'Production Queue Eligible': 'No',
                'Production Task Scope': '', 'Production Task Note': '',
                'Exception / Duplicate Note': first.duplicate_warning,
                'Inspection Complete': 'Yes', 'Commit Status': 'Pending Commit', 'Commit Note': '',
                'Drive File ID': file_id, 'Source Folder ID': 'f01',
            })
        records = [{
            'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-1', 'Batch ID': 'HIT-1-B0001',
            'Processing Unit / Source Folder': 'F01', 'Proposed Record ID': 'local-1',
            'Record Kind': 'Normal Odyssey', 'Record Operation': 'Create New',
            'Box': 'Chapters Box 34', 'Source Group / Folder': 'F01',
            'Template': 'Document', 'Type': 'Report', 'Metadata Readiness': 'Ready',
            'Boundary State After Record': 'Closed', 'Inspection Complete': 'Yes',
            'Commit Status': 'Pending Commit', 'Production Task Count': '0',
            'Digital Asset Count': '2', 'Logical Component Count': '2',
            'New Batch Assets': 'same.jpg [drive-a]; same.jpg [drive-b]',
            'Existing Committed Assets': '',
            'Full Record Assets After Commit': 'same.jpg [drive-a]; same.jpg [drive-b]',
            'Review Note': '', 'Further Detail': '', 'Notes': '',
        }]
        plan = plan_commit(queue, assets, records, {'Asset QC': [], 'Itemization': [], 'Metadata References': [], 'Production Queue': [], 'Ready for Metadata': []}, '10/22/26')
        written = plan.append_rows['Asset QC']
        self.assertEqual(len(written), 2)
        self.assertEqual({row['Drive File ID'] for row in written}, {'drive-a', 'drive-b'})
        self.assertEqual({row['Digital Asset'] for row in written}, {'same.jpg'})
        self.assertIn('Probable duplicate', written[0]['Exception / Duplicate Note'])


class SummaryTests(unittest.TestCase):
    def _queue_rows(self):
        tasks = []
        for index in range(27):
            tasks.append({
                'Project': 'Tri Delta Chapters', 'Box': 'Chapters Box 34',
                'Task Status': 'Open', 'Action Needed': 'Archival Border Crop',
                'Digital Asset': f'crop-{index}.jpg',
            })
        tasks.append({
            'Project': 'Tri Delta Chapters', 'Box': 'Chapters Box 34',
            'Task Status': 'Open', 'Action Needed': 'Additional Imaging Required',
            'Digital Asset': 'missing-page.jpg',
        })
        return tasks

    def test_task_counts_come_from_the_production_queue(self):
        tasks = self._queue_rows()
        metrics = calculate_metrics({'Production Queue': tasks, 'Asset QC': [], 'Itemization': [], 'Metadata References': [], 'Ready for Metadata': []})
        self.assertEqual(metrics.crop_qc_tasks, 27)
        self.assertEqual(metrics.additional_imaging_tasks, 1)
        self.assertEqual(metrics.production_tasks, 28)

    def test_completing_the_same_box_twice_keeps_one_authoritative_summary(self):
        sheets = MemorySheets()
        sheets.write('register', 'Box Summary', {
            'Box': '', 'Box Status': 'In Progress', 'Source Assets': '0',
            'Notes': 'no source assets committed',
        }, ('Box', ''))
        sheets.write('register', 'Box Summary', {
            'Box': 'Chapters Box 34', 'Box Status': 'In Progress', 'Source Assets': '10',
            'Notes': 'batch row',
        }, ('Box', 'Chapters Box 34'))
        sheets.write('register', 'Continuation State', {
            'State Field': 'Processing Note', 'Current Value': 'In Progress. no source assets committed',
        }, ('State Field', 'Processing Note'))
        sheets.write('register', 'Continuation State', {
            'State Field': 'Open Intellectual Item', 'Current Value': 'None',
        }, ('State Field', 'Open Intellectual Item'))
        sheets.write('register', 'Project Register', {
            'Project': 'Tri Delta Chapters', 'Box': 'Chapters Box 34', 'Notes': 'HIT-1-B0001',
            'Crop/QC Tasks': '28', 'Additional Imaging Tasks': '0', 'Production Tasks': '28',
        }, ('Notes', 'HIT-1-B0001'))
        for task in self._queue_rows():
            sheets.write('register', 'Production Queue', task)
        queue = [{
            'Project': 'Tri Delta Chapters', 'Box': 'Chapters Box 34',
            'Source Group / Folder': 'F04', 'Batch Status': 'Committed - Validated',
            'Resulting Open Item': 'None',
        }]
        first = finalize_completed_box(sheets, 'register', 'Tri Delta Chapters', 'Chapters Box 34', queue)
        second = finalize_completed_box(sheets, 'register', 'Tri Delta Chapters', 'Chapters Box 34', queue)
        rows = sheets.rows('register', 'Box Summary')
        current = [row for row in rows if row.get('Box') == 'Chapters Box 34']
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]['Box Status'], 'Completed - Validated')
        self.assertEqual(current[0]['Production Tasks'], '28')
        self.assertNotIn('In Progress', current[0]['Box Status'])
        superseded = [row for row in rows if str(row.get('Box') or '').startswith('Superseded')]
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0]['Box Status'], '')
        note = sheets.field('register', 'Processing Note')
        self.assertIn('Completed - Validated', note)
        self.assertNotIn('no source assets committed', note.casefold())
        self.assertEqual(sheets.field('register', 'Open Intellectual Item'), 'None')
        register = sheets.rows('register', 'Project Register')[0]
        self.assertEqual(register['Crop/QC Tasks'], '28')
        self.assertEqual(register['Additional Imaging Tasks'], '0')
        self.assertEqual(register['Production Tasks'], '28')
        self.assertEqual(first['metrics']['Crop/QC Tasks'], second['metrics']['Crop/QC Tasks'])
        self.assertEqual(len(sheets.rows('register', 'Box Summary')), 2)


class FolderCloseReconciliationTests(unittest.TestCase):
    JOB = 'HIT-fe3b819451ab26c3'
    BOX = 'Chapters Box 31'
    INIT_NEXT = 'TriDelta_Year2_Chapters_Box19_F01_001.jpg'

    def _names(self, count, start):
        return [f'TriDelta_Year2_Chapters_Box31_F04_{start + index}.jpg' for index in range(count)]

    def _install(self, sheets, batches, tasks, qc_names):
        sheets.store[('index', 'HistoryIT Jobs')] = [{'Job ID': self.JOB, 'Spreadsheet ID': 'register'}]
        for batch in batches:
            sheets.write('register', 'Batch Queue', batch, ('Batch ID', batch['Batch ID']))
        for name in qc_names:
            sheets.write('register', 'Asset QC', {
                'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Source Group / Folder': 'F04',
                'Processing Unit / Source Folder': 'F04', 'Digital Asset': name,
            }, ('Digital Asset', name))
        for task in tasks:
            sheets.write('register', 'Production Queue', task, ('Task ID', task['Task ID']))
        for field, value in (
            ('Open Intellectual Item', 'None'),
            ('Unresolved Boundary Item', 'None'),
            ('Active Box', 'Chapters Box 19'),
            ('Current Completed Processing Unit', 'None - developer register initialized'),
            ('Next Authorized / Unprocessed Folder', self.INIT_NEXT),
            ('Source Processing Authorization', 'Not yet batched - developer register initialized'),
            ('Resume Instruction', 'Begin Chapters Box 19 at F01. Enumerate the source folder once.'),
        ):
            sheets.set_field('register', field, value)

    def _batch(self, batch_id, names, status='Committed - Validated', folder='F04'):
        return {
            'Job ID': self.JOB, 'Batch ID': batch_id, 'Project': 'Tri Delta Chapters', 'Box': self.BOX,
            'Source Group / Folder': folder, 'Batch Status': status, 'Resulting Open Item': 'None',
            'Start Filename': names[0], 'End Filename': names[-1], 'Planned Asset Count': str(len(names)),
            'Asset Staging Row Range': '', 'Record Staging Row Range': '',
            'Notes': json.dumps({'manifest': [{'name': name, 'id': name} for name in names]}),
        }

    def test_finished_folder_replaces_developer_continuation_and_repeat_is_stable(self):
        first_names = self._names(2, 1)
        second_names = self._names(2, 3)
        second_names.append('TriDelta_Year2_Chapters_Box31_F04_Photograph_016b.jpg')
        sheets = MemorySheets()
        self._install(
            sheets,
            [self._batch(f'{self.JOB}-B0001', first_names), self._batch(f'{self.JOB}-B0002', second_names)],
            [
                {'Task ID': 'T1', 'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Source Group / Folder': 'F04', 'Task Status': 'Open', 'Action Needed': 'Archival Border Crop', 'Digital Asset': first_names[0]},
                {'Task ID': 'T2', 'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Source Group / Folder': 'F04', 'Task Status': 'Open', 'Action Needed': 'Rotate', 'Digital Asset': second_names[-1]},
                {'Task ID': 'T3', 'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Source Group / Folder': 'F04', 'Task Status': 'Open', 'Action Needed': 'Archival Border Crop', 'Digital Asset': second_names[-1]},
            ],
            first_names + second_names,
        )
        sheets.write('register', 'Folder Processing Unit Summary', {
            'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Processing Unit / Source Folder': 'F04',
            'Source Group / Folder': 'F04', 'Processing Status': 'In Progress', 'Production Tasks': '1',
            'Notes': f'{self.JOB}-B0002',
        }, ('Processing Unit / Source Folder', 'F04'))
        sheets.write('register', 'Project Register', {
            'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Notes': f'{self.JOB}-B0001',
            'Source Assets': '2', 'Production Tasks': '1', 'Crop/QC Tasks': '1', 'Additional Imaging Tasks': '0',
        }, ('Notes', f'{self.JOB}-B0001'))
        sheets.write('register', 'Project Register', {
            'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Notes': f'{self.JOB}-B0002',
            'Source Assets': '3', 'Production Tasks': '2', 'Crop/QC Tasks': '2', 'Additional Imaging Tasks': '0',
        }, ('Notes', f'{self.JOB}-B0002'))
        sheets.write('register', 'Asset Staging', {'Batch ID': f'{self.JOB}-B0001', 'Digital Asset': first_names[0]}, ('Digital Asset', first_names[0]))
        sheets.write('register', 'Asset Staging', {'Batch ID': f'{self.JOB}-B0002', 'Digital Asset': second_names[0]}, ('Digital Asset', second_names[0]))
        sheets.write('register', 'Record Staging', {'Batch ID': f'{self.JOB}-B0001', 'Proposed Record ID': 'R1'}, ('Proposed Record ID', 'R1'))
        sheets.write('register', 'Record Staging', {'Batch ID': f'{self.JOB}-B0002', 'Proposed Record ID': 'R2'}, ('Proposed Record ID', 'R2'))
        orchestrator = Orchestrator(sheets, None, None, WorkflowSettings())
        orchestrator._close_folders('register', self.JOB)
        again = {field: sheets.field('register', field) for field in (
            'Next Authorized / Unprocessed Folder', 'Active Box', 'Current Completed Processing Unit',
        )}
        folder = sheets.rows('register', 'Folder Processing Unit Summary')[0]
        box = next(row for row in sheets.rows('register', 'Box Summary') if row.get('Box') == self.BOX)
        registers = {row['Notes']: row for row in sheets.rows('register', 'Project Register')}
        self.assertEqual(again['Next Authorized / Unprocessed Folder'], 'None - authorized source exhausted')
        self.assertNotIn(self.INIT_NEXT, again['Next Authorized / Unprocessed Folder'])
        self.assertEqual(again['Active Box'], self.BOX)
        self.assertIn('F04 (Completed - Validated)', again['Current Completed Processing Unit'])
        self.assertNotIn('developer register initialized', again['Current Completed Processing Unit'])
        self.assertEqual(folder['Processing Status'], 'Completed - Validated')
        self.assertEqual(folder['Enumerated New Source Count'], '5')
        self.assertEqual(folder['Production Tasks'], '3')
        self.assertEqual(box['Production Tasks'], '3')
        self.assertEqual(box['Source Assets'], '5')
        self.assertEqual(registers[f'{self.JOB}-B0001']['Production Tasks'], '1')
        self.assertEqual(registers[f'{self.JOB}-B0001']['Source Assets'], '2')
        self.assertEqual(registers[f'{self.JOB}-B0002']['Production Tasks'], '2')
        self.assertEqual(registers[f'{self.JOB}-B0002']['Source Assets'], '3')
        orchestrator._close_folders('register', self.JOB)
        self.assertEqual(sheets.field('register', 'Next Authorized / Unprocessed Folder'), again['Next Authorized / Unprocessed Folder'])
        self.assertEqual(sheets.field('register', 'Active Box'), again['Active Box'])
        self.assertEqual(sheets.field('register', 'Current Completed Processing Unit'), again['Current Completed Processing Unit'])
        self.assertEqual(sheets.rows('register', 'Folder Processing Unit Summary')[0]['Production Tasks'], '3')
        self.assertEqual(sheets.rows('register', 'Project Register')[0]['Production Tasks'], '1')
        second = reconcile_register(sheets, 'register', self.JOB, orchestrator._queue('register', self.JOB))
        self.assertEqual(second['wrote'], [])

    def test_remaining_authorized_folder_stays_next(self):
        done = self._names(1, 1)
        pending = ['TriDelta_Year2_Chapters_Box31_F05_001.jpg']
        sheets = MemorySheets()
        self._install(
            sheets,
            [
                self._batch(f'{self.JOB}-B0001', done),
                self._batch(f'{self.JOB}-B0002', pending, status='Authorized / Pending Inspection', folder='F05'),
            ],
            [],
            done,
        )
        sheets.write('register', 'Folder Processing Unit Summary', {
            'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Processing Unit / Source Folder': 'F04',
            'Source Group / Folder': 'F04', 'Processing Status': 'Completed - Validated', 'Notes': f'{self.JOB}-B0001',
        }, ('Processing Unit / Source Folder', 'F04'))
        sheets.write('register', 'Asset Staging', {'Batch ID': f'{self.JOB}-B0001', 'Digital Asset': done[0]}, ('Digital Asset', done[0]))
        sheets.write('register', 'Record Staging', {'Batch ID': f'{self.JOB}-B0001', 'Proposed Record ID': 'R1'}, ('Proposed Record ID', 'R1'))
        reconcile_register(sheets, 'register', self.JOB, sheets.rows('register', 'Batch Queue'))
        self.assertEqual(sheets.field('register', 'Next Authorized / Unprocessed Folder'), pending[0])
        self.assertEqual(sheets.field('register', 'Active Box'), self.BOX)
        self.assertNotIn('authorized source exhausted', sheets.field('register', 'Next Authorized / Unprocessed Folder'))

    def test_batch_metrics_stay_separate_from_folder_totals(self):
        shared = {'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Task Status': 'Open'}
        tables = {
            'Asset QC': [
                {'Digital Asset': 'one.jpg', 'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Action Needed': 'N/A'},
                {'Digital Asset': 'crop.jpg', 'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Action Needed': 'Archival Border Crop'},
                {'Digital Asset': '016b.jpg', 'Project': 'Tri Delta Chapters', 'Box': self.BOX, 'Action Needed': 'Rotate + Archival Border Crop'},
                {'Digital Asset': 'other.jpg', 'Project': 'Tri Delta Chapters', 'Box': 'Chapters Box 19', 'Action Needed': 'Archival Border Crop'},
            ],
            'Production Queue': [
                {**shared, 'Digital Asset': 'crop.jpg', 'Action Needed': 'Archival Border Crop'},
                {**shared, 'Digital Asset': '016b.jpg', 'Action Needed': 'Rotate'},
                {**shared, 'Digital Asset': '016b.jpg', 'Action Needed': 'Archival Border Crop'},
                {'Project': 'Tri Delta Chapters', 'Box': 'Chapters Box 19', 'Task Status': 'Open', 'Digital Asset': 'other.jpg', 'Action Needed': 'Archival Border Crop'},
            ],
            'Itemization': [], 'Metadata References': [], 'Ready for Metadata': [],
        }
        batch = scoped_metrics(tables, names={'016b.jpg'}, project='Tri Delta Chapters', box=self.BOX)
        folder = scoped_metrics(tables, names={'one.jpg', 'crop.jpg', '016b.jpg'}, project='Tri Delta Chapters', box=self.BOX)
        box = scoped_metrics(tables, project='Tri Delta Chapters', box=self.BOX)
        self.assertEqual(batch.production_tasks, 2)
        self.assertEqual(batch.source_assets, 1)
        self.assertEqual(folder.production_tasks, 3)
        self.assertEqual(box.production_tasks, 3)
        self.assertEqual(box.source_assets, 3)
        crops = 35
        compound = 1
        metadata = 3
        self.assertEqual(crops + compound + metadata, 39)
        self.assertEqual(crops + (compound * 2), 37)

    def test_saved_register_continuation_is_initialization_not_a_conflict(self):
        path = Path(__file__).resolve().parents[1] / 'logs' / 'HIT-fe3b819451ab26c3-final' / 'HIT-fe3b819451ab26c3-register.xlsx'
        if not path.is_file():
            return
        saved = _xlsx_sheets(path, ('Continuation State', 'Batch Queue', 'Folder Processing Unit Summary', 'Production Queue', 'Asset QC'))
        queue = []
        for row in saved['Batch Queue']:
            if row.get('Job ID') != self.JOB:
                continue
            copied = dict(row)
            copied['Asset Staging Row Range'] = ''
            copied['Record Staging Row Range'] = ''
            queue.append(copied)
        decisions = plan_reconciliation(
            queue,
            saved['Folder Processing Unit Summary'],
            [{'Batch ID': row['Batch ID'], 'Digital Asset': row.get('Start Filename')} for row in queue],
            [],
            saved['Continuation State'],
            [],
            [],
        )
        nxt = next(item for item in decisions if item['field'] == 'Next Authorized / Unprocessed Folder')
        active = next(item for item in decisions if item['field'] == 'Active Box')
        self.assertEqual(nxt['current'], self.INIT_NEXT)
        self.assertEqual(nxt['action'], 'write')
        self.assertEqual(nxt['desired'], 'None - authorized source exhausted')
        self.assertEqual(active['desired'], self.BOX)
        self.assertNotEqual(active['action'], 'conflict')
        tasks = [row for row in saved['Production Queue'] if row.get('Task Status') == 'Open' and row.get('Box') == self.BOX]
        turned = [row.get('Action Needed') for row in tasks if 'Photograph_016b' in row.get('Digital Asset', '')]
        self.assertEqual(len(tasks), 37)
        self.assertEqual(turned, ['Rotate', 'Archival Border Crop'])


def _xlsx_column(ref):
    column = ''
    row = ''
    for character in ref:
        if character.isalpha():
            column += character
        else:
            row += character
    number = 0
    for character in column:
        number = number * 26 + ord(character.upper()) - 64
    return number, int(row)


def _xlsx_sheets(path, titles):
    import xml.etree.ElementTree as ET
    import zipfile

    namespace = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    main = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    relation = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    with zipfile.ZipFile(path) as book:
        workbook = ET.fromstring(book.read('xl/workbook.xml'))
        relationships = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
        targets = {item.attrib['Id']: item.attrib['Target'] for item in relationships}
        strings = []
        shared = ET.fromstring(book.read('xl/sharedStrings.xml'))
        for item in shared.findall('m:si', namespace):
            strings.append(''.join(node.text or '' for node in item.iter(main + 't')))

        def rows_for(title):
            sheet = next(item for item in workbook.find('m:sheets', namespace) if item.attrib['name'] == title)
            target = targets[sheet.attrib[relation + 'id']]
            if not target.startswith('xl/'):
                target = 'xl/' + target.lstrip('/')
            root = ET.fromstring(book.read(target))
            grid = {}
            for cell in root.iter(main + 'c'):
                ref = cell.attrib.get('r')
                if not ref:
                    continue
                column, row = _xlsx_column(ref)
                value = cell.find('m:v', namespace)
                if value is None or value.text is None:
                    text = ''
                elif cell.attrib.get('t') == 's':
                    text = strings[int(value.text)]
                else:
                    text = value.text
                grid.setdefault(row, {})[column] = text
            header_row = next(row for row in sorted(grid) if 'Batch ID' in grid[row].values() or 'State Field' in grid[row].values() or 'Task ID' in grid[row].values() or 'Digital Asset' in grid[row].values() or 'Processing Status' in grid[row].values())
            headers = grid[header_row]
            parsed = []
            for row in sorted(grid):
                if row <= header_row:
                    continue
                item = {headers[column]: grid[row].get(column, '') for column in headers}
                if any(str(value).strip() for value in item.values()):
                    parsed.append(item)
            return parsed

        return {title: rows_for(title) for title in titles}


class CropControlTests(unittest.TestCase):
    def _run(self, name, action, note, template='Photographic Image', type_name='Photograph'):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset(name, 'item', action=action, note=note)],
            records=[record('item', template=template, type_name=type_name, note=note)],
        ))
        return output.assets[0]

    def test_approved_visual_controls_and_rotation(self):
        slight = self._run('border.jpg', 'Archival Border Crop', 'A slight archival border is acceptable even though portions are a little over 75 pixels.')
        self.assertEqual(slight.action_needed, 'No Adjustment')
        self.assertEqual(slight.queue_eligible, 'No')
        excess = self._run('wide.jpg', 'No Adjustment', 'Clearly excessive capture background surrounds the photograph.')
        self.assertEqual(excess.action_needed, 'Archival Border Crop')
        self.assertEqual(excess.required_crop_standard, 'Archival Border')
        unbound = self._run(
            'newsletter.jpg', 'No Adjustment',
            'Unbound newsletter page. Crop to the extant page edge.',
            template='Publication', type_name='Magazine',
        )
        self.assertEqual(unbound.action_needed, 'Publication Crop')
        self.assertEqual(unbound.required_crop_standard, 'Publication Page Edge')
        bound_output = apply_governing_constraints(InspectionOutput(
            assets=[asset('program.jpg', 'item', action='Publication Crop', note='Bound program. The binding cord must remain visible.')],
            records=[record('item', template='Publication', type_name='Program', note='Bound program. The binding cord must remain visible.')],
        ))
        self.assertEqual(bound_output.assets[0].action_needed, 'No Adjustment')
        self.assertIn('binding is preserved', bound_output.records[0].review_note)
        denied = self._run('denied.jpg', 'Archival Border Crop', 'The excessive capture background is absent. A slight archival border remains.')
        self.assertEqual(denied.action_needed, 'No Adjustment')
        turned = self._run('turned.jpg', 'Rotate', 'The print is 180 degrees from upright.')
        self.assertEqual(turned.action_needed, 'Rotate')
        self.assertEqual(turned.queue_eligible, 'Yes')

    def test_consecutive_report_sections_stay_one_packet(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[
                asset('section-a.jpg', 'a', note='Chapter term report, opening section.'),
                asset('section-b.jpg', 'b', note='Consecutive report section of the same chapter and same term. Internal section heading only.'),
            ],
            records=[
                record('a', note='Chapter term report, opening section.'),
                record('b', note='Consecutive report section of the same chapter and same term. Internal section heading only.'),
            ],
        ))
        self.assertEqual(len(output.records), 1)
        self.assertEqual({item.assigned_record_id for item in output.assets}, {output.records[0].proposed_record_id})

    def test_a_true_document_boundary_stays_separate(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[
                asset('one.jpg', 'a', note='Chapter term report.'),
                asset('two.jpg', 'b', note='True document boundary. Different physical document.'),
            ],
            records=[
                record('a', note='Chapter term report.'),
                record('b', note='True document boundary. Different physical document.'),
            ],
        ))
        self.assertEqual(len(output.records), 2)


class CrossFolderCloseTests(unittest.TestCase):
    def test_f02_closes_the_f01_publication_without_rewriting_it(self):
        prior = 'TDCB3-B35-F01-001'
        batch = {
            'Project': 'Tri Delta Chapters', 'Box': 'Chapters Box 35',
            'Source Group / Folder': 'F02', 'Job ID': 'HIT-35', 'Batch ID': 'HIT-35-B0002',
            'Notes': '{"manifest":[{"id":"drive-f02","name":"f02.jpg","folder_id":"f02","folder_name":"F02"}]}',
        }
        assets = [{
            'Digital Asset': 'f02.jpg', 'Assigned Record ID': 'new-f02',
            'Action Needed': 'No Adjustment', 'Production Queue Eligible': 'No',
            'Drive File ID': 'drive-f02', 'Source Folder ID': 'f02',
        }]
        records = [
            {
                'Proposed Record ID': 'new-f02', 'Record Kind': 'Normal Odyssey',
                'Record Operation': 'Create New', 'Template': 'Document', 'Type': 'Report',
                'Metadata Readiness': 'Ready', 'Boundary State After Record': 'Closed',
                'Production Task Count': '0',
            },
            {
                'Proposed Record ID': prior, 'Record Kind': 'Metadata Only',
                'Record Operation': 'Update Existing Open Record', 'Template': 'Metadata Only',
                'Type': 'Newspaper', 'Metadata Readiness': 'Metadata Reference',
                'Boundary State After Record': 'Open', 'Production Task Count': '0',
            },
        ]
        items = [{
            'Item ID': prior, 'Template': 'Publication', 'Type': 'Program',
            'Digital Assets': 'f01.jpg', 'Digital Asset Count': '1', 'Production Task Count': '0',
        }]
        assign_identity(batch, assets, records, items, [], [], prior)
        close = next(row for row in records if row['Proposed Record ID'] == prior)
        created = next(row for row in records if row['Record Operation'] == 'Create New')
        self.assertEqual(records[0]['Proposed Record ID'], prior)
        self.assertEqual(close['Record Kind'], 'Normal Odyssey')
        self.assertEqual(close['Template'], 'Publication')
        self.assertEqual(close['Type'], 'Program')
        self.assertEqual(close['Boundary State After Record'], 'Closed')
        self.assertEqual(created['Record Operation'], 'Create New')
        queue = {
            'Batch ID': 'HIT-35-B0002', 'Batch Status': 'Inspection Complete - Awaiting Commit',
            'Planned Asset Count': '1',
        }
        for row in records:
            row.update({
                'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-35', 'Batch ID': 'HIT-35-B0002',
                'Processing Unit / Source Folder': 'F02', 'Box': 'Chapters Box 35',
                'Source Group / Folder': 'F02', 'Inspection Complete': 'Yes',
                'Commit Status': 'Pending Commit', 'Review Note': 'F02 does not continue the open record.',
            })
        assets[0].update({
            'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-35', 'Batch ID': 'HIT-35-B0002',
            'Processing Unit / Source Folder': 'F02', 'Box': 'Chapters Box 35',
            'Source Group / Folder': 'F02', 'Inspection Complete': 'Yes', 'Commit Status': 'Pending Commit',
            'QC Status': 'Pass', 'Required Crop Standard': 'N/A', 'Expected Output Asset Count': '1',
            'QC Confidence': 'High',
        })
        plan = plan_commit(queue, assets, records, {
            'Asset QC': [], 'Itemization': items, 'Metadata References': [],
            'Production Queue': [], 'Ready for Metadata': [],
        }, '10/22/26')
        updated = plan.update_rows_by_key['Itemization'][0][2]
        self.assertEqual(updated['Template'], 'Publication')
        self.assertEqual(updated['Type'], 'Program')
        self.assertNotIn(prior, {row.get('Reference ID') for row in plan.append_rows.get('Metadata References', [])})

    def test_box37_f03_closes_the_unresolved_f02_item_before_opening_minutes(self):
        prior = 'TDCB3-B37-F02-016'
        batch = {
            'Project': 'Tri Delta Chapters', 'Box': 'Chapters Box 37',
            'Source Group / Folder': 'F03', 'Job ID': 'HIT-37', 'Batch ID': 'HIT-37-B0030',
        }
        assets = [{
            'Digital Asset': 'f03-minutes.jpg', 'Assigned Record ID': 'new-f03',
            'Action Needed': 'No Adjustment', 'Production Queue Eligible': 'No',
        }]
        records = [
            {
                'Proposed Record ID': 'new-f03', 'Record Kind': 'Normal Odyssey',
                'Record Operation': 'Create New', 'Template': 'Document', 'Type': 'Minutes',
                'Metadata Readiness': 'Review', 'Boundary State After Record': 'Open',
                'Production Task Count': '0',
                'Review Note': '1892-1893 minutes continue beyond this batch.',
            },
            {
                'Proposed Record ID': prior, 'Record Kind': 'Metadata Only',
                'Record Operation': 'Update Existing Open Record', 'Template': 'Metadata Only',
                'Type': 'Newspaper', 'Metadata Readiness': 'Metadata Reference',
                'Boundary State After Record': 'Open – Unresolved Beyond Current Batch',
                'Production Task Count': '0',
                'Review Note': 'F03 does not continue the incomplete 1979-1980 chapter history.',
            },
        ]
        items = [{
            'Item ID': prior, 'Template': 'Document', 'Type': 'Report',
            'Metadata Readiness': 'Review', 'Digital Assets': 'f02-history.jpg',
            'Digital Asset Count': '1', 'Production Task Count': '0',
        }]
        assign_identity(batch, assets, records, items, [], [], prior)
        close = next(row for row in records if row['Proposed Record ID'] == prior)
        opened = next(row for row in records if row['Record Operation'] == 'Create New')
        self.assertEqual(records[0]['Proposed Record ID'], prior)
        self.assertEqual(close['Record Kind'], 'Normal Odyssey')
        self.assertEqual(close['Template'], 'Document')
        self.assertEqual(close['Type'], 'Report')
        self.assertEqual(close['Metadata Readiness'], 'Review')
        self.assertEqual(close['Boundary State After Record'], 'Closed')
        self.assertTrue(opened['Boundary State After Record'].startswith('Open'))
        open_rows = [row for row in records if str(row['Boundary State After Record']).startswith('Open')]
        self.assertEqual(open_rows, [opened])
        for row in records:
            row.update({
                'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-37', 'Batch ID': 'HIT-37-B0030',
                'Processing Unit / Source Folder': 'F03', 'Box': 'Chapters Box 37',
                'Source Group / Folder': 'F03', 'Inspection Complete': 'Yes',
                'Commit Status': 'Pending Commit',
            })
        assets[0].update({
            'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-37', 'Batch ID': 'HIT-37-B0030',
            'Processing Unit / Source Folder': 'F03', 'Box': 'Chapters Box 37',
            'Source Group / Folder': 'F03', 'Inspection Complete': 'Yes', 'Commit Status': 'Pending Commit',
            'QC Status': 'Pass', 'Required Crop Standard': 'N/A', 'Expected Output Asset Count': '1',
            'QC Confidence': 'High', 'Assigned Record ID': opened['Proposed Record ID'],
        })
        plan = plan_commit(
            {'Batch ID': 'HIT-37-B0030', 'Batch Status': 'Inspection Complete - Awaiting Commit', 'Planned Asset Count': '1'},
            assets, records,
            {'Asset QC': [], 'Itemization': items, 'Metadata References': [], 'Production Queue': [], 'Ready for Metadata': []},
            '10/23/26',
        )
        self.assertEqual(plan.resulting_open_item, opened['Proposed Record ID'])
        self.assertNotEqual(plan.resulting_open_item, prior)


def _f04(name):
    return f'TriDelta_Year2_Chapters_Box31_{name}'


class Box31CropCalibrationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'project_references'
        self.f04 = resolve_reference(root, 'Tri Delta Chapters', 'Chapters Box 31', 'F04')
        self.box19 = resolve_reference(root, 'Tri Delta Chapters', 'Chapters Box 19', 'F01')

    def _pair(self, names, record_id, action, kind='Normal Odyssey', template='Document', type_name='Form', note=''):
        assets = [asset(name, record_id, action=action, note=note) for name in names]
        records = [record(record_id, template=template, type_name=type_name, kind=kind, note=note)]
        if kind == 'Metadata Only':
            records[0].metadata_readiness = 'Metadata Reference'
            records[0].template = 'Metadata Only'
            records[0].type = ''
        return assets, records

    def test_file_by_file_actions_are_required_only_for_this_folder(self):
        self.assertEqual(self.f04['version'], '2026-10-24.1')
        self.assertNotIn('filename_rules', self.box19)
        names = [
            _f04('F04_001.jpg'), _f04('F04_002.jpg'), _f04('F04_003.jpg'),
            _f04('F04_Photograph_008.jpg'), _f04('F04_Photograph_009a.jpg'),
            _f04('F04_Photograph_016b.jpg'), _f04('F04_Photograph_017.jpg'),
            _f04('F04_Photograph_019.jpg'), _f04('F04_Photograph_020.jpg'),
        ]
        assets = [
            asset(names[0], 'enclosure', action='N/A'),
            asset(names[1], 'questions', action='Archival Border Crop'),
            asset(names[2], 'questions', action='Archival Border Crop'),
            asset(names[3], 'photo-8', action='Archival Border Crop'),
            asset(names[4], 'photo-9', action='Archival Border Crop'),
            asset(names[5], 'turned', action='Rotate + Archival Border Crop', note='The print is 180 degrees from upright.'),
            asset(names[6], 'note', action='Archival Border Crop', note='Crop while preserving the attached note.'),
            asset(names[7], 'white', action='Archival Border Crop', note="Crop while preserving the photograph's white physical border."),
            asset(names[8], 'dark', action='Archival Border Crop', note="Preserve the photograph's own dark edge and remove only excess capture background."),
        ]
        records = [
            record('enclosure', kind='Metadata Only', template='Metadata Only', type_name='', readiness='Metadata Reference'),
            record('questions', template='Document', type_name='Form', readiness='Blocked - Cropping/QC'),
            record('photo-8', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC'),
            record('photo-9', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC'),
            record('turned', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC'),
            record('note', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC'),
            record('white', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC'),
            record('dark', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC'),
        ]
        self.assertEqual(reference_mismatches(assets, records, self.f04, names), [])
        passed = [asset(names[1], 'questions', action='No Adjustment'), asset(names[2], 'questions', action='No Adjustment')]
        problems = reference_mismatches(passed, [records[1]], self.f04, names[:3])
        self.assertTrue(any('F04_002.jpg' in problem and 'Archival Border Crop' in problem for problem in problems))
        self.assertEqual(reference_mismatches(passed, [records[1]], self.box19, names[:3]), [])

    def test_special_photographs_keep_context_and_rotation_is_a_separate_task(self):
        turned = asset(_f04('F04_Photograph_016b.jpg'), 'turned', action='Rotate', note='The print is 180 degrees from upright.')
        record_row = record('turned', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC')
        problems = reference_mismatches([turned], [record_row], self.f04, [turned.digital_asset])
        self.assertTrue(any('Rotate + Archival Border Crop' in problem for problem in problems))
        white = apply_governing_constraints(InspectionOutput(
            assets=[asset('border.jpg', 'item', action='Archival Border Crop', note="Crop while preserving the photograph's white physical border.")],
            records=[record('item', template='Photographic Image', type_name='Photograph', note="Crop while preserving the photograph's white physical border.")],
        ))
        self.assertEqual(white.assets[0].action_needed, 'Archival Border Crop')
        tasks = build_task_rows(
            [{
                'Project': 'Tri Delta Chapters', 'Processing Unit / Source Folder': 'F04',
                'Assigned Record ID': 'TDCB3-B31-F04-001', 'Digital Asset': _f04('F04_Photograph_016b.jpg'),
                'Box': 'Chapters Box 31', 'Source Group / Folder': 'F04',
                'Action Needed': 'Rotate + Archival Border Crop', 'Production Queue Eligible': 'Yes',
                'Production Task Scope': '', 'Expected Output Asset Count': '1', 'QC Confidence': 'High',
                'Production Task Note': 'Rotate 180 degrees, then crop.',
            }],
            {'TDCB3-B31-F04-001': {'Template': 'Photographic Image', 'Type': 'Photograph'}},
            [],
        )
        self.assertEqual([row['Action Needed'] for row in tasks], ['Rotate', 'Archival Border Crop'])

    def test_saved_batch2_diagnostic_replays_without_rewriting_rotate(self):
        """Replay the saved B0002 conflict. No model call and no filename rewrite."""
        saved_error = (
            'TriDelta_Year2_Chapters_Box31_F04_Photograph_016b.jpg Action Needed is Rotate; '
            'approved calibration example is Rotate + Archival Border Crop'
        )
        diagnostic = Path(__file__).resolve().parents[1] / 'logs' / (
            'HIT-fe3b819451ab26c3_HIT-fe3b819451ab26c3-B0002_attempt-1.json'
        )
        if diagnostic.is_file():
            events = json.loads(diagnostic.read_text(encoding='utf-8'))
            blocked = next(item for item in events if item.get('event') == 'batch_blocked')
            received = next(item for item in events if item.get('event') == 'response_received')
            self.assertEqual(received.get('status'), 'completed')
            self.assertIn(saved_error, blocked['error'])
            self.assertEqual(blocked['error_type'], 'ReferenceError')
        prompt = decision_instructions()
        self.assertIn('Rotate + Archival Border Crop', prompt)
        self.assertIn('Rotate first and Archival Border Crop second', prompt)
        self.assertIn('Leave a rotation-only image as Rotate', prompt)
        self.assertNotIn('Photograph_016b', prompt)
        self.assertNotIn('Box31', prompt)
        name = _f04('F04_Photograph_016b.jpg')
        rotate_only = asset(name, 'turned', action='Rotate', note='The print is 180 degrees from upright and has excess capture background.')
        record_row = record('turned', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC')
        problems = reference_mismatches([rotate_only], [record_row], self.f04, [name])
        self.assertTrue(any('Action Needed is Rotate' in problem and 'Rotate + Archival Border Crop' in problem for problem in problems))
        kept = apply_governing_constraints(InspectionOutput(assets=[rotate_only], records=[record_row]))
        self.assertEqual(kept.assets[0].action_needed, 'Rotate')
        compound = asset(name, 'turned', action='Rotate + Archival Border Crop', note='The print is 180 degrees from upright.')
        self.assertEqual(reference_mismatches([compound], [record_row], self.f04, [name]), [])
        accepted = apply_governing_constraints(InspectionOutput(assets=[compound], records=[record_row]))
        self.assertEqual(accepted.assets[0].action_needed, 'Rotate + Archival Border Crop')
        tasks = build_task_rows(
            [{
                'Project': 'Tri Delta Chapters', 'Processing Unit / Source Folder': 'F04',
                'Assigned Record ID': 'TDCB3-B31-F04-001', 'Digital Asset': name,
                'Box': 'Chapters Box 31', 'Source Group / Folder': 'F04',
                'Action Needed': accepted.assets[0].action_needed, 'Production Queue Eligible': 'Yes',
                'Production Task Scope': '', 'Expected Output Asset Count': '1', 'QC Confidence': 'High',
                'Production Task Note': 'Rotate 180 degrees, then crop.',
            }],
            {'TDCB3-B31-F04-001': {'Template': 'Photographic Image', 'Type': 'Photograph'}},
            [],
        )
        self.assertEqual([row['Action Needed'] for row in tasks], ['Rotate', 'Archival Border Crop'])

    def test_saved_batch2_note_phrases_pass_only_when_the_frame_records_them(self):
        """Replay the attempt-2 note conflict. No model call and no filename rewrite."""
        saved_error = (
            'TriDelta_Year2_Chapters_Box31_F04_Photograph_017c.jpg does not record the required calibration note attached note; '
            'TriDelta_Year2_Chapters_Box31_F04_Photograph_019b.jpg does not record the required calibration note white physical border; '
            'TriDelta_Year2_Chapters_Box31_F04_Photograph_020b.jpg does not record the required calibration note dark edge'
        )
        diagnostic = Path(__file__).resolve().parents[1] / 'logs' / 'HIT-fe3b819451ab26c3-b0002-retry' / (
            'HIT-fe3b819451ab26c3_HIT-fe3b819451ab26c3-B0002_attempt-2.json'
        )
        if diagnostic.is_file():
            events = json.loads(diagnostic.read_text(encoding='utf-8'))
            blocked = next(item for item in events if item.get('event') == 'batch_blocked')
            received = next(item for item in events if item.get('event') == 'response_received')
            validated = next(item for item in events if item.get('event') == 'inspection_validated')
            self.assertEqual(received.get('status'), 'completed')
            self.assertTrue(validated.get('success'))
            self.assertIn(saved_error, blocked['error'])
            self.assertNotIn('Photograph_016b', blocked['error'])
            self.assertEqual(blocked['error_type'], 'ReferenceError')
        statement = self.f04['statement']
        self.assertIn('Photograph_017c', statement)
        self.assertIn('exact phrase "attached note"', statement)
        self.assertIn('Photograph_019b', statement)
        self.assertIn('exact phrase "white physical border"', statement)
        self.assertIn('Photograph_020b', statement)
        self.assertIn('exact phrase "dark edge"', statement)
        self.assertIn('Do not invent those phrases from the filename', statement)
        self.assertIn('omits its required phrase stays blocked', statement)
        prompt = decision_instructions()
        self.assertIn('write its exact phrase in the QC Note or Production Task Note', prompt)
        self.assertIn('A missing required phrase stays blocked', prompt)
        self.assertNotIn('Photograph_017c', prompt)
        self.assertNotIn('Photograph_019b', prompt)
        self.assertNotIn('Photograph_020b', prompt)
        self.assertNotIn('Box31', prompt)
        names = {
            '016b': _f04('F04_Photograph_016b.jpg'),
            '017c': _f04('F04_Photograph_017c.jpg'),
            '019b': _f04('F04_Photograph_019b.jpg'),
            '020b': _f04('F04_Photograph_020b.jpg'),
        }
        complete = [
            asset(names['016b'], 'turned', action='Rotate + Archival Border Crop', note='Rotate 180 degrees, then crop the excess capture background.'),
            asset(names['017c'], 'note', action='Archival Border Crop', note='Preserve the attached note while cropping excess capture background.'),
            asset(names['019b'], 'white', action='Archival Border Crop', note="Preserve the photograph's white physical border."),
            asset(names['020b'], 'dark', action='Archival Border Crop', note="Preserve the photograph's own dark edge and remove excess capture background."),
        ]
        records = [
            record('turned', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC'),
            record('note', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC'),
            record('white', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC'),
            record('dark', template='Photographic Image', type_name='Photograph', readiness='Blocked - Cropping/QC'),
        ]
        self.assertEqual(reference_mismatches(complete, records, self.f04, list(names.values())), [])
        missing = asset(names['017c'], 'note', action='Archival Border Crop', note='A handwritten caption is visible on this frame.')
        missing_white = asset(names['019b'], 'white', action='Archival Border Crop', note='Keep the pale margin around the print.')
        missing_dark = asset(names['020b'], 'dark', action='Archival Border Crop', note='Keep the print border and remove the capture background.')
        held = apply_governing_constraints(InspectionOutput(
            assets=[missing, missing_white, missing_dark],
            records=[records[1], records[2], records[3]],
        ))
        self.assertEqual(held.assets[0].qc_note, missing.qc_note)
        self.assertEqual(held.assets[1].qc_note, missing_white.qc_note)
        self.assertEqual(held.assets[2].qc_note, missing_dark.qc_note)
        self.assertEqual(held.assets[0].action_needed, 'Archival Border Crop')
        problems = reference_mismatches(held.assets, held.records, self.f04, [item.digital_asset for item in held.assets])
        self.assertTrue(any('017c' in problem and 'attached note' in problem for problem in problems))
        self.assertTrue(any('019b' in problem and 'white physical border' in problem for problem in problems))
        self.assertTrue(any('020b' in problem and 'dark edge' in problem for problem in problems))

    def test_metadata_sheet_is_not_a_crop_and_a_questionnaire_stays_one_record(self):
        enclosure = asset(_f04('F04_001.jpg'), 'enclosure', action='Archival Border Crop')
        meta = record('enclosure', kind='Metadata Only', template='Metadata Only', type_name='', readiness='Metadata Reference')
        problems = reference_mismatches([enclosure], [meta], self.f04, [enclosure.digital_asset])
        self.assertTrue(any('no crop' in problem for problem in problems))
        split = [
            asset(_f04('F04_002.jpg'), 'one', action='Archival Border Crop'),
            asset(_f04('F04_003.jpg'), 'two', action='Archival Border Crop'),
        ]
        records = [
            record('one', template='Document', type_name='Form', readiness='Blocked - Cropping/QC'),
            record('two', template='Document', type_name='Form', readiness='Blocked - Cropping/QC'),
        ]
        problems = reference_mismatches(split, records, self.f04, [item.digital_asset for item in split])
        self.assertTrue(any('f04-002-003' in problem for problem in problems))
