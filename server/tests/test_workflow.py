import json
import tempfile
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.authorization import RegisterStateError, authorized_manifest, manifest_filenames
from app.benchmark import compare_workbooks
from app.diagnostics import Diagnostics
from app.images import prepare_model_image
from app.models import ASSET_HEADERS, RECORD_HEADERS, AssetRow, InspectionOutput, JobCreate, RecordRow, Source
from app.orchestrator import Orchestrator
from app.references import reference_mismatches, resolve_reference
from client_reference.commit_engine import plan_commit


def name(number):
    return f'TriDelta_Year2_Chapters_Box19_F01_{number:03d}.jpg'


def filled(headers, **values):
    row = {header: '' for header in headers}
    row.update(values)
    return row


def filled(headers, **values):
    row = {header: '' for header in headers}
    row.update(values)
    return row


class MemorySheets:
    def __init__(self, continuation='None'):
        self.store = {}
        self.continuation = {
            'Open Intellectual Item': continuation,
            'Unresolved Boundary Item': 'None',
        }

    def rows(self, sid, sheet):
        return [dict(row) for row in self.store.get((sid, sheet), [])]

    def write(self, sid, sheet, row, key=None):
        rows = self.store.setdefault((sid, sheet), [])
        matches = []
        if isinstance(key, list):
            matches = [existing for existing in rows if all(str(existing.get(name)) == str(value) for name, value in key)]
        elif key:
            matches = [existing for existing in rows if str(existing.get(key[0])) == str(key[1])]
        if len(matches) > 1:
            raise ValueError(f'Duplicate key in {sheet}: {key}')
        if matches:
            matches[0].update({name: '' if value is None else str(value) for name, value in row.items()})
            return matches[0].get('_row', 1)
        stored = {name: '' if value is None else str(value) for name, value in row.items()}
        stored['_row'] = len(rows) + 2
        rows.append(stored)
        return stored['_row']

    def clear(self, sid, sheet, row_number, key):
        rows = self.store.get((sid, sheet), [])
        self.store[(sid, sheet)] = [row for row in rows if str(row.get(key[0])) != str(key[1])]

    def field(self, sid, name):
        for row in self.rows(sid, 'Continuation State'):
            if row.get('State Field') == name:
                return row.get('Current Value') or 'None'
        return self.continuation.get(name, 'None')

    def set_field(self, sid, name, value):
        self.continuation[name] = '' if value is None else str(value)
        self.write(sid, 'Continuation State', {'State Field': name, 'Current Value': self.continuation[name]}, ('State Field', name))

    def ensure_tab(self, sid, name, headers):
        self.store.setdefault((sid, name), [])


class Settings:
    job_index_spreadsheet_id = 'index'
    job_spending_limit_usd = 100
    max_retries = 3
    batch_size = 10
    openai_model = 'gpt-test'
    openai_input_usd_per_million = 1
    openai_cached_input_usd_per_million = 0
    openai_output_usd_per_million = 1


def manifest(names, folder='F01'):
    return [{
        'id': item,
        'name': item,
        'folder_id': 'folder',
        'folder_name': folder,
        'path': '/' + item,
        'mime_type': 'image/jpeg',
    } for item in names]


def queue_row(batch_id='HIT-1-B0001', names=None, **extra):
    names = names or [name(1)]
    row = {
        'Project': 'Tri Delta Chapters',
        'Job ID': 'HIT-1',
        'Batch ID': batch_id,
        'Box': 'Chapters Box 19',
        'Source Group / Folder': 'F01',
        'Batch Status': 'Authorized / Pending Inspection',
        'Prior Open Item': 'None',
        'Resulting Open Item': 'None',
        'Inspection Attempt': '0',
        'Commit Attempt': '0',
        'Planned Asset Count': str(len(names)),
        'Start Filename': names[0],
        'End Filename': names[-1],
        'Notes': json.dumps({'manifest': manifest(names)}),
        'Error / Retry Note': '',
    }
    row.update(extra)
    return row


def install_job(sheets, rows):
    sheets.store[('index', 'HistoryIT Jobs')] = [{'Job ID': rows[0]['Job ID'], 'Spreadsheet ID': 'register'}]
    sheets.store[('register', 'Batch Queue')] = rows


class CapturingInspector:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []
        self.diagnostics = None

    def inspect(self, job, batch_id, sources, context, rules):
        self.calls.append({'batch_id': batch_id, 'context': context, 'sources': [source.name for source in sources]})
        if self.error:
            raise self.error
        return self.output, {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1, 'elapsed_seconds': 0.1, 'response_id': 'resp'}


