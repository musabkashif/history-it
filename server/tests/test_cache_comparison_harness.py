import json
import subprocess
import unittest
from decimal import Decimal
from pathlib import Path

from app.decisions import decision_instructions
from app.inspection import OUTPUT_SCHEMA
from app.prompt_cache import governing_instruction, message_text
from harness.cache_comparison import (
    ACCEPTANCE_RULE,
    COMPARISON_APPLICATION_ESTIMATE_RATES,
    INCOMPLETE_CLEAN_RUN,
    MAX_CALLS,
    MAX_SPEND,
    PROMPT_CACHE_KEY_MAX_LEN,
    PROMPT_CACHE_KEY_PREFIX,
    QUALITY,
    STAGING_COMMIT,
    ComparisonHarness,
    arm_prompt_cache_key,
    assert_prompt_cache_key_allowed,
    baseline_instruction,
    baseline_request,
    comparison_payload,
    deployed_contract_from_source,
    EvidenceLog,
    comparison_sequence,
    dispatch_recorded_calls,
    evaluate_clean_acceptance,
    json_safe,
    load_fixture_expectations,
    optimized_request,
    prepare,
    record_observation,
    reservations_allow_generation,
    savings_are_inconclusive,
    verify_prepared_images,
    with_comparison_tier,
)


ROOT = Path(__file__).resolve().parents[2]
DEPLOYED = 'ee1f7d44436e824eef9290a08db9269e093e716e'


def deployed_source():
    return subprocess.check_output(
        ['git', 'show', f'{DEPLOYED}:server/app/inspection.py'],
        cwd=ROOT,
        text=True,
    )


class DeployedShapeTests(unittest.TestCase):
    def setUp(self):
        self.source = deployed_source()
        self.contract = deployed_contract_from_source(self.source)

    def test_baseline_matches_the_deployed_commit_and_not_the_optimized_arm(self):
        self.assertTrue(self.contract['preamble'].startswith('The client runtime prompt below is the canonical inspection workflow.'))
        instruction = baseline_instruction(self.contract, 'RUNTIME', ['guide.md\nKeep order.'], OUTPUT_SCHEMA, decision_instructions())
        self.assertEqual(
            instruction,
            governing_instruction('RUNTIME', ['guide.md\nKeep order.'], OUTPUT_SCHEMA, decision_instructions()),
        )
        payload = comparison_payload('JOB', 'B0001', {'folder_code': 'F04', 'prior_open_item': 'None'}, [], [], {'reference_id': 'disposable'})
        self.assertEqual(list(payload), self.contract['payload_keys'])
        images = [{'type': 'input_image', 'image_url': 'data:image/jpeg;base64,YQ==', 'detail': 'high'}]
        baseline = baseline_request(instruction, payload, images, QUALITY['model'], QUALITY['max_output_tokens'], QUALITY['reasoning_effort'])
        optimized = optimized_request(instruction, payload, images, QUALITY['model'], QUALITY['max_output_tokens'], QUALITY['reasoning_effort'])
        self.assertEqual(baseline['input'][0]['role'], 'system')
        self.assertEqual(baseline['input'][0]['content'], instruction)
        self.assertNotIn('prompt_cache_options', baseline)
        self.assertEqual(optimized['input'][0]['role'], 'developer')
        self.assertEqual(message_text(optimized['input'][0]['content']), instruction)
        self.assertEqual(optimized['input'][0]['content'][0]['prompt_cache_breakpoint'], {'mode': 'explicit'})
        self.assertEqual(baseline['input'][1], optimized['input'][1])
        self.assertEqual(baseline['model'], optimized['model'])
        self.assertEqual(baseline['reasoning'], optimized['reasoning'])
        self.assertEqual(baseline['max_output_tokens'], optimized['max_output_tokens'])
        self.assertIs(baseline['text_format'], optimized['text_format'])
        self.assertNotIn('TriDelta', baseline['input'][1]['content'][0]['text'])

    def test_normal_inspection_cannot_select_a_comparison_arm(self):
        source = (ROOT / 'server' / 'app' / 'inspection.py').read_text(encoding='utf-8')
        orchestrator = (ROOT / 'server' / 'app' / 'orchestrator.py').read_text(encoding='utf-8')
        config = (ROOT / 'server' / 'app' / 'config.py').read_text(encoding='utf-8')
        self.assertNotIn('cache_comparison', source)
        self.assertNotIn('ComparisonHarness', source)
        self.assertNotIn('service_tier', source)
        self.assertNotIn('cache_comparison', orchestrator)
        self.assertIn('job_spending_limit_usd', orchestrator)
        self.assertIn('job_spending_limit_usd: float = Field(default=100, ge=0)', config)
        self.assertIn('CONNECTION_ATTEMPTS = 4', source)

    def test_quality_settings_cannot_be_lowered_to_fit_the_budget(self):
        with self.assertRaises(ValueError):
            baseline_request('text', {}, [], QUALITY['model'], 1000, QUALITY['reasoning_effort'])


