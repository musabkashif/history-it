import unittest
from io import BytesIO

from PIL import Image

from app.decisions import apply_governing_constraints, decision_instructions
from app.images import prepare_model_image
from app.models import ASSET_HEADERS, RECORD_HEADERS, AssetRow, InspectionOutput, RecordRow
from app.register_state import box_status, reconcile_register
from tests.test_f01_reconcile import TrackingSheets
from tests.test_workflow import CapturingInspector, MemorySheets, Settings, filled, install_job, queue_row
from app.orchestrator import Orchestrator


def asset(name, record_id, action='No Adjustment', eligible='No'):
    return AssetRow.model_validate(filled(ASSET_HEADERS, **{
        'Digital Asset': name,
        'Assigned Record ID': record_id,
        'Action Needed': action,
        'Production Queue Eligible': eligible,
        'QC Status': 'Pass',
        'Required Crop Standard': 'page edge',
        'Inspection Complete': 'Yes',
    }))


def record(record_id, kind='Normal Odyssey', template='Document', type_name='Report', readiness='Ready', boundary='Closed'):
    return RecordRow.model_validate(filled(RECORD_HEADERS, **{
        'Proposed Record ID': record_id,
        'Record Kind': kind,
        'Record Operation': 'Create New',
        'Template': template,
        'Type': type_name,
        'Metadata Readiness': readiness,
        'Boundary State After Record': boundary,
        'Production Task Count': '0',
        'Inspection Complete': 'Yes',
        'Review Note': 'The next page is not in this batch.' if boundary == 'Open' or readiness == 'Review' else '',
    }))


