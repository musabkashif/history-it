import json
import unittest
from googleapiclient.errors import HttpError
from app.main import google_http_exception

class Resp:
    def __init__(self, status, reason='Bad Request'):
        self.status = status
        self.reason = reason

def http_error(status, message, reason='Bad Request'):
    body = json.dumps({'error': {'message': message}}).encode()
    return HttpError(Resp(status, reason), body)

class ApiErrorTests(unittest.TestCase):
    def test_office_file_is_a_client_error_with_conversion_hint(self):
        err = google_http_exception(http_error(400, 'This operation is not supported for this document. The document must not be an Office file.'))
        self.assertEqual(err.status_code, 400)
        self.assertIn('Save as Google Sheets', err.detail)

    def test_permission_error_names_the_share(self):
        err = google_http_exception(http_error(403, 'The caller does not have permission', reason='Forbidden'))
        self.assertEqual(err.status_code, 403)
        self.assertIn('service account', err.detail)

    def test_google_server_error_is_not_reported_as_an_app_crash(self):
        err = google_http_exception(http_error(500, 'Internal error', reason='Internal Error'))
        self.assertEqual(err.status_code, 502)
        self.assertIn('Internal error', err.detail)

if __name__ == '__main__':
    unittest.main()
