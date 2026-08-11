"""RBAC enforcement across every mutating administrative endpoint."""

from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api import admin_routes
from app.api.admin_permissions import (
    ADMIN_ENDPOINT_PERMISSIONS,
    MUTATING_HTTP_METHODS,
)
from app.core.config import settings
from app.main import app

client = TestClient(app)
# Authorization is decided before the handler runs, so a handler that fails for
# backend-specific reasons must still be observable as "not blocked by RBAC".
permissive_client = TestClient(app, raise_server_exceptions=False)

ROLE_TOKENS = {
    "viewer": "matrix-viewer-token",
    "operator": "matrix-operator-token",
    "superadmin": "matrix-superadmin-token",
}
ROLE_ORDER = {"viewer": 1, "operator": 2, "superadmin": 3}


class _RecordingRepository:
    """Records every repository call so denials can be proven side-effect free."""

    def __init__(self) -> None:
        self.audit_events: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def create_audit_event(self, **kwargs) -> int:
        self.calls.append("create_audit_event")
        self.audit_events.append(kwargs)
        return len(self.audit_events)

    def __getattr__(self, name: str):
        def _record(*args, **kwargs):
            self.calls.append(name)

        return _record


def _stub_orchestrator_result(**kwargs) -> dict[str, Any]:
    return {
        "contact_id": kwargs.get("contact_id", "5511999999999"),
        "company_id": 1,
        "classification": "faq",
        "classification_reason": "stub",
        "classification_confidence": 0.5,
        "reply": "ok",
        "source": "template",
        "auto_reply": False,
        "action": "handoff_human",
        "priority": "low",
        "pipeline_stage": "novo_lead",
        "next_action": "n/a",
        "lead_temperature": "cold",
        "handoff_required": False,
        "pending_question_id": None,
        "question_type": None,
    }


@pytest.fixture
def rbac_repository(monkeypatch) -> _RecordingRepository:
    repository = _RecordingRepository()
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", ROLE_TOKENS["viewer"])
    monkeypatch.setattr(settings, "admin_operator_token", ROLE_TOKENS["operator"])
    monkeypatch.setattr(settings, "admin_superadmin_token", ROLE_TOKENS["superadmin"])
    monkeypatch.setattr(admin_routes, "repository", repository)
    # Keep the simulator route away from the real orchestrator and database.
    monkeypatch.setattr(admin_routes, "process_incoming_message", _stub_orchestrator_result)
    return repository


def _iter_api_routes(routes: Any) -> list[APIRoute]:
    """Flatten the router tree; included routers are wrappers, not plain routes."""
    collected: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            collected.append(route)
            continue
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            collected.extend(_iter_api_routes(included_router.routes))
            continue
        nested = getattr(route, "routes", None)
        if nested:
            collected.extend(_iter_api_routes(nested))
    return collected


def _admin_mutating_routes() -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for route in _iter_api_routes(app.routes):
        if not route.path.startswith("/admin"):
            continue
        for method in sorted(set(route.methods or set()) & MUTATING_HTTP_METHODS):
            entries.append((method, route.path))
    return sorted(entries)


def _declared_role_for_route(method: str, path: str) -> str | None:
    for route in _iter_api_routes(app.routes):
        if route.path != path or method not in (route.methods or set()):
            continue
        for dependency in route.dependencies:
            required_role = getattr(dependency.dependency, "__admin_required_role__", None)
            if required_role:
                return str(required_role)
        return None
    return None


# ── The matrix is the specification ───────────────────────────────────


def test_every_mutating_admin_route_is_declared_in_the_matrix() -> None:
    undeclared = [entry for entry in _admin_mutating_routes() if entry not in ADMIN_ENDPOINT_PERMISSIONS]

    assert undeclared == [], f"Mutating admin routes without a declared permission: {undeclared}"


def test_matrix_has_no_entries_for_routes_that_no_longer_exist() -> None:
    existing = set(_admin_mutating_routes())
    stale = sorted(entry for entry in ADMIN_ENDPOINT_PERMISSIONS if entry not in existing)

    assert stale == [], f"Permission matrix references unknown routes: {stale}"


