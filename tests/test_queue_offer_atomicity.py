"""E-01: nenhuma resposta de erro sobre mutação já comitada.

`respond_to_queue_offer` no ramo `declined` fazia, nesta ordem:

1. `update_appointment_queue_offer(...)`        -> comitado
2. `update_appointment_queue_entry_status(...)` -> comitado
3. `_create_and_dispatch_outbound(...)`         -> pode estourar
4. `int(offer["released_booking_id"])`          -> `TypeError` se for NULL
5. `offer_released_booking_slot(...)`           -> pode não achar o booking

Os passos 3 a 5 acontecem **depois** de a oferta já ter mudado de `pending`
para `declined` e a entrada de `active` para `awaiting_confirmation`. Quando um
deles falha, o operador recebe 500 ou 404, conclui que nada aconteceu, e
repete — sobre um estado que já mudou.

A revisão adversarial alcançou os dois gatilhos, o segundo **pela API pública**:
enfileirar um contato que nunca passou pela ingestão e responder `waiting`
estourava em `create_outbound_message` com a entrada já alterada.

A regra que os testes abaixo fixam: **validar tudo antes da primeira escrita.**
"""

from __future__ import annotations

import pytest

from app.services import appointment_queue_service
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


def _make_entry_and_offer(company_id: int, *, released_booking_id: int | None) -> dict:
    repository.save_contact(
        contact_id="+5511988887777",
        contact_name="Cliente da fila",
        company_id=company_id,
    )
    entry_id = int(
        repository.create_appointment_queue_entry(
            company_id=company_id,
            contact_id="+5511988887777",
            desired_title="CONSULTA-E01",
            scheduling_profile_key=None,
            customer_profile=None,
            request_summary=None,
            timezone="America/Sao_Paulo",
            earliest_starts_at=None,
            latest_starts_at=None,
            preferred_periods=None,
            priority_tier="high",
            priority_score=10,
            accepts_short_notice=True,
            original_booking_id=None,
        )
    )
    offer_id = int(
        repository.create_appointment_queue_offer(
            company_id=company_id,
            queue_entry_id=entry_id,
            released_booking_id=released_booking_id,
            outbound_message_id=None,
            new_booking_id=None,
            offered_starts_at="2026-09-11T13:00:00+00:00",
            offered_ends_at="2026-09-11T14:00:00+00:00",
            timezone="America/Sao_Paulo",
        )
    )
    return {"entry_id": entry_id, "offer_id": offer_id}


def _state(company_id: int, ids: dict) -> tuple[str, str]:
    offer = repository.get_appointment_queue_offer(ids["offer_id"], company_id=company_id)
    entry = repository.get_appointment_queue_entry(ids["entry_id"], company_id=company_id)
    assert offer is not None and entry is not None
    return str(offer["status"]), str(entry["status"])


# --- released_booking_id ausente, inválido, inexistente ----------------------------------


def test_declining_without_a_released_booking_changes_nothing(company_factory) -> None:
    """Falha antes da primeira escrita, não depois da segunda.

    O caso "id inexistente" saiu desta matriz porque deixou de ser
    construível: com `PRAGMA foreign_keys` ligada (L-06), criar a oferta
    apontando para um booking que não existe é recusado pelo próprio banco. O
    caso que continua alcançável é o booking **de outro tenant**, coberto
    abaixo.
    """
    descricao = "ausente"
    company_id = company_factory("e01")
    ids = _make_entry_and_offer(company_id, released_booking_id=None)
    before = _state(company_id, ids)
    assert before == ("pending", "active")

    with pytest.raises(appointment_queue_service.AppointmentQueueError):
        appointment_queue_service.respond_to_queue_offer(
            offer_id=ids["offer_id"],
            response_status="declined",
            queue_membership_after_decline="ask",
            company_id=company_id,
            send_customer_followup=False,
        )

    assert _state(company_id, ids) == before, (
        f"released_booking_id {descricao}: o estado mudou apesar do erro"
    )


def test_declining_with_another_tenants_released_booking_changes_nothing(company_factory) -> None:
    """Booking existe, mas fora do escopo: recusa antes de escrever."""
    mine = company_factory("e01-mine")
    other = company_factory("e01-other")
    repository.save_contact(contact_id="+5511900000009", contact_name="X", company_id=other)
    foreign_booking = int(
        repository.create_appointment_booking(
            provider="mock",
            title="RESERVA-DE-OUTRO",
            starts_at="2026-09-11T13:00:00+00:00",
            ends_at="2026-09-11T14:00:00+00:00",
            timezone="America/Sao_Paulo",
            company_id=other,
            contact_id="+5511900000009",
            status="cancelled",
        )
    )
    ids = _make_entry_and_offer(mine, released_booking_id=foreign_booking)
    before = _state(mine, ids)

    with pytest.raises(appointment_queue_service.AppointmentQueueError):
        appointment_queue_service.respond_to_queue_offer(
            offer_id=ids["offer_id"],
            response_status="declined",
            queue_membership_after_decline="ask",
            company_id=mine,
            send_customer_followup=False,
        )

    assert _state(mine, ids) == before


def test_repeating_the_failed_request_does_not_accumulate_mutations(company_factory) -> None:
    """Repetir depois de um erro é o que o operador faz. Não pode acumular efeito."""
    company_id = company_factory("e01-repeat")
    ids = _make_entry_and_offer(company_id, released_booking_id=None)

    for _ in range(3):
        with pytest.raises(appointment_queue_service.AppointmentQueueError):
            appointment_queue_service.respond_to_queue_offer(
                offer_id=ids["offer_id"],
                response_status="declined",
                queue_membership_after_decline="ask",
                company_id=company_id,
                send_customer_followup=False,
            )

    assert _state(company_id, ids) == ("pending", "active")


