"""A signed Meta event must derive its tenant from phone_number_id alone."""

import hashlib
import hmac
import json
import sqlite3
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import admin_routes, routes
from app.core.config import settings
from app.main import app
from app.services import outbound_dispatch_service
from app.services.whatsapp_provider import WhatsAppProvider, WhatsAppProviderError
from app.storage.repository import RuntimeRepository


client = TestClient(app)
repository = RuntimeRepository()

META_APP_SECRET = "tenant-binding-meta-secret"
BARRIER_TIMEOUT_SECONDS = 10.0
JOIN_TIMEOUT_SECONDS = 30.0


@pytest.fixture
def tenants(monkeypatch) -> dict:
    """Two tenants; only tenant A owns the inbound business number."""
    monkeypatch.setattr(settings, "whatsapp_app_secret", META_APP_SECRET)
    suffix = uuid4().hex[:10]

    company_a = repository.create_company(slug=f"tenant-a-{suffix}", name="Tenant A")
    company_b = repository.create_company(slug=f"tenant-b-{suffix}", name="Tenant B")
    phone_a = f"phone-a-{suffix}"
    phone_b = f"phone-b-{suffix}"

    repository.upsert_whatsapp_binding(
        company_id=company_a,
        phone_number_id=phone_a,
        display_phone_number="5511900000001",
        updated_by="fixture",
    )
    repository.upsert_whatsapp_binding(
        company_id=company_b,
        phone_number_id=phone_b,
        display_phone_number="5511900000002",
        updated_by="fixture",
    )
    return {
        "company_a": company_a,
        "company_b": company_b,
        "slug_a": f"tenant-a-{suffix}",
        "slug_b": f"tenant-b-{suffix}",
        "phone_a": phone_a,
        "phone_b": phone_b,
        "suffix": suffix,
    }


