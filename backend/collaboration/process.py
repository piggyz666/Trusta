"""Resolve the OS-owned WSL launcher without searching the project directory."""
import ctypes
import os
from pathlib import Path
from backend.domain import Rejected


def wsl_executable():
    if os.name!='nt':raise Rejected('ANCHOR_WINDOWS_WSL_REQUIRED')
    buffer=ctypes.create_unicode_buffer(32768)
    length=ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buffer,len(buffer))
    if not 0<length<len(buffer):raise Rejected('ANCHOR_WSL_UNAVAILABLE')
    launcher=Path(buffer.value)/'System32/wsl.exe'
    if not launcher.is_file() or launcher.is_symlink():raise Rejected('ANCHOR_WSL_UNAVAILABLE')
    return str(launcher)