class PriorAndManifestTests(unittest.TestCase):
    def test_first_batch_prior_none_does_not_invent_an_open_item(self):
        sheets = MemorySheets()
        install_job(sheets, [queue_row()])
        inspector = CapturingInspector(error=RuntimeError('stop'))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        context = inspector.calls[0]['context']
        self.assertEqual(context['prior_open_item'], 'None')
        self.assertEqual(context['prior_open_item_source'], 'Batch Queue Prior Open Item')
        self.assertEqual(context['existing_open_record'], [])
        self.assertNotIn('continuation', context)
        self.assertEqual(context['run_context'], [])

    def test_stale_continuation_on_a_clean_first_batch_is_rejected(self):
        sheets = MemorySheets(continuation='OLD-ITEM')
        install_job(sheets, [queue_row()])
        with tempfile.TemporaryDirectory() as directory:
            inspector = CapturingInspector(error=RuntimeError('should not inspect'))
            inspector.diagnostics = Diagnostics(directory)
            Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
            self.assertEqual(inspector.calls, [])
            row = sheets.rows('register', 'Batch Queue')[0]
            self.assertEqual(row['Batch Status'], 'Blocked - Human Review')
            self.assertIn('Clean first-batch validation failed', row['Error / Retry Note'])
            self.assertIn('OLD-ITEM', row['Error / Retry Note'])
            self.assertIn('Diagnostic:', row['Error / Retry Note'])
            self.assertTrue(list(Path(directory).glob('*.json')))

    def test_batch_queue_prior_wins_over_continuation_state(self):
        sheets = MemorySheets(continuation='OLD-ITEM')
        prior = 'TDC-B19-F01-001'
        sheets.store[('register', 'Itemization')] = [{
            'Item ID': prior,
            'Digital Assets': name(4),
            'Digital Asset Count': '1',
        }]
        first = queue_row(batch_id='HIT-1-B0001', names=[name(4)], **{
            'Batch Status': 'Committed - Validated', 'Resulting Open Item': prior,
        })
        second = queue_row(batch_id='HIT-1-B0002', names=[name(5)], **{'Prior Open Item': prior})
        install_job(sheets, [first, second])
        orchestrator = Orchestrator(sheets, None, CapturingInspector(), Settings())
        _sources, context, _rules, reference, resolved = orchestrator._inspection_inputs('register', second)
        self.assertEqual(resolved, prior)
        self.assertEqual(context['prior_open_item'], prior)
        self.assertEqual(context['prior_open_item_source'], 'Batch Queue Prior Open Item')
        self.assertEqual(context['existing_open_record']['records'][0]['Item ID'], prior)
        self.assertNotIn('OLD-ITEM', json.dumps(context))
        self.assertNotIn('groups', reference or {})
        self.assertEqual(sheets.rows('register', 'Batch Queue')[1]['Prior Open Item'], prior)

    def test_contradictory_production_rows_are_not_repaired(self):
        sheets = MemorySheets(continuation='OLD-ITEM')
        sheets.store[('register', 'Itemization')] = [{'Item ID': 'OLD-ITEM', 'Digital Assets': 'old.jpg', 'Digital Asset Count': '1'}]
        sheets.store[('register', 'Asset QC')] = [{'Digital Asset': 'old.jpg'}]
        install_job(sheets, [queue_row()])
        Orchestrator(sheets, None, CapturingInspector(error=RuntimeError('should not inspect')), Settings()).run('HIT-1')
        self.assertEqual(sheets.rows('register', 'Itemization')[0]['Item ID'], 'OLD-ITEM')
        self.assertEqual(sheets.rows('register', 'Asset QC')[0]['Digital Asset'], 'old.jpg')
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Blocked - Human Review')

    def test_clean_create_rejects_stale_continuation_without_writing_batches(self):
        sheets = MemorySheets(continuation='OLD-ITEM')

        class Drive:
            def enumerate(self, root):
                return [Source(id='1', name=name(1), folder_id='folder', folder_name='F01', path='/' + name(1), mime_type='image/jpeg')]

        with self.assertRaises(RegisterStateError):
            Orchestrator(sheets, Drive(), None, Settings()).create(JobCreate(
                project='Tri Delta Chapters', source_folder_id='folder', spreadsheet_id='register', box='Chapters Box 19',
            ))
        self.assertEqual(sheets.rows('register', 'Batch Queue'), [])

    def test_exact_manifest_is_required_and_ranges_are_not_expanded(self):
        names = [name(1), name(3)]
        row = queue_row(names=names)
        self.assertEqual(manifest_filenames(row), names)
        row['Start Filename'] = name(2)
        with self.assertRaisesRegex(Exception, 'Start Filename'):
            authorized_manifest(row)

    def test_manifest_mismatch_stops_for_human_review(self):
        sheets = MemorySheets()
        row = queue_row()
        row['Start Filename'] = 'not-the-manifest.jpg'
        install_job(sheets, [row])
        inspector = CapturingInspector(error=RuntimeError('should not inspect'))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        self.assertEqual(inspector.calls, [])
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Blocked - Human Review')

    def test_ambiguous_in_progress_batches_are_not_chosen(self):
        sheets = MemorySheets()
        first = queue_row(batch_id='HIT-1-B0001', **{'Batch Status': 'Inspection In Progress'})
        second = queue_row(batch_id='HIT-1-B0002', names=[name(2)], **{'Batch Status': 'Inspection In Progress'})
        install_job(sheets, [first, second])
        inspector = CapturingInspector(error=RuntimeError('should not inspect'))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        self.assertEqual(inspector.calls, [])
        statuses = {row['Batch ID']: row['Batch Status'] for row in sheets.rows('register', 'Batch Queue')}
        self.assertEqual(set(statuses.values()), {'Blocked - Human Review'})

    def test_each_dirty_first_batch_condition_blocks_without_openai(self):
        cases = {
            'prior': ({'Prior Open Item': 'TDC-B19-F01-001'}, {}, 'Prior Open Item is TDC-B19-F01-001'),
            'open item': ({}, {'Open Intellectual Item': 'OLD-ITEM'}, 'OLD-ITEM'),
            'unresolved': ({}, {'Unresolved Boundary Item': 'BOUNDARY'}, 'BOUNDARY'),
            'asset qc': ({}, {}, 'Asset QC has 1 data rows'),
            'itemization': ({}, {}, 'Itemization has 1 data rows'),
            'metadata': ({}, {}, 'Metadata References has 1 data rows'),
        }
        tables = {
            'asset qc': ('Asset QC', {'Digital Asset': 'already.jpg'}),
            'itemization': ('Itemization', {'Item ID': 'EXISTING', 'Digital Assets': 'already.jpg'}),
            'metadata': ('Metadata References', {'Reference ID': 'REF-EXISTING', 'Digital Assets': 'already.jpg'}),
        }
        for label, (queue_extra, continuation, fragment) in cases.items():
            with self.subTest(label=label):
                sheets = MemorySheets()
                sheets.continuation.update(continuation)
                if label in tables:
                    sheet, row = tables[label]
                    sheets.store[('register', sheet)] = [dict(row)]
                install_job(sheets, [queue_row(**queue_extra)])
                with tempfile.TemporaryDirectory() as directory:
                    inspector = CapturingInspector(error=RuntimeError('should not inspect'))
                    inspector.diagnostics = Diagnostics(directory)
                    before = {name: sheets.rows('register', name) for name in ('Asset QC', 'Itemization', 'Metadata References')}
                    Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
                    self.assertEqual(inspector.calls, [])
                    blocked = sheets.rows('register', 'Batch Queue')[0]
                    self.assertEqual(blocked['Batch Status'], 'Blocked - Human Review')
                    self.assertIn('Clean first-batch validation failed', blocked['Error / Retry Note'])
                    self.assertIn(fragment, blocked['Error / Retry Note'])
                    self.assertIn('Diagnostic:', blocked['Error / Retry Note'])
                    self.assertTrue(list(Path(directory).glob('*.json')))
                    for name, rows in before.items():
                        self.assertEqual(sheets.rows('register', name), rows)

    def test_blank_later_batch_uses_resulting_open_item_not_continuation(self):
        sheets = MemorySheets(continuation='STALE')
        first = queue_row(batch_id='HIT-1-B0001', names=[name(1)], **{
            'Batch Status': 'Committed - Validated', 'Resulting Open Item': '',
        })
        second = queue_row(batch_id='HIT-1-B0002', names=[name(2)], **{'Prior Open Item': ''})
        install_job(sheets, [first, second])
        orchestrator = Orchestrator(sheets, None, CapturingInspector(error=RuntimeError('stop')), Settings())
        _sources, context, _rules, _reference, prior = orchestrator._inspection_inputs('register', second)
        self.assertEqual(prior, 'None')
        self.assertEqual(context['prior_open_item_source'], 'previous Batch Queue Resulting Open Item')
        self.assertEqual(context['existing_open_record'], [])
        self.assertEqual(sheets.rows('register', 'Batch Queue')[1]['Prior Open Item'], 'None')
        self.assertEqual(sheets.field('register', 'Open Intellectual Item'), 'STALE')

    def test_blank_later_batch_blocks_when_previous_is_not_validated(self):
        sheets = MemorySheets(continuation='STALE')
        first = queue_row(batch_id='HIT-1-B0001', names=[name(1)], **{'Resulting Open Item': 'SHOULD-NOT-COPY'})
        second = queue_row(batch_id='HIT-1-B0002', names=[name(2)], **{'Prior Open Item': ''})
        install_job(sheets, [first, second])
        orchestrator = Orchestrator(sheets, None, CapturingInspector(), Settings())
        with self.assertRaisesRegex(Exception, 'not Committed - Validated'):
            orchestrator._inspection_inputs('register', second)
        self.assertEqual(sheets.rows('register', 'Batch Queue')[1]['Prior Open Item'], '')
        self.assertEqual(sheets.field('register', 'Open Intellectual Item'), 'STALE')

    def test_next_batch_prior_is_written_only_after_validated_commit(self):
        sheets = MemorySheets(continuation='STALE')
        first = queue_row(batch_id='HIT-1-B0001', names=[name(1)], **{
            'Batch Status': 'Authorized / Pending Inspection', 'Resulting Open Item': 'ITEM-1',
        })
        second = queue_row(batch_id='HIT-1-B0002', names=[name(2)], **{'Prior Open Item': ''})
        install_job(sheets, [first, second])
        orchestrator = Orchestrator(sheets, None, CapturingInspector(), Settings())
        orchestrator._authorize_following('register', 'HIT-1', 'HIT-1-B0001')
        self.assertEqual(sheets.rows('register', 'Batch Queue')[1]['Prior Open Item'], '')
        sheets.store[('register', 'Batch Queue')][0]['Batch Status'] = 'Committed - Validated'
        sheets.store[('register', 'Batch Queue')][0]['Resulting Open Item'] = ''
        orchestrator._authorize_following('register', 'HIT-1', 'HIT-1-B0001')
        self.assertEqual(sheets.rows('register', 'Batch Queue')[1]['Prior Open Item'], 'None')
        sheets.store[('register', 'Batch Queue')][1]['Prior Open Item'] = ''
        sheets.store[('register', 'Batch Queue')][0]['Resulting Open Item'] = 'ITEM-1'
        orchestrator._authorize_following('register', 'HIT-1', 'HIT-1-B0001')
        self.assertEqual(sheets.rows('register', 'Batch Queue')[1]['Prior Open Item'], 'ITEM-1')


