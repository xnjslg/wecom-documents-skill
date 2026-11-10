"""Native document transport for the checked Windows WeCom client.

Every response is retained in the caller's private attempt directory. Document
mutations and uploads are submitted once; only explicit read operations retry
API 10004. Long-task polls retain the originating request's trace ID.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
import uuid

from client import ApiError, Client, ProtocolError, decode_mcp_result

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
READ_OPERATIONS = frozenset(('read', 'flowchart_pages', 'poll'))
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
    def __init__(self, pid: int, output: Path):
        if type(pid) is not int or pid <= 0:
            raise ValueError('A positive current WeCom agent PID is required')
        self.output = output
        self.output.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.count = 0
        source = native_source()
        self.client = Client(pid)
        try:
            self.client.script.unload()
            self.client.script = self.client.session.create_script(source)
            self.client.script.load()
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
        names = self.client.tool_names()
        result = {'native_bridge_available': 'wecom_tool' in names,
                  'upload_media_available': 'upload_media' in names,
                  'upload_doc_media_available': 'upload_doc_media' in names,
                  'document_routes': {}}
        if 'wecom_tool' not in names:
            return result
        api = self.raw('discovery', {'service': 'doc'}, uuid.uuid4().hex)
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

    def _request(self, operation: str, parameters: dict, trace: str) -> dict:
        attempts = 3 if operation in READ_OPERATIONS else 1
        for attempt in range(attempts):
            try:
                return self.raw(operation, parameters, trace)
            except ApiError as error:
                if error.code != 10004 or attempt + 1 == attempts:
                    raise
                time.sleep(attempt + 1)
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
                api = self._request('poll', {'taskid': taskid}, trace)
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
