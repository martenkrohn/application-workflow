"""Exclusive process lock for primary operational runs."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import TracebackType
from typing import Literal, Self


class RunAlreadyLocked(RuntimeError):
    """Raised when another primary run owns the configured lock."""


class FileRunLock:
    def __init__(self, path: Path, *, recover_stale: bool = False) -> None:
        self.path = path
        self.acquired = False
        self.recover_stale = recover_stale

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            owner = self.path.read_text(encoding="utf-8", errors="replace")
            if (
                self.recover_stale
                and self._abandoned(owner)
                and self.path.read_text(encoding="utf-8", errors="replace") == owner
            ):
                # A live owner, unreadable metadata or uncertain process access
                # must remain locked. Verify the file has not changed meanwhile.
                self.path.unlink()
                self.recover_stale = False
                return FileRunLock.__enter__(self)
            raise RunAlreadyLocked(
                f"run lock already exists at {self.path}: {owner}"
            ) from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid()}, handle)
        self.acquired = True
        return self

    @staticmethod
    def _abandoned(owner: str) -> bool:
        try:
            pid = json.loads(owner)["pid"]
        except (ValueError, TypeError, KeyError):
            return False
        if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid < 2**32:
            return False
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            )
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return (
                    ctypes.get_last_error() == 87
                )  # ERROR_INVALID_PARAMETER: no such PID.
            kernel.CloseHandle(handle)
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False
        return False
