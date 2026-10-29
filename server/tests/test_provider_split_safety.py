"""Offline coverage for provider-split depth, min size, and diagnostics."""
from __future__ import annotations

import unittest

from app.inspection import (
    PROVIDER_SPLIT_MAX_DEPTH,
    PROVIDER_SPLIT_MIN_IMAGES,
    ProviderSplitExhausted,
    inspect_resilient,
)
from app.models import ASSET_HEADERS, RECORD_HEADERS, AssetRow, InspectionOutput, RecordRow


def filled(headers, **values):
    row = {header: '' for header in headers}
    row.update(values)
    return row


def _source(name: str):
    return type('Source', (), {'name': name, 'mime_type': 'image/jpeg'})()


def _output(names, record_id: str, boundary: str = 'Closed'):
    assets = [AssetRow.model_validate(filled(ASSET_HEADERS, **{
        'Digital Asset': name, 'Assigned Record ID': record_id,
    })) for name in names]
    record = RecordRow.model_validate(filled(RECORD_HEADERS, **{
        'Proposed Record ID': record_id, 'Boundary State After Record': boundary,
    }))
    return InspectionOutput(assets=assets, records=[record])


def _server_error(status: int = 500, message: str = 'Error code: 500'):
    failure = RuntimeError(message)
    failure.status_code = status
    return failure


