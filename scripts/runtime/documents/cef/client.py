"""CEF document client, bound afresh to an explicit PID and document ID.

The Windows entrypoint must hold the existing Invoke-WeComQuery mutex for the
whole context, including cleanup. Importing this module performs no process IO.
evaluate() rejects possible side effects; evaluate_document() is the explicit
caller-authorized document-script path. Only refresh_document(docid=...) reloads.
"""
from __future__ import annotations

import threading
import time
import json
from collections import deque
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    import frida.core

from .errors import AmbiguousDocumentError, CefDocumentError, CleanupPendingError, DocumentInstance, DocumentNotOpenError
from .inventory import capture_browsers
from .layout import ROOT, document_url, load_layout


class _Exports(Protocol):
    def initialize(self, layout: dict[str, object], capture: dict[str, object], scope: dict[str, object]) -> dict[str, object]: ...
    def request(self, method: str, parameters: dict[str, object]) -> int: ...
    def beginclose(self) -> dict[str, object]: ...
    def status(self) -> dict[str, object]: ...
    def finish(self) -> dict[str, object]: ...


class CefDocumentClient:
    """One browser, one observer registration, one explicit lifetime owner."""

    def __init__(self, browser_pid: int, docid: str, *, browser_id: int | None = None):
        if type(browser_pid) is not int or browser_pid <= 0:
            raise ValueError("browser_pid must be a positive Windows process ID")
        if browser_id is not None and (type(browser_id) is not int or browser_id <= 0):
            raise ValueError("browser_id must be a positive ID from the matching candidates")
        self.browser_pid = browser_pid
        self.docid = docid
        self.document_url = document_url(docid)
        self.browser_id = browser_id
        self.instance: DocumentInstance | None = None
        self._session: frida.core.Session | None = None
        self._script: frida.core.Script | None = None
        self._exports: _Exports | None = None
        self._condition = threading.Condition()
        self._rpc_lock = threading.Lock()
        self._results: dict[int, dict[str, object]] = {}
        self._ready = False
        self._fault: CefDocumentError | None = None
        self._detached_reason: str | None = None
        self._close_requested = threading.Event()
        self._closed = threading.Event()
        self._custodian: threading.Thread | None = None
        self.last_cleanup_state: dict[str, object] | None = None
        self.last_checkpoint: dict[str, object] | None = None
        self._checkpoint_received_at: float | None = None
        self._checkpoints: deque[dict[str, object]] = deque(maxlen=16)
        self._open_timings: dict[str, int] = {}
        self._inventory_metadata: dict[str, object] = {}
        self._last_lifecycle: dict[str, object] | None = None
        self.initialization_timeout_diagnostics: dict[str, object] | None = None

    @staticmethod
    def _lifecycle_summary(status: dict[str, object] | None) -> dict[str, object] | None:
        if status is None:
            return None
        keys = ("phase", "cleanup_complete", "callbacks", "pending_count",
                "retained_browser_count", "safe_to_unload", "unsafe_native_state")
        result = {key: status[key] for key in keys if key in status}
        objects = status.get("objects")
        if isinstance(objects, list):
            result["objects"] = objects
        return result

    def diagnostics(self) -> dict[str, object]:
        """Only received metadata; never make a potentially blocking diagnostic RPC."""
        with self._condition:
            age = None if self._checkpoint_received_at is None else round(
                (time.monotonic() - self._checkpoint_received_at) * 1000)
            return {"browser_pid": self.browser_pid, "selected_browser_id": self.browser_id,
                    "timings_ms": dict(self._open_timings), "inventory": dict(self._inventory_metadata),
                    "last_checkpoint": self.last_checkpoint, "last_checkpoint_age_ms": age,
                    "checkpoint_history": list(self._checkpoints),
                    "last_lifecycle": self._last_lifecycle}

    def _preflight(self) -> tuple[dict[str, object], dict[str, object]]:
        layout = load_layout()
        return layout, capture_browsers(self.browser_pid, layout)

    def _message(self, message: dict[str, object], data: bytes | None) -> None:
        with self._condition:
            if message.get("type") == "error":
                self._fault = CefDocumentError(str(message.get("description", "Frida script error")))
                self._close_requested.set()
            elif message.get("type") == "send":
                payload = message.get("payload")
                if isinstance(payload, dict):
                    kind = payload.get("type")
                    if kind == "checkpoint":
                        self.last_checkpoint = {key: value for key, value in payload.items() if key != "type"}
                        self._checkpoint_received_at = time.monotonic()
                        self._checkpoints.append(self.last_checkpoint)
                    elif kind == "lifecycle":
                        self._last_lifecycle = self._lifecycle_summary(payload)
                    elif kind == "ready":
                        self.browser_id = payload["browser_id"]
                        self.instance = DocumentInstance(self.browser_id, self.document_url)
                        self._ready = True
                    elif kind == "result" and type(payload.get("id")) is int:
                        self._results[payload["id"]] = payload
                    elif kind == "failure":
                        text = str(payload.get("error", "CEF callback failed"))
                        if payload.get("code") == "ambiguous_document":
                            rows = payload["candidates"]
                            candidates = tuple(DocumentInstance(row["browser_id"], row["url"]) for row in rows)
                            self._fault = AmbiguousDocumentError(self.docid, candidates)
                        elif payload.get("code") == "document_not_open":
                            self._fault = DocumentNotOpenError(text)
                        else:
                            self._fault = CefDocumentError(text)
                        self._close_requested.set()
            self._condition.notify_all()

    def _detached(self, reason: str, crash: object = None) -> None:
        with self._condition:
            self._detached_reason = reason
            if not self._closed.is_set():
                self._fault = CefDocumentError(f"CEF session detached: {reason}")
            self._close_requested.set()
            self._condition.notify_all()

    def open(self, timeout: float = 15.0) -> CefDocumentClient:
        if timeout <= 0:
            raise ValueError("Timeout must be positive")
        if self._session is not None or self._closed.is_set():
            raise CefDocumentError("CEF client cannot be opened twice")
        started = time.monotonic()
        layout, capture = self._preflight()
        self._open_timings["inventory_ms"] = round((time.monotonic() - started) * 1000)
        self._inventory_metadata = {key: capture[key] for key in
            ("candidate_count", "scanned_bytes", "unreadable_chunks", "seconds") if key in capture}
        import frida
        attach_started = time.monotonic()
        session = frida.attach(self.browser_pid)
        self._session = session
        session.on("detached", self._detached)
        script: frida.core.Script | None = None
        try:
            script = session.create_script((ROOT / "agent.js").read_text(encoding="utf-8"))
            self._script = script
            script.on("message", self._message)
            script.load()
        except BaseException:
            # No RPC initialization ran, so no CEF callback/object was published.
            if script is not None and not script.is_destroyed:
                script.unload()
            if not session.is_detached:
                session.detach()
            self._closed.set()
            raise
        self._open_timings["attach_and_load_ms"] = round((time.monotonic() - attach_started) * 1000)
        self._exports = cast(_Exports, script.exports_sync)
        self._custodian = threading.Thread(target=self._cleanup_owner, name="cef-cdp-custodian", daemon=False)
        self._custodian.start()
        try:
            rpc_started = time.monotonic()
            with self._rpc_lock:
                self._exports.initialize(layout, capture, {"docid": self.docid,
                    "document_url": self.document_url, "browser_id": self.browser_id})
            self._open_timings["initialize_rpc_ms"] = round((time.monotonic() - rpc_started) * 1000)
            wait_started = time.monotonic()
            deadline = wait_started + timeout
            with self._condition:
                while not self._ready:
                    if self._fault is not None:
                        raise self._fault
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._open_timings["ready_wait_ms"] = round((time.monotonic() - wait_started) * 1000)
                        self.initialization_timeout_diagnostics = self.diagnostics()
                        checkpoint = self.last_checkpoint or {}
                        error = TimeoutError("CEF UI observer initialization timed out; last_stage=" +
                            str(checkpoint.get("stage", "no_checkpoint_received")) + "; active_operations=" +
                            json.dumps(checkpoint.get("active_operations", []), ensure_ascii=False))
                        error.add_note("CEF initialization before cleanup: " + json.dumps(
                            self.initialization_timeout_diagnostics, ensure_ascii=False))
                        raise error
                    self._condition.wait(min(remaining, 0.25))
                if self._fault is not None:
                    raise self._fault
            self._open_timings["ready_wait_ms"] = round((time.monotonic() - wait_started) * 1000)
            return self
        except BaseException as error:
            try:
                self.close(timeout=timeout)
            except CleanupPendingError as cleanup_error:
                error.add_note(str(cleanup_error))
            if self.initialization_timeout_diagnostics is not None:
                error.add_note("CEF initialization cleanup: " + json.dumps({"owner_closed": self._closed.is_set(),
                    "lifecycle": self._lifecycle_summary(self.last_cleanup_state),
                    "last_checkpoint": self.last_checkpoint}, ensure_ascii=False))
            raise

    def _call(self, method: str, parameters: dict[str, object], timeout: float) -> dict[str, object]:
        if timeout <= 0:
            raise ValueError("Timeout must be positive")
        with self._rpc_lock:
            if self._exports is None or not self._ready or self._close_requested.is_set():
                raise CefDocumentError("CEF document client is not open")
            request_id = self._exports.request(method, parameters)
        deadline = time.monotonic() + timeout
        with self._condition:
            while request_id not in self._results:
                if self._fault is not None:
                    raise self._fault
                if self._close_requested.is_set():
                    raise CefDocumentError("CEF document client is closing")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Stop further requests and unregister the observer; no weaker retry.
                    self._close_requested.set()
                    raise TimeoutError(f"CDP {method} result timed out")
                self._condition.wait(min(remaining, 0.25))
            result = self._results.pop(request_id)
        if result.get("success") is not True:
            raise CefDocumentError(f"CDP {method} failed: {result.get('value')!r}")
        value = result.get("value")
        if not isinstance(value, dict):
            raise CefDocumentError(f"CDP {method} returned a non-object result")
        return value

    def frame_tree(self, *, timeout: float = 10.0) -> dict[str, object]:
        return self._call("Page.getFrameTree", {}, timeout)

    def refresh_document(self, *, docid: str, timeout: float = 10.0) -> dict[str, object]:
        if docid != self.docid:
            raise CefDocumentError("Refresh requires the exact bound docid explicitly")
        return self._call("Page.reload", {"docid": docid}, timeout)

    def evaluate(self, expression: str, *, timeout: float = 10.0) -> dict[str, object]:
        if not isinstance(expression, str) or not 1 <= len(expression) <= 16384:
            raise ValueError("Expected a read-only expression of 1..16384 characters")
        return self._call("Runtime.evaluate", {"expression": expression}, timeout)

    def evaluate_document(self, expression: str, *, timeout: float = 20.0) -> dict[str, object]:
        """Run caller-authorized editor code in the explicitly bound document."""
        if not isinstance(expression, str) or not 1 <= len(expression) <= 16384:
            raise ValueError('Expected a reviewed document expression of 1..16384 characters')
        return self._call('Runtime.evaluate', {'expression': expression, 'document_script': True}, timeout)

    def _cleanup_owner(self) -> None:
        self._close_requested.wait()
        while not self._closed.is_set():
            # Target process death removes all of its native callbacks. A transport
            # loss alone does not establish that, so it never authorizes unload.
            if self._detached_reason in ("process-terminated", "process-replaced"):
                self._closed.set()
                break
            try:
                with self._rpc_lock:
                    exports = self._exports
                    script, session = self._script, self._session
                    if exports is None or script is None or session is None:
                        raise CefDocumentError("Cleanup owner lost its CEF session")
                    status = exports.beginclose()
                    self.last_cleanup_state = status
                    if status.get("safe_to_unload") is True:
                        if exports.finish().get("finished") is not True:
                            raise CefDocumentError("CEF script refused finish handshake")
                        if not script.is_destroyed:
                            script.unload()
                        if not session.is_detached:
                            session.detach()
                        self._closed.set()
                        break
            except Exception as error:
                with self._condition:
                    self._fault = CefDocumentError(f"CEF cleanup pending: {error}")
                    self._condition.notify_all()
            self._closed.wait(0.1)
        with self._condition:
            self._condition.notify_all()

    def close(self, timeout: float = 15.0) -> None:
        if self._session is None or self._closed.is_set():
            return
        self._close_requested.set()
        if not self._closed.wait(timeout):
            raise CleanupPendingError(
                "CEF cleanup has not drained; script remains pinned/attached and "
                "the custody thread remains alive. Do not force-unload it. "
                f"Last lifecycle state: {self.last_cleanup_state!r}"
            )
        if self._custodian is not None and threading.current_thread() is not self._custodian:
            self._custodian.join()

    def __enter__(self) -> CefDocumentClient:
        return self.open()

    def __exit__(self, kind: object, error: BaseException | None, traceback: object) -> bool:
        try:
            self.close()
        except CleanupPendingError as cleanup_error:
            if error is None:
                raise
            error.add_note(str(cleanup_error))
        return False


def list_document_instances(browser_pid: int, docid: str) -> tuple[DocumentInstance, ...]:
    """List current exact matches, closing native ownership before returning.

    A missing document raises DocumentNotOpenError. No CDP request or refresh is
    sent. The caller holds the same global query mutex as other adapter queries.
    """
    try:
        with CefDocumentClient(browser_pid, docid) as client:
            if client.instance is None:
                raise CefDocumentError("CEF reported ready without a document instance")
            return (client.instance,)
    except AmbiguousDocumentError as error:
        return error.candidates
