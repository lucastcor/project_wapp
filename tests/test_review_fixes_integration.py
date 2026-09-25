"""Regressões dos achados da revisão independente de P02/P03/P05 (14/09/2026)."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import settings
from app.main import app
from app.services import conversation_workflow_service as workflow_service
from app.services import inbound_service, outbound_dispatch_service
from app.services.outbound_dispatch_service import OutboundDispatchError, dispatch_outbound_message
from app.storage import channel_delivery_repository as channel
from app.storage import inbound_repository as queue
from app.storage import operation_repository as ops
from app.storage.repository import RuntimeRepository
from app.storage.sql_support import execute_rowcount, fetch_one

client = TestClient(app)
repository = RuntimeRepository()


def _contact() -> str:
    return f"5511{uuid4().int % 10**9:09d}"


@pytest.fixture
def empty_queue():
    execute_rowcount("DELETE FROM inbound_jobs")
    yield
    execute_rowcount("DELETE FROM inbound_jobs")


# ── botão/lista não confirma ação ────────────────────────────────────


@pytest.mark.parametrize("content_type", ["button", "interactive"])
def test_tapped_confirmation_never_executes_and_asks_for_typed_yes(monkeypatch, content_type) -> None:
    def _fail(**kwargs):
        raise AssertionError("toque não pode chegar à fila nem aos fluxos")

    monkeypatch.setattr(workflow_service, "process_queue_contact_message", _fail)
    monkeypatch.setattr(workflow_service.repository, "get_active_conversation_flow", lambda **kwargs: None)

    result = workflow_service.handle_conversation_workflow(
        contact_id=_contact(),
        contact_name=None,
        message="Confirmar",
        company_id=None,
        company_context={},
        content_type=content_type,
    )

    assert result.handled is True
    assert result.action == "confirmation_requires_typed_reply"


# ── webhook: item de número não vinculado não derruba o lote ──────────


def test_mixed_batch_keeps_bound_messages_and_rejects_unbound(monkeypatch, company_factory, empty_queue) -> None:
    company_id = company_factory("mixed-batch")
    phone_bound = f"phone-{uuid4().hex[:8]}"
    assert repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=phone_bound, updated_by="test")
    secret = "synthetic-app-secret"
    monkeypatch.setattr(settings, "whatsapp_app_secret", secret)
    payload = {"object": "whatsapp_business_account", "entry": [
        {"changes": [{"value": {"metadata": {"phone_number_id": phone_bound}, "messages": [
            {"id": f"wamid.{uuid4().hex}", "from": _contact(), "type": "text", "text": {"body": "oi"}, "timestamp": "99999999999999999999"},
        ]}}]},
        {"changes": [{"value": {"metadata": {"phone_number_id": "phone-unbound-test"}, "messages": [
            {"id": f"wamid.{uuid4().hex}", "from": _contact(), "type": "text", "text": {"body": "oi"}},
        ]}}]},
    ]}
    raw = json.dumps(payload).encode()
    headers = {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}

    response = client.post("/webhooks/whatsapp", content=raw, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["queued_count"] == 1
    assert body["rejected_count"] == 1
    assert len(queue.list_jobs(company_id)) == 1


def test_absurd_provider_timestamp_does_not_break_extraction() -> None:
    assert routes._unix_timestamp_iso("99999999999999999999") is None
    assert routes._unix_timestamp_iso("abc") is None


# ── worker de entrada: resultado do envio ─────────────────────────────


def _ready_job(company_id: int, monkeypatch) -> dict:
    contact_id = _contact()
    repository.save_contact(contact_id, "Cliente", company_id=company_id)
    queue.enqueue_batch([{"company_id": company_id, "from_number": contact_id, "text": "oi",
                          "provider_message_id": uuid4().hex}])
    monkeypatch.setattr(
        inbound_service,
        "process_whatsapp_webhook",
        lambda **kwargs: {"company_id": company_id, "contact_id": contact_id, "auto_reply": True, "reply": "Olá!"},
    )
    monkeypatch.setattr(inbound_service, "publish_incoming_message_event", lambda *_a, **_kw: None)
    inbound_service.run_inbound_iteration()
    return queue.list_jobs(company_id)[0]


@pytest.mark.parametrize(
    ("kind", "expected_status"),
    [("permanent", "completed"), ("transient", "ready"), ("uncertain", "review")],
)
def test_dispatch_outcome_decides_whether_the_conversation_blocks(
    monkeypatch, company_factory, empty_queue, kind, expected_status
) -> None:
    company_id = company_factory("inbound-dispatch")
    job = _ready_job(company_id, monkeypatch)
    assert job["status"] == "ready"

    def failing_dispatch(*args, **kwargs):
        raise OutboundDispatchError("blocked:outside_window_no_template", kind=kind)

    monkeypatch.setattr(inbound_service, "dispatch_outbound_message", failing_dispatch)
    inbound_service.run_inbound_iteration()

    assert queue.list_jobs(company_id)[0]["status"] == expected_status


def _transient_failure(*args, **kwargs):
    raise OutboundDispatchError("transient:130429:Rate limit hit", kind="transient")


def test_transient_dispatch_failure_waits_without_blocking_other_companies(
    monkeypatch, company_factory, empty_queue
) -> None:
    waiting_company = company_factory("inbound-wait")
    other_company = company_factory("inbound-other")
    waiting = _ready_job(waiting_company, monkeypatch)
    monkeypatch.setattr(inbound_service, "dispatch_outbound_message", _transient_failure)

    inbound_service.run_inbound_iteration()
    stored = fetch_one("SELECT status, available_at, dispatch_attempts FROM inbound_jobs WHERE id = ?", (waiting["id"],))
    assert (stored["status"], stored["dispatch_attempts"]) == ("ready", 1)
    assert stored["available_at"] > queue.now_iso()

    other_contact = _contact()
    repository.save_contact(other_contact, "Cliente", company_id=other_company)
    queue.enqueue_batch([{"company_id": other_company, "from_number": other_contact, "text": "oi",
                          "provider_message_id": uuid4().hex}])
    outcome = inbound_service.run_inbound_iteration()

    assert outcome["job_id"] != waiting["id"]
    assert queue.list_jobs(other_company)[0]["status"] == "ready"


def test_dispatch_retries_are_capped_and_go_to_review(monkeypatch, company_factory, empty_queue) -> None:
    monkeypatch.setattr(settings, "inbound_dispatch_max_attempts", 2)
    company_id = company_factory("inbound-cap")
    job = _ready_job(company_id, monkeypatch)
    monkeypatch.setattr(inbound_service, "dispatch_outbound_message", _transient_failure)

    inbound_service.run_inbound_iteration()
    execute_rowcount("UPDATE inbound_jobs SET available_at = NULL WHERE id = ?", (job["id"],))
    inbound_service.run_inbound_iteration()

    stored = fetch_one("SELECT status, last_error, dispatch_attempts FROM inbound_jobs WHERE id = ?", (job["id"],))
    assert (stored["status"], stored["last_error"], stored["dispatch_attempts"]) == (
        "review", "dispatch_retry_exhausted", 2
    )


def test_outbound_stuck_in_processing_sends_the_conversation_to_review(
    monkeypatch, company_factory, empty_queue
) -> None:
    company_id = company_factory("inbound-stuck")
    job = _ready_job(company_id, monkeypatch)
    outbound_id = fetch_one("SELECT outbound_message_id FROM inbound_jobs WHERE id = ?", (job["id"],))["outbound_message_id"]
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    execute_rowcount(
        "UPDATE outbound_messages SET status = 'processing', claimed_at = ?, claim_token = 'other-worker' WHERE id = ?",
        (stale, outbound_id),
    )

    inbound_service.run_inbound_iteration()

    stored = fetch_one("SELECT status, last_error FROM inbound_jobs WHERE id = ?", (job["id"],))
    assert (stored["status"], stored["last_error"]) == ("review", "StuckOutboundDispatchError")


def test_tap_without_anything_to_confirm_follows_the_normal_flow(monkeypatch, company_factory) -> None:
    company_id = company_factory("tap-free")
    monkeypatch.setattr(
        workflow_service,
        "process_queue_contact_message",
        lambda **kwargs: {"reply": "seguiu", "action": "queue_probe", "workflow_type": "appointment_queue"},
    )
    monkeypatch.setattr(workflow_service.repository, "get_active_conversation_flow", lambda **kwargs: None)

    result = workflow_service.handle_conversation_workflow(
        contact_id=_contact(),
        contact_name=None,
        message="Confirmar",
        company_id=company_id,
        company_context={},
        content_type="button",
    )

    assert result.action == "queue_probe"


# ── operação aceita e nunca reivindicada ─────────────────────────────


def test_stale_confirmed_operation_fails_without_effect(company_factory) -> None:
    company_id = company_factory("stale-confirmed")
    contact_id = _contact()
    proposal = ops.create_proposal(company_id=company_id, contact_id=contact_id, kind="create",
                                   params={"starts_at": "2030-01-01T12:00:00+00:00"}, ttl_minutes=30)
    confirmed_at = datetime.now(UTC) - timedelta(minutes=30)
    accepted = ops.accept_proposal(company_id=company_id, contact_id=contact_id, operation_id=proposal["id"],
                                   params_hash=proposal["params_hash"], confirmation_ref=f"wamid.{uuid4().hex}",
                                   now=confirmed_at)
    assert accepted.outcome == "accepted"

    assert ops.fail_stale_confirmations(company_id=company_id, older_than_minutes=10) == 1
    stored = ops.get_operation(company_id=company_id, operation_id=proposal["id"])
    assert (stored["status"], stored["last_error"]) == ("failed", "confirmation_not_executed")
    assert ops.claim_for_execution(company_id=company_id, operation_id=proposal["id"]) is None


# ── remarcação não sobrescreve cancelamento concorrente ──────────────


def test_schedule_update_does_not_overwrite_a_cancelled_booking(company_factory) -> None:
    company_id = company_factory("reschedule-race", business_type="services")
    starts_at = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    booking_id = repository.create_appointment_booking(
        provider="mock", title="Consulta", starts_at=starts_at.isoformat(),
        ends_at=(starts_at + timedelta(hours=1)).isoformat(), timezone="America/Sao_Paulo",
        company_id=company_id, contact_id=_contact(),
    )
    new_start = starts_at + timedelta(days=1)
    assert repository.hold_appointment_booking_interval(
        booking_id, hold_starts_at=new_start.isoformat(),
        hold_ends_at=(new_start + timedelta(hours=1)).isoformat(), company_id=company_id,
    )
    repository.update_appointment_booking_status(booking_id, "cancelled", company_id=company_id)

    updated = repository.update_appointment_booking_schedule(
        booking_id, starts_at=new_start.isoformat(), ends_at=(new_start + timedelta(hours=1)).isoformat(),
        timezone="America/Sao_Paulo", external_event_id=None, company_id=company_id,
    )

    assert updated is False
    stored = repository.get_appointment_booking(booking_id, company_id=company_id)
    assert stored["status"] == "cancelled"
    assert datetime.fromisoformat(str(stored["starts_at"])).astimezone(UTC) == starts_at


# ── canal: status órfão e falha na decisão ────────────────────────────


def test_status_callback_arriving_before_send_is_linked_afterwards(company_factory, monkeypatch) -> None:
    company_id = company_factory("orphan-status")
    contact_id = _contact()
    provider_id = f"wamid.{uuid4().hex}"
    channel.record_delivery_status(company_id=company_id, provider_message_id=provider_id, status="delivered")
    channel.record_inbound(company_id=company_id, contact_id=contact_id)
    repository.save_contact(contact_id, "Cliente", company_id=company_id)
    outbound_id = repository.create_outbound_message(contact_id=contact_id, company_id=company_id,
                                                     message="Olá", source="whatsapp_auto_reply")

    class FakeProvider:
        def __init__(self, *, company_id=None) -> None:
            pass

        def send_message(self, contact_id, message):
            return {"provider_message_id": provider_id, "provider_response": {}}

    monkeypatch.setattr(settings, "outbound_provider", "whatsapp")
    monkeypatch.setattr(outbound_dispatch_service, "WhatsAppProvider", FakeProvider)

    dispatch_outbound_message(outbound_id, "whatsapp", company_id=company_id)

    assert repository.get_outbound_message(outbound_id, company_id=company_id)["delivery_status"] == "delivered"


def test_delivery_policy_failure_is_a_transient_dispatch_error(company_factory, monkeypatch) -> None:
    company_id = company_factory("policy-failure")
    contact_id = _contact()
    repository.save_contact(contact_id, "Cliente", company_id=company_id)
    outbound_id = repository.create_outbound_message(contact_id=contact_id, company_id=company_id,
                                                     message="Olá", source="whatsapp_auto_reply")
    monkeypatch.setattr(settings, "outbound_provider", "whatsapp")

    def broken(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(outbound_dispatch_service, "decide_whatsapp_delivery", broken)

    with pytest.raises(OutboundDispatchError) as exc_info:
        dispatch_outbound_message(outbound_id, "whatsapp", company_id=company_id)

    assert exc_info.value.kind == "transient"
    assert repository.get_outbound_message(outbound_id, company_id=company_id)["status"] == "retry"
