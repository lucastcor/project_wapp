"""Matriz de autorização administrativa por tenant.

Papel global não basta: cada principal só alcança as empresas da sua
membership. Superadmin global é a única exceção.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import admin_principal_service
from app.services.admin_principal_service import (
    AdminPrincipalError,
    build_legacy_principal,
    create_principal_with_token,
    revoke_membership,
    revoke_principal_token,
)
from app.services.platform_session_service import issue_platform_session

client = TestClient(app)


@pytest.fixture(autouse=True)
def _enable_admin_auth(monkeypatch):
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "legacy-viewer")
    monkeypatch.setattr(settings, "admin_operator_token", "legacy-operator")
    monkeypatch.setattr(settings, "admin_superadmin_token", "legacy-superadmin")
    monkeypatch.setattr(settings, "admin_api_token", None)


@pytest.fixture()
def two_tenants(company_factory):
    return company_factory("authz-a"), company_factory("authz-b")


def _headers(principal: dict) -> dict[str, str]:
    return {"X-Admin-Token": principal["token"]}


# --- leitura -----------------------------------------------------------------------


def test_viewer_can_read_its_own_company(two_tenants, admin_principal_factory) -> None:
    company_a, _ = two_tenants
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_a])

    response = client.get(
        "/admin/knowledge/documents",
        params={"company_id": company_a},
        headers=_headers(viewer),
    )

    assert response.status_code == 200


def test_viewer_cannot_read_another_company(two_tenants, admin_principal_factory) -> None:
    company_a, company_b = two_tenants
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_a])

    response = client.get(
        "/admin/knowledge/documents",
        params={"company_id": company_b},
        headers=_headers(viewer),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Company access denied for this principal."


def test_viewer_cannot_mutate_its_own_company(two_tenants, admin_principal_factory) -> None:
    company_a, _ = two_tenants
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_a])

    response = client.post(
        "/admin/knowledge/documents",
        params={"company_id": company_a},
        json={"title": "t", "content": "c"},
        headers=_headers(viewer),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient admin privileges."


def test_operator_can_mutate_its_own_company(two_tenants, admin_principal_factory) -> None:
    company_a, _ = two_tenants
    operator = admin_principal_factory(global_role="operator", company_ids=[company_a])

    response = client.post(
        "/admin/knowledge/documents",
        params={"company_id": company_a},
        json={"title": "Política", "content": "Conteúdo da política interna."},
        headers=_headers(operator),
    )

    assert response.status_code == 200


def test_operator_cannot_mutate_another_company(two_tenants, admin_principal_factory) -> None:
    company_a, company_b = two_tenants
    operator = admin_principal_factory(global_role="operator", company_ids=[company_a])

    response = client.post(
        "/admin/knowledge/documents",
        params={"company_id": company_b},
        json={"title": "Política", "content": "Conteúdo."},
        headers=_headers(operator),
    )

    assert response.status_code == 403


def test_global_superadmin_reaches_any_company(two_tenants, admin_principal_factory) -> None:
    company_a, company_b = two_tenants
    superadmin = admin_principal_factory(global_role="superadmin", is_global=True)

    for company_id in (company_a, company_b):
        response = client.get(
            "/admin/knowledge/documents",
            params={"company_id": company_id},
            headers=_headers(superadmin),
        )
        assert response.status_code == 200


def test_scoped_principal_must_name_the_company_when_it_has_many(
    two_tenants,
    admin_principal_factory,
) -> None:
    company_a, company_b = two_tenants
    operator = admin_principal_factory(
        global_role="operator",
        company_ids=[company_a, company_b],
    )

    response = client.get("/admin/knowledge/documents", headers=_headers(operator))

    assert response.status_code == 400
    assert "company_id or company_slug is required" in response.json()["detail"]


def test_single_company_principal_gets_implicit_scope(
    two_tenants,
    admin_principal_factory,
) -> None:
    company_a, _ = two_tenants
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_a])

    response = client.get("/admin/knowledge/documents", headers=_headers(viewer))

    assert response.status_code == 200


def test_principal_without_membership_is_denied(admin_principal_factory) -> None:
    orphan = admin_principal_factory(global_role="viewer", company_ids=[])

    response = client.get("/admin/knowledge/documents", headers=_headers(orphan))

    assert response.status_code == 403


def test_revoked_membership_takes_effect_immediately(
    two_tenants,
    admin_principal_factory,
) -> None:
    company_a, _ = two_tenants
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_a])

    assert client.get(
        "/admin/knowledge/documents",
        params={"company_id": company_a},
        headers=_headers(viewer),
    ).status_code == 200

    revoke_membership(principal_id=viewer["principal_id"], company_id=company_a)

    assert client.get(
        "/admin/knowledge/documents",
        params={"company_id": company_a},
        headers=_headers(viewer),
    ).status_code == 403


def test_revoked_token_is_rejected(two_tenants, admin_principal_factory) -> None:
    company_a, _ = two_tenants
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_a])

    assert revoke_principal_token(token_id=viewer["token_id"])

    response = client.get(
        "/admin/knowledge/documents",
        params={"company_id": company_a},
        headers=_headers(viewer),
    )

    assert response.status_code == 401


def test_x_admin_actor_is_not_identity(two_tenants, admin_principal_factory) -> None:
    """O header de ator não pode promover ninguém nem trocar de principal."""
    company_a, company_b = two_tenants
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_a])

    response = client.get(
        "/admin/knowledge/documents",
        params={"company_id": company_b},
        headers={**_headers(viewer), "X-Admin-Actor": "superadmin-global"},
    )

    assert response.status_code == 403


def test_companies_listing_is_scoped(two_tenants, admin_principal_factory) -> None:
    company_a, company_b = two_tenants
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_a])

    response = client.get("/admin/companies", headers=_headers(viewer))

    assert response.status_code == 200
    visible = {company["id"] for company in response.json()}
    assert company_a in visible
    assert company_b not in visible


def test_company_detail_of_another_tenant_is_not_enumerable(
    two_tenants,
    admin_principal_factory,
) -> None:
    company_a, company_b = two_tenants
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_a])

    existing = client.get(f"/admin/companies/{company_b}", headers=_headers(viewer))
    missing = client.get("/admin/companies/99999999", headers=_headers(viewer))

    # Mesma resposta para "existe mas não é sua" e "não existe".
    assert existing.status_code == missing.status_code == 404


# --- channel bindings ------------------------------------------------------------------


def test_channel_bindings_do_not_leak_other_tenants(
    two_tenants,
    admin_principal_factory,
) -> None:
    company_a, company_b = two_tenants
    superadmin = admin_principal_factory(global_role="superadmin", is_global=True)
    client.post(
        "/admin/channel-bindings",
        params={"company_id": company_b},
        json={"provider": "whatsapp", "external_id": "phone-secreto-b"},
        headers=_headers(superadmin),
    )

    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_a])
    response = client.get(
        "/admin/channel-bindings",
        params={"company_id": company_a},
        headers=_headers(viewer),
    )

    assert response.status_code == 200
    assert response.json() == []
    assert "phone-secreto-b" not in response.text


def test_channel_binding_external_id_is_masked_by_default(
    two_tenants,
    admin_principal_factory,
) -> None:
    company_a, _ = two_tenants
    superadmin = admin_principal_factory(global_role="superadmin", is_global=True)
    client.post(
        "/admin/channel-bindings",
        params={"company_id": company_a},
        json={"provider": "whatsapp", "external_id": "phone-number-id-123456"},
        headers=_headers(superadmin),
    )

    operator = admin_principal_factory(global_role="operator", company_ids=[company_a])
    masked = client.get(
        "/admin/channel-bindings",
        params={"company_id": company_a},
        headers=_headers(operator),
    ).json()[0]

    assert masked["external_id_masked"] is True
    assert "phone-number-id-123456" not in str(masked)
    assert masked["external_id"].endswith("3456")

    revealed = client.get(
        "/admin/channel-bindings",
        params={"company_id": company_a, "reveal_external_id": True},
        headers=_headers(superadmin),
    ).json()
    revealed_ids = {binding["external_id"] for binding in revealed}
    assert "phone-number-id-123456" in revealed_ids


def test_global_channel_binding_listing_requires_global_superadmin(
    two_tenants,
    admin_principal_factory,
) -> None:
    company_a, _ = two_tenants
    operator = admin_principal_factory(global_role="operator", company_ids=[company_a])

    response = client.get(
        "/admin/channel-bindings",
        params={"company_id": company_a, "include_all_companies": True},
        headers=_headers(operator),
    )

    assert response.status_code == 403


# --- SSE ---------------------------------------------------------------------------------


def test_sse_uses_the_same_company_authorization(
    two_tenants,
    admin_principal_factory,
) -> None:
    company_a, company_b = two_tenants
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_a])
    session = issue_platform_session(
        role="viewer",
        actor="viewer",
        principal_id=viewer["principal_id"],
    )

    with TestClient(app) as local_client:
        local_client.cookies.set("project_wapp_platform_session", session["token"])
        denied = local_client.get("/platform/events", params={"company_id": company_b})

    assert denied.status_code == 403


# --- tokens legados ------------------------------------------------------------------------


def test_legacy_operator_token_is_not_global_in_production(monkeypatch) -> None:
    monkeypatch.setattr(admin_principal_service, "is_production_like", lambda: True)

    principal = build_legacy_principal("operator", "legacy", auth_mode="legacy_token")

    assert principal.is_global is False
    assert principal.allowed_company_ids == frozenset()


def test_legacy_superadmin_token_stays_global_in_production(monkeypatch) -> None:
    monkeypatch.setattr(admin_principal_service, "is_production_like", lambda: True)

    principal = build_legacy_principal("superadmin", "legacy", auth_mode="legacy_token")

    assert principal.is_global is True


def test_legacy_global_behaviour_is_explicitly_configurable(monkeypatch) -> None:
    monkeypatch.setattr(admin_principal_service, "is_production_like", lambda: False)
    monkeypatch.setattr(settings, "allow_legacy_global_admin_tokens", False)

    principal = build_legacy_principal("viewer", "legacy", auth_mode="legacy_token")

    assert principal.is_global is False


def test_non_superadmin_principal_cannot_be_global() -> None:
    with pytest.raises(AdminPrincipalError, match="Only superadmin"):
        create_principal_with_token(
            principal_key="bad-global",
            display_name="Bad",
            global_role="operator",
            is_global=True,
        )


def test_principal_token_is_returned_once_and_stored_as_hash(
    two_tenants,
    admin_principal_factory,
) -> None:
    company_a, _ = two_tenants
    principal = admin_principal_factory(global_role="viewer", company_ids=[company_a])
    superadmin = admin_principal_factory(global_role="superadmin", is_global=True)

    described = client.get(
        f"/admin/principals/{principal['principal_id']}",
        headers=_headers(superadmin),
    ).json()

    assert principal["token"] not in str(described)
    assert all("token_hash" not in token for token in described["tokens"])
    assert described["tokens"][0]["token_prefix"] == principal["token_prefix"]
