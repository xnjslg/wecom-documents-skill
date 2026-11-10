"""Document-bound CEF operations. Callers serialize access with Invoke-WeComQuery."""
from .client import CefDocumentClient, list_document_instances
from .errors import AmbiguousDocumentError, CefDocumentError, CleanupPendingError, DocumentInstance, DocumentNotOpenError

__all__ = ["CefDocumentClient", "list_document_instances", "CefDocumentError", "CleanupPendingError",
           "AmbiguousDocumentError", "DocumentNotOpenError", "DocumentInstance"]