@pytest.fixture
def captured_processing(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    def _capture(**kwargs):
        captured.append(kwargs)
        return {
            "contact_id": kwargs["from_number"],
            "company_id": kwargs.get("company_id"),
            "company_slug": kwargs.get("company_slug"),
            "classification": "faq",
            "classification_reason": "stub",
            "classification_confidence": 0.5,
            "reply": "",
            "source": "none",
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

    monkeypatch.setattr(routes, "process_whatsapp_webhook", _capture)
    return captured


def _meta_body(phone_number_id: str, message_id: str | None = None) -> bytes:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {
                                "display_phone_number": "5511999990000",
                                "phone_number_id": phone_number_id,
                            },
                            "contacts": [
                                {"wa_id": "5511977776666", "profile": {"name": "Cliente"}}
                            ],
                            "messages": [
                                {
                                    "id": message_id or f"wamid.{uuid4().hex[:12]}",
                                    "from": "5511977776666",
                                    "type": "text",
                                    "text": {"body": "Olá"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    return json.dumps(payload).encode("utf-8")


def _signed_headers(raw_body: bytes) -> dict[str, str]:
    signature = hmac.new(META_APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={signature}",
    }


# ── Inbound tenant resolution ─────────────────────────────────────────


def test_signed_event_is_persisted_under_the_bound_tenant(tenants, captured_processing) -> None:
    raw_body = _meta_body(tenants["phone_a"])

    response = client.post("/webhooks/whatsapp", content=raw_body, headers=_signed_headers(raw_body))

    assert response.status_code == 200
    assert captured_processing[0]["company_id"] == tenants["company_a"]
    assert captured_processing[0]["company_slug"] == tenants["slug_a"]


@pytest.mark.parametrize(
    "redirect",
    [
        {"params": {"company_id": "OTHER_ID"}},
        {"params": {"company_slug": "OTHER_SLUG"}},
        {"headers": {"X-Company-Id": "OTHER_ID"}},
        {"headers": {"X-Company-Slug": "OTHER_SLUG"}},
    ],
    ids=["query_company_id", "query_company_slug", "header_company_id", "header_company_slug"],
)
def test_caller_cannot_redirect_a_signed_event_to_another_tenant(
    tenants, captured_processing, redirect
) -> None:
    """Query/header selectors are ignored: the binding decides the tenant."""
    raw_body = _meta_body(tenants["phone_a"])
    headers = dict(_signed_headers(raw_body))
    params = {}

    for key, value in redirect.get("params", {}).items():
        params[key] = str(tenants["company_b"]) if value == "OTHER_ID" else tenants["slug_b"]
    for key, value in redirect.get("headers", {}).items():
        headers[key] = str(tenants["company_b"]) if value == "OTHER_ID" else tenants["slug_b"]

    response = client.post("/webhooks/whatsapp", content=raw_body, headers=headers, params=params)

    assert response.status_code == 200
    assert captured_processing[0]["company_id"] == tenants["company_a"]
    assert captured_processing[0]["company_slug"] == tenants["slug_a"]


def test_unknown_phone_number_id_fails_closed(tenants, captured_processing) -> None:
    raw_body = _meta_body(f"unbound-{uuid4().hex[:8]}")

    response = client.post("/webhooks/whatsapp", content=raw_body, headers=_signed_headers(raw_body))

    assert response.status_code == 403
    assert captured_processing == []


def test_missing_phone_number_id_fails_closed(tenants, captured_processing) -> None:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "5511977776666", "profile": {"name": "C"}}],
                            "messages": [
                                {
                                    "id": f"wamid.{uuid4().hex[:12]}",
                                    "from": "5511977776666",
                                    "type": "text",
                                    "text": {"body": "Olá"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    raw_body = json.dumps(payload).encode("utf-8")

    response = client.post("/webhooks/whatsapp", content=raw_body, headers=_signed_headers(raw_body))

    assert response.status_code == 403
    assert captured_processing == []


def test_inactive_binding_fails_closed(tenants, captured_processing) -> None:
    binding = repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])
    assert repository.set_whatsapp_binding_active(int(binding["id"]), active=False) == "updated"

    raw_body = _meta_body(tenants["phone_a"])
    response = client.post("/webhooks/whatsapp", content=raw_body, headers=_signed_headers(raw_body))

    assert response.status_code == 403
    assert captured_processing == []


def test_inactive_company_fails_closed(tenants, captured_processing) -> None:
    repository.deactivate_companies([tenants["company_a"]])

    raw_body = _meta_body(tenants["phone_a"])
    response = client.post("/webhooks/whatsapp", content=raw_body, headers=_signed_headers(raw_body))

    assert response.status_code == 403
    assert captured_processing == []


def test_default_company_is_never_used_as_a_meta_fallback(tenants, captured_processing) -> None:
    repository.ensure_default_company()
    raw_body = _meta_body(f"no-binding-{uuid4().hex[:8]}")

    response = client.post("/webhooks/whatsapp", content=raw_body, headers=_signed_headers(raw_body))

    assert response.status_code == 403
    assert captured_processing == []


def test_idempotency_receipt_is_tenant_scoped(tenants, captured_processing, monkeypatch) -> None:
    receipts: list[dict] = []

    class _ReceiptRepository(RuntimeRepository):
        def begin_inbound_message_processing(self, **kwargs):
            receipts.append(kwargs)
            return "started"

        def mark_inbound_message_processing_completed(self, **kwargs):
            receipts.append({"completed": kwargs})

    monkeypatch.setattr(routes, "repository", _ReceiptRepository())

    raw_body = _meta_body(tenants["phone_a"], message_id="wamid.tenant.scope.1")
    response = client.post("/webhooks/whatsapp", content=raw_body, headers=_signed_headers(raw_body))

    assert response.status_code == 200
    assert receipts[0]["company_id"] == tenants["company_a"]
    assert receipts[-1]["completed"]["company_id"] == tenants["company_a"]


def test_no_cross_tenant_realtime_event_is_emitted(tenants, captured_processing, monkeypatch) -> None:
    published: list[tuple] = []
    monkeypatch.setattr(
        "app.services.realtime_service.broadcast_event",
        lambda event_type, data=None, company_id=None: published.append((event_type, company_id)),
    )

    raw_body = _meta_body(tenants["phone_a"])
    client.post("/webhooks/whatsapp", content=raw_body, headers=_signed_headers(raw_body))

    assert all(company_id != tenants["company_b"] for _event, company_id in published)


def test_normalized_ingress_still_requires_its_secret(tenants) -> None:
    """Binding changes must not weaken the normalized ingress gate."""
    response = client.post(
        "/webhooks/whatsapp",
        json={"event": "message.received", "from_number": "5511977776666", "text": "oi"},
    )
    assert response.status_code == 403


def test_normalized_ingress_may_still_select_a_tenant(tenants, captured_processing, monkeypatch) -> None:
    """Documented policy: the internal-secret holder is trusted to pick a tenant."""
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", "tenant-binding-internal-secret")

    response = client.post(
        "/webhooks/whatsapp",
        params={"company_slug": tenants["slug_b"]},
        json={"event": "message.received", "from_number": "5511977776666", "text": "oi"},
        headers={"X-Internal-Ingress-Secret": "tenant-binding-internal-secret"},
    )

    assert response.status_code == 200
    assert captured_processing[0]["company_id"] == tenants["company_b"]


# ── Binding management ────────────────────────────────────────────────


def test_duplicate_active_binding_is_prevented(tenants) -> None:
    result = repository.upsert_whatsapp_binding(
        company_id=tenants["company_b"],
        phone_number_id=tenants["phone_a"],
        updated_by="test",
    )

    assert result is None
    binding = repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])
    assert binding["company_id"] == tenants["company_a"]


def test_binding_can_move_after_deactivation(tenants) -> None:
    binding = repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])
    repository.set_whatsapp_binding_active(int(binding["id"]), active=False)

    moved = repository.upsert_whatsapp_binding(
        company_id=tenants["company_b"],
        phone_number_id=tenants["phone_a"],
        updated_by="test",
    )

    assert moved is not None
    assert repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])["company_id"] == tenants["company_b"]


