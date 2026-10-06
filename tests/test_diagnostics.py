import json
import tempfile
import unittest
from pathlib import Path

from app.diagnostics import Diagnostics
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
