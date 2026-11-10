"""Document transport primitives extracted from the checked local WeCom client."""

from __future__ import annotations

import hashlib
import io
import json
import os
from http.client import HTTPResponse
from pathlib import Path
from typing import cast

REFERENCE_VERSION = '5.0.12.6018'

class ProtocolError(RuntimeError):
    pass

class ApiError(RuntimeError):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f'WeCom error {code}: {message}')

def object_value(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ProtocolError(f'{path}: expected an object')
    return cast(dict[str, object], value)

class BufferedSocket:
    def __init__(self, data: bytes):
        self.data = data

    def makefile(self, mode: str) -> io.BytesIO:
        return io.BytesIO(self.data)

def decode_mcp_result(raw: bytes) -> dict[str, object]:
    response = HTTPResponse(BufferedSocket(raw))  # type: ignore[arg-type]
    response.begin()
    body = response.read()
    if response.status != 200:
        raise ProtocolError(f'Local MCP returned HTTP {response.status}')
    rpc = object_value(json.loads(body), 'rpc')
    if 'error' in rpc:
        raise ProtocolError(f'MCP: {rpc["error"]}')
    return object_value(rpc.get('result'), 'rpc.result')

def inspect_binaries() -> dict:
    install_text = os.environ.get('WECOM_DOCUMENT_INSTALL')
    if not install_text:
        raise ProtocolError('Use the Windows launcher to discover the running WeCom installation')
    install = Path(install_text)
    if not install.is_absolute():
        raise ProtocolError('The discovered WeCom installation must be an absolute path')
    inspected = {}
    for label, env, filename in (('main', 'WECOM_DOCUMENT_MAIN_PATH', 'wxwork.exe'),
                                 ('agent', 'WECOM_DOCUMENT_AGENT_PATH', 'wecom-agent.exe')):
        path = Path(os.environ.get(env, ''))
        if not path.is_absolute() or path.name.lower() != filename or not path.is_relative_to(install):
            raise ProtocolError(f'Invalid discovered {label} executable')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        inspected[label] = {'path': str(path), 'sha256': actual}
    return inspected


class Client:
    def __init__(self, pid: int):
        import frida

        self.binaries = inspect_binaries()
        self.pid = pid
        self.log_requests = False
        self.detached_reason: str | None = None
        self.session = frida.attach(pid)
        self.session.on('detached', self._on_detached)
        try:
            self.script = self.session.create_script(Path(__file__).with_name('native.js').read_text(encoding='utf-8'))
            self.script.load()
            identity = self.script.exports_sync.identity()
            if identity.get('pid') != pid or os.path.normcase(identity.get('path', '')) != os.path.normcase(self.binaries['agent']['path']):
                raise ProtocolError('Attached agent identity differs from the discovered process')
        except BaseException as error:
            try:
                if hasattr(self, 'script'):
                    self.close(error)
                elif not self.session.is_detached:
                    self.session.detach()
            except Exception as cleanup_error:
                error.add_note(f'WeCom initialization cleanup failed: {type(cleanup_error).__name__}')
            raise

    def tool_names(self) -> list[str]:
        result = decode_mcp_result(self.script.exports_sync.capabilities().encode('latin1'))
        tools = result.get('tools')
        if not isinstance(tools, list) or any(not isinstance(t, dict) or not isinstance(t.get('name'), str) for t in tools):
            raise ProtocolError('MCP capabilities did not contain a valid tools list')
        return sorted({t['name'] for t in tools})

    def _on_detached(self, reason: str, crash: object) -> None:
        self.detached_reason = reason

    def close(self, original_error: BaseException | None = None) -> None:
        import frida

        errors: list[Exception] = []
        try:
            try:
                if not self.script.is_destroyed:
                    try:
                        self.script.unload()
                    except frida.InvalidOperationError:
                        if not self.script.is_destroyed:
                            raise
            except Exception as error:
                errors.append(error)
        finally:
            try:
                if not self.session.is_detached:
                    try:
                        self.session.detach()
                    except frida.InvalidOperationError:
                        if not self.session.is_detached:
                            raise
            except Exception as error:
                errors.append(error)
        if errors:
            primary = original_error if original_error is not None else errors.pop(0)
            for error in errors:
                primary.add_note(f'WeCom cleanup failed: {type(error).__name__}')
            if original_error is None:
                raise primary
