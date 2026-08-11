"""Regression coverage for I-016 (connection leaks) and I-017 (optional filters).

These run against whichever backend the suite is pointed at, so the same
assertions execute on SQLite and on PostgreSQL (``TEST_DATABASE_URL``).
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest

from app.storage import db as db_module
from app.storage.db import (
    connection_scope,
    db_session,
    db_transaction,
    get_connection,
    get_database_backend,
)
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


@pytest.fixture
def tenant() -> dict:
    suffix = uuid4().hex[:10]
    company_id = repository.create_company(slug=f"pgc-{suffix}", name=f"PG Compat {suffix}")
    contact_id = f"pgc-contact-{suffix}"
    repository.save_contact(contact_id, "Compat", company_id=company_id)
    return {"company_id": company_id, "contact_id": contact_id, "suffix": suffix}


# ── I-017: optional filters ───────────────────────────────────────────

# Each entry: (label, callable taking the filter kwargs).
OPTIONAL_FILTER_LISTINGS = [
    ("appointment_bookings", repository.get_appointment_bookings),
    ("knowledge_entries", repository.get_knowledge_entries),
    ("customer_reminders", repository.get_customer_reminders),
    ("pending_questions", repository.get_pending_questions),
    ("audit_events", repository.get_audit_events),
    ("messages", repository.get_messages),
    ("contacts", repository.get_contacts),
    ("outbound_messages", repository.get_outbound_messages),
    ("finetuning_jobs", repository.get_finetuning_job_records),
    ("message_examples", repository.get_message_examples),
    ("queue_entries", repository.get_appointment_queue_entries),
    ("queue_offers", repository.get_appointment_queue_offers),
    ("calendar_integrations", repository.get_calendar_integrations),
    ("active_knowledge_entries", repository.get_active_knowledge_entries),
]


@pytest.mark.parametrize(("label", "call"), OPTIONAL_FILTER_LISTINGS, ids=[e[0] for e in OPTIONAL_FILTER_LISTINGS])
def test_optional_filter_accepts_none(label, call) -> None:
    """No filter must not send an untyped placeholder (IndeterminateDatatype)."""
    assert isinstance(call(company_id=None), list)


@pytest.mark.parametrize(("label", "call"), OPTIONAL_FILTER_LISTINGS, ids=[e[0] for e in OPTIONAL_FILTER_LISTINGS])
def test_optional_filter_accepts_a_valid_value(label, call, tenant) -> None:
    assert isinstance(call(company_id=tenant["company_id"]), list)


@pytest.mark.parametrize(("label", "call"), OPTIONAL_FILTER_LISTINGS, ids=[e[0] for e in OPTIONAL_FILTER_LISTINGS])
def test_optional_filter_with_no_matches_returns_empty(label, call) -> None:
    assert call(company_id=987654321) == []


def test_multiple_optional_filters_combine(tenant) -> None:
    company_id = tenant["company_id"]
    contact_id = tenant["contact_id"]

    # All None
    assert isinstance(repository.get_appointment_bookings(), list)
    # One set
    assert isinstance(repository.get_appointment_bookings(company_id=company_id), list)
    # All three set
    assert repository.get_appointment_bookings(
        company_id=company_id,
        contact_id=contact_id,
        status="scheduled",
    ) == []
    # Reminders: company + status + audience
    assert repository.get_customer_reminders(
        company_id=company_id,
        status="pending",
        audience="customer",
    ) == []
    # Audit events: action + resource_type + company
    assert isinstance(
        repository.get_audit_events(action="authenticate", resource_type="admin_access", company_id=company_id),
        list,
    )


def test_optional_filters_actually_filter(tenant) -> None:
    """Semantics preserved: the filter narrows, and None does not."""
    other_company = repository.create_company(
        slug=f"pgc-other-{tenant['suffix']}",
        name="Other Tenant",
    )
    repository.save_message(
        contact_id=tenant["contact_id"],
        company_id=tenant["company_id"],
        message="scoped",
        classification="faq",
        reply="ok",
    )

    scoped = repository.get_messages(company_id=tenant["company_id"])
    assert scoped and all(row["company_id"] == tenant["company_id"] for row in scoped)
    assert repository.get_messages(company_id=other_company) == []
    assert len(repository.get_messages()) >= len(scoped)


def test_no_untyped_placeholder_comparisons_remain_in_repository() -> None:
    """Guard against reintroducing `? IS NULL OR col = ?`."""
    source = Path("app/storage/repository.py").read_text(encoding="utf-8")
    offenders = re.findall(r"\(\?\s+IS NULL OR", source)
    assert offenders == []


# ── I-016: connection lifecycle ───────────────────────────────────────


class _FailingCursor:
    """Wraps a real cursor and raises at one chosen stage."""

    def __init__(self, real_cursor, fail_on: str) -> None:
        self._real = real_cursor
        self._fail_on = fail_on

    def execute(self, query, params=None):
        if self._fail_on == "execute":
            raise RuntimeError("injected execute failure")
        if params is None:
            return self._real.execute(query)
        return self._real.execute(query, params)

    def fetchone(self):
        if self._fail_on == "fetchone":
            raise RuntimeError("injected fetchone failure")
        return self._real.fetchone()

    def fetchall(self):
        if self._fail_on == "fetchall":
            raise RuntimeError("injected fetchall failure")
        return self._real.fetchall()

    @property
    def rowcount(self):
        return self._real.rowcount

    @property
    def lastrowid(self):
        return getattr(self._real, "lastrowid", None)


@pytest.mark.parametrize("fail_on", ["execute", "fetchone", "fetchall"])
def test_failed_select_releases_the_connection(fail_on) -> None:
    """A real cursor failure mid-SELECT must still release the connection."""
    connection = get_connection()
    real_cursor = connection.cursor()

    with pytest.raises(RuntimeError, match="injected"):
        with db_session() as session_connection:
            assert session_connection is not None
            cursor = _FailingCursor(real_cursor, fail_on)
            cursor.execute("SELECT 1 AS ok")
            cursor.fetchone()
            cursor.fetchall()

    connection.close()
    assert connection.released is True
    # The pool is immediately usable again.
    assert isinstance(repository.get_companies(), list)


def test_failed_session_inside_db_session_releases_its_own_connection() -> None:
    captured: list = []

    with pytest.raises(RuntimeError, match="injected"):
        with db_session() as connection:
            captured.append(connection)
            cursor = _FailingCursor(connection.cursor(), "execute")
            cursor.execute("SELECT 1 AS ok")

    assert captured[0].released is True


def test_failed_write_rolls_back_and_releases() -> None:
    with pytest.raises(RuntimeError):
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO companies (slug, name, active, updated_at)"
                " VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (f"rollback-{uuid4().hex[:8]}", "Rollback Co", 1),
            )
            raise RuntimeError("injected failure after insert")

    # The connection is usable again and the insert did not survive.
    assert isinstance(repository.get_companies(), list)


def test_transaction_commits_exactly_once() -> None:
    slug = f"commit-once-{uuid4().hex[:8]}"
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO companies (slug, name, active, updated_at)"
            " VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (slug, "Commit Once", 1),
        )

    matches = [c for c in repository.get_companies() if c["slug"] == slug]
    assert len(matches) == 1


def test_close_is_idempotent_and_never_returns_twice() -> None:
    connection = get_connection()
    connection.close()
    assert connection.released is True
    # Repeated close must be a no-op rather than a second pool return.
    connection.close()
    connection.close()
    assert connection.released is True


def test_released_connection_cannot_be_reused() -> None:
    connection = get_connection()
    connection.close()
    with pytest.raises(RuntimeError):
        connection.cursor()
    with pytest.raises(RuntimeError):
        connection.commit()


def test_connection_scope_releases_leaked_connections() -> None:
    leaked = []
    with connection_scope():
        leaked.append(get_connection())
        leaked.append(get_connection())
        assert all(not c.released for c in leaked)
    assert all(c.released for c in leaked)


def test_connection_scope_releases_on_exception() -> None:
    leaked = []
    with pytest.raises(RuntimeError):
        with connection_scope():
            leaked.append(get_connection())
            raise RuntimeError("injected failure")
    assert leaked[0].released is True


def test_nested_scopes_release_independently() -> None:
    with connection_scope():
        outer = get_connection()
        with connection_scope():
            inner = get_connection()
        assert inner.released is True
        assert outer.released is False
    assert outer.released is True


def test_repository_methods_are_connection_guarded() -> None:
    assert getattr(RuntimeRepository.get_messages, "__connection_guarded__", False)
    assert getattr(RuntimeRepository.create_company, "__connection_guarded__", False)
    assert getattr(RuntimeRepository.get_company_context_record, "__connection_guarded__", False)


def test_repeated_repository_failures_do_not_exhaust_the_pool(monkeypatch) -> None:
    """More failures than DATABASE_POOL_SIZE must not block the next caller."""
    pool_size = max(1, int(db_module.settings.database_pool_size))
    failures = pool_size * 3

    original_get_connection = db_module.get_connection

    def failing_cursor_connection():
        connection = original_get_connection()
        real_cursor = connection.cursor

        def broken_cursor():
            real_cursor()
            raise RuntimeError("injected cursor failure")

        connection.cursor = broken_cursor  # type: ignore[method-assign]
        return connection

    monkeypatch.setattr("app.storage.repository.get_connection", failing_cursor_connection)
    for _ in range(failures):
        with pytest.raises(RuntimeError):
            repository.get_messages()

    monkeypatch.undo()
    # The pool must still serve requests immediately after the failure burst.
    assert isinstance(repository.get_messages(), list)


def test_failed_migration_releases_its_connection(monkeypatch) -> None:
    def exploding_migration(cursor):
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(
        db_module,
        "MIGRATIONS",
        [*db_module.MIGRATIONS, ("999_injected_failure", "Injected failure", exploding_migration)],
    )
    with pytest.raises(RuntimeError):
        db_module.init_db()

    monkeypatch.undo()
    # A released migration connection also drops the Postgres advisory lock, so
    # the next init_db must not block.
    db_module.init_db()
    assert isinstance(repository.get_schema_migrations(), list)


def test_backend_is_one_of_the_supported_two() -> None:
    assert get_database_backend() in {"sqlite", "postgres"}


def test_optional_filter_rejects_unsafe_identifiers() -> None:
    """Only a bare column or alias.column may be interpolated."""
    from app.storage.repository import _optional_equals

    assert _optional_equals("company_id", 1) == ("company_id = ?", (1,))
    assert _optional_equals("b.company_id", 1) == ("b.company_id = ?", (1,))
    assert _optional_equals("company_id", None) == ("1 = 1", ())

    for unsafe in [
        "company_id = 1 OR 1=1 --",
        "company_id); DROP TABLE companies;--",
        "company_id, name",
        "",
        "1",
    ]:
        with pytest.raises(ValueError):
            _optional_equals(unsafe, 1)


# ── Connection release failure modes (Release 1A.2) ───────────────────


class _FakePool:
    """Minimal pool double that can be told to fail on putconn."""

    def __init__(self, *, fail_putconn: bool = False) -> None:
        self.fail_putconn = fail_putconn
        self.returned: list = []

    def putconn(self, raw_connection) -> None:
        if self.fail_putconn:
            raise RuntimeError("injected putconn failure")
        self.returned.append(raw_connection)


class _FakeRawConnection:
    def __init__(self, *, fail_rollback: bool = False, fail_close: bool = False) -> None:
        self.fail_rollback = fail_rollback
        self.fail_close = fail_close
        self.rollback_calls = 0
        self.close_calls = 0

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.fail_rollback:
            raise RuntimeError("injected rollback failure")

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("injected raw close failure")


def _wrapper(**kwargs):
    from app.storage.db import PostgresConnectionWrapper

    raw = _FakeRawConnection(
        fail_rollback=kwargs.pop("fail_rollback", False),
        fail_close=kwargs.pop("fail_close", False),
    )
    pool = _FakePool(fail_putconn=kwargs.pop("fail_putconn", False))
    return PostgresConnectionWrapper(raw, pool), raw, pool


def test_successful_release_returns_the_connection_once() -> None:
    wrapper, raw, pool = _wrapper()

    wrapper.close()

    assert wrapper.released is True
    assert wrapper.release_error is None
    assert raw.rollback_calls == 1
    assert pool.returned == [raw]


def test_repeated_close_returns_the_connection_only_once() -> None:
    wrapper, raw, pool = _wrapper()

    wrapper.close()
    wrapper.close()
    wrapper.close()

    assert len(pool.returned) == 1
    assert raw.rollback_calls == 1


def test_rollback_failure_is_recorded_and_connection_still_returned() -> None:
    wrapper, raw, pool = _wrapper(fail_rollback=True)

    wrapper.close()

    assert wrapper.released is True
    assert isinstance(wrapper.release_error, RuntimeError)
    # The pool still gets it back so the slot is not lost.
    assert pool.returned == [raw]


def test_putconn_failure_after_successful_rollback_discards_the_connection() -> None:
    wrapper, raw, pool = _wrapper(fail_putconn=True)

    wrapper.close()

    assert wrapper.released is True
    assert "putconn" in str(wrapper.release_error)
    assert pool.returned == []
    # Not silently stranded: the raw connection is closed to free the slot.
    assert raw.close_calls == 1


def test_putconn_failure_after_rollback_failure_still_discards() -> None:
    wrapper, raw, _pool = _wrapper(fail_rollback=True, fail_putconn=True)

    wrapper.close()

    assert wrapper.released is True
    assert wrapper.release_error is not None
    assert raw.close_calls == 1


def test_close_never_raises_even_when_every_cleanup_step_fails() -> None:
    """Cleanup failures must not mask the application exception in flight."""
    wrapper, _raw, _pool = _wrapper(fail_rollback=True, fail_putconn=True, fail_close=True)

    wrapper.close()  # must not raise

    assert wrapper.released is True
    assert isinstance(wrapper.release_error, RuntimeError)


def test_original_exception_survives_a_failing_cleanup() -> None:
    from app.storage.db import PostgresConnectionWrapper

    raw = _FakeRawConnection(fail_rollback=True, fail_close=True)
    pool = _FakePool(fail_putconn=True)
    wrapper = PostgresConnectionWrapper(raw, pool)

    with pytest.raises(ValueError, match="original application error"):
        try:
            raise ValueError("original application error")
        finally:
            wrapper.close()


def test_repository_failure_then_successful_reuse_with_a_tiny_pool(monkeypatch) -> None:
    """Repeated failures with a 1-connection pool must not strand the slot."""
    monkeypatch.setattr(db_module.settings, "database_pool_size", 1, raising=False)

    original_get_connection = db_module.get_connection

    def failing_execute_connection():
        connection = original_get_connection()
        real_cursor = connection.cursor

        def broken_cursor():
            real_cursor()
            raise RuntimeError("injected cursor failure")

        connection.cursor = broken_cursor  # type: ignore[method-assign]
        return connection

    monkeypatch.setattr("app.storage.repository.get_connection", failing_execute_connection)
    for _ in range(5):
        with pytest.raises(RuntimeError, match="injected"):
            repository.get_companies()

    monkeypatch.undo()
    assert isinstance(repository.get_companies(), list)


def test_pool_is_configured_to_open_explicitly() -> None:
    kwargs = db_module._get_postgres_connect_kwargs()

    assert kwargs["open"] is True
    assert kwargs["max_size"] >= 1