def test_admin_binding_endpoint_requires_superadmin(tenants, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", "wb-viewer")
    monkeypatch.setattr(settings, "admin_operator_token", "wb-operator")
    monkeypatch.setattr(settings, "admin_superadmin_token", "wb-superadmin")

    body = {"company_id": tenants["company_a"], "phone_number_id": f"admin-{uuid4().hex[:8]}"}

    for token, expected in [("wb-viewer", 403), ("wb-operator", 403)]:
        response = client.put(
            "/admin/whatsapp/bindings",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == expected

    response = client.put(
        "/admin/whatsapp/bindings",
        json=body,
        headers={"Authorization": "Bearer wb-superadmin"},
    )
    assert response.status_code == 200


def test_admin_binding_conflict_returns_409(tenants, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", False)

    response = client.put(
        "/admin/whatsapp/bindings",
        json={"company_id": tenants["company_b"], "phone_number_id": tenants["phone_a"]},
    )

    assert response.status_code == 409


def test_binding_response_never_exposes_secrets(tenants, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", False)

    response = client.get("/admin/whatsapp/bindings", params={"company_id": tenants["company_a"]})

    assert response.status_code == 200
    body = response.text.lower()
    for forbidden in ["token", "secret", "app_secret", "authorization"]:
        assert forbidden not in body


def test_binding_mutation_is_audited(tenants, monkeypatch) -> None:
    audit_events: list[dict] = []

    class _AuditRepository(RuntimeRepository):
        def create_audit_event(self, **kwargs) -> int:
            audit_events.append(kwargs)
            return 1

    monkeypatch.setattr(settings, "enable_admin_auth", False)
    monkeypatch.setattr(admin_routes, "repository", _AuditRepository())

    phone_number_id = f"audited-{uuid4().hex[:8]}"
    response = client.put(
        "/admin/whatsapp/bindings",
        json={"company_id": tenants["company_a"], "phone_number_id": phone_number_id},
    )

    assert response.status_code == 200
    binding_events = [e for e in audit_events if e.get("resource_type") == "whatsapp_channel_binding"]
    assert binding_events
    assert binding_events[-1]["action"] == "upsert"
    metadata = binding_events[-1]["metadata"]
    assert metadata["phone_number_id"] == phone_number_id
    assert "token" not in json.dumps(metadata).lower()


# ── Outbound tenant scoping ───────────────────────────────────────────


def test_outbound_uses_the_tenant_bound_phone_number_id(tenants, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.example.com/v22.0")
    monkeypatch.setattr(settings, "whatsapp_api_token", "outbound-token")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", tenants["phone_b"])

    provider_a = WhatsAppProvider(company_id=tenants["company_a"])
    provider_b = WhatsAppProvider(company_id=tenants["company_b"])

    assert provider_a.phone_number_id == tenants["phone_a"]
    assert provider_b.phone_number_id == tenants["phone_b"]
    assert provider_a._build_url().endswith(f"/{tenants['phone_a']}/messages")


def test_outbound_fails_safely_without_a_binding(tenants, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.example.com/v22.0")
    monkeypatch.setattr(settings, "whatsapp_api_token", "outbound-token")
    unbound_company = repository.create_company(
        slug=f"unbound-{tenants['suffix']}",
        name="Unbound Co",
    )

    provider = WhatsAppProvider(company_id=unbound_company)
    with pytest.raises(WhatsAppProviderError) as exc_info:
        provider.send_message(contact_id="5511977776666", message="oi")

    detail = str(exc_info.value)
    assert "no single active WhatsApp channel binding" in detail
    assert "outbound-token" not in detail


def test_dispatch_scopes_the_provider_to_the_message_company(tenants, monkeypatch) -> None:
    used_company_ids: list[int | None] = []

    class _RecordingProvider:
        def __init__(self, *, company_id: int | None = None) -> None:
            used_company_ids.append(company_id)

        def send_message(self, contact_id: str, message: str) -> dict:
            return {
                "channel": "whatsapp",
                "contact_id": contact_id,
                "message": message,
                "provider_message_id": "wamid.out.1",
                "provider_response": {},
            }

    class _OutboundRepository:
        def get_outbound_message(self, outbound_message_id, *, company_id=None):
            return {
                "id": outbound_message_id,
                "contact_id": "5511977776666",
                "company_id": tenants["company_b"],
                "message": "resposta",
                "source": "test",
                "status": "pending",
            }

        def mark_outbound_message_sent(self, *args, **kwargs):
            return None

    monkeypatch.setattr(settings, "outbound_provider", "whatsapp")
    monkeypatch.setattr(outbound_dispatch_service, "WhatsAppProvider", _RecordingProvider)
    monkeypatch.setattr(outbound_dispatch_service, "repository", _OutboundRepository())

    outbound_dispatch_service.dispatch_outbound_message(outbound_message_id=1, channel="whatsapp")

    assert used_company_ids == [tenants["company_b"]]


# ── Binding conflict hardening (Release 1A.2) ─────────────────────────


def test_reactivating_a_historical_binding_conflicts(tenants) -> None:
    """Tenant A's number moved to Tenant B; A's old row must not be revivable."""
    binding_a = repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])
    assert repository.set_whatsapp_binding_active(int(binding_a["id"]), active=False) == "updated"
    moved = repository.upsert_whatsapp_binding(
        company_id=tenants["company_b"],
        phone_number_id=tenants["phone_a"],
        updated_by="test",
    )
    assert moved is not None

    # The database, not a preflight SELECT, rejects the reactivation.
    result = repository.set_whatsapp_binding_active(int(binding_a["id"]), active=True)

    assert result == "conflict"
    owner = repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])
    assert owner["company_id"] == tenants["company_b"]


def test_reactivation_conflict_leaves_no_partial_state(tenants) -> None:
    binding_a = repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])
    repository.set_whatsapp_binding_active(int(binding_a["id"]), active=False)
    repository.upsert_whatsapp_binding(
        company_id=tenants["company_b"],
        phone_number_id=tenants["phone_a"],
        updated_by="test",
    )

    before = repository.get_whatsapp_bindings()
    assert repository.set_whatsapp_binding_active(int(binding_a["id"]), active=True) == "conflict"
    after = repository.get_whatsapp_bindings()

    assert before == after, "a rejected activation must not change any row"
    active_for_phone = [
        b for b in after if b["phone_number_id"] == tenants["phone_a"] and b["active"] == 1
    ]
    assert len(active_for_phone) == 1


