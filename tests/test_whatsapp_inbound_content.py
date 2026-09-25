"""P05 — W07: botões, listas e conteúdo sem texto na entrada WhatsApp.

Resposta a botão/lista vira texto utilizável; reação é ignorada; áudio,
imagem e demais tipos recebem orientação controlada, sem modelo e sem repetir.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core import orchestrator
from app.core.company_context import get_seed_company_context
from app.core.config import settings
from app.main import app
from app.services import inbound_service
from app.services.company_config_service import save_company_config
from app.storage import inbound_repository as queue
from app.storage.repository import RuntimeRepository
from app.storage.sql_support import execute_rowcount, fetch_one

client = TestClient(app)
DEFAULT_ORIENTATION = "Por enquanto consigo entender apenas mensagens de texto. Pode escrever o que você precisa?"


def _meta_payload(messages: list[dict]) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "bound"}, "messages": messages}}]}],
    }


def test_interactive_button_list_and_media_are_normalized() -> None:
    payload = _meta_payload([
        {"id": "m1", "from": "c1", "type": "interactive",
         "interactive": {"type": "button_reply", "button_reply": {"id": "confirm-42", "title": "Confirmar"}},
         "context": {"from": "bot", "id": "wamid.question"}},
        {"id": "m2", "from": "c1", "type": "interactive",
         "interactive": {"type": "list_reply", "list_reply": {"id": "slot-3", "title": "Terça 10h", "description": "x"}}},
        {"id": "m3", "from": "c1", "type": "button", "button": {"payload": "keep-booking", "text": "Manter horário"}},
        {"id": "m4", "from": "c1", "type": "audio", "audio": {"id": "media-1", "mime_type": "audio/ogg"}},
        {"id": "m5", "from": "c1", "type": "image", "image": {"id": "media-2"}},
        {"id": "m6", "from": "c1", "type": "reaction", "reaction": {"message_id": "wamid.x", "emoji": "👍"}},
        {"id": "m7", "from": "c1", "type": "unsupported", "errors": [{"code": 131051}]},
        {"id": "m8", "from": "c1", "type": "text", "text": {"body": "oi"}},
    ])

    extracted = routes._extract_meta_whatsapp_messages(payload)

    summary = [(item["provider_message_id"], item["content_type"], item["text"], item["reply_id"]) for item in extracted]
    assert summary == [
        ("m1", "interactive", "Confirmar", "confirm-42"),
        ("m2", "interactive", "Terça 10h", "slot-3"),
        ("m3", "button", "Manter horário", "keep-booking"),
        ("m4", "unsupported", "[audio]", None),
        ("m5", "unsupported", "[image]", None),
        ("m7", "unsupported", "[unsupported]", None),
        ("m8", "text", "oi", None),
    ]
    assert extracted[0]["context_message_id"] == "wamid.question"
    assert extracted[3]["media_type"] == "audio"


@pytest.fixture
def empty_queue():
    execute_rowcount("DELETE FROM inbound_jobs")
    yield
    execute_rowcount("DELETE FROM inbound_jobs")


def test_signed_audio_message_is_queued_as_unsupported_content(monkeypatch, company_factory, empty_queue) -> None:
    company_id = company_factory("media")
    secret = "synthetic-app-secret"
    monkeypatch.setattr(settings, "whatsapp_app_secret", secret)
    monkeypatch.setattr(routes, "_resolve_meta_company", lambda _: {"company_id": company_id, "company_slug": None})
    message_id = f"wamid.{uuid4().hex}"
    raw = json.dumps(_meta_payload([{"id": message_id, "from": "5511900000300", "type": "audio", "audio": {"id": "m"}}])).encode()
    headers = {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}

    response = client.post("/webhooks/whatsapp", content=raw, headers=headers)

    assert response.status_code == 200
    assert response.json()["queued_count"] == 1
    stored = fetch_one(
        "SELECT payload_json FROM inbound_jobs WHERE company_id = ? AND provider_message_id = ?",
        (company_id, message_id),
    )
    assert json.loads(stored["payload_json"])["content_type"] == "unsupported"


def test_worker_passes_content_type_and_message_ref_to_the_orchestrator(
    monkeypatch, company_factory, empty_queue
) -> None:
    company_id = company_factory("media-worker")
    message_id = uuid4().hex
    queue.enqueue_batch([{
        "company_id": company_id, "from_number": "5511900000301", "from_name": None,
        "text": "[audio]", "content_type": "unsupported", "provider_message_id": message_id,
    }])
    captured: list[dict] = []

    def capture(**kwargs):
        captured.append(kwargs)
        return {"company_id": company_id, "contact_id": "5511900000301", "auto_reply": False, "reply": ""}

    monkeypatch.setattr(inbound_service, "process_whatsapp_webhook", capture)
    monkeypatch.setattr(inbound_service, "publish_incoming_message_event", lambda *_a, **_kw: None)

    inbound_service.run_inbound_iteration()

    assert captured[0]["content_type"] == "unsupported"
    assert captured[0]["message_ref"] == f"whatsapp:{message_id}"


def _publish(company_id: int, policies: dict | None = None) -> None:
    context = copy.deepcopy(get_seed_company_context(None))
    context["company_name"] = "Mídia Ltda"
    save_company_config(company_id=company_id, context=context, policies=policies, author="test")


def _fail(reason: str):
    def _raise(*args, **kwargs):
        raise AssertionError(reason)

    return _raise


@pytest.fixture
def quiet_orchestrator(monkeypatch):
    monkeypatch.setattr(orchestrator, "update_conversation_summary", lambda **kwargs: "resumo")
    monkeypatch.setattr(
        orchestrator,
        "infer_pipeline_state",
        lambda **kwargs: {"pipeline_stage": "novo_lead", "next_action": "-", "lead_temperature": "warm"},
    )
    monkeypatch.setattr(orchestrator, "is_within_business_hours", lambda context, now: True)


def test_unsupported_content_gets_one_orientation_without_model_or_workflow(
    monkeypatch, company_factory, quiet_orchestrator
) -> None:
    company_id = company_factory("media-orchestrator")
    _publish(company_id)
    monkeypatch.setattr(orchestrator, "classify_message", _fail("mídia não chama o classificador"))
    monkeypatch.setattr(orchestrator, "handle_conversation_workflow", _fail("mídia não mexe no fluxo ativo"))

    first = orchestrator.process_whatsapp_webhook(
        "5511900000302", None, "[audio]", company_id=company_id, content_type="unsupported"
    )
    second = orchestrator.process_whatsapp_webhook(
        "5511900000302", None, "[image]", company_id=company_id, content_type="unsupported"
    )

    assert first["action"] == "unsupported_content"
    assert first["reply"] == DEFAULT_ORIENTATION
    assert first["auto_reply"] is True
    assert first["handoff_required"] is False
    assert second["reply"] == ""
    assert second["auto_reply"] is False
    assert RuntimeRepository().get_pending_questions(company_id=company_id) == []


def test_orientation_text_follows_company_policy(monkeypatch, company_factory, quiet_orchestrator) -> None:
    company_id = company_factory("media-policy")
    _publish(company_id, {"conversation": {"unsupported_content_message": "Mande por escrito, {company_name} agradece."}})

    result = orchestrator.process_whatsapp_webhook(
        "5511900000303", None, "[video]", company_id=company_id, content_type="unsupported"
    )

    assert result["reply"] == "Mande por escrito, Mídia Ltda agradece."


def test_button_reply_goes_through_the_normal_conversation_path(
    monkeypatch, company_factory, quiet_orchestrator
) -> None:
    company_id = company_factory("media-button")
    _publish(company_id)
    seen: list[str] = []

    def workflow(**kwargs):
        seen.append(kwargs["message"])
        return type(
            "WorkflowStub",
            (),
            {"handled": True, "classification": "agendamento", "reply": "Pronto.", "source": "workflow",
             "action": "booking_kept", "priority": "high", "auto_reply": True, "workflow_type": "booking_change"},
        )()

    monkeypatch.setattr(orchestrator, "handle_conversation_workflow", workflow)

    result = orchestrator.process_whatsapp_webhook(
        "5511900000304", None, "Manter horário", company_id=company_id, content_type="button"
    )

    assert seen == ["Manter horário"]
    assert result["action"] == "booking_kept"
