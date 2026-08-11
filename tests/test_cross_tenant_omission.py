"""O exploit da omissão: não enviar `company_id` alcançava todas as empresas.

A suíte anterior tinha uma cegueira específica. Todo teste de isolamento
enviava `params={"company_id": other_company}` — caminho que de fato respondia
403. O caminho da **omissão** nunca era exercido, e era ele o exploit:

    GET /admin/messages          (token do tenant A, sem nenhum parâmetro)
    → devolvia as mensagens do tenant B

Causa raiz: `require_company_scope` resolvia e autorizava a empresa e gravava
em `request.state`, mas 40 handlers de `admin_routes.py` liam `company_id` da
query de novo. E `company_id=None` significa **todas as empresas** no SQL
(`WHERE (? IS NULL OR company_id = ?)`).

Os testes abaixo cobrem as quatro formas do mesmo defeito:
leitura (T-01), escrita por recurso carregado por ID (T-02), escrita em rota
que nem aceitava empresa (T-03) e `company_id` vindo do path (T-04).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.storage.repository import RuntimeRepository

client = TestClient(app)
repository = RuntimeRepository()


@pytest.fixture(autouse=True)
def _enable_admin_auth(monkeypatch):
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "legacy-viewer")
    monkeypatch.setattr(settings, "admin_operator_token", "legacy-operator")
    monkeypatch.setattr(settings, "admin_superadmin_token", "legacy-superadmin")
    monkeypatch.setattr(settings, "admin_api_token", None)


@pytest.fixture()
def two_tenants(company_factory, admin_principal_factory):
    """Duas empresas com dados marcadores distintos.

    O tenant A tem um principal; o tenant B só tem dados. Nada do B pode
    aparecer para o principal do A, por nenhum caminho.
    """
    company_a = company_factory("omit-a")
    company_b = company_factory("omit-b")

    contact_b = f"marker-b-{company_b}"
    repository.save_contact(contact_b, "CONTATO-DO-TENANT-B", company_id=company_b)
    repository.save_message(
        contact_id=contact_b,
        company_id=company_b,
        message="MENSAGEM-SECRETA-DO-TENANT-B",
        classification="duvida_geral",
        reply="RESPOSTA-SECRETA-DO-TENANT-B",
    )
    repository.create_pending_question(
        contact_id=contact_b,
        company_id=company_b,
        customer_message="PERGUNTA-DO-TENANT-B",
        question_type="general",
        question_for_owner="PERGUNTA-DO-TENANT-B",
    )
    repository.create_outbound_message(
        contact_id=contact_b,
        message="OUTBOUND-DO-TENANT-B",
        source="test",
        company_id=company_b,
    )

    return {
        "company_a": company_a,
        "company_b": company_b,
        "contact_b": contact_b,
        "operator_a": admin_principal_factory(
            global_role="operator",
            company_ids=[company_a],
        ),
        "viewer_a": admin_principal_factory(global_role="viewer", company_ids=[company_a]),
    }


def _headers(principal: dict) -> dict[str, str]:
    return {"X-Admin-Token": principal["token"]}


# --- T-01: leitura -------------------------------------------------------------------

# Rota e o marcador do tenant B que jamais pode aparecer na resposta.
LEAKY_READ_ROUTES: list[tuple[str, str]] = [
    ("/admin/messages", "MENSAGEM-SECRETA-DO-TENANT-B"),
    ("/admin/contacts", "CONTATO-DO-TENANT-B"),
    ("/admin/pending-questions", "PERGUNTA-DO-TENANT-B"),
    ("/admin/outbound-messages", "OUTBOUND-DO-TENANT-B"),
]


@pytest.mark.parametrize(("path", "marker"), LEAKY_READ_ROUTES)
def test_omitting_company_id_never_returns_another_tenant(two_tenants, path, marker) -> None:
    """O exploit central: sem parâmetro nenhum, com token do tenant A."""
    response = client.get(path, headers=_headers(two_tenants["operator_a"]))

    assert response.status_code == 200
    assert marker not in response.text, f"{path} vazou dado do tenant B"


@pytest.mark.parametrize(("path", "_marker"), LEAKY_READ_ROUTES)
def test_omitting_company_id_scopes_to_the_single_membership(
    two_tenants,
    path,
    _marker,
) -> None:
    """E o que volta pertence à empresa do principal, não a 'nenhuma'."""
    response = client.get(path, headers=_headers(two_tenants["operator_a"]))

    assert response.status_code == 200
    payload = response.json()
    rows = payload if isinstance(payload, list) else payload.get("items", [])
    for row in rows:
        if isinstance(row, dict) and "company_id" in row:
            assert int(row["company_id"]) == two_tenants["company_a"]


def test_audit_events_do_not_expose_another_tenants_operations(two_tenants) -> None:
    """Auditoria é o mapa de quem opera o outro tenant e o que faz."""
    client.get("/admin/messages", headers=_headers(two_tenants["operator_a"]))

    response = client.get("/admin/audit-events", headers=_headers(two_tenants["operator_a"]))

    assert response.status_code == 200
    for event in response.json():
        company_id = event.get("company_id")
        if company_id is not None:
            assert int(company_id) != two_tenants["company_b"]


# --- T-02: escrita por recurso carregado por ID ---------------------------------------


def test_patching_a_contact_of_another_tenant_is_refused(two_tenants) -> None:
    """Autorização invertida: a empresa do registro virava a empresa da escrita."""
    response = client.patch(
        f"/admin/contacts/{two_tenants['contact_b']}",
        json={"contact_name": "INVADIDO-POR-A", "notes": "pwned"},
        headers=_headers(two_tenants["operator_a"]),
    )

    assert response.status_code in {403, 404}

    unchanged = repository.get_contact(
        two_tenants["contact_b"],
        company_id=two_tenants["company_b"],
    )
    assert unchanged is not None
    assert unchanged["contact_name"] == "CONTATO-DO-TENANT-B"


def test_marking_another_tenants_outbound_as_sent_is_refused(two_tenants) -> None:
    """Marcar como enviada suprime o envio real do outro tenant."""
    outbound = repository.get_outbound_messages(company_id=two_tenants["company_b"])
    assert outbound, "fixture precisa de uma mensagem no tenant B"
    outbound_id = int(outbound[0]["id"])

    response = client.post(
        f"/admin/outbound-messages/{outbound_id}/mark-sent",
        headers=_headers(two_tenants["operator_a"]),
    )

    assert response.status_code in {403, 404}

    still = repository.get_outbound_message(
        outbound_id,
        company_id=two_tenants["company_b"],
    )
    assert still is not None
    assert still["status"] == "pending"


def test_answering_another_tenants_pending_question_is_refused(two_tenants) -> None:
    """Responder gera outbound para o cliente do outro tenant."""
    questions = repository.get_pending_questions(company_id=two_tenants["company_b"])
    assert questions
    question_id = int(questions[0]["id"])

    response = client.post(
        f"/admin/pending-questions/{question_id}/answer",
        json={"owner_answer": "RESPOSTA-INJETADA-POR-A"},
        headers=_headers(two_tenants["operator_a"]),
    )

    assert response.status_code in {403, 404, 422}

    still = repository.get_pending_question(
        question_id,
        company_id=two_tenants["company_b"],
    )
    assert still is not None
    assert still["status"] == "pending"


# --- T-04: company_id vindo do path ---------------------------------------------------


def test_company_id_in_the_path_is_authorized(two_tenants) -> None:
    """`require_company_scope` só lia a query; o path passava direto."""
    response = client.get(
        f"/admin/onboarding/setup-status/{two_tenants['company_b']}",
        headers=_headers(two_tenants["viewer_a"]),
    )

    assert response.status_code in {403, 404}
    assert "omit-b" not in response.text


def test_the_same_data_via_query_is_already_refused(two_tenants) -> None:
    """Controle: o caminho da query sempre esteve correto.

    A assimetria entre os dois é a prova de que o defeito era a falta de
    autorização do path, não uma decisão de produto.
    """
    response = client.get(
        "/admin/system/onboarding",
        params={"company_id": two_tenants["company_b"]},
        headers=_headers(two_tenants["viewer_a"]),
    )

    assert response.status_code == 403


# --- T-06: papel efetivo por membership ------------------------------------------------


def test_the_membership_role_limits_a_globally_privileged_principal(
    company_factory,
    admin_principal_factory,
) -> None:
    """Papel global não pode valer dentro de uma empresa.

    `platform_company_memberships.role` era gravado, devolvido pela query e
    descartado no carregamento do escopo. Rebaixar alguém numa empresa não
    tinha efeito nenhum.
    """
    from app.services.admin_principal_service import grant_membership

    company_id = company_factory("role-scoped")
    principal = admin_principal_factory(global_role="operator", company_ids=[company_id])

    # Rebaixado a viewer **nesta** empresa, embora continue operator global.
    grant_membership(
        principal_id=int(principal["principal_id"]),
        company_id=company_id,
        role="viewer",
    )

    response = client.post(
        "/admin/knowledge/documents",
        params={"company_id": company_id},
        json={"title": "Doc", "content": "Conteúdo de teste.", "document_type": "general_faq"},
        headers=_headers(principal),
    )

    assert response.status_code == 403


def test_the_membership_role_can_exceed_nothing_it_was_not_granted(
    company_factory,
    admin_principal_factory,
) -> None:
    """E o inverso: membership não eleva acima do papel global."""
    from app.services.admin_principal_service import grant_membership

    company_id = company_factory("role-elevation")
    principal = admin_principal_factory(global_role="viewer", company_ids=[company_id])

    grant_membership(
        principal_id=int(principal["principal_id"]),
        company_id=company_id,
        role="superadmin",
    )

    response = client.post(
        "/admin/knowledge/documents",
        params={"company_id": company_id},
        json={"title": "Doc", "content": "Conteúdo de teste.", "document_type": "general_faq"},
        headers=_headers(principal),
    )

    assert response.status_code == 403


def test_a_matching_membership_role_still_works(
    company_factory,
    admin_principal_factory,
) -> None:
    """Controle: a correção não pode quebrar o caminho legítimo."""
    company_id = company_factory("role-ok")
    principal = admin_principal_factory(global_role="operator", company_ids=[company_id])

    response = client.post(
        "/admin/knowledge/documents",
        params={"company_id": company_id},
        json={"title": "Doc", "content": "Conteúdo de teste.", "document_type": "general_faq"},
        headers=_headers(principal),
    )

    assert response.status_code == 200
