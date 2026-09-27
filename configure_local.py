"""Resolve Windows app filesystem redirection so desktop launch uses the same data."""
import ctypes
import json
import os
import sys
from pathlib import Path
from ctypes import wintypes


def physical_path(path: Path) -> Path:
    if sys.platform != "win32":
        return path.resolve()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        if not kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(buffer.value.removeprefix("\\\\?\\"))
    finally:
        kernel.CloseHandle(handle)


if __name__ == "__main__":
    project = Path(__file__).resolve().parent
    root = Path.home() / "MeetingScribeData"
    root.mkdir(parents=True, exist_ok=True)
    root = physical_path(root)
    runtime = physical_path(Path(sys.executable))
    (project / "local_paths.json").write_text(json.dumps({"data_root": str(root), "python": str(runtime)}, indent=2), encoding="utf-8")
    (project / "runtime-path.txt").write_text(str(runtime.with_name("pythonw.exe")), encoding="utf-8")
    print(json.dumps({"data_root": str(root), "python": str(runtime)}))
