"""Concurrent company-context writes must produce controlled conflicts.

Threads are used so each writer gets its own connection from the real pool
(SQLite opens its own file handle), exercising the database-level guarantee
rather than an in-process lock. Every wait is bounded so a regression fails
instead of hanging CI.
"""

import copy
import threading
from uuid import uuid4

import pytest

from app.core.company_context import (
    CompanyContextConflictError,
    get_company_context,
    get_company_context_record,
    save_company_context_record,
)
from app.storage.repository import RuntimeRepository

BARRIER_TIMEOUT_SECONDS = 10.0
JOIN_TIMEOUT_SECONDS = 30.0

repository = RuntimeRepository()


@pytest.fixture
def base_context() -> dict:
    return copy.deepcopy(get_company_context())


def _context(base: dict, company_name: str) -> dict:
    payload = copy.deepcopy(base)
    payload["company_name"] = company_name
    return payload


def _run_concurrently(worker, count: int) -> list:
    """Start `count` threads that meet at a barrier, then all write at once."""
    barrier = threading.Barrier(count, timeout=BARRIER_TIMEOUT_SECONDS)
    results: list = [None] * count
    threads: list[threading.Thread] = []

    def target(index: int) -> None:
        try:
            barrier.wait()
            results[index] = ("ok", worker(index))
        except CompanyContextConflictError as exc:
            results[index] = ("conflict", exc)
        except Exception as exc:
            results[index] = ("error", exc)

    for index in range(count):
        thread = threading.Thread(target=target, args=(index,), daemon=True)
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT_SECONDS)
        assert not thread.is_alive(), "concurrent writer did not finish within the timeout"

    return results


def test_concurrent_creation_produces_exactly_one_winner(base_context) -> None:
    slug = f"race-create-{uuid4().hex[:10]}"
    writers = 6

    def worker(index: int):
        return save_company_context_record(
            _context(base_context, f"Writer {index}"),
            company_slug=slug,
            expected_revision=0,
            updated_by=f"writer-{index}",
        )

    results = _run_concurrently(worker, writers)

    outcomes = [status for status, _payload in results]
    errors = [payload for status, payload in results if status == "error"]

    assert errors == [], f"unexpected database exceptions: {errors}"
    assert outcomes.count("ok") == 1, f"exactly one writer must create revision 1: {outcomes}"
    assert outcomes.count("conflict") == writers - 1

    record = get_company_context_record(company_slug=slug)
    assert record["revision"] == 1
    # The stored payload belongs to the single winner, not a blend of writers.
    assert record["data"]["company_name"].startswith("Writer ")
    assert record["updated_by"] == f"writer-{record['data']['company_name'].split()[-1]}"


def test_concurrent_creation_without_a_revision_token_never_raises(base_context) -> None:
    """No revision token means no precondition, so a later writer may update.

    The invariant is still strict: creation happens once, no writer sees a raw
    unique-violation, and every success advances the revision exactly once (no
    lost updates, no duplicate rows).
    """
    slug = f"race-create-none-{uuid4().hex[:10]}"
    writers = 4

    def worker(index: int):
        return save_company_context_record(
            _context(base_context, f"NoToken {index}"),
            company_slug=slug,
            updated_by=f"notoken-{index}",
        )

    results = _run_concurrently(worker, writers)
    outcomes = [status for status, _payload in results]

    assert [payload for status, payload in results if status == "error"] == []
    assert outcomes.count("ok") >= 1

    record = get_company_context_record(company_slug=slug)
    assert record["revision"] == outcomes.count("ok")
    assert record["data"]["company_name"].startswith("NoToken ")

    # Exactly one row exists for the slug: creation was not duplicated.
    stored = [
        row
        for row in repository.get_company_context_records()
        if row["company_slug"] == slug
    ]
    assert len(stored) == 1


def test_a_losing_creator_can_retry_and_update(base_context) -> None:
    slug = f"race-retry-{uuid4().hex[:10]}"
    save_company_context_record(_context(base_context, "First"), company_slug=slug, expected_revision=0)

    with pytest.raises(CompanyContextConflictError):
        save_company_context_record(_context(base_context, "Second"), company_slug=slug, expected_revision=0)

    retried = save_company_context_record(_context(base_context, "Second"), company_slug=slug)

    assert retried["revision"] == 2
    assert retried["data"]["company_name"] == "Second"


def test_concurrent_updates_produce_one_winner_per_revision(base_context) -> None:
    slug = f"race-update-{uuid4().hex[:10]}"
    created = save_company_context_record(
        _context(base_context, "Base"),
        company_slug=slug,
        expected_revision=0,
    )
    assert created["revision"] == 1

    def worker(index: int):
        return save_company_context_record(
            _context(base_context, f"Update {index}"),
            company_slug=slug,
            expected_revision=1,
            updated_by=f"updater-{index}",
        )

    results = _run_concurrently(worker, 5)
    outcomes = [status for status, _payload in results]

    assert [payload for status, payload in results if status == "error"] == []
    assert outcomes.count("ok") == 1, f"only one writer may advance revision 1: {outcomes}"

    record = get_company_context_record(company_slug=slug)
    assert record["revision"] == 2
    assert record["data"]["company_name"].startswith("Update ")


def test_final_revision_and_payload_stay_consistent(base_context) -> None:
    """Sequential writers must leave revision and payload in lockstep."""
    slug = f"race-consistency-{uuid4().hex[:10]}"
    save_company_context_record(_context(base_context, "v1"), company_slug=slug, expected_revision=0)

    for expected in range(1, 5):
        record = save_company_context_record(
            _context(base_context, f"v{expected + 1}"),
            company_slug=slug,
            expected_revision=expected,
        )
        assert record["revision"] == expected + 1
        assert record["data"]["company_name"] == f"v{expected + 1}"

    final = get_company_context_record(company_slug=slug)
    assert final["revision"] == 5
    assert final["data"]["company_name"] == "v5"


def test_no_partial_write_is_committed_on_conflict(base_context) -> None:
    slug = f"race-partial-{uuid4().hex[:10]}"
    save_company_context_record(_context(base_context, "Winner"), company_slug=slug, expected_revision=0)

    with pytest.raises(CompanyContextConflictError):
        save_company_context_record(
            _context(base_context, "Loser"),
            company_slug=slug,
            expected_revision=0,
            updated_by="loser",
        )

    record = get_company_context_record(company_slug=slug)
    assert record["data"]["company_name"] == "Winner"
    assert record["revision"] == 1
    assert record["updated_by"] != "loser"


def test_concurrent_writes_leave_a_usable_connection_pool(base_context) -> None:
    """A burst of conflicting writers must not strand pooled connections."""
    slug = f"race-pool-{uuid4().hex[:10]}"

    def worker(index: int):
        return save_company_context_record(
            _context(base_context, f"Pool {index}"),
            company_slug=slug,
            expected_revision=0,
        )

    _run_concurrently(worker, 8)

    assert isinstance(repository.get_companies(), list)
    assert get_company_context_record(company_slug=slug)["revision"] == 1
