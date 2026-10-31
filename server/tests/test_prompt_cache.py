import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.decisions import decision_instructions
from app.inspection import Inspector, OUTPUT_SCHEMA
from app.models import Source
from app.config import Settings
from app.models import InspectionOutput
from app.prompt_cache import (
    FLAG_NAME,
    REQUEST_SHAPE_OPTIMIZED,
    REQUEST_SHAPE_SYSTEM,
    build_inspection_request,
    cache_options,
    dynamic_message,
    governing_instruction,
    message_text,
    prefix_fingerprint,
    prompt_cache_optimization_enabled,
    stable_message,
)
from app.usage_accounting import parse_provider_usage, usage_categories_complete, usage_cost, UsageAccountingError


class Rates:
    openai_input_usd_per_million = 4
    openai_cached_input_usd_per_million = 0.4
    openai_cache_write_30m_usd_per_million = 5
    openai_cache_write_12h_usd_per_million = 10
    openai_output_usd_per_million = 20


def _jpeg():
    out = BytesIO()
    Image.new('RGB', (8, 8), 'white').save(out, format='JPEG')
    return out.getvalue()


def _source(name):
    return Source(id='drive-' + name, name=name, folder_id='folder', folder_name='F01', path=name, mime_type='image/jpeg')


class _Drive:
    def image(self, _source):
        return _jpeg()


class _Settings:
    def __init__(self, directory, *, openai_prompt_cache_optimization_enabled=False):
        self.governing_docs_dir = directory
        self.diagnostics_dir = directory
        self.openai_model = 'gpt-5.6-sol'
        self.openai_max_output_tokens = 1000
        self.openai_reasoning_effort = 'high'
        self.openai_input_usd_per_million = 4
        self.openai_cached_input_usd_per_million = 0.4
        self.openai_cache_write_30m_usd_per_million = 0
        self.openai_cache_write_12h_usd_per_million = 0
        self.openai_output_usd_per_million = 20
        self.openai_prompt_cache_optimization_enabled = openai_prompt_cache_optimization_enabled


class _Client:
    def __init__(self):
        self.responses = self
        self.requests = []

    def parse(self, **request):
        self.requests.append(request)
        raise RuntimeError('captured')


class FlaggedRequestTests(unittest.TestCase):
    def test_settings_default_keeps_cache_optimization_off(self):
        self.assertFalse(Settings().openai_prompt_cache_optimization_enabled)
        self.assertFalse(prompt_cache_optimization_enabled(Settings()))
        self.assertEqual(FLAG_NAME, 'openai_prompt_cache_optimization_enabled')

    def test_default_inspection_uses_system_role_without_cache_options(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'rules.md').write_text('Inspect every image.\n', encoding='utf-8')
            client = _Client()
            inspector = Inspector(_Settings(directory), _Drive(), client=client)
            with self.assertRaisesRegex(RuntimeError, 'captured'):
                inspector.inspect('JOB', 'B0001', [_source('one.jpg')], {'inspection_attempt': 1, 'prior_open_item': 'None'}, [])
            request = client.requests[0]
            prepared = json.loads(Path(directory, 'JOB_B0001_attempt-1.json').read_text(encoding='utf-8'))[0]
        self.assertEqual(request['input'][0]['role'], 'system')
        self.assertIsInstance(request['input'][0]['content'], str)
        self.assertNotIn('prompt_cache_options', request)
        self.assertIs(request['text_format'], InspectionOutput)
        self.assertEqual(request['reasoning'], {'effort': 'high'})
        self.assertEqual(request['max_output_tokens'], 1000)
        self.assertFalse(prepared[FLAG_NAME])
        self.assertEqual(prepared['request_shape'], REQUEST_SHAPE_SYSTEM)

    def test_optimized_shape_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'rules.md').write_text('Inspect every image.\n', encoding='utf-8')
            client = _Client()
            settings = _Settings(directory, openai_prompt_cache_optimization_enabled=True)
            inspector = Inspector(settings, _Drive(), client=client)
            with self.assertRaisesRegex(RuntimeError, 'captured'):
                inspector.inspect('JOB', 'B0001', [_source('one.jpg')], {'inspection_attempt': 1, 'prior_open_item': 'None'}, [])
            request = client.requests[0]
            prepared = json.loads(Path(directory, 'JOB_B0001_attempt-1.json').read_text(encoding='utf-8'))[0]
        self.assertEqual(request['input'][0]['role'], 'developer')
        self.assertEqual(request['input'][0]['content'][0]['prompt_cache_breakpoint'], {'mode': 'explicit'})
        self.assertEqual(request['prompt_cache_options'], cache_options())
        self.assertTrue(prepared[FLAG_NAME])
        self.assertEqual(prepared['request_shape'], REQUEST_SHAPE_OPTIMIZED)

    def test_rollback_disabling_flag_restores_system_role(self):
        instruction = 'Govern the inspection.'
        payload = {'batch_id': 'B0001'}
        images = [{'type': 'input_image', 'detail': 'high'}]
        optimized = build_inspection_request(
            model='gpt-5.6-sol', instruction=instruction, payload=payload, images=images,
            text_format=InspectionOutput, optimized=True, max_output_tokens=1000, reasoning_effort='high',
        )
        rolled_back = build_inspection_request(
            model='gpt-5.6-sol', instruction=instruction, payload=payload, images=images,
            text_format=InspectionOutput, optimized=False, max_output_tokens=1000, reasoning_effort='high',
        )
        self.assertEqual(optimized['input'][0]['role'], 'developer')
        self.assertIn('prompt_cache_options', optimized)
        self.assertEqual(rolled_back['input'][0]['role'], 'system')
        self.assertEqual(rolled_back['input'][0]['content'], instruction)
        self.assertNotIn('prompt_cache_options', rolled_back)
        self.assertIs(optimized['text_format'], rolled_back['text_format'])
        self.assertEqual(optimized['model'], rolled_back['model'])
        self.assertEqual(optimized['reasoning'], rolled_back['reasoning'])
        self.assertEqual(optimized['max_output_tokens'], rolled_back['max_output_tokens'])
        self.assertEqual(optimized['input'][1], rolled_back['input'][1])

    def test_unknown_cache_write_stays_unknown_under_either_shape(self):
        record = parse_provider_usage(
            {'input_tokens': 1000, 'output_tokens': 10, 'input_tokens_details': {'cached_tokens': 100, 'cache_write_tokens': 900}},
            response_id='resp-flag', attempt=1, part=1, status='Received',
        )
        self.assertEqual(record['cache_write_retention'], 'unknown')
        self.assertFalse(usage_categories_complete(record))
        with self.assertRaises(UsageAccountingError):
            usage_cost(record, Rates())


