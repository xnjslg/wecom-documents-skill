"""Native document transport for the checked Windows WeCom client.

Every response is retained in the caller's private attempt directory. Document
mutations and uploads are submitted once; only explicit read operations retry
API 10004. Long-task polls retain the originating request's trace ID.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
import uuid

from client import ApiError, Client, ProtocolError, decode_mcp_result
from documents.diagnostics import event

BASE = Path(__file__).resolve().parents[1]
ROUTES = {
    'read': {'path': '/doc/contents/get', 'keys': ['docid', 'content_type', 'sub_id']},
    'create': {'path': '/doc/create', 'keys': ['doc_name', 'doc_type', 'content', 'content_type',
                                           'doc_requests', 'file_path', 'fields', 'grid_data', 'sheet_title', 'source_type']},
    'append': {'path': '/doc/contents/append', 'keys': ['docid', 'content']},
    'overwrite': {'path': '/doc/contents/overwrite', 'keys': ['docid', 'content_type', 'content', 'file_path']},
    'import': {'path': '/doc/import', 'keys': ['doc_type', 'file_name', 'file_content', 'media_id', 'overwrite_doc_id']},
    'flowchart_pages': {'path': '/doc/flowcharts/pages/list', 'keys': ['docid']},
    'poll': {'path': '/task/query', 'keys': ['taskid']},
}
READ_OPERATIONS = frozenset(('discovery', 'read', 'flowchart_pages', 'poll'))
# Retries are opt-in and add at most three seconds of scheduled waiting.
READ_RETRY_DELAYS = (1, 2)
UPLOAD_TOOLS = frozenset(('upload_media', 'upload_doc_media'))


def object_value(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ProtocolError(f'{label}: expected an object')
    return value


def native_source() -> str:
    source = (BASE / 'native.js').read_text(encoding='utf-8')
    anchor = "  discovery: {path:'/service/discovery', keys:['service']},"
    if source.count(anchor) != 1:
        raise ProtocolError('The checked native source anchor has changed')
    source = source.replace(anchor, anchor + '\n' + '\n'.join(
        '  ' + name + ': ' + json.dumps(spec) + ',' for name, spec in ROUTES.items()))
    source += """
