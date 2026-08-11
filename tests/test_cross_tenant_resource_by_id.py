"""T-03: mutação de recurso alcançado só pelo ID, sem escopo de empresa.

Cinco rotas recebiam um `booking_id`/`offer_id` no path, não aceitavam
`company_id` nenhum, e chamavam o serviço apenas com o ID. Como os
repositórios fazem busca global quando `company_id=None`, um principal do
tenant A cancelava o agendamento do tenant B — verificado: HTTP 200 e o
booking de B terminando `cancelled`, com os lembretes dele cancelados junto.

Os IDs são sequenciais globais, portanto enumeráveis.

Por que a suíte anterior não via: `tests/test_cross_tenant_omission.py` cobre
T-01 (leitura), T-02 (escrita por recurso carregado por ID **com** parâmetro
de empresa) e T-04 (path `company_id`). Estas cinco rotas não têm parâmetro de
empresa **nenhum**, então nem a conversão em massa nem os testes as tocaram.

O invariante que estes testes fixam:

    tenant autorizado -> query escopada -> recurso

e nunca:

    recurso por ID global -> recurso.company_id -> autorização

Cada teste verifica o **estado persistido** depois da requisição, não apenas o
status HTTP: um 403 que já tenha escrito é tão ruim quanto um 200.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.storage import identity_repository
from app.storage.repository import RuntimeRepository

client = TestClient(app)
repository = RuntimeRepository()

# Respostas aceitáveis para "não é seu". 404 e 403 são ambos defensáveis; o
# que não pode acontecer é 200, nem 500.
DENIED = {403, 404}


@pytest.fixture(autouse=True)
def _enable_admin_auth(monkeypatch):
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "legacy-viewer")
    monkeypatch.setattr(settings, "admin_operator_token", "legacy-operator")
    monkeypatch.setattr(settings, "admin_superadmin_token", "legacy-superadmin")
    monkeypatch.setattr(settings, "admin_api_token", None)


def _headers(principal: dict) -> dict[str, str]:
    return {"X-Admin-Token": principal["token"]}


def _make_booking(company_id: int, marker: str) -> int:
    return int(
        repository.create_appointment_booking(
            provider="mock",
            title=marker,
            starts_at="2026-09-10T10:00:00+00:00",
            ends_at="2026-09-10T11:00:00+00:00",
            timezone="America/Sao_Paulo",
            company_id=company_id,
            contact_id=f"contato-{company_id}",
            status="confirmed",
        )
    )


@pytest.fixture()
def tenants(company_factory, admin_principal_factory):
    """Duas empresas, cada uma com um agendamento marcado.

    O tenant B não tem principal: nada dele pode ser alcançado por ninguém do
    tenant A, por nenhum caminho.
    """
    company_a = company_factory("t03-a")
    company_b = company_factory("t03-b")

    return {
        "a": company_a,
        "b": company_b,
        "b_slug": str(repository.get_company(company_b)["slug"]),
        "booking_a": _make_booking(company_a, "AGENDAMENTO-DO-TENANT-A"),
        "booking_b": _make_booking(company_b, "AGENDAMENTO-DO-TENANT-B"),
        "operator_a": admin_principal_factory(global_role="operator", company_ids=[company_a]),
        "viewer_a": admin_principal_factory(global_role="viewer", company_ids=[company_a]),
        "superadmin_a": admin_principal_factory(
            global_role="superadmin",
            company_ids=[company_a],
        ),
        # Papel global alto, membership rebaixada: o papel efetivo em A é viewer.
        "demoted_superadmin_a": admin_principal_factory(
            global_role="superadmin",
            company_ids=[company_a],
            company_roles={company_a: "viewer"},
        ),
    }


# --- as cinco rotas, sob a mesma matriz --------------------------------------------------
#
# A rodada anterior chamava esta coleção de "as cinco rotas" e listava quatro:
# `queue-offers/{id}/respond` ficava de fora, com dois cenários próprios. Um
# recurso com ciclo de vida diferente não justifica cobertura menor — justifica
# um adaptador.
#
# Cada família descreve como criar o recurso numa empresa, como ler o estado
# persistido e quais efeitos derivados não podem se mover. A matriz de cenários
# é escrita uma vez e vale para todas.


@dataclass(frozen=True)
class RouteCase:
    """Uma rota mutável que recebe o id do recurso no path."""

    name: str
    path: str
    body: dict
    #: cria o recurso na empresa dada e devolve o id usado no path
    create: Callable[[int, str], int]
    #: estado persistido, lido **sem** passar pela rota
    read_state: Callable[[int, int], object]
    #: estado esperado logo após a criação
    pristine: object
    #: o que a rota faz quando é legítima (para o controle positivo)
    expected_after_success: object


def _make_queue_offer(company_id: int, marker: str) -> int:
    # O contato precisa existir: o acompanhamento ao cliente grava em
    # `outbound_messages`, cujo `contact_id` é chave estrangeira.
    repository.save_contact(
        contact_id=f"contato-fila-{company_id}",
        contact_name=f"Contato da fila {company_id}",
        company_id=company_id,
    )
    entry_id = int(
        repository.create_appointment_queue_entry(
            company_id=company_id,
            contact_id=f"contato-fila-{company_id}",
            desired_title=marker,
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
    return int(
        repository.create_appointment_queue_offer(
            company_id=company_id,
            queue_entry_id=entry_id,
            released_booking_id=None,
            outbound_message_id=None,
            new_booking_id=None,
            offered_starts_at="2026-09-11T13:00:00+00:00",
            offered_ends_at="2026-09-11T14:00:00+00:00",
            timezone="America/Sao_Paulo",
        )
    )


def _booking_status(booking_id: int, company_id: int) -> object:
    return _persisted("booking_status", booking_id, company_id)


# Consultas literais, uma por estado observado. Montar o SQL por f-string
# funcionaria, mas deixaria um `noqa` de SQL dinâmico dentro do teste que existe
# justamente para provar rigor.
STATE_QUERIES: dict[str, str] = {
    "booking_status": "SELECT status AS value, company_id FROM appointment_bookings WHERE id = ?",
    "booking_start": "SELECT starts_at AS value, company_id FROM appointment_bookings WHERE id = ?",
    "booking_outcome": (
        "SELECT booking_outcome AS value, company_id FROM appointment_bookings WHERE id = ?"
    ),
    "cancellation_reason": (
        "SELECT cancellation_reason AS value, company_id FROM appointment_bookings WHERE id = ?"
    ),
    "offer_status": (
        "SELECT status AS value, company_id FROM appointment_queue_offers WHERE id = ?"
    ),
}


def _persisted(state: str, resource_id: int, company_id: int) -> object:
    """Lê o estado direto do banco, sem passar por nenhum acessor da aplicação.

    Um teste adversarial não pode confiar no caminho de leitura do produto: o
    acessor filtra por empresa, então uma escrita cross-tenant ficaria invisível
    justamente para o teste que deveria vê-la. Aqui a consulta é por id, e a
    empresa é apenas **conferida**.
    """
    # `fetch_one` traduz o placeholder por backend; `connection.execute` é API
    # do sqlite3 e não existe no wrapper do PostgreSQL.
    from app.storage.sql_support import fetch_one

    row = fetch_one(STATE_QUERIES[state], (resource_id,))
    if row is None:
        return None
    assert int(row["company_id"]) == int(company_id), (
        f"{state}#{resource_id} pertence a {row['company_id']}, não a {company_id}"
    )
    value = row["value"]
    return None if value is None else str(value)


def _offer_status(offer_id: int, company_id: int) -> object:
    return _persisted("offer_status", offer_id, company_id)


def _booking_outcome(booking_id: int, company_id: int) -> object:
    return _persisted("booking_outcome", booking_id, company_id)


def _booking_start(booking_id: int, company_id: int) -> object:
    return _persisted("booking_start", booking_id, company_id)


def _cancellation_reason(booking_id: int, company_id: int) -> object:
    return _persisted("cancellation_reason", booking_id, company_id)


ROUTE_CASES: tuple[RouteCase, ...] = (
    RouteCase(
        name="cancel",
        path="/admin/calendar/bookings/{resource_id}/cancel",
        body={"reason": "teste adversarial"},
        create=_make_booking,
        read_state=_booking_status,
        pristine="confirmed",
        expected_after_success="cancelled",
    ),
    RouteCase(
        name="reschedule",
        path="/admin/calendar/bookings/{resource_id}/reschedule",
        body={
            "starts_at": "2026-09-11T13:00:00+00:00",
            "ends_at": "2026-09-11T14:00:00+00:00",
            "timezone": "America/Sao_Paulo",
        },
        create=_make_booking,
        read_state=_booking_start,
        pristine="2026-09-10T10:00:00+00:00",
        expected_after_success="2026-09-11T13:00:00+00:00",
    ),
    RouteCase(
        name="outcome",
        path="/admin/analytics/bookings/{resource_id}/outcome",
        body={"outcome": "no_show"},
        create=_make_booking,
        read_state=_booking_outcome,
        pristine=None,
        expected_after_success="no_show",
    ),
    RouteCase(
        name="cancellation_reason",
        path="/admin/analytics/bookings/{resource_id}/cancellation-reason",
        body={"reason": "cliente desistiu"},
        create=_make_booking,
        read_state=_cancellation_reason,
        pristine=None,
        expected_after_success="cliente desistiu",
    ),
    RouteCase(
        name="queue_offer_respond",
        path="/admin/calendar/queue-offers/{resource_id}/respond",
        body={"response_status": "accepted"},
        create=_make_queue_offer,
        read_state=_offer_status,
        pristine="pending",
        expected_after_success="accepted",
    ),
)

CASE_IDS = [case.name for case in ROUTE_CASES]


@pytest.fixture(params=ROUTE_CASES, ids=CASE_IDS)
def case(request) -> RouteCase:
    return request.param


@pytest.fixture()
def victim(case, tenants) -> dict:
    """Um recurso do tenant B, no estado inicial, e o url para atacá-lo."""
    resource_id = case.create(tenants["b"], f"RECURSO-DE-B-{case.name}")
    assert case.read_state(resource_id, tenants["b"]) == case.pristine
    return {"id": resource_id, "url": case.path.format(resource_id=resource_id)}


def _assert_untouched(case: RouteCase, tenants: dict, resource_id: int) -> None:
    assert case.read_state(resource_id, tenants["b"]) == case.pristine, (
        f"{case.name}: o recurso do tenant B mudou de estado"
    )


# --- cenário 1: tenant omitido -----------------------------------------------------------


def test_omitting_the_company_does_not_reach_another_tenants_resource(case, tenants, victim) -> None:
    response = client.post(victim["url"], json=case.body, headers=_headers(tenants["operator_a"]))

    assert response.status_code in DENIED
    _assert_untouched(case, tenants, victim["id"])


# --- cenário 2: tenant próprio, recurso alheio -------------------------------------------


def test_naming_my_own_company_does_not_unlock_another_tenants_resource(case, tenants, victim) -> None:
    """O ataque mais direto: credencial válida em A, id de B, `company_id=A`.

    A rota precisa consultar dentro de A e não achar. Se ela carregar o recurso
    por id global e só depois olhar a empresa, a mutação passa.
    """
    response = client.post(
        victim["url"],
        params={"company_id": tenants["a"]},
        json=case.body,
        headers=_headers(tenants["operator_a"]),
    )

    assert response.status_code == 404, response.text
    _assert_untouched(case, tenants, victim["id"])


# --- cenário 3: tenant alheio ------------------------------------------------------------


def test_naming_the_other_company_is_blocked_before_the_handler(case, tenants, victim) -> None:
    response = client.post(
        victim["url"],
        params={"company_id": tenants["b"]},
        json=case.body,
        headers=_headers(tenants["operator_a"]),
    )

    assert response.status_code == 403, response.text
    _assert_untouched(case, tenants, victim["id"])


def test_naming_the_other_company_by_slug_is_blocked(case, tenants, victim) -> None:
    response = client.post(
        victim["url"],
        params={"company_slug": tenants["b_slug"]},
        json=case.body,
        headers=_headers(tenants["operator_a"]),
    )

    assert response.status_code == 403, response.text
    _assert_untouched(case, tenants, victim["id"])


# --- cenário 4: inexistente indistinguível de alheio -------------------------------------


def test_a_missing_resource_is_indistinguishable_from_another_tenants(case, tenants, victim) -> None:
    """Ids são sequenciais. Se "não existe" e "não é seu" tiverem respostas
    diferentes, a diferença enumera o banco inteiro do vizinho."""
    foreign = client.post(
        victim["url"],
        params={"company_id": tenants["a"]},
        json=case.body,
        headers=_headers(tenants["operator_a"]),
    )
    missing = client.post(
        case.path.format(resource_id=99_999_999),
        params={"company_id": tenants["a"]},
        json=case.body,
        headers=_headers(tenants["operator_a"]),
    )

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json(), "o corpo da resposta distingue os dois casos"
    _assert_untouched(case, tenants, victim["id"])


# --- cenário 5: viewer -------------------------------------------------------------------


def test_a_viewer_cannot_mutate_even_in_its_own_company(case, tenants) -> None:
    own = case.create(tenants["a"], f"RECURSO-DE-A-{case.name}")

    response = client.post(
        case.path.format(resource_id=own),
        params={"company_id": tenants["a"]},
        json=case.body,
        headers=_headers(tenants["viewer_a"]),
    )

    assert response.status_code == 403, response.text
    assert case.read_state(own, tenants["a"]) == case.pristine


# --- cenário 6: operador legítimo (controle positivo) ------------------------------------


def test_a_legitimate_operator_still_succeeds(case, tenants) -> None:
    """Sem isto, negar tudo passaria na matriz inteira."""
    own = case.create(tenants["a"], f"RECURSO-DE-A-{case.name}")

    response = client.post(
        case.path.format(resource_id=own),
        params={"company_id": tenants["a"]},
        json=case.body,
        headers=_headers(tenants["operator_a"]),
    )

    assert response.status_code == 200, response.text
    assert case.read_state(own, tenants["a"]) == case.expected_after_success


# --- cenário 7: membership revogada ------------------------------------------------------


def test_revoking_the_membership_blocks_immediately(case, tenants) -> None:
    own = case.create(tenants["a"], f"RECURSO-DE-A-{case.name}")
    principal = tenants["operator_a"]

    identity_repository.remove_membership(
        principal_id=int(principal["principal_id"]),
        company_id=int(tenants["a"]),
    )

    response = client.post(
        case.path.format(resource_id=own),
        params={"company_id": tenants["a"]},
        json=case.body,
        headers=_headers(principal),
    )

    assert response.status_code in DENIED, response.text
    assert case.read_state(own, tenants["a"]) == case.pristine


# --- cenário 8: status canônico ----------------------------------------------------------


def test_the_denial_status_is_canonical(case, tenants, victim) -> None:
    """404 para fora de escopo, 403 para empresa não autorizada, 400 para
    ausência de empresa quando ela é obrigatória. Nunca 500, nunca 200."""
    out_of_scope = client.post(
        victim["url"],
        params={"company_id": tenants["a"]},
        json=case.body,
        headers=_headers(tenants["operator_a"]),
    )
    forbidden = client.post(
        victim["url"],
        params={"company_id": tenants["b"]},
        json=case.body,
        headers=_headers(tenants["operator_a"]),
    )

    assert out_of_scope.status_code == 404
    assert out_of_scope.json() == {"detail": "Resource not found."}
    assert forbidden.status_code == 403


# --- cenário 9: papel efetivo vem da membership ------------------------------------------


def test_a_global_superadmin_demoted_in_the_company_cannot_mutate(case, tenants) -> None:
    """Papel efetivo é o da membership da empresa selecionada — teto, não piso.

    Um superadmin global rebaixado a viewer em A não pode escrever em A.
    """
    own = case.create(tenants["a"], f"RECURSO-DE-A-{case.name}")
    demoted = tenants["demoted_superadmin_a"]

    response = client.post(
        case.path.format(resource_id=own),
        params={"company_id": tenants["a"]},
        json=case.body,
        headers=_headers(demoted),
    )

    assert response.status_code == 403, response.text
    assert case.read_state(own, tenants["a"]) == case.pristine


# --- cenário 10: auditoria com a empresa autorizada --------------------------------------


def test_a_successful_mutation_is_audited_under_the_authorized_company(case, tenants) -> None:
    """A trilha de auditoria precisa aparecer para o tenant que sofreu a ação.

    Com `company_id` ausente, o evento fica com empresa nula: some do filtro de
    quem deveria investigá-lo. Foi o que aconteceu com as duas rotas de
    analytics enquanto o teste estrutural, que só comparava o valor quando o
    argumento existia, continuava verde.
    """
    own = case.create(tenants["a"], f"RECURSO-DE-A-{case.name}")

    response = client.post(
        case.path.format(resource_id=own),
        params={"company_id": tenants["a"]},
        json=case.body,
        headers=_headers(tenants["operator_a"]),
    )
    assert response.status_code == 200, response.text

    events = repository.get_audit_events(company_id=tenants["a"], limit=50)
    matching = [
        event
        for event in events
        if str(event.get("resource_id")) == str(own)
        and int(event["company_id"] or 0) == int(tenants["a"])
    ]
    assert matching, (
        f"{case.name}: nenhum evento de auditoria com company_id={tenants['a']} "
        f"para o recurso {own}. Eventos vistos: "
        f"{[(e.get('action'), e.get('company_id'), e.get('resource_id')) for e in events]}"
    )