class ReferenceTests(unittest.TestCase):
    def test_f01_reference_loads_and_does_not_apply_to_another_folder(self):
        root = Path(__file__).resolve().parents[1] / 'project_references'
        f01 = resolve_reference(root, 'Tri Delta Chapters', 'Chapters Box 19', 'F01')
        f02 = resolve_reference(root, 'Tri Delta Chapters', 'Chapters Box 19', 'F02')
        f03 = resolve_reference(root, 'Tri Delta Chapters', 'Chapters Box 19', 'F03')
        other = resolve_reference(root, 'Other Project', 'Chapters Box 19', 'F01')
        self.assertEqual(f01['reference_id'], 'tri-delta-chapters-box19-f01-calibration')
        self.assertEqual(f01['version'], '2026-10-07.1')
        self.assertEqual(f01['material_class'], 'client-approved-calibration-example')
        self.assertTrue(f01['not_an_executable_rule'])
        self.assertEqual(f02['reference_id'], f01['reference_id'])
        self.assertEqual(f02['version'], f01['version'])
        self.assertNotIn('examples', f03)
        self.assertNotIn('examples', other)
        self.assertNotIn('groups', f01)
        self.assertFalse((root / 'tri-delta-chapters-f01.json').exists())

    def test_missing_reference_file_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'index.json').write_text(json.dumps({'required': [{
                'project': 'Tri Delta Chapters', 'box': 'Chapters Box 19', 'folder': 'F01', 'file': 'missing.json',
            }]}), encoding='utf-8')
            with self.assertRaisesRegex(Exception, 'missing'):
                resolve_reference(directory, 'Tri Delta Chapters', 'Chapters Box 19', 'F01')

    def test_f01_grouping_and_metadata_references_match_the_approved_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            answer = {
                'project': 'Tri Delta Chapters', 'box': 'Chapters Box 19', 'folder': 'F01',
                'groups': [{
                    'id': 'F01-Report', 'type': 'Report', 'record_kind': 'Normal Odyssey',
                    'filenames': [name(i) for i in range(4, 14)],
                }],
            }
            Path(directory, 'answer.json').write_text(json.dumps(answer), encoding='utf-8')
            Path(directory, 'index.json').write_text(json.dumps({'required': [{
                'project': 'Tri Delta Chapters', 'box': 'Chapters Box 19', 'folder': 'F01', 'file': 'answer.json',
            }]}), encoding='utf-8')
            with self.assertRaisesRegex(Exception, 'benchmark answer material'):
                resolve_reference(directory, 'Tri Delta Chapters', 'Chapters Box 19', 'F01')
            claimed = {
                'reference_id': 'unlisted', 'version': '0',
                'material_class': 'client-approved-calibration-example', 'approval': 'client-approved',
                'not_an_executable_rule': True,
                'examples': [{'example_id': 'x', 'filenames': [name(4)], 'outcome': {'type': 'Report'}}],
            }
            Path(directory, 'claimed.json').write_text(json.dumps(claimed), encoding='utf-8')
            Path(directory, 'index.json').write_text(json.dumps({
                'generic': '',
                'required': [{
                    'project': 'Tri Delta Chapters', 'box': 'Chapters Box 19', 'folder': 'F01', 'file': 'claimed.json',
                }],
            }), encoding='utf-8')
            with self.assertRaisesRegex(Exception, 'not listed in approved_calibration'):
                resolve_reference(directory, 'Tri Delta Chapters', 'Chapters Box 19', 'F01')
        problems = reference_mismatches([], [], {'groups': [{'id': 'F01-Report', 'filenames': [name(4)]}]}, [name(4)])
        self.assertTrue(problems)
        self.assertIn('benchmark answer material', problems[0])

    def test_f01_metadata_only_reference_survives_staging(self):
        asset = AssetRow.model_validate(filled(ASSET_HEADERS, **{
            'Digital Asset': name(1), 'Assigned Record ID': 'cover',
        }))
        record = RecordRow.model_validate(filled(RECORD_HEADERS, **{
            'Proposed Record ID': 'cover', 'Record Kind': 'Metadata Only', 'Record Operation': 'Create New',
            'Template': 'Metadata Only', 'Metadata Readiness': 'Metadata Reference', 'Boundary State After Record': 'Closed',
            'Reference Role': 'Folder / Context',
        }))
        sheets = MemorySheets()
        install_job(sheets, [queue_row()])
        inspector = CapturingInspector(output=InspectionOutput(assets=[asset], records=[record]))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')
        self.assertEqual(sheets.rows('register', 'Metadata References')[0]['Reference ID'], 'TDC-B19-F01-REF-001')
        self.assertEqual(sheets.rows('register', 'Metadata References')[0]['Digital Assets'], name(1))
        self.assertEqual(sheets.rows('register', 'Itemization'), [])
        self.assertEqual(sheets.field('register', 'Open Intellectual Item'), 'None')

    def test_f01_groups_commit_in_approved_order_with_the_letter_left_open(self):
        definitions = [
            ('letter', 'Normal Odyssey', 'Correspondence', 'Letter', 'Review', 'Medium', 'Open', '', list(range(16, 22))),
            ('listing', 'Normal Odyssey', 'Document', 'List', 'Ready', 'High', 'Closed', '', [14, 15]),
            ('report', 'Normal Odyssey', 'Document', 'Report', 'Ready', 'High', 'Closed', '', list(range(4, 14))),
            ('ref2', 'Metadata Only', 'Metadata Only', '', 'Metadata Reference', '', 'Closed', 'Descriptive Reference', [2, 3]),
            ('ref1', 'Metadata Only', 'Metadata Only', '', 'Metadata Reference', '', 'Closed', 'Folder / Context', [1]),
        ]
        assets = []
        records = []
        for record_id, kind, template, type_name, readiness, confidence, boundary, role, numbers in definitions:
            records.append(RecordRow.model_validate(filled(RECORD_HEADERS, **{
                'Proposed Record ID': record_id, 'Record Kind': kind, 'Record Operation': 'Create New',
                'Template': template, 'Type': type_name, 'Metadata Readiness': readiness, 'Confidence': confidence,
                'Reference Role': role, 'Boundary State After Record': boundary, 'Production Task Count': '0',
                'Review Note': 'F01 does not establish the end of the letter.' if boundary == 'Open' else '',
            })))
            for number in numbers:
                assets.append(AssetRow.model_validate(filled(ASSET_HEADERS, **{
                    'Digital Asset': name(number), 'Assigned Record ID': record_id, 'Action Needed': 'No Adjustment',
                })))
        assets.sort(key=lambda row: row.digital_asset)
        sheets = MemorySheets()
        install_job(sheets, [queue_row(names=[name(i) for i in range(1, 22)])])
        inspector = CapturingInspector(output=InspectionOutput(assets=assets, records=records))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        sent = inspector.calls[0]['context']['approved_project_reference']
        self.assertEqual(sent['reference_id'], 'tri-delta-chapters-box19-f01-calibration')
        self.assertEqual(sent['version'], '2026-10-07.1')
        self.assertTrue(sent['not_an_executable_rule'])
        self.assertNotIn('groups', sent)
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')
        references = sheets.rows('register', 'Metadata References')
        self.assertEqual(references[0]['Digital Assets'], name(2) + '; ' + name(3))
        self.assertEqual(references[1]['Digital Assets'], name(1))
        items = sheets.rows('register', 'Itemization')
        self.assertEqual([row['Type'] for row in items], ['Letter', 'List', 'Report'])
        self.assertEqual(items[0]['Digital Assets'], '; '.join(name(i) for i in range(16, 22)))
        self.assertEqual(items[0]['Metadata Readiness'], 'Review')
        self.assertEqual(sheets.field('register', 'Open Intellectual Item'), items[0]['Item ID'])
        self.assertNotEqual(sheets.field('register', 'Open Intellectual Item'), 'None')
        self.assertEqual(len(sheets.rows('register', 'Asset QC')), 21)


