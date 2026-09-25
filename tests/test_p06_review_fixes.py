"""Regressões da revisão independente de P06 (14/09/2026).

Posse humana precisa valer também para o que já estava em andamento quando o
operador assumiu, para os envios automáticos fora do orquestrador e para a
resolução de revisão.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core import orchestrator
from app.core.company_context import get_seed_company_context
from app.core.config import settings
from app.main import app
from app.services import (
    inbound_service,
    memory_service,
    operations_summary_service,
    outbound_dispatch_service,
    pipeline_service,
)
from app.services.company_config_service import save_company_config
from app.services.outbound_dispatch_service import OutboundDispatchError, dispatch_outbound_message
from app.storage import inbound_repository as queue
from app.storage import ownership_repository as ownership
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


@pytest.fixture
def session_auth(monkeypatch):
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "legacy-viewer")
    monkeypatch.setattr(settings, "admin_operator_token", "legacy-operator")
    monkeypatch.setattr(settings, "admin_superadmin_token", "legacy-superadmin")
    monkeypatch.setattr(settings, "admin_api_token", None)


def _headers(principal: dict) -> dict[str, str]:
    return {
        "Cookie": f"project_wapp_platform_session={principal['token']}; project_wapp_csrf={principal['csrf']}",
        "X-CSRF-Token": principal["csrf"],
    }


def _enqueue(company_id: int, contact_id: str) -> None:
    repository.save_contact(contact_id, "Cliente", company_id=company_id)
    queue.enqueue_batch([{"company_id": company_id, "from_number": contact_id, "text": "oi",
                          "provider_message_id": uuid4().hex}])


# ── A1: tomada durante a geração da resposta ─────────────────────────


def test_take_during_generation_suppresses_the_bot_reply(
    monkeypatch, company_factory, admin_principal_factory, empty_queue
) -> None:
    company_id = company_factory("take-race")
    contact_id = _contact()
    user_id = int(admin_principal_factory(global_role="operator", company_ids=[company_id])["user_id"])
    _enqueue(company_id, contact_id)

    def generation_interrupted_by_take(**kwargs):
        assert ownership.take_ownership(company_id=company_id, contact_id=contact_id, user_id=user_id).outcome == "taken"
        return {"company_id": company_id, "contact_id": contact_id, "auto_reply": True, "reply": "Resposta do bot",
                "action": "auto_reply"}

    sent: list[int] = []
    monkeypatch.setattr(inbound_service, "process_whatsapp_webhook", generation_interrupted_by_take)
    monkeypatch.setattr(inbound_service, "publish_incoming_message_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(inbound_service, "dispatch_outbound_message", lambda oid, **kwargs: sent.append(oid))

    inbound_service.run_inbound_iteration()
    inbound_service.run_inbound_iteration()

    job = fetch_one("SELECT status, outbound_message_id FROM inbound_jobs WHERE company_id = ?", (company_id,))
    assert (job["status"], job["outbound_message_id"]) == ("completed", None)
    assert sent == []


def test_opt_out_confirmation_still_goes_out_under_human_ownership(
    monkeypatch, company_factory, admin_principal_factory, empty_queue
) -> None:
    company_id = company_factory("take-optout")
    contact_id = _contact()
    user_id = int(admin_principal_factory(global_role="operator", company_ids=[company_id])["user_id"])
    ownership.take_ownership(company_id=company_id, contact_id=contact_id, user_id=user_id)
    _enqueue(company_id, contact_id)
    monkeypatch.setattr(
        inbound_service,
        "process_whatsapp_webhook",
        lambda **kwargs: {"company_id": company_id, "contact_id": contact_id, "auto_reply": True,
                          "reply": "Pronto, você não vai mais receber avisos.", "action": "opt_out"},
    )
    monkeypatch.setattr(inbound_service, "publish_incoming_message_event", lambda *args, **kwargs: None)

    inbound_service.run_inbound_iteration()

    job = fetch_one("SELECT status, outbound_message_id FROM inbound_jobs WHERE company_id = ?", (company_id,))
    assert job["status"] == "ready"
    outbound = fetch_one("SELECT source FROM outbound_messages WHERE id = ?", (job["outbound_message_id"],))
    assert outbound["source"] == "whatsapp_channel_reply"


# ── A2 e M1: ramo humano sem modelo e sem automação ──────────────────


def test_human_owned_message_calls_no_model_and_triggers_no_automation(
    monkeypatch, company_factory, admin_principal_factory
) -> None:
    company_id = company_factory("take-llm")
    save_company_config(company_id=company_id, context=copy.deepcopy(get_seed_company_context(None)), author="test")
    contact_id = _contact()
    user_id = int(admin_principal_factory(global_role="operator", company_ids=[company_id])["user_id"])
    ownership.take_ownership(company_id=company_id, contact_id=contact_id, user_id=user_id)
    calls: list[str] = []

    class RecordingProvider:
        def generate_conversation_summary(self, **kwargs):
            calls.append("summary")
            return "resumo"

        def infer_pipeline_state(self, **kwargs):
            calls.append("pipeline")
            return {"pipeline_stage": "novo_lead", "next_action": "-", "lead_temperature": "warm"}

    def must_not_run(*args, **kwargs):
        calls.append("automation_or_workflow")
        raise AssertionError("nada automático sob posse humana")

    monkeypatch.setattr(settings, "classification_provider", "openai")
    monkeypatch.setattr(memory_service, "get_llm_provider", lambda **kwargs: RecordingProvider())
    monkeypatch.setattr(pipeline_service, "get_llm_provider", lambda **kwargs: RecordingProvider())
    monkeypatch.setattr(orchestrator, "handle_conversation_workflow", must_not_run)
    monkeypatch.setattr(orchestrator, "classify_message", must_not_run)
    monkeypatch.setattr(orchestrator, "emit_handoff_created", must_not_run)
    monkeypatch.setattr(orchestrator, "build_message_processed_event", must_not_run)
    monkeypatch.setattr(orchestrator, "is_within_business_hours", lambda context, now: True)

    result = orchestrator.process_whatsapp_webhook(
        contact_id, None, "texto do cliente", company_id=company_id, message_ref="whatsapp:human-llm"
    )

    assert result["action"] == "human_owned"
    assert calls == []


def test_automatic_sends_are_withheld_while_a_human_owns_the_conversation(
    monkeypatch, company_factory, admin_principal_factory
) -> None:
    company_id = company_factory("take-dispatch")
    contact_id = _contact()
    user_id = int(admin_principal_factory(global_role="operator", company_ids=[company_id])["user_id"])
    repository.save_contact(contact_id, "Cliente", company_id=company_id)
    ownership.take_ownership(company_id=company_id, contact_id=contact_id, user_id=user_id)
    monkeypatch.setattr(settings, "outbound_provider", "mock")
    monkeypatch.setattr(outbound_dispatch_service, "require_usage_capacity", lambda **kwargs: None)

    for source in ("reminder_customer", "appointment_queue_offer", "automation:execution:7", "whatsapp_auto_reply"):
        outbound_id = repository.create_outbound_message(contact_id=contact_id, company_id=company_id,
                                                         message="automática", source=source)
        with pytest.raises(OutboundDispatchError):
            dispatch_outbound_message(outbound_id, channel="whatsapp", company_id=company_id)
        stored = fetch_one("SELECT status, last_error FROM outbound_messages WHERE id = ?", (outbound_id,))
        assert stored["status"] == "dead_letter", source
        assert "blocked:human_owned" in stored["last_error"], source

    operator_text = repository.create_outbound_message(contact_id=contact_id, company_id=company_id,
                                                       message="Oi, aqui é a Ana da equipe.", source="owner_resolution")
    assert dispatch_outbound_message(operator_text, channel="whatsapp", company_id=company_id)["delivery_status"] == "sent"


# ── M2: resolver revisão retira a resposta pendente ──────────────────


@pytest.fixture
def review_with_reply(monkeypatch, company_factory, admin_principal_factory, empty_queue) -> dict:
    company_id = company_factory("resolve-reply")
    contact_id = _contact()
    _enqueue(company_id, contact_id)
    monkeypatch.setattr(
        inbound_service,
        "process_whatsapp_webhook",
        lambda **kwargs: {"company_id": company_id, "contact_id": contact_id, "auto_reply": True, "reply": "Olá!",
                          "action": "auto_reply"},
    )
    monkeypatch.setattr(inbound_service, "publish_incoming_message_event", lambda *args, **kwargs: None)
    inbound_service.run_inbound_iteration()
    job = fetch_one("SELECT id, outbound_message_id FROM inbound_jobs WHERE company_id = ?", (company_id,))
    execute_rowcount("UPDATE inbound_jobs SET status = 'review', last_error = 'dispatch_retry_exhausted' WHERE id = ?",
                     (job["id"],))
    return {
        "company_id": company_id,
        "job_id": job["id"],
        "outbound_id": job["outbound_message_id"],
        "operator": admin_principal_factory(global_role="operator", company_ids=[company_id]),
    }


def _resolve(review: dict):
    return client.post(
        f"/admin/inbound/jobs/{review['job_id']}/resolve",
        params={"company_id": review["company_id"]},
        headers=_headers(review["operator"]),
    )


def test_resolving_a_review_withdraws_the_pending_reply(session_auth, review_with_reply) -> None:
    execute_rowcount("UPDATE outbound_messages SET status = 'retry' WHERE id = ?", (review_with_reply["outbound_id"],))

    response = _resolve(review_with_reply)

    assert response.status_code == 200, response.text
    assert response.json()["outbound"] == "withdrawn"
    outbound = fetch_one("SELECT status, last_error FROM outbound_messages WHERE id = ?", (review_with_reply["outbound_id"],))
    assert (outbound["status"], outbound["last_error"]) == ("dead_letter", "permanent:-:blocked:resolved_manually")
    job = fetch_one("SELECT status FROM inbound_jobs WHERE id = ?", (review_with_reply["job_id"],))
    assert job["status"] == "completed"
    summary = operations_summary_service.get_operations_summary(review_with_reply["company_id"])
    assert "blocked:resolved_manually" not in summary["outbound"]["dead_letters_by_code"]


def test_resolving_while_the_reply_is_being_sent_is_refused(session_auth, review_with_reply) -> None:
    execute_rowcount(
        "UPDATE outbound_messages SET status = 'processing', claimed_at = ?, claim_token = 'worker' WHERE id = ?",
        (datetime.now(UTC).isoformat(), review_with_reply["outbound_id"]),
    )

    assert _resolve(review_with_reply).status_code == 409
    assert fetch_one("SELECT status FROM inbound_jobs WHERE id = ?", (review_with_reply["job_id"],))["status"] == "review"


# ── M4: rotas e resumo que faltavam provar ───────────────────────────


def test_token_without_user_cannot_take_and_owner_releases_own_conversation(
    session_auth, company_factory, admin_principal_factory
) -> None:
    company_id = company_factory("take-routes")
    operator = admin_principal_factory(global_role="operator", company_ids=[company_id])
    formatted = "+55 (11) 90000-0123"

    legacy = client.post(
        f"/admin/conversations/{quote(formatted, safe='')}/take",
        params={"company_id": company_id},
        headers={"X-Admin-Token": "legacy-operator"},
    )
    assert legacy.status_code in {403, 422}
    assert ownership.is_human_owned(company_id=company_id, contact_id="5511900000123") is False

    taken = client.post(f"/admin/conversations/{quote(formatted, safe='')}/take", params={"company_id": company_id},
                        headers=_headers(operator))
    assert taken.status_code == 200, taken.text
    assert ownership.is_human_owned(company_id=company_id, contact_id="5511900000123") is True

    released = client.post("/admin/conversations/5511900000123/release", params={"company_id": company_id},
                           headers=_headers(operator))
    assert released.status_code == 200, released.text
    assert released.json()["outcome"] == "released"


def test_summary_flags_a_stale_backlog_without_a_live_worker(monkeypatch, company_factory, empty_queue) -> None:
    company_id = company_factory("summary-backlog")
    _enqueue(company_id, _contact())
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="microseconds")
    execute_rowcount("UPDATE inbound_jobs SET created_at = ? WHERE company_id = ?", (old, company_id))
    monkeypatch.setattr(operations_summary_service, "worker_is_alive", lambda component: False)

    summary = operations_summary_service.get_operations_summary(company_id)

    codes = {alert["code"] for alert in summary["alerts"]}
    assert {"inbound_backlog_stale", "inbound_worker_down"} <= codes
