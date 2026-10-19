import json
import unittest

from app.archival import assess_archival, classify_prose
from app.box_folders import select_authorized_sources
from app.benchmark import compare_workbooks
from app.decisions import apply_governing_constraints, decision_instructions
from app.models import ASSET_HEADERS, RECORD_HEADERS, AssetRow, InspectionOutput, RecordRow, Source
from app.orchestrator import Orchestrator
from app.register_state import box_status, reconcile_register
from tests.test_decisions import asset, record
from tests.test_f01_reconcile import TrackingSheets
from tests.test_workflow import CapturingInspector, MemorySheets, Settings, install_job, queue_row


def source(folder_id, name, path):
    return Source(id=folder_id + '-' + name, name=name, folder_id=folder_id, folder_name=path.split('/')[-2], path=path, mime_type='image/jpeg')


def qc(name, record_id, action='No Adjustment', status='Pass', standard='Archival Border'):
    return {
        'Digital Asset': name,
        'Assigned Record ID': record_id,
        'Action Needed': action,
        'QC Status': status,
        'Required Crop Standard': standard,
        'Template': 'Document',
        'Type': 'Report',
    }


def item(record_id, files, template='Document', type_name='Report', readiness='Ready', detail='', notes=''):
    return {
        'Item ID': record_id,
        'Digital Assets': '; '.join(files),
        'Digital Asset Count': str(len(files)),
        'Template': template,
        'Type': type_name,
        'Metadata Readiness': readiness,
        'Further Detail': detail,
        'Notes': notes,
        'Source Group / Folder': 'F03',
    }


def workbook(items, assets, tasks=None, box_status_value='Completed - Validated', folders=1):
    return {
        'Itemization': items,
        'Asset QC': assets,
        'Metadata References': [],
        'Ready for Metadata': [],
        'Production Queue': tasks or [],
        'Continuation State': [],
        'Box Summary': [{'Box': 'Chapters Box 19', 'Box Status': box_status_value}],
        'Folder Processing Unit Summary': [{'Processing Unit / Source Folder': f'F0{index}'} for index in range(1, folders + 1)],
        'Project Register': [],
    }


