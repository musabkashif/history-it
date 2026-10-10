import unittest

from app.benchmark import compare_workbooks, f01_calibration_checkpoint
from app.models import AssetRow
from app.orchestrator import Orchestrator
from app.register_state import ReconciliationConflict, reconcile_register
from tests.test_workflow import (
    LETTER_NOTE, MemorySheets, Settings, calibration_records, f01_intermediate_register,
    install_job, name, queue_row,
)
from client_reference.commit_engine import split_assets


def sheet_dump(sheets):
    names = (
        'Asset QC', 'Itemization', 'Metadata References', 'Ready for Metadata', 'Production Queue',
        'Continuation State', 'Batch Queue', 'Asset Staging', 'Record Staging',
        'Folder Processing Unit Summary', 'Box Summary',
    )
    return {name: sheets.rows('register', name) for name in names}


class TrackingSheets(MemorySheets):
    def __init__(self):
        super().__init__()
        self.written = []

    def write(self, sid, sheet, row, key=None):
        self.written.append(sheet)
        return super().write(sid, sheet, row, key)

    def grid(self, sid, sheet, a1):
        self.written.append('grid:' + sheet)
        values = {row.get('State Field'): row.get('Current Value') for row in self.rows(sid, 'Continuation State')}
        box = self.rows(sid, 'Box Summary')
        count = box[0].get('Completed - Validated Folders') if box else ''
        rows = [['Section', 'Field', 'Current Value', 'Source / Range', 'Control Note']]
        for field, value in {
            'Current Completed Processing Unit': values.get('Current Completed Processing Unit', ''),
            'Next Authorized / Unprocessed Folder': values.get('Next Authorized / Unprocessed Folder', ''),
            'Current Authorization Scope': values.get('Source Processing Authorization', ''),
            'Resume Instruction': values.get('Resume Instruction', ''),
            'Open Intellectual Item': values.get('Open Intellectual Item', ''),
            'Completed - Validated Folders': count,
        }.items():
            rows.append(['STATE', field, value, "='Continuation State'!B8", ''])
        return rows


def letter_assets_high():
    output = calibration_records()
    for asset in output.assets:
        if any(asset.digital_asset.endswith(f'F01_{number:03d}.jpg') for number in range(16, 22)):
            asset.qc_confidence = 'High'
    return output