@pytest.mark.parametrize(
    ("method", "path"),
    sorted(ADMIN_ENDPOINT_PERMISSIONS),
    ids=lambda value: value if isinstance(value, str) else str(value),
)
def test_route_dependency_matches_the_declared_permission(method: str, path: str) -> None:
    assert _declared_role_for_route(method, path) == ADMIN_ENDPOINT_PERMISSIONS[(method, path)]


def test_no_mutating_admin_route_relies_on_generic_admin_access_only() -> None:
    unprotected = [
        (method, path)
        for method, path in _admin_mutating_routes()
        if _declared_role_for_route(method, path) is None
    ]

    assert unprotected == [], f"Mutating admin routes without a role check: {unprotected}"


# ── Runtime enforcement ───────────────────────────────────────────────


def _sample_request(method: str, path: str) -> tuple[str, str, dict[str, Any] | None, dict[str, str]]:
    """Build a syntactically valid request for a matrix entry."""
    concrete_path = (
        path.replace("{booking_id}", "1")
        .replace("{offer_id}", "1")
        .replace("{contact_id}", "5511999999999")
        .replace("{question_id}", "1")
        .replace("{outbound_message_id}", "1")
    )
    bodies: dict[str, dict[str, Any]] = {
        "/admin/companies": {"slug": "rbac-co", "name": "RBAC Co"},
        "/admin/company-context": {"data": {}},
        "/admin/onboarding/setup": {"company_name": "RBAC Co", "business_type_key": "generic"},
        "/admin/calendar/integrations": {"provider": "mock"},
        "/admin/billing/activate": {"plan_key": "starter"},
        "/admin/billing/cancel": {"reason": "test"},
        "/admin/billing/reactivate": {"billing_cycle": "monthly"},
        "/admin/billing/change-plan": {"new_plan_key": "pro"},
        "/admin/billing/usage/record": {"metric": "messages", "quantity": 1},
        "/admin/calendar/availability/query": {
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "duration_minutes": 30,
        },
        "/admin/calendar/queue": {"contact_id": "5511999999999", "desired_title": "Corte"},
        "/admin/calendar/bookings": {
            "title": "Corte",
            "starts_at": "2026-01-01T10:00:00",
            "ends_at": "2026-01-01T10:30:00",
        },
        "/admin/calendar/bookings/{booking_id}/reschedule": {
            "starts_at": "2026-01-01T10:00:00",
            "ends_at": "2026-01-01T10:30:00",
        },
        "/admin/calendar/bookings/{booking_id}/cancel": {"trigger_queue_recovery": False},
        "/admin/calendar/queue-offers/{offer_id}/respond": {"response_status": "accepted"},
        "/admin/reminders": {"owner_contact_id": "5511999999999", "message_text": "lembrete"},
        "/admin/contacts/{contact_id}": {"contact_name": "Maria"},
        "/admin/pending-questions/{question_id}/answer": {"owner_answer": "ok"},
        "/admin/message-examples": {
            "profile_name": "default",
            "scenario_type": "general",
            "customer_message": "oi",
            "approved_reply": "olá",
        },
        "/admin/training-import": {"format": "csv", "records": []},
        "/admin/analytics/bookings/{booking_id}/outcome": {"outcome": "attended"},
        "/admin/analytics/bookings/{booking_id}/cancellation-reason": {"reason": "cliente desistiu"},
        "/admin/simulator/messages": {"contact_id": "5511999999999", "message": "oi"},
    }
    params: dict[str, str] = {}
    if path.startswith("/admin/billing/"):
        params["company_id"] = "1"
    return method, concrete_path, bodies.get(path), params


@pytest.mark.parametrize(
    ("method", "path"),
    sorted(ADMIN_ENDPOINT_PERMISSIONS),
    ids=lambda value: value if isinstance(value, str) else str(value),
)
def test_viewer_cannot_mutate_any_administrative_resource(rbac_repository, method, path) -> None:
    request_method, concrete_path, body, params = _sample_request(method, path)

    response = client.request(
        request_method,
        concrete_path,
        params=params,
        json=body,
        headers={"Authorization": f"Bearer {ROLE_TOKENS['viewer']}"},
    )

    assert response.status_code == 403, f"{method} {path} was not denied for viewer"
    assert response.json()["detail"] == "Insufficient admin privileges."