class ImageAndBenchmarkTests(unittest.TestCase):
    def test_high_fidelity_preparation_preserves_small_text_dimensions(self):
        source = BytesIO()
        Image.new('RGB', (1200, 400), 'white').save(source, format='PNG')
        original = source.getvalue()
        kept, mime, mode = prepare_model_image(original, 'image/png')
        self.assertEqual((kept, mime, mode), (original, 'image/png', 'original'))
        wide = BytesIO()
        Image.new('RGB', (9000, 3000), 'white').save(wide, format='JPEG', quality=95)
        reduced, reduced_mime, reduced_mode = prepare_model_image(wide.getvalue(), 'image/jpeg')
        self.assertEqual(reduced_mode, 'resized-high-quality')
        self.assertEqual(reduced_mime, 'image/jpeg')
        with Image.open(BytesIO(reduced)) as image:
            self.assertEqual(image.size, (2048, 683))
            self.assertEqual(round(image.size[0] / image.size[1], 2), round(9000 / 3000, 2))

    def test_benchmark_reports_exact_internal_and_semantic_outcomes(self):
        item = {
            'Item ID': 'TDC-B19-F01-001', 'Digital Assets': name(4), 'Digital Asset Count': '1',
            'Template': 'Document', 'Type': 'Report', 'Metadata Readiness': 'Ready',
        }
        continuation = {'State Field': 'Open Intellectual Item', 'Current Value': 'None', 'Last Updated': '2026-10-01'}
        live = {'Itemization': [dict(item)], 'Continuation State': [dict(continuation)]}
        exact = compare_workbooks(live, {'Itemization': [dict(item)], 'Continuation State': [dict(continuation)]}, box='Chapters Box 19')
        self.assertEqual(exact['outcome'], 'exact_match')
        self.assertTrue(exact['matches'])
        dated = {'Itemization': [dict(item)], 'Continuation State': [{**continuation, 'Last Updated': '2026-10-06'}]}
        internal = compare_workbooks(live, dated)
        self.assertEqual(internal['outcome'], 'deterministic_internal_consistency_only')
        self.assertEqual(internal['sheets']['Continuation State']['value_mismatches'][0]['fields']['Last Updated']['live'], '2026-10-01')
        changed = {'Itemization': [{**item, 'Type': 'Letter'}], 'Continuation State': [dict(continuation)]}
        semantic = compare_workbooks(changed, live)
        self.assertEqual(semantic['outcome'], 'semantic_benchmark_mismatch')
        self.assertEqual(semantic['sheets']['Itemization']['value_mismatches'][0]['key'], 'TDC-B19-F01-001')
        self.assertIn('Type', semantic['sheets']['Itemization']['value_mismatches'][0]['fields'])
        before = json.dumps(live)
        compare_workbooks(live, changed)
        self.assertEqual(json.dumps(live), before)

    def test_f01_folder_checkpoint_does_not_require_the_whole_workbook(self):
        letter = {
            'Item ID': 'TDC-B19-F01-003', 'Source Group / Folder': 'F01',
            'Digital Assets': name(16), 'Digital Asset Count': '1',
            'Template': 'Correspondence', 'Type': 'Letter', 'Metadata Readiness': 'Review',
        }
        later = {
            'Item ID': 'TDC-B19-F02-001', 'Source Group / Folder': 'F02',
            'Digital Assets': 'TriDelta_Year2_Chapters_Box19_F02_001.jpg', 'Digital Asset Count': '1',
            'Template': 'Correspondence', 'Type': 'Letter', 'Metadata Readiness': 'Ready',
        }
        closed = {**letter, 'Metadata Readiness': 'Ready'}
        live = {'Itemization': [dict(letter)]}
        frozen = {'Itemization': [dict(closed), dict(later)], 'Continuation State': [
            {'State Field': 'Open Intellectual Item', 'Current Value': 'None'},
        ]}
        whole = compare_workbooks(live, frozen)
        self.assertTrue(whole['whole_workbook'])
        self.assertEqual(whole['outcome'], 'semantic_benchmark_mismatch')
        checkpoint = compare_workbooks(
            {'Itemization': [dict(letter)]},
            {'Itemization': [dict(letter), dict(later)], 'Continuation State': [
                {'State Field': 'Open Intellectual Item', 'Current Value': 'None'},
            ]},
            folder='F01',
        )
        self.assertFalse(checkpoint['whole_workbook'])
        self.assertEqual(checkpoint['folder'], 'F01')
        self.assertEqual(checkpoint['outcome'], 'exact_match')
        self.assertFalse(checkpoint['final_box19_match'])
        self.assertIn('F02', checkpoint['f01_letter_transition'])
        self.assertIn('Box 19', checkpoint['f01_letter_transition'])
        self.assertEqual(checkpoint['sheets']['Itemization']['missing'], [])
        self.assertNotIn('TDC-B19-F02-001', json.dumps(checkpoint['sheets']['Itemization']))
        disagreed = compare_workbooks(live, {'Itemization': [dict(closed)]}, folder='F01')
        self.assertEqual(disagreed['outcome'], 'semantic_benchmark_mismatch')
        self.assertIn('Metadata Readiness', disagreed['sheets']['Itemization']['value_mismatches'][0]['fields'])
        before = json.dumps(frozen)
        compare_workbooks(live, frozen, folder='F01')
        self.assertEqual(json.dumps(frozen), before)


def closed_staging(filename='page.jpg'):
    asset = {
        'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-1', 'Batch ID': 'HIT-1-B0001',
        'Processing Unit / Source Folder': 'F01', 'Digital Asset': filename, 'Box': 'Chapters Box 19',
        'Source Group / Folder': 'F01', 'Assigned Record ID': 'TDC-B19-F01-001', 'Asset Role': 'Page',
        'Required Crop Standard': 'N/A', 'QC Status': 'Pass', 'Action Needed': 'No Adjustment',
        'Expected Output Asset Count': '1', 'QC Confidence': 'High', 'Production Queue Eligible': 'No',
        'Inspection Complete': 'Yes', 'Commit Status': 'Pending Commit',
    }
    record = {
        'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-1', 'Batch ID': 'HIT-1-B0001',
        'Processing Unit / Source Folder': 'F01', 'Proposed Record ID': 'TDC-B19-F01-001',
        'Record Kind': 'Normal Odyssey', 'Record Operation': 'Create New', 'Box': 'Chapters Box 19',
        'Source Group / Folder': 'F01', 'New Batch Assets': filename, 'Existing Committed Assets': '',
        'Full Record Assets After Commit': filename, 'Digital Asset Count': '1', 'Logical Component Count': '1',
        'Template': 'Document', 'Type': 'Report', 'Metadata Readiness': 'Review', 'Confidence': 'High',
        'Production Task Count': '0', 'Boundary State After Record': 'Closed',
        'Inspection Complete': 'Yes', 'Commit Status': 'Pending Commit',
    }
    return asset, record


class RecoveryTests(unittest.TestCase):
    def test_already_committed_batch_is_a_noop(self):
        sheets = MemorySheets()
        asset, record = closed_staging()
        row = queue_row(names=['page.jpg'], **{'Batch Status': 'Inspection Complete - Awaiting Commit', 'Source Group / Folder': 'F01'})
        row['Notes'] = json.dumps({'manifest': manifest(['page.jpg'])})
        plan = plan_commit(row, [asset], [record], {'Asset QC': [], 'Itemization': [], 'Metadata References': [], 'Ready for Metadata': [], 'Production Queue': []}, date.today().strftime('%m/%d/%y'))
        for sheet, rows in plan.append_rows.items():
            for item in rows:
                sheets.write('register', sheet, item)
        row['Batch Status'] = 'Committed - Validated'
        row['Resulting Open Item'] = plan.resulting_open_item
        install_job(sheets, [row])
        sheets.store[('register', 'Asset Staging')] = [asset]
        sheets.store[('register', 'Record Staging')] = [record]
        sheets.set_field('register', 'Open Intellectual Item', plan.resulting_open_item)
        before = len(sheets.rows('register', 'Asset QC'))
        inspector = CapturingInspector(error=AssertionError('committed batch must not be inspected'))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        self.assertEqual(inspector.calls, [])
        self.assertEqual(len(sheets.rows('register', 'Asset QC')), before)
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')

    def test_start_leaves_an_earlier_commit_validated_after_continuation_moves(self):
        sheets = MemorySheets()
        first = queue_row(names=['page.jpg'], **{
            'Batch Status': 'Committed - Validated',
            'Resulting Open Item': 'TDC-B19-F01-003',
            'Validation Status': 'PASS',
            'Source Group / Folder': 'F01',
        })
        second = queue_row(batch_id='HIT-1-B0002', names=['next.jpg'], **{
            'Source Group / Folder': 'F02',
            'Prior Open Item': 'None',
            'Notes': json.dumps({'manifest': manifest(['next.jpg'], 'F02')}),
        })
        install_job(sheets, [first, second])
        sheets.set_field('register', 'Open Intellectual Item', 'None')
        inspector = CapturingInspector(error=RuntimeError('stop after the earlier batch is left alone'))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        rows = sheets.rows('register', 'Batch Queue')
        self.assertEqual(rows[0]['Batch Status'], 'Committed - Validated')
        self.assertEqual(rows[0]['Validation Status'], 'PASS')
        self.assertEqual(inspector.calls[0]['batch_id'], 'HIT-1-B0002')
        self.assertEqual(rows[1]['Batch Status'], 'Inspection Failed - Retryable')

    def test_crash_after_staging_commits_without_another_inspection(self):
        sheets = MemorySheets()
        asset, record = closed_staging()
        row = queue_row(names=['page.jpg'], **{'Batch Status': 'Inspection In Progress'})
        row['Notes'] = json.dumps({'manifest': manifest(['page.jpg'])})
        install_job(sheets, [row])
        sheets.store[('register', 'Asset Staging')] = [asset]
        sheets.store[('register', 'Record Staging')] = [record]
        inspector = CapturingInspector(error=AssertionError('staging is already complete'))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        self.assertEqual(inspector.calls, [])
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')
        self.assertEqual([item['Digital Asset'] for item in sheets.rows('register', 'Asset QC')], ['page.jpg'])

    def test_crash_after_commit_before_validation_does_not_duplicate_rows(self):
        sheets = MemorySheets()
        asset, record = closed_staging()
        row = queue_row(names=['page.jpg'], **{'Batch Status': 'Inspection Complete - Awaiting Commit'})
        row['Notes'] = json.dumps({'manifest': manifest(['page.jpg'])})
        plan = plan_commit(row, [asset], [record], {'Asset QC': [], 'Itemization': [], 'Metadata References': [], 'Ready for Metadata': [], 'Production Queue': []}, date.today().strftime('%m/%d/%y'))
        for sheet, rows in plan.append_rows.items():
            for item in rows:
                sheets.write('register', sheet, item)
        row['Batch Status'] = 'Commit Written - Pending Validation'
        install_job(sheets, [row])
        sheets.store[('register', 'Asset Staging')] = [asset]
        sheets.store[('register', 'Record Staging')] = [record]
        inspector = CapturingInspector(error=AssertionError('validation recovery must not inspect'))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        self.assertEqual(inspector.calls, [])
        self.assertEqual([item['Digital Asset'] for item in sheets.rows('register', 'Asset QC')], ['page.jpg'])
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Validation Status'], 'PASS')

    def test_retry_records_the_diagnostic_file(self):
        from app.inspection import Inspector

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'rules.md').write_text('Inspect every authorized image.', encoding='utf-8')

            class LocalSettings:
                governing_docs_dir = directory
                diagnostics_dir = directory
                openai_model = 'gpt-test'
                openai_max_output_tokens = 1000
                openai_reasoning_effort = 'low'
                openai_input_usd_per_million = 1
                openai_cached_input_usd_per_million = 0
                openai_output_usd_per_million = 1

            class Drive:
                def image(self, _source):
                    out = BytesIO()
                    Image.new('RGB', (8, 8), 'white').save(out, format='JPEG')
                    return out.getvalue()

            class Client:
                def __init__(self):
                    self.responses = self

                def parse(self, **_request):
                    class Usage:
                        input_tokens = 10
                        output_tokens = 2
                        input_tokens_details = None

                    class Response:
                        id = 'resp_retry'
                        status = 'completed'
                        incomplete_details = None
                        usage = Usage()
                        output_parsed = None

                    return Response()

            sheets = MemorySheets()
            install_job(sheets, [queue_row(names=['Example_F02_001.jpg'], **{
                'Project': 'Example Project', 'Box': 'Box 1', 'Source Group / Folder': 'F02',
            })])
            sheets.store[('register', 'Batch Queue')][0]['Notes'] = json.dumps({'manifest': manifest(['Example_F02_001.jpg'], 'F02')})
            orchestrator = Orchestrator(sheets, None, Inspector(LocalSettings(), Drive(), client=Client()), Settings())
            orchestrator.run('HIT-1')
            row = sheets.rows('register', 'Batch Queue')[0]
            self.assertEqual(row['Batch Status'], 'Inspection Failed - Retryable')
            self.assertIn('Diagnostic:', row['Error / Retry Note'])
            path = Path(row['Error / Retry Note'].split('[Diagnostic: ', 1)[1].rstrip(']'))
            self.assertTrue(path.is_file())
            events = json.loads(path.read_text(encoding='utf-8'))
            self.assertIn('inspection_error', [event['event'] for event in events])
            self.assertEqual(events[0]['prior_open_item_source'], 'Batch Queue Prior Open Item')
            self.assertIn('image_preparation', events[0])


