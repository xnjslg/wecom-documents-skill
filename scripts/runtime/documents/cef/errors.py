"""Errors and public document-instance metadata for the CEF document client."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentInstance:
    browser_id: int
    url: str


class CefDocumentError(RuntimeError):
    """A verified CEF precondition, native task or CDP operation failed."""


class DocumentNotOpenError(CefDocumentError):
    """The requested document was not found in the current checked inventory."""


class AmbiguousDocumentError(CefDocumentError):
    """Several open browser instances match; the caller must select one."""

    def __init__(self, docid: str, candidates: tuple[DocumentInstance, ...]):
        self.docid = docid
        self.candidates = candidates
        ids = ", ".join(str(item.browser_id) for item in candidates)
        super().__init__(f"Document {docid!r} is open in browser instances {ids}; specify browser_id explicitly")


class CleanupPendingError(CefDocumentError):
    """Native ownership has not drained; its custody thread retains the session."""
