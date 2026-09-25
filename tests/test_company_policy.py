"""P01 — política efetiva por empresa e fonte única de contexto em runtime.

Cenários de docs/piloto/VALIDACAO.md: C01 (saudação sem fluxo), C03
(ambiguidade/modelo indisponível), C04 (políticas distintas, override de
preset, rascunho sem efeito, fora do horário). Também compatibilidade com o
contexto legado, recusa de identidade alheia e validação na escrita.

Integração com banco real (SQLite descartável ou TEST_DATABASE_URL); só
classificador, gerador de resposta e resumo são substituídos, para que o
resultado dependa da política e não de um modelo.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import orchestrator
from app.core.company_context import get_seed_company_context, save_company_context
from app.services import company_config_service
from app.services.company_config_service import (
    CompanyConfigError,
    CompanyConfigInvalidError,
    get_company_config,
    publish_company_config,
    publish_legacy_context_write,
    save_company_config,
)
from app.services.company_policy_service import (
    BookingChangeRule,
    evaluate_booking_change,
    is_plain_greeting,
    is_within_business_hours,
    resolve_effective_policy,
)
from app.storage import company_config_repository as config_repository
from app.storage.repository import RuntimeRepository

DEFAULT_CLARIFICATION = (
    "Não consegui entender bem o que você precisa. "
    "Pode me contar com um pouco mais de detalhe?"
)
DEFAULT_HANDOFF_NOTICE = (
    "Recebi sua mensagem e encaminhei para a nossa equipe, "
    "que continua o atendimento por aqui."
)
DEFAULT_AFTER_HOURS = (
    "Estamos fora do horário de atendimento. Sua mensagem ficou registrada "
    "e a equipe responde no próximo período de atendimento."
)


def _context(name: str, **overrides) -> dict:
    base = copy.deepcopy(get_seed_company_context(None))
    base["company_name"] = name
    base.update(overrides)
    return base


def _publish(company_id: int, *, name: str, policies=None, context_overrides=None, publish=True) -> dict:
    return save_company_config(
        company_id=company_id,
        context=_context(name, **(context_overrides or {})),
        policies=policies,
        author="test",
        publish=publish,
    )


def _classify_as(monkeypatch, classification: str, confidence: float) -> None:
    monkeypatch.setattr(
        orchestrator,
        "classify_message",
        lambda message: {"classification": classification, "confidence": confidence, "reason": "teste"},
    )


def _send(company_id: int, message: str, contact_id: str = "5511900000001") -> dict:
    return orchestrator.process_incoming_message(
        contact_id=contact_id,
        contact_name="Cliente",
        message=message,
        company_id=company_id,
    )


def _pending_count(company_id: int) -> int:
    return len(RuntimeRepository().get_pending_questions(company_id=company_id))


def _fail(reason: str):
    def _raise(*args, **kwargs):
        raise AssertionError(reason)

    return _raise


@pytest.fixture()
def deterministic_runtime(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "generate_business_reply",
        lambda **kwargs: {"reply": "Resposta gerada.", "source": "openai"},
    )
    monkeypatch.setattr(orchestrator, "update_conversation_summary", lambda **kwargs: "resumo")
    monkeypatch.setattr(
        orchestrator,
        "infer_pipeline_state",
        lambda **kwargs: {"pipeline_stage": "novo_lead", "next_action": "-", "lead_temperature": "warm"},
    )
    monkeypatch.setattr(orchestrator, "is_within_business_hours", lambda context, now: True)


# --- C01: saudação -----------------------------------------------------------------


def test_greeting_without_active_flow_follows_each_company_policy(
    company_factory, monkeypatch, deterministic_runtime
) -> None:
    company_a = company_factory("greet-a")
    company_b = company_factory("greet-b")
    _publish(company_a, name="Clínica Aurora")
    _publish(
        company_b,
        name="Studio Beta",
        policies={"conversation": {"greeting_message": "E aí! Aqui é o {company_name}, bora marcar?"}},
    )
    monkeypatch.setattr(orchestrator, "classify_message", _fail("saudação não chama o classificador"))
    monkeypatch.setattr(
        orchestrator,
        "update_conversation_summary",
        lambda **kwargs: (
            "resumo"
            if kwargs.get("allow_model") is False
            else (_ for _ in ()).throw(AssertionError("saudação não chama modelo para memória"))
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "infer_pipeline_state",
        _fail("saudação não chama inferência de pipeline"),
    )

    result_a = _send(company_a, "Oi, tudo bem?")
    result_b = _send(company_b, "Oi, tudo bem?")

    assert result_a["reply"] == "Olá! Aqui é o atendimento da Clínica Aurora. Como posso ajudar?"
    assert result_b["reply"] == "E aí! Aqui é o Studio Beta, bora marcar?"
    for result, company_id in ((result_a, company_a), (result_b, company_b)):
        assert result["action"] == "greeting"
        assert result["auto_reply"] is True
        assert result["handoff_required"] is False
        assert _pending_count(company_id) == 0


def test_disabled_greeting_goes_through_classification(
    company_factory, monkeypatch, deterministic_runtime
) -> None:
    company_id = company_factory("greet-off")
    _publish(company_id, name="Sem Saudação", policies={"conversation": {"greeting_enabled": False}})
    _classify_as(monkeypatch, "indefinido", 0.4)

    result = _send(company_id, "Oi, tudo bem?")

    assert result["action"] == "review"
    assert result["question_type"] == "classification_uncertain"
    assert _pending_count(company_id) == 1


@pytest.mark.parametrize(
    "message",
    ["Oi, tudo bem?", "oiii", "Olá!", "Bom dia", "boa tarde, tudo bem?", "E aí, tudo certo?", "Oi pessoal"],
)
def test_plain_greetings_are_recognized(message: str) -> None:
    assert is_plain_greeting(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Oi, quero agendar",
        "bom dia, preciso cancelar meu horário",
        "tudo bem cancelar amanhã?",
        "sim",
        "não",
        "",
        "Boa tarde, qual o valor?",
        "oi " + "x" * 80,
        # Concordância no meio da conversa não é saudação.
        "beleza",
        "blz",
        "opa",
        "tudo certo",
        "tudo bem",
    ],
)
def test_greeting_with_a_request_is_not_a_plain_greeting(message: str) -> None:
    assert is_plain_greeting(message) is False


# --- C03 e autonomia ------------------------------------------------------------------


def test_ambiguous_message_with_default_policy_is_handed_off_with_notice(
    company_factory, monkeypatch, deterministic_runtime
) -> None:
    company_id = company_factory("ambiguous")
    _publish(company_id, name="Padrão")
    # Mesmo resultado do fallback quando o modelo está indisponível.
    _classify_as(monkeypatch, "indefinido", 0.4)

    result = _send(company_id, "hmm talvez depois vejo isso")

    assert result["action"] == "review"
    assert result["reply"] == DEFAULT_HANDOFF_NOTICE
    assert result["source"] == "policy_handoff_notice"
    assert result["question_type"] == "classification_uncertain"
    assert _pending_count(company_id) == 1


def test_low_confidence_without_handoff_asks_for_clarification(
    company_factory, monkeypatch, deterministic_runtime
) -> None:
    company_id = company_factory("clarify")
    _publish(company_id, name="Esclarece", policies={"handoff_on_low_confidence": False})
    _classify_as(monkeypatch, "indefinido", 0.4)

    result = _send(company_id, "hmm talvez depois vejo isso")

    assert result["action"] == "request_customer_clarification"
    assert result["reply"] == DEFAULT_CLARIFICATION
    assert result["handoff_required"] is False
    assert _pending_count(company_id) == 0


def test_confidence_threshold_comes_from_the_published_policy(
    company_factory, monkeypatch, deterministic_runtime
) -> None:
    lenient = company_factory("lenient")
    strict = company_factory("strict")
    _publish(lenient, name="Flexível", policies={"handoff_confidence_threshold": 0.6})
    _publish(strict, name="Rigorosa", policies={"handoff_confidence_threshold": 0.9})
    _classify_as(monkeypatch, "lead_novo", 0.8)

    lenient_result = _send(lenient, "Quero conhecer o serviço de vocês")
    strict_result = _send(strict, "Quero conhecer o serviço de vocês")

    assert lenient_result["action"] == "auto_reply"
    assert lenient_result["reply"] == "Resposta gerada."
    assert lenient_result["handoff_required"] is False
    assert strict_result["action"] == "review"
    assert strict_result["question_type"] == "classification_uncertain"
    assert strict_result["reply"] == DEFAULT_HANDOFF_NOTICE


def test_intent_not_authorized_for_auto_reply_goes_to_the_team(
    company_factory, monkeypatch, deterministic_runtime
) -> None:
    company_id = company_factory("restricted")
    seed_response_policy = copy.deepcopy(get_seed_company_context(None)["response_policy"])
    _publish(
        company_id,
        name="Restrita",
        context_overrides={
            "response_policy": {**seed_response_policy, "auto_reply_allowed_for": ["lead_novo"]}
        },
    )
    _classify_as(monkeypatch, "suporte", 0.95)
    monkeypatch.setattr(orchestrator, "generate_business_reply", _fail("intenção não autorizada"))

    result = _send(company_id, "Meu acesso parou de funcionar")

    assert result["action"] == "review"
    assert result["question_type"] == "auto_reply_not_allowed"
    assert result["reply"] == DEFAULT_HANDOFF_NOTICE
    assert _pending_count(company_id) == 1


def test_spam_stays_silent_and_creates_no_work(company_factory, monkeypatch, deterministic_runtime) -> None:
    company_id = company_factory("spam")
    _publish(company_id, name="Antispam")
    _classify_as(monkeypatch, "spam", 0.97)

    result = _send(company_id, "GANHE DESCONTO JÁ clique no link")

    assert result["action"] == "ignore"
    assert result["reply"] == ""
    assert result["auto_reply"] is False
    assert _pending_count(company_id) == 0


# --- C04: override, rascunho, fora do horário -------------------------------------------


def test_company_override_prevails_over_vertical_preset(company_factory) -> None:
    clinic_default = company_factory("clinic-default", business_type="clinic")
    clinic_override = company_factory("clinic-override", business_type="clinic")
    _publish(clinic_default, name="Clínica Padrão")
    _publish(
        clinic_override,
        name="Clínica Ajustada",
        policies={
            "handoff_confidence_threshold": 0.5,
            "conversation": {"after_hours_mode": "notice_only"},
        },
    )

    default_policy = resolve_effective_policy(get_company_config(clinic_default))
    override_policy = resolve_effective_policy(get_company_config(clinic_override))

    assert default_policy.autonomy.confidence_threshold == 0.75
    assert override_policy.autonomy.confidence_threshold == 0.5
    assert override_policy.conversation.after_hours_mode == "notice_only"
    # Override parcial da seção preserva as demais chaves do default.
    assert override_policy.conversation.greeting_enabled is True
    assert override_policy.conversation.handoff_notice_message == DEFAULT_HANDOFF_NOTICE


def test_draft_has_no_effect_until_published(company_factory, monkeypatch, deterministic_runtime) -> None:
    company_id = company_factory("draft")
    _publish(company_id, name="Versões", policies={"conversation": {"greeting_message": "Versão publicada."}})
    assert _send(company_id, "oi")["reply"] == "Versão publicada."

    draft = _publish(
        company_id,
        name="Versões",
        policies={"conversation": {"greeting_message": "Versão rascunho."}},
        publish=False,
    )
    assert _send(company_id, "bom dia")["reply"] == "Versão publicada."

    publish_company_config(company_id=company_id, version_id=draft["id"], author="test")
    assert _send(company_id, "boa tarde!")["reply"] == "Versão rascunho."


def test_publication_by_another_process_is_seen_without_waiting_for_cache(company_factory) -> None:
    company_id = company_factory("cross-process")
    _publish(company_id, name="Primeira")
    assert get_company_config(company_id).context["company_name"] == "Primeira"

    draft = _publish(company_id, name="Segunda", publish=False)
    # Publicação direta no banco, sem a invalidação local que o processo que
    # publicou faria: simula API e worker em processos diferentes.
    config_repository.publish_context_version(company_id=company_id, version_id=draft["id"])

    assert get_company_config(company_id).context["company_name"] == "Segunda"


def test_after_hours_notice_only_suspends_bot_and_notifies_once_per_contact(
    company_factory, monkeypatch, deterministic_runtime
) -> None:
    company_id = company_factory("after-hours")
    _publish(company_id, name="Noturna", policies={"conversation": {"after_hours_mode": "notice_only"}})
    monkeypatch.setattr(orchestrator, "is_within_business_hours", lambda context, now: False)
    monkeypatch.setattr(orchestrator, "classify_message", _fail("bot suspenso fora do horário"))
    monkeypatch.setattr(orchestrator, "handle_conversation_workflow", _fail("bot suspenso fora do horário"))

    first = _send(company_id, "Quero agendar amanhã", contact_id="5511900000100")
    second = _send(company_id, "Alguém aí?", contact_id="5511900000100")
    other_contact = _send(company_id, "Olá", contact_id="5511900000101")

    assert first["action"] == "after_hours"
    assert first["reply"] == DEFAULT_AFTER_HOURS
    assert first["question_type"] == "after_hours_contact"
    assert second["reply"] == ""
    assert second["auto_reply"] is False
    assert second["handoff_required"] is False
    assert other_contact["reply"] == DEFAULT_AFTER_HOURS
    assert _pending_count(company_id) == 2


def test_repeated_messages_do_not_repeat_the_handoff_notice(
    company_factory, monkeypatch, deterministic_runtime
) -> None:
    company_id = company_factory("notice-cooldown")
    _publish(company_id, name="Sem Repetição")
    _classify_as(monkeypatch, "indefinido", 0.4)

    first = _send(company_id, "hmm", contact_id="5511900000200")
    second = _send(company_id, "ok", contact_id="5511900000200")
    other_contact = _send(company_id, "hmm", contact_id="5511900000201")

    assert first["reply"] == DEFAULT_HANDOFF_NOTICE
    assert second["reply"] == ""
    assert second["auto_reply"] is False
    assert second["handoff_required"] is True
    assert other_contact["reply"] == DEFAULT_HANDOFF_NOTICE


@pytest.mark.parametrize("hours", ["8h às 18h", "18:00-02:00", "08:00-24:00", "18:60-19:00", ""])
def test_malformed_business_hours_are_rejected_on_write(company_factory, hours) -> None:
    company_id = company_factory("bad-hours")
    context = _context("Horário Ruim", business_hours={"timezone": "America/Sao_Paulo", "weekdays": hours})

    with pytest.raises(CompanyConfigError, match="não é um horário válido"):
        save_company_config(company_id=company_id, context=context)


@pytest.mark.parametrize("hours", ["8h às 18h", "18:00-02:00", "08:00-24:00", "18:60-19:00"])
def test_unreadable_business_hours_count_as_open_and_never_raise(hours) -> None:
    # Versão antiga gravada antes da validação: nunca derruba nem silencia o bot.
    context = {"business_hours": {"timezone": "America/Sao_Paulo", "weekdays": hours}}
    monday_noon = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)

    assert is_within_business_hours(context, monday_noon) is True


def test_after_hours_continue_mode_keeps_bot_and_warns_on_handoff(
    company_factory, monkeypatch, deterministic_runtime
) -> None:
    company_id = company_factory("after-hours-continue")
    _publish(company_id, name="Contínua")
    monkeypatch.setattr(orchestrator, "is_within_business_hours", lambda context, now: False)
    _classify_as(monkeypatch, "financeiro", 0.95)

    result = _send(company_id, "Tenho um boleto em aberto")

    assert result["action"] == "handoff_human"
    assert result["question_type"] == "handoff_required"
    assert result["reply"] == f"{DEFAULT_HANDOFF_NOTICE} {DEFAULT_AFTER_HOURS}"


def test_business_hours_use_company_timezone_day_rollover_and_closed_days() -> None:
    context = {
        "business_hours": {
            "timezone": "America/Sao_Paulo",
            "weekdays": "08:00-18:00",
            "saturday": "09:00-12:00",
            "sunday": "fechado",
        }
    }
    # 14/09/2026 é segunda; São Paulo é UTC-3.
    assert is_within_business_hours(context, datetime(2026, 9, 14, 11, 0, tzinfo=UTC))
    assert not is_within_business_hours(context, datetime(2026, 9, 14, 10, 59, tzinfo=UTC))
    assert not is_within_business_hours(context, datetime(2026, 9, 14, 21, 0, tzinfo=UTC))
    # Domingo aberto em UTC, fechado no fuso da empresa.
    assert not is_within_business_hours(context, datetime(2026, 9, 13, 18, 0, tzinfo=UTC))
    # Sábado 02:30 UTC ainda é sexta 23:30 local: fechado.
    assert not is_within_business_hours(context, datetime(2026, 9, 19, 2, 30, tzinfo=UTC))
    assert is_within_business_hours(context, datetime(2026, 9, 19, 12, 30, tzinfo=UTC))
    # Sem horário configurado, o modo fora do horário não silencia por suposição.
    assert is_within_business_hours({}, datetime(2026, 9, 13, 3, 0, tzinfo=UTC))
    with pytest.raises(ValueError):
        is_within_business_hours(context, datetime(2026, 9, 14, 11, 0))  # noqa: DTZ001 - recusa de datetime ingênuo


# --- legado e identidade -------------------------------------------------------------------


def test_legacy_context_of_the_same_company_is_used_until_a_version_exists(company_factory) -> None:
    repository = RuntimeRepository()
    company_id = company_factory("legacy")
    slug = repository.get_company(company_id)["slug"]
    save_company_context(_context("Legado Ltda"), company_slug=slug)

    legacy_config = get_company_config(company_id)
    assert legacy_config.source == "legacy_context"
    assert legacy_config.context["company_name"] == "Legado Ltda"

    stored = publish_legacy_context_write(
        company_slug=slug,
        context=_context("Legado Publicado"),
        author="test",
        change_note="escrita legada",
    )
    assert stored is not None
    published_config = get_company_config(company_id)
    assert published_config.source == "database"
    assert published_config.version == stored["version"]
    assert published_config.context["company_name"] == "Legado Publicado"


def test_legacy_row_of_a_colliding_slug_never_lends_identity(monkeypatch) -> None:
    from uuid import uuid4

    repository = RuntimeRepository()
    suffix = uuid4().hex[:8]
    upper_id = repository.create_company(slug=f"Colide-{suffix}", name="Maiúscula", business_type="general")
    lower_id = repository.create_company(slug=f"colide-{suffix}", name="Minúscula", business_type="general")
    # Chave legada normalizada "colide-<suffix>", vinculada à empresa "Colide-...".
    save_company_context(_context("Contexto da Maiúscula"), company_slug=f"Colide-{suffix}")

    assert get_company_config(upper_id).source == "legacy_context"
    lower_config = get_company_config(lower_id)
    assert lower_config.source == "global_template"
    assert lower_config.context["company_name"] != "Contexto da Maiúscula"

    monkeypatch.setattr(company_config_service, "is_production_like", lambda: True)
    with pytest.raises(CompanyConfigError):
        get_company_config(lower_id, use_cache=False)


def test_legacy_write_keeps_the_published_policy(company_factory) -> None:
    repository = RuntimeRepository()
    company_id = company_factory("legacy-policy")
    slug = repository.get_company(company_id)["slug"]
    _publish(company_id, name="Com Política", policies={"handoff_confidence_threshold": 0.85})

    publish_legacy_context_write(
        company_slug=slug,
        context=_context("Contexto Novo"),
        author="test",
        change_note="escrita legada",
    )

    policy = resolve_effective_policy(get_company_config(company_id))
    assert policy.company_name == "Contexto Novo"
    assert policy.autonomy.confidence_threshold == 0.85


def test_legacy_admin_put_publishes_a_version_used_by_runtime(company_factory, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "enable_admin_auth", False)
    company_id = company_factory("legacy-put")
    slug = RuntimeRepository().get_company(company_id)["slug"]
    client = TestClient(app)

    response = client.put(
        "/admin/company-context",
        params={"company_slug": slug},
        json={"data": _context("Via API Legada")},
    )
    assert response.status_code == 200
    config = get_company_config(company_id)
    assert config.source == "database"
    assert config.context["company_name"] == "Via API Legada"

    invalid = _context(
        "Inválida",
        response_policy={"auto_reply_allowed_for": ["financeiro"], "handoff_required_for": ["financeiro"]},
    )
    rejected = client.put("/admin/company-context", params={"company_slug": slug}, json={"data": invalid})
    assert rejected.status_code == 400
    # Nada gravado: nem legado nem versão.
    assert get_company_config(company_id).context["company_name"] == "Via API Legada"


def test_company_without_own_context_never_borrows_another_identity(company_factory, monkeypatch) -> None:
    company_id = company_factory("orphan")

    # Fora de produção: template global explicitamente marcado, nunca como
    # se fosse contexto legado da empresa.
    assert get_company_config(company_id).source == "global_template"

    monkeypatch.setattr(company_config_service, "is_production_like", lambda: True)
    with pytest.raises(CompanyConfigError):
        get_company_config(company_id, use_cache=False)


def test_unresolved_company_is_refused_in_production(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "is_production_like", lambda: True)

    with pytest.raises(orchestrator.UnresolvedCompanyError):
        orchestrator.process_incoming_message(
            contact_id="5511900000999",
            contact_name=None,
            message="oi",
            company_slug="empresa-que-nao-existe-p01",
        )


# --- validação na escrita e publicação --------------------------------------------------------


@pytest.mark.parametrize(
    ("policies", "fragment"),
    [
        ({"handoff_confidence_treshold": 0.7}, "chaves desconhecidas"),
        ({"handoff_confidence_threshold": 1.5}, "entre 0 e 1"),
        ({"handoff_confidence_threshold": True}, "entre 0 e 1"),
        ({"conversation": {"after_hours_mode": "sleep"}}, "after_hours_mode"),
        ({"conversation": {"greeting_message": "   "}}, "texto não vazio"),
        ({"conversation": {"greting_enabled": True}}, "chaves desconhecidas"),
        ({"booking_rules": {"cancellation": {"min_notice_minutes": -1}}}, "min_notice_minutes"),
        ({"booking_rules": {"refund": {}}}, "chaves desconhecidas"),
        ({"confirmation": {"proposal_ttl_minutes": 0}}, "proposal_ttl_minutes"),
    ],
)
def test_invalid_policies_are_rejected_before_writing(company_factory, policies, fragment) -> None:
    company_id = company_factory("invalid-policy")
    before = config_repository.list_context_versions(company_id=company_id)

    with pytest.raises(CompanyConfigError, match=fragment):
        save_company_config(company_id=company_id, context=_context("Inválida"), policies=policies)

    assert config_repository.list_context_versions(company_id=company_id) == before


@pytest.mark.parametrize(
    ("context_patch", "fragment"),
    [
        (
            {"response_policy": {"auto_reply_allowed_for": ["financeiro"], "handoff_required_for": ["financeiro"]}},
            "ao mesmo tempo",
        ),
        ({"response_policy": {"auto_reply_allowed_for": ["vendas_magicas"]}}, "desconhecida"),
        ({"response_policy": {"auto_reply_allowed_for": ["spam"]}}, "não pode incluir"),
        (
            {"business_hours": {"timezone": "America/Atlantida", "weekdays": "08:00-18:00"}},
            "fuso IANA",
        ),
    ],
)
def test_invalid_context_policy_fields_are_rejected(company_factory, context_patch, fragment) -> None:
    company_id = company_factory("invalid-context")

    with pytest.raises(CompanyConfigError, match=fragment):
        save_company_config(company_id=company_id, context=_context("Inválida", **context_patch))


def test_invalid_draft_cannot_be_published(company_factory) -> None:
    company_id = company_factory("bad-draft")
    _publish(company_id, name="Válida")
    invalid_draft = config_repository.create_context_version(
        company_id=company_id,
        context=_context("Inválida"),
        capabilities={},
        policies={"handoff_confidence_threshold": 7},
        vertical_pack="general",
        status=config_repository.STATUS_DRAFT,
    )

    with pytest.raises(CompanyConfigInvalidError):
        publish_company_config(company_id=company_id, version_id=invalid_draft["id"])

    assert get_company_config(company_id).context["company_name"] == "Válida"


# --- regra de cancelamento/remarcação (consumida pela agenda em P03/P04) ------------------------


def test_booking_change_rule_boundaries_and_timezones() -> None:
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    rule = BookingChangeRule(allowed=True, min_notice_minutes=120, outside_window="handoff")

    exactly_at_notice = evaluate_booking_change(rule, starts_at=now + timedelta(minutes=120), now=now)
    assert exactly_at_notice.allowed
    inside_notice = evaluate_booking_change(rule, starts_at=now + timedelta(minutes=119), now=now)
    assert (inside_notice.outcome, inside_notice.reason) == ("requires_human", "inside_minimum_notice")
    started = evaluate_booking_change(rule, starts_at=now, now=now)
    assert (started.outcome, started.reason) == ("requires_human", "booking_already_started")

    deny_rule = BookingChangeRule(allowed=True, min_notice_minutes=120, outside_window="deny")
    assert evaluate_booking_change(deny_rule, starts_at=now, now=now).outcome == "denied"
    closed_rule = BookingChangeRule(allowed=False, min_notice_minutes=0, outside_window="handoff")
    assert evaluate_booking_change(closed_rule, starts_at=now + timedelta(days=3), now=now).reason == (
        "change_not_allowed"
    )

    # 11:00 em São Paulo = 14:00 UTC: exatamente 120 minutos depois de `now`.
    local_start = datetime(2026, 9, 14, 11, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert evaluate_booking_change(rule, starts_at=local_start, now=now).allowed

    with pytest.raises(ValueError):
        evaluate_booking_change(rule, starts_at=datetime(2026, 9, 15, 12, 0), now=now)  # noqa: DTZ001 - recusa de datetime ingênuo