class BudgetTests(unittest.TestCase):
    def test_real_fixture_is_blocked_inside_twenty_dollars(self):
        plan = prepare(deployed_source(), json.loads((ROOT / 'server' / 'project_references' / 'tri-delta-chapters-box31-f04-calibration.json').read_text(encoding='utf-8')))
        self.assertFalse(plan['ready'])
        self.assertFalse(plan['executed'])
        self.assertEqual(plan['paid_calls_made'], 0)
        self.assertFalse(plan['count_endpoint_called'])
        self.assertFalse(plan['input_count']['called'])
        self.assertEqual(plan['input_count']['billing'], 'not documented')
        self.assertEqual(plan['staging_commit_unchanged'], STAGING_COMMIT)
        self.assertEqual(plan['pricing_inputs']['label'], 'application_estimate_not_invoice_charge')
        self.assertEqual(plan['isolation_method']['mechanism'], 'prompt_cache_key per arm under a fresh run id')
        self.assertFalse(plan['failed_26_oct_run']['reuse'])
        self.assertIn('application estimates', plan['authorization_needed'])
        self.assertFalse(plan['input_count']['known_charge'])
        self.assertIsNone(plan['input_tokens'])
        self.assertEqual(plan['maximum_reserved_spend_usd'], Decimal('20'))
        self.assertEqual(plan['comparison_service_tier'], 'default')
        pinned_baseline = with_comparison_tier(baseline_request('text', {}, [], QUALITY['model'], QUALITY['max_output_tokens'], QUALITY['reasoning_effort']))
        pinned_optimized = with_comparison_tier(optimized_request('text', {}, [], QUALITY['model'], QUALITY['max_output_tokens'], QUALITY['reasoning_effort']))
        self.assertEqual(pinned_baseline['service_tier'], pinned_optimized['service_tier'])
        self.assertEqual(plan['max_calls'], 6)
        self.assertEqual(plan['max_spend_usd'], Decimal('20'))
        self.assertEqual(plan['quality']['image_detail'], 'high')
        self.assertEqual(plan['quality']['max_output_tokens'], 60000)
        self.assertIn('system', plan['role_difference'])
        self.assertIn('developer', plan['role_difference'])
        self.assertFalse(plan['writes_registers'])

    def test_spend_and_call_limits_stop_and_a_failure_is_not_replaced(self):
        guard = ComparisonHarness()
        self.assertTrue(guard.reserve(Decimal('4')))
        self.assertTrue(guard.reserve(Decimal('4')))
        self.assertTrue(guard.reserve(Decimal('4')))
        self.assertTrue(guard.reserve(Decimal('4')))
        self.assertTrue(guard.reserve(Decimal('4')))
        self.assertFalse(guard.reserve(Decimal('4')))
        self.assertEqual(guard.reserved, Decimal('20'))
        calls = ComparisonHarness()
        for _ in range(MAX_CALLS):
            self.assertTrue(calls.reserve(Decimal('1')))
        self.assertFalse(calls.reserve(Decimal('1')))
        self.assertEqual(calls.calls, MAX_CALLS)

        class Client:
            max_retries = 0
            def __init__(self):
                self.responses = self
                self.calls = 0
            def parse(self, **_request):
                self.calls += 1
                raise TimeoutError('timed out')

        client = Client()
        stopped = ComparisonHarness()
        valid_request = {
            'model': 'gpt-5.6-sol',
            'prompt_cache_key': arm_prompt_cache_key('clean-limit-test', 'optimized'),
        }
        with self.assertRaises(RuntimeError):
            stopped.execute(client, [valid_request], [Decimal('1')])
        self.assertEqual(client.calls, 0)
        result = stopped.execute(client, [valid_request], [Decimal('1')], allow_paid_calls=True)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result['reserved'], Decimal('1'))
        self.assertIn('stops the run', result['stopped'])
        blocked = ComparisonHarness()
        blocked.execute(client, [{}] * 6, [Decimal('4')] * 6, allow_paid_calls=True)
        self.assertEqual(client.calls, 1)
        self.assertFalse(reservations_allow_generation([Decimal('4')] * 6))
        self.assertIn('No generation call was made', blocked.stopped)

    def test_expectations_name_the_reviewed_local_frames(self):
        calibration = json.loads((ROOT / 'server' / 'project_references' / 'tri-delta-chapters-box31-f04-calibration.json').read_text(encoding='utf-8'))
        expectations = load_fixture_expectations(calibration)
        self.assertEqual(expectations['calibration_scope']['version'], '2026-10-24.1')
        self.assertTrue(expectations['calibration_scope']['not_an_executable_rule'])
        self.assertEqual(expectations['images'][0]['expected']['record_kind'], 'Metadata Only')
        self.assertEqual(expectations['images'][0]['expected']['production_tasks'], 'none')
        self.assertEqual(expectations['images'][1]['expected']['action'], 'Archival Border Crop')
        self.assertEqual(expectations['images'][2]['expected']['action'], 'Rotate + Archival Border Crop')
        self.assertIn('180', expectations['images'][2]['expected']['note_must_include'])
        self.assertTrue(expectations['images'][2]['source_filename'].endswith('Photograph_016b.jpg'))
        fixture = json.loads((ROOT / 'server' / 'harness' / 'fixtures' / 'disposable_expectations.json').read_text(encoding='utf-8'))
        self.assertFalse(fixture['writes_client_registers'])
        self.assertEqual(
            [item['source_filename'] for item in fixture['images']],
            [item['source_filename'] for item in expectations['images']],
        )
        self.assertNotIn('disp-', json.dumps(fixture))
        self.assertEqual(MAX_SPEND, Decimal('20'))

    def test_sequence_keeps_one_prefix_and_matching_images(self):
        calibration = {'reference_id': 'tri-delta-chapters-box31-f04-calibration', 'version': '2026-10-24.1'}
        parts = {
            'TriDelta_Year2_Chapters_Box31_F04_004.jpg': [{'type': 'input_image', 'image_url': 'data:image/jpeg;base64,YQ==', 'detail': 'high'}],
            'TriDelta_Year2_Chapters_Box31_F04_002.jpg': [{'type': 'input_image', 'image_url': 'data:image/jpeg;base64,Yg==', 'detail': 'high'}],
            'TriDelta_Year2_Chapters_Box31_F04_Photograph_016b.jpg': [{'type': 'input_image', 'image_url': 'data:image/jpeg;base64,Yw==', 'detail': 'high'}],
        }
        steps = comparison_sequence('SAME GOVERNING TEXT', calibration, parts, run_id='clean-test-isolation-0001')
        self.assertEqual([step['arm'] for step in steps], ['optimized', 'optimized', 'optimized', 'baseline', 'baseline', 'baseline'])
        self.assertEqual([step['warm_up'] for step in steps], [True, False, False, True, False, False])
        self.assertTrue(all(step['included_in_arm_total'] for step in steps))
        self.assertEqual(len({step['governing_fingerprint'] for step in steps}), 1)
        for offset in range(3):
            self.assertEqual(steps[offset]['source_filename'], steps[offset + 3]['source_filename'])
            self.assertEqual(steps[offset]['request']['input'][1], steps[offset + 3]['request']['input'][1])
            self.assertEqual(steps[offset]['request']['service_tier'], steps[offset + 3]['request']['service_tier'])
        self.assertEqual(steps[0]['request']['prompt_cache_key'], arm_prompt_cache_key('clean-test-isolation-0001', 'optimized'))
        self.assertEqual(steps[3]['request']['prompt_cache_key'], arm_prompt_cache_key('clean-test-isolation-0001', 'baseline'))
        self.assertNotEqual(steps[0]['request']['prompt_cache_key'], steps[3]['request']['prompt_cache_key'])
        self.assertNotIn('prompt_cache_options', steps[3]['request'])
        self.assertIn('prompt_cache_options', steps[0]['request'])

    def test_arm_cache_namespaces_never_share_a_key(self):
        left = arm_prompt_cache_key('clean-a', 'optimized')
        right = arm_prompt_cache_key('clean-a', 'baseline')
        other = arm_prompt_cache_key('clean-b', 'optimized')
        self.assertNotEqual(left, right)
        self.assertNotEqual(left, other)
        self.assertNotIn('TriDelta', left)
        self.assertTrue(left.startswith(PROMPT_CACHE_KEY_PREFIX + '-'))
        self.assertLessEqual(len(left), PROMPT_CACHE_KEY_MAX_LEN)
        self.assertLessEqual(len(right), PROMPT_CACHE_KEY_MAX_LEN)

    def test_prompt_cache_keys_are_short_deterministic_and_run_scoped(self):
        run = 'clean-20261029T174759Z-d99cf263'
        optimized = arm_prompt_cache_key(run, 'optimized')
        baseline = arm_prompt_cache_key(run, 'baseline')
        self.assertNotEqual(optimized, baseline)
        self.assertLessEqual(len(optimized), PROMPT_CACHE_KEY_MAX_LEN)
        self.assertLessEqual(len(baseline), PROMPT_CACHE_KEY_MAX_LEN)
        self.assertEqual(optimized, arm_prompt_cache_key(run, 'optimized'))
        self.assertEqual(baseline, arm_prompt_cache_key(run, 'baseline'))
        other_run = arm_prompt_cache_key('clean-20261029T180000Z-aaaaaaaa', 'optimized')
        self.assertNotEqual(optimized, other_run)
        self.assertNotEqual(optimized, arm_prompt_cache_key(INCOMPLETE_CLEAN_RUN['id'], 'baseline'))
        assert_prompt_cache_key_allowed({'prompt_cache_key': optimized})
        with self.assertRaises(RuntimeError):
            assert_prompt_cache_key_allowed({'prompt_cache_key': 'x' * 65})

    def test_invalid_prompt_cache_key_stops_before_provider_call(self):
        class Client:
            def __init__(self):
                self.calls = 0
                self.max_retries = 0
                self.responses = self

            def parse(self, **_kwargs):
                self.calls += 1
                raise AssertionError('provider call must not run')

        client = Client()
        harness = ComparisonHarness()
        request = {'prompt_cache_key': 'x' * (PROMPT_CACHE_KEY_MAX_LEN + 1)}
        result = harness.execute(client, [request], [Decimal('0.10')], allow_paid_calls=True)
        self.assertEqual(client.calls, 0)
        self.assertEqual(result['calls'], 0)
        self.assertIn('exceeds', harness.stopped)
        self.assertFalse(INCOMPLETE_CLEAN_RUN['reuse'])
        self.assertEqual(INCOMPLETE_CLEAN_RUN['status'], 'incomplete_unknown_outcome')

    def test_unknown_or_overlapping_cost_is_inconclusive(self):
        self.assertTrue(savings_are_inconclusive(
            {'application_estimate_usd': None, 'conservative_bound_usd': Decimal('2')},
            {'application_estimate_usd': Decimal('1'), 'conservative_bound_usd': Decimal('1')},
        ))
        self.assertTrue(savings_are_inconclusive(
            {'application_estimate_usd': Decimal('1'), 'conservative_bound_usd': Decimal('3')},
            {'application_estimate_usd': Decimal('2'), 'conservative_bound_usd': Decimal('2')},
        ))
        self.assertFalse(savings_are_inconclusive(
            {'application_estimate_usd': Decimal('1'), 'conservative_bound_usd': Decimal('1')},
            {'application_estimate_usd': Decimal('2'), 'conservative_bound_usd': Decimal('2')},
        ))

    def test_observation_keeps_an_unscoped_write_unknown(self):
        class Response:
            id = 'resp_test'
            service_tier = 'default'
            usage = {
                'input_tokens': 100,
                'output_tokens': 10,
                'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 40},
            }
        step = {'warm_up': True, 'observation': {}}
        observed = record_observation(step, Response(), 'start', 'end')
        self.assertEqual(observed['response_id'], 'resp_test')
        self.assertEqual(observed['service_tier'], 'default')
        self.assertEqual(observed['parsed_usage']['cache_write_tokens'], 40)
        self.assertEqual(observed['parsed_usage']['uncached_input_tokens'], 60)
        self.assertEqual(observed['attempt'], 1)
        self.assertTrue(observed['billed'])
        self.assertIsNone(observed['application_estimate_usd'])
        self.assertIsNotNone(observed['conservative_bound_usd'])
        self.assertIsNone(observed['contamination'])
        self.assertFalse(observed['cost_known'])

    def test_observation_stores_named_ttl_write_details(self):
        class Response:
            id = 'resp_ttl'
            service_tier = 'default'
            usage = {
                'input_tokens': 1000,
                'output_tokens': 5,
                'input_tokens_details': {
                    'cached_tokens': 0,
                    'cache_write_tokens': 900,
                    'cache_write_tokens_details': {
                        'ttl_30m_tokens': 900,
                        'ttl_12h_tokens': 0,
                    },
                },
            }
        step = {'warm_up': True, 'observation': {}}
        observed = record_observation(step, Response(), 'start', 'end')
        self.assertEqual(observed['parsed_usage']['cache_write_30m_tokens'], 900)
        self.assertEqual(observed['parsed_usage']['cache_write_12h_tokens'], 0)
        self.assertEqual(observed['parsed_usage']['cache_write_retention'], '30m')
        self.assertEqual(observed['parsed_usage']['uncached_input_tokens'], 100)
        self.assertTrue(observed['cost_known'])
        self.assertEqual(observed['cost_basis'], COMPARISON_APPLICATION_ESTIMATE_RATES['label'])

    def test_clean_acceptance_requires_uncontaminated_warmups_and_follow_up_reads(self):
        def step(arm, warm_up, cached, quality=True, estimate='1', bound='1', name='a.jpg'):
            return {
                'arm': arm,
                'warm_up': warm_up,
                'source_filename': name,
                'quality': {'pass': quality, 'problems': []},
                'observation': {
                    'contamination': 'warm-up returned cache-read tokens' if warm_up and cached else None,
                    'parsed_usage': {'cached_input_tokens': cached},
                    'application_estimate_usd': Decimal(estimate),
                    'conservative_bound_usd': Decimal(bound),
                },
            }
        failing = {
            'steps': [
                step('optimized', True, 10, name='w.jpg'),
                step('optimized', False, 50, name='f1.jpg'),
                step('optimized', False, 50, name='f2.jpg'),
                step('baseline', True, 0, name='w.jpg'),
                step('baseline', False, 0, name='f1.jpg'),
                step('baseline', False, 0, name='f2.jpg'),
            ],
            'optimized': {'application_estimate_usd': Decimal('1'), 'conservative_bound_usd': Decimal('1')},
            'baseline': {'application_estimate_usd': Decimal('3'), 'conservative_bound_usd': Decimal('3')},
        }
        result = evaluate_clean_acceptance(failing)
        self.assertEqual(result['result'], 'inconclusive')
        self.assertFalse(result['warm_ups_uncontaminated'])
        self.assertIn(ACCEPTANCE_RULE, result['acceptance_rule'])
        passing = {
            'steps': [
                step('optimized', True, 0, estimate='2', bound='2', name='w.jpg'),
                step('optimized', False, 80, estimate='1', bound='1', name='f1.jpg'),
                step('optimized', False, 80, estimate='1', bound='1', name='f2.jpg'),
                step('baseline', True, 0, estimate='3', bound='3', name='w.jpg'),
                step('baseline', False, 0, estimate='3', bound='3', name='f1.jpg'),
                step('baseline', False, 0, estimate='3', bound='3', name='f2.jpg'),
            ],
            'optimized': {'application_estimate_usd': Decimal('4'), 'conservative_bound_usd': Decimal('4')},
            'baseline': {'application_estimate_usd': Decimal('9'), 'conservative_bound_usd': Decimal('9')},
        }
        self.assertEqual(evaluate_clean_acceptance(passing)['result'], 'pass')

    def test_local_model_inputs_match_the_recorded_frames(self):
        verified = verify_prepared_images()
        self.assertEqual([item['pixels'] for item in verified], ['2048x1536', '2048x1536', '2048x1365'])
        self.assertTrue(all(item['mode'] == 'original' for item in verified))
        self.assertTrue(verified[2]['source_filename'].endswith('Photograph_016b.jpg'))