class AuthorizedRuleTests(unittest.TestCase):
    def test_biographical_notebook_is_document_and_uses_book_crop(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('page.jpg', 'book', action='Publication Crop')],
            records=[record('book', template='Ledger', type_name='Ledger', readiness='Review')],
        ))
        output.records[0].further_detail = 'Hole-bound personnel notebook with handwritten profiles.'
        output.records[0].review_note = 'Could reasonably fit Ledger or Document.'
        apply_governing_constraints(output)
        row = output.records[0]
        self.assertEqual(row.template, 'Document')
        self.assertEqual(row.type, 'Biographical Notes')
        self.assertIn('Notebook binding', row.further_detail)
        self.assertEqual(output.assets[0].action_needed, 'Crop / Recrop')
        self.assertEqual(output.assets[0].required_crop_standard, 'Book Page Edge')
        self.assertEqual(output.assets[0].qc_status, 'Action Required')
        self.assertNotEqual(row.metadata_readiness, 'Review')

    def test_research_index_and_envelope_are_not_metadata_only_or_a_letter(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('index.jpg', 'notes'), asset('back.jpg', 'mail')],
            records=[
                record('notes', kind='Metadata Only', template='Metadata Only', type_name='', readiness='Metadata Reference'),
                record('mail', template='Correspondence', type_name='Letter'),
            ],
        ))
        output.records[0].further_detail = 'Typed citation index and its verso.'
        output.records[1].further_detail = 'Mailed envelope and its back. The letter itself is not present.'
        apply_governing_constraints(output)
        self.assertEqual(output.records[0].template, 'Document')
        self.assertEqual(output.records[0].type, 'Research Notes')
        self.assertEqual(output.records[0].record_kind, 'Normal Odyssey')
        self.assertEqual(output.records[1].type, 'Envelope')
        self.assertEqual(output.assets[0].qc_status, 'Pass')

    def test_blank_file_folder_stays_metadata_only(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('folder.jpg', 'folder', action='Publication Crop')],
            records=[record('folder', template='Scrapbook Page', type_name='Scrapbook Page')],
        ))
        output.records[0].further_detail = 'Blank file folder.'
        apply_governing_constraints(output)
        self.assertEqual(output.records[0].record_kind, 'Metadata Only')
        self.assertEqual(output.assets[0].qc_status, 'Reference Only')
        self.assertEqual(output.assets[0].queue_eligible, 'No')

    def test_a_visible_margin_alone_does_not_create_a_crop_task(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('sheet.jpg', 'notes', action='Archival Border Crop')],
            records=[record('notes', template='Document', type_name='Research Notes')],
        ))
        output.assets[0].qc_note = 'A black border is visible around the sheet.'
        apply_governing_constraints(output)
        self.assertEqual(output.assets[0].action_needed, 'No Adjustment')
        self.assertEqual(output.assets[0].qc_status, 'Pass')
        self.assertEqual(output.assets[0].queue_eligible, 'No')

    def test_a_large_book_background_still_crops(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('cover.jpg', 'book', action='Crop / Recrop')],
            records=[record('book', template='Document', type_name='Biographical Notes', readiness='Blocked - Cropping/QC')],
        ))
        output.records[0].further_detail = 'Notebook binding.'
        output.assets[0].qc_note = 'A large background remains around the cover.'
        apply_governing_constraints(output)
        self.assertEqual(output.assets[0].action_needed, 'Crop / Recrop')
        self.assertEqual(output.assets[0].qc_status, 'Action Required')

    def test_a_photographic_print_uses_archival_border_without_a_task(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('print.jpg', 'photo', action='No Adjustment')],
            records=[record('photo', template='Document', type_name='Biographical Notes')],
        ))
        output.assets[0].qc_note = 'This photograph already fills the frame.'
        output.assets[0].required_crop_standard = 'Tight Border'
        apply_governing_constraints(output)
        self.assertEqual(output.assets[0].action_needed, 'No Adjustment')
        self.assertEqual(output.assets[0].required_crop_standard, 'Archival Border')
        self.assertEqual(output.assets[0].qc_status, 'Pass')

    def test_mounted_photograph_keeps_a_distinct_derivative_action(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('page.jpg', 'book', action='Crop / Recrop')],
            records=[record('book', template='Document', type_name='Biographical Notes')],
        ))
        output.assets[0].qc_note = 'A mounted photograph on this page needs its own cropped deliverable.'
        output.records[0].further_detail = 'Notebook binding.'
        apply_governing_constraints(output)
        self.assertEqual(output.assets[0].action_needed, 'Crop / Recrop + Create Derivative Crop')
        self.assertEqual(output.assets[0].qc_status, 'Action Required')

    def test_plain_crop_matches_crop_recrop_but_not_a_derivative(self):
        files = ['page.jpg']
        live = workbook(
            [item('L1', files, template='Document', type_name='Biographical Notes', readiness='Blocked - Cropping/QC')],
            [qc('page.jpg', 'L1', action='Crop / Recrop', status='Action Required', standard='Book Page Edge')],
            tasks=[{'Digital Asset': 'page.jpg', 'Action Needed': 'Crop / Recrop', 'Task ID': 'T1'}],
        )
        benchmark = workbook(
            [item('B1', files, template='Document', type_name='Biographical Notes', readiness='Blocked - Cropping/QC')],
            [qc('page.jpg', 'B1', action='Crop', status='Action Required', standard='Book Page Edge')],
            tasks=[{'Digital Asset': 'page.jpg', 'Action Needed': 'Crop', 'Task ID': 'T9'}],
        )
        self.assertEqual(assess_archival(live, benchmark)['outcome'], 'archival_accept')
        derivative = workbook(
            [item('B1', files, template='Document', type_name='Biographical Notes', readiness='Blocked - Cropping/QC')],
            [qc('page.jpg', 'B1', action='Crop + Create Derivative Crop', status='Action Required', standard='Book Page Edge')],
            tasks=[{'Digital Asset': 'page.jpg', 'Action Needed': 'Crop + Create Derivative Crop', 'Task ID': 'T9'}],
        )
        self.assertEqual(assess_archival(live, derivative)['outcome'], 'archival_material_mismatch')

    def test_file_folder_against_scrapbook_page_is_a_benchmark_discrepancy(self):
        live = workbook(
            [],
            [qc('folder.jpg', 'REF', action='N/A', status='Reference Only', standard='N/A')],
        )
        live['Metadata References'] = [{
            'Reference ID': 'REF', 'Digital Assets': 'folder.jpg', 'Template': 'Metadata Only',
            'Metadata Readiness': 'Metadata Reference', 'Notes': 'Blank file folder.',
        }]
        benchmark = workbook(
            [item('B1', ['folder.jpg'], template='Scrapbook Page', type_name='Scrapbook Page', readiness='Blocked - Cropping/QC')],
            [qc('folder.jpg', 'B1', action='Crop', status='Action Required', standard='Scrapbook Page Edge')],
            tasks=[{'Digital Asset': 'folder.jpg', 'Action Needed': 'Crop', 'Task ID': 'T1'}],
        )
        result = assess_archival(live, benchmark)
        self.assertNotEqual(result['outcome'], 'archival_accept')
        self.assertTrue(any(row.get('discrepancy') == 'benchmark_discrepancy' for row in result['differences']))

    def test_unresolved_review_blocks_acceptance_and_box_completion(self):
        live = workbook(
            [item('L1', ['page.jpg'], readiness='Review')],
            [qc('page.jpg', 'L1')],
        )
        live['Itemization'][0]['Review Note'] = 'Held for review: template is unresolved classification.'
        live['Itemization'][0]['Template'] = ''
        benchmark = workbook(
            [item('B1', ['page.jpg'])],
            [qc('page.jpg', 'B1')],
        )
        self.assertNotEqual(assess_archival(live, benchmark)['outcome'], 'archival_accept')
        rows = [{'Source Group / Folder': folder, 'Batch Status': 'Committed - Validated'} for folder in ('F01', 'F02', 'F03', 'F04', 'F05', 'F06')]
        self.assertEqual(box_status(rows, ['F01', 'F02', 'F03', 'F04', 'F05', 'F06'], 'None', live['Itemization']), 'In Progress')


