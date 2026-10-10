import json
import tempfile
import unittest
from pathlib import Path

from app.diagnostics import Diagnostics, exception_chain
from app.inspection import Inspector
from app.models import ASSET_HEADERS, RECORD_HEADERS, AssetRow, InspectionOutput, RecordRow, Source


class DiagnosticsTests(unittest.TestCase):
    def test_attempt_log_is_json_and_appends_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Diagnostics(directory).write('JOB', 'BATCH', 2, 'response_received',
                                                returned_asset_filenames=['a.jpg'],
                                                response_id='resp_1')
            Diagnostics(directory).write('JOB', 'BATCH', 2, 'inspection_validation_failed',
                                         missing=['b.jpg'])
            data = json.loads(Path(path).read_text())
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]['response_id'], 'resp_1')
            self.assertEqual(data[1]['missing'], ['b.jpg'])

    def test_incomplete_model_response_keeps_usage_and_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'rules.md').write_text('Inspect every image.', encoding='utf-8')

            class Settings:
                governing_docs_dir = directory
                diagnostics_dir = directory
                openai_model = 'test-model'
                openai_max_output_tokens = 30000
                openai_reasoning_effort = ''

            class Drive:
                def image(self, _source):
                    from io import BytesIO
                    from PIL import Image
                    out = BytesIO()
                    Image.new('RGB', (4, 4), 'white').save(out, format='JPEG')
                    return out.getvalue()

            asset = AssetRow.model_validate({**{key: '' for key in ASSET_HEADERS},
                                             'Digital Asset': 'one.jpg', 'Assigned Record ID': 'record'})
            record = RecordRow.model_validate({**{key: '' for key in RECORD_HEADERS},
                                               'Proposed Record ID': 'record'})

            class Client:
                def __init__(self):
                    self.responses = self

                def parse(self, **_request):
                    class Usage:
                        input_tokens = 123
                        output_tokens = 456
                        input_tokens_details = None

                    class Response:
                        id = 'resp_test'
                        status = 'completed'
                        incomplete_details = None
                        usage = Usage()
                        output_parsed = InspectionOutput(assets=[asset], records=[record])

                    return Response()

            class RecordingClient(Client):
                def parse(self, **request):
                    self.request = request
                    return super().parse(**request)

            sources = [Source(id=str(index), name=name, folder_id='folder', folder_name='F01',
                              path=name, mime_type='image/jpeg')
                       for index, name in enumerate(('one.jpg', 'two.jpg'), start=1)]
            client = RecordingClient()
            settings = Settings()
            settings.openai_reasoning_effort = 'high'
            settings.openai_max_output_tokens = 60000
            inspector = Inspector(settings, Drive(), client=client)
            with self.assertRaisesRegex(ValueError, 'expected 2 assets, got 1') as caught:
                inspector.inspect('JOB', 'BATCH', sources, {'inspection_attempt': 3}, [])
            self.assertEqual(caught.exception.inspection_usage['output_tokens'], 456)
            self.assertEqual(caught.exception.inspection_usage['response_id'], 'resp_test')
            path = Path(caught.exception.diagnostics_path)
            self.assertTrue(path.is_absolute())
            events = json.loads(path.read_text())
            self.assertEqual([event['event'] for event in events],
                             ['request_prepared', 'response_received', 'inspection_validation_failed', 'inspection_error'])
            self.assertEqual(events[2]['missing'], ['two.jpg'])
            self.assertEqual(events[1]['output_tokens'], 456)
            self.assertEqual(client.request['model'], 'test-model')
            self.assertEqual(client.request['max_output_tokens'], 60000)
            self.assertEqual(client.request['reasoning'], {'effort': 'high'})
            self.assertNotIn('reasoning_effort', client.request)
            prepared = events[0]
            self.assertGreater(prepared['prepared_bytes_total'], 0)
            self.assertEqual(prepared['image_count'], 2)
            self.assertIn('prepared_bytes', prepared['image_preparation'][0])

    def test_uploaded_images_survive_one_connection_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'rules.md').write_text('Inspect every image.', encoding='utf-8')

            class Settings:
                governing_docs_dir = directory
                diagnostics_dir = directory
                openai_model = 'test-model'
                openai_max_output_tokens = 1000
                openai_reasoning_effort = ''

            class Drive:
                def image(self, _source):
                    from io import BytesIO
                    from PIL import Image
                    out = BytesIO()
                    Image.new('RGB', (4, 4), 'white').save(out, format='JPEG')
                    return out.getvalue()

            asset = AssetRow.model_validate({**{key: '' for key in ASSET_HEADERS},
                                             'Digital Asset': 'one.jpg', 'Assigned Record ID': 'record'})
            record = RecordRow.model_validate({**{key: '' for key in RECORD_HEADERS},
                                               'Proposed Record ID': 'record'})

            class Files:
                def __init__(self):
                    self.created = []
                    self.deleted = []
                    self.resets_remaining = 1

                def create(self, *, file, purpose, expires_after):
                    if self.resets_remaining:
                        self.resets_remaining -= 1
                        raise ConnectionResetError('[WinError 10054] An existing connection was forcibly closed by the remote host')
                    name, data, mime = file
                    self.created.append({'name': name, 'bytes': len(data), 'mime': mime, 'purpose': purpose, 'expires_after': expires_after})

                    class Uploaded:
                        id = 'file-one'

                    return Uploaded()

                def delete(self, file_id):
                    self.deleted.append(file_id)

            class Client:
                def __init__(self):
                    self.files = Files()
                    self.responses = self

                def parse(self, **request):
                    self.request = request

                    class Usage:
                        input_tokens = 10
                        output_tokens = 2
                        input_tokens_details = None

                    class Response:
                        id = 'resp_files'
                        status = 'completed'
                        incomplete_details = None
                        usage = Usage()
                        output_parsed = InspectionOutput(assets=[asset], records=[record])

                    return Response()

            client = Client()
            inspector = Inspector(Settings(), Drive(), client=client)
            inspector._sleep = lambda _seconds: None
            source = Source(id='drive-1', name='one.jpg', folder_id='folder', folder_name='F02',
                            path='one.jpg', mime_type='image/jpeg')
            output, usage = inspector.inspect('JOB', 'BATCH', [source], {'inspection_attempt': 2}, [])
            image = client.request['input'][1]['content'][2]
            self.assertEqual(output.assets[0].digital_asset, 'one.jpg')
            self.assertEqual(image['file_id'], 'file-one')
            self.assertEqual(image['detail'], 'high')
            self.assertNotIn('image_url', image)
            self.assertEqual(client.files.created[0]['purpose'], 'user_data')
            self.assertEqual(client.files.deleted, ['file-one'])
            self.assertEqual(usage['response_id'], 'resp_files')
            log = json.loads((Path(directory) / 'JOB_BATCH_attempt-2.json').read_text(encoding='utf-8'))
            self.assertEqual(log[0]['event'], 'connection_retry')
            self.assertEqual(log[0]['operation'], 'files.create one.jpg')
            prepared = next(event for event in log if event['event'] == 'request_prepared')
            self.assertEqual(prepared['image_transport'], 'openai_files')
            self.assertEqual(prepared['uploaded_file_count'], 1)

    def test_exception_chain_keeps_cause_and_redacts_secrets(self):
        try:
            try:
                raise ConnectionError('reset while uploading sk-secretkey123')
            except ConnectionError as cause:
                raise RuntimeError('Connection error.') from cause
        except RuntimeError as exc:
            chain = exception_chain(exc)
        self.assertEqual([item['type'] for item in chain], ['RuntimeError', 'ConnectionError'])
        self.assertEqual(chain[0]['message'], 'Connection error.')
        self.assertNotIn('sk-secretkey123', chain[1]['message'])
        self.assertIn('[REDACTED]', chain[1]['message'])
        self.assertTrue(chain[1]['traceback'])

    def test_report_is_standalone_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Diagnostics(directory).write_report('benchmark-compare', outcome='semantic_benchmark_mismatch', matches=False, counts={'live': {'assets': 1}})
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['report'], 'benchmark-compare')
            self.assertFalse(data['matches'])
            self.assertEqual(data['counts']['live']['assets'], 1)
