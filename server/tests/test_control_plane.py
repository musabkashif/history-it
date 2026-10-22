"""MongoDB control plane. Uses the in-memory store, never Atlas."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import unittest
from fastapi.testclient import TestClient

from app.control_plane.catalog import KNOWN_STAGING_JOBS
from app.control_plane.config import (
    CONFIGURED_FLAGS_OFF,
    INVALID,
    NOT_CONFIGURED,
    PRODUCTION_REFUSED,
    announce,
    redact,
    refuse_atlas_during_tests,
    startup_message,
)
from app.control_plane.errors import ControlPlaneUnavailable, LeaseHeld, NotAllowed, WorkspaceIsolationError
from app.control_plane.importing import plan_from_extract, read_register
from app.control_plane.indexes import INDEX_SPECS
from app.control_plane.memory import MemoryStore
from app.control_plane.models import JobSource
from app.control_plane.mongo import MongoStore
from app.control_plane import runtime
from app.control_plane.service import ControlService
from app.control_plane.shadow import guarded_shadow, preserve_sheets_result
from app import main

ROOT = Path(__file__).resolve().parents[2]
F04 = 'HIT-ea97b58c8e4a8c58'
B1 = F04 + '-B0001'
B2 = F04 + '-B0002'
ALLOWED = {
    NOT_CONFIGURED,
    INVALID,
    'MongoDB is configured',
    CONFIGURED_FLAGS_OFF,
    'MongoDB is configured and reachable',
    'MongoDB is configured but unavailable',
    PRODUCTION_REFUSED,
}
SECRET_URI = 'mongodb+srv://operator:SuperSecret@cluster0.mongodb.net/historyit_control'


class SettingsStub:
    def __init__(self, **kwargs):
        self.mongodb_uri = kwargs.get('mongodb_uri', '')
        self.mongodb_database = kwargs.get('mongodb_database', '')
        self.control_plane_environment = kwargs.get('control_plane_environment', 'staging')
        self.database_control_plane_enabled = kwargs.get('database_control_plane_enabled', False)
        self.database_shadow_writes_enabled = kwargs.get('database_shadow_writes_enabled', False)
        self.database_backfill_enabled = kwargs.get('database_backfill_enabled', False)
        self.job_index_spreadsheet_id = kwargs.get('job_index_spreadsheet_id', '')


def service():
    store = MemoryStore()
    store.ensure_indexes()
    return ControlService(store, 'staging'), store


def _safe(text: str):
    assert '://' not in text
    assert '@' not in text
    assert 'SuperSecret' not in text
    assert text in ALLOWED


class ConfigurationTests(unittest.TestCase):
    def test_startup_messages_never_include_the_uri(self):
        empty = SettingsStub()
        self.assertEqual(startup_message(empty), NOT_CONFIGURED)
        invalid = SettingsStub(mongodb_uri='not-a-database')
        self.assertEqual(startup_message(invalid), INVALID)
        configured = SettingsStub(mongodb_uri=SECRET_URI)
        self.assertEqual(startup_message(configured), CONFIGURED_FLAGS_OFF)
        refused = SettingsStub(mongodb_uri=SECRET_URI, control_plane_environment='production')
        self.assertEqual(startup_message(refused), PRODUCTION_REFUSED)
        for item in (empty, invalid, configured, refused):
            _safe(startup_message(item))
        with self.assertLogs('historyit.control', level='INFO') as logs:
            announce(configured)
        self.assertTrue(logs.output)
        for line in logs.output:
            self.assertNotIn('SuperSecret', line)
            self.assertNotIn('mongodb+srv', line)

    def test_flags_default_off_and_live_settings_stay_allowlisted(self):
        fresh = main.Settings()
        self.assertFalse(fresh.database_control_plane_enabled)
        self.assertFalse(fresh.database_shadow_writes_enabled)
        self.assertFalse(fresh.database_backfill_enabled)
        _safe(startup_message(main.settings))

    def test_redact_and_test_process_refuses_atlas(self):
        cleaned = redact('down ' + SECRET_URI + ' password=SuperSecret')
        self.assertNotIn('SuperSecret', cleaned)
        self.assertIn('[redacted-uri]', cleaned)
        with self.assertRaises(ControlPlaneUnavailable) as caught:
            refuse_atlas_during_tests(SECRET_URI)
        self.assertNotIn('SuperSecret', str(caught.exception))

    def test_indexes_are_unique_for_workspace_job_folder_batch_and_event(self):
        by_name = {item['name']: item for item in INDEX_SPECS}
        self.assertTrue(by_name['uniq_workspace_id']['unique'])
        self.assertTrue(by_name['uniq_job_id']['unique'])
        self.assertTrue(by_name['uniq_job_folder']['unique'])
        self.assertEqual(by_name['uniq_job_folder']['keys'], [('job_id', 1), ('folder_id', 1)])
        self.assertTrue(by_name['uniq_job_batch']['unique'])
        self.assertEqual(by_name['uniq_job_batch']['keys'], [('job_id', 1), ('batch_id', 1)])
        self.assertTrue(by_name['uniq_event_id']['unique'])
        for name in ('job_project_name', 'job_box_label', 'job_status', 'job_created_at', 'job_updated_at'):
            self.assertIn(name, by_name)

        class Collection:
            def __init__(self):
                self.indexes = []
            def create_index(self, keys, unique=False, name=None):
                self.indexes.append({'keys': list(keys), 'unique': unique, 'name': name})
                return name

        class Database:
            def __init__(self):
                self.collections = {}
            def __getitem__(self, name):
                self.collections.setdefault(name, Collection())
                return self.collections[name]

        database = Database()
        applied = MongoStore(database).ensure_indexes()
        self.assertEqual(len(applied), len(INDEX_SPECS))
        folder = database.collections['folders'].indexes[0]
        self.assertEqual(folder['keys'], [('job_id', 1), ('folder_id', 1)])
        self.assertTrue(folder['unique'])


class StoreFlowTests(unittest.TestCase):
    def test_create_is_idempotent_and_workspaces_do_not_share_jobs(self):
        ctrl, store = service()
        alpha = ctrl.create_workspace(project_name='Alpha Project')
        beta = ctrl.create_workspace(project_name='Beta Project')
        self.assertNotEqual(alpha['workspace_id'], beta['workspace_id'])
        again = ctrl.create_workspace(project_name='Alpha Project')
        self.assertEqual(again['workspace_id'], alpha['workspace_id'])
        first = ctrl.create_job(job_id='HIT-aaaaaaaaaaaaaaaa', workspace_id=alpha['workspace_id'], project_name='Alpha Project', box_label='Box A', source='live')
        second = ctrl.create_job(job_id='HIT-aaaaaaaaaaaaaaaa', workspace_id=alpha['workspace_id'], project_name='Alpha Project', box_label='Box A', source='live')
        self.assertEqual(len(second['events']), 1)
        self.assertEqual(first['job']['job_id'], second['job']['job_id'])
        ctrl.create_job(job_id='HIT-bbbbbbbbbbbbbbbb', workspace_id=beta['workspace_id'], project_name='Beta Project', box_label='Box B', source='live')
        self.assertEqual([row['job_id'] for row in ctrl.list_jobs(workspace_id=alpha['workspace_id'])], ['HIT-aaaaaaaaaaaaaaaa'])
        with self.assertRaises(WorkspaceIsolationError):
            ctrl.get_detail('HIT-aaaaaaaaaaaaaaaa', beta['workspace_id'])
        with self.assertRaises(WorkspaceIsolationError):
            ctrl.create_job(job_id='HIT-aaaaaaaaaaaaaaaa', workspace_id=beta['workspace_id'], project_name='Beta Project', source='live')

    def test_folder_and_batch_duplicates_collapse(self):
        ctrl, _store = service()
        workspace = ctrl.create_workspace(project_name='Alpha Project')
        ctrl.create_job(job_id='HIT-aaaaaaaaaaaaaaaa', workspace_id=workspace['workspace_id'], project_name='Alpha Project', source='live')
        ctrl.add_folder(workspace_id=workspace['workspace_id'], job_id='HIT-aaaaaaaaaaaaaaaa', folder_id='folder-1', folder_name='F04', asset_count=10)
        ctrl.add_folder(workspace_id=workspace['workspace_id'], job_id='HIT-aaaaaaaaaaaaaaaa', folder_id='folder-1', folder_name='F04', asset_count=12)
        detail = ctrl.add_batch(workspace_id=workspace['workspace_id'], job_id='HIT-aaaaaaaaaaaaaaaa', folder_id='folder-1', batch_id='HIT-aaaaaaaaaaaaaaaa-B0001', batch_number=1, asset_count=10, status='Authorized / Pending Inspection')
        detail = ctrl.add_batch(workspace_id=workspace['workspace_id'], job_id='HIT-aaaaaaaaaaaaaaaa', folder_id='folder-1', batch_id='HIT-aaaaaaaaaaaaaaaa-B0001', batch_number=1, asset_count=10, status='Authorized / Pending Inspection')
        self.assertEqual(len(detail['folders']), 1)
        self.assertEqual(len(detail['batches']), 1)
        self.assertEqual(detail['folders'][0]['asset_count'], 12)

    def test_pause_resume_retry_complete_and_lease_recovery(self):
        ctrl, store = service()
        workspace = ctrl.create_workspace(project_name='Alpha Project')
        wid = workspace['workspace_id']
        job = 'HIT-aaaaaaaaaaaaaaaa'
        ctrl.create_job(job_id=job, workspace_id=wid, project_name='Alpha Project', source='live')
        ctrl.add_batch(workspace_id=wid, job_id=job, folder_id='folder-1', batch_id=job + '-B0001', status='failed', attempt_count=1)
        started = ctrl.start_job(job, wid, correlation_id='start-1')
        self.assertEqual(started['job']['job_status'], 'running')
        again = ctrl.start_job(job, wid, correlation_id='start-1')
        self.assertEqual(again['job']['job_status'], 'running')
        self.assertEqual(sum(1 for event in again['events'] if event['event_type'] == 'started'), 1)
        paused = ctrl.pause_job(job, wid, correlation_id='pause-1')
        paused_again = ctrl.pause_job(job, wid, correlation_id='pause-1')
        self.assertEqual(paused_again['job']['job_status'], 'paused')
        self.assertEqual(sum(1 for event in paused_again['events'] if event['event_type'] == 'paused'), 1)
        self.assertTrue(paused['checkpoints'][-1]['pause_state']['paused'])
        resumed = ctrl.resume_job(job, wid, correlation_id='resume-1')
        self.assertEqual(resumed['job']['job_status'], 'running')
        resumed_again = ctrl.resume_job(job, wid, correlation_id='resume-1')
        self.assertEqual(sum(1 for event in resumed_again['events'] if event['event_type'] == 'resumed'), 1)
        claimed = ctrl.claim_batch(workspace_id=wid, job_id=job, batch_id=job + '-B0001', worker_id='worker-a', now='2026-10-21T00:00:00Z', ttl_seconds=60)
        with self.assertRaises(LeaseHeld):
            ctrl.claim_batch(workspace_id=wid, job_id=job, batch_id=job + '-B0001', worker_id='worker-b', now='2026-10-21T00:00:30Z', ttl_seconds=60)
        stale = store.update_batch(job, job + '-B0001', claimed['version'] - 1, {'lease_owner': 'worker-c'})
        self.assertIsNone(stale)
        expired = ctrl.recover_job(job, wid, now='2026-10-21T00:02:00Z')
        self.assertEqual(expired['job']['job_status'], 'paused')
        self.assertEqual(expired['events'][-1]['event_type'], 'lease_expired')
        self.assertFalse(expired['inspection_started'])
        ctrl.resume_job(job, wid)
        store.update_batch(job, job + '-B0001', store.get_batch(job, job + '-B0001')['version'], {'status': 'failed', 'lease_owner': None, 'lease_until': None})
        ctrl._save_job(ctrl.require_job(job, wid), {'job_status': 'failed', 'updated_at': '2026-10-21T00:03:00Z'})
        retried = ctrl.retry_batch(job, job + '-B0001', wid, correlation_id='retry-1')
        retried_again = ctrl.retry_batch(job, job + '-B0001', wid, correlation_id='retry-1')
        self.assertEqual(retried_again['job']['job_status'], 'queued')
        self.assertEqual(sum(1 for event in retried_again['events'] if event['event_type'] == 'retried'), 1)
        self.assertEqual(retried['batches'][0]['attempt_count'], 2)
        completed = ctrl.complete_job(job, wid, evidence='batch_status', correlation_id='done-1')
        completed_again = ctrl.complete_job(job, wid, evidence='batch_status', correlation_id='done-1')
        self.assertEqual(completed_again['job']['job_status'], 'completed')
        self.assertEqual(sum(1 for event in completed_again['events'] if event['event_type'] == 'completed'), 1)
        with self.assertRaises(NotAllowed):
            ctrl.complete_job(job, wid, evidence='')
            other = ctrl.create_job(job_id='HIT-cccccccccccccccc', workspace_id=wid, project_name='Alpha Project', source='live')
            self.assertEqual(other['job']['job_status'], 'queued')
            ctrl.complete_job('HIT-cccccccccccccccc', wid, evidence=None)

    def test_running_without_a_lease_is_left_alone(self):
        ctrl, _store = service()
        workspace = ctrl.create_workspace(project_name='Alpha Project')
        job = 'HIT-aaaaaaaaaaaaaaaa'
        ctrl.create_job(job_id=job, workspace_id=workspace['workspace_id'], project_name='Alpha Project', source='live')
        ctrl.start_job(job, workspace['workspace_id'])
        recovered = ctrl.recover_job(job, workspace['workspace_id'], now='2026-10-21T03:00:00Z')
        self.assertEqual(recovered['job']['job_status'], 'running')


class ImportTests(unittest.TestCase):
    def test_historical_import_does_not_invent_completion_or_touch_sheets(self):
        sheets = _sheets(f04_tables())
        extract = read_register(sheets, 'test-register-f04', F04)
        self.assertEqual(sheets.writes, [])
        self.assertEqual(sheets.starts, [])
        plan = plan_from_extract(extract, source=JobSource.historical, job_id=F04)
        self.assertFalse(plan.inspection_started)
        self.assertEqual(plan.job_status.value, 'completed')
        self.assertEqual(plan.summary_counts.asset_count, 39)
        self.assertEqual([batch.batch_id for batch in plan.batches], [B1, B2])
        self.assertIsNone(plan.batches[0].start_filename)
        ctrl, _store = service()
        detail = ctrl.import_record(plan, correlation_id='f04-import')
        self.assertEqual(detail['job']['job_id'], F04)
        self.assertEqual(detail['job']['source'], 'historical')
        self.assertEqual(detail['job']['summary_counts']['asset_count'], 39)
        self.assertEqual(detail['job']['completion_evidence'], 'batch_status_and_validation')
        self.assertFalse(detail['inspection_started'])
        self.assertFalse(detail['job']['resume_allowed'])
        again = ctrl.import_record(plan, correlation_id='f04-import')
        self.assertTrue(again['already_applied'])
        self.assertEqual(len(again['batches']), 2)
        self.assertEqual(sum(1 for event in again['events'] if event['event_type'] == 'imported'), 1)
        stored = again['batches'][0]
        self.assertNotIn('Notes', stored)
        self.assertNotIn('Input Tokens', stored)
        self.assertEqual(stored['response_ids'], ['resp_f04_batch_1'])
        reopened = ctrl.reopen_job(F04)
        reopened_again = ctrl.reopen_job(F04)
        self.assertEqual(reopened['job']['job_status'], 'completed')
        self.assertFalse(reopened['inspection_started'])
        self.assertTrue(reopened_again['already_applied'])
        with self.assertRaises(NotAllowed):
            ctrl.resume_job(F04)
        self.assertEqual(sheets.writes, [])
        self.assertEqual(sheets.starts, [])

    def test_validation_failure_and_missing_rows_do_not_count_as_completed(self):
        tables = f04_tables()
        tables['Validation'][0]['Result'] = 'FAIL'
        plan = plan_from_extract(read_register(_sheets(tables), 'test-register-f04', F04), source=JobSource.historical, job_id=F04)
        self.assertEqual(plan.job_status.value, 'failed')
        empty = plan_from_extract({'register_was_read': True, 'summary_origin': 'register', 'batch_queue': [], 'warnings': []}, source=JobSource.imported, job_id=F04)
        self.assertEqual(empty.job_status.value, 'unknown')
        self.assertIsNone(empty.completion_evidence)

    def test_known_staging_jobs_then_register_upgrade_stay_isolated(self):
        ctrl, _store = service()
        ids = []
        for record in KNOWN_STAGING_JOBS:
            from app.control_plane.importing import operator_note_extract
            plan = plan_from_extract(operator_note_extract(record), source=JobSource.historical, job_id=record['job_id'])
            detail = ctrl.import_record(plan, correlation_id='catalog:' + record['job_id'])
            ids.append(detail['job']['job_id'])
            self.assertEqual(detail['job']['job_status'], 'unknown')
            self.assertIsNone(detail['job']['register_url'])
            self.assertNotEqual(detail['job']['job_status'], 'completed')
        self.assertEqual(ids, [F04, 'HIT-b646d242c6175cdb', 'HIT-36381173e12dba3e'])
        f04 = ctrl.get_detail(F04)
        self.assertEqual(f04['job']['project_name'], 'Tri Delta Chapters')
        self.assertEqual(f04['job']['summary_counts']['asset_count'], 39)
        self.assertEqual(f04['job']['summary_origin'], 'operator_notes')
        self.assertEqual(f04['job']['deployed_release'], '2044707')
        others = ctrl.list_jobs(workspace_id=f04['job']['workspace_id'])
        self.assertEqual([row['job_id'] for row in others], [F04])
        upgraded = ctrl.import_record(plan_from_extract(read_register(_sheets(f04_tables()), 'test-register-f04', F04), source=JobSource.historical, job_id=F04))
        self.assertEqual(upgraded['job']['job_status'], 'completed')
        self.assertEqual(upgraded['job']['job_id'], F04)
        self.assertEqual(len(ctrl.list_jobs(workspace_id=f04['job']['workspace_id'])), 1)
        other = plan_from_extract({'project_name': 'Other Project', 'summary_origin': 'register', 'register_was_read': True, 'batch_queue': []}, source=JobSource.historical, job_id=F04)
        with self.assertRaises(WorkspaceIsolationError):
            ctrl.import_record(other)


class ShadowAndApiTests(unittest.TestCase):
    def setUp(self):
        self.flags = (
            main.settings.database_control_plane_enabled,
            main.settings.database_shadow_writes_enabled,
            main.settings.database_backfill_enabled,
        )
        runtime.clear_store()

    def tearDown(self):
        main.settings.database_control_plane_enabled = self.flags[0]
        main.settings.database_shadow_writes_enabled = self.flags[1]
        main.settings.database_backfill_enabled = self.flags[2]
        runtime.clear_store()

    def test_shadow_failure_keeps_the_sheets_result_and_hides_the_uri(self):
        sentinel = {'job_id': 'HIT-aaaaaaaaaaaaaaaa', 'spreadsheet_id': 'sheet'}
        def explode():
            raise RuntimeError(SECRET_URI)
        with self.assertLogs('historyit.control', level='WARNING') as logs:
            returned = preserve_sheets_result(sentinel, explode)
        self.assertEqual(returned['job_id'], 'HIT-aaaaaaaaaaaaaaaa')
        self.assertNotIn('SuperSecret', '\n'.join(logs.output))
        called = []
        guarded_shadow(main.settings, 'create', 'HIT-aaaaaaaaaaaaaaaa', sentinel, lambda: called.append(True))
        self.assertEqual(called, [])

    def test_create_project_does_not_start_inspection_or_duplicate(self):
        client = TestClient(main.app)
        with patch('app.main.guard', lambda *args, **kwargs: None):
            blocked = client.post('/api/control/workspaces', json={'project_name': 'Tri Delta Chapters'})
        self.assertEqual(blocked.status_code, 409)
        self.assertIn('Google Sheets', blocked.json()['detail'])
        main.settings.database_control_plane_enabled = True
        runtime.set_store(MemoryStore())
        with patch('app.main.guard', lambda *args, **kwargs: None):
            with patch('app.main.live', side_effect=AssertionError('sheets')):
                first = client.post('/api/control/workspaces', json={
                    'project_name': 'Tri Delta Chapters',
                    'client_organization': 'Chapter',
                })
                second = client.post('/api/control/workspaces', json={'project_name': 'tri delta chapters'})
                listed = client.get('/api/control/workspaces')
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()['inspection_started'])
        self.assertFalse(first.json()['already_applied'])
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()['already_applied'])
        self.assertEqual(first.json()['workspace']['workspace_id'], second.json()['workspace']['workspace_id'])
        self.assertEqual(second.json()['workspace']['project_name'], 'Tri Delta Chapters')
        self.assertEqual(second.json()['workspace']['client_organization'], 'Chapter')
        self.assertEqual(len(listed.json()['workspaces']), 1)

    def test_disabled_empty_error_and_offline_states(self):
        client = TestClient(main.app)
        with patch('app.main.guard', lambda *a, **k: None):
            disabled = client.get('/api/control/jobs')
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(disabled.json()['state'], 'disabled')
        self.assertEqual(disabled.json()['jobs'], [])
        self.assertIn('Google Sheets', disabled.json()['warning'])
        main.settings.database_control_plane_enabled = True
        store = MemoryStore()
        runtime.set_store(store)
        with patch('app.main.guard', lambda *a, **k: None):
            empty = client.get('/api/control/workspaces')
        self.assertEqual(empty.json()['state'], 'empty')
        def boom():
            raise RuntimeError(SECRET_URI)
        store.list_workspaces = boom
        with patch('app.main.guard', lambda *a, **k: None):
            with self.assertLogs('historyit.control', level='WARNING') as logs:
                failed = client.get('/api/control/workspaces')
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()['state'], 'error')
        self.assertNotIn('SuperSecret', failed.text)
        self.assertNotIn('SuperSecret', '\n'.join(logs.output))
        runtime.clear_store()
        with patch('app.main.guard', lambda *a, **k: None):
            offline = client.get('/api/control/jobs')
        self.assertEqual(offline.json()['state'], 'offline')
        self.assertIn('Google Sheets', offline.json()['warning'])

    def test_import_endpoint_reads_sheets_and_does_not_start_or_write(self):
        sheets = _sheets(f04_tables())
        main.settings.database_control_plane_enabled = True
        main.settings.database_backfill_enabled = True
        runtime.set_store(MemoryStore())
        client = TestClient(main.app)
        with patch('app.main.guard', lambda *a, **k: None):
            with patch('app.main.live', return_value=sheets):
                response = client.post('/api/control/import', json={
                    'job_id': F04,
                    'register_url': 'https://docs.google.com/spreadsheets/d/test-register-f04/edit',
                    'source': 'historical',
                    'deployed_release': '2044707',
                })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body['inspection_started'])
        self.assertEqual(body['job']['job_id'], F04)
        self.assertEqual(body['job']['summary_counts']['asset_count'], 39)
        self.assertEqual(body['job']['job_status'], 'completed')
        self.assertEqual([row['batch_id'] for row in body['batches']], [B1, B2])
        self.assertEqual(sheets.writes, [])
        self.assertEqual(sheets.starts, [])
        with patch('app.main.guard', lambda *a, **k: None):
            with patch('app.main.live', side_effect=AssertionError('start')):
                resume = client.post('/api/control/jobs/' + F04 + '/resume', json={})
        self.assertEqual(resume.status_code, 409)
        self.assertIn('cannot be resumed', resume.json()['detail'].lower())
        with patch('app.main.guard', lambda *a, **k: None):
            reopened = client.post('/api/control/jobs/' + F04 + '/reopen', json={})
        self.assertEqual(reopened.status_code, 200)
        self.assertFalse(reopened.json()['inspection_started'])
        self.assertEqual(reopened.json()['job']['job_status'], 'completed')

    def test_backfill_lists_known_jobs_without_claiming_completion(self):
        main.settings.database_control_plane_enabled = True
        main.settings.database_backfill_enabled = True
        runtime.set_store(MemoryStore())
        client = TestClient(main.app)
        with patch('app.main.guard', lambda *a, **k: None):
            response = client.post('/api/control/backfill', json={'include_known_staging_jobs': True, 'registers': []})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body['inspection_started'])
        self.assertEqual(body['job_ids'], [item['job_id'] for item in KNOWN_STAGING_JOBS])
        self.assertTrue(all(item['job']['job_status'] == 'unknown' for item in body['results']))

    def test_sheets_create_survives_a_shadow_failure(self):
        sheets = _sheets({})
        runtime_obj = type('Runtime', (), {'sheets': sheets, 'create': staticmethod(lambda req: _job_state()), 'start': staticmethod(lambda job: (_ for _ in ()).throw(AssertionError('start')))})()
        main.settings.database_shadow_writes_enabled = True
        client = TestClient(main.app)
        with patch('app.main.guard', lambda *a, **k: None):
            with patch('app.main.live', return_value=runtime_obj):
                with patch('app.control_plane.shadow.get_store', side_effect=RuntimeError(SECRET_URI)):
                    with self.assertLogs('historyit.control', level='WARNING') as logs:
                        response = client.post('/api/jobs', json={
                            'project': 'Example Project',
                            'source_folder_id': 'folder-id',
                            'spreadsheet_id': 'sheet-id',
                            'box': 'Box A',
                        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['job_id'], 'HIT-aaaaaaaaaaaaaaaa')
        self.assertEqual(response.json()['status'], 'Authorized / Pending Inspection')
        self.assertEqual(sheets.writes, [])
        self.assertNotIn('SuperSecret', response.text)
        self.assertNotIn('SuperSecret', '\n'.join(logs.output))

    def test_sheets_modules_do_not_import_the_control_plane(self):
        for relative in ('server/app/orchestrator.py', 'server/app/inspection.py', 'server/app/commit.py', 'server/app/decisions.py', 'server/app/register_state.py'):
            tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and 'control_plane' in node.module:
                    self.fail(relative + ' imports the control plane')
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn('control_plane', alias.name)


def f04_tables():
    queue = [
        {
            'Project': 'Tri Delta Chapters', 'Job ID': F04, 'Batch ID': B1, 'Box': 'Chapters Box 31',
            'Source Group / Folder': 'F04', 'Source Folder URL': 'https://drive.google.com/drive/folders/folder-f04',
            'Planned Asset Count': '20', 'Batch Status': 'Committed - Validated',
            'Prior Open Item': 'None', 'Resulting Open Item': 'None',
            'Inspection Attempt': '1', 'Commit Attempt': '1', 'Validation Status': 'PASS',
            'Error / Retry Note': '', 'Notes': '{"manifest":[{"name":"not-stored"}]}',
        },
        {
            'Project': 'Tri Delta Chapters', 'Job ID': F04, 'Batch ID': B2, 'Box': 'Chapters Box 31',
            'Source Group / Folder': 'F04', 'Source Folder URL': 'https://drive.google.com/drive/folders/folder-f04',
            'Planned Asset Count': '19', 'Batch Status': 'Committed - Validated',
            'Prior Open Item': 'None', 'Resulting Open Item': 'None',
            'Inspection Attempt': '1', 'Commit Attempt': '1', 'Validation Status': 'PASS',
            'Error / Retry Note': '',
        },
    ]
    return {
        'Batch Queue': queue,
        'Folder Processing Unit Summary': [{
            'Processing Unit / Source Folder': 'F04', 'Box': 'Chapters Box 31',
            'Processing Status': 'Completed - Validated', 'Enumerated New Source Count': '39',
            'Validation Status': 'PASS',
        }],
        'Box Summary': [{'Box': 'Chapters Box 31', 'Box Status': 'In Progress'}],
        'Validation': [
            {'Check ID': B1 + '-V01', 'Scope': F04, 'Result': 'PASS'},
            {'Check ID': B2 + '-V01', 'Scope': F04, 'Result': 'PASS'},
        ],
        'Continuation State': [{'State Field': 'Open Intellectual Item', 'Current Value': 'None'}],
        'grid:Run Context': [
            ['Field', 'Current Value'],
            ['Project', 'Tri Delta Chapters'],
            ['Job ID', F04],
        ],
        'usage': [
            {'Batch ID': B1, 'Response ID': 'resp_f04_batch_1', 'Input Tokens': '99999', 'raw_response': '{"secret":true}'},
        ],
    }


class _sheets:
    def __init__(self, tables):
        self.tables = tables
        self.writes = []
        self.starts = []
        self.sheets = self
    def rows(self, sid, name):
        if name == 'OpenAI Usage':
            return list(self.tables.get('usage') or [])
        return list(self.tables.get(name) or [])
    def grid(self, sid, name, a1):
        return list(self.tables.get('grid:' + name) or [])
    def write(self, *args, **kwargs):
        self.writes.append(args)
        raise AssertionError('register write')
    def start(self, *args, **kwargs):
        self.starts.append(args)
        raise AssertionError('inspection start')


def _job_state():
    return {
        'job_id': 'HIT-aaaaaaaaaaaaaaaa',
        'project': 'Example Project',
        'spreadsheet_id': 'sheet-id',
        'source_folder_id': 'folder-id',
        'authorized_source_folder_ids': ['folder-id'],
        'discovered_folders': [],
        'batches': 1,
        'status': 'Authorized / Pending Inspection',
        'next_batch': 'HIT-aaaaaaaaaaaaaaaa-B0001',
        'current_folder': None,
        'current_batch': 'HIT-aaaaaaaaaaaaaaaa-B0001',
        'phase': 'Authorized / Pending Inspection',
        'running': False,
        'already_running': False,
        'pause_requested': False,
        'authorized_scope_complete': False,
        'alert': None,
    }


if __name__ == '__main__':
    unittest.main()
