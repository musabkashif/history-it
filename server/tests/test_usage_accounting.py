import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.adapters import SheetResizeError
from app.models import ASSET_HEADERS, RECORD_HEADERS, AssetRow, InspectionOutput, RecordRow, Source
from app.inspection import Inspector
from app.orchestrator import Orchestrator
from app.usage_accounting import parse_provider_usage, reconcile_usage, usage_cost


class Rates:
    openai_input_usd_per_million = 4
    openai_cached_input_usd_per_million = Decimal('0.5')
    openai_cache_write_30m_usd_per_million = 5
    openai_cache_write_12h_usd_per_million = 6
    openai_output_usd_per_million = 15


def _record(**usage):
    return parse_provider_usage(usage, response_id='resp-price', attempt=1, part=1, status='Received')


class PricingTests(unittest.TestCase):
    def test_cache_writes_are_not_priced_as_ordinary_input(self):
        record = _record(
            input_tokens=1_000_000,
            output_tokens=0,
            input_tokens_details={'cached_tokens': 0, 'cache_write_tokens': 1_000_000},
        )
        self.assertEqual(record['uncached_input_tokens'], 0)
        self.assertEqual(record['cache_write_tokens'], 1_000_000)
        self.assertEqual(record['cache_write_30m_tokens'], '')
        self.assertEqual(record['cache_write_retention'], 'unknown')
        with self.assertRaisesRegex(Exception, 'retention was not returned'):
            usage_cost(record, Rates())
        named = _record(
            input_tokens=1_000_000,
            output_tokens=0,
            input_tokens_details={'cached_tokens': 0, 'cache_write_30m_tokens': 1_000_000},
        )
        self.assertEqual(named['cache_write_30m_tokens'], 1_000_000)
        self.assertEqual(usage_cost(named, Rates()), '5.000000')
        ordinary = (Decimal(1_000_000) * Decimal(4)) / Decimal(1_000_000)
        self.assertNotEqual(Decimal(usage_cost(named, Rates())), ordinary)

    def test_exact_cost_uses_each_category_rate(self):
        record = _record(
            input_tokens=4_000_000,
            output_tokens=1_000_000,
            input_tokens_details={
                'cached_tokens': 1_000_000,
                'cache_write_30m_tokens': 1_000_000,
                'cache_write_12h_tokens': 1_000_000,
            },
        )
        self.assertEqual(record['uncached_input_tokens'], 1_000_000)
        # 4 + 0.5 + 5 + 6 + 15
        self.assertEqual(Decimal(usage_cost(record, Rates())), Decimal('30.5'))

    def test_missing_cache_write_rate_does_not_use_the_input_rate(self):
        class Missing(Rates):
            openai_cache_write_30m_usd_per_million = 0

        record = _record(input_tokens=100, output_tokens=1, input_tokens_details={'cached_tokens': 0, 'cache_write_30m_tokens': 100})
        with self.assertRaisesRegex(Exception, 'OPENAI_CACHE_WRITE_30M_USD_PER_MILLION'):
            usage_cost(record, Missing())

    def test_contradictory_input_total_is_not_rewritten(self):
        record = _record(input_tokens=10, output_tokens=1, input_tokens_details={'cached_tokens': 8, 'cache_write_tokens': 5})
        self.assertEqual(record['uncached_input_tokens'], '')
        self.assertIn('less than cached', record['usage_identity_gap'])


class MemorySheets:
    def __init__(self):
        self.store = {}
        self.fail_usage = False

    def rows(self, sid, sheet):
        return [dict(row) for row in self.store.get((sid, sheet), [])]

    def write(self, sid, sheet, row, key=None):
        if self.fail_usage and sheet == 'OpenAI Usage':
            raise RuntimeError('usage sheet unavailable')
        rows = self.store.setdefault((sid, sheet), [])
        if key:
            for existing in rows:
                if str(existing.get(key[0])) == str(key[1]):
                    existing.update({name: '' if value is None else str(value) for name, value in row.items()})
                    return 1
        rows.append({name: '' if value is None else str(value) for name, value in row.items()})
        return len(rows)

    def field(self, sid, name):
        return 'None'


class Settings:
    job_index_spreadsheet_id = 'index'
    job_spending_limit_usd = 100
    max_retries = 5
    openai_model = 'gpt-test'
    openai_input_usd_per_million = 4
    openai_cached_input_usd_per_million = Decimal('0.5')
    openai_cache_write_30m_usd_per_million = 5
    openai_cache_write_12h_usd_per_million = 6
    openai_output_usd_per_million = 15
    diagnostics_dir = ''