class ProviderSplitSafetyTests(unittest.TestCase):
    def test_one_failure_then_successful_child_splits(self):
        calls = []
        events = []

        def once(sources, context):
            names = [item.name for item in sources]
            calls.append(names)
            if len(names) == 4:
                raise _server_error()
            return _output(names, 'item'), {
                'input_tokens': len(names),
                'output_tokens': 1,
                'response_id': f'resp-{names[0]}',
            }

        context = {'prior_open_item': 'None'}
        output, usage = inspect_resilient(
            once,
            [_source(name) for name in ('a.jpg', 'b.jpg', 'c.jpg', 'd.jpg')],
            context,
            on_event=events.append,
        )
        self.assertEqual([asset.digital_asset for asset in output.assets], ['a.jpg', 'b.jpg', 'c.jpg', 'd.jpg'])
        self.assertEqual(usage['input_tokens'], 4)
        self.assertEqual(calls, [
            ['a.jpg', 'b.jpg', 'c.jpg', 'd.jpg'],
            ['a.jpg', 'b.jpg'],
            ['c.jpg', 'd.jpg'],
        ])
        kinds = [event['kind'] for event in events]
        self.assertEqual(kinds, [
            'unsuccessful_parent_request',
            'successful_billed_leaf',
            'successful_billed_leaf',
        ])
        parent = events[0]
        self.assertEqual(parent['billing_status'], 'unbilled_http_failure')
        self.assertIs(parent['billed'], False)
        self.assertIsNone(parent['token_usage'])
        self.assertIsNone(parent['response_id'])
        self.assertEqual(parent['http_status'], 500)
        self.assertFalse(parent['application_retry'])
        self.assertFalse(parent['provider_api_retry'])
        self.assertTrue(parent['child_split_request'])
        for leaf in events[1:]:
            self.assertEqual(leaf['parent_id'], parent['node_id'])
            self.assertEqual(leaf['billing_status'], 'billed_response')
            self.assertIs(leaf['billed'], True)
            self.assertTrue(leaf['child_split_request'])
            self.assertFalse(leaf['application_retry'])
            self.assertFalse(leaf['provider_api_retry'])
            self.assertIsNotNone(leaf['response_id'])
            self.assertEqual(leaf['token_usage']['input_tokens'], 2)
        self.assertEqual(context['_provider_split_tree'], events)

    def test_parent_http_500_plus_successful_billed_leaves(self):
        events = []

        def once(sources, context):
            names = [item.name for item in sources]
            if len(names) > 1:
                raise _server_error(500, 'InternalServerError')
            return _output(names, names[0]), {
                'input_tokens': 3,
                'cached_input_tokens': 1,
                'output_tokens': 2,
                'response_id': f'id-{names[0]}',
            }

        context = {'prior_open_item': 'None'}
        output, usage = inspect_resilient(
            once,
            [_source(name) for name in ('a.jpg', 'b.jpg')],
            context,
            on_event=events.append,
        )
        self.assertEqual(len(output.assets), 2)
        self.assertEqual(usage['input_tokens'], 6)
        parent = events[0]
        self.assertEqual(parent['event'], 'provider_split')
        self.assertEqual(parent['http_status'], 500)
        self.assertEqual(parent['billing_status'], 'unbilled_http_failure')
        self.assertIsNone(parent['token_usage'])
        leaves = [event for event in events if event['event'] == 'provider_split_leaf']
        self.assertEqual(len(leaves), 2)
        self.assertTrue(all(leaf['billed'] is True for leaf in leaves))
        self.assertTrue(all(leaf['response_id'] for leaf in leaves))
        self.assertEqual({leaf['parent_id'] for leaf in leaves}, {parent['node_id']})

    def test_repeated_failures_stop_at_maximum_depth(self):
        calls = []
        events = []

        def once(sources, context):
            names = [item.name for item in sources]
            calls.append((len(names), names[0]))
            raise _server_error()

        # Keep >=2 images at max depth so the guard trips on depth, not min size.
        names = [f'{index:03d}.jpg' for index in range(128)]
        context = {'prior_open_item': 'None'}
        with self.assertRaises(ProviderSplitExhausted) as raised:
            inspect_resilient(
                once,
                [_source(name) for name in names],
                context,
                on_event=events.append,
            )
        self.assertIn(str(PROVIDER_SPLIT_MAX_DEPTH), str(raised.exception))
        self.assertIn('No further child requests were created', str(raised.exception))
        exhausted = [event for event in events if event['event'] == 'provider_split_exhausted']
        self.assertEqual(len(exhausted), 1)
        self.assertEqual(exhausted[0]['kind'], 'max_depth')
        self.assertEqual(exhausted[0]['depth'], PROVIDER_SPLIT_MAX_DEPTH)
        self.assertGreaterEqual(exhausted[0]['source_image_count'], PROVIDER_SPLIT_MIN_IMAGES)
        self.assertEqual(exhausted[0]['billing_status'], 'unbilled_http_failure')
        self.assertIsNone(exhausted[0]['token_usage'])
        self.assertIsNone(exhausted[0]['response_id'])
        # Root plus one left-child request per depth level until the cap refuses another split.
        self.assertEqual(len(calls), PROVIDER_SPLIT_MAX_DEPTH + 1)
        self.assertEqual(max(event['depth'] for event in events), PROVIDER_SPLIT_MAX_DEPTH)
        self.assertEqual(context['_provider_split_tree'], events)
        self.assertTrue(any(event['event'] == 'provider_split' for event in context['_provider_split_tree']))

    def test_minimum_size_part_refuses_another_split(self):
        events = []

        def once(sources, context):
            raise _server_error()

        context = {'prior_open_item': 'None'}
        with self.assertRaisesRegex(RuntimeError, 'only.jpg'):
            inspect_resilient(
                once,
                [_source('only.jpg')],
                context,
                on_event=events.append,
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event'], 'provider_split_exhausted')
        self.assertEqual(events[0]['kind'], 'minimum_part_size')
        self.assertEqual(events[0]['reason'], 'part_below_minimum_split_size')
        self.assertEqual(events[0]['source_image_count'], 1)
        self.assertLess(1, PROVIDER_SPLIT_MIN_IMAGES)
        self.assertEqual(events[0]['billing_status'], 'unbilled_http_failure')
        self.assertIsNone(events[0]['token_usage'])

    def test_failed_parents_do_not_invent_token_usage_or_billing(self):
        events = []

        def once(sources, context):
            names = [item.name for item in sources]
            if len(names) > 2:
                raise _server_error(502, 'Bad gateway')
            return _output(names, 'item'), {'input_tokens': len(names), 'response_id': names[0]}

        inspect_resilient(
            once,
            [_source(name) for name in ('a.jpg', 'b.jpg', 'c.jpg', 'd.jpg')],
            {'prior_open_item': 'None'},
            on_event=events.append,
        )
        parents = [event for event in events if event['kind'] == 'unsuccessful_parent_request']
        self.assertTrue(parents)
        for parent in parents:
            self.assertIs(parent['billed'], False)
            self.assertEqual(parent['billing_status'], 'unbilled_http_failure')
            self.assertIsNone(parent['response_id'])
            self.assertIsNone(parent['token_usage'])
            self.assertNotIn('input_tokens', parent)
            self.assertNotIn('estimated_cost_usd', parent)

    def test_response_ids_and_parent_child_relationships_are_retained(self):
        events = []

        def once(sources, context):
            names = [item.name for item in sources]
            if len(names) > 2:
                raise _server_error()
            return _output(names, 'item'), {
                'input_tokens': len(names),
                'output_tokens': 1,
                'response_id': f'resp-{"".join(names)}',
            }

        context = {'prior_open_item': 'None'}
        inspect_resilient(
            once,
            [_source(name) for name in ('a.jpg', 'b.jpg', 'c.jpg', 'd.jpg')],
            context,
            on_event=events.append,
        )
        parent = events[0]
        leaves = events[1:]
        self.assertEqual(parent['node_id'], 'split-1')
        self.assertIsNone(parent['parent_id'])
        self.assertEqual([leaf['node_id'] for leaf in leaves], ['split-1.L', 'split-1.R'])
        self.assertEqual([leaf['parent_id'] for leaf in leaves], ['split-1', 'split-1'])
        self.assertEqual(
            [leaf['response_id'] for leaf in leaves],
            ['resp-a.jpgb.jpg', 'resp-c.jpgd.jpg'],
        )
        self.assertEqual(context['_provider_split_tree'][0]['node_id'], 'split-1')

    def test_successful_single_request_inspection_remains_unchanged(self):
        calls = []
        events = []

        def once(sources, context):
            names = [item.name for item in sources]
            calls.append(names)
            piece = _output(names, 'solo')
            return piece, {'input_tokens': 9, 'output_tokens': 2, 'response_id': 'only-one'}

        context = {'prior_open_item': 'None'}
        output, usage = inspect_resilient(
            once,
            [_source(name) for name in ('a.jpg', 'b.jpg', 'c.jpg')],
            context,
            on_event=events.append,
        )
        self.assertEqual(calls, [['a.jpg', 'b.jpg', 'c.jpg']])
        self.assertEqual(events, [])
        self.assertFalse(context.get('_provider_split_tree'))
        self.assertEqual([asset.digital_asset for asset in output.assets], ['a.jpg', 'b.jpg', 'c.jpg'])
        self.assertEqual(usage, {'input_tokens': 9, 'output_tokens': 2, 'response_id': 'only-one'})

    def test_leaf_with_tokens_but_no_response_id_stays_billing_unknown(self):
        events = []

        def once(sources, context):
            names = [item.name for item in sources]
            if len(names) > 1:
                raise _server_error()
            return _output(names, names[0]), {'input_tokens': 4, 'output_tokens': 1}

        inspect_resilient(
            once,
            [_source('a.jpg'), _source('b.jpg')],
            {'prior_open_item': 'None'},
            on_event=events.append,
        )
        leaves = [event for event in events if event['event'] == 'provider_split_leaf']
        self.assertTrue(leaves)
        for leaf in leaves:
            self.assertIsNone(leaf['billed'])
            self.assertEqual(leaf['billing_status'], 'unknown')
            self.assertIsNone(leaf['response_id'])
            self.assertEqual(leaf['token_usage']['input_tokens'], 4)


if __name__ == '__main__':
    unittest.main()
