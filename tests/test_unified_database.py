import multiprocessing
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import pytest
from hypershell_reach.database import ReachDatabase, SCHEMA_VERSION, timestamp


def test_explicit_creation_version_no_overwrite_and_missing_fails(tmp_path):
    db = ReachDatabase.create(tmp_path / 'reach.sqlite3')
    db.validate()
    with db.connection() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert conn.execute('PRAGMA synchronous').fetchone()[0] == 2
    with pytest.raises(FileExistsError):
        ReachDatabase.create(db.path)
    with pytest.raises(sqlite3.OperationalError):
        ReachDatabase(tmp_path / 'missing').validate()
    assert not (tmp_path / 'missing').exists()


def test_nested_transactions_reuse_connection_and_roll_back_all(tmp_path):
    db = ReachDatabase.create(tmp_path / 'reach.sqlite3')
    other = ReachDatabase(db.path)
    with pytest.raises(ValueError):
        with db.connection(write=True) as first:
            first.execute("INSERT INTO metadata VALUES ('one','1')")
            with other.connection(write=True) as second:
                assert first is second
                second.execute("INSERT INTO metadata VALUES ('two','2')")
            raise ValueError('abort')
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) FROM metadata').fetchone()[0] == 0
        with pytest.raises(RuntimeError, match='upgrade'):
            with other.connection(write=True):
                pass


def test_read_only_symlink_and_unknown_schema_fail_closed(tmp_path):
    db = ReachDatabase.create(tmp_path / 'reach.sqlite3')
    with pytest.raises(RuntimeError, match='read-only'):
        with ReachDatabase(db.path, read_only=True).connection(write=True):
            pass
    with ReachDatabase(db.path, read_only=True).connection() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO metadata VALUES ('x','x')")
    link = tmp_path / 'link'
    link.symlink_to(db.path)
    with pytest.raises(RuntimeError, match='symlink'):
        ReachDatabase(link).validate()
    conn = sqlite3.connect(db.path)
    conn.execute('PRAGMA user_version=999')
    conn.close()
    with pytest.raises(RuntimeError, match='version'):
        db.validate()


def test_concurrent_writers_and_readers(tmp_path):
    db = ReachDatabase.create(tmp_path / 'reach.sqlite3')
    with db.connection(write=True) as conn:
        conn.execute("INSERT INTO metadata VALUES ('counter','0')")
    def increment(_):
        for _ in range(10):
            with ReachDatabase(db.path).connection(write=True) as conn:
                value = int(conn.execute("SELECT value FROM metadata WHERE key='counter'").fetchone()[0])
                conn.execute("UPDATE metadata SET value=? WHERE key='counter'", (str(value + 1),))
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(increment, range(5)))
    with db.connection() as conn:
        assert conn.execute("SELECT value FROM metadata WHERE key='counter'").fetchone()[0] == '50'
    def read():
        with ReachDatabase(db.path, read_only=True).connection() as conn:
            return conn.execute("SELECT value FROM metadata WHERE key='counter'").fetchone()[0]
    with db.connection(write=True) as conn:
        conn.execute("UPDATE metadata SET value='51' WHERE key='counter'")
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(read).result(timeout=2) == '50'
    assert read() == '51'


def _uncommitted_writer(path, ready):
    with ReachDatabase(path).connection(write=True) as conn:
        conn.execute("INSERT INTO metadata VALUES ('uncommitted','lost')")
        ready.set()
        time.sleep(60)


def test_killed_writer_recovers_wal_without_losing_committed_state(tmp_path):
    db = ReachDatabase.create(tmp_path / 'reach.sqlite3')
    with db.connection(write=True) as conn:
        conn.execute("INSERT INTO metadata VALUES ('committed','preserved')")
    ctx = multiprocessing.get_context('spawn')
    ready = ctx.Event()
    process = ctx.Process(target=_uncommitted_writer, args=(str(db.path), ready))
    process.start()
    try:
        assert ready.wait(10)
        process.kill()
        process.join(10)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
            process.join()
    with db.connection() as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM metadata')] == [('committed','preserved')]
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_busy_timeout_is_bounded_and_never_silently_drops_write(tmp_path):
    db = ReachDatabase.create(tmp_path / 'reach.sqlite3')
    holder = sqlite3.connect(db.path)
    holder.execute('BEGIN IMMEDIATE')
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            with db.connection(write=True):
                pass
    finally:
        holder.rollback()
        holder.close()
    assert 4.5 <= time.monotonic() - started < 10
    with db.connection(write=True) as conn:
        conn.execute("INSERT INTO metadata VALUES ('after','works')")


def test_timestamp_requires_explicit_timezone():
    assert timestamp('2026-01-01T00:00:00Z') == timestamp('2026-01-01T01:00:00+01:00')
    with pytest.raises(ValueError, match='timezone'):
        timestamp('2026-01-01T00:00:00')


def test_copied_context_after_parent_close_opens_independent_thread_connection(tmp_path):
    from contextvars import copy_context
    db = ReachDatabase.create(tmp_path / 'reach.sqlite3')
    with db.connection(write=True) as parent:
        parent.execute("INSERT INTO metadata VALUES ('parent','committed')")
        copied = copy_context()
    def child():
        with db.connection(write=True) as conn:
            assert conn is not parent
            conn.execute("INSERT INTO metadata VALUES ('child','committed')")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(copied.run, child).result(timeout=10)
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) FROM metadata').fetchone()[0] == 2


@pytest.mark.asyncio
async def test_child_async_task_context_after_parent_close_is_independent(tmp_path):
    import asyncio
    db = ReachDatabase.create(tmp_path / 'reach.sqlite3')
    release = asyncio.Event()
    async def child():
        await release.wait()
        with db.connection(write=True) as conn:
            assert conn is not parent
            conn.execute("INSERT INTO metadata VALUES ('child','committed')")
    with db.connection(write=True) as parent:
        task = asyncio.create_task(child())
        parent.execute("INSERT INTO metadata VALUES ('parent','committed')")
    release.set()
    await task
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) FROM metadata').fetchone()[0] == 2
