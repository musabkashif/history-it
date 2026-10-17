"""Mocked orchestration reliability tests. No live Drive, Sheets, or OpenAI calls."""
from __future__ import annotations

import json
import threading
import time
import unittest
from datetime import date

from app.box_folders import select_authorized_sources
from app.commit import CommitService
from app.models import ASSET_HEADERS, RECORD_HEADERS, AssetRow, InspectionOutput, JobCreate, RecordRow, Source
from app.orchestrator import Orchestrator
from app.register_state import box_status, source_scope_closed
from client_reference.commit_engine import plan_commit
from tests.test_workflow import CapturingInspector, MemorySheets, Settings, install_job, manifest, queue_row


def filled(headers, **values):
    row = {header: '' for header in headers}
    row.update(values)
    return row


def closed_output(job_id, batch_id, folder, filenames, record_id='REC-001'):
    assets = []
    for index, filename in enumerate(filenames, 1):
        assets.append(AssetRow.model_validate(filled(
            ASSET_HEADERS,
            **{
                'Project': 'Tri Delta Chapters',
                'Job ID': job_id,
                'Batch ID': batch_id,
                'Processing Unit / Source Folder': folder,
                'Asset Sequence': str(index),
                'Digital Asset': filename,
                'Box': 'Chapters Box 19',
                'Source Group / Folder': folder,
                'Assigned Record ID': record_id,
                'Asset Role': 'Page',
                'Required Crop Standard': 'N/A',
                'QC Status': 'Pass',
                'Action Needed': 'No Adjustment',
                'Expected Output Asset Count': '1',
                'QC Confidence': 'High',
                'QC Note': '',
                'Odyssey Ingest / Visibility Note': '',
                'Production Queue Eligible': 'No',
                'Production Task Scope': '',
                'Production Task Note': '',
                'Exception / Duplicate Note': '',
                'Inspection Complete': 'Yes',
                'Commit Status': 'Pending Commit',
                'Commit Note': '',
            },
        )))
    listing = '; '.join(filenames)
    records = [RecordRow.model_validate(filled(
        RECORD_HEADERS,
        **{
            'Project': 'Tri Delta Chapters',
            'Job ID': job_id,
            'Batch ID': batch_id,
            'Processing Unit / Source Folder': folder,
            'Record Sequence': '1',
            'Proposed Record ID': record_id,
            'Record Kind': 'Normal Odyssey',
            'Record Operation': 'Create New',
            'Box': 'Chapters Box 19',
            'Source Group / Folder': folder,
            'Parent Scrapbook': '',
            'Parent Scrapbook Page': '',
            'New Batch Assets': listing,
            'Existing Committed Assets': '',
            'Full Record Assets After Commit': listing,
            'Digital Asset Count': str(len(filenames)),
            'Logical Component Count': '1',
            'Template': 'Document',
            'Type': 'Report',
            'Reference Role': '',
            'Further Detail': '',
            'Required Crop Standard': 'N/A',
            'Record QC Status': 'Pass',
            'Record Action Needed': 'No Adjustment',
            'Metadata Readiness': 'Ready',
            'Confidence': 'High',
            'Review Note': '',
            'Production Task Count': '0',
            'Odyssey Ingest / Visibility Note': '',
            'Notes': '',
            'Boundary State After Record': 'Closed',
            'Inspection Complete': 'Yes',
            'Commit Status': 'Pending Commit',
            'Commit Note': '',
        },
    ))]
    return InspectionOutput(assets=assets, records=records)


class ScriptedBoxInspector(CapturingInspector):
    def __init__(self):
        super().__init__()
        self.outputs = {}

    def inspect(self, job, batch_id, sources, context, rules):
        self.calls.append({
            'batch_id': batch_id,
            'context': context,
            'sources': [source.name for source in sources],
            'folder': context.get('folder_code'),
            'model_inputs': {
                'prior_open_item': context.get('prior_open_item'),
                'filenames': [source.name for source in sources],
                'folder_code': context.get('folder_code'),
            },
        })
        if self.error:
            raise self.error
        key = batch_id
        output = self.outputs.get(key) or closed_output(
            job, batch_id, context.get('folder_code') or 'F01',
            [source.name for source in sources],
            record_id=f'REC-{len(self.calls):03d}',
        )
        return output, {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1, 'elapsed_seconds': 0.1, 'response_id': f'resp-{len(self.calls)}'}


