"""P06 - H01 a H03, F03 e F06: posse humana, fencing do job e resumo operacional."""

from __future__ import annotations

import copy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core import orchestrator
from app.core.company_context import get_seed_company_context
from app.core.config import settings
from app.main import app
from app.services import execution_lease
from app.services.company_config_service import save_company_config
from app.services.operations_summary_service import dead_letter_code, get_operations_summary
from app.storage import inbound_repository as queue
from app.storage import ownership_repository as ownership
from app.storage.repository import RuntimeRepository
from app.storage.sql_support import execute_rowcount, fetch_one

client = TestClient(app)
repository = RuntimeRepository()


@pytest.fixture(autouse=True)
def _session_auth(monkeypatch):
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "legacy-viewer")
    monkeypatch.setattr(settings, "admin_operator_token", "legacy-operator")
    monkeypatch.setattr(settings, "admin_superadmin_token", "legacy-superadmin")
    monkeypatch.setattr(settings, "admin_api_token", None)


def _headers(principal: dict) -> dict[str, str]:
    return {
        "Cookie": f"project_wapp_platform_session={principal['token']}; project_wapp_csrf={principal['csrf']}",
        "X-CSRF-Token": principal["csrf"],
    }


def _contact() -> str:
    return f"5511{uuid4().int % 10**9:09d}"


@pytest.fixture
def tenant(company_factory, admin_principal_factory) -> dict:
    company_id = company_factory("ownership-api")
    return {
        "company_id": company_id,
        "viewer": admin_principal_factory(global_role="viewer", company_ids=[company_id]),
        "operator": admin_principal_factory(global_role="operator", company_ids=[company_id]),
        "operator2": admin_principal_factory(global_role="operator", company_ids=[company_id]),
        "admin": admin_principal_factory(global_role="superadmin", company_ids=[company_id]),
    }


@pytest.fixture
def empty_queue():
    execute_rowcount("DELETE FROM inbound_jobs")
    yield
    execute_rowcount("DELETE FROM inbound_jobs")


def _post(path: str, principal: dict, company_id: int, json: dict | None = None):
    return client.post(path, params={"company_id": company_id}, json=json, headers=_headers(principal))


# ── H01-H03: rotas de posse ──────────────────────────────────────────


def test_operator_takes_lists_and_releases_a_conversation(tenant) -> None:
    company_id = tenant["company_id"]
    contact_id = _contact()

    taken = _post(f"/admin/conversations/{contact_id}/take", tenant["operator"], company_id, {"reason": "cliente pediu"})
    assert taken.status_code == 200, taken.text
    assert taken.json()["outcome"] == "taken"

    listed = client.get(
        "/admin/conversations/ownership", params={"company_id": company_id}, headers=_headers(tenant["viewer"])
    )
    assert listed.status_code == 200
    assert [row["contact_id"] for row in listed.json()["conversations"]] == [contact_id]

    assert _post(f"/admin/conversations/{contact_id}/take", tenant["operator2"], company_id).status_code == 409
    assert _post(f"/admin/conversations/{contact_id}/release", tenant["operator2"], company_id).status_code == 409
    assert _post(
        f"/admin/conversations/{contact_id}/release", tenant["operator2"], company_id, {"force": True}
    ).status_code == 403

    forced = _post(f"/admin/conversations/{contact_id}/release", tenant["admin"], company_id, {"force": True})
    assert forced.status_code == 200, forced.text
    assert forced.json()["outcome"] == "released"
    assert ownership.is_human_owned(company_id=company_id, contact_id=contact_id) is False


def test_viewer_and_operator_of_another_company_cannot_take(tenant, company_factory, admin_principal_factory) -> None:
    company_id = tenant["company_id"]
    contact_id = _contact()
    other_company = company_factory("ownership-other")
    outsider = admin_principal_factory(global_role="operator", company_ids=[other_company])

    assert _post(f"/admin/conversations/{contact_id}/take", tenant["viewer"], company_id).status_code == 403
    assert _post(f"/admin/conversations/{contact_id}/take", outsider, company_id).status_code in {403, 404}
    assert ownership.is_human_owned(company_id=company_id, contact_id=contact_id) is False


# ── H01/H02: bot suspenso durante a posse ────────────────────────────


def _open_human_questions(company_id: int, contact_id: str) -> int:
    return len([
        question
        for question in repository.get_pending_questions(company_id=company_id)
        if question["contact_id"] == contact_id
        and question["question_type"] == "human_conversation_message"
        and question["status"] == "pending"
    ])


def test_bot_stays_silent_while_a_human_owns_and_resumes_after_release(tenant, monkeypatch) -> None:
    company_id = tenant["company_id"]
    save_company_config(company_id=company_id, context=copy.deepcopy(get_seed_company_context(None)), author="test")
    contact_id = _contact()
    ownership.take_ownership(company_id=company_id, contact_id=contact_id, user_id=int(tenant["operator"]["user_id"]))

    def _fail(**kwargs):
        raise AssertionError("bot não pode conduzir conversa com humano")

    monkeypatch.setattr(orchestrator, "handle_conversation_workflow", _fail)
    monkeypatch.setattr(orchestrator, "classify_message", lambda message: _fail())
    monkeypatch.setattr(orchestrator, "update_conversation_summary", lambda **kwargs: "resumo")
    monkeypatch.setattr(
        orchestrator,
        "infer_pipeline_state",
        lambda **kwargs: {"pipeline_stage": "novo_lead", "next_action": "-", "lead_temperature": "warm"},
    )
    monkeypatch.setattr(orchestrator, "is_within_business_hours", lambda context, now: True)

    first = orchestrator.process_whatsapp_webhook(contact_id, None, "Oi, tudo bem?", company_id=company_id, message_ref="whatsapp:h1")
    second = orchestrator.process_whatsapp_webhook(contact_id, None, "alguém aí?", company_id=company_id, message_ref="whatsapp:h2")

    assert (first["action"], first["reply"], first["auto_reply"]) == ("human_owned", "", False)
    assert second["action"] == "human_owned"
    assert _open_human_questions(company_id, contact_id) == 1

    ownership.release_ownership(company_id=company_id, contact_id=contact_id, user_id=int(tenant["operator"]["user_id"]))
    monkeypatch.setattr(
        orchestrator, "handle_conversation_workflow", lambda **kwargs: type("W", (), {"handled": False})()
    )
    resumed = orchestrator.process_whatsapp_webhook(contact_id, None, "Oi", company_id=company_id, message_ref="whatsapp:h3")
    assert resumed["action"] == "greeting"
    assert resumed["auto_reply"] is True


