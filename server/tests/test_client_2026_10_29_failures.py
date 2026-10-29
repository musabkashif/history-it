"""Offline regressions for Kristen 29 Oct 2026 aggregation failures.

Raiders within-batch Create/Update and Box 35 prior-open coalesce are expected
to succeed after the local fix. True cross-batch Updates still require a
committed Sheets row. No provider or register I/O.
See docs/Kristen_2026-10-29_Investigation.md.
"""
from __future__ import annotations

import json
import unittest

from app.identity import assign_identity, folder_code
from app.inspection import continuation_context, merge_inspections, _is_connection_error
from app.models import ASSET_HEADERS, RECORD_HEADERS, AssetRow, InspectionOutput, RecordRow
from client_reference.commit_engine import CommitError


def filled(headers, **values):
    row = {name: '' for name in headers}
    row.update(values)
    return row


def _asset(name: str, record_id: str) -> AssetRow:
    return AssetRow.model_validate(filled(
        ASSET_HEADERS,
        **{'Digital Asset': name, 'Assigned Record ID': record_id, 'Action Needed': 'No Adjustment'},
    ))


def _record(record_id: str, operation: str, *, boundary: str = 'Open') -> RecordRow:
    return RecordRow.model_validate(filled(
        RECORD_HEADERS,
        **{
            'Proposed Record ID': record_id,
            'Record Operation': operation,
            'Record Kind': 'Normal Odyssey',
            'Template': 'Document',
            'Boundary State After Record': boundary,
            'Metadata Readiness': 'Ready',
        },
    ))


def _output(pairs, records) -> InspectionOutput:
    return InspectionOutput(
        assets=[_asset(name, rid) for name, rid in pairs],
        records=[_record(rid, op, boundary=boundary) for rid, op, boundary in records],
    )


def _raiders_batch():
    return {
        'Project': 'Raiders Credentials Boxes',
        'Box': 'Credentials Binders Box 1',
        'Job ID': 'HIT-ed5fd9772d72a6b9',
        'Batch ID': 'HIT-ed5fd9772d72a6b9-B0001',
        'Source Group / Folder': 'F01',
    }


def _box35_batch():
    return {
        'Project': 'Tri Delta Chapters',
        'Box': 'Chapters Box 35',
        'Job ID': 'HIT-a915dbc3c4ab3d8a',
        'Batch ID': 'HIT-a915dbc3c4ab3d8a-B0002',
        'Source Group / Folder': 'F02',
    }


class GammaRootFolderTests(unittest.TestCase):
    def test_root_level_source_group_uses_root_folder_code(self):
        batch = {
            'Batch ID': 'HIT-4435b42e0c217f99-B0001',
            'Source Group / Folder': 'Scrapbook 4',
            'Notes': json.dumps({'manifest': [{'name': 'page.jpg', 'folder_name': 'Scrapbook 4'}]}),
        }
        self.assertEqual(folder_code(batch), 'ROOT')


class RaidersWithinBatchTests(unittest.TestCase):
    def test_cross_batch_update_without_committed_row_still_fails(self):
        rid = 'HIT-ed5fd9772d72a6b9-R0001'
        assets = [{'Digital Asset': '0006.jpg', 'Assigned Record ID': rid, 'Action Needed': 'No Adjustment', 'Production Queue Eligible': 'No'}]
        records = [{
            'Proposed Record ID': rid, 'Record Kind': 'Normal Odyssey',
            'Record Operation': 'Update Existing Open Record', 'Template': 'Document', 'Type': '',
            'Metadata Readiness': 'Ready', 'Boundary State After Record': 'Open', 'Production Task Count': '0',
        }]
        with self.assertRaisesRegex(CommitError, f'Update Existing Open Record has no committed row: {rid}'):
            assign_identity(_raiders_batch(), assets, records, [], [], [], 'None')

    def test_later_part_continues_same_batch_create_without_sheets_row(self):
        rid = 'HIT-ed5fd9772d72a6b9-R0001'
        left = _output([('0000.jpg', rid)], [(rid, 'Create New', 'Open')])
        context = continuation_context({'prior_open_item': 'None'}, left)
        right = _output([('0006.jpg', rid)], [(rid, 'Update Existing Open Record', 'Open')])
        merged = merge_inspections([left, right], [context.get('prior_open_item')])
        self.assertEqual([record.proposed_record_id for record in merged.records], [rid])
        self.assertEqual(merged.records[0].record_operation, 'Create New')
        assets = [{
            'Digital Asset': asset.digital_asset,
            'Assigned Record ID': asset.assigned_record_id,
            'Action Needed': 'No Adjustment',
            'Production Queue Eligible': 'No',
        } for asset in merged.assets]
        records = [{
            'Proposed Record ID': record.proposed_record_id,
            'Record Kind': record.record_kind,
            'Record Operation': record.record_operation,
            'Template': 'Document',
            'Type': '',
            'Metadata Readiness': 'Ready',
            'Boundary State After Record': record.boundary_state or 'Open',
            'Production Task Count': '0',
        } for record in merged.records]
        folder = assign_identity(_raiders_batch(), assets, records, [], [], [], 'None')
        self.assertEqual(folder, 'F01')
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['Record Operation'], 'Create New')
        self.assertEqual(records[0]['Proposed Record ID'], 'RCB-B1-F01-001')
        self.assertEqual({asset['Assigned Record ID'] for asset in assets}, {'RCB-B1-F01-001'})


