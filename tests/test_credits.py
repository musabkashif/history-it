import json
import tempfile
import unittest
from pathlib import Path

from app.credits import CreditShortageError, as_credit_error, credit_alert, is_credit_shortage
from app.inspection import Inspector
from app.orchestrator import Orchestrator


class MemorySheets:
    def __init__(self):
        self.store = {}

    def rows(self, sid, sheet):
        return [dict(row) for row in self.store.get((sid, sheet), [])]

    def write(self, sid, sheet, row, key=None):
        rows = self.store.setdefault((sid, sheet), [])
        if key:
            for existing in rows:
                if str(existing.get(key[0])) == str(key[1]):
                    existing.update({name: str(value) for name, value in row.items()})
                    return 1
        rows.append({name: str(value) for name, value in row.items()})
        return len(rows)

    def field(self, sid, name):
        return 'None'


class Settings:
    job_index_spreadsheet_id = 'index'
    job_spending_limit_usd = 100
    max_retries = 1
    openai_model = 'gpt-test'
    openai_input_usd_per_million = 1
    openai_cached_input_usd_per_million = 0
    openai_output_usd_per_million = 1


def batch(bid):
    manifest = [{
        'id': bid,
        'name': bid + '.jpg',
        'folder_id': 'folder',
        'folder_name': 'F01',
        'path': '/' + bid + '.jpg',
        'mime_type': 'image/jpeg',
    }]
    return {
        'Project': 'Tri Delta',
        'Job ID': 'HIT-1',
        'Batch ID': bid,
        'Batch Status': 'Authorized / Pending Inspection',
        'Inspection Attempt': '0',
        'Planned Asset Count': '1',
        'Notes': json.dumps({'manifest': manifest}),
        'Error / Retry Note': '',
    }


def quota_error():
    error = RuntimeError('You exceeded your current quota, please check your plan and billing details.')
    error.status_code = 429
    error.code = 'insufficient_quota'
    error.body = {'error': {'message': str(error), 'type': 'insufficient_quota', 'code': 'insufficient_quota'}}
    return error


class CreditDetectionTests(unittest.TestCase):
    def test_quota_and_billing_are_credit_shortages(self):
        self.assertTrue(is_credit_shortage(quota_error()))
        billing = RuntimeError('billing hard limit reached')
        billing.status_code = 402
        self.assertTrue(is_credit_shortage(billing))

    def test_rate_limit_is_not_a_credit_shortage(self):
        error = RuntimeError('Rate limit reached for requests')
        error.status_code = 429
        error.code = 'rate_limit_exceeded'
        self.assertFalse(is_credit_shortage(error))
        self.assertIs(as_credit_error(error), error)

    def test_quota_error_becomes_credit_error(self):
        converted = as_credit_error(quota_error())
        self.assertIsInstance(converted, CreditShortageError)


class InspectionCreditTests(unittest.TestCase):
    def test_inspect_stops_with_credit_error(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'rules.md').write_text('Govern the inspection.', encoding='utf-8')

            class LocalSettings:
                governing_docs_dir = folder
                openai_api_key = 'test'
                openai_model = 'gpt-test'

            class Drive:
                def image(self, source):
                    return b'image-bytes'

            class Client:
                def __init__(self):
                    self.responses = self

                def parse(self, **kwargs):
                    raise quota_error()

            class SourceFile:
                name = 'a.jpg'
                id = 'file-1'
                mime_type = 'image/jpeg'

            inspector = Inspector(LocalSettings(), Drive(), client=Client())
            with self.assertRaises(CreditShortageError):
                inspector.inspect('HIT-1', 'HIT-1-B0001', [SourceFile()], {}, [])