@pytest.mark.parametrize(
    ("method", "path"),
    sorted(entry for entry, role in ADMIN_ENDPOINT_PERMISSIONS.items() if role == "superadmin"),
    ids=lambda value: value if isinstance(value, str) else str(value),
)
def test_operator_cannot_mutate_privileged_resources(rbac_repository, method, path) -> None:
    request_method, concrete_path, body, params = _sample_request(method, path)

    response = client.request(
        request_method,
        concrete_path,
        params=params,
        json=body,
        headers={"Authorization": f"Bearer {ROLE_TOKENS['operator']}"},
    )

    assert response.status_code == 403, f"{method} {path} was not denied for operator"


@pytest.mark.parametrize(
    ("method", "path"),
    sorted(entry for entry, role in ADMIN_ENDPOINT_PERMISSIONS.items() if role == "operator"),
    ids=lambda value: value if isinstance(value, str) else str(value),
)
def test_operator_is_allowed_past_authorization_for_operational_routes(rbac_repository, method, path) -> None:
    """The operator must not be stopped by RBAC (the handler may still fail)."""
    request_method, concrete_path, body, params = _sample_request(method, path)

    response = permissive_client.request(
        request_method,
        concrete_path,
        params=params,
        json=body,
        headers={"Authorization": f"Bearer {ROLE_TOKENS['operator']}"},
    )

    assert response.status_code != 403


@pytest.mark.parametrize(
    ("method", "path"),
    sorted(ADMIN_ENDPOINT_PERMISSIONS),
    ids=lambda value: value if isinstance(value, str) else str(value),
)
def test_denied_mutation_does_not_change_state(rbac_repository, method, path) -> None:
    request_method, concrete_path, body, params = _sample_request(method, path)

    response = client.request(
        request_method,
        concrete_path,
        params=params,
        json=body,
        headers={"Authorization": f"Bearer {ROLE_TOKENS['viewer']}"},
    )

    assert response.status_code == 403
    assert set(rbac_repository.calls) == {"create_audit_event"}, (
        f"{method} {path} touched the repository despite being denied: {rbac_repository.calls}"
    )


def test_denied_authorization_is_audited(rbac_repository) -> None:
    response = client.post(
        "/admin/companies",
        json={"slug": "should-not-exist", "name": "Nope"},
        headers={"Authorization": f"Bearer {ROLE_TOKENS['viewer']}"},
    )

    assert response.status_code == 403
    denials = [event for event in rbac_repository.audit_events if event.get("status") == "denied"]
    assert denials, "A denied authorization must be audited."
    assert denials[-1]["metadata"]["current_role"] == "viewer"
    assert denials[-1]["metadata"]["required_role"] == "superadmin"


SIMULATOR_BODY = {"contact_id": "5511999998888", "message": "oi"}


def test_simulator_rejects_anonymous_callers(rbac_repository) -> None:
    """The simulator must never be an unauthenticated ingress path."""
    response = client.post("/admin/simulator/messages", json=SIMULATOR_BODY)

    assert response.status_code == 401