class QcCorrectionTests(unittest.TestCase):
    """Deterministic mapping of mocked inspection rows. These do not prove model vision."""

    def test_prompt_keeps_the_governing_distinctions(self):
        text = decision_instructions()
        self.assertIn('descriptive inventory', text)
        self.assertIn('not Research Notes', text)
        self.assertIn('excess black capture background', text)
        self.assertIn('slight archival edge', text)
        self.assertIn('180-degree', text)
        self.assertNotIn('F04_004', text)
        self.assertNotIn('Photograph_016b', text)

    def test_context_inventory_and_blank_verso_are_one_metadata_reference(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[
                asset('caption-sheet.jpg', 'reference', action='Rotate'),
                asset('caption-verso.jpg', 'reference', action='Rotate'),
            ],
            records=[record('reference', template='Document', type_name='Research Notes', readiness='Ready')],
        ))
        output.records[0].further_detail = (
            'Descriptive inventory that describes the surrounding photographs and functions as context. '
            'The sheet lists citations for those items.'
        )
        output.assets[1].qc_note = 'Blank verso.'
        apply_governing_constraints(output)
        row = output.records[0]
        self.assertEqual(row.record_kind, 'Metadata Only')
        self.assertEqual(row.template, 'Metadata Only')
        self.assertEqual(row.type, '')
        self.assertEqual(row.metadata_readiness, 'Metadata Reference')
        self.assertEqual(row.production_task_count, '0')
        self.assertTrue(all(item.action_needed == 'N/A' for item in output.assets))
        self.assertTrue(all(item.queue_eligible == 'No' for item in output.assets))

    def test_collection_research_notes_and_blank_verso_stay_a_normal_record(self):
        front = asset('notes.jpg', 'notes', action='No Adjustment')
        verso = asset('notes-verso.jpg', 'notes', action='Rotate')
        verso.qc_note = 'Blank verso.'
        notes = record('notes', kind='Metadata Only', template='Metadata Only', type_name='', readiness='Metadata Reference')
        notes.further_detail = 'Typed citation index and its verso are the collection item.'
        output = apply_governing_constraints(InspectionOutput(assets=[front, verso], records=[notes]))
        row = output.records[0]
        self.assertEqual(row.record_kind, 'Normal Odyssey')
        self.assertEqual(row.template, 'Document')
        self.assertEqual(row.type, 'Research Notes')
        self.assertEqual(output.assets[1].action_needed, 'No Adjustment')
        self.assertEqual(output.assets[1].queue_eligible, 'No')
        self.assertEqual(row.production_task_count, '0')
        self.assertNotEqual(row.metadata_readiness, 'Metadata Reference')

    def test_a_sheet_that_supports_one_item_is_not_forced_to_metadata_only(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('print.jpg', 'photo', action='No Adjustment')],
            records=[record('photo', template='Photographic Image', type_name='Photograph')],
        ))
        output.records[0].further_detail = 'Descriptive sheet associated with this one photograph; stays with this item.'
        apply_governing_constraints(output)
        self.assertEqual(output.records[0].record_kind, 'Normal Odyssey')
        self.assertEqual(output.records[0].template, 'Photographic Image')

    def test_excess_capture_background_keeps_the_crop_and_blocks_readiness(self):
        page = asset('page.jpg', 'page', action='Archival Border Crop')
        page.qc_note = 'Excess black capture background surrounds the page.'
        page.required_crop_standard = 'Archival Border'
        output = apply_governing_constraints(InspectionOutput(
            assets=[page],
            records=[record('page', template='Document', type_name='Report', readiness='Ready')],
        ))
        self.assertEqual(output.assets[0].action_needed, 'Archival Border Crop')
        self.assertEqual(output.assets[0].required_crop_standard, 'Archival Border')
        self.assertEqual(output.assets[0].qc_status, 'Action Required')
        self.assertEqual(output.assets[0].queue_eligible, 'Yes')
        self.assertEqual(output.records[0].metadata_readiness, 'Blocked - Cropping/QC')
        self.assertEqual(output.records[0].production_task_count, '1')

    def test_a_compliant_archival_edge_does_not_create_a_crop_task(self):
        print_asset = asset('print.jpg', 'photo', action='Archival Border Crop')
        print_asset.qc_note = 'A slight archival edge surrounds the print.'
        output = apply_governing_constraints(InspectionOutput(
            assets=[print_asset],
            records=[record('photo', template='Photographic Image', type_name='Photograph', readiness='Ready')],
        ))
        self.assertEqual(output.assets[0].action_needed, 'No Adjustment')
        self.assertEqual(output.assets[0].required_crop_standard, 'Archival Border')
        self.assertEqual(output.assets[0].qc_status, 'Pass')
        self.assertEqual(output.assets[0].queue_eligible, 'No')
        self.assertEqual(output.records[0].metadata_readiness, 'Ready')
        self.assertEqual(output.records[0].production_task_count, '0')

    def test_naming_the_standard_alone_does_not_create_a_crop_task(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('page.jpg', 'page', action='No Adjustment')],
            records=[record('page', readiness='Ready')],
        ))
        output.assets[0].required_crop_standard = 'Archival Border'
        apply_governing_constraints(output)
        self.assertEqual(output.assets[0].action_needed, 'No Adjustment')
        self.assertEqual(output.assets[0].queue_eligible, 'No')
        self.assertEqual(output.records[0].metadata_readiness, 'Ready')

    def test_required_rotation_is_kept_and_upright_blank_and_uncertain_controls_are_not(self):
        turned = asset('print.jpg', 'turned', action='Rotate')
        upright = asset('upright.jpg', 'upright', action='No Adjustment')
        blank = asset('blank.jpg', 'blank', action='Rotate')
        layout = asset('layout.jpg', 'layout', action='No Adjustment')
        unsure = asset('unsure.jpg', 'unsure', action='Review')
        turned.qc_note = 'The print is 180 degrees from upright.'
        upright.qc_note = 'The photograph is upright.'
        blank.qc_note = 'Blank image.'
        layout.qc_note = 'Intentional layout, already upright.'
        unsure.qc_note = 'Orientation cannot be determined.'
        output = apply_governing_constraints(InspectionOutput(
            assets=[turned, upright, blank, layout, unsure],
            records=[
                record('turned', template='Photographic Image', type_name='Photograph', readiness='Ready'),
                record('upright', template='Photographic Image', type_name='Photograph', readiness='Ready'),
                record('blank', template='Photographic Image', type_name='Photograph', readiness='Ready'),
                record('layout', template='Photographic Image', type_name='Photograph', readiness='Ready'),
                record('unsure', template='Photographic Image', type_name='Photograph', readiness='Review'),
            ],
        ))
        self.assertEqual(output.assets[0].action_needed, 'Rotate')
        self.assertEqual(output.assets[0].queue_eligible, 'Yes')
        self.assertEqual(output.records[0].metadata_readiness, 'Blocked - Cropping/QC')
        self.assertEqual(output.assets[1].action_needed, 'No Adjustment')
        self.assertEqual(output.assets[1].queue_eligible, 'No')
        self.assertEqual(output.records[1].metadata_readiness, 'Ready')
        self.assertEqual(output.assets[2].action_needed, 'No Adjustment')
        self.assertEqual(output.assets[2].queue_eligible, 'No')
        self.assertEqual(output.assets[3].action_needed, 'No Adjustment')
        self.assertEqual(output.assets[4].action_needed, 'Review')
        self.assertEqual(output.assets[4].queue_eligible, 'No')
        self.assertEqual(output.records[4].metadata_readiness, 'Review')

    def test_same_batch_context_keeps_the_reference_separate_from_the_document(self):
        sheet = asset('caption-sheet.jpg', 'reference', action='Rotate')
        verso = asset('caption-verso.jpg', 'reference', action='Rotate')
        letter_asset = asset('letter.jpg', 'letter', action='Archival Border Crop')
        letter_verso = asset('letter-verso.jpg', 'letter', action='No Adjustment')
        verso.qc_note = 'Blank verso.'
        letter_asset.qc_note = 'Excess black capture background surrounds the letter.'
        letter_verso.qc_note = 'Blank verso.'
        output = apply_governing_constraints(InspectionOutput(
            assets=[sheet, verso, letter_asset, letter_verso],
            records=[
                record('reference', template='Document', type_name='Research Notes'),
                record('letter', template='Correspondence', type_name='Letter', readiness='Ready'),
            ],
        ))
        output.records[0].further_detail = 'Descriptive inventory that describes the surrounding items and functions as context.'
        apply_governing_constraints(output)
        reference = output.records[0]
        letter = output.records[1]
        self.assertEqual(reference.record_kind, 'Metadata Only')
        self.assertEqual(reference.production_task_count, '0')
        self.assertEqual(letter.record_kind, 'Normal Odyssey')
        self.assertEqual(letter.type, 'Letter')
        self.assertEqual(output.assets[2].action_needed, 'Archival Border Crop')
        self.assertEqual(letter.metadata_readiness, 'Blocked - Cropping/QC')
        self.assertEqual({row.assigned_record_id for row in output.assets[:2]}, {'reference'})
        self.assertEqual({row.assigned_record_id for row in output.assets[2:]}, {'letter'})

    def test_a_later_batch_does_not_reclassify_the_continued_document(self):
        letter_asset = asset('letter.jpg', 'letter', action='Archival Border Crop')
        letter_asset.qc_note = 'Excess black capture background surrounds the letter.'
        first = apply_governing_constraints(InspectionOutput(
            assets=[letter_asset],
            records=[record('letter', template='Correspondence', type_name='Letter', readiness='Ready', boundary='Open')],
        ))
        second = apply_governing_constraints(InspectionOutput(
            assets=[
                asset('letter-verso.jpg', 'letter', action='No Adjustment'),
                asset('caption-sheet.jpg', 'reference', action='Rotate'),
                asset('caption-verso.jpg', 'reference', action='Rotate'),
            ],
            records=[
                record('letter', template='Correspondence', type_name='Letter', readiness='Ready'),
                record('reference', template='Document', type_name='Research Notes'),
            ],
        ))
        second.records[0].further_detail = 'Next page of the same letter continued from the prior open item.'
        second.records[0].review_note = 'prior_open_item continues this physical item.'
        second.assets[0].qc_note = 'Blank verso.'
        second.records[1].further_detail = 'Descriptive inventory that describes the surrounding photographs and functions as context.'
        second.assets[2].qc_note = 'Blank verso.'
        apply_governing_constraints(second)
        self.assertEqual(first.records[0].metadata_readiness, 'Blocked - Cropping/QC')
        self.assertEqual(second.records[0].record_kind, 'Normal Odyssey')
        self.assertEqual(second.records[0].type, 'Letter')
        self.assertEqual(second.assets[0].action_needed, 'No Adjustment')
        self.assertEqual(second.records[1].record_kind, 'Metadata Only')
        self.assertEqual(second.records[1].production_task_count, '0')

    def test_crop_and_rotation_survive_staging(self):
        page = asset('page.jpg', 'page', action='Archival Border Crop')
        turned = asset('print.jpg', 'print', action='Rotate')
        compliant = asset('compliant.jpg', 'compliant', action='No Adjustment')
        page.qc_note = 'Excess black capture background surrounds the page.'
        page.required_crop_standard = 'Archival Border'
        turned.qc_note = 'The print is 180 degrees from upright.'
        compliant.qc_note = 'A slight archival edge surrounds the print.'
        output = apply_governing_constraints(InspectionOutput(
            assets=[page, turned, compliant],
            records=[
                record('page', readiness='Ready'),
                record('print', template='Photographic Image', type_name='Photograph', readiness='Ready'),
                record('compliant', template='Photographic Image', type_name='Photograph', readiness='Ready'),
            ],
        ))
        sheets = MemorySheets()
        install_job(sheets, [queue_row(names=['page.jpg', 'print.jpg', 'compliant.jpg'])])
        Orchestrator(sheets, None, CapturingInspector(output=output), Settings()).run('HIT-1')
        tasks = sheets.rows('register', 'Production Queue')
        self.assertEqual(sorted(row['Digital Asset'] for row in tasks), ['page.jpg', 'print.jpg'])
        self.assertEqual(sorted(row['Action Needed'] for row in tasks), ['Archival Border Crop', 'Rotate'])
        staged = {row['Digital Asset']: row for row in sheets.rows('register', 'Asset QC')}
        self.assertEqual(staged['page.jpg']['QC Status'], 'Action Required')
        self.assertEqual(staged['print.jpg']['Action Needed'], 'Rotate')
        self.assertEqual(staged['compliant.jpg']['Action Needed'], 'No Adjustment')
        committed = sheets.rows('register', 'Itemization') + sheets.rows('register', 'Record Staging')

        def readiness_for(name):
            for row in committed:
                assets = ' '.join(row.get(field) or '' for field in (
                    'Digital Assets', 'New Batch Assets', 'Full Record Assets After Commit',
                ))
                if name in assets:
                    return row.get('Metadata Readiness')
            return ''

        self.assertEqual(readiness_for('page.jpg'), 'Blocked - Cropping/QC')
        self.assertEqual(readiness_for('print.jpg'), 'Blocked - Cropping/QC')
        self.assertEqual(readiness_for('compliant.jpg'), 'Ready')

    def test_negated_keywords_do_not_override_the_stated_decision(self):
        denied_crop = asset('page.jpg', 'page', action='Archival Border Crop')
        denied_crop.qc_note = 'No excess background; a slight border remains.'
        denied_crop.required_crop_standard = 'Archival Border'
        real_crop = asset('wide.jpg', 'wide', action='Archival Border Crop')
        real_crop.qc_note = 'No slight edge. Excess black capture background surrounds the page.'
        real_crop.required_crop_standard = 'Archival Border'
        denied_inventory = record('notes', kind='Metadata Only', template='Metadata Only', type_name='', readiness='Metadata Reference')
        denied_inventory.further_detail = 'Typed citation index. This sheet is not a descriptive inventory.'
        affirmed_inventory = record('reference', template='Document', type_name='Research Notes')
        affirmed_inventory.further_detail = (
            'Not associated with this one item. Descriptive inventory that describes the surrounding photographs and functions as context.'
        )
        not_blank = asset('print.jpg', 'turned', action='Rotate')
        not_blank.qc_note = 'The print is not blank.'
        blank_turned = asset('back.jpg', 'turned180', action='Rotate')
        blank_turned.qc_note = 'Blank verso. The print is 180 degrees from upright.'
        unsure = asset('unsure.jpg', 'unsure', action='Rotate')
        unsure.qc_note = 'Orientation cannot be determined.'
        output = apply_governing_constraints(InspectionOutput(
            assets=[denied_crop, real_crop, not_blank, blank_turned, unsure],
            records=[
                record('page', template='Document', type_name='Report', readiness='Ready'),
                record('wide', template='Document', type_name='Report', readiness='Ready'),
                denied_inventory,
                affirmed_inventory,
                record('turned', template='Photographic Image', type_name='Photograph', readiness='Ready'),
                record('turned180', template='Photographic Image', type_name='Photograph', readiness='Ready'),
                record('unsure', template='Photographic Image', type_name='Photograph', readiness='Ready'),
            ],
        ))
        self.assertEqual(output.assets[0].action_needed, 'No Adjustment')
        self.assertEqual(output.assets[0].queue_eligible, 'No')
        self.assertEqual(output.records[0].metadata_readiness, 'Ready')
        self.assertEqual(output.assets[1].action_needed, 'Archival Border Crop')
        self.assertEqual(output.records[1].metadata_readiness, 'Blocked - Cropping/QC')
        self.assertEqual(output.records[2].record_kind, 'Normal Odyssey')
        self.assertEqual(output.records[2].type, 'Research Notes')
        self.assertEqual(output.records[3].record_kind, 'Metadata Only')
        self.assertEqual(output.records[3].production_task_count, '0')
        self.assertEqual(output.assets[2].action_needed, 'Rotate')
        self.assertEqual(output.records[4].metadata_readiness, 'Blocked - Cropping/QC')
        self.assertEqual(output.assets[3].action_needed, 'Rotate')
        self.assertEqual(output.assets[4].action_needed, 'Review')
        self.assertEqual(output.assets[4].queue_eligible, 'No')
        self.assertEqual(output.records[6].metadata_readiness, 'Review')

    def test_a_denial_after_the_phrase_does_not_cancel_another_clause(self):
        absent = asset('absent.jpg', 'absent', action='Archival Border Crop')
        absent.qc_note = 'Excess background is absent.'
        absent.required_crop_standard = 'Archival Border'
        prefixed = asset('prefixed.jpg', 'prefixed', action='Archival Border Crop')
        prefixed.qc_note = 'No excess background.'
        prefixed.required_crop_standard = 'Archival Border'
        present = asset('present.jpg', 'present', action='Archival Border Crop')
        present.qc_note = 'Excess background is present.'
        present.required_crop_standard = 'Archival Border'
        mixed = asset('mixed.jpg', 'mixed', action='Archival Border Crop')
        mixed.qc_note = 'Excess background is absent. Excess black capture background surrounds the page.'
        mixed.required_crop_standard = 'Archival Border'
        denied_inventory = record('denied', kind='Metadata Only', template='Metadata Only', type_name='', readiness='Metadata Reference')
        denied_inventory.further_detail = 'Descriptive inventory is absent. Typed citation index.'
        affirmed_inventory = record('affirmed', template='Document', type_name='Report')
        affirmed_inventory.further_detail = (
            'Descriptive inventory that describes the surrounding photographs is present. The citation index is absent.'
        )
        blank_absent = asset('blank-absent.jpg', 'blank-absent', action='Rotate')
        blank_absent.qc_note = 'Blank verso is absent.'
        blank_present = asset('blank-present.jpg', 'blank-present', action='Rotate')
        blank_present.qc_note = 'Blank verso is present.'
        mixed_turn = asset('mixed-turn.jpg', 'mixed-turn', action='Rotate')
        mixed_turn.qc_note = 'Blank verso is absent. The print is 180 degrees from upright.'
        output = apply_governing_constraints(InspectionOutput(
            assets=[absent, prefixed, present, mixed, blank_absent, blank_present, mixed_turn],
            records=[
                record('absent', template='Document', type_name='Report', readiness='Ready'),
                record('prefixed', template='Document', type_name='Report', readiness='Ready'),
                record('present', template='Document', type_name='Report', readiness='Ready'),
                record('mixed', template='Document', type_name='Report', readiness='Ready'),
                denied_inventory,
                affirmed_inventory,
                record('blank-absent', template='Photographic Image', type_name='Photograph', readiness='Ready'),
                record('blank-present', template='Photographic Image', type_name='Photograph', readiness='Ready'),
                record('mixed-turn', template='Photographic Image', type_name='Photograph', readiness='Ready'),
            ],
        ))
        self.assertEqual(output.assets[0].action_needed, 'No Adjustment')
        self.assertEqual(output.records[0].metadata_readiness, 'Ready')
        self.assertEqual(output.assets[1].action_needed, 'No Adjustment')
        self.assertEqual(output.records[1].metadata_readiness, 'Ready')
        self.assertEqual(output.assets[2].action_needed, 'Archival Border Crop')
        self.assertEqual(output.records[2].metadata_readiness, 'Blocked - Cropping/QC')
        self.assertEqual(output.assets[3].action_needed, 'Archival Border Crop')
        self.assertEqual(output.records[3].metadata_readiness, 'Blocked - Cropping/QC')
        self.assertEqual(output.records[4].record_kind, 'Normal Odyssey')
        self.assertEqual(output.records[4].type, 'Research Notes')
        self.assertEqual(output.records[5].record_kind, 'Metadata Only')
        self.assertEqual(output.records[5].production_task_count, '0')
        self.assertEqual(output.assets[4].action_needed, 'Rotate')
        self.assertEqual(output.records[6].metadata_readiness, 'Blocked - Cropping/QC')
        self.assertEqual(output.assets[5].action_needed, 'No Adjustment')
        self.assertEqual(output.assets[6].action_needed, 'Rotate')
        self.assertEqual(output.records[8].metadata_readiness, 'Blocked - Cropping/QC')


