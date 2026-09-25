"""P02 — conversa, confirmação e mudança de intenção (C02, C05 a C07, A01 a A04).

Banco real (SQLite por padrão; PostgreSQL com TEST_DATABASE_URL), agenda `mock`
e relógio do workflow controlado. Nenhuma rede, LLM ou canal externo. Os efeitos
de agenda são as funções reais embrulhadas por contadores; falhas são injetadas
por monkeypatch só onde o cenário exige.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.company_context import get_seed_company_context
from app.core.config import settings
from app.services import conversation_workflow_service as workflow
from app.services.appointment_queue_service import AppointmentQueueError
from app.services.calendar_service import BookingConflictError, BookingPolicyError
from app.services.company_config_service import save_company_config
from app.services.company_policy_service import build_effective_policy
from app.storage import operation_repository as ops
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()
TZ = "America/Sao_Paulo"
# Segunda-feira, 08:00 local. "amanhã" = terça 09/04; quarta 10/04; sexta 12/04.
START = datetime.fromisoformat("2030-04-08T08:00:00-03:00")
_AUTO = object()


def _control():
    from app.services import conversation_control_service

    return conversation_control_service


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _contact() -> str:
    return f"55119{uuid4().int % 10**8:08d}"


class Clock:
    def __init__(self) -> None:
        self.now = START

    def advance(self, **delta: int) -> None:
        self.now += timedelta(**delta)

    def local(self, timezone_obj: Any) -> datetime:
        return self.now.astimezone(timezone_obj)


@dataclass
class Harness:
    company_id: int
    context: dict[str, Any]
    clock: Clock

    def send(
        self,
        contact_id: str,
        message: str,
        *,
        ref: Any = _AUTO,
        policy: Any = None,
        context: dict[str, Any] | None = None,
        contact_name: str = "Cliente Teste",
    ) -> workflow.WorkflowResult:
        # Referência distinta por mensagem, como o worker faz com o id do provider.
        message_ref = f"test:{uuid4().hex}" if ref is _AUTO else ref
        return workflow.handle_conversation_workflow(
            contact_id=contact_id,
            contact_name=contact_name,
            message=message,
            company_id=self.company_id,
            company_context=context or self.context,
            policy=policy,
            message_ref=message_ref,
        )

    def booking(self, contact_id: str, local_start: str, minutes: int = 60) -> int:
        starts = datetime.fromisoformat(local_start)
        return repository.create_appointment_booking(
            provider="mock",
            title="Consultoria inicial",
            starts_at=starts.astimezone(UTC).isoformat(),
            ends_at=(starts + timedelta(minutes=minutes)).astimezone(UTC).isoformat(),
            timezone=TZ,
            company_id=self.company_id,
            contact_id=contact_id,
        )

    def booking_row(self, booking_id: int) -> dict[str, Any]:
        row = repository.get_appointment_booking(booking_id, company_id=self.company_id)
        assert row is not None
        return row

    def bookings(self, contact_id: str) -> list[dict[str, Any]]:
        return repository.get_appointment_bookings(company_id=self.company_id, contact_id=contact_id)

    def operations(self, contact_id: str) -> list[dict[str, Any]]:
        return list(reversed(ops.list_operations(company_id=self.company_id, contact_id=contact_id)))

    def flow(self, contact_id: str) -> dict[str, Any] | None:
        return repository.get_active_conversation_flow(contact_id=contact_id, company_id=self.company_id)

    def policy(self, policies: dict[str, Any] | None = None) -> Any:
        return build_effective_policy(context=self.context, policies=policies)


def _context() -> dict[str, Any]:
    context = copy.deepcopy(get_seed_company_context(None))
    context["company_name"] = "Clinica P02"
    context["catalog"]["items"].append(
        {
            "sku": "AVAL-001",
            "name": "Avaliação completa",
            "category": "consultoria",
            "type": "service",
            "schedule_required": True,
            "duration": "60 minutos",
        }
    )
    return context


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    current = Clock()
    monkeypatch.setattr(workflow, "_get_current_time", current.local)
    return current


@pytest.fixture()
def env(company_factory, monkeypatch, clock) -> Harness:
    monkeypatch.setattr(settings, "calendar_provider", "mock")
    monkeypatch.setattr(settings, "classification_provider", "mock")
    company_id = company_factory("p02")
    context = _context()
    save_company_config(company_id=company_id, context=context, author="test", publish=True)
    return Harness(company_id=company_id, context=context, clock=clock)


@pytest.fixture()
def effects(monkeypatch) -> dict[str, int]:
    """Conta chamadas às funções de agenda sem trocar o comportamento real."""
    calls = {"create": 0, "cancel": 0, "reschedule": 0}

    def _spy(name: str, attribute: str) -> None:
        real = getattr(workflow, attribute)

        def _wrapped(**kwargs: Any) -> Any:
            calls[name] += 1
            return real(**kwargs)

        monkeypatch.setattr(workflow, attribute, _wrapped)

    _spy("create", "create_calendar_booking")
    _spy("cancel", "release_booking_slot")
    _spy("reschedule", "reschedule_calendar_booking")
    return calls


def _statuses(env: Harness, contact_id: str) -> list[str]:
    return [str(operation["status"]) for operation in env.operations(contact_id)]


def _open_proposals(env: Harness, contact_id: str) -> list[dict[str, Any]]:
    return [operation for operation in env.operations(contact_id) if operation["status"] == "proposed"]


def _no_effect_states(env: Harness, contact_id: str) -> None:
    assert not [
        operation
        for operation in env.operations(contact_id)
        if operation["status"] in {"confirmed", "executing", "completed", "review"}
    ]


# ── interpretador de controle (puro) ──────────────────────────────────


CONFIRM_PHRASES = [
    "sim",
    "Sim!",
    "SIM.",
    "simmm",
    "s",
    "sim, pode confirmar",
    "pode confirmar",
    "Pode confirmar, por favor",
    "confirmado",
    "confirmo",
    "ok",
    "ok, obrigado",
    "isso",
    "isso mesmo",
    "pode",
    "pode sim",
    "tá bom",
    "claro",
    "com certeza",
    "perfeito",
    "fechado",
    "combinado",
    "beleza",
    "sim, obrigada",
]
REJECT_PHRASES = [
    "não",
    "nao",
    "Não.",
    "não quero confirmar",
    "nao quero",
    "não, obrigado",
    "melhor não",
    "agora não",
    "deixa pra lá",
    "desisto",
    "esquece",
    "nem pensar",
    "negativo",
    "n",
    "não confirma não",
    "não quero às 16h",
]
QUESTION_PHRASES = [
    "assim está bom?",
    "isso cancela todos?",
    "isso cancela todos",
    "sim?",
    "confirma?",
    "será que dá?",
    "quanto custa?",
    "qual o endereço",
    "e se eu não puder ir?",
    "como funciona",
]
UNCERTAIN_PHRASES = [
    "",
    "   ",
    "assim",
    "acho que sim",
    "talvez",
    "pode ser",
    "sim mas",
    "hmm",
    "👍",
    "oi",
    "sim, às 10h ou às 11h",
    "simples",
    "sim, e o valor",
]


@pytest.mark.parametrize("phrase", CONFIRM_PHRASES)
def test_control_confirm_only_for_unequivocal_phrases(phrase: str) -> None:
    result = _control().interpret_control_message(phrase, kind="create")
    assert result.intent == "confirm", (phrase, result)
    assert result.authorizes is True


@pytest.mark.parametrize("phrase", REJECT_PHRASES)
@pytest.mark.parametrize("kind", ["create", "cancel", "reschedule"])
def test_control_negation_has_priority(phrase: str, kind: str) -> None:
    result = _control().interpret_control_message(phrase, kind=kind)
    assert result.intent == "reject", (phrase, kind, result)
    assert result.authorizes is False


@pytest.mark.parametrize("phrase", QUESTION_PHRASES)
@pytest.mark.parametrize("kind", ["create", "cancel", "reschedule"])
def test_control_questions_never_authorize(phrase: str, kind: str) -> None:
    result = _control().interpret_control_message(phrase, kind=kind)
    assert result.intent == "question", (phrase, kind, result)
    assert result.authorizes is False


@pytest.mark.parametrize("phrase", UNCERTAIN_PHRASES)
def test_control_ambiguity_is_uncertain(phrase: str) -> None:
    result = _control().interpret_control_message(phrase, kind="create")
    assert result.intent == "uncertain", (phrase, result)
    assert result.authorizes is False


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("sim, mas às 16h", {"times": ("16:00",)}),
        ("Sim, mas as 16:30", {"times": ("16:30",)}),
        ("pode ser amanhã às 10h?", {"times": ("10:00",), "dates": ("amanha",)}),
        ("não, prefiro às 15h30", {"times": ("15:30",)}),
        ("prefiro de tarde", {"periods": ("afternoon",)}),
        ("melhor de manhã", {"periods": ("morning",)}),
        ("quero outro horário", {"other_option": True}),
        ("sim, mas quero a Avaliação completa", {"services": ("Avaliação completa",)}),
        ("a segunda opção", {"choice": 2}),
        ("2", {"choice": 2}),
        ("não, o de sexta", {"dates": ("sexta",)}),
        ("pode ser dia 12/04?", {"dates": ("12/04",)}),
        ("depois de amanhã", {"dates": ("depois de amanha",)}),
    ],
)
def test_control_change_carries_detected_details(phrase: str, expected: dict[str, Any]) -> None:
    result = _control().interpret_control_message(
        phrase,
        kind="create",
        service_names=["Consultoria inicial", "Avaliação completa"],
    )
    assert result.intent == "change", (phrase, result)
    assert result.authorizes is False
    for field, value in expected.items():
        assert getattr(result.change, field) == value, (phrase, field, result.change)


@pytest.mark.parametrize(
    ("phrase", "kind", "intent"),
    [
        ("pode cancelar", "cancel", "confirm"),
        ("sim, pode cancelar", "cancel", "confirm"),
        ("pode cancelar", "create", "reject"),
        ("cancela", "reschedule", "reject"),
        ("pode remarcar", "reschedule", "confirm"),
        ("pode marcar", "create", "confirm"),
        ("pode manter", "cancel", "reject"),
        ("não, pode manter", "cancel", "reject"),
        ("prefiro manter", "reschedule", "reject"),
        ("pode manter", "create", "uncertain"),
        ("sim, cancela todos", "cancel", "uncertain"),
        ("cancela tudo", "cancel", "uncertain"),
        ("cancela os dois", "cancel", "uncertain"),
        ("boa tarde", "create", "uncertain"),
    ],
)
def test_control_vocabulary_depends_on_the_proposal_kind(phrase: str, kind: str, intent: str) -> None:
    assert _control().interpret_control_message(phrase, kind=kind).intent == intent


def test_control_is_word_bounded_and_deterministic() -> None:
    control = _control()
    # "assim" contém "sim", "isso" está em "isso cancela"; nada disso é aceite.
    for phrase in ("assim", "assim sim?", "sim? assim", "simpatico", "okay?"):
        assert control.interpret_control_message(phrase, kind="create").intent != "confirm", phrase
    first = control.interpret_control_message("Sim, pode confirmar", kind="create")
    assert all(control.interpret_control_message("Sim, pode confirmar", kind="create") == first for _ in range(5))


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("cancelar", True),
        ("desisto", True),
        ("deixa pra depois", True),
        ("não quero mais", True),
        ("não", True),
        ("não, obrigado", True),
        ("não sei ainda", False),
        ("amanhã não posso, pode ser sexta?", False),
        ("quero cancelar meu agendamento", True),
        ("assim", False),
    ],
)
def test_abandon_request_detection(phrase: str, expected: bool) -> None:
    assert _control().is_abandon_request(phrase) is expected


# ── C05/C06: regressão da confirmação incorreta de cancelamento ───────


@pytest.mark.parametrize("phrase", ["não quero confirmar", "assim está bom?", "isso cancela todos?"])
def test_c05_non_confirmation_after_cancel_request_never_cancels(env: Harness, effects, phrase: str) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")

    first = env.send(contact, "Quero cancelar meu agendamento")
    assert first.action == "booking_cancel_confirmation_requested"

    second = env.send(contact, phrase)
    assert second.handled is True
    assert second.action != "booking_cancelled"
    assert effects["cancel"] == 0
    assert env.booking_row(booking_id)["status"] == "confirmed"
    _no_effect_states(env, contact)


@pytest.mark.parametrize("phrase", ["assim está bom?", "isso cancela todos?"])
def test_c05_question_keeps_the_proposal_and_explains_scope(env: Harness, effects, phrase: str) -> None:
    contact = _contact()
    env.booking(contact, "2030-04-10T10:00:00-03:00")
    env.send(contact, "Quero cancelar meu agendamento")
    [proposal] = _open_proposals(env, contact)

    result = env.send(contact, phrase)
    assert result.action == "booking_cancel_confirmation_requested"
    assert "sim" in result.reply.lower()
    assert [operation["id"] for operation in _open_proposals(env, contact)] == [proposal["id"]]

    # A proposta continua valendo: um "sim" inequívoco depois executa uma vez.
    confirmed = env.send(contact, "sim")
    assert confirmed.action == "booking_cancelled"
    assert effects["cancel"] == 1


def test_c05_refusal_rejects_the_proposal(env: Harness, effects) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")
    env.send(contact, "Quero cancelar meu agendamento")

    result = env.send(contact, "não quero confirmar")
    assert result.action == "booking_change_kept"
    assert _statuses(env, contact) == ["rejected"]
    assert env.booking_row(booking_id)["status"] == "confirmed"
    assert env.flow(contact) is None


def test_c06_keep_after_cancel_request_preserves_booking_and_closes_flow(env: Harness, effects) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")
    env.send(contact, "Quero cancelar meu agendamento")

    result = env.send(contact, "não, pode manter")
    assert result.action == "booking_change_kept"
    assert "não cancelei" in result.reply.lower()
    assert "continua" in result.reply.lower()
    assert effects["cancel"] == 0
    assert env.booking_row(booking_id)["status"] == "confirmed"
    assert env.flow(contact) is None
    assert _statuses(env, contact) == ["rejected"]

    # Encerrado de verdade: um "sim" solto depois não ressuscita o cancelamento.
    later = env.send(contact, "sim")
    assert later.action != "booking_cancelled"
    assert effects["cancel"] == 0


@pytest.mark.parametrize("phrase", ["não quero confirmar", "assim está bom?", "isso remarca todos?", "não, pode manter"])
def test_c05_reschedule_confirmation_variants_do_not_reschedule(env: Harness, effects, phrase: str) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")
    original = env.booking_row(booking_id)

    first = env.send(contact, "Quero remarcar meu horário para amanhã às 15h")
    assert first.action == "booking_reschedule_confirmation_requested"

    second = env.send(contact, phrase)
    assert second.action != "booking_rescheduled"
    assert effects["reschedule"] == 0
    row = env.booking_row(booking_id)
    assert _aware(row["starts_at"]) == _aware(original["starts_at"])
    _no_effect_states(env, contact)


@pytest.mark.parametrize("phrase", ["não quero confirmar", "assim está bom?", "isso confirma todos?", "não"])
def test_c05_new_booking_confirmation_variants_do_not_book(env: Harness, effects, phrase: str) -> None:
    contact = _contact()
    first = env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h")
    assert first.action == "schedule_confirmation_requested"

    second = env.send(contact, phrase)
    assert second.action != "schedule_confirmed"
    assert effects["create"] == 0
    assert env.bookings(contact) == []
    _no_effect_states(env, contact)


def _operator_setup(env: Harness) -> tuple[str, str, dict[str, Any]]:
    operator = _contact()
    customer = _contact()
    suffix = uuid4().hex[:8]
    repository.save_contact(customer, f"Zeca {suffix}", company_id=env.company_id)
    context = {**env.context, "internal_operation": {"operator_contact_ids": [operator]}}
    return operator, customer, {"context": context, "command": f"Marque o cliente Zeca {suffix} amanhã às 10h"}


def test_c05_operator_flow_contact_and_slot_confirmation(env: Harness, effects) -> None:
    operator, customer, data = _operator_setup(env)
    context = data["context"]

    first = env.send(operator, data["command"], context=context, contact_name="Dono")
    assert first.action == "operator_customer_confirmation_requested"

    # "isso não" contém "isso": não pode selecionar o contato.
    refused = env.send(operator, "isso não", context=context, contact_name="Dono")
    assert refused.action != "operator_booking_confirmation_requested"
    assert effects["create"] == 0

    operator, customer, data = _operator_setup(env)
    context = data["context"]
    env.send(operator, data["command"], context=context, contact_name="Dono")
    selected = env.send(operator, "sim", context=context, contact_name="Dono")
    assert selected.action == "operator_booking_confirmation_requested"

    # Pergunta mantém a proposta aberta; nada é criado.
    for phrase in ("assim está bom?", "isso confirma?"):
        result = env.send(operator, phrase, context=context, contact_name="Dono")
        assert result.action == "operator_booking_confirmation_requested", phrase
    assert effects["create"] == 0
    assert len(_open_proposals(env, operator)) == 1

    # Negativa recusa a proposta e encerra o fluxo do operador.
    refused = env.send(operator, "não quero confirmar", context=context, contact_name="Dono")
    assert refused.action == "operator_booking_abandoned"
    assert env.flow(operator) is None
    assert _statuses(env, operator) == ["rejected"]
    assert effects["create"] == 0
    assert env.bookings(customer) == []

    operator, customer, data = _operator_setup(env)
    context = data["context"]
    env.send(operator, data["command"], context=context, contact_name="Dono")
    assert env.send(operator, "sim", context=context, contact_name="Dono").action == (
        "operator_booking_confirmation_requested"
    )
    created = env.send(operator, "sim, pode confirmar", context=context, contact_name="Dono")
    assert created.action == "operator_booking_created"
    [booking] = env.bookings(customer)
    assert _aware(booking["starts_at"]) == datetime.fromisoformat("2030-04-09T13:00:00+00:00")
    [operation] = [op for op in env.operations(operator) if op["status"] == "completed"]
    assert operation["source"] == "operator"
    assert operation["booking_id"] == booking["id"]


# ── C02: saudação durante escolha de horário ──────────────────────────


def test_c02_greeting_during_slot_choice_preserves_state(env: Harness, effects) -> None:
    contact = _contact()
    presented = env.send(contact, "Quero agendar a Consultoria inicial amanhã")
    assert presented.action == "schedule_options_presented"
    flow_before = env.flow(contact)
    assert flow_before is not None
    [proposal] = _open_proposals(env, contact)

    for greeting in ("Oi", "bom dia", "boa tarde", "Oi, tudo bem?"):
        result = env.send(contact, greeting)
        assert result.handled is True
        assert result.action == "scheduling_resumed", greeting
        assert result.handoff_question_type is None
        flow_after = env.flow(contact)
        assert flow_after is not None and flow_after["id"] == flow_before["id"]
        assert flow_after["state"] == flow_before["state"], greeting
        assert [operation["id"] for operation in _open_proposals(env, contact)] == [proposal["id"]]
    assert effects["create"] == 0

    confirmed = env.send(contact, "sim")
    assert confirmed.action == "schedule_confirmed"
    [booking] = env.bookings(contact)
    assert _aware(booking["starts_at"]) == _aware(proposal["params"]["starts_at"])


def test_c02_greeting_while_collecting_date_keeps_step(env: Harness, effects) -> None:
    contact = _contact()
    asked = env.send(contact, "Quero agendar a Consultoria inicial")
    state_before = env.flow(contact)["state"]
    result = env.send(contact, "bom dia")
    assert result.action == "scheduling_resumed"
    assert "dia" in result.reply.lower()
    assert env.flow(contact)["state"] == state_before
    assert asked.handled is True


# ── C07: mudança invalida a proposta anterior ─────────────────────────


def test_c07_yes_but_other_time_supersedes_and_requires_new_confirmation(env: Harness, effects) -> None:
    contact = _contact()
    env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h")
    [first] = env.operations(contact)

    changed = env.send(contact, "sim, mas às 16h")
    assert changed.action == "schedule_confirmation_requested"
    assert "16:00" in changed.reply
    assert effects["create"] == 0
    operations = env.operations(contact)
    assert [operation["status"] for operation in operations] == ["superseded", "proposed"]
    assert _aware(operations[1]["params"]["starts_at"]) == datetime.fromisoformat("2030-04-09T19:00:00+00:00")

    # O hash antigo não confirma mais nada.
    stale = ops.accept_proposal(
        company_id=env.company_id,
        contact_id=contact,
        operation_id=first["id"],
        params_hash=first["params_hash"],
        confirmation_ref=f"test:{uuid4().hex}",
        now=env.clock.now,
    )
    assert stale.outcome == "superseded"

    confirmed = env.send(contact, "sim")
    assert confirmed.action == "schedule_confirmed"
    [booking] = env.bookings(contact)
    assert _aware(booking["starts_at"]) == datetime.fromisoformat("2030-04-09T19:00:00+00:00")
    assert effects["create"] == 1


def test_c07_service_change_creates_new_proposal(env: Harness, effects) -> None:
    contact = _contact()
    env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h")
    changed = env.send(contact, "sim, mas quero a Avaliação completa")
    assert changed.action in {"schedule_confirmation_requested", "schedule_options_presented"}
    assert effects["create"] == 0
    operations = env.operations(contact)
    assert [operation["status"] for operation in operations] == ["superseded", "proposed"]
    assert operations[1]["params"]["service_name"] == "Avaliação completa"


def test_c07_target_change_between_two_bookings(env: Harness, effects) -> None:
    contact = _contact()
    wednesday = env.booking(contact, "2030-04-10T10:00:00-03:00")
    friday = env.booking(contact, "2030-04-12T15:00:00-03:00")

    env.send(contact, "Quero cancelar meu agendamento")
    first = env.send(contact, "1")
    assert first.action == "booking_cancel_confirmation_requested"
    assert _open_proposals(env, contact)[0]["booking_id"] == wednesday

    switched = env.send(contact, "não, o de sexta")
    assert switched.action == "booking_cancel_confirmation_requested"
    assert effects["cancel"] == 0
    assert _statuses(env, contact) == ["superseded", "proposed"]
    assert _open_proposals(env, contact)[0]["booking_id"] == friday

    done = env.send(contact, "sim")
    assert done.action == "booking_cancelled"
    assert env.booking_row(friday)["status"] == "cancelled"
    assert env.booking_row(wednesday)["status"] == "confirmed"


# ── A01: booking e operação batem com o apresentado ───────────────────


def test_a01_booking_and_operation_match_presented_slot(env: Harness, effects) -> None:
    contact = _contact()
    presented = env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h")
    assert "09/04 às 10:00" in presented.reply
    [proposal] = env.operations(contact)
    assert proposal["kind"] == "create"
    assert proposal["params"]["timezone"] == TZ
    assert _aware(proposal["params"]["starts_at"]) == datetime.fromisoformat("2030-04-09T13:00:00+00:00")
    assert env.flow(contact)["state"]["proposal"]["operation_id"] == proposal["id"]

    confirmed = env.send(contact, "sim")
    assert confirmed.action == "schedule_confirmed"
    assert "09/04 às 10:00" in confirmed.reply
    [booking] = env.bookings(contact)
    assert _aware(booking["starts_at"]) == _aware(proposal["params"]["starts_at"])
    assert _aware(booking["ends_at"]) == _aware(proposal["params"]["ends_at"])
    [operation] = env.operations(contact)
    assert operation["status"] == "completed"
    assert operation["booking_id"] == booking["id"]
    assert operation["confirmation_ref"].startswith("test:")
    assert operation["result"]["booking_id"] == booking["id"]
    assert confirmed.metadata["booking_id"] == booking["id"]
    assert env.flow(contact) is None


def test_a01_sync_api_path_without_message_ref_still_uses_a_proposal(env: Harness, effects) -> None:
    contact = _contact()
    env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h", ref=None)
    confirmed = env.send(contact, "sim", ref=None)
    assert confirmed.action == "schedule_confirmed"
    [operation] = env.operations(contact)
    assert operation["status"] == "completed"
    assert operation["confirmation_ref"].startswith("conversation:")


def test_a01_reschedule_executes_the_proposed_slot(env: Harness, effects) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")
    env.send(contact, "Quero remarcar meu horário para amanhã às 15h")
    [proposal] = env.operations(contact)
    assert proposal["kind"] == "reschedule"
    assert proposal["booking_id"] == booking_id

    done = env.send(contact, "sim")
    assert done.action == "booking_rescheduled"
    row = env.booking_row(booking_id)
    assert _aware(row["starts_at"]) == datetime.fromisoformat("2030-04-09T18:00:00+00:00")
    assert env.operations(contact)[0]["status"] == "completed"


# ── A02: dois bookings exigem seleção ─────────────────────────────────


def test_a02_two_bookings_require_selection_and_only_selected_changes(env: Harness, effects) -> None:
    contact = _contact()
    wednesday = env.booking(contact, "2030-04-10T10:00:00-03:00")
    friday = env.booking(contact, "2030-04-12T15:00:00-03:00")

    asked = env.send(contact, "Quero cancelar meu agendamento, pode confirmar")
    assert asked.action == "booking_change_selection_requested"
    assert env.operations(contact) == []

    proposed = env.send(contact, "2")
    assert proposed.action == "booking_cancel_confirmation_requested"
    [proposal] = env.operations(contact)
    assert proposal["booking_id"] == friday

    done = env.send(contact, "sim")
    assert done.action == "booking_cancelled"
    assert effects["cancel"] == 1
    assert env.booking_row(friday)["status"] == "cancelled"
    assert env.booking_row(wednesday)["status"] == "confirmed"


# ── A03: aceite expirado, repetido ou substituído ─────────────────────


def test_a03_expired_proposal_does_not_execute(env: Harness, effects) -> None:
    contact = _contact()
    env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h")
    env.clock.advance(minutes=31)

    result = env.send(contact, "sim")
    assert result.action == "schedule_confirmation_requested"
    assert "expir" in result.reply.lower()
    assert effects["create"] == 0
    assert env.bookings(contact) == []
    assert _statuses(env, contact) == ["expired", "proposed"]


def test_a03_ttl_comes_from_policy(env: Harness, effects) -> None:
    contact = _contact()
    policy = env.policy({"confirmation": {"proposal_ttl_minutes": 5}})
    env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h", policy=policy)
    [proposal] = env.operations(contact)
    created = _aware(proposal["created_at"])
    assert _aware(proposal["expires_at"]) - created == timedelta(minutes=5)


def test_a03_redelivered_create_confirmation_does_not_duplicate(env: Harness, effects) -> None:
    contact = _contact()
    env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h")
    reference = f"whatsapp:wamid.{uuid4().hex}"

    first = env.send(contact, "sim", ref=reference)
    assert first.action == "schedule_confirmed"
    again = env.send(contact, "sim", ref=reference)
    assert again.action == "operation_replay"
    assert "09/04 às 10:00" in again.reply
    assert effects["create"] == 1
    assert len(env.bookings(contact)) == 1
    assert _statuses(env, contact) == ["completed"]


def test_a03_redelivered_cancel_confirmation_does_not_duplicate(env: Harness, effects) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")
    env.send(contact, "Quero cancelar meu agendamento")
    reference = f"whatsapp:wamid.{uuid4().hex}"

    assert env.send(contact, "sim", ref=reference).action == "booking_cancelled"
    again = env.send(contact, "sim", ref=reference)
    assert again.action == "operation_replay"
    assert effects["cancel"] == 1
    assert env.booking_row(booking_id)["status"] == "cancelled"


def test_a03_acceptance_after_substitution_does_not_execute(env: Harness, effects) -> None:
    contact = _contact()
    env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h")
    [stale] = env.operations(contact)
    # Outra via criou proposta nova para o mesmo contato: a do fluxo foi substituída.
    ops.create_proposal(
        company_id=env.company_id,
        contact_id=contact,
        kind="create",
        params={"starts_at": "2030-04-09T20:00:00.000000+00:00", "timezone": TZ},
        ttl_minutes=30,
        now=env.clock.now,
    )

    result = env.send(contact, "sim")
    assert result.action == "schedule_confirmation_requested"
    assert effects["create"] == 0
    assert env.bookings(contact) == []
    assert ops.get_operation(company_id=env.company_id, operation_id=stale["id"])["status"] == "superseded"


def test_a03_target_changed_after_proposal_is_not_executed(env: Harness, effects) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")
    env.send(contact, "Quero cancelar meu agendamento")
    [first] = env.operations(contact)
    repository.update_appointment_booking_status(booking_id, "pending", company_id=env.company_id)

    result = env.send(contact, "sim")
    assert result.action == "booking_cancel_confirmation_requested"
    assert effects["cancel"] == 0
    operations = env.operations(contact)
    assert operations[0]["status"] == "failed"
    assert operations[0]["last_error"] == "target_changed"
    assert operations[1]["status"] == "proposed"
    assert operations[1]["target_version"] != first["target_version"]
    assert env.booking_row(booking_id)["status"] == "pending"


# ── A04: cancelar durante coleta de nova reserva ──────────────────────


@pytest.mark.parametrize("phrase", ["cancelar", "desisto"])
def test_a04_cancel_during_collection_only_ends_collection(env: Harness, effects, phrase: str) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")
    env.send(contact, "Quero agendar a Consultoria inicial")
    assert env.flow(contact)["flow_type"] == "scheduling"

    result = env.send(contact, phrase)
    assert result.action == "scheduling_abandoned"
    assert "não vou seguir com o agendamento" in result.reply.lower()
    assert "já marcado" in result.reply.lower()
    assert env.flow(contact) is None
    assert effects["cancel"] == 0
    assert env.booking_row(booking_id)["status"] == "confirmed"


def test_a04_abandon_with_pending_proposal_rejects_it(env: Harness, effects) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")
    env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h")

    result = env.send(contact, "cancelar")
    assert result.action == "scheduling_abandoned"
    assert _statuses(env, contact) == ["rejected"]
    assert effects == {"create": 0, "cancel": 0, "reschedule": 0}
    assert env.booking_row(booking_id)["status"] == "confirmed"


def test_a04_negative_answer_to_date_question_does_not_abort(env: Harness, effects) -> None:
    contact = _contact()
    env.send(contact, "Quero agendar a Consultoria inicial")
    result = env.send(contact, "não sei ainda")
    assert result.handled is True
    assert env.flow(contact) is not None


# ── confirmação embutida na mensagem inicial ──────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "cancela meu horário de amanhã, pode confirmar",
        "Quero cancelar meu horário de amanhã, pode confirmar",
    ],
)
def test_initial_embedded_confirmation_never_cancels(env: Harness, effects, message: str) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-09T10:00:00-03:00")
    result = env.send(contact, message)
    assert result.action == "booking_cancel_confirmation_requested"
    assert effects["cancel"] == 0
    assert env.booking_row(booking_id)["status"] == "confirmed"
    assert _statuses(env, contact) == ["proposed"]


def test_initial_embedded_confirmation_never_books(env: Harness, effects) -> None:
    contact = _contact()
    result = env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h, pode confirmar")
    assert result.action == "schedule_confirmation_requested"
    assert effects["create"] == 0
    assert env.bookings(contact) == []


# ── F04: conflito, política e erro inesperado ─────────────────────────


def test_conflict_fails_operation_and_offers_other_slots(env: Harness, monkeypatch) -> None:
    contact = _contact()
    env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h")
    [first] = env.operations(contact)

    def _occupied(**kwargs: Any) -> Any:
        raise BookingConflictError("ocupado")

    monkeypatch.setattr(workflow, "create_calendar_booking", _occupied)
    result = env.send(contact, "sim")
    assert result.action == "schedule_slot_unavailable"
    assert "confirmado" not in result.reply.lower()
    assert "Encontrei" in result.reply
    assert env.bookings(contact) == []
    operations = env.operations(contact)
    assert operations[0]["status"] == "failed"
    assert operations[0]["last_error"] == "slot_unavailable"
    [replacement] = _open_proposals(env, contact)
    assert replacement["params"]["starts_at"] != first["params"]["starts_at"]


def test_reschedule_conflict_keeps_original_booking(env: Harness, monkeypatch) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")
    original = env.booking_row(booking_id)
    env.send(contact, "Quero remarcar meu horário para amanhã às 15h")

    def _occupied(**kwargs: Any) -> Any:
        raise BookingConflictError("ocupado")

    monkeypatch.setattr(workflow, "reschedule_calendar_booking", _occupied)
    result = env.send(contact, "sim")
    assert result.action == "booking_reschedule_slot_unavailable"
    assert "remarquei" not in result.reply.lower()
    assert env.operations(contact)[0]["last_error"] == "slot_unavailable"
    assert _aware(env.booking_row(booking_id)["starts_at"]) == _aware(original["starts_at"])


def test_policy_precheck_requires_human_creates_handoff_without_proposal(env: Harness, effects) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")
    policy = env.policy(
        {"booking_rules": {"cancellation": {"allowed": True, "min_notice_minutes": 72 * 60, "outside_window": "handoff"}}}
    )

    result = env.send(contact, "Quero cancelar meu agendamento", policy=policy)
    assert result.action == "booking_change_requires_human"
    assert result.handoff_question_type == "booking_policy_exception"
    assert result.handoff_question
    assert "equipe" in result.reply.lower()
    assert env.operations(contact) == []
    assert env.flow(contact) is None
    assert effects["cancel"] == 0
    assert env.booking_row(booking_id)["status"] == "confirmed"


def test_policy_precheck_denied_explains_rule(env: Harness, effects) -> None:
    contact = _contact()
    env.booking(contact, "2030-04-10T10:00:00-03:00")
    policy = env.policy(
        {"booking_rules": {"reschedule": {"allowed": False, "min_notice_minutes": 0, "outside_window": "deny"}}}
    )

    result = env.send(contact, "Quero remarcar meu horário para amanhã às 15h", policy=policy)
    assert result.action == "booking_change_denied"
    assert result.handoff_question_type is None
    assert "política" in result.reply.lower()
    assert env.operations(contact) == []
    assert effects["reschedule"] == 0


def test_policy_is_rechecked_at_execution_time(env: Harness, effects) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-08T12:00:00-03:00")
    policy = env.policy(
        {
            "booking_rules": {"cancellation": {"allowed": True, "min_notice_minutes": 120, "outside_window": "handoff"}},
            "confirmation": {"proposal_ttl_minutes": 240},
        }
    )
    assert env.send(contact, "Quero cancelar meu agendamento", policy=policy).action == (
        "booking_cancel_confirmation_requested"
    )
    env.clock.advance(hours=3)

    result = env.send(contact, "sim", policy=policy)
    assert result.handoff_question_type == "booking_policy_exception"
    assert effects["cancel"] == 0
    [operation] = env.operations(contact)
    assert operation["status"] == "failed"
    assert operation["last_error"] == "policy_inside_minimum_notice"
    assert env.booking_row(booking_id)["status"] == "confirmed"


def test_booking_policy_error_from_agenda_is_handed_off(env: Harness, monkeypatch) -> None:
    contact = _contact()
    booking_id = env.booking(contact, "2030-04-10T10:00:00-03:00")
    env.send(contact, "Quero cancelar meu agendamento")

    def _outside_policy(**kwargs: Any) -> Any:
        # `release_booking_slot` embrulha erros da agenda em AppointmentQueueError.
        try:
            raise BookingPolicyError("fora da política", outcome="requires_human", reason="inside_minimum_notice")
        except BookingPolicyError as exc:
            raise AppointmentQueueError(str(exc)) from exc

    monkeypatch.setattr(workflow, "release_booking_slot", _outside_policy)
    result = env.send(contact, "sim")
    assert result.handoff_question_type == "booking_policy_exception"
    assert result.action == "booking_change_requires_human"
    assert "cancelei" not in result.reply.lower()
    [operation] = env.operations(contact)
    assert operation["status"] == "failed"
    assert operation["last_error"] == "policy_inside_minimum_notice"
    assert env.booking_row(booking_id)["status"] == "confirmed"


def test_booking_policy_error_denied_explains(env: Harness, monkeypatch) -> None:
    contact = _contact()
    env.booking(contact, "2030-04-10T10:00:00-03:00")
    env.send(contact, "Quero remarcar meu horário para amanhã às 15h")

    def _denied(**kwargs: Any) -> Any:
        raise BookingPolicyError("negado", outcome="denied", reason="change_not_allowed")

    monkeypatch.setattr(workflow, "reschedule_calendar_booking", _denied)
    result = env.send(contact, "sim")
    assert result.action == "booking_change_denied"
    assert result.handoff_question_type is None
    assert env.operations(contact)[0]["last_error"] == "policy_change_not_allowed"


def test_unexpected_error_marks_review_and_propagates(env: Harness, monkeypatch) -> None:
    contact = _contact()
    env.send(contact, "Quero agendar a Consultoria inicial amanhã às 10h")

    def _boom(**kwargs: Any) -> Any:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(workflow, "create_calendar_booking", _boom)
    with pytest.raises(RuntimeError):
        env.send(contact, "sim")
    [operation] = env.operations(contact)
    assert operation["status"] == "review"
    assert operation["last_error"] == "unexpected_error"
    assert env.bookings(contact) == []