def test_upserting_a_number_active_on_another_company_conflicts(tenants) -> None:
    result = repository.upsert_whatsapp_binding(
        company_id=tenants["company_b"],
        phone_number_id=tenants["phone_a"],
        updated_by="test",
    )

    assert result is None
    assert repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])["company_id"] == tenants["company_a"]


def test_deactivation_never_conflicts(tenants) -> None:
    binding = repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])

    assert repository.set_whatsapp_binding_active(int(binding["id"]), active=False) == "updated"
    assert repository.set_whatsapp_binding_active(int(binding["id"]), active=False) == "updated"


def test_unknown_binding_reports_not_found(tenants) -> None:
    assert repository.set_whatsapp_binding_active(999_999_999, active=True) == "not_found"


def test_unrelated_integrity_errors_are_not_translated(tenants) -> None:
    """A different constraint must keep propagating, not become a conflict."""
    from app.storage.db import is_unique_violation
    from app.storage.repository import _is_active_phone_number_conflict

    class _OtherUniqueViolation(Exception):
        sqlstate = "23505"

        class diag:  # noqa: N801 - mimics psycopg's diagnostics object
            constraint_name = "idx_company_contexts_slug"

    class _ForeignKeyViolation(Exception):
        sqlstate = "23503"

        class diag:  # noqa: N801
            constraint_name = "idx_whatsapp_bindings_active_phone"

    assert _is_active_phone_number_conflict(_OtherUniqueViolation()) is False
    assert _is_active_phone_number_conflict(_ForeignKeyViolation()) is False
    assert is_unique_violation(
        sqlite3.IntegrityError("UNIQUE constraint failed: company_contexts.company_slug"),
        postgres_constraint="idx_whatsapp_bindings_active_phone",
        sqlite_qualified_column="whatsapp_channel_bindings.phone_number_id",
    ) is False


