"""Load the compact layouts for the one statically and live-checked CEF build."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from .errors import CefDocumentError


ROOT = Path(__file__).resolve().parent
EXPECTED_SHA256 = "305967f575bc139b13975d080f620103993b65a6bbef9672c07d3f036743c59f"


def document_url(docid: str) -> str:
    if not isinstance(docid, str) or not docid or any(
        character in "/?#\\" or character.isspace() or ord(character) < 32 for character in docid
    ):
        raise ValueError("docid must be a nonempty document ID, without URL separators or whitespace")
    return "https://doc.weixin.qq.com/doc/" + quote(docid, safe="-._~")


def load_layout() -> dict[str, object]:
    browser = json.loads((ROOT / "browser-wrapper-layout.json").read_text(encoding="utf-8"))
    host = json.loads((ROOT / "host-wrapper-layout.json").read_text(encoding="utf-8"))
    for layout, size, count in ((browser, 208, 25), (host, 584, 72)):
        if layout["sha256"] != EXPECTED_SHA256 or int(layout["capi_base_size"], 16) != size:
            raise CefDocumentError("Bundled CEF layout does not match the verified build")
        methods = layout["methods"]
        if len(methods) != count or [int(item["capi_offset"], 16) for item in methods] != list(range(8, size, 8)):
            raise CefDocumentError("Bundled CEF callback table is incomplete")
    browser["host"] = host
    return browser