class ParentDiscoveryTests(unittest.TestCase):
    def test_parent_only_authorization_orders_f01_through_f06_and_skips_delhivery(self):
        names = ['F06', 'F01', 'F03', 'F02', 'F05', 'F04']
        sources = [source(folder.lower(), f'{folder}.jpg', f'Chapters Box 19/{folder}/{folder}.jpg') for folder in names]
        sources.append(source('del', 'copy.jpg', 'Chapters Box 19/Delhivery/F02/copy.jpg'))
        selected = select_authorized_sources(sources)
        self.assertEqual([item.folder_name for item in selected], ['F01', 'F02', 'F03', 'F04', 'F05', 'F06'])
        self.assertNotIn('copy.jpg', [item.name for item in selected])


class ClassificationGuardTests(unittest.TestCase):
    def test_letter_research_notes_and_scrapbook_page_cannot_stay_metadata_only(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[
                asset('TriDelta_Year2_Chapters_Box19_F03_035.jpg', 'letter', action='No Adjustment'),
                asset('TriDelta_Year2_Chapters_Box19_F03_036.jpg', 'letter', action='No Adjustment'),
                asset('TriDelta_Year2_Chapters_Box19_F03_029.jpg', 'notes', action='No Adjustment'),
                asset('TriDelta_Year2_Chapters_Box19_F04_001.jpg', 'page', action='Publication Crop'),
            ],
            records=[
                record('letter', kind='Metadata Only', template='Metadata Only', type_name='Letter'),
                record('notes', kind='Metadata Only', template='Metadata Only', type_name='Research Notes'),
                record('page', kind='Metadata Only', template='Scrapbook Page', type_name='Scrapbook Page'),
            ],
        ))
        self.assertTrue(all(row.record_kind == 'Normal Odyssey' for row in output.records))
        notes = next(row for row in output.records if row.type == 'Research Notes')
        self.assertEqual(notes.template, 'Document')
        self.assertNotEqual(notes.metadata_readiness, 'Metadata Reference')
        self.assertTrue(all(row.queue_eligible == 'No' for row in output.assets))

    def test_ledger_is_not_rewritten_to_document(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('notes.jpg', 'ledger', action='Publication Crop')],
            records=[record('ledger', template='Ledger', type_name='Ledger', readiness='Blocked - Cropping/QC')],
        ))
        self.assertEqual(output.records[0].template, 'Ledger')
        self.assertEqual(output.records[0].type, 'Ledger')
        self.assertEqual(output.records[0].metadata_readiness, 'Blocked - Cropping/QC')
        self.assertEqual(output.assets[0].queue_eligible, 'Yes')

    def test_unsupported_action_is_held_and_not_mapped_to_action_required(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('page.jpg', 'item', action='Needs Adjustment', eligible='Yes')],
            records=[record('item')],
        ))
        self.assertEqual(output.assets[0].action_needed, 'Review')
        self.assertNotEqual(output.assets[0].action_needed, 'Action Required')
        self.assertEqual(output.assets[0].queue_eligible, 'No')
        self.assertEqual(output.records[0].metadata_readiness, 'Review')
        self.assertIn('Needs Adjustment', output.records[0].review_note)

    def test_approved_crop_still_creates_one_task_per_asset(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[
                asset('a.jpg', 'item', action='Publication Crop', eligible='No'),
                asset('b.jpg', 'item', action='publication crop', eligible='No'),
                asset('c.jpg', 'item', action='No Adjustment', eligible='Yes'),
            ],
            records=[record('item')],
        ))
        self.assertEqual([row.queue_eligible for row in output.assets], ['Yes', 'Yes', 'No'])
        self.assertEqual(output.assets[1].action_needed, 'Publication Crop')
        self.assertEqual(output.records[0].metadata_readiness, 'Blocked - Cropping/QC')
        self.assertEqual(output.records[0].production_task_count, '2')

    def test_crop_standard_capitalization_is_canonicalized(self):
        output = apply_governing_constraints(InspectionOutput(
            assets=[asset('photo.jpg', 'item')],
            records=[record('item', template='Photographic Image', type_name='Photograph')],
        ))
        output.assets[0].required_crop_standard = 'archival border'
        apply_governing_constraints(output)
        self.assertEqual(output.assets[0].required_crop_standard, 'Archival Border')

    def test_batch_boundary_keeps_one_physical_item(self):
        class Continuing(CapturingInspector):
            def inspect(self, job, batch_id, sources, context, rules):
                prior = context.get('prior_open_item')
                self.calls.append(prior)
                filenames = [source.name for source in sources]
                if prior in (None, '', 'None'):
                    chosen = InspectionOutput(
                        assets=[asset(filenames[0], 'open-item', action='No Adjustment')],
                        records=[record('open-item', boundary='Open - same physical item continues')],
                    )
                    chosen.records[0].review_note = 'The verso is the next authorized image of this same sheet.'
                else:
                    chosen = InspectionOutput(
                        assets=[asset(filenames[0], prior, action='No Adjustment')],
                        records=[record(prior, boundary='Closed')],
                    )
                    chosen.records[0].record_operation = 'Update Existing Open Record'
                return chosen, {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1, 'elapsed_seconds': 0.1, 'response_id': 'resp'}

        sheets = MemorySheets()
        install_job(sheets, [
            queue_row(names=['front.jpg'], **{'Project': 'Example Project', 'Box': 'Example Box 19', 'Source Group / Folder': 'F03'}),
            queue_row(batch_id='HIT-1-B0002', names=['verso.jpg'], **{
                'Project': 'Example Project', 'Box': 'Example Box 19', 'Source Group / Folder': 'F03', 'Prior Open Item': '',
            }),
        ])
        Orchestrator(sheets, None, Continuing(), Settings()).run('HIT-1')
        items = sheets.rows('register', 'Itemization')
        self.assertEqual(len(items), 1)
        self.assertIn('front.jpg', items[0]['Digital Assets'])
        self.assertIn('verso.jpg', items[0]['Digital Assets'])
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')
        self.assertEqual(sheets.rows('register', 'Batch Queue')[1]['Batch Status'], 'Committed - Validated')