rpc.exports.documentupload = async function(name,args) {
  if (!['upload_media','upload_doc_media'].includes(name)) throw new Error('Unexpected document upload tool');
  return await post({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:name,arguments:args}});
};
"""
    return source


class DocumentSession:
    def __init__(self, pid: int, output: Path, *, read_attempts: int = 1):
        if type(pid) is not int or pid <= 0:
            raise ValueError('A positive current WeCom agent PID is required')
        if type(read_attempts) is not int or not 1 <= read_attempts <= 3:
            raise ValueError('read_attempts must be an integer from 1 to 3')
        self.read_attempts = read_attempts
        self.output = output
        self.output.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.count = 0
        source = native_source()
        event(self.output, 'native_attach_started', agent_pid=pid)
        self.client = Client(pid)
        try:
            self.client.script.unload()
            self.client.script = self.client.session.create_script(source)
            self.client.script.load()
            event(self.output, 'native_attach_completed', agent_pid=pid)
        except BaseException as error:
            self.client.close(error)
            raise

    def save(self, name: str, value: object) -> Path:
        if Path(name).name != name:
            raise ValueError('Document artifact name must be a filename')
        path = self.output / name
        with path.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        return path

    def capabilities(self) -> dict:
        event(self.output, 'tools_list_started')
        names = self.client.tool_names()
        event(self.output, 'tools_list_completed', tool_count=len(names))
        result = {'native_bridge_available': 'wecom_tool' in names,
                  'upload_media_available': 'upload_media' in names,
                  'upload_doc_media_available': 'upload_doc_media' in names,
                  'document_routes': {}}
        if 'wecom_tool' not in names:
            return result
        api = self._request('discovery', {'service': 'doc'}, uuid.uuid4().hex)
        service = api.get('result')
        if isinstance(service, str):
            service = json.loads(service)
        service = object_value(service, 'document service discovery')
        if service.get('name') != 'doc':
            raise ProtocolError('Discovery returned a different service')
        self.save('document-service.json', service)
        paths = set()
        def walk(node):
            if not isinstance(node, dict):
                return
            if node.get('http_method') and isinstance(node.get('path'), str):
                paths.add(node['path'])
            for key in ('resources', 'methods'):
                children = object_value(node.get(key, {}), 'discovery.' + key)
                for child in children.values():
                    walk(child)
        walk(service)
        result['document_routes'] = {name: spec['path'] in paths for name, spec in ROUTES.items() if name != 'poll'}
        return result

    def _capture(self, operation: str, raw: str) -> dict:
        self.count += 1
        with (self.output / f'{self.count:03d}-{operation}.http').open('xb') as stream:
            stream.write(raw.encode('latin1'))
        return decode_mcp_result(raw.encode('latin1'))

    def raw(self, operation: str, parameters: dict, trace: str) -> dict:
        started = time.monotonic()
        metadata = {'operation': operation, 'trace_id': trace,
                    'path': '/service/discovery' if operation == 'discovery' else ROUTES[operation]['path'],
                    'parameter_keys': sorted(parameters)}
        if parameters.get('content_type') in ('markdown', 'ooxml'):
            metadata['content_type'] = parameters['content_type']
        event(self.output, 'request_started', **metadata)
        previous_count = self.count
        try:
            api = self._raw(operation, parameters, trace)
        except Exception as error:
            detail = {'error_type': type(error).__name__}
            if isinstance(error, ApiError):
                detail['api_error_code'] = error.code
                callid = re.search(r'callid:\s*(\d+)', error.message)
                if callid:
                    detail['call_id'] = callid.group(1)
            if self.count > previous_count:
                detail['response_file'] = f'{self.count:03d}-{operation}.http'
            event(self.output, 'request_failed', **metadata, **detail,
                  elapsed_ms=round((time.monotonic() - started) * 1000))
            raise
        event(self.output, 'request_completed', **metadata,
              elapsed_ms=round((time.monotonic() - started) * 1000),
              response_file=f'{self.count:03d}-{operation}.http',
              asynchronous=bool(api.get('taskid')))
        return api

    def _raw(self, operation: str, parameters: dict, trace: str) -> dict:
        rpc = self._capture(operation, self.client.script.exports_sync.query(operation, parameters, trace))
        content = rpc.get('content')
        if rpc.get('isError') or not isinstance(content, list) or len(content) != 1:
            raise ProtocolError('Expected one successful native document tool result')
        text = object_value(content[0], 'content[0]').get('text')
        if not isinstance(text, str):
            raise ProtocolError('Document tool content must be JSON text')
        wrapped = object_value(json.loads(text), 'document tool')
        if not isinstance(wrapped.get('data'), str):
            raise ProtocolError('Document tool data must be JSON text')
        api = object_value(json.loads(wrapped['data']), 'document API')
        if 'error' in api:
            error = object_value(api['error'], 'document API error')
            if type(error.get('code')) is not int or not isinstance(error.get('message'), str):
                raise ProtocolError('Malformed native document API error')
            if error['code'] != 0:
                raise ApiError(error['code'], error['message'])
        return api

    def _request(self, operation: str, parameters: dict, trace: str, *, deadline: float | None = None) -> dict:
        attempts = self.read_attempts if operation in READ_OPERATIONS else 1
        delays = READ_RETRY_DELAYS
        for attempt in range(attempts):
            try:
                api = self.raw(operation, parameters, trace)
            except ApiError as error:
                if error.code != 10004 or attempt + 1 == attempts:
                    raise
                delay = delays[attempt]
                if deadline is not None and time.monotonic() + delay >= deadline:
                    raise
                event(self.output, 'read_retry_scheduled', operation=operation, trace_id=trace,
                      next_attempt=attempt + 2, max_attempts=attempts, delay_seconds=delay)
                time.sleep(delay)
            else:
                if attempt:
                    event(self.output, 'read_retry_recovered', operation=operation, trace_id=trace,
                          attempts_used=attempt + 1)
                return api
        raise AssertionError('Unreachable document request state')

    def call(self, operation: str, parameters: dict) -> dict:
        if operation not in ROUTES or not isinstance(parameters, dict):
            raise ValueError('Unsupported native document operation or request')
        if any(key not in ROUTES[operation]['keys'] for key in parameters):
            raise ValueError('Unexpected native document request field')
        trace = uuid.uuid4().hex
        api = self._request(operation, parameters, trace)
        taskid = api.get('taskid')
        if taskid:
            if not isinstance(taskid, str):
                raise ProtocolError('Document taskid must be a string')
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                api = self._request('poll', {'taskid': taskid}, trace, deadline=deadline)
                poll = object_value(api.get('long_task_poll'), 'long_task_poll')
                if type(poll.get('done')) is not bool:
                    raise ProtocolError('long_task_poll.done must be a boolean')
                if poll['done']:
                    break
                interval = poll.get('polling_interval_ms', 2000)
                if type(interval) is not int or interval < 0:
                    raise ProtocolError('long_task_poll.polling_interval_ms must be a nonnegative integer')
                time.sleep(min(max(0.5, interval / 1000), max(0, deadline - time.monotonic())))
            else:
                raise TimeoutError('Native document task exceeded 180 seconds; no mutation was replayed')
        result = api.get('result')
        if isinstance(result, str):
            result = json.loads(result)
        result = object_value(result, 'document API result')
        if 'errcode' in result and (type(result['errcode']) is not int or result['errcode'] != 0):
            raise ProtocolError('Native document result reported a nonzero/invalid errcode')
        return result

    def tool(self, name: str, arguments: dict) -> dict:
        if name not in UPLOAD_TOOLS or not isinstance(arguments, dict):
            raise ValueError('Unsupported document upload tool or arguments')
        rpc = self._capture(name, self.client.script.exports_sync.documentupload(name, arguments))
        content = rpc.get('content')
        if rpc.get('isError') or not isinstance(content, list) or len(content) != 1:
            raise ProtocolError('Native document upload failed')
        text = object_value(content[0], 'upload content').get('text')
        if not isinstance(text, str):
            raise ProtocolError('Native upload content must be JSON text')
        result = object_value(json.loads(text), 'upload response')
        if result.get('error'):
            raise ProtocolError('Native document upload reported an error')
        return object_value(result.get('result'), 'upload result')

    def close(self, failure: BaseException | None = None) -> None:
        self.client.close(failure)

    def __enter__(self) -> DocumentSession:
        return self

    def __exit__(self, kind: object, error: BaseException | None, traceback: object) -> bool:
        self.close(error)
        return False