def source(folder_id, name, path):
    return Source(
        id=f'{folder_id}-{name}', name=name, folder_id=folder_id,
        folder_name=path.split('/')[-2], path=path, mime_type='image/jpeg',
    )


class MultiFolderOrchestrationTests(unittest.TestCase):
    def test_one_start_completes_multi_folder_multi_batch_box(self):
        sheets = MemorySheets()
        inspector = ScriptedBoxInspector()

        class Drive:
            def enumerate(self, root):
                items = []
                for folder in ('F01', 'F02'):
                    for index in (1, 2):
                        name = f'{folder}_{index}.jpg'
                        items.append(source(folder.lower(), name, f'Box/{folder}/{name}'))
                return select_authorized_sources(items)

        orch = Orchestrator(sheets, Drive(), inspector, Settings())
        state = orch.create(JobCreate(
            project='Tri Delta Chapters', source_folder_id='box', spreadsheet_id='register',
            batch_size=1, box='Chapters Box 19',
        ))
        job_id = state['job_id']
        self.assertEqual(state['discovered_folders'], ['F01', 'F02'])
        self.assertEqual(state['batches'], 4)
        orch.run(job_id)
        rows = sheets.rows('register', 'Batch Queue')
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row['Batch Status'] == 'Committed - Validated' for row in rows))
        self.assertTrue(all(row['Validation Status'] == 'PASS' for row in rows))
        self.assertEqual(len(inspector.calls), 4)
        self.assertEqual([call['folder'] for call in inspector.calls], ['F01', 'F01', 'F02', 'F02'])
        final = orch.state(job_id)
        self.assertTrue(final['authorized_scope_complete'])
        self.assertEqual(final['status'], 'Completed - Validated')
        self.assertFalse(final['running'])
        for row in sheets.rows('register', 'Asset Staging'):
            self.assertEqual(row['Commit Status'], 'Committed - Validated')
        for row in sheets.rows('register', 'Record Staging'):
            self.assertEqual(row['Commit Status'], 'Committed - Validated')

    def test_more_than_six_folders_natural_order(self):
        sheets = MemorySheets()
        inspector = ScriptedBoxInspector()

        class Drive:
            def enumerate(self, root):
                items = []
                for number in (10, 2, 1, 7, 3, 4, 5, 6):
                    folder = f'F{number:02d}'
                    items.append(source(folder.lower(), f'{folder}.jpg', f'Box/{folder}/{folder}.jpg'))
                items.append(source('del', 'skip.jpg', 'Box/Delhivery/skip.jpg'))
                return select_authorized_sources(items)

        orch = Orchestrator(sheets, Drive(), inspector, Settings())
        state = orch.create(JobCreate(
            project='Tri Delta Chapters', source_folder_id='box', spreadsheet_id='register',
            batch_size=10, box='Chapters Box 19',
        ))
        self.assertEqual(state['discovered_folders'], ['F01', 'F02', 'F03', 'F04', 'F05', 'F06', 'F07', 'F10'])
        orch.run(state['job_id'])
        folders = [row['Source Group / Folder'] for row in sheets.rows('register', 'Batch Queue')]
        self.assertEqual(folders, ['F01', 'F02', 'F03', 'F04', 'F05', 'F06', 'F07', 'F10'])
        self.assertEqual(len(inspector.calls), 8)
        self.assertTrue(all(row['Batch Status'] == 'Committed - Validated' for row in sheets.rows('register', 'Batch Queue')))

    def test_concurrent_start_admits_only_one_runner(self):
        sheets = MemorySheets()
        gate = threading.Event()
        started = threading.Event()

        class SlowInspector(ScriptedBoxInspector):
            def inspect(self, job, batch_id, sources, context, rules):
                started.set()
                gate.wait(2)
                return super().inspect(job, batch_id, sources, context, rules)

        class Drive:
            def enumerate(self, root):
                return [Source(id='1', name='a.jpg', folder_id='f01', folder_name='F01', path='/a.jpg', mime_type='image/jpeg')]

        orch = Orchestrator(sheets, Drive(), SlowInspector(), Settings())
        job_id = orch.create(JobCreate(
            project='Tri Delta Chapters', source_folder_id='f01', spreadsheet_id='register',
            batch_size=10, box='Chapters Box 19',
        ))['job_id']
        first = orch.start(job_id)
        self.assertTrue(first['running'])
        self.assertFalse(first.get('already_running'))
        started.wait(2)
        second = orch.start(job_id)
        self.assertTrue(second['already_running'])
        self.assertTrue(second['running'])
        gate.set()
        thread = orch.threads[job_id]
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(orch.inspector.calls), 1)

    def test_runner_advances_without_frontend_polling(self):
        sheets = MemorySheets()
        inspector = ScriptedBoxInspector()

        class Drive:
            def enumerate(self, root):
                return select_authorized_sources([
                    source('f01', 'a.jpg', 'Box/F01/a.jpg'),
                    source('f02', 'b.jpg', 'Box/F02/b.jpg'),
                ])

        orch = Orchestrator(sheets, Drive(), inspector, Settings())
        job_id = orch.create(JobCreate(
            project='Tri Delta Chapters', source_folder_id='box', spreadsheet_id='register',
            batch_size=10, box='Chapters Box 19',
        ))['job_id']
        # Direct backend run — no HTTP client, no polling loop.
        orch.run(job_id)
        self.assertEqual(len(inspector.calls), 2)
        self.assertTrue(all(row['Batch Status'] == 'Committed - Validated' for row in sheets.rows('register', 'Batch Queue')))

    def test_pending_validation_recovery_does_not_repeat_inspection(self):
        sheets = MemorySheets()
        asset = {
            'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-1', 'Batch ID': 'HIT-1-B0001',
            'Processing Unit / Source Folder': 'F01', 'Digital Asset': 'page.jpg', 'Box': 'Chapters Box 19',
            'Source Group / Folder': 'F01', 'Assigned Record ID': 'TDC-B19-F01-001', 'Asset Role': 'Page',
            'Required Crop Standard': 'N/A', 'QC Status': 'Pass', 'Action Needed': 'No Adjustment',
            'Expected Output Asset Count': '1', 'QC Confidence': 'High', 'Production Queue Eligible': 'No',
            'Inspection Complete': 'Yes', 'Commit Status': 'Pending Commit',
        }
        record = {
            'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-1', 'Batch ID': 'HIT-1-B0001',
            'Processing Unit / Source Folder': 'F01', 'Proposed Record ID': 'TDC-B19-F01-001',
            'Record Kind': 'Normal Odyssey', 'Record Operation': 'Create New', 'Box': 'Chapters Box 19',
            'Source Group / Folder': 'F01', 'New Batch Assets': 'page.jpg', 'Existing Committed Assets': '',
            'Full Record Assets After Commit': 'page.jpg', 'Digital Asset Count': '1', 'Logical Component Count': '1',
            'Template': 'Document', 'Type': 'Report', 'Metadata Readiness': 'Ready', 'Confidence': 'High',
            'Production Task Count': '0', 'Boundary State After Record': 'Closed',
            'Inspection Complete': 'Yes', 'Commit Status': 'Pending Commit',
        }
        row = queue_row(names=['page.jpg'], **{'Batch Status': 'Inspection Complete - Awaiting Commit'})
        plan = plan_commit(row, [asset], [record], {
            'Asset QC': [], 'Itemization': [], 'Metadata References': [], 'Ready for Metadata': [], 'Production Queue': [],
        }, date.today().strftime('%m/%d/%y'))
        for sheet, rows in plan.append_rows.items():
            for item in rows:
                sheets.write('register', sheet, item)
        row['Batch Status'] = 'Commit Written - Pending Validation'
        install_job(sheets, [row])
        sheets.store[('register', 'Asset Staging')] = [asset]
        sheets.store[('register', 'Record Staging')] = [record]
        inspector = CapturingInspector(error=AssertionError('must not inspect'))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        self.assertEqual(inspector.calls, [])
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Validation Status'], 'PASS')
        self.assertEqual(sheets.rows('register', 'Asset Staging')[0]['Commit Status'], 'Committed - Validated')
        self.assertEqual(sheets.rows('register', 'Record Staging')[0]['Commit Status'], 'Committed - Validated')

    def test_pause_resume_and_restart_preserve_completed_work(self):
        sheets = MemorySheets()
        inspector = ScriptedBoxInspector()

        class Drive:
            def enumerate(self, root):
                return select_authorized_sources([
                    source('f01', 'a.jpg', 'Box/F01/a.jpg'),
                    source('f02', 'b.jpg', 'Box/F02/b.jpg'),
                    source('f03', 'c.jpg', 'Box/F03/c.jpg'),
                ])

        orch = Orchestrator(sheets, Drive(), inspector, Settings())
        job_id = orch.create(JobCreate(
            project='Tri Delta Chapters', source_folder_id='box', spreadsheet_id='register',
            batch_size=10, box='Chapters Box 19',
        ))['job_id']
        # Process first batch only by pausing the rest before start.
        rows = sheets.rows('register', 'Batch Queue')
        for row in rows[1:]:
            sheets.write('register', 'Batch Queue', {'Batch Status': 'Paused'}, ('Batch ID', row['Batch ID']))
        orch.run(job_id)
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')
        self.assertEqual(len(inspector.calls), 1)
        orch.pause(job_id)
        before_calls = len(inspector.calls)
        orch.run(job_id)
        self.assertEqual(len(inspector.calls), before_calls)
        orch.resume(job_id)
        thread = orch.threads[job_id]
        thread.join(10)
        self.assertTrue(all(
            row['Batch Status'] == 'Committed - Validated'
            for row in sheets.rows('register', 'Batch Queue')
        ))
        self.assertEqual(len(inspector.calls), 3)
        # Restart recovery: validated batches are not reinspected.
        restart_inspector = ScriptedBoxInspector()
        restart = Orchestrator(sheets, Drive(), restart_inspector, Settings())
        restart.run(job_id)
        self.assertEqual(restart_inspector.calls, [])

    def test_blockers_halt_but_crop_tasks_do_not(self):
        sheets = MemorySheets()
        row = queue_row(names=['page.jpg'], **{'Batch Status': 'Blocked - Human Review', 'Error / Retry Note': 'Validation failed: 08'})
        later = queue_row(batch_id='HIT-1-B0002', names=['next.jpg'], **{'Source Group / Folder': 'F02'})
        install_job(sheets, [row, later])
        inspector = CapturingInspector(error=AssertionError('blocked job must stop'))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        self.assertEqual(inspector.calls, [])
        self.assertEqual(sheets.rows('register', 'Batch Queue')[1]['Batch Status'], 'Authorized / Pending Inspection')

        # Cropping/QC production tasks on itemization do not set Batch Status blocked.
        crop_rows = [
            {'Source Group / Folder': 'F01', 'Batch Status': 'Committed - Validated'},
            {'Source Group / Folder': 'F02', 'Batch Status': 'Committed - Validated'},
        ]
        items = [{'Item ID': 'X', 'Metadata Readiness': 'Blocked - Cropping/QC', 'Template': 'Document', 'Type': 'Report', 'Review Note': ''}]
        self.assertEqual(box_status(crop_rows, ['F01', 'F02'], 'None', items), 'Completed - Validated')
        self.assertTrue(source_scope_closed(crop_rows, ['F01', 'F02'], 'None'))

    def test_no_final_pass_before_readback_and_no_whole_box_success_while_work_remains(self):
        sheets = MemorySheets()
        asset = {
            'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-1', 'Batch ID': 'HIT-1-B0001',
            'Processing Unit / Source Folder': 'F01', 'Digital Asset': 'page.jpg', 'Box': 'Chapters Box 19',
            'Source Group / Folder': 'F01', 'Assigned Record ID': 'TDC-B19-F01-001', 'Asset Role': 'Page',
            'Required Crop Standard': 'N/A', 'QC Status': 'Pass', 'Action Needed': 'No Adjustment',
            'Expected Output Asset Count': '1', 'QC Confidence': 'High', 'Production Queue Eligible': 'No',
            'Inspection Complete': 'Yes', 'Commit Status': 'Pending Commit',
        }
        record = {
            'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-1', 'Batch ID': 'HIT-1-B0001',
            'Processing Unit / Source Folder': 'F01', 'Proposed Record ID': 'TDC-B19-F01-001',
            'Record Kind': 'Normal Odyssey', 'Record Operation': 'Create New', 'Box': 'Chapters Box 19',
            'Source Group / Folder': 'F01', 'New Batch Assets': 'page.jpg', 'Existing Committed Assets': '',
            'Full Record Assets After Commit': 'page.jpg', 'Digital Asset Count': '1', 'Logical Component Count': '1',
            'Template': 'Document', 'Type': 'Report', 'Metadata Readiness': 'Ready', 'Confidence': 'High',
            'Production Task Count': '0', 'Boundary State After Record': 'Closed',
            'Inspection Complete': 'Yes', 'Commit Status': 'Pending Commit',
        }
        first = queue_row(names=['page.jpg'], **{
            'Batch Status': 'Commit Written - Pending Validation',
            'Validation Status': 'Not Run',
        })
        second = queue_row(batch_id='HIT-1-B0002', names=['next.jpg'], **{'Source Group / Folder': 'F02'})
        install_job(sheets, [first, second])
        sheets.store[('register', 'Asset Staging')] = [asset]
        sheets.store[('register', 'Record Staging')] = [record]
        state = Orchestrator(sheets, None, CapturingInspector(), Settings()).state('HIT-1')
        self.assertFalse(state['authorized_scope_complete'])
        self.assertNotEqual(state['status'], 'Completed - Validated')
        self.assertNotEqual(state['status'], 'Committed - Validated')
        self.assertIn(state['phase'], ('Commit Written - Pending Validation', 'Authorized / Pending Inspection'))

    def test_staging_status_repair_after_validated_crash(self):
        sheets = MemorySheets()
        asset = {
            'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-1', 'Batch ID': 'HIT-1-B0001',
            'Processing Unit / Source Folder': 'F01', 'Digital Asset': 'page.jpg', 'Box': 'Chapters Box 19',
            'Source Group / Folder': 'F01', 'Assigned Record ID': 'TDC-B19-F01-001', 'Asset Role': 'Page',
            'Required Crop Standard': 'N/A', 'QC Status': 'Pass', 'Action Needed': 'No Adjustment',
            'Expected Output Asset Count': '1', 'QC Confidence': 'High', 'Production Queue Eligible': 'No',
            'Inspection Complete': 'Yes', 'Commit Status': 'Pending Commit',
        }
        record = {
            'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-1', 'Batch ID': 'HIT-1-B0001',
            'Processing Unit / Source Folder': 'F01', 'Proposed Record ID': 'TDC-B19-F01-001',
            'Record Kind': 'Normal Odyssey', 'Record Operation': 'Create New', 'Box': 'Chapters Box 19',
            'Source Group / Folder': 'F01', 'New Batch Assets': 'page.jpg', 'Existing Committed Assets': '',
            'Full Record Assets After Commit': 'page.jpg', 'Digital Asset Count': '1', 'Logical Component Count': '1',
            'Template': 'Document', 'Type': 'Report', 'Metadata Readiness': 'Ready', 'Confidence': 'High',
            'Production Task Count': '0', 'Boundary State After Record': 'Closed',
            'Inspection Complete': 'Yes', 'Commit Status': 'Pending Commit',
        }
        row = queue_row(names=['page.jpg'], **{
            'Batch Status': 'Committed - Validated',
            'Validation Status': 'PASS',
            'Resulting Open Item': 'None',
        })
        install_job(sheets, [row])
        sheets.store[('register', 'Asset Staging')] = [asset]
        sheets.store[('register', 'Record Staging')] = [record]
        sheets.set_field('register', 'Open Intellectual Item', 'None')
        CommitService(sheets).validate('register', row, already=True)
        self.assertEqual(sheets.rows('register', 'Asset Staging')[0]['Commit Status'], 'Committed - Validated')
        self.assertEqual(sheets.rows('register', 'Record Staging')[0]['Commit Status'], 'Committed - Validated')
        # Idempotent second repair.
        CommitService(sheets).validate('register', row, already=True)
        self.assertEqual(sheets.rows('register', 'Asset Staging')[0]['Commit Status'], 'Committed - Validated')

    def test_individual_folder_jobs_remain_compatible(self):
        sheets = MemorySheets()
        inspector = ScriptedBoxInspector()

        class Drive:
            def enumerate(self, root):
                self.root = root
                return [Source(id='1', name='only.jpg', folder_id='f01', folder_name='F01', path='/only.jpg', mime_type='image/jpeg')]

        drive = Drive()
        orch = Orchestrator(sheets, drive, inspector, Settings())
        state = orch.create(JobCreate(
            project='Tri Delta Chapters', source_folder_id='f01-only', spreadsheet_id='register',
            batch_size=10, box='Chapters Box 19',
        ))
        self.assertEqual(drive.root, 'f01-only')
        self.assertEqual(state['batches'], 1)
        self.assertEqual(state['discovered_folders'], ['F01'])
        orch.run(state['job_id'])
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')

    def test_inspection_payload_contract_unchanged_for_identical_batch(self):
        sheets = MemorySheets()
        inspector = ScriptedBoxInspector()
        row = queue_row(names=['page.jpg'])
        install_job(sheets, [row])
        orch = Orchestrator(sheets, None, inspector, Settings())
        sources, context, rules, reference, prior = orch._inspection_inputs('register', row)
        fingerprint = {
            'filenames': [source.name for source in sources],
            'prior': prior,
            'folder': context['folder_code'],
            'attempt_key': 'inspection_attempt' in context,
            'has_run_context': 'run_context' in context,
            'has_manifest': 'manifest' in context,
            'reference_type': type(reference).__name__,
            'rules_type': type(rules).__name__,
        }
        self.assertEqual(fingerprint['filenames'], ['page.jpg'])
        self.assertEqual(fingerprint['prior'], 'None')
        self.assertEqual(fingerprint['folder'], 'F01')
        self.assertTrue(fingerprint['attempt_key'])
        self.assertTrue(fingerprint['has_run_context'])
        self.assertTrue(fingerprint['has_manifest'])
        # Model settings remain on Settings / Inspector — orchestration must not alter them.
        self.assertEqual(Settings.openai_model, 'gpt-test')