class CloseoutTests(unittest.TestCase):
    def test_f01_through_f06_with_no_open_item_completes_the_box(self):
        sheets = TrackingSheets()
        rows = []
        for index, folder in enumerate(('F01', 'F02', 'F03', 'F04', 'F05', 'F06'), 1):
            filename = f'{folder}.jpg'
            rows.append(queue_row(
                batch_id=f'HIT-1-B{index:04d}',
                names=[filename],
                **{
                    'Source Group / Folder': folder,
                    'Batch Status': 'Committed - Validated',
                    'Resulting Open Item': 'None',
                    'Notes': json.dumps({'manifest': [{
                        'id': filename, 'name': filename, 'folder_id': folder, 'folder_name': folder,
                        'path': '/' + filename, 'mime_type': 'image/jpeg',
                    }]}),
                },
            ))
            sheets.write('register', 'Folder Processing Unit Summary', {
                'Processing Unit / Source Folder': folder,
                'Source Group / Folder': folder,
                'Box': 'Chapters Box 19',
                'Processing Status': 'Completed - Validated',
            }, ('Processing Unit / Source Folder', folder))
        install_job(sheets, rows)
        sheets.store[('register', 'Asset Staging')] = [
            {'Batch ID': row['Batch ID'], 'Digital Asset': row['Start Filename'], '_row': index}
            for index, row in enumerate(rows, 2)
        ]
        sheets.store[('register', 'Record Staging')] = [
            {'Batch ID': row['Batch ID'], 'Proposed Record ID': f'R{index}', '_row': index}
            for index, row in enumerate(rows, 2)
        ]
        sheets.set_field('register', 'Open Intellectual Item', 'None')
        sheets.set_field('register', 'Unresolved Boundary Item', 'None')
        sheets.write('register', 'Box Summary', {'Box': 'Chapters Box 19', 'Box Status': 'In Progress'}, ('Box', 'Chapters Box 19'))
        self.assertEqual(box_status(rows, ['F01', 'F02', 'F03', 'F04', 'F05', 'F06'], 'None'), 'Completed - Validated')
        reconcile_register(sheets, 'register', 'HIT-1', rows)
        status = sheets.rows('register', 'Box Summary')[0]
        self.assertEqual(status['Box Status'], 'Completed - Validated')
        resume = sheets.field('register', 'Resume Instruction')
        self.assertIn('source processing is complete', resume)
        self.assertNotIn('Authorize the next source folder', resume)

    def test_a_partial_folder_set_stays_in_progress(self):
        # Authorized scope still has unfinished work.
        self.assertEqual(box_status([
            {'Source Group / Folder': 'F05', 'Batch Status': 'Committed - Validated'},
            {'Source Group / Folder': 'F06', 'Batch Status': 'Authorized / Pending Inspection'},
        ], ['F05'], 'None'), 'In Progress')
        # Completing the frozen authorized queue closes the box even if it is not F01–F06.
        self.assertEqual(box_status([
            {'Source Group / Folder': 'F05', 'Batch Status': 'Committed - Validated'},
            {'Source Group / Folder': 'F06', 'Batch Status': 'Committed - Validated'},
        ], ['F05', 'F06'], 'None'), 'Completed - Validated')