LETTER_NOTE = 'F01 shows three typed letter pages and three blank versos and does not establish the end.'


def f02_name():
    return 'TriDelta_Year2_Chapters_Box19_F02_001.jpg'


def calibration_records(boundary='Open', readiness='Review', confidence='Medium', template='Correspondence', note=LETTER_NOTE):
    definitions = [
        ('ref1', 'Metadata Only', 'Metadata Only', '', 'Metadata Reference', '', 'Closed', 'Folder / Context', [1]),
        ('ref2', 'Metadata Only', 'Metadata Only', '', 'Metadata Reference', '', 'Closed', 'Descriptive Reference', [2, 3]),
        ('report', 'Normal Odyssey', 'Document', 'Report', 'Ready', 'High', 'Closed', '', list(range(4, 14))),
        ('listing', 'Normal Odyssey', 'Document', 'List', 'Ready', 'High', 'Closed', '', [14, 15]),
        ('letter', 'Normal Odyssey', template, 'Letter', readiness, confidence, boundary, '', list(range(16, 22))),
    ]
    assets = []
    records = []
    for record_id, kind, record_template, type_name, record_readiness, record_confidence, record_boundary, role, numbers in definitions:
        review = note if record_boundary == 'Open' or record_readiness == 'Review' else ''
        records.append(RecordRow.model_validate(filled(RECORD_HEADERS, **{
            'Proposed Record ID': record_id, 'Record Kind': kind, 'Record Operation': 'Create New',
            'Template': record_template, 'Type': type_name, 'Metadata Readiness': record_readiness,
            'Confidence': record_confidence, 'Reference Role': role, 'Boundary State After Record': record_boundary,
            'Production Task Count': '0', 'Review Note': review,
        })))
        for number in numbers:
            assets.append(AssetRow.model_validate(filled(ASSET_HEADERS, **{
                'Digital Asset': name(number), 'Assigned Record ID': record_id, 'Action Needed': 'No Adjustment',
            })))
    assets.sort(key=lambda row: row.digital_asset)
    return InspectionOutput(assets=assets, records=records)


class ScriptedF02(CapturingInspector):
    def __init__(self, boundary, readiness, confidence, note):
        super().__init__()
        self.boundary = boundary
        self.readiness = readiness
        self.confidence = confidence
        self.note = note

    def inspect(self, job, batch_id, sources, context, rules):
        self.calls.append({'batch_id': batch_id, 'context': context, 'sources': [source.name for source in sources]})
        if batch_id.endswith('B0001'):
            return calibration_records(), {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1, 'elapsed_seconds': 0.1, 'response_id': 'resp'}
        prior = context['prior_open_item']
        asset = AssetRow.model_validate(filled(ASSET_HEADERS, **{
            'Digital Asset': f02_name(), 'Assigned Record ID': prior, 'Action Needed': 'No Adjustment',
        }))
        record = RecordRow.model_validate(filled(RECORD_HEADERS, **{
            'Proposed Record ID': prior, 'Record Kind': 'Normal Odyssey', 'Record Operation': 'Update Existing Open Record',
            'Template': 'Correspondence', 'Type': 'Letter', 'Metadata Readiness': self.readiness,
            'Confidence': self.confidence, 'Boundary State After Record': self.boundary,
            'Production Task Count': '0', 'Review Note': self.note,
        }))
        return InspectionOutput(assets=[asset], records=[record]), {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1, 'elapsed_seconds': 0.1, 'response_id': 'resp-2'}


def install_f01_f02(sheets):
    first = queue_row(names=[name(i) for i in range(1, 22)])
    second = queue_row(batch_id='HIT-1-B0002', names=[f02_name()], **{
        'Source Group / Folder': 'F02', 'Prior Open Item': '',
    })
    second['Notes'] = json.dumps({'manifest': manifest([f02_name()], 'F02')})
    install_job(sheets, [first, second])