class OrchestratorCreditTests(unittest.TestCase):
    def setUp(self):
        self.sheets = MemorySheets()
        self.sheets.store[('index', 'HistoryIT Jobs')] = [{'Job ID': 'HIT-1', 'Spreadsheet ID': 'register'}]
        self.sheets.store[('register', 'Batch Queue')] = [batch('HIT-1-B0001'), batch('HIT-1-B0002')]
        self.calls = []

        class Inspector:
            def inspect(inner, job, batch_id, sources, context, rules):
                self.calls.append(batch_id)
                error = CreditShortageError(credit_alert(job))
                error.elapsed_seconds = 0.2
                raise error

        self.orchestrator = Orchestrator(self.sheets, None, Inspector(), Settings())

    def queue(self):
        return {row['Batch ID']: row for row in self.sheets.rows('register', 'Batch Queue')}

    def test_credit_shortage_stops_without_using_a_retry(self):
        self.orchestrator.run('HIT-1')
        rows = self.queue()
        self.assertEqual(self.calls, ['HIT-1-B0001'])
        self.assertEqual(rows['HIT-1-B0001']['Batch Status'], 'Blocked - Human Review')
        self.assertEqual(rows['HIT-1-B0001']['Inspection Attempt'], '0')
        self.assertTrue(rows['HIT-1-B0001']['Error / Retry Note'].startswith('OpenAI account is out of credits.'))
        self.assertIn('/api/jobs/HIT-1/resume', rows['HIT-1-B0001']['Error / Retry Note'])
        self.assertEqual(rows['HIT-1-B0002']['Batch Status'], 'Authorized / Pending Inspection')
        usage = self.sheets.rows('register', 'OpenAI Usage')
        self.assertEqual(usage[0]['Status'], 'Blocked - OpenAI Credits')
        state = self.orchestrator.state('HIT-1')
        self.assertEqual(state['status'], 'Blocked - Human Review')
        self.assertEqual(state['alert'], rows['HIT-1-B0001']['Error / Retry Note'])

        self.orchestrator.run('HIT-1')
        self.assertEqual(self.calls, ['HIT-1-B0001'])
        self.assertNotEqual(self.queue()['HIT-1-B0001']['Error / Retry Note'], 'Inspection retry limit reached')

    def test_resume_clears_only_the_credit_block(self):
        self.orchestrator.run('HIT-1')
        started = []
        self.orchestrator.start = lambda job: started.append(job) or self.orchestrator.state(job)
        state = self.orchestrator.resume('HIT-1')
        self.assertEqual(started, ['HIT-1'])
        self.assertEqual(self.queue()['HIT-1-B0001']['Batch Status'], 'Authorized / Pending Inspection')
        self.assertEqual(self.queue()['HIT-1-B0001']['Error / Retry Note'], '')
        self.assertIsNone(state['alert'])

    def test_resume_leaves_spending_block_in_place(self):
        self.sheets.store[('register', 'Batch Queue')][0]['Batch Status'] = 'Blocked - Human Review'
        self.sheets.store[('register', 'Batch Queue')][0]['Error / Retry Note'] = 'Job spending limit reached'
        self.orchestrator.start = lambda job: self.orchestrator.state(job)
        state = self.orchestrator.resume('HIT-1')
        self.assertEqual(self.queue()['HIT-1-B0001']['Batch Status'], 'Blocked - Human Review')
        self.assertEqual(state['alert'], 'Job spending limit reached')

    def test_rate_limit_stays_retryable(self):
        class Inspector:
            def inspect(inner, job, batch_id, sources, context, rules):
                self.calls.append(batch_id)
                error = RuntimeError('Rate limit reached for requests')
                error.status_code = 429
                error.elapsed_seconds = 0.1
                raise error

        self.orchestrator.inspector = Inspector()
        self.orchestrator.run('HIT-1')
        row = self.queue()['HIT-1-B0001']
        self.assertEqual(row['Batch Status'], 'Inspection Failed - Retryable')
        self.assertEqual(row['Inspection Attempt'], '1')
        self.assertIsNone(self.orchestrator.state('HIT-1')['alert'])


if __name__ == '__main__':
    unittest.main()