def _filled(model, **overrides):
    values = {}
    for name, field in model.model_fields.items():
        values[field.alias or name] = overrides.get(name, 'x')
    return values


class EvidenceTests(unittest.TestCase):
    def test_inspection_output_and_response_serialize(self):
        from app.models import AssetRow, InspectionOutput, RecordRow

        parsed = InspectionOutput.model_validate({
            'assets': [_filled(AssetRow, digital_asset='TriDelta_Year2_Chapters_Box31_F04_004.jpg', action_needed='No Adjustment')],
            'records': [_filled(RecordRow, record_kind='Metadata Only', template='Metadata Only')],
        })

        class Usage:
            def model_dump(self, mode='json', by_alias=False):
                return {'input_tokens': 120, 'output_tokens': 30, 'input_tokens_details': {'cached_tokens': 80, 'cache_write_tokens': 20}}

        class Response:
            id = 'resp_local_test'
            service_tier = 'default'
            usage = Usage()
            output_parsed = parsed

        payload = json_safe({
            'text_format': InspectionOutput,
            'response_id': Response.id,
            'service_tier': Response.service_tier,
            'usage': Response.usage,
            'output': Response.output_parsed,
        })
        encoded = json.dumps(payload)
        self.assertNotIn('ModelMetaclass', encoded)
        self.assertEqual(payload['text_format']['schema_name'], 'InspectionOutput')
        self.assertIn('json_schema', payload['text_format'])
        self.assertEqual(payload['usage']['input_tokens_details']['cache_write_tokens'], 20)
        self.assertEqual(payload['output']['records'][0]['Record Kind'], 'Metadata Only')

    def test_summary_failure_keeps_earlier_call_records(self):
        folder = self._folder()
        log = EvidenceLog(folder)
        log.append({'event': 'response', 'identity': '1:optimized:a.jpg', 'response_id': 'resp_kept'})

        class Broken(EvidenceLog):
            def replace_summary(self, report):
                raise RuntimeError('summary failed')

        with self.assertRaises(RuntimeError):
            Broken(folder).replace_summary({'steps': []})
        self.assertEqual(log.read_events()[0]['response_id'], 'resp_kept')
        self.assertFalse((folder / 'cache_comparison_report.json').exists())

    def test_interrupted_run_keeps_the_completed_response(self):
        log = EvidenceLog(self._folder())
        log.append({'event': 'request', 'identity': '1:optimized:a.jpg', 'arm': 'optimized', 'input_fingerprint': 'abc', 'reservation_usd': '1.20'})
        log.append({'event': 'response', 'identity': '1:optimized:a.jpg', 'response_id': 'resp_done', 'usage': {'input_tokens': 10}, 'service_tier': 'default'})
        again = EvidenceLog(log.folder)
        saved = again.read_events()
        self.assertEqual(saved[1]['response_id'], 'resp_done')
        self.assertFalse(again.summary_path.exists())

    def test_write_failure_prevents_the_next_provider_call(self):
        class FailOnSecond(EvidenceLog):
            def __init__(self, folder):
                super().__init__(folder)
                self.writes = 0

            def append(self, record):
                self.writes += 1
                if self.writes == 2:
                    raise OSError('disk full')
                super().append(record)

        calls = []
        sent = dispatch_recorded_calls(FailOnSecond(self._folder()), [
            {'identity': '1:optimized:a.jpg', 'arm': 'optimized', 'input_fingerprint': 'a', 'reservation_usd': '1'},
            {'identity': '2:optimized:b.jpg', 'arm': 'optimized', 'input_fingerprint': 'b', 'reservation_usd': '1'},
        ], calls.append)
        self.assertEqual(sent, ['1:optimized:a.jpg'])
        self.assertEqual([item['identity'] for item in calls], ['1:optimized:a.jpg'])

    def test_restart_does_not_repeat_a_recorded_request(self):
        log = EvidenceLog(self._folder())
        log.append({'event': 'request', 'identity': '1:optimized:a.jpg', 'arm': 'optimized', 'input_fingerprint': 'a', 'reservation_usd': '1'})
        log.append({'event': 'response', 'identity': '1:optimized:a.jpg', 'response_id': 'resp_done'})
        calls = []
        sent = dispatch_recorded_calls(log, [
            {'identity': '1:optimized:a.jpg', 'arm': 'optimized', 'input_fingerprint': 'a', 'reservation_usd': '1'},
            {'identity': '2:optimized:b.jpg', 'arm': 'optimized', 'input_fingerprint': 'b', 'reservation_usd': '1'},
        ], calls.append)
        self.assertEqual(sent, ['2:optimized:b.jpg'])
        self.assertEqual([item['identity'] for item in calls], ['2:optimized:b.jpg'])

    def _folder(self):
        import tempfile
        return Path(tempfile.mkdtemp())