def test_the_error_does_not_leak_internals(company_factory) -> None:
    """A resposta não pode citar tabela, coluna ou id interno."""
    company_id = company_factory("e01-leak")
    ids = _make_entry_and_offer(company_id, released_booking_id=None)

    with pytest.raises(appointment_queue_service.AppointmentQueueError) as caught:
        appointment_queue_service.respond_to_queue_offer(
            offer_id=ids["offer_id"],
            response_status="declined",
            queue_membership_after_decline="ask",
            company_id=company_id,
            send_customer_followup=False,
        )

    message = str(caught.value).lower()
    for forbidden in ("appointment_queue_offers", "released_booking_id", "traceback", "select "):
        assert forbidden not in message, f"a mensagem vaza `{forbidden}`: {caught.value}"


# --- controle positivo -------------------------------------------------------------------


def test_a_legitimate_decline_still_works(company_factory) -> None:
    """Sem isto, recusar tudo passaria em todos os testes acima."""
    company_id = company_factory("e01-ok")
    booking_id = int(
        repository.create_appointment_booking(
            provider="mock",
            title="RESERVA-LIBERADA",
            starts_at="2026-09-11T13:00:00+00:00",
            ends_at="2026-09-11T14:00:00+00:00",
            timezone="America/Sao_Paulo",
            company_id=company_id,
            contact_id="+5511977776666",
            status="cancelled",
        )
    )
    ids = _make_entry_and_offer(company_id, released_booking_id=booking_id)

    result = appointment_queue_service.respond_to_queue_offer(
        offer_id=ids["offer_id"],
        response_status="declined",
        queue_membership_after_decline="keep",
        company_id=company_id,
        send_customer_followup=False,
    )

    assert result["offer"]["status"] == "declined"
    assert _state(company_id, ids)[0] == "declined"


def test_the_connection_is_returned_when_the_precondition_fails(company_factory) -> None:
    """O elo com I-01: uma recusa de pré-condição não pode custar uma conexão."""
    from app.storage import db as db_module

    company_id = company_factory("e01-conn")
    ids = _make_entry_and_offer(company_id, released_booking_id=None)

    opened: list[object] = []
    closed: list[object] = []
    original = db_module.get_connection

    class CountingConnection:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def close(self) -> None:
            closed.append(self._inner)
            self._inner.close()

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    def _counting():
        connection = original()
        opened.append(connection)
        return CountingConnection(connection)

    db_module.get_connection = _counting  # type: ignore[assignment]
    try:
        with pytest.raises(appointment_queue_service.AppointmentQueueError):
            appointment_queue_service.respond_to_queue_offer(
                offer_id=ids["offer_id"],
                response_status="declined",
                queue_membership_after_decline="ask",
                company_id=company_id,
                send_customer_followup=False,
            )
    finally:
        db_module.get_connection = original  # type: ignore[assignment]

    assert len(opened) == len(closed), f"aberturas={len(opened)} fechamentos={len(closed)}"


# --- D-10: os ramos que ainda comitavam antes de poder falhar ----------------------------


@pytest.mark.parametrize(
    ("response_status", "membership"),
    [("waiting", "ask"), ("declined", "ask"), ("declined", "keep")],
)
def test_a_missing_contact_blocks_every_branch_before_it_writes(
    company_factory,
    response_status: str,
    membership: str,
) -> None:
    """O acompanhamento ao cliente falhava DEPOIS das mutações.

    `outbound_messages.contact_id` é chave estrangeira e `enqueue` não exige que
    o contato exista, então um contact_id digitado à mão chegava aqui. O
    operador recebia 500 sobre a oferta já recusada e a entrada já movida.

    No ramo `declined` era pior: `offer_released_booking_slot()` nunca rodava,
    o horário ficava marcado como liberado e nunca era reofertado — a fila
    travava sem ninguém perceber.
    """
    company_id = company_factory("d10")
    booking_id = int(
        repository.create_appointment_booking(
            provider="mock",
            title="RESERVA-D10",
            starts_at="2026-09-11T13:00:00+00:00",
            ends_at="2026-09-11T14:00:00+00:00",
            timezone="America/Sao_Paulo",
            company_id=company_id,
            contact_id="+5511988887777",
            status="cancelled",
        )
    )
    ids = _make_entry_and_offer(company_id, released_booking_id=booking_id)

    # A entrada passa a apontar para um contato que não existe — exatamente o
    # que `enqueue` permite hoje.
    from app.storage.db import transaction

    with transaction() as connection:
        connection.cursor().execute(
            "UPDATE appointment_queue_entries SET contact_id = ? WHERE id = ?",
            ("+5511000000000", ids["entry_id"]),
        )

    before = _state(company_id, ids)
    assert before == ("pending", "active")

    with pytest.raises(appointment_queue_service.AppointmentQueueError):
        appointment_queue_service.respond_to_queue_offer(
            offer_id=ids["offer_id"],
            response_status=response_status,
            queue_membership_after_decline=membership,
            company_id=company_id,
            send_customer_followup=True,
        )

    assert _state(company_id, ids) == before, (
        f"{response_status}/{membership}: mutação comitada antes da falha do envio"
    )
    offer = repository.get_appointment_queue_offer(ids["offer_id"], company_id=company_id)
    assert offer is not None and offer.get("responded_at") is None, (
        "responded_at foi gravado apesar do erro"
    )