def batch(bid='HIT-1-B0001'):
    return {
        'Project': 'Tri Delta',
        'Job ID': 'HIT-1',
        'Batch ID': bid,
        'Batch Status': 'Authorized / Pending Inspection',
        'Inspection Attempt': '0',
        'Planned Asset Count': '1',
        'Notes': json.dumps({'manifest': [{
            'id': bid, 'name': bid + '.jpg', 'folder_id': 'folder', 'folder_name': 'F01',
            'path': '/' + bid + '.jpg', 'mime_type': 'image/jpeg',
        }]}),
        'Error / Retry Note': '',
    }


def _usage(response_id, **extra):
    payload = {
        'input_tokens': 1_000_000,
        'output_tokens': 0,
        'cached_input_tokens': 0,
        'elapsed_seconds': 0.2,
        'response_id': response_id,
    }
    payload.update(extra)
    return payload


class OrchestratorUsageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.sheets = MemorySheets()
        self.sheets.store[('index', 'HistoryIT Jobs')] = [{'Job ID': 'HIT-1', 'Spreadsheet ID': 'register'}]
        self.sheets.store[('register', 'Batch Queue')] = [batch()]
        self.settings = Settings()
        self.settings.diagnostics_dir = self.directory.name
        self.calls = []

    def tearDown(self):
        self.directory.cleanup()

    def _orchestrator(self, inspector):
        return Orchestrator(self.sheets, None, inspector, self.settings)

    def test_calibration_conflict_keeps_the_provider_usage(self):
        class Inspector:
            def inspect(inner, job, batch_id, sources, context, rules):
                self.calls.append(batch_id)
                return SimpleNamespace(assets=[], records=[]), _usage('resp-calibration', cache_write_tokens=1_000_000)

        seen = {'n': 0}

        def mismatches(*args, **kwargs):
            seen['n'] += 1
            if seen['n'] == 1:
                return []
            return ['Photograph_016b conflict']

        with patch('app.orchestrator.apply_governing_constraints', side_effect=lambda output: output), \
             patch('app.orchestrator.reference_mismatches', side_effect=mismatches):
            self._orchestrator(Inspector()).run('HIT-1')
        rows = self.sheets.rows('register', 'OpenAI Usage')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Response ID'], 'resp-calibration')
        self.assertEqual(rows[0]['Call ID'], 'response:resp-calibration')
        self.assertEqual(rows[0]['Cache Write Tokens'], '1000000')
        self.assertEqual(rows[0]['Cache Write 30m Tokens'], '')
        self.assertEqual(rows[0]['Cache Write Retention'], 'unknown')
        self.assertEqual(rows[0]['Uncached Input Tokens'], '0')
        self.assertEqual(rows[0]['Estimated Cost USD'], '')
        self.assertEqual(rows[0]['Cost Basis'], 'unknown')
        self.assertEqual(rows[0]['Status'], 'Blocked - Calibration')
        self.assertEqual(self.sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Blocked - Human Review')
        self.assertEqual(self.calls, ['HIT-1-B0001'])

    def test_sheet_resize_after_a_response_keeps_the_provider_usage(self):
        class Inspector:
            def inspect(inner, job, batch_id, sources, context, rules):
                return SimpleNamespace(assets=[], records=[]), _usage('resp-sheets')

        orchestrator = self._orchestrator(Inspector())
        orchestrator._stage = lambda *args, **kwargs: (_ for _ in ()).throw(SheetResizeError('grid denied'))
        with patch('app.orchestrator.apply_governing_constraints', side_effect=lambda output: output), \
             patch('app.orchestrator.reference_mismatches', return_value=[]), \
             patch('app.orchestrator.order_records', side_effect=lambda records, assets, reference: records):
            orchestrator.run('HIT-1')
        row = self.sheets.rows('register', 'OpenAI Usage')[0]
        self.assertEqual(row['Response ID'], 'resp-sheets')
        self.assertEqual(row['Input Tokens'], '1000000')
        self.assertEqual(row['Status'], 'Blocked - Sheets')
        self.assertIn('grid denied', row['Failure Reason'])

    def test_retry_attempts_keep_separate_response_rows(self):
        class Inspector:
            def __init__(inner):
                inner.count = 0

            def inspect(inner, job, batch_id, sources, context, rules):
                inner.count += 1
                error = ValueError('validation failed')
                error.inspection_usage = _usage(f'resp-attempt-{inner.count}', input_tokens=10, output_tokens=2)
                error.elapsed_seconds = 0.1
                raise error

        inspector = Inspector()
        orchestrator = self._orchestrator(inspector)
        orchestrator.run('HIT-1')
        orchestrator.run('HIT-1')
        rows = self.sheets.rows('register', 'OpenAI Usage')
        self.assertEqual([row['Response ID'] for row in rows], ['resp-attempt-1', 'resp-attempt-2'])
        self.assertEqual([row['Attempt'] for row in rows], ['1', '2'])
        self.assertEqual([row['Call ID'] for row in rows], ['response:resp-attempt-1', 'response:resp-attempt-2'])
        self.assertEqual(rows[0]['Input Tokens'], '10')
        self.assertTrue(all(row['Status'] == 'Validation Failed' for row in rows))

    def test_duplicate_response_id_does_not_overwrite_tokens(self):
        orchestrator = self._orchestrator(SimpleNamespace())
        batch_row = batch()
        first = _usage('resp-same', input_tokens=10, output_tokens=2)
        first['attempt'] = 1
        orchestrator._persist_usage('register', batch_row, first)
        orchestrator._persist_usage('register', batch_row, dict(first))
        self.assertEqual(len(self.sheets.rows('register', 'OpenAI Usage')), 1)
        changed = dict(first)
        changed['input_tokens'] = 99
        with self.assertRaisesRegex(Exception, 'does not match'):
            orchestrator._persist_usage('register', batch_row, changed)
        self.assertEqual(self.sheets.rows('register', 'OpenAI Usage')[0]['Input Tokens'], '10')
        outbox = list(Path(self.directory.name, 'usage-outbox').glob('*.json'))
        self.assertEqual(len(outbox), 1)

    def test_usage_write_failure_is_visible_and_recoverable(self):
        class Inspector:
            def inspect(inner, job, batch_id, sources, context, rules):
                self.calls.append(batch_id)
                return SimpleNamespace(assets=[], records=[]), _usage('resp-outbox')

        self.sheets.fail_usage = True
        with patch('app.orchestrator.apply_governing_constraints', side_effect=lambda output: output):
            self._orchestrator(Inspector()).run('HIT-1')
        self.assertEqual(self.sheets.rows('register', 'OpenAI Usage'), [])
        note = self.sheets.rows('register', 'Batch Queue')[0]['Error / Retry Note']
        self.assertIn('Outbox:', note)
        self.assertEqual(self.sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Inspection Failed - Retryable')
        saved = json.loads(next(Path(self.directory.name, 'usage-outbox').glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(saved['record']['response_id'], 'resp-outbox')
        self.assertEqual(saved['record']['input_tokens'], 1_000_000)
        self.assertNotIn('api_key', json.dumps(saved).lower())
        self.assertEqual(self.calls, ['HIT-1-B0001'])

    def test_connection_failure_does_not_invent_token_counts(self):
        class Inspector:
            def inspect(inner, job, batch_id, sources, context, rules):
                raise ConnectionError('getaddrinfo failed')

        self._orchestrator(Inspector()).run('HIT-1')
        self.assertEqual(self.sheets.rows('register', 'OpenAI Usage'), [])
        self.assertEqual(self.sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Inspection Failed - Retryable')


class InspectorUsageTests(unittest.TestCase):
    def test_split_responses_are_recorded_separately_before_validation(self):
        recorded = []

        def row(name, record_id):
            asset = AssetRow.model_validate({**{key: '' for key in ASSET_HEADERS}, 'Digital Asset': name, 'Assigned Record ID': record_id})
            record = RecordRow.model_validate({**{key: '' for key in RECORD_HEADERS}, 'Proposed Record ID': record_id})
            return InspectionOutput(assets=[asset], records=[record])

        class Details:
            cached_tokens = 100
            cache_write_tokens = 900

        class Client:
            def __init__(self):
                self.responses = self
                self.count = 0

            def parse(self, **kwargs):
                self.count += 1
                if self.count == 1:
                    failure = RuntimeError('Error code: 500')
                    failure.status_code = 500
                    raise failure
                text = kwargs['input'][1]['content'][0]['text']
                names = json.loads(text)['authorized_filenames']
                self.last = names

                class Usage:
                    input_tokens = 1000
                    output_tokens = 50
                    input_tokens_details = Details()

                class Response:
                    id = 'resp-' + names[0]
                    status = 'completed'
                    incomplete_details = None
                    usage = Usage()
                    output_parsed = row(names[0], 'item-' + names[0])

                return Response()

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'rules.md').write_text('Inspect every image.', encoding='utf-8')

            class Local:
                governing_docs_dir = directory
                diagnostics_dir = directory
                openai_model = 'gpt-test'
                openai_max_output_tokens = 1000
                openai_reasoning_effort = ''

            class Drive:
                def image(self, _source):
                    from io import BytesIO
                    from PIL import Image
                    out = BytesIO()
                    Image.new('RGB', (4, 4), 'white').save(out, format='JPEG')
                    return out.getvalue()

            inspector = Inspector(Local(), Drive(), client=Client())
            inspector._sleep = lambda _seconds: None
            inspector.usage_recorder = recorded.append
            sources = [
                Source(id='1', name='a.jpg', folder_id='folder', folder_name='F01', path='a.jpg', mime_type='image/jpeg'),
                Source(id='2', name='b.jpg', folder_id='folder', folder_name='F01', path='b.jpg', mime_type='image/jpeg'),
            ]
            output, usage = inspector.inspect('JOB', 'BATCH', sources, {'inspection_attempt': 4}, [])
        self.assertEqual([item.digital_asset for item in output.assets], ['a.jpg', 'b.jpg'])
        self.assertEqual([item['response_id'] for item in recorded], ['resp-a.jpg', 'resp-b.jpg'])
        self.assertEqual([item['part'] for item in recorded], [1, 2])
        self.assertEqual(recorded[0]['cache_write_tokens'], 900)
        self.assertEqual(recorded[0]['cache_write_30m_tokens'], '')
        self.assertEqual(recorded[0]['cache_write_retention'], 'unknown')
        self.assertEqual(recorded[0]['cached_input_tokens'], 100)
        self.assertEqual(recorded[0]['uncached_input_tokens'], 0)
        self.assertEqual(usage['usage_recorded'], True)
        self.assertEqual(len(usage['responses']), 2)

    def test_connection_retries_are_unbilled_diagnostics(self):
        class Client:
            def __init__(self):
                self.responses = self

            def parse(self, **kwargs):
                raise ConnectionError('getaddrinfo failed')

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'rules.md').write_text('Inspect every image.', encoding='utf-8')

            class Local:
                governing_docs_dir = directory
                diagnostics_dir = directory
                openai_model = 'gpt-test'
                openai_max_output_tokens = 100
                openai_reasoning_effort = ''

            class Drive:
                def image(self, _source):
                    from io import BytesIO
                    from PIL import Image
                    out = BytesIO()
                    Image.new('RGB', (4, 4), 'white').save(out, format='JPEG')
                    return out.getvalue()

            inspector = Inspector(Local(), Drive(), client=Client())
            inspector._sleep = lambda _seconds: None
            source = Source(id='1', name='a.jpg', folder_id='folder', folder_name='F01', path='a.jpg', mime_type='image/jpeg')
            with self.assertRaises(ConnectionError):
                inspector.inspect('JOB', 'BATCH', [source], {'inspection_attempt': 1}, [])
            events = json.loads(Path(directory, 'JOB_BATCH_attempt-1.json').read_text(encoding='utf-8'))
        unbilled = [event for event in events if event['event'] == 'usage_unbilled']
        self.assertEqual(len(unbilled), 4)
        self.assertTrue(all(event['billed'] is False for event in unbilled))
        self.assertTrue(all('input_tokens' not in event for event in unbilled))


class ReconciliationTests(unittest.TestCase):
    def test_saved_diagnostics_match_register_rows_and_cost(self):
        record = parse_provider_usage(
            {'input_tokens': 1000, 'output_tokens': 50, 'input_tokens_details': {'cached_tokens': 100, 'cache_write_30m_tokens': 900, 'cache_write_12h_tokens': 0}},
            response_id='resp-1', attempt=2, part=1, status='Received',
        )
        cost = usage_cost(record, Rates())
        diagnostics = [{
            'event': 'response_received',
            'response_id': 'resp-1',
            'input_tokens': 1000,
            'uncached_input_tokens': 0,
            'cached_input_tokens': 100,
            'cache_write_30m_tokens': 900,
            'cache_write_12h_tokens': 0,
            'output_tokens': 50,
            'estimated_cost_usd': cost,
        }]
        rows = [{
            'Response ID': 'resp-1',
            'Billed': 'Yes',
            'Input Tokens': '1000',
            'Uncached Input Tokens': '0',
            'Cached Input Tokens': '100',
            'Cache Write 30m Tokens': '900',
            'Cache Write 12h Tokens': '0',
            'Output Tokens': '50',
            'Estimated Cost USD': cost,
        }]
        self.assertTrue(reconcile_usage(diagnostics=diagnostics, register_rows=rows)['ok'])
        missing = reconcile_usage(diagnostics=diagnostics, register_rows=[])
        self.assertEqual(missing['missing_from_register'], ['resp-1'])
        self.assertFalse(missing['ok'])


if __name__ == '__main__':
    unittest.main()
