import unittest
from decimal import Decimal

from app.comparison_budget import (
    BudgetGuard,
    call_ceiling,
    classify_usage,
    comparison_requests,
    high_detail_image_token_ceiling,
    proposed_comparison,
)
from app.prompt_cache import message_text
from app.usage_accounting import parse_provider_usage


class CeilingTests(unittest.TestCase):
    def test_high_detail_image_ceiling_uses_the_published_patch_budget(self):
        self.assertEqual(high_detail_image_token_ceiling(), 3001)

    def test_ceiling_stops_when_a_bound_is_not_established(self):
        common = dict(input_ceiling=1000, max_output_tokens=100, host='https://api.openai.com/v1', service_tier='default', detail='high', image_count=1, text_byte_ceiling_accepted=True)
        self.assertFalse(call_ceiling(**{**common, 'text_byte_ceiling_accepted': False})['established'])
        self.assertFalse(call_ceiling(**{**common, 'host': 'https://regional.example'})['established'])
        self.assertFalse(call_ceiling(**{**common, 'service_tier': 'auto'})['established'])
        self.assertFalse(call_ceiling(**{**common, 'detail': 'original'})['established'])
        self.assertFalse(call_ceiling(**{**common, 'image_count': 2})['established'])
        self.assertFalse(call_ceiling(**{**common, 'max_output_tokens': 0})['established'])

    def test_short_context_ceiling_uses_the_12_hour_write_rate(self):
        ceiling = call_ceiling(input_ceiling=1000, max_output_tokens=100, host='https://api.openai.com/v1/', service_tier='default', detail='high', image_count=1, text_byte_ceiling_accepted=True)
        self.assertTrue(ceiling['established'])
        self.assertEqual(ceiling['band'], 'short')
        # 1000 tokens at $10 / million plus 100 output tokens at $20 / million.
        self.assertEqual(ceiling['usd'], Decimal('0.012'))

    def test_a_ceiling_above_272k_uses_long_context_rates_for_the_whole_call(self):
        ceiling = call_ceiling(input_ceiling=272_001, max_output_tokens=100, host='https://api.openai.com/v1', service_tier='default', detail='high', image_count=1, text_byte_ceiling_accepted=True)
        self.assertEqual(ceiling['band'], 'long')
        self.assertEqual(ceiling['usd'], Decimal(272_001) * Decimal('20') / Decimal(1_000_000) + Decimal('0.003'))


class GuardTests(unittest.TestCase):
    def test_unknown_responses_retries_and_a_missing_bound_stay_reserved(self):
        guard = BudgetGuard(max_spend=Decimal('1.00'), max_calls=3)
        self.assertTrue(guard.reserve(Decimal('0.40')))
        guard.note_response(None)
        self.assertEqual(guard.reserved, Decimal('0.40'))
        self.assertTrue(guard.stopped)
        self.assertFalse(guard.reserve(Decimal('0.10')))

    def test_a_point_estimate_does_not_release_the_hold(self):
        guard = BudgetGuard(max_spend=Decimal('1.00'), max_calls=2)
        self.assertTrue(guard.reserve(Decimal('0.40')))
        guard.note_response(Decimal('0.40'))
        self.assertEqual(guard.reserved, Decimal('0.40'))
        self.assertFalse(guard.reserve(Decimal('0.70')))
        self.assertIn('maximum spend', guard.stopped)

    def test_call_limit_counts_every_attempt(self):
        guard = BudgetGuard(max_spend=Decimal('10'), max_calls=2)
        self.assertTrue(guard.reserve(Decimal('0.10')))
        self.assertTrue(guard.reserve(Decimal('0.10')))
        self.assertFalse(guard.reserve(Decimal('0.10')))
        self.assertEqual(guard.calls, 2)


class ClassificationTests(unittest.TestCase):
    def test_unscoped_write_stays_unreported_and_is_bounded_at_the_12_hour_rate(self):
        record = parse_provider_usage(
            {'input_tokens': 1000, 'output_tokens': 10, 'input_tokens_details': {'cached_tokens': 100, 'cache_write_tokens': 900}},
            response_id='resp-1', attempt=1, part=1, status='Received',
        )
        classified = classify_usage(record)
        self.assertIsNone(classified['provider_confirmed_usd'])
        self.assertEqual(classified['provider_reported_retention'], 'unknown')
        self.assertIsNone(classified['application_estimate_usd'])
        self.assertEqual(classified['bound_rate_used_for_unscoped_write'], '12h_standard_rate_not_provider_reported')
        # 0 uncached at $4, 100 cached at $0.40, 900 unscoped at $10, 10 output at $20.
        self.assertEqual(classified['conservative_bound_usd'], Decimal('0.00924'))

    def test_a_named_30_minute_write_can_be_estimated_without_relabeling(self):
        record = parse_provider_usage(
            {'input_tokens': 1000, 'output_tokens': 0, 'input_tokens_details': {'cached_tokens': 0, 'cache_write_30m_tokens': 1000}},
            response_id='resp-30', attempt=1, part=1, status='Received',
        )
        classified = classify_usage(record)
        self.assertEqual(classified['provider_reported_retention'], '30m')
        self.assertEqual(classified['application_estimate_usd'], Decimal('0.005'))
        self.assertEqual(classified['conservative_bound_usd'], Decimal('0.005'))
        self.assertIsNone(classified['bound_rate_used_for_unscoped_write'])


class ComparisonShapeTests(unittest.TestCase):
    def test_arms_share_instruction_text_and_isolate_calibration(self):
        instruction = 'Govern the inspection.\nCalibration is not an answer key.'
        payload = {'batch_id': 'B0002', 'approved_project_reference': {'reference_id': 'disposable'}}
        arms = comparison_requests(instruction, payload, [{'type': 'input_image', 'detail': 'high'}])
        baseline = arms['baseline']['input'][0]
        explicit = arms['explicit']['input'][0]
        self.assertEqual(baseline['role'], 'system')
        self.assertEqual(baseline['content'], instruction)
        self.assertEqual(explicit['role'], 'developer')
        self.assertEqual(message_text(explicit['content']), instruction)
        self.assertNotIn('disposable', message_text(explicit['content']))
        self.assertIn('disposable', arms['explicit']['input'][1]['content'][0]['text'])
        self.assertEqual(arms['explicit']['prompt_cache_key'], 'compare-explicit')
        self.assertNotEqual(arms['baseline']['prompt_cache_key'], arms['explicit']['prompt_cache_key'])
        self.assertNotIn('prompt_cache_options', arms['baseline'])

    def test_local_governing_prefix_fits_the_proposed_short_context_budget(self):
        plan = proposed_comparison(instruction_bytes=62445, max_output_tokens=60000)
        self.assertTrue(plan['established'])
        self.assertEqual(plan['band'], 'short')
        self.assertEqual(plan['logical_requests'], 6)
        self.assertEqual(plan['max_calls'], 24)
        self.assertLess(plan['input_ceiling'], 272_000)
        self.assertGreater(plan['max_spend_usd'], plan['per_call_usd'] * 6)
        self.assertEqual(plan['order'][0], 'explicit warm-up')
        self.assertEqual(plan['order'][-1], 'baseline follow-up 2')