# ── F03: fencing do job de entrada ───────────────────────────────────


def test_lease_is_renewed_by_its_owner_and_refused_after_loss(company_factory, empty_queue) -> None:
    company_id = company_factory("lease")
    queue.enqueue_batch([{"company_id": company_id, "from_number": _contact(), "text": "oi",
                          "provider_message_id": uuid4().hex}])
    job = queue.claim_next()
    assert job is not None and job["company_id"] == company_id
    before = job["lease_expires_at"]

    with execution_lease.bind_job_lease(job):
        execution_lease.assert_lease_before_effect()
    renewed = fetch_one("SELECT lease_expires_at FROM inbound_jobs WHERE id = ?", (job["id"],))
    assert renewed["lease_expires_at"] >= before

    execute_rowcount("UPDATE inbound_jobs SET claim_token = ? WHERE id = ?", ("new-owner-token", job["id"]))
    with execution_lease.bind_job_lease(job), pytest.raises(execution_lease.LeaseLostError):
        execution_lease.assert_lease_before_effect()

    execution_lease.assert_lease_before_effect()  # fora do worker: sem lease a proteger


# ── F06: resumo operacional ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("last_error", "code"),
    [
        ("transient:130429:Rate limit hit", "transient:130429"),
        ("permanent:-:blocked:opted_out", "blocked:opted_out"),
        ("sem formato", "unclassified"),
    ],
)
def test_dead_letter_codes_are_aggregable(last_error: str, code: str) -> None:
    assert dead_letter_code(last_error) == code


def test_operations_summary_shows_what_needs_attention(tenant, company_factory, admin_principal_factory, empty_queue) -> None:
    company_id = tenant["company_id"]
    contact_id = _contact()
    queue.enqueue_batch([{"company_id": company_id, "from_number": contact_id, "text": "oi",
                          "provider_message_id": uuid4().hex}])
    execute_rowcount("UPDATE inbound_jobs SET status = 'review' WHERE company_id = ?", (company_id,))
    repository.save_contact(contact_id, "Cliente", company_id=company_id)
    outbound_id = repository.create_outbound_message(contact_id=contact_id, company_id=company_id,
                                                     message="Lembrete", source="reminder_customer")
    execute_rowcount(
        "UPDATE outbound_messages SET status = 'dead_letter', last_error = ? WHERE id = ?",
        ("permanent:-:blocked:outside_window_no_template", outbound_id),
    )
    ownership.take_ownership(company_id=company_id, contact_id=contact_id, user_id=int(tenant["operator"]["user_id"]))

    summary = get_operations_summary(company_id)

    assert summary["inbound"]["by_status"]["review"] == 1
    assert summary["outbound"]["dead_letters_by_code"] == {"blocked:outside_window_no_template": 1}
    assert summary["human_owned_conversations"] == 1
    assert {"inbound_review_open", "outbound_dead_letters"} <= {alert["code"] for alert in summary["alerts"]}

    response = client.get("/admin/operations/summary", params={"company_id": company_id}, headers=_headers(tenant["viewer"]))
    assert response.status_code == 200
    assert response.json()["human_owned_conversations"] == 1

    other_company = company_factory("summary-other")
    outsider = admin_principal_factory(global_role="viewer", company_ids=[other_company])
    denied = client.get("/admin/operations/summary", params={"company_id": company_id}, headers=_headers(outsider))
    assert denied.status_code in {403, 404}


# ── P07: lista de revisão com contexto mínimo ────────────────────────


def test_review_listing_filters_by_status_without_message_content(tenant, empty_queue) -> None:
    company_id = tenant["company_id"]
    reviewed, pending = _contact(), _contact()
    review_ref = uuid4().hex
    queue.enqueue_batch([
        {"company_id": company_id, "from_number": reviewed, "text": "oi", "provider_message_id": review_ref},
        {"company_id": company_id, "from_number": pending, "text": "olá", "provider_message_id": uuid4().hex},
    ])
    execute_rowcount(
        "UPDATE inbound_jobs SET status = 'review', last_error = 'dispatch_retry_exhausted' "
        "WHERE company_id = ? AND provider_message_id = ?",
        (company_id, review_ref),
    )

    response = client.get(
        "/admin/inbound/jobs", params={"company_id": company_id, "status": "review"}, headers=_headers(tenant["viewer"])
    )

    assert response.status_code == 200, response.text
    jobs = response.json()["jobs"]
    assert [(job["contact_id"], job["last_error"]) for job in jobs] == [(reviewed, "dispatch_retry_exhausted")]
    assert "payload_json" not in jobs[0] and "result_json" not in jobs[0]
    invalid = client.get(
        "/admin/inbound/jobs", params={"company_id": company_id, "status": "bogus"}, headers=_headers(tenant["viewer"])
    )
    assert invalid.status_code == 422