def test_binding_activation_conflict_returns_409(tenants, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", False)
    binding_a = repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])
    repository.set_whatsapp_binding_active(int(binding_a["id"]), active=False)
    repository.upsert_whatsapp_binding(
        company_id=tenants["company_b"],
        phone_number_id=tenants["phone_a"],
        updated_by="test",
    )

    response = client.post(
        f"/admin/whatsapp/bindings/{binding_a['id']}/state",
        json={"active": True},
    )

    assert response.status_code == 409
    assert "another company" in response.json()["detail"]


def test_binding_state_endpoint_returns_404_for_unknown_binding(tenants, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", False)

    response = client.post("/admin/whatsapp/bindings/999999999/state", json={"active": False})

    assert response.status_code == 404


def test_binding_state_endpoint_success_contract(tenants, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", False)
    binding = repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])

    response = client.post(
        f"/admin/whatsapp/bindings/{binding['id']}/state",
        json={"active": False},
    )

    assert response.status_code == 200
    assert response.json() == {"binding_id": int(binding["id"]), "active": False}


def test_conflicting_activation_is_not_audited_as_success(tenants, monkeypatch) -> None:
    audit_events: list[dict] = []

    class _AuditRepository(RuntimeRepository):
        def create_audit_event(self, **kwargs) -> int:
            audit_events.append(kwargs)
            return 1

    binding_a = repository.get_whatsapp_binding_by_phone_number_id(tenants["phone_a"])
    repository.set_whatsapp_binding_active(int(binding_a["id"]), active=False)
    repository.upsert_whatsapp_binding(
        company_id=tenants["company_b"],
        phone_number_id=tenants["phone_a"],
        updated_by="test",
    )

    monkeypatch.setattr(settings, "enable_admin_auth", False)
    monkeypatch.setattr(admin_routes, "repository", _AuditRepository())

    response = client.post(
        f"/admin/whatsapp/bindings/{binding_a['id']}/state",
        json={"active": True},
    )

    assert response.status_code == 409
    binding_events = [e for e in audit_events if e.get("resource_type") == "whatsapp_channel_binding"]
    assert binding_events
    assert binding_events[-1]["status"] == "conflict"
    assert all(e.get("status") != "success" for e in binding_events)