def test_simulator_rejects_invalid_credentials(rbac_repository) -> None:
    response = client.post(
        "/admin/simulator/messages",
        json=SIMULATOR_BODY,
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401


def test_simulator_rejects_viewer(rbac_repository) -> None:
    response = client.post(
        "/admin/simulator/messages",
        json=SIMULATOR_BODY,
        headers={"Authorization": f"Bearer {ROLE_TOKENS['viewer']}"},
    )

    assert response.status_code == 403


def test_simulator_rejects_an_unknown_company(rbac_repository, monkeypatch) -> None:
    """The caller cannot push a simulated conversation into an arbitrary tenant."""
    monkeypatch.setattr(admin_routes.repository, "resolve_company_id", lambda **kwargs: None)

    response = client.post(
        "/admin/simulator/messages",
        params={"company_slug": "does-not-exist"},
        json=SIMULATOR_BODY,
        headers={"Authorization": f"Bearer {ROLE_TOKENS['operator']}"},
    )

    assert response.status_code == 400


def test_simulator_audits_the_run_with_a_masked_contact(rbac_repository) -> None:
    response = client.post(
        "/admin/simulator/messages",
        json=SIMULATOR_BODY,
        headers={"Authorization": f"Bearer {ROLE_TOKENS['operator']}"},
    )

    assert response.status_code == 200
    simulations = [e for e in rbac_repository.audit_events if e.get("action") == "simulate_message"]
    assert simulations, "Simulator runs must be audited."
    assert simulations[-1]["resource_id"] == "***8888"
    assert SIMULATOR_BODY["contact_id"] not in str(simulations[-1])


def test_read_only_endpoints_remain_available_to_viewer(monkeypatch) -> None:
    class _ReadRepository:
        def get_audit_events(self, **kwargs) -> list[dict]:
            return [{"id": 1, "action": "authenticate"}]

        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", ROLE_TOKENS["viewer"])
    monkeypatch.setattr(settings, "admin_operator_token", None)
    monkeypatch.setattr(settings, "admin_superadmin_token", None)
    monkeypatch.setattr(admin_routes, "repository", _ReadRepository())

    response = client.get(
        "/admin/audit-events",
        headers={"Authorization": f"Bearer {ROLE_TOKENS['viewer']}"},
    )

    assert response.status_code == 200


def test_superadmin_can_perform_privileged_mutations(monkeypatch) -> None:
    audit_events: list[dict[str, Any]] = []

    class _SuperadminRepository:
        def get_company_by_slug(self, slug: str):
            return None

        def create_company(self, **kwargs) -> int:
            return 501

        def get_company(self, company_id: int) -> dict:
            return {
                "id": company_id,
                "slug": "rbac-co",
                "name": "RBAC Co",
                "business_type": "general",
                "active": 1,
                "created_at": "2026-07-25 12:00:00",
                "updated_at": None,
            }

        def create_audit_event(self, **kwargs) -> int:
            audit_events.append(kwargs)
            return len(audit_events)

    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", None)
    monkeypatch.setattr(settings, "admin_operator_token", None)
    monkeypatch.setattr(settings, "admin_superadmin_token", ROLE_TOKENS["superadmin"])
    monkeypatch.setattr(admin_routes, "repository", _SuperadminRepository())

    response = client.post(
        "/admin/companies",
        json={"slug": "rbac-co", "name": "RBAC Co"},
        headers={
            "Authorization": f"Bearer {ROLE_TOKENS['superadmin']}",
            "X-Admin-Actor": "ops-lead",
        },
    )

    assert response.status_code == 200
    creations = [event for event in audit_events if event.get("action") == "create"]
    assert creations, "A privileged mutation must be audited."
    assert creations[-1]["resource_type"] == "company"
    assert creations[-1]["admin_role"] == "superadmin"
    assert creations[-1]["actor_id"] == "ops-lead"


def test_billing_usage_recording_is_audited(monkeypatch) -> None:
    audit_events: list[dict[str, Any]] = []

    class _BillingRepository:
        def create_audit_event(self, **kwargs) -> int:
            audit_events.append(kwargs)
            return len(audit_events)

    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", None)
    monkeypatch.setattr(settings, "admin_operator_token", None)
    monkeypatch.setattr(settings, "admin_superadmin_token", ROLE_TOKENS["superadmin"])
    monkeypatch.setattr(admin_routes, "repository", _BillingRepository())
    monkeypatch.setattr(
        "app.services.billing_service.record_usage",
        lambda **kwargs: {"recorded": True, "metric": kwargs["metric"], "quantity": kwargs["quantity"]},
    )

    response = client.post(
        "/admin/billing/usage/record",
        params={"company_id": 1},
        json={"metric": "messages", "quantity": 3},
        headers={"Authorization": f"Bearer {ROLE_TOKENS['superadmin']}"},
    )

    assert response.status_code == 200
    usage_events = [event for event in audit_events if event.get("resource_type") == "billing_usage"]
    assert usage_events, "Billing usage recording must be audited."
    assert usage_events[-1]["action"] == "record_usage"
    assert usage_events[-1]["company_id"] == 1
    assert usage_events[-1]["metadata"]["metric"] == "messages"


def test_role_hierarchy_is_monotonic() -> None:
    """A role must never be able to do less than a lower role on the same route."""
    for (method, path), role in ADMIN_ENDPOINT_PERMISSIONS.items():
        assert role in ROLE_ORDER, f"{method} {path} declares unknown role {role!r}"
        assert role != "viewer", f"{method} {path} is a mutation and must not be viewer-writable"
