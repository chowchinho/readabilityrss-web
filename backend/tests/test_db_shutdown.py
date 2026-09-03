"""A process that opens the app's database must still be able to exit.

aiosqlite's worker thread is not a daemon, so an unclosed connection blocks
`threading._shutdown()` indefinitely. This has cost real time before: one orphan sat on
the Pi for two days holding `feeds.db`, `-wal` and `-shm` open.
"""
import os
import subprocess
import sys
import textwrap

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body: str, timeout: int = 30) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        import asyncio, sys
        sys.path.insert(0, {BACKEND_DIR!r})
        from app.database import Database

        db = Database(":memory:")
        asyncio.run(db._get_db())
        {body}
    """)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR, timeout=timeout,
        capture_output=True, text=True,
    )


def test_close_sync_lets_the_process_exit():
    result = _run("db.close_sync()")
    assert result.returncode == 0, result.stderr


def test_close_sync_is_safe_when_never_connected():
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {BACKEND_DIR!r})
        from app.database import Database
        Database(":memory:").close_sync()
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR, timeout=30, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_close_sync_is_idempotent():
    result = _run("db.close_sync(); db.close_sync()")
    assert result.returncode == 0, result.stderr


def test_an_unclosed_connection_hangs_the_interpreter():
    """Negative control. If this ever stops timing out, aiosqlite has started
    daemonising its worker and close_sync is no longer load-bearing."""
    with pytest.raises(subprocess.TimeoutExpired):
        _run("pass", timeout=10)