def test_concurrent_upserts_produce_exactly_one_active_owner(tenants) -> None:
    """Competing writers race on the same number; the database picks one winner."""
    phone = f"race-phone-{uuid4().hex[:10]}"
    companies = [
        repository.create_company(slug=f"race-co-{uuid4().hex[:8]}", name=f"Race {index}")
        for index in range(6)
    ]

    barrier = threading.Barrier(len(companies), timeout=BARRIER_TIMEOUT_SECONDS)
    results: list = [None] * len(companies)

    def worker(index: int) -> None:
        try:
            barrier.wait()
            results[index] = repository.upsert_whatsapp_binding(
                company_id=companies[index],
                phone_number_id=phone,
                updated_by=f"racer-{index}",
            )
        except Exception as exc:  # noqa: BLE001 - an escaping exception is the failure
            results[index] = exc

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(len(companies))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT_SECONDS)
        assert not thread.is_alive(), "concurrent binding writer did not finish in time"

    raised = [r for r in results if isinstance(r, Exception)]
    assert raised == [], f"no integrity exception may escape: {raised}"

    winners = [r for r in results if isinstance(r, dict)]
    assert len(winners) == 1, f"exactly one writer may take ownership, got {len(winners)}"

    active = [b for b in repository.get_whatsapp_bindings() if b["phone_number_id"] == phone and b["active"] == 1]
    assert len(active) == 1
    assert active[0]["company_id"] == winners[0]["company_id"]


def test_concurrent_activation_of_historical_bindings_has_one_winner(tenants) -> None:
    """Several deactivated rows for one number race to reactivate."""
    phone = f"reactivate-race-{uuid4().hex[:10]}"
    binding_ids: list[int] = []
    for index in range(4):
        company_id = repository.create_company(
            slug=f"react-co-{uuid4().hex[:8]}", name=f"React {index}"
        )
        binding = repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=phone)
        assert binding is not None
        binding_ids.append(int(binding["id"]))
        repository.set_whatsapp_binding_active(int(binding["id"]), active=False)

    barrier = threading.Barrier(len(binding_ids), timeout=BARRIER_TIMEOUT_SECONDS)
    outcomes: list = [None] * len(binding_ids)

    def worker(index: int) -> None:
        try:
            barrier.wait()
            outcomes[index] = repository.set_whatsapp_binding_active(binding_ids[index], active=True)
        except Exception as exc:  # noqa: BLE001
            outcomes[index] = exc

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(len(binding_ids))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT_SECONDS)
        assert not thread.is_alive()

    assert [o for o in outcomes if isinstance(o, Exception)] == []
    assert outcomes.count("updated") == 1, f"exactly one reactivation may win: {outcomes}"
    assert outcomes.count("conflict") == len(binding_ids) - 1

    active = [b for b in repository.get_whatsapp_bindings() if b["phone_number_id"] == phone and b["active"] == 1]
    assert len(active) == 1
