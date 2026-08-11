"""Atomic outbound claims, retries, dead letters and worker leases."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from app.core.config import settings
from app.services import outbound_dispatch_service
from app.services.outbound_dispatch_service import (
    OutboundDispatchError,
    dispatch_outbound_message,
)
from app.storage.db import db_transaction
from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()


def _outbound_fixture() -> int:
    contact_id = f"outbound-{uuid4().hex[:12]}"
    repository.save_contact(contact_id, "Outbound Reliability")
    return repository.create_outbound_message(
        contact_id=contact_id,
        message="reliable delivery",
        source="reliability-test",
    )


def test_two_replicas_only_send_one_provider_request(monkeypatch) -> None:
    outbound_id = _outbound_fixture()
    provider_entered = Event()
    provider_can_finish = Event()
    provider_calls: list[int] = []

    def controlled_provider(channel, outbound_message):
        provider_calls.append(int(outbound_message["id"]))
        provider_entered.set()
        assert provider_can_finish.wait(timeout=5)
        return {
            "provider_message_id": "provider-once",
            "provider_response": {"accepted": True},
        }

    monkeypatch.setattr(
        outbound_dispatch_service,
        "_send_via_provider",
        controlled_provider,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(dispatch_outbound_message, outbound_id, "generic")
        assert provider_entered.wait(timeout=5)
        second = dispatch_outbound_message(outbound_id, "generic")
        provider_can_finish.set()
        first_result = first.result(timeout=5)

    assert provider_calls == [outbound_id]
    assert second["delivery_status"] == "already_processing"
    assert first_result["delivery_status"] == "sent"
    stored = repository.get_outbound_message(outbound_id)
    assert stored["status"] == "sent"
    assert stored["provider_message_id"] == "provider-once"
    assert stored["attempt_count"] == 1


def test_provider_failures_backoff_then_dead_letter(monkeypatch) -> None:
    outbound_id = _outbound_fixture()
    monkeypatch.setattr(settings, "outbound_max_attempts", 2)
    monkeypatch.setattr(settings, "outbound_retry_base_seconds", 1)
    monkeypatch.setattr(settings, "outbound_retry_max_seconds", 1)

    def broken_provider(channel, outbound_message):
        raise OutboundDispatchError("provider unavailable")

    monkeypatch.setattr(
        outbound_dispatch_service,
        "_send_via_provider",
        broken_provider,
    )

    with pytest.raises(OutboundDispatchError):
        dispatch_outbound_message(outbound_id, "generic")
    first_failure = repository.get_outbound_message(outbound_id)
    assert first_failure["status"] == "retry"
    assert first_failure["attempt_count"] == 1
    assert first_failure["next_attempt_at"] is not None
    assert first_failure["claim_token"] is None

    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE outbound_messages
            SET next_attempt_at = '2000-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (outbound_id,),
        )

    with pytest.raises(OutboundDispatchError):
        dispatch_outbound_message(outbound_id, "generic")
    final_failure = repository.get_outbound_message(outbound_id)
    assert final_failure["status"] == "dead_letter"
    assert final_failure["attempt_count"] == 2
    assert final_failure["next_attempt_at"] is None
    assert final_failure["failed_at"] is not None
    assert "provider unavailable" in final_failure["last_error"]


def test_worker_lease_has_single_owner_and_can_be_released() -> None:
    lease_name = f"lease-{uuid4().hex[:12]}"

    assert repository.acquire_worker_lease(lease_name, "worker-a", ttl_seconds=60) is True
    assert repository.acquire_worker_lease(lease_name, "worker-b", ttl_seconds=60) is False
    assert repository.release_worker_lease(lease_name, "worker-b") is False
    assert repository.release_worker_lease(lease_name, "worker-a") is True
    assert repository.acquire_worker_lease(lease_name, "worker-b", ttl_seconds=60) is True
