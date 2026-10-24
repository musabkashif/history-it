"""No-cost usage event imports. Fixtures stand in for saved diagnostics and registers."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.control_plane import runtime
from app.control_plane.errors import UsageConflict
from app.control_plane.indexes import INDEX_SPECS
from app.control_plane.memory import MemoryStore
from app.control_plane.mongo import MongoStore
from app.control_plane.service import ControlService
from app.control_plane.usage_events import plan_usage_import

FIXTURE = Path(__file__).parent / 'fixtures' / 'usage_reconciliation_sample.example.json'
PROVEN_RATES = {'input': 4, 'cached': 0.4, 'write30': 5, 'write12': 6, 'output': 20}


def sample():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


class UsageEventImportTests(unittest.TestCase):
    def test_fixture_collapses_copies_retries_joins_and_duplicate_registers(self):
        loaded = sample()
        plan = plan_usage_import(loaded['register_rows'], loaded['diagnostics'])
        by_id = {item['response_id']: item for item in plan['events']}
        self.assertEqual(len(by_id), 7)
        self.assertEqual(by_id['resp_03e6edcb0ccde4a8006adb6433d33887d183413e2ac62a1b6d']['attempt'], 1)
        self.assertEqual(by_id['resp_002d7076d5a95733006adc522c90bc87d08179cd89885c7ff5']['attempt'], 2)
        self.assertNotIn(
            'resp_08c6173809d3e645006acd9ebd6c6487d09e98a023918245a9,resp_0c22e17bc00cea32006acd9f52c9f887d081293781bd9c5740,resp_00377ea00fd77590006acda046f8c087d1bb3278b62cf718c1',
            by_id,
        )
        self.assertEqual(len(plan['skipped_joined']), 1)
        joined = by_id['resp_049a05bf76261b65006ad27f365dc087d198f3ec91a1424b3c,resp_0da847c3221f3a07006ad27f8eed0c87d184421826e2e06bf4']
        self.assertEqual(joined['request_count'], 2)
        self.assertEqual(joined['register_estimate_usd'], '0.578840')
        self.assertIsNone(joined['calculated_cost_usd'])
        self.assertEqual(joined['cost_state'], 'unknown')
        self.assertIsNone(joined['cache_write_30m_tokens'])
        duplicate = by_id['resp_02ddca27e1158d90006ad9e5a157bc87d084e725ba5a6c74fa']
        self.assertEqual(duplicate['job_id'], 'HIT-0a9d311d62b4396f')
        self.assertEqual(plan['duplicates'][0]['response_id'], duplicate['response_id'])
        self.assertTrue(any(item.get('kind') == 'attribution' for item in plan['conflicts']))
        self.assertEqual(plan['rows_without_response'], 1)
        self.assertTrue(all(item['source'] == 'historical' for item in plan['events']))
        self.assertFalse(plan['inspection_started'])

    def test_unknown_historical_cost_is_null_and_conflicts_keep_the_first_payload(self):
        store = MemoryStore()
        service = ControlService(store, 'staging')
        loaded = sample()
        first = service.import_usage_observations(loaded['register_rows'], loaded['diagnostics'], {})
        self.assertIsNone(first['summary']['proven_cost_usd'])
        self.assertEqual(first['summary']['unknown_event_count'], 7)
        self.assertEqual(first['summary']['request_count'], 8)
        self.assertIsNone(first['summary']['cache_write_tokens'])
        self.assertEqual(first['summary']['cache_write_unknown_count'], 7)
        self.assertEqual(first['created'], 7)
        self.assertFalse(first['sheets_written'])
        again = service.import_usage_observations(loaded['register_rows'], loaded['diagnostics'], {})
        self.assertEqual(again['created'], 0)
        self.assertEqual(again['existing'], 7)
        self.assertEqual(len(store.usage_events), 7)
        conflict = service.import_usage_observations([{
            'Response ID': 'resp_03e6edcb0ccde4a8006adb6433d33887d183413e2ac62a1b6d',
            'Job ID': 'HIT-fe3b819451ab26c3',
            'Input Tokens': '1',
            'Cached Input Tokens': '0',
            'Output Tokens': '1',
            'Estimated Cost USD': '9',
        }], [], {})
        self.assertEqual(conflict['conflicts'][0]['response_id'], 'resp_03e6edcb0ccde4a8006adb6433d33887d183413e2ac62a1b6d')
        stored = store.get_usage_event('resp_03e6edcb0ccde4a8006adb6433d33887d183413e2ac62a1b6d')
        self.assertEqual(stored['input_tokens'], 80970)
        self.assertIsNone(stored['calculated_cost_usd'])

    def test_joined_cell_is_not_added_on_top_of_its_parts(self):
        store = MemoryStore()
        service = ControlService(store, 'staging')
        loaded = sample()
        joined = loaded['register_rows'][0]
        service.import_usage_observations([joined], [], {})
        self.assertEqual(len(store.usage_events), 1)
        self.assertEqual(next(iter(store.usage_events.values()))['request_count'], 3)
        covered = service.import_usage_observations([], loaded['diagnostics'], {})
        self.assertEqual(len(store.usage_events), 3)
        self.assertEqual(len(covered['covered']), 3)
        self.assertEqual(covered['summary']['request_count'], 5)

    def test_parts_imported_first_skip_the_joined_cost(self):
        store = MemoryStore()
        service = ControlService(store, 'staging')
        loaded = sample()
        service.import_usage_observations([], [row for row in loaded['diagnostics'] if row['response_id'].startswith('resp_08') or row['response_id'].startswith('resp_0c') or row['response_id'].startswith('resp_003')], {})
        self.assertEqual(len(store.usage_events), 3)
        again = service.import_usage_observations([loaded['register_rows'][0]], [], {})
        self.assertEqual(again['created'], 0)
        self.assertEqual(len(again['covered']), 1)
        self.assertEqual(len(store.usage_events), 3)

    def test_proven_cost_excludes_unknown_events(self):
        store = MemoryStore()
        service = ControlService(store, 'staging')
        historical = {
            'Response ID': 'resp-old',
            'Job ID': 'HIT-old',
            'Project': 'Alpha',
            'workspace_id': 'ws-a',
            'Model': 'gpt-5.6-sol',
            'Status': 'Success',
            'Input Tokens': '1000000',
            'Cached Input Tokens': '0',
            'Output Tokens': '0',
            'Estimated Cost USD': '4',
        }
        proven = {
            'Response ID': 'resp-new',
            'Job ID': 'HIT-new',
            'Project': 'Alpha',
            'workspace_id': 'ws-a',
            'Model': 'gpt-5.6-sol',
            'Status': 'Success',
            'observed_at': '2026-10-24T12:00:00Z',
            'Input Tokens': '1000000',
            'Uncached Input Tokens': '1000000',
            'Cached Input Tokens': '0',
            'Cache Write 30m Tokens': '0',
            'Cache Write 12h Tokens': '0',
            'Output Tokens': '0',
        }
        other = {
            'Response ID': 'resp-other',
            'Job ID': 'HIT-other',
            'Project': 'Beta',
            'workspace_id': 'ws-b',
            'Model': 'gpt-other',
            'Status': 'Failure',
            'observed_at': '2026-10-01T00:00:00Z',
            'Uncached Input Tokens': '0',
            'Cached Input Tokens': '0',
            'Cache Write 30m Tokens': '0',
            'Cache Write 12h Tokens': '0',
            'Output Tokens': '0',
        }
        result = service.import_usage_observations([historical, proven, other], [], PROVEN_RATES)
        self.assertEqual(result['summary']['proven_cost_usd'], '4.000000')
        self.assertEqual(result['summary']['unknown_event_count'], 1)
        self.assertEqual(result['summary']['request_count'], 3)
        self.assertIsNone(result['summary']['cache_write_tokens'])
        self.assertEqual(result['summary']['cache_read_tokens'], 0)
        job = service.usage_summary(job_id='HIT-new')
        self.assertEqual(job['summary']['proven_cost_usd'], '4.000000')
        self.assertEqual(job['summary']['unknown_event_count'], 0)
        project = service.usage_summary(project_name='alpha')
        self.assertEqual(project['summary']['request_count'], 2)
        workspace = service.usage_summary(workspace_id='ws-b')
        self.assertEqual(workspace['summary']['proven_cost_usd'], '0.000000')
        self.assertEqual(workspace['summary']['request_count'], 1)
        dated = service.usage_summary(observed_from='2026-10-24', observed_to='2026-10-24')
        self.assertEqual(dated['summary']['request_count'], 1)
        self.assertEqual(service.usage_summary(model='gpt-other')['summary']['request_count'], 1)
        self.assertEqual(service.usage_summary(status='Failure')['summary']['request_count'], 1)
        self.assertIsNone(store.get_usage_event('resp-old')['calculated_cost_usd'])
        self.assertEqual(store.get_usage_event('resp-old')['register_estimate_usd'], '4.000000')

    def test_store_refuses_to_replace_a_usage_event(self):
        store = MemoryStore()
        service = ControlService(store, 'staging')
        service.import_usage_observations([{
            'Response ID': 'resp-lock',
            'Input Tokens': '10',
            'Job ID': 'HIT-lock',
        }], [], {})
        with self.assertRaises(UsageConflict):
            store.insert_usage_event({**store.get_usage_event('resp-lock'), 'input_tokens': 11})
        self.assertEqual(store.get_usage_event('resp-lock')['input_tokens'], 10)

    def test_usage_response_index_is_unique_and_flags_stay_off(self):
        by_name = {item['name']: item for item in INDEX_SPECS}
        self.assertTrue(by_name['uniq_usage_response_id']['unique'])
        self.assertEqual(by_name['uniq_usage_response_id']['keys'], [('response_id', 1)])

        class Collection:
            def __init__(self):
                self.indexes = []
            def create_index(self, keys, unique=False, name=None):
                self.indexes.append({'name': name, 'unique': unique, 'keys': list(keys)})

        class Database:
            def __init__(self):
                self.collections = {}
            def __getitem__(self, name):
                self.collections.setdefault(name, Collection())
                return self.collections[name]

        database = Database()
        MongoStore(database).ensure_indexes()
        self.assertTrue(database.collections['usage_events'].indexes[0]['unique'])
        fields = main.Settings.model_fields
        self.assertFalse(fields['database_control_plane_enabled'].default)
        self.assertFalse(fields['database_shadow_writes_enabled'].default)
        self.assertFalse(fields['database_backfill_enabled'].default)
        self.assertFalse(fields['database_usage_ingestion_enabled'].default)


class UsageEventApiTests(unittest.TestCase):
    def setUp(self):
        self.flags = (
            main.settings.database_control_plane_enabled,
            main.settings.database_shadow_writes_enabled,
            main.settings.database_backfill_enabled,
            main.settings.database_usage_ingestion_enabled,
        )
        runtime.clear_store()

    def tearDown(self):
        (
            main.settings.database_control_plane_enabled,
            main.settings.database_shadow_writes_enabled,
            main.settings.database_backfill_enabled,
            main.settings.database_usage_ingestion_enabled,
        ) = self.flags
        runtime.clear_store()

    def test_summary_uses_null_when_disabled_and_import_leaves_backfill_off(self):
        client = TestClient(main.app)
        main.settings.database_control_plane_enabled = False
        main.settings.database_backfill_enabled = False
        main.settings.database_shadow_writes_enabled = False
        main.settings.database_usage_ingestion_enabled = False
        with patch('app.main.guard', lambda *args, **kwargs: None):
            disabled = client.get('/api/control/usage/summary')
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(disabled.json()['state'], 'disabled')
        self.assertIsNone(disabled.json()['summary']['proven_cost_usd'])
        self.assertEqual(disabled.json()['summary']['request_count'], 0)
        self.assertNotEqual(disabled.json()['summary']['proven_cost_usd'], 0)
        main.settings.database_control_plane_enabled = True
        runtime.set_store(MemoryStore())
        loaded = sample()
        with patch('app.main.guard', lambda *args, **kwargs: None):
            blocked = client.post('/api/control/usage/import', json={'register_rows': [], 'diagnostics': []})
        self.assertEqual(blocked.status_code, 409)
        self.assertIn('backfill', blocked.json()['detail'].lower())
        main.settings.database_backfill_enabled = True
        with patch('app.main.guard', lambda *args, **kwargs: None):
            with patch('app.main.live', side_effect=AssertionError('sheets')):
                imported = client.post('/api/control/usage/import', json={
                    'register_rows': loaded['register_rows'],
                    'diagnostics': loaded['diagnostics'],
                })
                summary = client.get('/api/control/usage/summary', params={'project': 'Tri Delta Chapters'})
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertFalse(imported.json()['inspection_started'])
        self.assertFalse(imported.json()['sheets_written'])
        self.assertIsNone(imported.json()['summary']['proven_cost_usd'])
        self.assertGreater(summary.json()['summary']['request_count'], 0)
        self.assertFalse(main.settings.database_shadow_writes_enabled)
        self.assertFalse(main.settings.database_usage_ingestion_enabled)
        main.settings.database_backfill_enabled = False


class LiveUsageIngestTests(unittest.TestCase):
    def test_mongo_outage_writes_an_outbox_and_replay_inserts_once(self):
        import tempfile
        from app.control_plane.usage_ingest import ingest_live_usage, replay_usage_ingestion

        with tempfile.TemporaryDirectory() as directory:
            class Settings:
                diagnostics_dir = directory
                database_usage_ingestion_enabled = True

            record = {
                'response_id': 'resp-live',
                'input_tokens': 10,
                'uncached_input_tokens': 10,
                'cached_input_tokens': 0,
                'cache_write_30m_tokens': 0,
                'cache_write_12h_tokens': 0,
                'cache_write_tokens': '',
                'cache_write_retention': '30m',
                'output_tokens': 2,
                'attempt': 1,
                'part': 1,
                'status': 'Received',
                'billed': True,
            }
            batch = {'Job ID': 'HIT-live', 'Batch ID': 'HIT-live-B0001', 'Project': 'Box'}
            with patch('app.control_plane.runtime.get_store', side_effect=RuntimeError('mongo down')):
                result = ingest_live_usage(Settings(), record, batch, '0.000080')
            self.assertFalse(result['stored'])
            saved = json.loads(next(Path(directory, 'usage-ingest').glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(saved['event']['source'], 'live')
            self.assertEqual(saved['event']['calculated_cost_usd'], '0.000080')
            self.assertEqual(saved['event']['cost_basis'], 'application estimate')
            self.assertFalse(saved['replayed'])
            store = MemoryStore()
            with patch('app.control_plane.runtime.get_store', return_value=store):
                replayed = replay_usage_ingestion(Settings())
                again = replay_usage_ingestion(Settings())
            self.assertEqual(replayed['replayed'], 1)
            self.assertEqual(again['replayed'], 0)
            self.assertEqual(len(store.list_usage_events()), 1)
            self.assertEqual(store.get_usage_event('resp-live')['source'], 'live')


class DisposableDatabaseUsageTests(unittest.TestCase):
    def test_mongo_store_import_is_scoped_idempotent_and_reports_conflicts(self):
        from pymongo.errors import DuplicateKeyError
        from app.control_plane.service import ControlService

        class Collection:
            def __init__(self):
                self.docs = []
                self.unique = []

            def create_index(self, keys, unique=False, name=None):
                if unique:
                    self.unique.append(list(keys))

            def find_one(self, query, projection=None):
                for doc in self.docs:
                    if all(doc.get(key) == value for key, value in query.items()):
                        return dict(doc)
                return None

            def insert_one(self, doc):
                for keys in self.unique:
                    query = {key: doc.get(key) for key, _direction in keys}
                    if self.find_one(query) is not None:
                        raise DuplicateKeyError('duplicate response')
                self.docs.append(dict(doc))

            def find(self, query, projection=None):
                return [dict(doc) for doc in self.docs]

        class Database:
            def __init__(self):
                self.collections = {}

            def __getitem__(self, name):
                self.collections.setdefault(name, Collection())
                return self.collections[name]

            def __getattr__(self, name):
                if name.startswith('_'):
                    raise AttributeError(name)
                return self[name]

        database = Database()
        store = MongoStore(database)
        store.ensure_indexes()
        self.assertTrue(database.collections['usage_events'].unique)
        service = ControlService(store)
        loaded = sample()
        first = service.import_usage_observations(loaded['register_rows'], loaded['diagnostics'], PROVEN_RATES)
        second = service.import_usage_observations(loaded['register_rows'], loaded['diagnostics'], PROVEN_RATES)
        self.assertGreater(first['created'], 0)
        self.assertEqual(second['created'], 0)
        self.assertEqual(second['existing'], first['created'])
        self.assertEqual(len(store.list_usage_events()), first['created'])
        summary = service.usage_summary()
        self.assertIsNone(summary['summary']['proven_cost_usd'])
        self.assertGreater(summary['summary']['unknown_event_count'], 0)
        job = service.usage_summary(job_id='HIT-0a9d311d62b4396f')
        other = service.usage_summary(job_id='HIT-missing')
        self.assertGreater(job['summary']['request_count'], 0)
        self.assertEqual(other['summary']['request_count'], 0)
        self.assertEqual(other['summary']['proven_cost_usd'], None)
        conflict = dict(store.get_usage_event('resp_02ddca27e1158d90006ad9e5a157bc87d084e725ba5a6c74fa'))
        conflict['input_tokens'] = 1
        with self.assertRaises(UsageConflict):
            store.insert_usage_event(conflict)
        self.assertNotEqual(store.get_usage_event(conflict['response_id'])['input_tokens'], 1)


if __name__ == '__main__':
    unittest.main()