class PrefixTests(unittest.TestCase):
    def _inspect(self, directory, batch, name, context):
        client = _Client()
        settings = _Settings(directory, openai_prompt_cache_optimization_enabled=True)
        inspector = Inspector(settings, _Drive(), client=client)
        with self.assertRaisesRegex(RuntimeError, 'captured'):
            inspector.inspect('JOB', batch, [_source(name)], context, [])
        return client.requests[0]

    def test_stable_prefix_matches_across_batches_and_excludes_dynamic_data(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'rules.md').write_text('Inspect every image.\n', encoding='utf-8')
            first_context = {
                'inspection_attempt': 1,
                'folder_code': 'F01',
                'prior_open_item': 'None',
                'project': {'name': 'Project A'},
                'approved_project_reference': {'reference_id': 'cal-a', 'version': '1'},
            }
            second_context = {
                'inspection_attempt': 1,
                'folder_code': 'F02',
                'prior_open_item': 'ITEM-2',
                'project': {'name': 'Project B'},
                'approved_project_reference': {'reference_id': 'cal-b', 'version': '2', 'note': 'other box'},
            }
            first = self._inspect(directory, 'B0001', 'one.jpg', first_context)
            second = self._inspect(directory, 'B0002', 'two.jpg', second_context)
        left = message_text(first['input'][0]['content'])
        right = message_text(second['input'][0]['content'])
        self.assertEqual(left, right)
        self.assertEqual(prefix_fingerprint(left), prefix_fingerprint(right))
        self.assertEqual(first['input'][0]['content'][0]['prompt_cache_breakpoint'], {'mode': 'explicit'})
        user = second['input'][1]['content'][0]['text']
        self.assertIn('B0002', user)
        self.assertIn('two.jpg', user)
        self.assertIn('Project B', user)
        self.assertIn('cal-b', user)
        self.assertNotIn('B0002', left)
        self.assertNotIn('two.jpg', left)
        self.assertNotIn('Project B', left)
        self.assertNotIn('cal-b', left)
        self.assertNotIn('prompt_cache_breakpoint', second['input'][1]['content'][0])
        self.assertEqual(second['input'][1]['content'][2]['detail'], 'high')

    def test_governing_context_change_invalidates_the_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'rules.md')
            path.write_text('Inspect every image.\n', encoding='utf-8')
            context = {'inspection_attempt': 1, 'prior_open_item': 'None'}
            before = message_text(self._inspect(directory, 'B0001', 'one.jpg', context)['input'][0]['content'])
            path.write_text('Inspect every image.\nNew governing rule.\n', encoding='utf-8')
            after = message_text(self._inspect(directory, 'B0001', 'one.jpg', context)['input'][0]['content'])
        self.assertNotEqual(prefix_fingerprint(before), prefix_fingerprint(after))
        self.assertIn('New governing rule.', after)

    def test_instruction_order_and_schema_text_stay_in_the_prefix(self):
        text = governing_instruction('RUNTIME', ['guide.md\nKeep source order.'], OUTPUT_SCHEMA, decision_instructions())
        self.assertLess(text.index('canonical inspection workflow'), text.index('RUNTIME'))
        self.assertLess(text.index('RUNTIME'), text.index('guide.md'))
        self.assertLess(text.index('guide.md'), text.index('Structured output schema only'))
        self.assertLess(text.index('Structured output schema only'), text.index(decision_instructions()[:40]))
        message = stable_message(text)
        self.assertEqual(message_text(message['content']), text)

    def test_request_uses_the_supported_cache_options_only(self):
        import typing
        from openai.types.responses.response_create_params import PromptCacheOptions
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'rules.md').write_text('Inspect every image.\n', encoding='utf-8')
            request = self._inspect(directory, 'B0001', 'one.jpg', {'inspection_attempt': 1, 'prior_open_item': 'None'})
        self.assertEqual(request['prompt_cache_options'], cache_options())
        self.assertEqual(request['prompt_cache_options'], {'mode': 'explicit', 'ttl': '30m'})
        allowed = typing.get_args(typing.get_type_hints(PromptCacheOptions)['ttl'])
        self.assertIn(request['prompt_cache_options']['ttl'], allowed)
        self.assertNotIn('prompt_cache_key', request)
        self.assertIs(request['text_format'], __import__('app.models', fromlist=['InspectionOutput']).InspectionOutput)
        self.assertEqual(request['reasoning'], {'effort': 'high'})
        payload = json.loads(dynamic_message({'batch_id': 'B0001'}, [])['content'][0]['text'])
        self.assertEqual(payload['batch_id'], 'B0001')