class ArchivalComparisonTests(unittest.TestCase):
    def test_capitalization_and_paraphrase_do_not_decide_acceptance(self):
        files = ['front.jpg', 'verso.jpg']
        live = workbook(
            [item('L1', files, detail='Front and verso belong to one item.')],
            [qc(name, 'L1', standard='archival border') for name in files],
        )
        benchmark = workbook(
            [item('B9', files, detail='Front and back belong to one item.')],
            [qc(name, 'B9', standard='Archival Border') for name in files],
        )
        strict = compare_workbooks(live, benchmark)
        self.assertEqual(strict['outcome'], 'semantic_benchmark_mismatch')
        self.assertEqual(strict['strict_outcome'], 'semantic_benchmark_mismatch')
        self.assertFalse(strict['matches'])
        archival = assess_archival(live, benchmark)
        self.assertEqual(classify_prose(live['Itemization'][0]['Further Detail'], benchmark['Itemization'][0]['Further Detail']), 'equivalent')
        self.assertEqual(archival['outcome'], 'archival_accept')
        self.assertTrue(archival['matches'])
        self.assertTrue(any(row['prose_class'] == 'equivalent' for row in archival['differences']))

    def test_ready_versus_blocked_and_a_missing_task_remain_material(self):
        live = workbook(
            [item('L1', ['page.jpg'], readiness='Ready')],
            [qc('page.jpg', 'L1', action='No Adjustment', status='Pass')],
        )
        benchmark = workbook(
            [item('B1', ['page.jpg'], readiness='Blocked - Cropping/QC')],
            [qc('page.jpg', 'B1', action='Publication Crop', status='Pass')],
            tasks=[{'Digital Asset': 'page.jpg', 'Action Needed': 'Publication Crop', 'Task ID': 'T1'}],
        )
        archival = assess_archival(live, benchmark)
        self.assertEqual(archival['outcome'], 'archival_material_mismatch')
        fields = {row['field'] for row in archival['differences'] if row['material']}
        self.assertIn('Metadata Readiness', fields)
        self.assertIn('Action Needed', fields)

    def test_folder_row_count_is_not_material(self):
        files = ['a.jpg']
        live = workbook([item('L1', files)], [qc('a.jpg', 'L1')], folders=6)
        benchmark = workbook([item('B1', files)], [qc('a.jpg', 'B1')], folders=18)
        archival = assess_archival(live, benchmark)
        self.assertEqual(archival['outcome'], 'archival_accept')
        structure = [row for row in archival['differences'] if row['sheet'] == 'Folder Processing Unit Summary']
        self.assertEqual(structure[0]['material'], False)

    def test_contradictory_prose_is_material_and_uncertain_prose_needs_review(self):
        self.assertEqual(classify_prose('The record is Ready.', 'The record is Blocked - Cropping/QC.'), 'material')
        self.assertEqual(classify_prose('Edge wear differs on this copy.', 'The paper stock looks older.'), 'needs_human_review')
        files = ['a.jpg']
        live = workbook([item('L1', files, notes='Edge wear differs on this copy.')], [qc('a.jpg', 'L1')])
        benchmark = workbook([item('B1', files, notes='The paper stock looks older.')], [qc('a.jpg', 'B1')])
        self.assertEqual(assess_archival(live, benchmark)['outcome'], 'archival_needs_human_review')

    def test_match_summary_counts_shared_decisions_and_not_wording(self):
        files = ['front.jpg', 'verso.jpg']
        live = workbook(
            [item('L1', files, detail='The sheet is creased along the left edge.')],
            [qc(name, 'L1') for name in files],
        )
        benchmark = workbook(
            [item('B1', files, detail='Ready for metadata after the verso was checked.')],
            [qc(name, 'B1') for name in files],
        )
        result = assess_archival(live, benchmark)
        self.assertEqual(result['match']['files']['percent'], 100)
        self.assertEqual(result['match']['file_groups']['percent'], 100)
        self.assertEqual(result['match']['qc_status']['percent'], 100)
        split = workbook(
            [item('L1', ['front.jpg']), item('L2', ['verso.jpg'])],
            [qc('front.jpg', 'L1'), qc('verso.jpg', 'L2')],
        )
        split_result = assess_archival(split, benchmark)
        self.assertEqual(split_result['match']['files']['percent'], 100)
        self.assertEqual(split_result['match']['files_in_same_group']['matched'], 0)
        self.assertLess(split_result['match']['files_in_same_group']['percent'], 100)

    def test_policy_rejects_a_threshold_and_the_needs_adjustment_synonym(self):
        policy = assess_archival(workbook([], []), workbook([], []))['policy']
        self.assertIn('no similarity threshold', policy.casefold())
        self.assertIn('Needs Adjustment is not Action Required', policy)
        self.assertNotIn('80%', policy)

    def test_partial_register_recognizes_files_present_in_both_workbooks(self):
        shared = 'TriDelta_Year2_Chapters_Box19_F03_029.jpg'
        verso = 'TriDelta_Year2_Chapters_Box19_F03_030.jpg'
        unprocessed = 'TriDelta_Year2_Chapters_Box19_F03_001.jpg'
        live = workbook(
            [item('TDC-B19-F03-007', [shared, verso], type_name='Research Notes')],
            [qc(shared, 'TDC-B19-F03-007', standard='Tight Border'), qc(verso, 'TDC-B19-F03-007', standard='Tight Border')],
        )
        benchmark = workbook(
            [
                item('TDC-B19-F03-029', [shared, verso], type_name='Research Notes'),
                item('TDC-B19-F03-001', [unprocessed], template='Metadata Only', type_name='Folder/Enclosure', readiness='Metadata Reference'),
            ],
            [
                qc(shared, 'TDC-B19-F03-029', standard='Scrapbook Loose Item / Tight Border'),
                qc(verso, 'TDC-B19-F03-029', standard='Scrapbook Loose Item / Tight Border'),
                qc(unprocessed, 'TDC-B19-F03-001', action='N/A', status='Reference Only', standard='N/A'),
            ],
        )
        result = assess_archival(live, benchmark, scoped=True)
        absent_from_benchmark = [
            row['key'] for row in result['differences']
            if row['reason'] == 'Source image is absent from the benchmark.'
        ]
        absent_from_live = [
            row['key'] for row in result['differences']
            if row['reason'] == 'Source image is absent from the live register.'
        ]
        self.assertEqual(absent_from_benchmark, [])
        self.assertEqual(absent_from_live, [unprocessed])
        self.assertNotIn(shared, absent_from_benchmark)
        self.assertNotIn(verso, absent_from_benchmark)
