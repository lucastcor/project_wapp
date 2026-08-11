"""Concurrency regression coverage for database bootstrap migrations."""

import threading
from concurrent.futures import ThreadPoolExecutor

from app.core.config import settings
from app.storage import db as db_module


def test_sqlite_migrations_are_serialized_before_reading_the_ledger(
    tmp_path,
    monkeypatch,
) -> None:
    fresh_db = tmp_path / "concurrent-bootstrap.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{fresh_db}")

    original_get_applied = db_module._get_applied_migration_versions
    ledger_reads = 0
    ledger_reads_lock = threading.Lock()
    both_read_deferred_ledger = threading.Event()

    def synchronized_get_applied(cursor):
        nonlocal ledger_reads
        versions = original_get_applied(cursor)
        with ledger_reads_lock:
            ledger_reads += 1
            if ledger_reads == 2:
                both_read_deferred_ledger.set()
        # Without BEGIN IMMEDIATE both initializers reach this point with the
        # same empty ledger, making the migration-version insert race
        # deterministic. With the fix, the first initializer holds the write
        # lock and completes after this short timeout; only then can the second
        # read the committed ledger.
        both_read_deferred_ledger.wait(timeout=0.25)
        return versions

    monkeypatch.setattr(
        db_module,
        "_get_applied_migration_versions",
        synchronized_get_applied,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(db_module.init_db) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)

    with db_module.db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT version) AS distinct_total "
            "FROM schema_migrations"
        )
        ledger = cursor.fetchone()

    assert ledger_reads == 2
    assert ledger["total"] == len(db_module.MIGRATIONS)
    assert ledger["distinct_total"] == len(db_module.MIGRATIONS)