class Box35PriorCloseTests(unittest.TestCase):
    def test_two_prior_updates_merge_into_one_final_update(self):
        prior = 'TDC-B35-F01-001'
        items = [{
            'Item ID': prior, 'Template': 'Publication', 'Type': 'Program',
            'Digital Assets': 'f01.jpg', 'Digital Asset Count': '1', 'Production Task Count': '0',
            'Metadata Readiness': 'Review',
        }]
        left = _output(
            [('F02_001.jpg', 'NEW1')],
            [(prior, 'Update Existing Open Record', 'Closed'), ('NEW1', 'Create New', 'Open')],
        )
        right = _output(
            [('F02_014.jpg', 'NEW2')],
            [(prior, 'Update Existing Open Record', 'Closed'), ('NEW2', 'Create New', 'Closed')],
        )
        merged = merge_inspections([left, right], continuation_ids=['NEW1'])
        prior_rows = [record for record in merged.records if record.proposed_record_id == prior]
        self.assertEqual(len(prior_rows), 1)
        self.assertNotIn(f'{prior}~1', [record.proposed_record_id for record in merged.records])
        assets = [{
            'Digital Asset': asset.digital_asset,
            'Assigned Record ID': asset.assigned_record_id,
            'Action Needed': 'No Adjustment',
            'Production Queue Eligible': 'No',
        } for asset in merged.assets]
        records = [{
            'Proposed Record ID': record.proposed_record_id,
            'Record Kind': record.record_kind,
            'Record Operation': record.record_operation,
            'Template': record.template or 'Document',
            'Type': '',
            'Metadata Readiness': 'Ready',
            'Boundary State After Record': record.boundary_state or 'Open',
            'Production Task Count': '0',
        } for record in merged.records]
        assign_identity(_box35_batch(), assets, records, items, [], [], prior)
        closes = [row for row in records if row['Proposed Record ID'] == prior]
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]['Record Operation'], 'Update Existing Open Record')
        self.assertEqual(closes[0]['Record Kind'], 'Normal Odyssey')
        self.assertEqual(closes[0]['Template'], 'Publication')
        self.assertEqual(closes[0]['Type'], 'Program')

    def test_id_and_alias_coalesce_only_for_same_committed_prior(self):
        prior = 'TDC-B35-F01-001'
        items = [{
            'Item ID': prior, 'Template': 'Publication', 'Type': 'Program',
            'Digital Assets': 'f01.jpg', 'Digital Asset Count': '1', 'Production Task Count': '0',
            'Metadata Readiness': 'Review',
        }]
        assets = [
            {'Digital Asset': 'F02_001.jpg', 'Assigned Record ID': 'NEW1', 'Action Needed': 'No Adjustment', 'Production Queue Eligible': 'No'},
            {'Digital Asset': 'F02_014.jpg', 'Assigned Record ID': 'NEW2', 'Action Needed': 'No Adjustment', 'Production Queue Eligible': 'No'},
        ]
        records = [
            {
                'Proposed Record ID': prior, 'Record Kind': 'Metadata Only',
                'Record Operation': 'Update Existing Open Record', 'Template': 'Metadata Only', 'Type': 'Newspaper',
                'Metadata Readiness': 'Metadata Reference', 'Boundary State After Record': 'Closed', 'Production Task Count': '0',
            },
            {
                'Proposed Record ID': 'NEW1', 'Record Kind': 'Normal Odyssey',
                'Record Operation': 'Create New', 'Template': 'Document', 'Type': '',
                'Metadata Readiness': 'Ready', 'Boundary State After Record': 'Open', 'Production Task Count': '0',
            },
            {
                'Proposed Record ID': f'{prior}~1', 'Record Kind': 'Metadata Only',
                'Record Operation': 'Update Existing Open Record', 'Template': 'Metadata Only', 'Type': 'Newspaper',
                'Metadata Readiness': 'Metadata Reference', 'Boundary State After Record': 'Closed', 'Production Task Count': '0',
            },
            {
                'Proposed Record ID': 'NEW2', 'Record Kind': 'Normal Odyssey',
                'Record Operation': 'Create New', 'Template': 'Document', 'Type': '',
                'Metadata Readiness': 'Ready', 'Boundary State After Record': 'Closed', 'Production Task Count': '0',
            },
        ]
        assign_identity(_box35_batch(), assets, records, items, [], [], prior)
        closes = [row for row in records if row['Proposed Record ID'] == prior]
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]['Template'], 'Publication')
        self.assertEqual(closes[0]['Type'], 'Program')
        self.assertEqual(closes[0]['Record Kind'], 'Normal Odyssey')

    def test_unrelated_alias_without_committed_base_still_fails_cross_batch_rule(self):
        assets = [{
            'Digital Asset': 'x.jpg', 'Assigned Record ID': 'LOCAL~1',
            'Action Needed': 'No Adjustment', 'Production Queue Eligible': 'No',
        }]
        records = [{
            'Proposed Record ID': 'LOCAL~1', 'Record Kind': 'Normal Odyssey',
            'Record Operation': 'Update Existing Open Record', 'Template': 'Document', 'Type': '',
            'Metadata Readiness': 'Ready', 'Boundary State After Record': 'Open', 'Production Task Count': '0',
        }]
        with self.assertRaisesRegex(CommitError, 'Update Existing Open Record has no committed row: LOCAL~1'):
            assign_identity(_raiders_batch(), assets, records, [], [], [], 'None')

    def test_single_prior_close_still_assigns(self):
        prior = 'TDC-B35-F01-001'
        items = [{
            'Item ID': prior, 'Template': 'Publication', 'Type': 'Program',
            'Digital Assets': 'f01.jpg', 'Digital Asset Count': '1', 'Production Task Count': '0',
        }]
        assets = [{
            'Digital Asset': 'F02_001.jpg', 'Assigned Record ID': 'NEW1',
            'Action Needed': 'No Adjustment', 'Production Queue Eligible': 'No',
        }]
        records = [
            {
                'Proposed Record ID': prior, 'Record Kind': 'Normal Odyssey',
                'Record Operation': 'Update Existing Open Record', 'Template': 'Publication', 'Type': 'Program',
                'Metadata Readiness': 'Ready', 'Boundary State After Record': 'Closed', 'Production Task Count': '0',
            },
            {
                'Proposed Record ID': 'NEW1', 'Record Kind': 'Normal Odyssey',
                'Record Operation': 'Create New', 'Template': 'Document', 'Type': '',
                'Metadata Readiness': 'Ready', 'Boundary State After Record': 'Open', 'Production Task Count': '0',
            },
        ]
        assign_identity(_box35_batch(), assets, records, items, [], [], prior)
        self.assertEqual(records[0]['Proposed Record ID'], prior)


class McCallieTransportClassificationTests(unittest.TestCase):
    def test_incomplete_read_is_not_treated_as_openai_connection_retry(self):
        class IncompleteRead(Exception):
            pass

        self.assertFalse(_is_connection_error(IncompleteRead('16777216 bytes read, 4530908 more expected')))


if __name__ == '__main__':
    unittest.main()
