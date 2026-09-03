"""aiosqlite worker threads must not outlive the connections that own them.

aiosqlite.Connection subclasses threading.Thread without daemon=True and defines no
__del__ anywhere in its MRO, so a connection dropped without stopping it leaves a live
non-daemon thread. threading._shutdown() then joins it forever and the process never
exits — which is what hung the test suite on both Windows and Linux.

These tests compare thread *identity* rather than counts. close_all_sync() is global and
also stops connections opened by earlier tests, so a count captured mid-session is not a
stable reference point.
"""
import asyncio
import gc
import threading
import time

from app.database import Database, close_all_sync


def _survivors(before, timeout=5.0):
    """Threads that appeared during the test and are still alive."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        new_alive = [t for t in threading.enumerate() if t not in before and t.is_alive()]
        if not new_alive:
            return []
        time.sleep(0.05)
    return [t for t in threading.enumerate() if t not in before and t.is_alive()]


async def _touch(db):
    conn = await db._get_db()
    await conn.execute("SELECT 1")


def test_close_sync_stops_the_worker(tmp_path):
    before = set(threading.enumerate())
    db = Database(db_path=str(tmp_path / "one.db"))

    asyncio.run(_touch(db))
    assert [t for t in threading.enumerate() if t not in before], "expected a worker thread"

    db.close_sync()
    assert _survivors(before) == []


def test_no_worker_leaks_across_event_loops(tmp_path):
    """Each asyncio.run() is a fresh loop, exactly as pytest-asyncio gives each test.

    _get_lock() drops self._db when the loop changes. If it drops a live connection
    without stopping it, every loop change leaks one non-daemon thread.
    """
    before = set(threading.enumerate())
    db = Database(db_path=str(tmp_path / "many.db"))

    for _ in range(3):
        asyncio.run(_touch(db))

    db.close_sync()
    assert _survivors(before) == [], (
        "workers from superseded event loops were left running; the process would "
        "hang at interpreter exit"
    )


def test_worker_is_reachable_after_its_database_is_collected(tmp_path):
    """A Database created inside a test goes out of scope when that test ends.

    Database._instances is a WeakSet, so the collected wrapper disappears from it — but
    the Connection's worker keeps running, because Connection has no __del__ and stays
    reachable from threading._active. close_all_sync() must still be able to stop it.
    """
    before = set(threading.enumerate())

    def make_and_drop():
        db = Database(db_path=str(tmp_path / "orphan.db"))
        asyncio.run(_touch(db))
        # db falls out of scope here, exactly as it does at the end of a test.

    make_and_drop()
    gc.collect()

    close_all_sync()
    assert _survivors(before) == [], (
        "a worker whose Database wrapper was garbage collected could not be stopped"
    )
