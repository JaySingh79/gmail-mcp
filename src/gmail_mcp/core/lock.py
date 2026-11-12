"""Cross-platform single-instance process lock for the Gmail MCP Server."""

from __future__ import annotations

import atexit
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Detect platform-specific non-blocking file locking mechanisms
IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import msvcrt
else:
    import fcntl


class SingleInstanceLockError(Exception):
    """Raised when an operation requires an exclusive lock but another instance is running."""


class SingleInstanceLock:
    """
    Ensures only a single instance of the Gmail MCP Server is running at any given time.

    Uses operating system-level non-blocking file locks (`msvcrt.locking` on Windows,
    `fcntl.flock` on Unix). If the holding process exits normally, crashes, or is terminated
    via signal/kill, the OS automatically releases the file lock, ensuring no stale or
    orphaned lock prevents subsequent restarts.

    A companion sidecar `.pid` file stores operational metadata (PID, launch timestamp,
    transport mode) for diagnostic messages when subsequent launch attempts are rejected.
    """

    def __init__(
        self,
        lock_path: Path | str | None = None,
        pid_path: Path | str | None = None,
    ) -> None:
        if lock_path is None:
            from gmail_mcp.config import WORKSPACE_ROOT
            self.lock_path = Path(WORKSPACE_ROOT) / "data" / "gmail_mcp.lock"
        else:
            self.lock_path = Path(lock_path)

        if pid_path is None:
            self.pid_path = self.lock_path.with_suffix(".pid")
        else:
            self.pid_path = Path(pid_path)

        self._file_handle: Any = None
        self._acquired = False

    @property
    def is_acquired(self) -> bool:
        """Return True if this lock instance currently holds the lock."""
        return self._acquired

    def acquire(self, transport: str = "stdio") -> bool:
        """
        Attempt to acquire the singleton lock non-blockingly.

        Returns:
            True if the lock was acquired successfully.
            False if another server instance currently holds the lock.
        """
        if self._acquired:
            return True

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Open file in append/read-binary mode without truncating
            self._file_handle = open(self.lock_path, "a+b")
            self._file_handle.seek(0)

            if IS_WINDOWS:
                # Lock byte 0 without blocking. Raises OSError/PermissionError if already locked.
                msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            self._acquired = True

            # Write diagnostic sidecar PID info
            info = {
                "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "transport": transport,
                "command": sys.argv,
            }
            try:
                self.pid_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
            except Exception:
                pass

            atexit.register(self.release)
            return True

        except (OSError, IOError, PermissionError):
            if self._file_handle:
                try:
                    self._file_handle.close()
                except Exception:
                    pass
                self._file_handle = None
            self._acquired = False
            return False

    def release(self) -> None:
        """Release the lock and clean up the sidecar PID file."""
        if not self._acquired or not self._file_handle:
            return

        try:
            if IS_WINDOWS:
                try:
                    self._file_handle.seek(0)
                    msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            else:
                try:
                    fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass

            self._file_handle.close()
        except Exception:
            pass
        finally:
            self._file_handle = None
            self._acquired = False

        try:
            self.pid_path.unlink(missing_ok=True)
        except Exception:
            pass

    def get_running_instance_info(self) -> dict[str, Any] | None:
        """
        Read metadata of the currently running server instance from the sidecar PID file.
        Returns a dictionary with 'pid', 'started_at', 'transport', or None if unavailable.
        """
        if not self.pid_path.exists():
            return None
        try:
            data = json.loads(self.pid_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return None

    def is_another_instance_running(self) -> bool:
        """Check whether another instance is currently running without holding the lock."""
        if self._acquired:
            return False
        if not self.lock_path.exists():
            return False
        # Try to acquire temporarily
        temp_handle = None
        try:
            temp_handle = open(self.lock_path, "a+b")
            temp_handle.seek(0)
            if IS_WINDOWS:
                msvcrt.locking(temp_handle.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(temp_handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(temp_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(temp_handle.fileno(), fcntl.LOCK_UN)
            return False
        except (OSError, IOError, PermissionError):
            return True
        finally:
            if temp_handle:
                try:
                    temp_handle.close()
                except Exception:
                    pass

    def __enter__(self) -> SingleInstanceLock:
        if not self.acquire():
            info = self.get_running_instance_info()
            pid_str = f" (PID: {info.get('pid')})" if info and "pid" in info else ""
            raise SingleInstanceLockError(
                f"Another Gmail MCP Server instance is already running{pid_str}."
            )
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()