class CalibrationTests(unittest.TestCase):
    def test_calibration_contradiction_stops_before_commit_without_rewriting(self):
        sheets = MemorySheets()
        install_job(sheets, [queue_row(names=[name(i) for i in range(1, 22)])])
        wrong = calibration_records(boundary='Closed', readiness='Ready', confidence='High', template='Document', note='')
        Orchestrator(sheets, None, CapturingInspector(output=wrong), Settings()).run('HIT-1')
        row = sheets.rows('register', 'Batch Queue')[0]
        self.assertEqual(row['Batch Status'], 'Blocked - Human Review')
        self.assertIn('Calibration conflict', row['Error / Retry Note'])
        self.assertIn('Staging was not changed', row['Error / Retry Note'])
        self.assertEqual(sheets.rows('register', 'Asset QC'), [])
        self.assertEqual(sheets.rows('register', 'Itemization'), [])

    def test_review_open_without_evidence_stops_before_commit(self):
        sheets = MemorySheets()
        install_job(sheets, [queue_row(names=[name(i) for i in range(1, 22)])])
        missing = calibration_records(note='')
        Orchestrator(sheets, None, CapturingInspector(output=missing), Settings()).run('HIT-1')
        row = sheets.rows('register', 'Batch Queue')[0]
        self.assertEqual(row['Batch Status'], 'Blocked - Human Review')
        self.assertIn('Review Note', row['Error / Retry Note'])
        self.assertEqual(sheets.rows('register', 'Asset QC'), [])

    def test_f01_folder_completes_while_the_letter_stays_open(self):
        sheets = MemorySheets()
        install_job(sheets, [queue_row(names=[name(i) for i in range(1, 22)])])
        Orchestrator(sheets, None, CapturingInspector(output=calibration_records()), Settings()).run('HIT-1')
        letter = sheets.field('register', 'Open Intellectual Item')
        self.assertNotEqual(letter, 'None')
        summary = next(row for row in sheets.rows('register', 'Folder Processing Unit Summary') if row.get('Source Group / Folder') == 'F01')
        self.assertEqual(summary['Processing Status'], 'Completed - Validated')
        self.assertEqual(summary['Open Item'], letter)
        self.assertEqual(len(sheets.rows('register', 'Asset QC')), 21)

    def test_f02_continues_the_open_letter_without_duplicating_it(self):
        sheets = MemorySheets()
        install_f01_f02(sheets)
        inspector = ScriptedF02('Open', 'Review', 'Medium', 'The first F02 image continues the same letter.')
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        self.assertEqual(inspector.calls[1]['sources'], [f02_name()])
        prior = inspector.calls[1]['context']['prior_open_item']
        self.assertNotEqual(prior, 'None')
        self.assertEqual(sheets.rows('register', 'Batch Queue')[1]['Prior Open Item'], prior)
        self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Resulting Open Item'], prior)
        existing = inspector.calls[1]['context']['existing_open_record']
        self.assertEqual(existing['records'][0]['Item ID'], prior)
        committed = {row['Digital Asset'] for row in existing['committed_assets']}
        self.assertTrue(all(name(number) in committed for number in range(16, 22)))
        self.assertNotIn(f02_name(), committed)
        letters = [row for row in sheets.rows('register', 'Itemization') if row['Type'] == 'Letter']
        self.assertEqual(len(letters), 1)
        self.assertEqual(letters[0]['Item ID'], prior)
        self.assertIn(name(16), letters[0]['Digital Assets'])
        self.assertIn(f02_name(), letters[0]['Digital Assets'])
        self.assertEqual(sheets.field('register', 'Open Intellectual Item'), prior)
        qc = [row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')]
        self.assertEqual(len(qc), len(set(qc)))
        self.assertEqual(len(qc), 22)
        summary = next(row for row in sheets.rows('register', 'Folder Processing Unit Summary') if row.get('Source Group / Folder') == 'F01')
        self.assertEqual(summary['Processing Status'], 'Completed - Validated')
        self.assertEqual(summary['Open Item'], prior)

    def test_f02_may_close_the_letter_from_its_own_images(self):
        sheets = MemorySheets()
        install_f01_f02(sheets)
        inspector = ScriptedF02('Closed', 'Ready', 'High', 'The first F02 image establishes that the letter has ended.')
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        prior = inspector.calls[1]['context']['prior_open_item']
        self.assertEqual(sheets.rows('register', 'Batch Queue')[1]['Prior Open Item'], prior)
        self.assertEqual(sheets.rows('register', 'Batch Queue')[1]['Resulting Open Item'], 'None')
        letters = [row for row in sheets.rows('register', 'Itemization') if row['Type'] == 'Letter']
        self.assertEqual([row['Item ID'] for row in letters], [prior])
        self.assertIn(name(21), letters[0]['Digital Assets'])
        self.assertIn(f02_name(), letters[0]['Digital Assets'])
        self.assertEqual(sheets.field('register', 'Open Intellectual Item'), 'None')
        self.assertEqual(len({row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')}), 22)
        summary = next(row for row in sheets.rows('register', 'Folder Processing Unit Summary') if row.get('Source Group / Folder') == 'F01')
        self.assertEqual(summary['Processing Status'], 'Completed - Validated')

    def test_calibration_id_and_version_are_logged_and_sent(self):
        from app.inspection import Inspector
        reference = resolve_reference(Path(__file__).resolve().parents[1] / 'project_references', 'Tri Delta Chapters', 'Chapters Box 19', 'F01')
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'rules.md').write_text('Inspect every authorized image.', encoding='utf-8')

            class LocalSettings:
                governing_docs_dir = directory
                diagnostics_dir = directory
                openai_model = 'gpt-test'
                openai_max_output_tokens = 1000
                openai_reasoning_effort = ''
                openai_input_usd_per_million = 1
                openai_cached_input_usd_per_million = 0
                openai_output_usd_per_million = 1

            class Drive:
                def image(self, _source):
                    out = BytesIO()
                    Image.new('RGB', (8, 8), 'white').save(out, format='JPEG')
                    return out.getvalue()

            captured = {}

            class Client:
                def __init__(self):
                    self.responses = self

                def parse(self, **request):
                    captured.update(request)
                    raise RuntimeError('captured')

            with self.assertRaisesRegex(RuntimeError, 'captured'):
                Inspector(LocalSettings(), Drive(), client=Client()).inspect(
                    'HIT-1', 'HIT-1-B0001',
                    [Source(id='file-1', name=name(1), folder_id='folder', folder_name='F01', path='/' + name(1), mime_type='image/jpeg')],
                    {'folder_code': 'F01', 'prior_open_item': 'None', 'approved_project_reference': reference, 'inspection_attempt': 1},
                    [],
                )
            instruction = captured['input'][0]['content']
            payload = json.loads(captured['input'][1]['content'][0]['text'])
            self.assertNotIn(name(4), instruction)
            self.assertIn('calibration', instruction)
            self.assertEqual(payload['approved_project_reference']['reference_id'], 'tri-delta-chapters-box19-f01-calibration')
            self.assertEqual(payload['approved_project_reference']['version'], '2026-10-07.1')
            self.assertEqual(captured['input'][1]['content'][2]['detail'], 'high')
            event = json.loads(next(Path(directory).glob('*.json')).read_text(encoding='utf-8'))[0]
            self.assertEqual(event['reference_id'], 'tri-delta-chapters-box19-f01-calibration')
            self.assertEqual(event['reference_version'], '2026-10-07.1')


class DirectPriorTests(unittest.TestCase):
    def test_assert_prior_helper_uses_the_batch_value(self):
        from app.authorization import assert_batch_prior
        prior, source = assert_batch_prior('TDC-B19-F01-003', 'OLD-ITEM', 'OLD-ITEM', [], [], [], first_batch=False)
        self.assertEqual((prior, source), ('TDC-B19-F01-003', 'Batch Queue Prior Open Item'))


def f01_item(item_id, type_name, readiness, asset, template='Document'):
    return {
        'Item ID': item_id, 'Source Group / Folder': 'F01', 'Digital Assets': asset,
        'Digital Asset Count': '1', 'Logical Component Count': '1', 'Template': template,
        'Type': type_name, 'Metadata Readiness': readiness, 'Production Task Count': '0',
    }


def _joined(numbers):
    return '; '.join(name(number) for number in numbers)


def f01_counts_only_register():
    """Right headline counts and a wrong filename assignment. Counts alone must not pass."""
    report = f01_item('TDC-B19-F01-001', 'Report', 'Ready', name(4))
    listing = f01_item('TDC-B19-F01-002', 'List', 'Ready', name(14))
    letter = f01_item('TDC-B19-F01-003', 'Letter', 'Review', name(16), template='Correspondence')
    return {
        'Asset QC': [{'Digital Asset': name(number), 'Source Group / Folder': 'F01'} for number in range(1, 22)],
        'Metadata References': [
            {
                'Reference ID': 'TDC-B19-F01-REF-001', 'Source Group / Folder': 'F01',
                'Digital Assets': name(1), 'Digital Asset Count': '1', 'Reference Role': 'Folder / Context',
                'Template': 'Metadata Only', 'Metadata Readiness': 'Metadata Reference',
            },
            {
                'Reference ID': 'TDC-B19-F01-REF-002', 'Source Group / Folder': 'F01',
                'Digital Assets': name(2), 'Digital Asset Count': '1', 'Reference Role': 'Descriptive Reference',
                'Template': 'Metadata Only', 'Metadata Readiness': 'Metadata Reference',
            },
        ],
        'Itemization': [report, listing, letter],
        'Ready for Metadata': [dict(report), dict(listing)],
        'Production Queue': [],
        'Continuation State': [{
            'State Field': 'Open Intellectual Item',
            'Current Value': 'TDC-B19-F01-003',
        }],
    }


def f01_intermediate_register(assets=21, extra_record=False, letter_closed=False):
    from app.register_state import authorization_scope, completed_processing_unit, next_unprocessed_source, resume_instruction

    groups = [
        ('TDC-B19-F01-REF-001', 'Metadata Only', 'Metadata Only', '', 'Folder / Context', 'Metadata Reference', '', 'Closed', [1]),
        ('TDC-B19-F01-REF-002', 'Metadata Only', 'Metadata Only', '', 'Descriptive Reference', 'Metadata Reference', '', 'Closed', [2, 3]),
        ('TDC-B19-F01-001', 'Normal Odyssey', 'Document', 'Report', '', 'Ready', 'High', 'Closed', list(range(4, 14))),
        ('TDC-B19-F01-002', 'Normal Odyssey', 'Document', 'List', '', 'Ready', 'High', 'Closed', [14, 15]),
        ('TDC-B19-F01-003', 'Normal Odyssey', 'Correspondence', 'Letter', '', 'Review', 'Medium', 'Open', list(range(16, 22))),
    ]
    if letter_closed:
        kind, template, type_name, role, readiness, confidence, boundary, numbers = groups[-1][1:]
        groups[-1] = (groups[-1][0], kind, template, type_name, role, 'Ready', 'High', 'Closed', numbers)
    open_id = 'None' if letter_closed else 'TDC-B19-F01-003'
    asset_qc = []
    items = []
    references = []
    ready = []
    staging_assets = []
    staging_records = []
    for record_id, kind, template, type_name, role, readiness, confidence, boundary, numbers in groups:
        filenames = [name(number) for number in numbers]
        for filename in filenames:
            asset_qc.append({
                'Digital Asset': filename, 'Source Group / Folder': 'F01', 'Assigned Record ID': record_id,
                'Template': template, 'Type': type_name, 'QC Confidence': 'High',
            })
        listing = _joined(numbers)
        if kind == 'Metadata Only':
            references.append({
                'Reference ID': record_id, 'Source Group / Folder': 'F01', 'Digital Assets': listing,
                'Digital Asset Count': str(len(numbers)), 'Template': 'Metadata Only', 'Reference Role': role,
                'Metadata Readiness': readiness,
            })
        else:
            item = {
                'Item ID': record_id, 'Source Group / Folder': 'F01', 'Digital Assets': listing,
                'Digital Asset Count': str(len(numbers)), 'Logical Component Count': str(len(numbers)),
                'Template': template, 'Type': type_name, 'Metadata Readiness': readiness,
                'Confidence': confidence, 'Production Task Count': '0',
            }
            items.append(item)
            if readiness == 'Ready':
                ready.append(dict(item))
        staging_records.append({
            'Batch ID': 'HIT-1-B0001', 'Proposed Record ID': record_id, 'Record Kind': kind,
            'Template': template, 'Type': type_name, 'Reference Role': role,
            'Metadata Readiness': readiness, 'Confidence': confidence,
            'Boundary State After Record': 'Open - continuation unresolved' if boundary == 'Open' else 'Closed',
            '_row': 4 + len(staging_records) + 1,
        })
    if extra_record:
        items.append(f01_item('TDC-B19-F01-004', 'Report', 'Ready', name(13)))
    asset_qc = asset_qc[:assets]
    for index, row in enumerate(asset_qc, 5):
        staging_assets.append({
            'Batch ID': 'HIT-1-B0001', 'Digital Asset': row['Digital Asset'], '_row': index,
        })
    queue = {
        'Project': 'Tri Delta Chapters', 'Job ID': 'HIT-1', 'Batch ID': 'HIT-1-B0001',
        'Box': 'Chapters Box 19', 'Source Group / Folder': 'F01',
        'Batch Status': 'Committed - Validated', 'Prior Open Item': 'None',
        'Resulting Open Item': open_id, 'Planned Asset Count': '21',
        'Start Filename': name(1), 'End Filename': name(21),
        'Asset Staging Row Range': 'Asset Staging!A5:X25',
        'Record Staging Row Range': 'Record Staging!A5:AH9',
    }
    folder = {
        'Processing Unit / Source Folder': 'F01', 'Source Group / Folder': 'F01',
        'Box': 'Chapters Box 19', 'Processing Status': 'Completed - Validated',
        'Open Item': open_id, 'Enumerated New Source Count': '21', 'Processed New Source Count': '21',
    }
    done = ['F01']
    resume = resume_instruction(done, '' if open_id == 'None' else open_id)
    continuation = [
        {'State Field': 'Open Intellectual Item', 'Current Value': open_id},
        {'State Field': 'Unresolved Boundary Item', 'Current Value': open_id},
        {'State Field': 'Current Completed Processing Unit', 'Current Value': completed_processing_unit([queue], done)},
        {'State Field': 'Next Authorized / Unprocessed Folder', 'Current Value': next_unprocessed_source([queue], done)},
        {'State Field': 'Source Processing Authorization', 'Current Value': authorization_scope([queue])},
        {'State Field': 'Resume Instruction', 'Current Value': resume},
        {'State Field': 'Last Closed Item', 'Current Value': 'TDC-B19-F01-002'},
    ]
    return {
        'Asset QC': asset_qc,
        'Metadata References': references,
        'Itemization': items,
        'Ready for Metadata': ready,
        'Production Queue': [],
        'Continuation State': continuation,
        'Batch Queue': [queue],
        'Asset Staging': staging_assets,
        'Record Staging': staging_records,
        'Folder Processing Unit Summary': [folder],
        'Box Summary': [{
            'Box': 'Chapters Box 19', 'Completed - Validated Folders': '1', 'Open Item': open_id,
        }],
    }


def f01_final_letter_register():
    """Frozen Benchmark shape for the F01 letter after F02 closed it, plus one F02 record."""
    closed = f01_intermediate_register(letter_closed=True)
    closed['Itemization'].append({
        'Item ID': 'TDC-B19-F02-001', 'Source Group / Folder': 'F02',
        'Digital Assets': f02_name(), 'Digital Asset Count': '1', 'Logical Component Count': '1',
        'Template': 'Correspondence', 'Type': 'Letter', 'Metadata Readiness': 'Ready',
        'Production Task Count': '0',
    })
    closed['Asset QC'].append({'Digital Asset': f02_name(), 'Source Group / Folder': 'F02'})
    closed['Ready for Metadata'].append(dict(closed['Itemization'][-1]))
    return closed


class F01CheckpointTests(unittest.TestCase):
    def test_correct_f01_intermediate_checkpoint(self):
        from app.benchmark import f01_calibration_checkpoint

        live = f01_intermediate_register()
        before = json.dumps(live)
        result = f01_calibration_checkpoint(live)
        self.assertEqual(json.dumps(live), before)
        self.assertEqual(result['outcome'], 'f01_intermediate_match')
        self.assertTrue(result['matches'])
        self.assertEqual(result['problems'], [])
        self.assertEqual(result['actual'], result['expected'])
        self.assertEqual(result['open_intellectual_item'], 'TDC-B19-F01-003')
        self.assertFalse(result['final_box19_match'])
        self.assertTrue(all(result['verified'].values()))
        self.assertIn('Record Staging', result['distinctions']['asset_qc_confidence'])
        self.assertEqual(result['distinctions']['letter_asset_qc_confidence_values'], ['High'])
        self.assertNotEqual(result['outcome'], 'exact_match')

    def test_incorrect_f01_grouping_fails_the_intermediate_checkpoint(self):
        from app.benchmark import f01_calibration_checkpoint

        grouped = f01_calibration_checkpoint(f01_intermediate_register(extra_record=True))
        short = f01_calibration_checkpoint(f01_intermediate_register(assets=20))
        self.assertEqual(grouped['outcome'], 'f01_intermediate_mismatch')
        self.assertEqual(short['outcome'], 'f01_intermediate_mismatch')
        self.assertFalse(grouped['matches'])
        self.assertTrue(any('normal_records is 4' in problem for problem in grouped['problems']))
        self.assertTrue(any('assets is 20' in problem for problem in short['problems']))
        counts_only = f01_calibration_checkpoint(f01_counts_only_register())
        self.assertEqual(counts_only['actual'], counts_only['expected'])
        self.assertEqual(counts_only['outcome'], 'f01_intermediate_mismatch')
        self.assertFalse(counts_only['matches'])
        self.assertTrue(any('not one record' in problem or 'Template' in problem or 'Batch Queue' in problem for problem in counts_only['problems']))

    def test_premature_letter_closure_fails_the_intermediate_checkpoint(self):
        from app.benchmark import f01_calibration_checkpoint

        result = f01_calibration_checkpoint(f01_intermediate_register(letter_closed=True))
        self.assertEqual(result['outcome'], 'f01_intermediate_mismatch')
        self.assertEqual(result['actual']['review_open_letters'], 0)
        self.assertEqual(result['actual']['ready_records'], 3)
        self.assertEqual(result['open_intellectual_item'], 'None')
        self.assertFalse(result['final_box19_match'])

    def test_folder_scope_against_the_final_letter_is_not_an_exact_workbook_match(self):
        intermediate = f01_intermediate_register()
        final = f01_final_letter_register()
        scoped = compare_workbooks(intermediate, final, folder='F01')
        self.assertEqual(scoped['outcome'], 'semantic_benchmark_mismatch')
        self.assertFalse(scoped['matches'])
        self.assertFalse(scoped['whole_workbook'])
        self.assertEqual(scoped['counts']['differences']['review_records'], {'live': 1, 'benchmark': 0})
        self.assertIn('Metadata Readiness', scoped['sheets']['Itemization']['value_mismatches'][0]['fields'])
        whole = compare_workbooks(intermediate, final)
        self.assertTrue(whole['whole_workbook'])
        self.assertEqual(whole['outcome'], 'semantic_benchmark_mismatch')
        finished = compare_workbooks(final, json.loads(json.dumps(final)))
        self.assertTrue(finished['whole_workbook'])
        self.assertEqual(finished['outcome'], 'exact_match')
        self.assertTrue(finished['matches'])

    def test_f01_checkpoint_route_does_not_open_the_frozen_benchmark(self):
        import app.main as main
        from fastapi.testclient import TestClient

        class TrackingSheets(MemorySheets):
            def __init__(self):
                super().__init__()
                self.opened = []

            def rows(self, sid, sheet):
                self.opened.append(sid)
                return super().rows(sid, sheet)

        sheets = TrackingSheets()
        for sheet, rows in f01_intermediate_register().items():
            sheets.store[('register', sheet)] = rows
        orchestrator = Orchestrator(sheets, None, None, Settings())
        original_live, original_guard = main.live, main.guard
        try:
            main.live = lambda: orchestrator
            main.guard = lambda *args, **kwargs: None
            with TestClient(main.app) as client:
                response = client.post('/api/benchmark/f01-checkpoint', json={'spreadsheet_id': 'register'})
        finally:
            main.live, main.guard = original_live, original_guard
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['outcome'], 'f01_intermediate_match')
        self.assertEqual(response.json()['final_box19_match'], False)
        self.assertEqual(set(sheets.opened), {'register'})


class RecordingDrive:
    def __init__(self):
        self.calls = []

    def enumerate(self, root):
        self.calls.append(root)
        if root == 'f01-folder':
            return [
                Source(
                    id=f'f01-{number}', name=name(number), folder_id='f01-folder', folder_name='F01',
                    path='/' + name(number), mime_type='image/jpeg',
                )
                for number in range(1, 22)
            ]
        if root == 'f02-folder':
            return [Source(
                id='f02-1', name=f02_name(), folder_id='f02-folder', folder_name='F02',
                path='/' + f02_name(), mime_type='image/jpeg',
            )]
        raise AssertionError(root)


class F02AuthorizationTests(unittest.TestCase):
    def test_f02_is_authorized_on_the_same_job_after_f01_commits(self):
        import app.main as main
        from fastapi.testclient import TestClient

        sheets = MemorySheets()
        drive = RecordingDrive()
        inspector = ScriptedF02('Closed', 'Ready', 'High', 'The first F02 image establishes that the letter has ended.')
        orchestrator = Orchestrator(sheets, drive, inspector, Settings())
        original_live, original_guard = main.live, main.guard
        try:
            main.live = lambda: orchestrator
            main.guard = lambda *args, **kwargs: None
            with TestClient(main.app) as client:
                created = client.post('/api/jobs', json={
                    'project': 'Tri Delta Chapters', 'source_folder_id': 'f01-folder',
                    'spreadsheet_id': 'register', 'batch_size': 21, 'box': 'Chapters Box 19',
                })
                self.assertEqual(created.status_code, 200, created.text)
                job_id = created.json()['job_id']
                self.assertEqual(created.json()['authorized_source_folder_ids'], ['f01-folder'])
                self.assertEqual(drive.calls, ['f01-folder'])
                duplicate = client.post('/api/jobs', json={
                    'project': 'Tri Delta Chapters', 'source_folder_id': 'f01-folder',
                    'spreadsheet_id': 'register', 'batch_size': 21, 'box': 'Chapters Box 19',
                })
                self.assertEqual(duplicate.status_code, 409)
                self.assertIn('already initialized', duplicate.json()['detail'])
                self.assertEqual(drive.calls, ['f01-folder'])
                early = client.post(f'/api/jobs/{job_id}/folders', json={'source_folder_id': 'f02-folder'})
                self.assertEqual(early.status_code, 409)
                self.assertIn('not Committed - Validated', early.json()['detail'])
                self.assertEqual(drive.calls, ['f01-folder'])
                self.assertEqual(inspector.calls, [])
                orchestrator.run(job_id)
                letter = sheets.rows('register', 'Batch Queue')[0]['Resulting Open Item']
                self.assertNotEqual(letter, 'None')
                self.assertEqual(sheets.rows('register', 'Batch Queue')[0]['Batch Status'], 'Committed - Validated')
                summary = next(row for row in sheets.rows('register', 'Folder Processing Unit Summary') if row.get('Source Group / Folder') == 'F01')
                self.assertEqual(summary['Processing Status'], 'Completed - Validated')
                self.assertEqual(summary['Open Item'], letter)
                self.assertEqual(len(inspector.calls), 1)
                rejected = client.post('/api/jobs', json={
                    'project': 'Tri Delta Chapters', 'source_folder_id': 'f02-folder',
                    'spreadsheet_id': 'register', 'batch_size': 21, 'box': 'Chapters Box 19',
                })
                self.assertEqual(rejected.status_code, 409)
                self.assertIn('Clean first-batch validation failed', rejected.json()['detail'])
                self.assertEqual(drive.calls, ['f01-folder'])
                self.assertEqual(len(sheets.rows('register', 'Batch Queue')), 1)
                sheets.set_field('register', 'Open Intellectual Item', 'WRONG-ITEM')
                authorized = client.post(f'/api/jobs/{job_id}/folders', json={'source_folder_id': 'f02-folder'})
                self.assertEqual(authorized.status_code, 200, authorized.text)
                self.assertEqual(authorized.json()['source_folder_id'], 'f01-folder')
                self.assertEqual(authorized.json()['authorized_source_folder_ids'], ['f01-folder', 'f02-folder'])
                self.assertEqual(authorized.json()['status'], 'Authorized / Pending Inspection')
                self.assertEqual(drive.calls, ['f01-folder', 'f02-folder'])
                self.assertEqual(len(inspector.calls), 1)
                again = client.post(f'/api/jobs/{job_id}/folders', json={'source_folder_id': 'f02-folder'})
                self.assertEqual(again.status_code, 200, again.text)
                self.assertEqual(drive.calls, ['f01-folder', 'f02-folder'])
                self.assertEqual(len(sheets.rows('register', 'Batch Queue')), 2)
        finally:
            main.live, main.guard = original_live, original_guard
        f02 = sheets.rows('register', 'Batch Queue')[1]
        self.assertEqual(f02['Prior Open Item'], letter)
        self.assertNotEqual(f02['Prior Open Item'], 'WRONG-ITEM')
        self.assertEqual(f02['Batch Status'], 'Authorized / Pending Inspection')
        self.assertEqual(json.loads(f02['Notes'])['manifest'][0]['name'], f02_name())
        self.assertEqual(len(json.loads(f02['Notes'])['manifest']), 1)
        sheets.set_field('register', 'Open Intellectual Item', letter)
        orchestrator.run(job_id)
        self.assertEqual(inspector.calls[1]['sources'], [f02_name()])
        self.assertEqual(inspector.calls[1]['context']['prior_open_item'], letter)
        self.assertEqual(inspector.calls[1]['context']['existing_open_record']['records'][0]['Item ID'], letter)
        committed = {row['Digital Asset'] for row in inspector.calls[1]['context']['existing_open_record']['committed_assets']}
        self.assertTrue(all(name(number) in committed for number in range(16, 22)))
        self.assertNotIn(f02_name(), committed)
        letters = [row for row in sheets.rows('register', 'Itemization') if row.get('Type') == 'Letter']
        self.assertEqual(len(letters), 1)
        self.assertEqual(letters[0]['Item ID'], letter)
        self.assertIn(name(16), letters[0]['Digital Assets'])
        self.assertIn(f02_name(), letters[0]['Digital Assets'])
        qc = [row['Digital Asset'] for row in sheets.rows('register', 'Asset QC')]
        self.assertEqual(qc.count(name(16)), 1)
        self.assertEqual(qc.count(f02_name()), 1)
        self.assertEqual(len(qc), len(set(qc)))
        self.assertEqual(len(qc), 22)


if __name__ == '__main__':
    unittest.main()
