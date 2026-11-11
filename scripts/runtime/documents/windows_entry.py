"""One private document job, invoked by the serialized Windows PowerShell runner.

argv[1] is the JSON job file. stdout is one metadata-only JSON summary; raw
responses, values and attempted scripts remain in the private output directory.
This module performs no client IO on import.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from client import ApiError, ProtocolError, REFERENCE_VERSION
from documents.io import DocumentSession, ROUTES

COMMANDS = frozenset(('probe', 'snapshot', 'state', 'locate', 'apply', 'io', 'upload', 'refresh'))
JOB_FIELDS = frozenset(('command', 'docid', 'output_windows', 'browser_id', 'ranges',
                        'text', 'script', 'expected_version', 'before_sha256', 'operation', 'request',
                        'kind', 'file_windows', 'python_windows'))


class DocumentJobError(RuntimeError):
    pass


def required_string(value: dict, key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise DocumentJobError(f'{key} must be a nonempty string')
    return result


def positive_pid(name: str, *, required: bool) -> int:
    value = os.environ.get(name, '0')
    if not re.fullmatch(r'[0-9]+', value):
        raise DocumentJobError(f'{name} must be a nonnegative integer')
    pid = int(value)
    if required and pid <= 0:
        raise DocumentJobError(f'{name} is unavailable for this command')
    return pid


def docid_value(job: dict, *, required: bool = True) -> str | None:
    value = job.get('docid')
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or any(
        character in '/?#\\' or character.isspace() or ord(character) < 32 for character in value
    ):
        raise DocumentJobError('docid must be a document ID without URL separators or whitespace')
    return value


def ranges_value(job: dict) -> list[dict]:
    values = job.get('ranges', [])
    if not isinstance(values, list):
        raise DocumentJobError('ranges must be an array')
    for value in values:
        if not isinstance(value, dict) or set(value) != {'start', 'end'} or any(
            type(value[key]) is not int for key in ('start', 'end')
        ) or value['start'] < 0 or value['end'] < value['start']:
            raise DocumentJobError('Every range must contain integer start/end with 0 <= start <= end')
    return values


class Attempt:
    def __init__(self, job: dict):
        self.output = Path(required_string(job, 'output_windows'))
        if not self.output.is_absolute() or not self.output.is_dir():
            raise DocumentJobError('output_windows must be an existing absolute directory')
        self.state = {'command': job['command'], 'status': 'started', 'write_attempted': False}
        self.save('attempt.json', self.state)

    def location(self, path: Path) -> str:
        return str(path)

    def save(self, name: str, value: object) -> Path:
        path = self.output / name
        with path.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        return path

    def phase(self, status: str, **metadata: object) -> None:
        self.state.update(status=status, **metadata)
        (self.output / 'attempt.json').write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def payload_text(envelope: dict, kind: str) -> tuple[str, str]:
    if type(envelope.get('errcode')) is not int or envelope['errcode'] != 0:
        raise ProtocolError(f'{kind}: expected a successful native content envelope')
    fields = [key for key in ('content', 'file_path') if key in envelope]
    if len(fields) != 1 or not isinstance(envelope[fields[0]], str):
        raise ProtocolError(f'{kind}: content/file_path must be mutually exclusive text fields')
    text = envelope[fields[0]]
    if kind == 'ooxml':
        tree = json.loads(text)
        if not isinstance(tree, dict) or tree.get('type') != 'Document':
            raise ProtocolError('OOXML file_path/content is a JSON Document tree, not a disk path')
    return text, fields[0]


def take_snapshot(job: dict, attempt: Attempt, label: str, *, allow_partial: bool = False) -> dict:
    docid = docid_value(job)
    pid = positive_pid('WECOM_DOCUMENT_AGENT_PID', required=True)
    directory = attempt.output / label
    directory.mkdir(mode=0o700)
    metadata = {}
    errors = {}
    with DocumentSession(pid, directory / 'native') as session:
        for kind in ('markdown', 'ooxml'):
            try:
                envelope = session.call('read', {'docid': docid, 'content_type': kind})
            except (ApiError, TimeoutError) as error:
                failure = {'format': kind, 'error_type': type(error).__name__,
                           'message': str(error), 'traceback': traceback.format_exc()}
                if isinstance(error, ApiError):
                    failure['api_error_code'] = error.code
                error_path = directory / f'{kind}-error.json'
                with error_path.open('x', encoding='utf-8') as stream:
                    json.dump(failure, stream, ensure_ascii=False, indent=2)
                    stream.write('\n')
                # Editing requires both formats. A standalone read can use an
                # already validated body when the supplemental request fails.
                if not allow_partial or not metadata:
                    raise
                errors[kind] = {'error_type': failure['error_type'],
                                'error_json': attempt.location(error_path)}
                if isinstance(error, ApiError):
                    errors[kind]['api_error_code'] = error.code
                break
            envelope_path = directory / f'{kind}.json'
            with envelope_path.open('x', encoding='utf-8') as stream:
                json.dump(envelope, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
            text, field = payload_text(envelope, kind)
            source = envelope.get('url')
            if not isinstance(source, str) or urlsplit(source).hostname != 'doc.weixin.qq.com' or urlsplit(source).path != '/doc/' + docid:
                raise ProtocolError('Native content envelope identifies a different document')
            content_path = directory / ('markdown.txt' if kind == 'markdown' else 'ooxml-tree.json')
            with content_path.open('x', encoding='utf-8', newline='') as stream:
                stream.write(text)
            metadata[kind + '_json'] = attempt.location(envelope_path)
            metadata[kind + '_content'] = attempt.location(content_path)
            metadata[kind + '_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
            metadata[kind + '_characters'] = len(text)
            metadata[kind + '_payload_field'] = field
    available = [kind for kind in ('markdown', 'ooxml') if kind + '_content' in metadata]
    metadata.update(complete=len(available) == 2, available_formats=available,
                    missing_formats=[kind for kind in ('markdown', 'ooxml') if kind not in available],
                    format_errors=errors)
    return metadata


# All SDK members below were observed in the current editor. This reads the
# existing model/selection/queues; it never changes selection, mounts or saves.
STATE_FUNCTION = r"""function readDocumentState(ranges) {
  const e=window.pad&&window.pad.editor, app=window.App;
  if(!e||!app||!app.changesetManager||!app.collabCenterAdapter)throw new Error('Existing editor services unavailable');
  const m=app.changesetManager,a=app.collabCenterAdapter,d=e.service.getDocument();
  const pending=a.getLocalUserChanges(),version=a.getVersion();
  if(!Array.isArray(m.composeCommands)||!Array.isArray(pending)||!Number.isInteger(version)||
     typeof a.isConnected!=='boolean'||typeof m.hasPauseCollab!=='boolean'||
     !(m.composeTimer===null||typeof m.composeTimer==='number'))throw new Error('Existing synchronization structure differs');
  const selection=e.selection.gcpRange;
  return {pathname:location.pathname,version,
    selection:{start:selection.begin(),end:selection.end()},
    editable:e.clipboardManager.context.isEditable(),
    sync:{state:a.isConnected&&!m.hasPauseCollab&&!m.composeCommands.length&&m.composeTimer===null&&!pending.length?'quiescent':'busy-or-offline',
      connected:a.isConnected,paused:m.hasPauseCollab,composingCommands:m.composeCommands.length,
      composeTimerActive:m.composeTimer!==null,waitingOrCommittingChanges:pending.length,
      version,serverPersistence:'unverified'},
    ranges:ranges.map(r=>{
      const raw=d.readText(r.start,r.end-r.start);
      const drawing=e.state.getDrawingStream().getDrawingInfoFromGcp(r.start);
      const math=e.state.getMathStream().getMathInfo(r.start,1);
      const scalar=v=>v===null||['string','number','boolean'].includes(typeof v)?v:'unknown';
      let mathInfo=null,drawingInfo=null;
      if(math){
        const p=math.property;
        mathInfo={type:scalar(p&&p.type),isBegin:scalar(p&&p.isBegin),nestingLevel:scalar(p&&p.nestingLevel),
          range:math.range&&typeof math.range.begin==='function'&&typeof math.range.end==='function'
            ?{start:math.range.begin(),end:math.range.end()}:'unknown'};
      }
      if(drawing){
        const p=drawing.property,g=p&&p.drawing&&p.drawing.graphic&&p.drawing.graphic.graphicData;
        const pic=g&&g.pic,wsp=g&&g.wsp,contentId=wsp&&wsp.txbx&&wsp.txbx.contentId;
        drawingInfo={start:drawing.range.begin(),end:drawing.range.end(),
          anchorId:p&&typeof p.anchorId==='string'?p.anchorId:'unknown',
          contentId:typeof contentId==='string'?contentId:null,
          kind:pic&&!wsp?'pic':wsp&&!pic?'wsp':'unknown'};
      }
      return {start:r.start,end:r.end,rawLength:raw.length,
        rawUtf16:Array.from({length:raw.length},(_,i)=>raw.charCodeAt(i)),
        mathInfo,drawingInfo};
    })};
}"""


def state_expression(ranges: list[dict]) -> str:
    return '(' + STATE_FUNCTION + ')(' + json.dumps(ranges, separators=(',', ':')) + ')'


def locate_expression(text: str) -> str:
    return ('''(() => {
      const hits=window.pad.editor.service.getDocument().search(''' + json.dumps(text) + ''');
      if(!Array.isArray(hits))throw new Error('Document.search did not return the observed array');
      const matches=hits.map(hit=>({start:hit.realRange.begin(),end:hit.realRange.end()}));
      const state=(''' + STATE_FUNCTION + ''')(matches);state.matches=matches;return state;
    })()''')


def cdp_value(response: dict) -> tuple[bool, object]:
    if 'exceptionDetails' in response:
        raise DocumentJobError('The editor expression raised an exception; inspect editor-response.json')
    remote = response.get('result')
    if not isinstance(remote, dict):
        raise DocumentJobError('The editor did not return the observed CDP RemoteObject')
    return 'value' in remote, remote.get('value')


def read_state(cef, attempt: Attempt, name: str) -> dict:
    response = cef.evaluate_document(state_expression([]))
    attempt.save(name, response)
    available, value = cdp_value(response)
    if not available or not isinstance(value, dict) or type(value.get('version')) is not int:
        raise DocumentJobError('Existing editor state/version is unavailable')
    return value


def wait_quiescent(cef, attempt: Attempt) -> dict:
    deadline = time.monotonic() + 30
    consecutive = 0
    previous_version = None
    index = 0
    while time.monotonic() < deadline:
        index += 1
        value = read_state(cef, attempt, f'quiescent-{index:03d}.json')
        if value['sync']['state'] == 'quiescent':
            consecutive = consecutive + 1 if value['version'] == previous_version else 1
            previous_version = value['version']
            if consecutive >= 2:
                return {'version': value['version'], 'local_quiescent': True, 'observations': index,
                        'minimum_observation_separation_ms': 500, 'server_persistence': 'unverified'}
        else:
            consecutive = 0
            previous_version = None
        time.sleep(0.5)
    raise DocumentJobError('Local editor queues did not reach two stable quiescent observations; do not replay')


def cef_client(job: dict):
    from documents.cef import CefDocumentClient
    browser_id = job.get('browser_id')
    if browser_id is not None and (type(browser_id) is not int or browser_id <= 0):
        raise DocumentJobError('browser_id must be an explicit positive candidate ID')
    return CefDocumentClient(positive_pid('WECOM_DOCUMENT_BROWSER_PID', required=True),
                             docid_value(job), browser_id=browser_id)


def apply_job(job: dict, attempt: Attempt) -> dict:
    script = required_string(job, 'script')
    expected = job.get('expected_version')
    sha = required_string(job, 'before_sha256')
    if type(expected) is not int or expected < 0 or not re.fullmatch(r'[a-fA-F0-9]{64}', sha):
        raise DocumentJobError('apply requires an integer expected_version and a SHA-256 hex digest')
    with (attempt.output / 'edit.js').open('x', encoding='utf-8', newline='') as stream:
        stream.write(script)
    attempt.phase('reading_before', expected_version=expected, expected_markdown_sha256=sha.lower())
    before = take_snapshot(job, attempt, 'before')
    if before['markdown_sha256'] != sha.lower():
        raise DocumentJobError('Latest native Markdown SHA-256 differs from before_sha256; no edit submitted')
    attempt.phase('binding_editor')
    with cef_client(job) as cef:
        state = read_state(cef, attempt, 'editor-before-state.json')
        if state['version'] != expected:
            raise DocumentJobError('Existing editor version differs from expected_version; no edit submitted')
        if state['sync']['state'] != 'quiescent':
            raise DocumentJobError('Existing editor has pending changes or is offline; no edit submitted')
        guarded = ('(() => {if(window.App.collabCenterAdapter.getVersion()!==' + str(expected) +
                   ')throw new Error("Expected editor version changed before execution");return (\n' + script + '\n);})()')
        attempt.phase('editor_expression_submitted', write_attempted=True, browser_id=cef.browser_id)
        response = cef.evaluate_document(guarded, timeout=30)
        attempt.save('editor-response.json', response)
        available, value = cdp_value(response)
        result = value if available else {'editor_value_available': False}
        result_path = attempt.save('result.json', result)
        quiescent = wait_quiescent(cef, attempt)
        browser_id = cef.browser_id
    # Native transport is opened only after all CEF references have drained.
    attempt.phase('editor_closed_reading_after')
    after = take_snapshot(job, attempt, 'after')
    attempt.phase('written_needs_audit', final_editor_version=quiescent['version'])
    return {'ok': True, 'status': 'written_needs_audit', 'browser_id': browser_id,
            'version': quiescent['version'], 'editor_value_available': available,
            'result_json': attempt.location(result_path), 'before': before, 'after': after,
            'local_sync': quiescent, 'target_correctness_verified': False, 'outside_preservation_verified': False}


def execute_job(job: dict, attempt: Attempt) -> dict:
    command = job['command']
    if command == 'apply':
        return apply_job(job, attempt)
    if command == 'probe':
        pid = positive_pid('WECOM_DOCUMENT_AGENT_PID', required=True)
        with DocumentSession(pid, attempt.output / 'native') as session:
            capabilities = session.capabilities()
            binaries = session.client.binaries
        browser_pid = positive_pid('WECOM_DOCUMENT_BROWSER_PID', required=False)
        result = {'status': 'capabilities_inspected',
                  'client_version': os.environ.get('WECOM_DOCUMENT_CLIENT_VERSION'),
                  'agent_pid': pid, 'browser_pid': browser_pid,
                  **capabilities,
                  'binaries': binaries, 'document_binding_verified': False,
                  'editor_connection': 'not_checked; run state with the target docid'}
    elif command == 'snapshot':
        snapshot = take_snapshot(job, attempt, 'snapshot', allow_partial=True)
        result = {'status': 'snapshot_saved' if snapshot['complete'] else 'snapshot_partial', **snapshot}
    elif command == 'io':
        operation = required_string(job, 'operation')
        request = job.get('request')
        if operation not in ROUTES or not isinstance(request, dict):
            raise DocumentJobError('io requires a supported operation and the original request object')
        docid = docid_value(job, required=False)
        if docid is not None and 'docid' in request and request['docid'] != docid:
            raise DocumentJobError('io request.docid differs from job.docid')
        attempt.phase('native_request_submitted', write_attempted=operation in ('create', 'append', 'overwrite', 'import'), operation=operation)
        with DocumentSession(positive_pid('WECOM_DOCUMENT_AGENT_PID', required=True), attempt.output / 'native') as session:
            native = session.call(operation, request)
        path = attempt.save('result.json', native)
        return {'ok': True, 'status': 'native_response_saved', 'operation': operation,
                'result_json': attempt.location(path), 'result_keys': list(native),
                'target_correctness_verified': False}
    elif command == 'upload':
        kind = job.get('kind')
        if kind not in ('document-image', 'media'):
            raise DocumentJobError('upload kind must be document-image or media')
        file_path = Path(required_string(job, 'file_windows'))
        if not file_path.is_absolute() or not file_path.is_file():
            raise DocumentJobError('file_windows must identify an existing absolute local file')
        arguments = {'file_path': str(file_path)}
        if kind == 'document-image':
            arguments['open_id'] = docid_value(job)
        attempt.phase('upload_submitted', write_attempted=True, upload_kind=kind)
        with DocumentSession(positive_pid('WECOM_DOCUMENT_AGENT_PID', required=True), attempt.output / 'native') as session:
            uploaded = session.tool('upload_doc_media' if kind == 'document-image' else 'upload_media', arguments)
        path = attempt.save('result.json', uploaded)
        return {'ok': True, 'status': 'upload_response_saved', 'kind': kind,
                'result_json': attempt.location(path), 'result_keys': list(uploaded)}
    else:
        if command == 'state':
            expression = state_expression(ranges_value(job))
        elif command == 'locate':
            expression = locate_expression(required_string(job, 'text'))
        elif command != 'refresh':
            raise DocumentJobError('Unsupported document command')
        with cef_client(job) as cef:
            if command == 'refresh':
                attempt.phase('explicit_refresh_submitted')
                response = cef.refresh_document(docid=docid_value(job))
                attempt.save('editor-response.json', response)
                result = {'refresh_requested': True, 'browser_id': cef.browser_id, 'readback_verified': False}
            else:
                response = cef.evaluate_document(expression)
                attempt.save('editor-response.json', response)
                available, result = cdp_value(response)
                if not available or not isinstance(result, dict):
                    raise DocumentJobError('The requested editor read did not return an object value')
                result['browser_id'] = cef.browser_id
        path = attempt.save('result.json', result)
        summary = {'ok': True, 'status': 'refresh_requested' if command == 'refresh' else 'editor_read_saved',
                   'result_json': attempt.location(path), 'browser_id': result['browser_id']}
        if command != 'refresh':
            summary.update(version=result['version'], range_count=len(result['ranges']),
                           local_sync_state=result['sync']['state'])
        if command == 'locate':
            summary['match_count'] = len(result['matches'])
        return summary
    path = attempt.save('result.json', result)
    return {'ok': True, **result, 'result_json': attempt.location(path)}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    attempt = None
    try:
        if len(argv) != 1:
            raise DocumentJobError('Pass exactly one private JSON job filename')
        job = json.loads(Path(argv[0]).read_text(encoding='utf-8'))
        if not isinstance(job, dict) or job.get('command') not in COMMANDS or set(job) - JOB_FIELDS:
            raise DocumentJobError('Malformed document job or unexpected job field')
        attempt = Attempt(job)
        result = execute_job(job, attempt)
        if result.get('status') == 'snapshot_partial':
            attempt.phase('partial', available_formats=result['available_formats'],
                          missing_formats=result['missing_formats'])
        elif attempt.state['status'] != 'written_needs_audit':
            attempt.phase('completed')
        attempt.save('summary.json', result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except Exception as error:
        summary = {'ok': False, 'status': 'failed', 'error_type': type(error).__name__,
                   'message': str(error) if isinstance(error, DocumentJobError) else 'Document job failed; inspect private artifacts'}
        if isinstance(error, ApiError):
            summary['api_error_code'] = error.code
        if isinstance(error, ProtocolError) or type(error).__name__ in ('CefDocumentError', 'DocumentNotOpenError'):
            summary['compatibility_advice'] = ('Inspect the original error, login and document state first. '
                'For an interface incompatibility, the tested reference is WeCom '+REFERENCE_VERSION+
                '; this is a troubleshooting reference, not a required version.')
        if attempt is not None:
            failure = {'error_type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()}
            candidates = getattr(error, 'candidates', None)
            if isinstance(candidates, tuple) and all(is_dataclass(candidate) for candidate in candidates):
                failure['candidates'] = [asdict(candidate) for candidate in candidates]
                summary['candidate_browser_ids'] = [candidate.browser_id for candidate in candidates]
            attempt.phase('failed', error_type=type(error).__name__)
            failure_path = attempt.save('failure.json', failure)
            summary.update(result_json=attempt.location(failure_path), write_attempted=attempt.state['write_attempted'])
            attempt.save('summary.json', summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