class F01ReconcileTests(unittest.TestCase):
    def test_inconsistent_f01_state_is_repaired_without_appending_or_reprocessing(self):
        from tests.test_workflow import CapturingInspector

        sheets = TrackingSheets()
        install_job(sheets, [queue_row(names=[name(number) for number in range(1, 22)])])
        inspector = CapturingInspector(output=letter_assets_high())
        orchestrator = Orchestrator(sheets, None, inspector, Settings())
        orchestrator.run('HIT-1')
        self.assertEqual(len(inspector.calls), 1)
        committed = [row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')]
        self.assertEqual(len(committed), 21)
        letter = sheets.field('register', 'Open Intellectual Item')
        self.assertNotEqual(letter, 'None')
        self.assertEqual(sheets.rows('register', 'Box Summary')[0]['Completed - Validated Folders'], '1')
        self.assertNotIn('Run Context', sheets.written)
        self.assertIn('grid:Run Context', sheets.written)
        checkpoint = f01_calibration_checkpoint(sheet_dump(sheets))
        self.assertEqual(checkpoint['problems'], [], checkpoint['problems'])
        self.assertEqual(checkpoint['outcome'], 'f01_intermediate_match')
        letter_rows = [row for row in sheets.rows('register', 'Asset QC') if row['Assigned Record ID'] == letter]
        self.assertEqual({row['QC Confidence'] for row in letter_rows}, {'High'})
        item = next(row for row in sheets.rows('register', 'Itemization') if row['Item ID'] == letter)
        self.assertEqual(item['Confidence'], 'Medium')
        metadata = [row for row in sheets.rows('register', 'Asset QC') if row['Template'] == 'Metadata Only']
        self.assertTrue(metadata)
        self.assertEqual({row['Type'] for row in metadata}, {''})

        sheets.set_field('register', 'Current Completed Processing Unit', 'None - developer register initialized')
        sheets.set_field('register', 'Next Authorized / Unprocessed Folder', name(1))
        sheets.set_field('register', 'Source Processing Authorization', 'Not yet batched - developer orchestrator should enumerate the source folder before inspection.')
        sheets.set_field('register', 'Resume Instruction', 'Begin Chapters Box 19 at F01. Enumerate the source folder once.')
        sheets.write('register', 'Box Summary', {'Completed - Validated Folders': '0'}, ('Box', 'Chapters Box 19'))
        batch_id = sheets.rows('register', 'Batch Queue')[0]['Batch ID']
        sheets.write('register', 'Batch Queue', {'Asset Staging Row Range': '', 'Record Staging Row Range': ''}, ('Batch ID', batch_id))
        broken = f01_calibration_checkpoint(sheet_dump(sheets))
        self.assertFalse(broken['matches'])
        self.assertEqual(broken['actual'], broken['expected'])
        self.assertTrue(any('Completed - Validated Folders' in problem for problem in broken['problems']))
        self.assertTrue(any('F01_001' in problem for problem in broken['problems']))
        self.assertTrue(any('not yet batched' in problem.casefold() for problem in broken['problems']))
        self.assertTrue(any('Resume Instruction' in problem for problem in broken['problems']))

        first = reconcile_register(sheets, 'register', 'HIT-1', orchestrator._queue('register', 'HIT-1'))
        self.assertEqual(first['open_intellectual_item'], letter)
        self.assertEqual(first['asset_qc_count'], 21)
        self.assertEqual(first['assets_appended'], 0)
        self.assertEqual(sheets.rows('register', 'Box Summary')[0]['Completed - Validated Folders'], '1')
        self.assertEqual(first['run_context_readback']['values']['Completed - Validated Folders'], '1')
        self.assertNotIn(name(1), sheets.field('register', 'Next Authorized / Unprocessed Folder'))
        self.assertNotIn('not yet batched', sheets.field('register', 'Source Processing Authorization').casefold())
        self.assertNotIn('begin chapters box 19 at f01', sheets.field('register', 'Resume Instruction').casefold())
        self.assertIn(letter, sheets.field('register', 'Resume Instruction'))
        queue = sheets.rows('register', 'Batch Queue')[0]
        self.assertTrue(queue['Asset Staging Row Range'].startswith('Asset Staging!A'))
        self.assertTrue(queue['Record Staging Row Range'].startswith('Record Staging!A'))
        self.assertEqual([row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')], committed)
        reread = [row for row in sheets.rows('register', 'Asset QC') if row['Assigned Record ID'] == letter]
        self.assertEqual({row['QC Confidence'] for row in reread}, {'High'})
        self.assertEqual({row['Type'] for row in sheets.rows('register', 'Asset QC') if row['Template'] == 'Metadata Only'}, {''})
        repaired = f01_calibration_checkpoint(sheet_dump(sheets))
        self.assertEqual(repaired['problems'], [], repaired['problems'])

        again = reconcile_register(sheets, 'register', 'HIT-1', orchestrator._queue('register', 'HIT-1'))
        self.assertEqual(again['wrote'], [])
        self.assertEqual([row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')], committed)

        class Exploding:
            def inspect(self, *args, **kwargs):
                raise AssertionError('committed F01 assets must not be inspected again')

        orchestrator.inspector = Exploding()
        orchestrator.run('HIT-1')
        self.assertEqual([row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')], committed)
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')
        self.assertEqual(sheets.field('register', 'Open Intellectual Item'), letter)

    def test_conflict_stops_before_any_derived_write(self):
        sheets = TrackingSheets()
        live = f01_intermediate_register()
        for sheet, rows in live.items():
            sheets.store[('register', sheet)] = rows
        sheets.store[('index', 'HistoryIT Jobs')] = [{'Job ID': 'HIT-1', 'Spreadsheet ID': 'register'}]
        sheets.write('register', 'Box Summary', {'Completed - Validated Folders': '9'}, ('Box', 'Chapters Box 19'))
        sheets.set_field('register', 'Resume Instruction', 'Hold for the cataloger.')
        assets = [row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')]
        with self.assertRaises(ReconciliationConflict) as caught:
            reconcile_register(sheets, 'register', 'HIT-1', sheets.rows('register', 'Batch Queue'))
        self.assertIn('Completed - Validated Folders', str(caught.exception))
        self.assertEqual(sheets.rows('register', 'Box Summary')[0]['Completed - Validated Folders'], '9')
        self.assertEqual(sheets.field('register', 'Resume Instruction'), 'Hold for the cataloger.')
        self.assertEqual([row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')], assets)

    def test_compare_separates_confidence_and_metadata_type_from_controlled_fields(self):
        shared = {
            'Source Group / Folder': 'F01', 'Template': 'Metadata Only', 'Asset Role': 'Folder / Context',
            'QC Note': 'Folder enclosure capture.', 'QC Status': 'Reference Only', 'Action Needed': 'N/A',
        }
        live = {
            'Asset QC': [{'Digital Asset': name(1), 'Type': '', 'QC Confidence': 'High', **shared}],
            'Folder Processing Unit Summary': [{'Processing Unit / Source Folder': 'F01', 'Source Group / Folder': 'F01'}],
        }
        benchmark = {
            'Asset QC': [{'Digital Asset': name(1), 'Type': 'Folder/Enclosure', 'QC Confidence': 'Medium', **shared}],
            'Folder Processing Unit Summary': [{'Processing Unit / Source Folder': 'F01', 'Source Group / Folder': 'F01'}],
        }
        compared = compare_workbooks(live, benchmark, folder='F01')
        self.assertFalse(compared['final_box19_match'])
        self.assertIn('F02', compared['f01_letter_transition'])
        classes = {item['field']: item['class'] for item in compared['difference_classes'] if item.get('field')}
        self.assertEqual(classes.get('Type'), 'free_text')
        self.assertFalse(compared['controlled_mismatch'])
        self.assertTrue(compared['free_text_mismatch'])
        self.assertEqual(compared['outcome'], 'semantic_benchmark_mismatch')
        confidence_only = compare_workbooks(
            {'Asset QC': [{'Digital Asset': name(16), 'Template': 'Correspondence', 'Type': 'Letter', 'QC Confidence': 'High', 'Source Group / Folder': 'F01'}]},
            {'Asset QC': [{'Digital Asset': name(16), 'Template': 'Correspondence', 'Type': 'Letter', 'QC Confidence': 'Medium', 'Source Group / Folder': 'F01'}]},
            folder='F01',
        )
        self.assertEqual(confidence_only['outcome'], 'exact_match')
        self.assertEqual(confidence_only['distinctions']['asset_qc_confidence_versus_record_confidence'], [{
            'sheet': 'Asset QC', 'key': name(16), 'field': 'QC Confidence', 'live': 'High', 'benchmark': 'Medium',
        }])
        self.assertFalse(confidence_only['final_box19_match'])


class AssignmentLockTests(unittest.TestCase):
    def test_record_confidence_is_not_copied_onto_asset_qc(self):
        output = letter_assets_high()
        letter = next(row for row in output.records if row.type == 'Letter')
        self.assertEqual(letter.confidence, 'Medium')
        self.assertTrue(all(
            asset.qc_confidence == 'High'
            for asset in output.assets
            if asset.assigned_record_id == letter.proposed_record_id
        ))
        dumped = AssetRow.model_validate({
            'Project': '', 'Job ID': '', 'Batch ID': '', 'Processing Unit / Source Folder': '',
            'Asset Sequence': '', 'Digital Asset': name(16), 'Box': '', 'Source Group / Folder': '',
            'Assigned Record ID': letter.proposed_record_id, 'Asset Role': '', 'Required Crop Standard': '',
            'QC Status': '', 'Action Needed': 'No Adjustment', 'Expected Output Asset Count': '',
            'QC Confidence': 'High', 'QC Note': '', 'Odyssey Ingest / Visibility Note': '',
            'Production Queue Eligible': '', 'Production Task Scope': '', 'Production Task Note': '',
            'Exception / Duplicate Note': '', 'Inspection Complete': '', 'Commit Status': '', 'Commit Note': '',
        }).model_dump(by_alias=True)
        self.assertEqual(dumped['QC Confidence'], 'High')
        self.assertNotIn(LETTER_NOTE, dumped['QC Confidence'])
        self.assertTrue(split_assets(name(16)) == [name(16)])
