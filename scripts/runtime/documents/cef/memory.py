"""Read-only Windows memory access to the explicitly selected WeCom CEF process."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
from pathlib import Path
import struct
import sys
from collections.abc import Iterator


class MemoryInfo(C.Structure):
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", W.DWORD), ("RegionSize", C.c_size_t),
                ("State", W.DWORD), ("Protect", W.DWORD), ("Type", W.DWORD)]


class ModuleInfo(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("th32ModuleID", W.DWORD),
                ("th32ProcessID", W.DWORD), ("GlblcntUsage", W.DWORD),
                ("ProccntUsage", W.DWORD), ("modBaseAddr", C.c_void_p),
                ("modBaseSize", W.DWORD), ("hModule", W.HMODULE),
                ("szModule", W.WCHAR * 256), ("szExePath", W.WCHAR * 260)]


@dataclass(frozen=True)
class Module:
    base: int
    size: int
    path: Path


class Reader:
    def __init__(self, pid: int):
        if sys.platform != "win32" or C.sizeof(C.c_void_p) != 8:
            raise OSError("CEF memory reads require 64-bit Windows Python")
        self.api = C.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "OpenProcess": ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            "CloseHandle": ([W.HANDLE], W.BOOL),
            "ReadProcessMemory": ([W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], W.BOOL),
            "VirtualQueryEx": ([W.HANDLE, C.c_void_p, C.POINTER(MemoryInfo), C.c_size_t], C.c_size_t),
            "CreateToolhelp32Snapshot": ([W.DWORD, W.DWORD], W.HANDLE),
            "Module32FirstW": ([W.HANDLE, C.POINTER(ModuleInfo)], W.BOOL),
            "Module32NextW": ([W.HANDLE, C.POINTER(ModuleInfo)], W.BOOL),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.api.OpenProcess(0x0410, False, pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        self.modules: dict[str, Module] = {}
        snapshot = self.api.CreateToolhelp32Snapshot(0x18, pid)
        try:
            if snapshot == C.c_void_p(-1).value:
                raise C.WinError(C.get_last_error())
            entry = ModuleInfo()
            entry.dwSize = C.sizeof(entry)
            found = self.api.Module32FirstW(snapshot, C.byref(entry))
            while found:
                self.modules[entry.szModule.lower()] = Module(entry.modBaseAddr, entry.modBaseSize, Path(entry.szExePath))
                found = self.api.Module32NextW(snapshot, C.byref(entry))
            browser = self.modules.get("wxworkweb.exe")
            cef = self.modules.get("libcef.dll")
            if browser is None or cef is None or browser.path.parent != cef.path.parent:
                raise ValueError("The selected PID is not the checked WeCom CEF browser process")
            self.base, self.binary_path = cef.base, cef.path
        except BaseException:
            self.close()
            raise
        finally:
            if snapshot != C.c_void_p(-1).value:
                self.api.CloseHandle(snapshot)

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def read(self, address: int, size: int) -> bytes:
        buffer = C.create_string_buffer(size)
        received = C.c_size_t()
        if not self.api.ReadProcessMemory(self.handle, address, buffer, size, C.byref(received)) or received.value != size:
            raise C.WinError(C.get_last_error())
        return buffer.raw

    def private_ranges(self) -> Iterator[tuple[int, int]]:
        address = 0
        info = MemoryInfo()
        while address < 0x800000000000:
            if not self.api.VirtualQueryEx(self.handle, address, C.byref(info), C.sizeof(info)):
                break
            if info.State == 0x1000 and info.Type == 0x20000 and info.Protect == 4:
                yield info.BaseAddress, info.RegionSize
            address = (info.BaseAddress or 0) + info.RegionSize

    def u64(self, address: int) -> int:
        return struct.unpack("<Q", self.read(address, 8))[0]
