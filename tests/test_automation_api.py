"""API do Automation Studio: isolamento por tenant, RBAC e concorrência.

Cobre os cenários obrigatórios 1 (tenant A nunca alcança tenant B), 18 (grafo
inválido nunca publica), 20 (rascunho concorrente gera 409) e 21 (segredo na
configuração é rejeitado) pela superfície HTTP real.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from tests.support.automation_graphs import (
    SAMPLE_MESSAGE_CONTEXT,
    approval_graph,
    branching_graph,
    cyclic_graph,
    minimal_graph,
    secret_bearing_graph,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _enable_admin_auth_and_engine(monkeypatch):
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "legacy-viewer")
    monkeypatch.setattr(settings, "admin_operator_token", "legacy-operator")
    monkeypatch.setattr(settings, "admin_superadmin_token", "legacy-superadmin")
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "automation_engine_enabled", True)


def _headers(principal: dict) -> dict[str, str]:
    return {"X-Admin-Token": principal["token"]}


@pytest.fixture()
def tenant(company_factory, admin_principal_factory):
    """Uma empresa com os três papéis, cada um com membership explícita."""

    def _build(prefix: str = "auto-api"):
        company_id = company_factory(prefix)
        return {
            "company_id": company_id,
            "viewer": admin_principal_factory(global_role="viewer", company_ids=[company_id]),
            "operator": admin_principal_factory(global_role="operator", company_ids=[company_id]),
            "admin": admin_principal_factory(
                global_role="superadmin",
                company_ids=[company_id],
            ),
        }

    return _build


def _create_workflow(tenant_ctx: dict, key: str = "fluxo-teste") -> dict:
    response = client.post(
        "/admin/automations/workflows",
        params={"company_id": tenant_ctx["company_id"]},
        json={"workflow_key": key, "name": "Fluxo de teste"},
        headers=_headers(tenant_ctx["admin"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _save_draft(tenant_ctx: dict, workflow_id: int, graph: dict, revision: int):
    return client.put(
        f"/admin/automations/workflows/{workflow_id}/draft",
        params={"company_id": tenant_ctx["company_id"]},
        json={"graph": graph, "expected_revision": revision},
        headers=_headers(tenant_ctx["admin"]),
    )


# --- catálogo -----------------------------------------------------------------------


def test_catalog_is_available_to_a_viewer(tenant) -> None:
    context = tenant()

    response = client.get(
        "/admin/automations/catalog",
        params={"company_id": context["company_id"]},
        headers=_headers(context["viewer"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "automation-graph/1"
    assert len(body["nodes"]) >= 20


def test_catalog_never_exposes_a_provider_secret(tenant) -> None:
    context = tenant()

    response = client.get(
        "/admin/automations/catalog",
        params={"company_id": context["company_id"]},
        headers=_headers(context["viewer"]),
    )

    serialized = response.text.lower()
    for marker in ("sk-", "api_key", "secret", "password", "bearer "):
        assert marker not in serialized, marker


# --- 1: isolamento entre tenants ------------------------------------------------------


def test_tenant_a_never_lists_workflows_of_tenant_b(tenant) -> None:
    tenant_a = tenant("iso-a")
    tenant_b = tenant("iso-b")
    _create_workflow(tenant_a, key="fluxo-de-a")

    response = client.get(
        "/admin/automations/workflows",
        params={"company_id": tenant_b["company_id"]},
        headers=_headers(tenant_b["viewer"]),
    )

    assert response.status_code == 200
    assert response.json() == []


def test_tenant_a_cannot_read_a_workflow_of_tenant_b(tenant) -> None:
    tenant_a = tenant("read-a")
    tenant_b = tenant("read-b")
    workflow = _create_workflow(tenant_a, key="fluxo-privado")

    response = client.get(
        f"/admin/automations/workflows/{workflow['id']}",
        params={"company_id": tenant_b["company_id"]},
        headers=_headers(tenant_b["viewer"]),
    )

    # 404 e não 403: distinguir permitiria enumerar workflows do outro tenant.
    assert response.status_code == 404


def test_tenant_a_cannot_scope_a_request_to_tenant_b(tenant) -> None:
    tenant_a = tenant("scope-a")
    tenant_b = tenant("scope-b")

    response = client.get(
        "/admin/automations/workflows",
        params={"company_id": tenant_b["company_id"]},
        headers=_headers(tenant_a["viewer"]),
    )

    assert response.status_code == 403


def test_tenant_a_cannot_edit_a_workflow_of_tenant_b(tenant) -> None:
    tenant_a = tenant("edit-a")
    tenant_b = tenant("edit-b")
    workflow = _create_workflow(tenant_a, key="fluxo-alvo")

    response = _save_draft(tenant_b, int(workflow["id"]), minimal_graph(), 1)

    assert response.status_code == 404


def test_tenant_a_cannot_execute_a_workflow_of_tenant_b(tenant) -> None:
    tenant_a = tenant("run-a")
    tenant_b = tenant("run-b")
    workflow = _create_workflow(tenant_a, key="fluxo-run")

    response = client.post(
        f"/admin/automations/workflows/{workflow['id']}/trigger",
        params={"company_id": tenant_b["company_id"]},
        json={"idempotency_key": "chave-de-outro-tenant"},
        headers=_headers(tenant_b["operator"]),
    )

    assert response.status_code == 404


def test_executions_of_another_tenant_are_not_visible(tenant) -> None:
    tenant_a = tenant("exec-a")
    tenant_b = tenant("exec-b")

    response = client.get(
        "/admin/automations/executions",
        params={"company_id": tenant_b["company_id"]},
        headers=_headers(tenant_b["viewer"]),
    )
    assert response.status_code == 200
    assert response.json() == []

    denied = client.get(
        "/admin/automations/executions",
        params={"company_id": tenant_a["company_id"]},
        headers=_headers(tenant_b["viewer"]),
    )
    assert denied.status_code == 403


def test_duplicate_workflow_key_is_scoped_to_the_company(tenant) -> None:
    """A mesma chave pode existir em empresas diferentes."""
    tenant_a = tenant("key-a")
    tenant_b = tenant("key-b")

    _create_workflow(tenant_a, key="mesma-chave")
    second = _create_workflow(tenant_b, key="mesma-chave")

    assert second["workflow_key"] == "mesma-chave"


# --- RBAC -----------------------------------------------------------------------------


def test_viewer_cannot_create_a_workflow(tenant) -> None:
    context = tenant()

    response = client.post(
        "/admin/automations/workflows",
        params={"company_id": context["company_id"]},
        json={"workflow_key": "nao-permitido", "name": "X"},
        headers=_headers(context["viewer"]),
    )

    assert response.status_code == 403


def test_operator_cannot_publish(tenant) -> None:
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-publicar")

    response = client.post(
        f"/admin/automations/workflows/{workflow['id']}/publish",
        params={"company_id": context["company_id"]},
        json={},
        headers=_headers(context["operator"]),
    )

    assert response.status_code == 403


def test_viewer_cannot_trigger_manually(tenant) -> None:
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-trigger")

    response = client.post(
        f"/admin/automations/workflows/{workflow['id']}/trigger",
        params={"company_id": context["company_id"]},
        json={"idempotency_key": "chave-do-viewer"},
        headers=_headers(context["viewer"]),
    )

    assert response.status_code == 403


def test_viewer_can_read_the_approval_queue(tenant) -> None:
    context = tenant()

    response = client.get(
        "/admin/automations/approvals",
        params={"company_id": context["company_id"]},
        headers=_headers(context["viewer"]),
    )

    assert response.status_code == 200


def test_running_the_worker_requires_a_global_superadmin(tenant) -> None:
    """Operar o worker é ação de plataforma, não de tenant."""
    context = tenant()

    response = client.post(
        "/admin/automations/worker/run-once",
        headers=_headers(context["admin"]),
    )

    assert response.status_code == 403


# --- 20: concorrência otimista no rascunho -------------------------------------------


def test_a_stale_draft_revision_returns_409(tenant) -> None:
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-concorrente")
    workflow_id = int(workflow["id"])
    revision = int(workflow["draft"]["revision"])

    first = _save_draft(context, workflow_id, minimal_graph(), revision)
    assert first.status_code == 200

    # Segundo editor ainda com a revisão antiga.
    second = _save_draft(context, workflow_id, branching_graph(), revision)

    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["expected_revision"] == revision
    assert detail["current_revision"] == revision + 1


def test_saving_with_the_current_revision_succeeds(tenant) -> None:
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-sequencial")
    workflow_id = int(workflow["id"])

    first = _save_draft(context, workflow_id, minimal_graph(), 1)
    assert first.status_code == 200
    current = int(first.json()["draft"]["revision"])

    second = _save_draft(context, workflow_id, branching_graph(), current)

    assert second.status_code == 200
    assert int(second.json()["draft"]["revision"]) == current + 1


# --- 18/21: validação e publicação ----------------------------------------------------


def test_validation_reports_every_issue_at_once(tenant) -> None:
    context = tenant()

    response = client.post(
        "/admin/automations/workflows/validate",
        params={"company_id": context["company_id"]},
        json={"graph": cyclic_graph()},
        headers=_headers(context["viewer"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_valid"] is False
    assert "cycle_detected" in {issue["code"] for issue in body["issues"]}


def test_an_invalid_graph_never_publishes(tenant) -> None:
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-invalido")
    workflow_id = int(workflow["id"])
    _save_draft(context, workflow_id, cyclic_graph(), 1)

    response = client.post(
        f"/admin/automations/workflows/{workflow_id}/publish",
        params={"company_id": context["company_id"]},
        json={},
        headers=_headers(context["admin"]),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["issues"]

    versions = client.get(
        f"/admin/automations/workflows/{workflow_id}/versions",
        params={"company_id": context["company_id"]},
        headers=_headers(context["viewer"]),
    )
    assert versions.json() == []


def test_a_graph_carrying_a_secret_never_publishes(tenant) -> None:
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-com-segredo")
    workflow_id = int(workflow["id"])
    _save_draft(context, workflow_id, secret_bearing_graph(), 1)

    response = client.post(
        f"/admin/automations/workflows/{workflow_id}/publish",
        params={"company_id": context["company_id"]},
        json={},
        headers=_headers(context["admin"]),
    )

    assert response.status_code == 422
    codes = {issue["code"] for issue in response.json()["detail"]["issues"]}
    assert "secret_like_config_key" in codes


def test_publishing_creates_a_paused_version(tenant) -> None:
    """Publicar nunca ativa: são duas ações distintas."""
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-pausado")
    workflow_id = int(workflow["id"])
    _save_draft(context, workflow_id, minimal_graph(), 1)

    published = client.post(
        f"/admin/automations/workflows/{workflow_id}/publish",
        params={"company_id": context["company_id"]},
        json={"notes": "primeira versão"},
        headers=_headers(context["admin"]),
    )

    assert published.status_code == 200
    body = published.json()
    assert body["state"] == "paused"
    assert body["version"] == 1

    described = client.get(
        f"/admin/automations/workflows/{workflow_id}",
        params={"company_id": context["company_id"]},
        headers=_headers(context["viewer"]),
    ).json()
    assert described["deployment"]["state"] == "paused"


def test_activation_is_a_separate_call(tenant) -> None:
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-ativar")
    workflow_id = int(workflow["id"])
    _save_draft(context, workflow_id, minimal_graph(), 1)
    client.post(
        f"/admin/automations/workflows/{workflow_id}/publish",
        params={"company_id": context["company_id"]},
        json={},
        headers=_headers(context["admin"]),
    )

    activated = client.post(
        f"/admin/automations/workflows/{workflow_id}/state",
        params={"company_id": context["company_id"]},
        json={"state": "active"},
        headers=_headers(context["admin"]),
    )

    assert activated.status_code == 200
    assert activated.json()["deployment"]["state"] == "active"


def test_activating_without_publishing_is_rejected(tenant) -> None:
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-sem-versao")

    response = client.post(
        f"/admin/automations/workflows/{workflow['id']}/state",
        params={"company_id": context["company_id"]},
        json={"state": "active"},
        headers=_headers(context["admin"]),
    )

    assert response.status_code == 422
    assert "not_published" in {
        issue["code"] for issue in response.json()["detail"]["issues"]
    }


# --- simulação ---------------------------------------------------------------------------


def test_simulation_produces_no_effect_and_reports_the_path(tenant) -> None:
    context = tenant()

    response = client.post(
        "/admin/automations/workflows/simulate",
        params={"company_id": context["company_id"]},
        json={
            "graph": branching_graph(),
            "sample_context": SAMPLE_MESSAGE_CONTEXT,
        },
        headers=_headers(context["viewer"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["simulation"]["produced_no_effects"] is True
    assert body["simulation"]["path"] == ["trigger", "cond", "done"]
    assert body["validation"]["is_valid"] is True


def test_simulation_answers_approval_gates_from_the_payload(tenant) -> None:
    context = tenant()

    response = client.post(
        "/admin/automations/workflows/simulate",
        params={"company_id": context["company_id"]},
        json={"graph": approval_graph(), "approval_decisions": {"gate": "rejected"}},
        headers=_headers(context["viewer"]),
    )

    assert response.status_code == 200
    assert response.json()["simulation"]["path"] == ["trigger", "gate", "stop"]


def test_simulation_rejects_an_unknown_decision(tenant) -> None:
    context = tenant()

    response = client.post(
        "/admin/automations/workflows/simulate",
        params={"company_id": context["company_id"]},
        json={"graph": approval_graph(), "approval_decisions": {"gate": "talvez"}},
        headers=_headers(context["viewer"]),
    )

    assert response.status_code == 422


# --- diff -------------------------------------------------------------------------------


def test_diff_ignores_pure_position_changes(tenant) -> None:
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-diff")
    workflow_id = int(workflow["id"])
    _save_draft(context, workflow_id, minimal_graph(), 1)
    client.post(
        f"/admin/automations/workflows/{workflow_id}/publish",
        params={"company_id": context["company_id"]},
        json={},
        headers=_headers(context["admin"]),
    )

    moved = minimal_graph()
    moved["nodes"][0]["position"] = {"x": 500, "y": 300}
    _save_draft(context, workflow_id, moved, 2)

    diff = client.get(
        f"/admin/automations/workflows/{workflow_id}/diff",
        params={"company_id": context["company_id"]},
        headers=_headers(context["viewer"]),
    ).json()

    assert diff["identical"] is True
    assert diff["nodes_changed"] == []


def test_diff_reports_behavioural_changes(tenant) -> None:
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-diff-real")
    workflow_id = int(workflow["id"])
    _save_draft(context, workflow_id, minimal_graph(), 1)
    client.post(
        f"/admin/automations/workflows/{workflow_id}/publish",
        params={"company_id": context["company_id"]},
        json={},
        headers=_headers(context["admin"]),
    )
    _save_draft(context, workflow_id, branching_graph(), 2)

    diff = client.get(
        f"/admin/automations/workflows/{workflow_id}/diff",
        params={"company_id": context["company_id"]},
        headers=_headers(context["viewer"]),
    ).json()

    assert diff["identical"] is False
    assert "cond" in diff["nodes_added"]


# --- feature flag -------------------------------------------------------------------------


def test_activation_is_refused_when_the_engine_is_disabled(tenant, monkeypatch) -> None:
    context = tenant()
    workflow = _create_workflow(context, key="fluxo-flag")
    workflow_id = int(workflow["id"])
    _save_draft(context, workflow_id, minimal_graph(), 1)
    client.post(
        f"/admin/automations/workflows/{workflow_id}/publish",
        params={"company_id": context["company_id"]},
        json={},
        headers=_headers(context["admin"]),
    )

    monkeypatch.setattr(settings, "automation_engine_enabled", False)
    response = client.post(
        f"/admin/automations/workflows/{workflow_id}/state",
        params={"company_id": context["company_id"]},
        json={"state": "active"},
        headers=_headers(context["admin"]),
    )

    assert response.status_code == 409
    assert "disabled" in response.json()["detail"]


def test_the_engine_is_disabled_by_default() -> None:
    """Nenhuma empresa passa a executar automação por causa de um deploy."""
    import os

    from app.core.config import Settings

    previous = {
        key: os.environ.pop(key, None)
        for key in ("AUTOMATION_ENGINE_ENABLED", "AUTOMATION_WORKER_ENABLED")
    }
    try:
        fresh = Settings()
        assert fresh.automation_engine_enabled is False
        assert fresh.automation_worker_enabled is False
    finally:
        for key, value in previous.items():
            if value is not None:
                os.environ[key] = value
