"""Failure diagnostics preserve request identity without emitting document text."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from client import ApiError
from documents.io import DocumentSession


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session = object.__new__(DocumentSession)
        self.session.output = Path(temporary.name)
        self.session.count = 0
        self.session.read_attempts = 1

    def test_single_attempt_failure_has_trace_without_request_or_error_content(self):
        self.session._raw = Mock(side_effect=ApiError(10004, 'private text (callid: 12345)'))
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(ApiError):
            self.session._request('read', {'docid': 'private-id', 'content_type': 'markdown'}, 'abc123')
        self.assertEqual(self.session._raw.call_count, 1)
        log = stderr.getvalue()
        self.assertNotIn('private', log)
        records = [json.loads(line) for line in log.splitlines()]
        self.assertEqual(records[-1]['event'], 'request_failed')
        self.assertEqual(records[-1]['trace_id'], 'abc123')
        self.assertEqual(records[-1]['call_id'], '12345')
        self.assertEqual(records[-1]['api_error_code'], 10004)
        self.assertEqual(log, (self.session.output / 'diagnostics.jsonl').read_text())

    def test_default_session_stops_after_first_error_without_sleep(self):
        with patch('documents.io.Client'), redirect_stderr(io.StringIO()):
            with DocumentSession(1, self.session.output / 'default') as session:
                session._raw = Mock(side_effect=ApiError(10004, 'failed'))
                with patch('documents.io.time.sleep') as sleep, self.assertRaises(ApiError):
                    session.call('read', {'docid': 'private', 'content_type': 'markdown'})
                session._raw.assert_called_once()
                sleep.assert_not_called()

    def test_enabled_read_retry_is_visible_and_preserves_trace(self):
        self.session.read_attempts = 2
        self.session._raw = Mock(side_effect=[ApiError(10004, 'failed'), {'result': {}}])
        stderr = io.StringIO()
        with redirect_stderr(stderr), patch('documents.io.time.sleep') as sleep:
            self.session._request('read', {'docid': 'private-id'}, 'trace')
        records = [json.loads(line) for line in stderr.getvalue().splitlines()]
        retry = [record for record in records if record['event'] == 'read_retry_scheduled']
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0]['next_attempt'], 2)
        sleep.assert_called_once_with(1)
        recovered = [record for record in records if record['event'] == 'read_retry_recovered']
        self.assertEqual(recovered[0]['attempts_used'], 2)
        self.assertEqual(self.session._raw.call_args_list[0], self.session._raw.call_args_list[1])

    def test_mutation_is_never_retried(self):
        self.session.read_attempts = 3
        self.session._raw = Mock(side_effect=ApiError(10004, 'failed'))
        with redirect_stderr(io.StringIO()), self.assertRaises(ApiError):
            self.session._request('append', {'content': 'private text'}, 'trace')
        self.assertEqual(self.session._raw.call_count, 1)

    def test_discovery_survives_two_errors_and_keeps_request_identity(self):
        self.session.read_attempts = 3
        self.session.client = Mock()
        self.session.client.tool_names.return_value = ['wecom_tool']
        self.session._raw = Mock(side_effect=[ApiError(10004, 'first'), ApiError(10004, 'second'),
            {'result': {'name': 'doc', 'methods': {
                'read': {'http_method': 'POST', 'path': '/doc/contents/get'}}}}])
        with redirect_stderr(io.StringIO()), patch('documents.io.time.sleep') as sleep:
            result = self.session.capabilities()
        self.assertTrue(result['document_routes']['read'])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        calls = self.session._raw.call_args_list
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[1], calls[2])
        self.assertTrue((self.session.output / 'document-service.json').is_file())

    def test_exhaustion_preserves_last_error_and_has_no_success_log(self):
        self.session.read_attempts = 3
        last = ApiError(10004, 'last (callid: 67890)')
        self.session._raw = Mock(side_effect=[ApiError(10004, 'first'), ApiError(10004, 'second'), last])
        stderr = io.StringIO()
        with redirect_stderr(stderr), patch('documents.io.time.sleep') as sleep:
            with self.assertRaises(ApiError) as raised:
                self.session._request('read', {}, 'trace')
        self.assertIs(raised.exception, last)
        self.assertEqual(self.session._raw.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertNotIn('read_retry_recovered', stderr.getvalue())

    def test_other_errors_do_not_retry(self):
        self.session.read_attempts = 3
        for error in (ApiError(40001, 'unclassified error'), TimeoutError('unknown outcome')):
            with self.subTest(error=type(error).__name__):
                self.session._raw = Mock(side_effect=error)
                with redirect_stderr(io.StringIO()), patch('documents.io.time.sleep') as sleep:
                    with self.assertRaises(type(error)):
                        self.session._request('read', {}, 'trace')
                self.assertEqual(self.session._raw.call_count, 1)
                sleep.assert_not_called()

    def test_task_retry_never_resubmits_an_accepted_mutation(self):
        self.session.read_attempts = 3
        self.session._raw = Mock(side_effect=[{'taskid': 'accepted'}, ApiError(10004, 'transient'),
            {'long_task_poll': {'done': True}, 'result': {'errcode': 0}}])
        with redirect_stderr(io.StringIO()), patch('documents.io.time.sleep') as sleep:
            result = self.session.call('append', {'docid': 'private', 'content': 'private'})
        self.assertEqual(result, {'errcode': 0})
        calls = self.session._raw.call_args_list
        self.assertEqual([call.args[0] for call in calls], ['append', 'poll', 'poll'])
        self.assertEqual(calls[1], calls[2])
        self.assertEqual(calls[0].args[2], calls[1].args[2])
        self.assertEqual(calls[1].args[1], {'taskid': 'accepted'})
        sleep.assert_called_once_with(1)

    def test_poll_retry_does_not_wait_past_remaining_deadline(self):
        self.session.read_attempts = 3
        self.session._raw = Mock(side_effect=ApiError(10004, 'failed'))
        with redirect_stderr(io.StringIO()), patch('documents.io.time.sleep') as sleep:
            with patch('documents.io.time.monotonic', return_value=100), self.assertRaises(ApiError):
                self.session._request('poll', {'taskid': 'accepted'}, 'trace', deadline=100.5)
        sleep.assert_not_called()
        self.assertEqual(self.session._raw.call_count, 1)


if __name__ == '__main__':
    unittest.main()
