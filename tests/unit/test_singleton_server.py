"""Unit and integration tests for the singleton server launch lock feature."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from gmail_mcp.core.lock import SingleInstanceLock, SingleInstanceLockError


def test_singleton_lock_acquire_and_release(tmp_path: Path) -> None:
    """Test basic acquire, holds lock, and release lifecycle."""
    lock_file = tmp_path / "server.lock"
    lock1 = SingleInstanceLock(lock_path=lock_file)
    assert lock1.is_acquired is False

    # 1. Acquire first lock
    assert lock1.acquire(transport="stdio") is True
    assert lock1.is_acquired is True
    assert lock_file.exists()
    assert lock1.pid_path.exists()

    info = lock1.get_running_instance_info()
    assert info is not None
    assert "pid" in info
    assert info["transport"] == "stdio"

    # 2. Second lock instance on same path should fail
    lock2 = SingleInstanceLock(lock_path=lock_file)
    assert lock2.acquire(transport="sse") is False
    assert lock2.is_acquired is False

    # 3. Release first lock
    lock1.release()
    assert lock1.is_acquired is False

    # 4. Now second lock instance should succeed
    assert lock2.acquire(transport="sse") is True
    assert lock2.is_acquired is True
    lock2.release()
    assert lock2.is_acquired is False


def test_singleton_lock_context_manager(tmp_path: Path) -> None:
    """Test context manager usage and SingleInstanceLockError when locked."""
    lock_file = tmp_path / "server_ctx.lock"

    with SingleInstanceLock(lock_path=lock_file) as lock1:
        assert lock1.is_acquired is True
        # Trying to enter a second context manager on the same file should raise SingleInstanceLockError
        with pytest.raises(SingleInstanceLockError) as exc_info:
            with SingleInstanceLock(lock_path=lock_file):
                pass
        assert "Another Gmail MCP Server instance is already running" in str(exc_info.value)

    # After exiting block, lock is released
    assert lock1.is_acquired is False
    with SingleInstanceLock(lock_path=lock_file) as lock2:
        assert lock2.is_acquired is True


def test_singleton_subprocess_launch_rejection(tmp_path: Path) -> None:
    """Simulate two processes attempting to launch the server concurrently."""
    lock_file = tmp_path / "server_proc.lock"
    pid_file = lock_file.with_suffix(".pid")

    # Start child 1 holding the lock for 10 seconds
    child_code = f"""
import os, time
from gmail_mcp.core.lock import SingleInstanceLock
lock = SingleInstanceLock(lock_path=r'{lock_file}', pid_path=r'{pid_file}')
if lock.acquire(transport='stdio'):
    print(f'CHILD_ACQUIRED {{os.getpid()}}', flush=True)
    time.sleep(10)
    lock.release()
"""
    proc1 = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Wait for child 1 to acquire
    line = proc1.stdout.readline() if proc1.stdout else ""
    assert "CHILD_ACQUIRED" in line
    child_pid = int(line.strip().split()[1])

    # Now attempt to acquire in current process: must fail
    proc2_lock = SingleInstanceLock(lock_path=lock_file, pid_path=pid_file)
    assert proc2_lock.acquire() is False
    info = proc2_lock.get_running_instance_info()
    assert info is not None
    assert info["pid"] == child_pid

    # Terminate child 1 process tree
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc1.pid)], capture_output=True)
    else:
        proc1.terminate()
    proc1.wait(timeout=5)

    # Now proc2 must be able to acquire immediately
    assert proc2_lock.acquire() is True
    proc2_lock.release()


def test_server_main_cli_singleton_rejection(tmp_path: Path) -> None:
    """Test that running the server entrypoint twice via CLI rejects the second launch."""
    from gmail_mcp.config import WORKSPACE_ROOT

    # First acquire the default singleton lock to simulate an already running server
    lock = SingleInstanceLock()
    assert lock.acquire() is True

    try:
        # Launch main.py in a subprocess (simulating user trying to run main.py again)
        main_script = Path(WORKSPACE_ROOT) / "main.py"
        res = subprocess.run(
            [sys.executable, str(main_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        assert res.returncode == 1
        assert "[GMAIL_MCP_ERROR] Another instance of Gmail MCP Server is already running" in res.stderr
    finally:
        lock.release()