class AccountingTests(unittest.TestCase):
    def test_unscoped_write_stays_unknown_when_the_request_asked_for_30m(self):
        record = parse_provider_usage(
            {'input_tokens': 1000, 'output_tokens': 10, 'input_tokens_details': {'cached_tokens': 100, 'cache_write_tokens': 900}},
            response_id='resp-1', attempt=1, part=1, status='Received',
        )
        self.assertEqual(record['cache_write_retention'], 'unknown')
        self.assertEqual(record['cache_write_30m_tokens'], '')
        self.assertFalse(usage_categories_complete(record))
        with self.assertRaises(UsageAccountingError):
            usage_cost(record, Rates())

    def test_named_30m_is_priced_and_24h_stays_unknown(self):
        named = parse_provider_usage(
            {'input_tokens': 1000, 'output_tokens': 10, 'input_tokens_details': {'cached_tokens': 100, 'cache_write_30m_tokens': 900}},
            response_id='resp-30', attempt=1, part=1, status='Received',
        )
        self.assertEqual(named['cache_write_retention'], '30m')
        self.assertTrue(usage_categories_complete(named))
        self.assertEqual(usage_cost(named, Rates()), '0.004740')
        day = parse_provider_usage(
            {'input_tokens': 1000, 'output_tokens': 10, 'input_tokens_details': {'cached_tokens': 0, 'cache_write_24h_tokens': 1000}},
            response_id='resp-24', attempt=1, part=1, status='Received',
        )
        self.assertEqual(day['cache_write_retention'], '24h')
        self.assertFalse(usage_categories_complete(day))
        with self.assertRaises(UsageAccountingError):
            usage_cost(day, Rates())


class TimeoutTests(unittest.TestCase):
    def test_timeout_is_not_labelled_unbilled_and_is_not_retried(self):
        class TimeoutErrorClient:
            def __init__(self):
                self.responses = self
                self.calls = 0

            def parse(self, **_request):
                self.calls += 1
                raise TimeoutError('read timed out')

        class APITimeout(Exception):
            pass

        APITimeout.__name__ = 'APITimeoutError'

        class APITimeoutClient:
            def __init__(self):
                self.responses = self
                self.calls = 0

            def parse(self, **_request):
                self.calls += 1
                raise APITimeout('request timed out')

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'rules.md').write_text('Inspect every image.\n', encoding='utf-8')
            for client, label in ((TimeoutErrorClient(), 'timeout'), (APITimeoutClient(), 'api')):
                inspector = Inspector(_Settings(directory), _Drive(), client=client)
                inspector._sleep = lambda _seconds: None
                with self.assertRaises(Exception):
                    inspector.inspect('JOB', 'BATCH', [_source('a.jpg')], {'inspection_attempt': 1}, [])
                events = json.loads(Path(directory, 'JOB_BATCH_attempt-1.json').read_text(encoding='utf-8'))
                unknown = [event for event in events if event['event'] == 'usage_outcome_unknown']
                unbilled = [event for event in events if event['event'] == 'usage_unbilled']
                self.assertEqual(client.calls, 1, label)
                self.assertEqual(len(unknown), 1, label)
                self.assertIsNone(unknown[0]['billed'])
                self.assertEqual(unknown[0]['outcome'], 'unknown')
                self.assertEqual(unbilled, [], label)
                self.assertNotIn('input_tokens', unknown[0])
                Path(directory, 'JOB_BATCH_attempt-1.json').unlink()