class GoverningConstraintTests(unittest.TestCase):
    def test_instructions_state_rules_without_a_filename_answer_key(self):
        text = decision_instructions()
        self.assertIn('Distinct physical copies remain distinct records', text)
        self.assertIn('blank verso', text.lower())
        self.assertIn('Metadata Only', text)
        self.assertIn('No Adjustment', text)
        self.assertIn('Publication Crop', text)
        self.assertIn('Biographical Notes', text)
        self.assertIn('Research Notes', text)
        self.assertIn('Action Required', text)
        self.assertIn('Create Derivative Crop', text)
        self.assertNotIn('F03_035', text)
        self.assertNotIn('F04_001', text)
        self.assertNotIn('F05_045', text)

    def test_f03_letter_and_verso_stay_one_record(self):
        names = [
            'TriDelta_Year2_Chapters_Box19_F03_035.jpg',
            'TriDelta_Year2_Chapters_Box19_F03_036.jpg',
        ]
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset(name, 'letter') for name in names],
            records=[record('letter', template='Correspondence', type_name='Letter')],
        ))
        self.assertEqual({row.assigned_record_id for row in output.assets}, {'letter'})
        self.assertEqual(output.records[0].record_kind, 'Normal Odyssey')
        self.assertEqual(output.records[0].type, 'Letter')
        sheets = MemorySheets()
        install_job(sheets, [queue_row(names=names, **{'Source Group / Folder': 'F03'})])
        Orchestrator(sheets, None, CapturingInspector(output=output), Settings()).run('HIT-1')
        self.assertEqual(sheets.rows('register', 'Metadata References'), [])
        items = sheets.rows('register', 'Itemization')
        self.assertEqual(len(items), 1)
        self.assertIn(names[0], items[0]['Digital Assets'])
        self.assertIn(names[1], items[0]['Digital Assets'])
        self.assertEqual(sheets.rows('register', 'Production Queue'), [])

    def test_similar_physical_copies_are_not_merged(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[
                asset('copy-a.jpg', 'copy-a'),
                asset('copy-b.jpg', 'copy-b'),
            ],
            records=[
                record('copy-a', template='Photographic Image', type_name='Photograph'),
                record('copy-b', template='Photographic Image', type_name='Photograph'),
            ],
        ))
        self.assertEqual([row.assigned_record_id for row in output.assets], ['copy-a', 'copy-b'])
        sheets = MemorySheets()
        install_job(sheets, [queue_row(names=['copy-a.jpg', 'copy-b.jpg'])])
        Orchestrator(sheets, None, CapturingInspector(output=output), Settings()).run('HIT-1')
        self.assertEqual(len(sheets.rows('register', 'Itemization')), 2)
        self.assertEqual(len(sheets.rows('register', 'Asset QC')), 2)

    def test_notebook_and_newspaper_types_are_not_rewritten(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('notes.jpg', 'notes'), asset('paper.jpg', 'paper')],
            records=[
                record('notes', type_name='Notebook'),
                record('paper', template='Publication', type_name='Newspaper'),
            ],
        ))
        self.assertEqual(output.records[0].type, 'Notebook')
        self.assertEqual(output.records[1].type, 'Newspaper')

    def test_crop_action_queues_a_task_and_no_adjustment_does_not(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[
                asset('page.jpg', 'item', action='Publication Crop', eligible='No'),
                asset('done.jpg', 'item', action='No Adjustment', eligible='Yes'),
            ],
            records=[record('item')],
        ))
        self.assertEqual(output.assets[0].queue_eligible, 'Yes')
        self.assertEqual(output.assets[1].queue_eligible, 'No')
        self.assertEqual(output.records[0].production_task_count, '1')
        sheets = MemorySheets()
        install_job(sheets, [queue_row(names=['page.jpg', 'done.jpg'])])
        Orchestrator(sheets, None, CapturingInspector(output=output), Settings()).run('HIT-1')
        tasks = sheets.rows('register', 'Production Queue')
        self.assertEqual([row['Digital Asset'] for row in tasks], ['page.jpg'])
        self.assertEqual(len({row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')}), 2)

    def test_metadata_only_reference_cannot_keep_a_crop_task(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('cover.jpg', 'cover', action='Archival Border Crop', eligible='Yes')],
            records=[record('cover', kind='Metadata Only', template='Metadata Only', type_name='', readiness='Ready')],
        ))
        self.assertEqual(output.assets[0].qc_status, 'Reference Only')
        self.assertEqual(output.assets[0].action_needed, 'N/A')
        self.assertEqual(output.assets[0].queue_eligible, 'No')
        self.assertEqual(output.records[0].metadata_readiness, 'Metadata Reference')
        self.assertEqual(output.records[0].production_task_count, '0')

    def test_box_closes_when_the_authorized_scope_validates(self):
        self.assertEqual(box_status([], [], 'None'), 'In Progress')
        sheets = TrackingSheets()
        rows = [
            queue_row(names=['a.jpg'], **{
                'Batch Status': 'Committed - Validated', 'Resulting Open Item': 'None', 'Source Group / Folder': 'F05',
            }),
            queue_row(batch_id='HIT-1-B0002', names=['b.jpg'], **{
                'Batch Status': 'Committed - Validated', 'Resulting Open Item': 'None', 'Source Group / Folder': 'F06',
                'Notes': '{"manifest":[{"id":"b.jpg","name":"b.jpg","folder_id":"folder","folder_name":"F06","path":"/b.jpg","mime_type":"image/jpeg"}]}',
            }),
        ]
        install_job(sheets, rows)
        sheets.set_field('register', 'Open Intellectual Item', 'None')
        sheets.set_field('register', 'Unresolved Boundary Item', 'None')
        for folder in ('F05', 'F06'):
            sheets.write('register', 'Folder Processing Unit Summary', {
                'Processing Unit / Source Folder': folder,
                'Source Group / Folder': folder,
                'Box': 'Chapters Box 19',
                'Processing Status': 'Completed - Validated',
            }, ('Processing Unit / Source Folder', folder))
        sheets.write('register', 'Box Summary', {'Box': 'Chapters Box 19', 'Box Status': ''}, ('Box', 'Chapters Box 19'))
        sheets.write('register', 'Asset QC', {'Digital Asset': 'a.jpg'}, ('Digital Asset', 'a.jpg'))
        sheets.store[('register', 'Asset Staging')] = [
            {'Batch ID': 'HIT-1-B0001', 'Digital Asset': 'a.jpg', '_row': 2},
            {'Batch ID': 'HIT-1-B0002', 'Digital Asset': 'b.jpg', '_row': 3},
        ]
        sheets.store[('register', 'Record Staging')] = [
            {'Batch ID': 'HIT-1-B0001', 'Proposed Record ID': 'r1', '_row': 2},
            {'Batch ID': 'HIT-1-B0002', 'Proposed Record ID': 'r2', '_row': 3},
        ]
        first = reconcile_register(sheets, 'register', 'HIT-1', sheets.rows('register', 'Batch Queue'))
        status = sheets.rows('register', 'Box Summary')[0]
        # Frozen authorized scope was only F05–F06; completing it closes the box.
        self.assertEqual(status['Box Status'], 'Completed - Validated')
        self.assertEqual(status['Completed - Validated Folders'], '2')
        second = reconcile_register(sheets, 'register', 'HIT-1', sheets.rows('register', 'Batch Queue'))
        self.assertEqual(sheets.rows('register', 'Box Summary')[0]['Box Status'], 'Completed - Validated')
        self.assertEqual([row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')], ['a.jpg'])
        self.assertEqual(first['assets_appended'], 0)
        self.assertEqual(second['assets_appended'], 0)
        self.assertFalse(any(item['field'] == 'Box Status' for item in second['wrote']))

    def test_failed_inspection_retry_does_not_append_a_second_asset(self):
        class Flaky(CapturingInspector):
            def inspect(self, job, batch_id, sources, context, rules):
                self.calls.append(batch_id)
                if len(self.calls) == 1:
                    raise RuntimeError('connection reset')
                return self.output, {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1, 'elapsed_seconds': 0.1, 'response_id': 'resp'}

        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('only.jpg', 'item')],
            records=[record('item')],
        ))
        sheets = MemorySheets()
        install_job(sheets, [queue_row(names=['only.jpg'])])
        orchestrator = Orchestrator(sheets, None, Flaky(output=output), Settings())
        orchestrator.run('HIT-1')
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Inspection Failed - Retryable')
        self.assertEqual(sheets.rows('register', 'Asset QC'), [])
        orchestrator.run('HIT-1')
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')
        self.assertEqual([row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')], ['only.jpg'])

    def test_provider_500_gets_one_attempt_after_the_retry_cap(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('only.jpg', 'item')],
            records=[record('item')],
        ))
        sheets = MemorySheets()
        install_job(sheets, [queue_row(names=['only.jpg'], **{
            'Batch Status': 'Inspection Failed - Retryable',
            'Inspection Attempt': '5',
            'Error / Retry Note': "Error code: 500 - {'error': {'message': 'The server had an error processing your request.'}}",
        })])

        class Cap(Settings):
            max_retries = 5

        inspector = CapturingInspector(output=output)
        orchestrator = Orchestrator(sheets, None, inspector, Cap())
        orchestrator.retry('HIT-1-B0001')
        orchestrator.threads['HIT-1'].join()
        self.assertEqual(len(inspector.calls), 1)
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')

    def test_retry_after_the_limit_lock_still_inspects_a_provider_failure(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('only.jpg', 'item')],
            records=[record('item')],
        ))
        sheets = MemorySheets()
        install_job(sheets, [queue_row(names=['only.jpg'], **{
            'Batch Status': 'Blocked - Human Review',
            'Inspection Attempt': '6',
            'Error / Retry Note': 'Inspection retry limit reached',
        })])

        class Cap(Settings):
            max_retries = 5

        inspector = CapturingInspector(output=output)
        orchestrator = Orchestrator(sheets, None, inspector, Cap())
        orchestrator.retry('HIT-1-B0001')
        orchestrator.threads['HIT-1'].join()
        self.assertEqual(len(inspector.calls), 1)
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')

    def test_image_over_15mb_keeps_aspect_ratio_inside_the_high_detail_edge(self):
        image = Image.new('RGB', (4608, 3072), 'white')
        image.putpixel((10, 10), (0, 0, 0))
        raw_file = BytesIO()
        image.save(raw_file, format='TIFF')
        raw = raw_file.getvalue()
        self.assertGreater(len(raw), 15_000_000)
        prepared, mime, mode = prepare_model_image(raw, 'image/tiff')
        self.assertEqual(mode, 'resized-high-quality')
        self.assertEqual(mime, 'image/jpeg')
        self.assertLess(len(prepared), len(raw))
        with Image.open(BytesIO(prepared)) as result:
            self.assertLessEqual(max(result.size), 2048)
            self.assertGreaterEqual(min(result.size), 1024)
            self.assertAlmostEqual(result.size[0] / result.size[1], 4608 / 3072, places=2)
            self.assertEqual(result.mode, 'RGB')
