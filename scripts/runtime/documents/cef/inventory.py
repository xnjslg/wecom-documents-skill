"""Build a fresh inventory from exact CAPI tables; never persist native pointers."""
from __future__ import annotations

from collections.abc import Iterable
import hashlib
from pathlib import Path
import struct
import time
from typing import Protocol

from .errors import CefDocumentError
from .layout import EXPECTED_SHA256
from .memory import Reader


class MemoryReader(Protocol):
    base: int
    binary_path: Path

    def read(self, address: int, size: int) -> bytes: ...
    def private_ranges(self) -> Iterable[tuple[int, int]]: ...


def scan_candidates(reader: MemoryReader, layout: dict[str, object]) -> dict[str, object]:
    """Pure scan over a reader, also usable with an offline byte fixture."""
    size = int(layout["capi_base_size"], 16)
    expected = bytearray(struct.pack("<Q", size))
    for method in layout["methods"]:
        if int(method["capi_offset"], 16) != len(expected):
            raise CefDocumentError("Browser callback table is not contiguous")
        expected.extend(struct.pack("<Q", reader.base + int(method["function_rva"], 16)))
    if len(expected) != size:
        raise CefDocumentError("Browser callback table size differs")
    rows: list[dict[str, object]] = []
    seen: set[int] = set()
    scanned = unreadable = 0
    for address, region_size in reader.private_ranges():
        for offset in range(0, region_size, 2 * 1024 * 1024):
            length = min(region_size - offset, 2 * 1024 * 1024 + size)
            try:
                data = reader.read(address + offset, length)
            except OSError:
                unreadable += 1
                continue
            scanned += length
            position = data.find(expected)
            while position >= 0:
                pointer = address + offset + position
                if pointer not in seen:
                    seen.add(pointer)
                    try:
                        vtable, unused, cpp, wrapper = struct.unpack("<4Q", reader.read(pointer - 32, 32))
                        references = struct.unpack("<i", reader.read(pointer + size, 4))[0]
                    except OSError:
                        # CEF may retire a wrapper between the two external reads.
                        unreadable += 1
                    else:
                        if vtable == reader.base + int(layout["wrapper_cpp_vtable_rva"], 16) and wrapper == pointer - 32 and cpp and references > 0:
                            rows.append({"capi": hex(pointer), "cpp_browser": hex(cpp), "refcount": references})
                position = data.find(expected, position + 8)
    return {"candidates": rows, "candidate_count": len(rows), "scanned_bytes": scanned, "unreadable_chunks": unreadable}


def capture_browsers(pid: int, layout: dict[str, object]) -> dict[str, object]:
    """Only read process memory and the actual module's bytes; caller holds query mutex."""
    started = time.monotonic()
    reader = Reader(pid)
    try:
        with reader.binary_path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != EXPECTED_SHA256:
                raise CefDocumentError("The running CEF module differs from the verified SHA256 build")
        return {"pid": pid, "libcef_base": hex(reader.base), "sha256": EXPECTED_SHA256,
                **scan_candidates(reader, layout), "seconds": round(time.monotonic() - started, 3)}
    finally:
        reader.close()
