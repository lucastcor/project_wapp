"""SaaS identity, CSRF and tenant-role regression coverage."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.storage.db import db_transaction
from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()


def _password(prefix: str = "Identity") -> str:
    return f"{prefix}!Secure2026"


def _register(client: TestClient, monkeypatch, *, suffix: str) -> tuple[dict, str]:
    monkeypatch.setattr(settings, "enable_self_service_signup", True)
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", None)
    monkeypatch.setattr(settings, "admin_operator_token", None)
    monkeypatch.setattr(settings, "admin_superadmin_token", None)
    response = client.post(
        "/platform/auth/register",
        json={
            "email": f"owner-{suffix}@example.com",
            "password": _password(),
            "display_name": "Tenant Owner",
            "company_name": f"Identity Company {suffix}",
            "company_slug": f"identity-{suffix}",
        },
    )
    assert response.status_code == 200, response.text
    csrf_token = client.cookies.get("project_wapp_csrf")
    assert csrf_token
    return response.json(), str(csrf_token)


def test_registration_issues_revocable_identity_session(monkeypatch) -> None:
    suffix = uuid4().hex[:10]
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=suffix)

        assert payload["auth_mode"] == "identity"
        assert payload["role"] == "superadmin"
        assert payload["memberships"][0]["role"] == "owner"

        session = client.get("/platform/auth/session")
        assert session.status_code == 200
        assert session.json()["email"] == f"owner-{suffix}@example.com"

        rejected_logout = client.delete("/platform/auth/session")
        assert rejected_logout.status_code == 403
        accepted_logout = client.delete(
            "/platform/auth/session",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert accepted_logout.status_code == 200
        assert client.get("/platform/auth/session").status_code == 401


def test_identity_session_only_lists_and_reads_member_companies(monkeypatch) -> None:
    suffix = uuid4().hex[:10]
    hidden_company_id = repository.create_company(
        slug=f"hidden-{suffix}",
        name="Hidden Company",
    )
    with TestClient(app) as client:
        payload, _csrf_token = _register(client, monkeypatch, suffix=suffix)
        owned_company_id = int(payload["memberships"][0]["company_id"])

        companies = client.get("/admin/companies")
        assert companies.status_code == 200
        assert [item["id"] for item in companies.json()] == [owned_company_id]

        own_company = client.get(f"/admin/companies/{owned_company_id}")
        assert own_company.status_code == 200
        hidden_company = client.get(f"/admin/companies/{hidden_company_id}")
        assert hidden_company.status_code == 403

        unscoped_messages = client.get("/admin/messages")
        assert unscoped_messages.status_code == 400
        scoped_messages = client.get(
            "/admin/messages",
            params={"company_id": owned_company_id},
        )
        assert scoped_messages.status_code == 200


def test_role_is_resolved_per_company_and_csrf_is_required(monkeypatch) -> None:
    suffix = uuid4().hex[:10]
    viewer_company_id = repository.create_company(
        slug=f"viewer-{suffix}",
        name="Viewer Company",
    )
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=suffix)
        user_id = int(payload["user_id"])
        owner_company_id = int(payload["memberships"][0]["company_id"])
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO company_memberships (company_id, user_id, role, active)
                VALUES (?, ?, 'viewer', 1)
                """,
                (viewer_company_id, user_id),
            )

        no_csrf = client.post(
            "/admin/billing/trial",
            params={"company_id": owner_company_id},
        )
        assert no_csrf.status_code == 403
        assert no_csrf.json()["detail"] == "CSRF validation failed."

        viewer_denied = client.post(
            "/admin/billing/trial",
            params={"company_id": viewer_company_id},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert viewer_denied.status_code == 403
        assert viewer_denied.json()["detail"] == "Insufficient admin privileges."


def test_invitation_acceptance_creates_membership_without_exposing_token_by_default(
    monkeypatch,
) -> None:
    suffix = uuid4().hex[:10]
    monkeypatch.setattr(settings, "identity_expose_action_tokens", True)
    with TestClient(app) as owner_client:
        payload, csrf_token = _register(owner_client, monkeypatch, suffix=suffix)
        company_id = int(payload["memberships"][0]["company_id"])
        invited_email = f"operator-{suffix}@example.com"

        invitation = owner_client.post(
            "/platform/identity/invitations",
            json={
                "company_id": company_id,
                "email": invited_email,
                "role": "operator",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        assert invitation.status_code == 200, invitation.text
        action_token = invitation.json()["action_token"]
        assert action_token

    with TestClient(app) as invited_client:
        accepted = invited_client.post(
            "/platform/auth/invitations/accept",
            json={
                "token": action_token,
                "password": _password("Operator"),
                "display_name": "Tenant Operator",
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["role"] == "operator"
        assert accepted.json()["memberships"][0]["company_id"] == company_id

        replay = invited_client.post(
            "/platform/auth/invitations/accept",
            json={
                "token": action_token,
                "password": _password("Operator"),
                "display_name": "Tenant Operator",
            },
        )
        assert replay.status_code == 400


def test_owner_can_list_and_update_members_but_cannot_remove_last_owner(
    monkeypatch,
) -> None:
    suffix = uuid4().hex[:10]
    monkeypatch.setattr(settings, "identity_expose_action_tokens", True)
    with TestClient(app) as owner_client:
        payload, csrf_token = _register(owner_client, monkeypatch, suffix=suffix)
        company_id = int(payload["memberships"][0]["company_id"])
        owner_user_id = int(payload["user_id"])

        invitation = owner_client.post(
            "/platform/identity/invitations",
            json={
                "company_id": company_id,
                "email": f"member-{suffix}@example.com",
                "role": "operator",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        assert invitation.status_code == 200, invitation.text

        with TestClient(app) as member_client:
            accepted = member_client.post(
                "/platform/auth/invitations/accept",
                json={
                    "token": invitation.json()["action_token"],
                    "password": _password("Member"),
                    "display_name": "Tenant Member",
                },
            )
            assert accepted.status_code == 200, accepted.text

        members = owner_client.get(
            "/platform/identity/members",
            params={"company_id": company_id},
        )
        assert members.status_code == 200, members.text
        assert len(members.json()) == 2
        owner_membership = next(
            item for item in members.json() if int(item["user_id"]) == owner_user_id
        )
        operator_membership = next(
            item for item in members.json() if int(item["user_id"]) != owner_user_id
        )

        updated = owner_client.patch(
            f"/platform/identity/members/{operator_membership['id']}",
            params={"company_id": company_id},
            json={"role": "viewer", "active": False},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["role"] == "viewer"
        assert updated.json()["active"] is False

        last_owner = owner_client.patch(
            f"/platform/identity/members/{owner_membership['id']}",
            params={"company_id": company_id},
            json={"active": False},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert last_owner.status_code == 409
        assert "at least one active owner" in last_owner.json()["detail"]
