"""Offline checks for partial reads and the editing snapshot requirement."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
sys.path.insert(0, str(RUNTIME))

from client import ApiError, ProtocolError
from documents import windows_entry as reader


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wecom-snapshot-test-')
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)
        self.job = {'command': 'snapshot', 'docid': 'w3_test', 'output_windows': str(self.output)}
        self.body = '用于校验的正文。'
        self.envelopes = {
            'markdown': {'errcode': 0, 'content': self.body,
                         'url': 'https://doc.weixin.qq.com/doc/w3_test?scode=returned'},
            'ooxml': {'errcode': 0, 'file_path': json.dumps({'type': 'Document', 'children': []}),
                      'url': 'https://doc.weixin.qq.com/doc/w3_test'},
        }
        self.environment = patch.dict(os.environ, {'WECOM_DOCUMENT_AGENT_PID': '1'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.session = MagicMock()
        self.session.__enter__.return_value = self.session

        def read(operation, parameters):
            self.assertEqual(operation, 'read')
            self.assertEqual(parameters['docid'], self.job['docid'])
            result = self.envelopes[parameters['content_type']]
            if isinstance(result, Exception):
                raise result
            return result

        self.session.call.side_effect = read
        self.transport = patch.object(reader, 'DocumentSession', return_value=self.session)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    def test_complete_snapshot_preserves_both_formats(self):
        result = reader.execute_job(self.job, reader.Attempt(self.job))
        self.assertTrue(result['ok'])
        self.assertTrue(result['complete'])
        self.assertEqual(result['status'], 'snapshot_saved')
        self.assertEqual(result['available_formats'], ['markdown', 'ooxml'])
        self.assertEqual(result['missing_formats'], [])
        self.assertEqual(Path(result['markdown_content']).read_text(), self.body)
        self.assertEqual(json.loads(Path(result['ooxml_content']).read_text())['type'], 'Document')

    def test_cli_reports_validated_body_when_ooxml_request_fails(self):
        self.envelopes['ooxml'] = ApiError(10004, 'operation failed')
        job_file = self.output / 'job.json'
        job_file.write_text(json.dumps(self.job), encoding='utf-8')
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = reader.main([str(job_file)])
        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(result['ok'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['status'], 'snapshot_partial')
        self.assertEqual(result['available_formats'], ['markdown'])
        self.assertEqual(result['missing_formats'], ['ooxml'])
        self.assertEqual(result['markdown_sha256'], hashlib.sha256(self.body.encode()).hexdigest())
        self.assertEqual(Path(result['markdown_content']).read_text(), self.body)
        error = result['format_errors']['ooxml']
        self.assertEqual(error['api_error_code'], 10004)
        self.assertEqual(json.loads(Path(error['error_json']).read_text())['format'], 'ooxml')
        self.assertEqual(json.loads((self.output / 'attempt.json').read_text())['status'], 'partial')
        self.assertEqual(json.loads((self.output / 'summary.json').read_text())['status'], 'snapshot_partial')
        self.assertNotIn(self.body, stdout.getvalue())

    def test_timeout_preserves_validated_body(self):
        self.envelopes['ooxml'] = TimeoutError('read task timed out')
        result = reader.execute_job(self.job, reader.Attempt(self.job))
        self.assertFalse(result['complete'])
        self.assertEqual(result['format_errors']['ooxml']['error_type'], 'TimeoutError')

    def test_first_format_failure_is_not_reported_as_success(self):
        self.envelopes['markdown'] = ApiError(10004, 'operation failed')
        with self.assertRaises(ApiError):
            reader.execute_job(self.job, reader.Attempt(self.job))
        self.assertEqual(self.session.call.call_count, 1)

    def test_invalid_document_identity_remains_an_error(self):
        self.envelopes['ooxml']['url'] = 'https://doc.weixin.qq.com/doc/different'
        with self.assertRaisesRegex(ProtocolError, 'different document'):
            reader.execute_job(self.job, reader.Attempt(self.job))

    def test_invalid_ooxml_tree_remains_an_error(self):
        self.envelopes['ooxml']['file_path'] = json.dumps({'type': 'Other'})
        with self.assertRaisesRegex(ProtocolError, 'Document tree'):
            reader.execute_job(self.job, reader.Attempt(self.job))

    def test_apply_never_binds_editor_after_partial_before_snapshot(self):
        self.envelopes['ooxml'] = ApiError(10004, 'operation failed')
        job = {**self.job, 'command': 'apply', 'script': 'true', 'expected_version': 1,
               'before_sha256': hashlib.sha256(self.body.encode()).hexdigest()}
        attempt = reader.Attempt(job)
        with patch.object(reader, 'cef_client') as editor:
            with self.assertRaises(ApiError):
                reader.apply_job(job, attempt)
            editor.assert_not_called()
        self.assertFalse(attempt.state['write_attempted'])


if __name__ == '__main__':
    unittest.main()
