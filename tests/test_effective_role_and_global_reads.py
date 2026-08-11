"""T-07 e F-01: papel efetivo e leituras que atravessam a plataforma.

Dois defeitos que a revisão independente provou explorando, não lendo:

**F-01** — `has_role("superadmin")` sem `company_id` cai em `role_in_company(None)`,
que devolve o papel **global**. Rebaixar alguém a `viewer` dentro de uma empresa
não tirava dele o `external_id` do provider (o phone_number_id da Meta) — o
mesmo campo que a rota mascara justamente por ser sensível. O `POST` da mesma
rota negava corretamente, porque `require_admin_role` usa o papel efetivo; só a
decisão de revelar é que consultava o papel errado.

**T-07 / B-01** — `/admin/state` e `/admin/observability/*` são `POLICY_UNSCOPED`
e não exigem papel nenhum. Um viewer com membership em UMA empresa lia
`project_wapp_total{entity="companies"} 4` e o volume de mensagens, contatos e
perguntas pendentes da plataforma inteira. Não é vazamento de registro: é
vazamento de **agregado**, que entrega o tamanho da carteira e a operação dos
concorrentes por diferença.

A distinção que organiza o arquivo: `unscoped` significa "não é uma leitura de
tenant", não "qualquer um pode ler". Postura operacional da plataforma é
`platform_global`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.storage import channel_repository
from app.storage.repository import RuntimeRepository

client = TestClient(app)
repository = RuntimeRepository()


@pytest.fixture(autouse=True)
def _enable_admin_auth(monkeypatch):
    """Sem isto o resolvedor cai no principal global sintético de desenvolvimento.

    Um teste de autorização que autentica como superadmin global por acidente
    não testa autorização nenhuma — ele passa por qualquer coisa.
    """
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "legacy-viewer")
    monkeypatch.setattr(settings, "admin_operator_token", "legacy-operator")
    monkeypatch.setattr(settings, "admin_superadmin_token", "legacy-superadmin")
    monkeypatch.setattr(settings, "admin_api_token", None)
    yield


def _headers(principal: dict) -> dict[str, str]:
    return {"X-Admin-Token": principal["token"]}


# --- F-01: papel efetivo na revelação de segredo -----------------------------------------


@pytest.fixture()
def company_with_binding(company_factory):
    company_id = company_factory("f01")
    channel_repository.create_channel_binding(
        company_id=company_id,
        provider="meta",
        external_id="SEGREDO-PHONE-NUMBER-ID-1234567890",
        channel_type="whatsapp",
        label="Linha principal",
    )
    return company_id


def _external_id(payload: object) -> tuple[str, bool]:
    rows = payload if isinstance(payload, list) else payload.get("items", [])  # type: ignore[union-attr]
    assert rows, f"nenhum binding retornado: {payload!r}"
    row = rows[0]
    return str(row["external_id"]), bool(row["external_id_masked"])


def test_a_global_superadmin_demoted_to_viewer_cannot_reveal_the_provider_id(
    company_with_binding,
    admin_principal_factory,
) -> None:
    """O papel da membership é teto. Rebaixar precisa rebaixar de verdade."""
    demoted = admin_principal_factory(
        global_role="superadmin",
        company_ids=[company_with_binding],
        company_roles={company_with_binding: "viewer"},
    )

    response = client.get(
        "/admin/channel-bindings",
        params={"company_id": company_with_binding, "reveal_external_id": True},
        headers=_headers(demoted),
    )

    assert response.status_code == 200, response.text
    external_id, masked = _external_id(response.json())
    assert masked is True, "o papel global venceu a membership rebaixada"
    assert "SEGREDO-PHONE-NUMBER-ID" not in external_id


def test_a_plain_viewer_never_reveals_the_provider_id(
    company_with_binding,
    admin_principal_factory,
) -> None:
    """Controle: o viewer comum já era mascarado antes da correção."""
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_with_binding])

    response = client.get(
        "/admin/channel-bindings",
        params={"company_id": company_with_binding, "reveal_external_id": True},
        headers=_headers(viewer),
    )

    _external_id_value, masked = _external_id(response.json())
    assert masked is True


def test_a_superadmin_with_a_superadmin_membership_still_reveals(
    company_with_binding,
    admin_principal_factory,
) -> None:
    """Controle positivo: mascarar tudo passaria nos dois testes acima."""
    superadmin = admin_principal_factory(
        global_role="superadmin",
        company_ids=[company_with_binding],
        company_roles={company_with_binding: "superadmin"},
    )

    response = client.get(
        "/admin/channel-bindings",
        params={"company_id": company_with_binding, "reveal_external_id": True},
        headers=_headers(superadmin),
    )

    external_id, masked = _external_id(response.json())
    assert masked is False, "o superadmin legítimo perdeu o acesso"
    assert external_id == "SEGREDO-PHONE-NUMBER-ID-1234567890"


# --- T-07: agregados da plataforma -------------------------------------------------------

PLATFORM_POSTURE_ROUTES = [
    "/admin/state",
    "/admin/observability/summary",
    "/admin/observability/metrics",
    "/admin/system/profile",
    "/admin/system/migrations",
]


@pytest.fixture()
def crowded_platform(company_factory):
    """Várias empresas, para que um agregado global seja distinguível de um local."""
    mine = company_factory("t07-mine")
    for index in range(3):
        other = company_factory(f"t07-other-{index}")
        # O contato precisa existir: `pending_questions.contact_id` é chave
        # estrangeira. O SQLite não a aplica (a PRAGMA está desligada — ver
        # I-01/L-06), o PostgreSQL sim. Sem esta linha a fixture passa num
        # backend e falha no outro.
        repository.save_contact(
            contact_id=f"contato-{index}",
            contact_name=f"Contato {index}",
            company_id=other,
        )
        repository.create_pending_question(
            contact_id=f"contato-{index}",
            customer_message=f"mensagem da empresa {index}",
            question_type="clarification",
            question_for_owner=f"pergunta da empresa {index}",
            company_id=other,
        )
    return mine


@pytest.mark.parametrize("path", PLATFORM_POSTURE_ROUTES)
def test_a_tenant_viewer_cannot_read_platform_wide_posture(
    path: str,
    crowded_platform,
    admin_principal_factory,
) -> None:
    """Um principal com membership em uma empresa não enxerga a plataforma."""
    viewer = admin_principal_factory(global_role="viewer", company_ids=[crowded_platform])

    response = client.get(path, headers=_headers(viewer))

    assert response.status_code == 403, (
        f"{path} devolveu {response.status_code} para um viewer de tenant: {response.text[:400]}"
    )


@pytest.mark.parametrize("path", PLATFORM_POSTURE_ROUTES)
def test_a_tenant_operator_cannot_read_platform_wide_posture(
    path: str,
    crowded_platform,
    admin_principal_factory,
) -> None:
    """Nem o operador: o problema é o alcance, não o papel dentro da empresa."""
    operator = admin_principal_factory(global_role="operator", company_ids=[crowded_platform])

    response = client.get(path, headers=_headers(operator))

    assert response.status_code == 403, f"{path} devolveu {response.status_code}"


@pytest.mark.parametrize("path", PLATFORM_POSTURE_ROUTES)
def test_a_global_principal_still_reads_platform_posture(
    path: str,
    crowded_platform,
    admin_principal_factory,
) -> None:
    """Controle positivo: negar para todo mundo passaria nos testes acima."""
    global_admin = admin_principal_factory(global_role="superadmin", is_global=True)

    response = client.get(path, headers=_headers(global_admin))

    assert response.status_code == 200, f"{path} negou o principal global: {response.text[:300]}"


def test_the_metrics_endpoint_does_not_leak_the_company_count_to_a_tenant(
    crowded_platform,
    admin_principal_factory,
) -> None:
    """A prova concreta do vazamento que a revisão executou."""
    viewer = admin_principal_factory(global_role="viewer", company_ids=[crowded_platform])

    response = client.get("/admin/observability/metrics", headers=_headers(viewer))

    assert response.status_code == 403
    assert "project_wapp_companies" not in response.text
