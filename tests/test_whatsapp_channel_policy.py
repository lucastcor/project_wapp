"""P05 — W03, W05, W06: janela de 24 h, templates, status de entrega e opt-out.

Sem rede: o provider WhatsApp é substituído por um registro de chamadas. Banco
real (SQLite descartável ou TEST_DATABASE_URL).
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core import orchestrator
from app.core.company_context import get_seed_company_context
from app.core.config import settings
from app.main import app
from app.services import outbound_dispatch_service
from app.services.company_config_service import CompanyConfigError, save_company_config
from app.services.outbound_dispatch_service import OutboundDispatchError, dispatch_outbound_message
from app.services.whatsapp_delivery_policy import (
    decide_whatsapp_delivery,
    parse_channel_command,
    purpose_for_source,
)
from app.storage import channel_delivery_repository as channel
from app.storage import inbound_repository as queue
from app.storage.repository import RuntimeRepository
from app.storage.sql_support import execute_rowcount

repository = RuntimeRepository()
client = TestClient(app)
NOW = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
REMINDER_TEXT = "Consulta amanhã às 10h"


def _contact() -> str:
    return f"5511{uuid4().int % 10**9:09d}"


def _outbound(company_id: int, contact_id: str, source: str, parameters: list[str] | None = None) -> dict:
    repository.save_contact(contact_id, "Cliente", company_id=company_id)
    outbound_id = repository.create_outbound_message(
        contact_id=contact_id,
        company_id=company_id,
        message=f"Lembrete: {REMINDER_TEXT}",
        source=source,
        template_parameters=parameters,
    )
    stored = repository.get_outbound_message(outbound_id, company_id=company_id)
    assert stored is not None
    return stored


def _template(company_id: int, *, purpose: str = "reminders", parameter_count: int = 1, status: str = "approved"):
    return channel.upsert_template(
        company_id=company_id,
        purpose=purpose,
        name=f"{purpose}_v1",
        language_code="pt_BR",
        category="utility",
        status=status,
        parameter_count=parameter_count,
    )


def _decide(outbound: dict, now: datetime = NOW):
    return decide_whatsapp_delivery(outbound, now=now, template_language="pt_BR")


@pytest.mark.parametrize(
    ("source", "purpose"),
    [
        ("whatsapp_auto_reply", "reply"),
        ("reminder_customer", "reminders"),
        ("reminder_internal", "internal_notice"),
        ("appointment_queue_offer", "queue_offers"),
        ("automation:execution:42", "automation"),
        ("owner_resolution", "service_followup"),
        ("origem-desconhecida", "service_followup"),
    ],
)
def test_purpose_is_derived_from_the_outbound_source(source: str, purpose: str) -> None:
    assert purpose_for_source(source) == purpose


@pytest.mark.parametrize(
    ("message", "command"),
    [
        ("SAIR", "opt_out"),
        ("Parar.", "opt_out"),
        ("cancelar mensagens", "opt_out"),
        ("Voltar", "opt_in"),
        ("cancelar", None),
        ("quero sair do plano", None),
        ("parar de receber lembrete amanhã?", None),
    ],
)
def test_only_the_whole_message_is_a_channel_command(message: str, command: str | None) -> None:
    assert parse_channel_command(message) == command


# ── W03: janela de atendimento e template ─────────────────────────────


def test_inside_the_service_window_free_text_is_allowed(company_factory) -> None:
    company_id = company_factory("window-inside")
    contact_id = _contact()
    outbound = _outbound(company_id, contact_id, "reminder_customer", [REMINDER_TEXT])
    channel.record_inbound(company_id=company_id, contact_id=contact_id, received_at=NOW - timedelta(hours=2))
    # Entrada mais antiga reentregue depois não encurta a janela.
    channel.record_inbound(company_id=company_id, contact_id=contact_id, received_at=NOW - timedelta(hours=30))

    decision = _decide(outbound)

    assert (decision.mode, decision.reason) == ("text", "within_service_window")


def test_outside_the_window_only_an_approved_utility_template_with_valid_parameters_is_sent(company_factory) -> None:
    company_id = company_factory("window-outside")
    contact_id = _contact()
    channel.record_inbound(company_id=company_id, contact_id=contact_id, received_at=NOW - timedelta(hours=25))
    outbound = _outbound(company_id, contact_id, "reminder_customer", [REMINDER_TEXT])

    assert _decide(outbound).reason == "outside_window_no_template"

    _template(company_id, status="pending")
    assert _decide(outbound).reason == "outside_window_no_template"

    _template(company_id, status="approved")
    decision = _decide(outbound)
    assert decision.mode == "template"
    assert decision.parameters == [REMINDER_TEXT]

    wrong_count = _outbound(company_id, contact_id, "reminder_customer", [REMINDER_TEXT, "extra"])
    assert _decide(wrong_count).reason == "template_parameters_invalid"
    multiline = _outbound(company_id, contact_id, "reminder_customer", ["linha 1\nlinha 2"])
    assert _decide(multiline).reason == "template_parameters_invalid"


def test_marketing_templates_cannot_serve_service_purposes(company_factory) -> None:
    company_id = company_factory("marketing-guard")

    with pytest.raises(ValueError):
        channel.upsert_template(
            company_id=company_id,
            purpose="reminders",
            name="promo",
            language_code="pt_BR",
            category="marketing",
            status="approved",
            parameter_count=0,
        )


@pytest.fixture
def fake_whatsapp(monkeypatch):
    calls: list[tuple] = []

    class FakeProvider:
        def __init__(self, *, company_id=None) -> None:
            self.company_id = company_id

        def send_message(self, contact_id, message):
            calls.append(("text", contact_id))
            return {"provider_message_id": f"wamid.text.{uuid4().hex[:8]}", "provider_response": {}}

        def send_template(self, contact_id, *, template_name, language_code, parameters):
            calls.append(("template", template_name, language_code, list(parameters)))
            return {"provider_message_id": f"wamid.tpl.{uuid4().hex[:8]}", "provider_response": {}}

    monkeypatch.setattr(settings, "outbound_provider", "whatsapp")
    monkeypatch.setattr(outbound_dispatch_service, "WhatsAppProvider", FakeProvider)
    return calls


def test_dispatch_blocks_visibly_without_template_and_sends_the_template_when_approved(
    company_factory, fake_whatsapp
) -> None:
    company_id = company_factory("dispatch-window")
    contact_id = _contact()
    blocked = _outbound(company_id, contact_id, "reminder_customer", [REMINDER_TEXT])

    with pytest.raises(OutboundDispatchError) as exc_info:
        dispatch_outbound_message(blocked["id"], "whatsapp", company_id=company_id)

    assert exc_info.value.kind == "permanent"
    stored = repository.get_outbound_message(blocked["id"], company_id=company_id)
    assert stored["status"] == "dead_letter"
    assert "blocked:outside_window_no_template" in stored["last_error"]
    assert fake_whatsapp == []

    _template(company_id)
    queued = _outbound(company_id, contact_id, "reminder_customer", [REMINDER_TEXT])
    result = dispatch_outbound_message(queued["id"], "whatsapp", company_id=company_id)

    assert result["delivery_status"] == "sent"
    assert fake_whatsapp == [("template", "reminders_v1", "pt_BR", [REMINDER_TEXT])]


# ── W06: opt-out e consentimento ──────────────────────────────────────


def test_opt_out_after_enqueue_blocks_proactive_messages_at_dispatch(company_factory, fake_whatsapp) -> None:
    company_id = company_factory("dispatch-optout")
    contact_id = _contact()
    channel.record_inbound(company_id=company_id, contact_id=contact_id)
    reminder = _outbound(company_id, contact_id, "reminder_customer", [REMINDER_TEXT])
    reply = _outbound(company_id, contact_id, "whatsapp_auto_reply")

    channel.set_opt_out(company_id=company_id, contact_id=contact_id, source="test")

    with pytest.raises(OutboundDispatchError):
        dispatch_outbound_message(reminder["id"], "whatsapp", company_id=company_id)
    stored = repository.get_outbound_message(reminder["id"], company_id=company_id)
    assert stored["status"] == "dead_letter"
    assert "blocked:opted_out" in stored["last_error"]

    # Resposta à própria mensagem do cliente não é contato proativo.
    assert dispatch_outbound_message(reply["id"], "whatsapp", company_id=company_id)["delivery_status"] == "sent"
    assert fake_whatsapp == [("text", contact_id)]


def test_revoked_consent_blocks_only_its_purpose_and_opt_out_is_per_company(company_factory) -> None:
    company_a = company_factory("consent-a")
    company_b = company_factory("consent-b")
    contact_id = _contact()
    for company_id in (company_a, company_b):
        channel.record_inbound(company_id=company_id, contact_id=contact_id, received_at=NOW - timedelta(hours=1))

    channel.set_consent(company_id=company_a, contact_id=contact_id, purpose="queue_offers", status="revoked", source="test")
    channel.set_opt_out(company_id=company_b, contact_id=contact_id, source="test")

    assert _decide(_outbound(company_a, contact_id, "appointment_queue_offer", ["x", "y"])).reason == "consent_revoked"
    assert _decide(_outbound(company_a, contact_id, "reminder_customer", [REMINDER_TEXT])).mode == "text"
    assert _decide(_outbound(company_b, contact_id, "reminder_customer", [REMINDER_TEXT])).reason == "opted_out"
    assert channel.is_opted_out(company_id=company_a, contact_id=contact_id) is False

    assert channel.clear_opt_out(company_id=company_b, contact_id=contact_id) is True
    assert _decide(_outbound(company_b, contact_id, "reminder_customer", [REMINDER_TEXT])).mode == "text"


@pytest.fixture
def quiet_orchestrator(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("comando de canal não passa por classificador nem workflow")

    monkeypatch.setattr(orchestrator, "classify_message", _fail)
    monkeypatch.setattr(orchestrator, "handle_conversation_workflow", _fail)
    monkeypatch.setattr(orchestrator, "update_conversation_summary", lambda **kwargs: "resumo")
    monkeypatch.setattr(
        orchestrator,
        "infer_pipeline_state",
        lambda **kwargs: {"pipeline_stage": "novo_lead", "next_action": "-", "lead_temperature": "warm"},
    )
    monkeypatch.setattr(orchestrator, "is_within_business_hours", lambda context, now: True)


def test_sair_and_voltar_change_opt_out_without_classifier_or_workflow(
    company_factory, monkeypatch, quiet_orchestrator
) -> None:
    company_id = company_factory("optout-command")
    context = copy.deepcopy(get_seed_company_context(None))
    save_company_config(company_id=company_id, context=context, author="test")
    contact_id = _contact()

    left = orchestrator.process_whatsapp_webhook(contact_id, None, "SAIR", company_id=company_id, message_ref="whatsapp:m1")
    assert left["action"] == "opt_out"
    assert left["reply"].startswith("Pronto. Você não vai mais receber")
    assert channel.is_opted_out(company_id=company_id, contact_id=contact_id)

    back = orchestrator.process_whatsapp_webhook(contact_id, None, "Voltar", company_id=company_id, message_ref="whatsapp:m2")
    assert back["action"] == "opt_in"
    assert not channel.is_opted_out(company_id=company_id, contact_id=contact_id)

    handled = type(
        "WorkflowStub",
        (),
        {"handled": True, "classification": "agendamento", "reply": "Ok.", "source": "workflow",
         "action": "workflow_step", "priority": "high", "auto_reply": True, "workflow_type": "scheduling"},
    )()
    monkeypatch.setattr(orchestrator, "handle_conversation_workflow", lambda **kwargs: handled)
    # "voltar" sem descadastro prévio é conversa normal.
    normal = orchestrator.process_whatsapp_webhook(contact_id, None, "voltar", company_id=company_id, message_ref="whatsapp:m3")
    assert normal["action"] == "workflow_step"


def test_invalid_template_language_policy_is_rejected(company_factory) -> None:
    company_id = company_factory("template-language")
    context = copy.deepcopy(get_seed_company_context(None))

    with pytest.raises(CompanyConfigError, match="template_language"):
        save_company_config(company_id=company_id, context=context, policies={"channel": {"template_language": "português"}})


# ── entrada registra a janela ────────────────────────────────────────


@pytest.fixture
def empty_queue():
    execute_rowcount("DELETE FROM inbound_jobs")
    yield
    execute_rowcount("DELETE FROM inbound_jobs")


def test_enqueue_records_the_window_from_provider_time_never_in_the_future(company_factory, empty_queue) -> None:
    company_id = company_factory("window-enqueue")
    contact_id = _contact()
    three_hours_ago = (datetime.now(UTC) - timedelta(hours=3)).replace(microsecond=0)

    queue.enqueue_batch([{
        "company_id": company_id, "from_number": contact_id, "text": "oi",
        "provider_message_id": uuid4().hex, "received_at": three_hours_ago.isoformat(),
    }])
    state = channel.get_contact_state(company_id=company_id, contact_id=contact_id)
    assert datetime.fromisoformat(state["last_inbound_at"]) == three_hours_ago

    queue.enqueue_batch([{
        "company_id": company_id, "from_number": contact_id, "text": "de novo",
        "provider_message_id": uuid4().hex, "received_at": (datetime.now(UTC) + timedelta(hours=5)).isoformat(),
    }])
    state = channel.get_contact_state(company_id=company_id, contact_id=contact_id)
    assert datetime.fromisoformat(state["last_inbound_at"]) <= datetime.now(UTC)


def test_meta_message_timestamp_becomes_received_at() -> None:
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "bound"},
        "messages": [{"id": "m1", "from": "c1", "timestamp": "1789000000", "type": "text", "text": {"body": "oi"}}],
    }}]}]}

    extracted = routes._extract_meta_whatsapp_messages(payload)

    assert extracted[0]["received_at"] == datetime.fromtimestamp(1789000000, UTC).isoformat()


# ── W05: status de entrega ───────────────────────────────────────────


def _sent_outbound(company_id: int, provider_message_id: str) -> int:
    outbound = _outbound(company_id, _contact(), "whatsapp_auto_reply")
    repository.mark_outbound_message_sent(outbound["id"], company_id=company_id, provider_message_id=provider_message_id)
    return int(outbound["id"])


def _delivery_status(company_id: int, outbound_id: int) -> str | None:
    return repository.get_outbound_message(outbound_id, company_id=company_id)["delivery_status"]


def test_delivery_status_advances_and_never_regresses(company_factory) -> None:
    company_id = company_factory("delivery")
    provider_id = f"wamid.{uuid4().hex}"
    outbound_id = _sent_outbound(company_id, provider_id)

    assert channel.record_delivery_status(company_id=company_id, provider_message_id=provider_id, status="sent")["advanced"]
    assert channel.record_delivery_status(company_id=company_id, provider_message_id=provider_id, status="read")["advanced"]
    late = channel.record_delivery_status(company_id=company_id, provider_message_id=provider_id, status="delivered")
    assert late == {"recorded": True, "outbound_message_id": outbound_id, "advanced": False}
    duplicate = channel.record_delivery_status(company_id=company_id, provider_message_id=provider_id, status="read")
    assert duplicate["recorded"] is False
    failed = channel.record_delivery_status(company_id=company_id, provider_message_id=provider_id, status="failed", error_code=131026)
    assert failed["advanced"] is False
    assert _delivery_status(company_id, outbound_id) == "read"


def test_failed_status_replaces_only_undelivered_sends(company_factory) -> None:
    company_id = company_factory("delivery-failed")
    provider_id = f"wamid.{uuid4().hex}"
    outbound_id = _sent_outbound(company_id, provider_id)

    channel.record_delivery_status(company_id=company_id, provider_message_id=provider_id, status="sent")
    channel.record_delivery_status(company_id=company_id, provider_message_id=provider_id, status="failed", error_code=131047)

    assert _delivery_status(company_id, outbound_id) == "failed"


def test_status_callback_never_touches_another_company(company_factory) -> None:
    company_a = company_factory("delivery-a")
    company_b = company_factory("delivery-b")
    provider_id = f"wamid.{uuid4().hex}"
    outbound_a = _sent_outbound(company_a, provider_id)

    foreign = channel.record_delivery_status(company_id=company_b, provider_message_id=provider_id, status="read")

    assert foreign == {"recorded": True, "outbound_message_id": None, "advanced": False}
    assert _delivery_status(company_a, outbound_a) is None


def test_signed_status_callback_updates_only_the_bound_company(company_factory, monkeypatch) -> None:
    company_id = company_factory("delivery-webhook")
    provider_id = f"wamid.{uuid4().hex}"
    outbound_id = _sent_outbound(company_id, provider_id)
    secret = "synthetic-app-secret"
    monkeypatch.setattr(settings, "whatsapp_app_secret", secret)
    monkeypatch.setattr(routes, "_resolve_status_company", lambda phone: company_id if phone == "bound-phone" else None)
    payload = {"object": "whatsapp_business_account", "entry": [
        {"changes": [{"value": {"metadata": {"phone_number_id": "bound-phone"}, "statuses": [
            {"id": provider_id, "status": "delivered", "timestamp": "1789000000", "recipient_id": "x"},
            {"id": provider_id, "status": "delivered", "timestamp": "1789000000", "recipient_id": "x"},
        ]}}]},
        {"changes": [{"value": {"metadata": {"phone_number_id": "unbound-phone"}, "statuses": [
            {"id": provider_id, "status": "read", "timestamp": "1789000100", "recipient_id": "x"},
        ]}}]},
    ]}
    raw = json.dumps(payload).encode()
    headers = {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}

    response = client.post("/webhooks/whatsapp", content=raw, headers=headers)

    assert response.status_code == 200
    assert response.json()["status_updates"] == 1
    assert _delivery_status(company_id, outbound_id) == "delivered"

    unsigned = client.post("/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64})
    assert unsigned.status_code == 403