class StartApiTests(unittest.TestCase):
    def test_start_endpoint_returns_running_state_synchronously(self):
        import app.main as main
        from fastapi.testclient import TestClient

        sheets = MemorySheets()
        gate = threading.Event()
        started = threading.Event()

        class SlowInspector(ScriptedBoxInspector):
            def inspect(self, job, batch_id, sources, context, rules):
                started.set()
                gate.wait(2)
                return super().inspect(job, batch_id, sources, context, rules)

        class Drive:
            def enumerate(self, root):
                return [Source(id='1', name='a.jpg', folder_id='f01', folder_name='F01', path='/a.jpg', mime_type='image/jpeg')]

        orch = Orchestrator(sheets, Drive(), SlowInspector(), Settings())
        original_live, original_guard = main.live, main.guard
        try:
            main.live = lambda: orch
            main.guard = lambda *args, **kwargs: None
            with TestClient(main.app) as client:
                created = client.post('/api/jobs', json={
                    'project': 'Tri Delta Chapters', 'source_folder_id': 'f01', 'spreadsheet_id': 'register',
                    'batch_size': 10, 'box': 'Chapters Box 19',
                })
                self.assertEqual(created.status_code, 200, created.text)
                job_id = created.json()['job_id']
                response = client.post(f'/api/jobs/{job_id}/start')
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertTrue(body['running'])
                started.wait(2)
                again = client.post(f'/api/jobs/{job_id}/start')
                self.assertTrue(again.json()['already_running'])
        finally:
            gate.set()
            main.live, main.guard = original_live, original_guard
            thread = orch.threads.get(job_id)
            if thread:
                thread.join(5)


if __name__ == '__main__':
    unittest.main()
