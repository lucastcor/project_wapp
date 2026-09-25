import hashlib
import hmac
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api import admin_security, routes
from app.core.config import settings
from app.main import app
from app.services import inbound_service
from app.storage import inbound_repository as queue
from app.storage.repository import RuntimeRepository
from app.storage.sql_support import execute_rowcount, fetch_one

client = TestClient(app)


def item(company_id, *, sender="synthetic-sender", message_id=None):
    return {"company_id": company_id, "from_number": sender, "from_name": "Synthetic",
            "text": "Olá", "provider_message_id": message_id or uuid4().hex}


@pytest.fixture
def empty_queue():
    execute_rowcount("DELETE FROM inbound_jobs")
    yield
    execute_rowcount("DELETE FROM inbound_jobs")


def test_queue_timestamps_have_fixed_width_for_textual_comparison():
    import re
    from datetime import UTC, datetime

    pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00")
    assert pattern.fullmatch(queue.now_iso())
    # Fração zero não pode encurtar o texto: "…:00+00:00" ordenaria errado.
    assert pattern.fullmatch(queue._iso(datetime(2026, 9, 13, 12, 0, tzinfo=UTC)))


def test_all_messages_and_contact_names_across_entries_are_extracted():
    value = {"metadata": {"phone_number_id": "phone-a"},
             "contacts": [{"wa_id": "a", "profile": {"name": "Alice"}}, {"wa_id": "b", "profile": {"name": "Bruno"}}],
             "messages": [{"id": "1", "from": "b", "type": "text", "text": {"body": "oi"}},
                          {"id": "2", "from": "a", "type": "text", "text": {"body": "olá"}}]}
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": value}]}, {"changes": [{"value": value}]}]}
    result = routes._extract_meta_whatsapp_messages(payload)
    assert [r["from_name"] for r in result] == ["Bruno", "Alice", "Bruno", "Alice"]
    assert len(result) == 4


def test_webhook_commits_whole_batch_without_invoking_model(monkeypatch, company_factory, empty_queue):
    cid = company_factory()
    secret = "synthetic-app-secret"
    monkeypatch.setattr(settings, "whatsapp_app_secret", secret)
    monkeypatch.setattr(routes, "_resolve_meta_company", lambda _: {"company_id": cid, "company_slug": None})
    monkeypatch.setattr(inbound_service, "process_whatsapp_webhook", lambda **_: pytest.fail("HTTP must not run the model"))
    value = {"metadata": {"phone_number_id": "bound"}, "messages": [
        {"id": f"batch-{i}", "from": "customer", "type": "text", "text": {"body": "olá"}} for i in range(2)]}
    raw = json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"value": value}]}]}).encode()
    headers = {"X-Hub-Signature-256": "sha256="+hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}
    response = client.post("/webhooks/whatsapp", content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json()["queued_count"] == 2
    assert len(queue.list_jobs(cid)) == 2
    assert client.post("/webhooks/whatsapp", content=raw, headers=headers).json()["queued_count"] == 0


def test_batch_is_atomic_and_dedup_is_per_company(company_factory, empty_queue):
    a, b = company_factory(), company_factory()
    with pytest.raises((sqlite3.IntegrityError, psycopg.IntegrityError)):
        queue.enqueue_batch([item(a, message_id="good"), item(99999999)])
    assert queue.list_jobs(a) == []
    results = queue.enqueue_batch([item(a, message_id="same"), item(a, message_id="same"), item(b, message_id="same")])
    assert [r["duplicate"] for r in results] == [False, True, False]


def test_claim_serializes_each_conversation_without_blocking_other_tenants(company_factory, empty_queue):
    a, b = company_factory(), company_factory()
    queue.enqueue_batch([item(a), item(a), item(b)])
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: queue.claim_next(), range(2)))
    claims = [c for c in claims if c]
    if len(claims) == 1:
        claims.append(queue.claim_next())
    assert {c["company_id"] for c in claims} == {a, b}
    assert queue.claim_next() is None


def test_crashed_attempt_is_reviewed_not_replayed_and_blocks_only_its_conversation(company_factory, empty_queue):
    a, b = company_factory(), company_factory()
    queue.enqueue_batch([item(a), item(a), item(b)])
    first = queue.claim_next()
    execute_rowcount("UPDATE inbound_jobs SET lease_expires_at = '2000-01-01' WHERE id = ?", (first["id"],))
    assert queue.recover_abandoned() == 1
    assert queue.list_jobs(a)[-1]["status"] == "review"
    assert queue.claim_next()["company_id"] == b
    with pytest.raises(RuntimeError):
        queue.save_result(first, {"auto_reply": False})


def test_result_and_outbound_checkpoint_survive_new_worker(monkeypatch, company_factory, empty_queue):
    cid = company_factory()
    RuntimeRepository().save_contact("synthetic-sender", "Synthetic", company_id=cid)
    queue.enqueue_batch([item(cid)])
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return {"company_id": cid, "contact_id": "synthetic-sender", "auto_reply": True, "reply": "Olá!"}
    monkeypatch.setattr(inbound_service, "process_whatsapp_webhook", generate)
    monkeypatch.setattr(inbound_service, "publish_incoming_message_event", lambda *_a, **_kw: None)
    inbound_service.run_inbound_iteration()
    stored = queue.list_jobs(cid)[0]
    assert stored["status"] == "ready"
    outbound_id = stored["outbound_message_id"]
    monkeypatch.setattr(inbound_service, "process_whatsapp_webhook", lambda **_: pytest.fail("Must resume delivery checkpoint"))
    deliveries = []
    def send(message_id, **kwargs):
        deliveries.append(message_id)
        return {"delivery_status": "sent"}
    monkeypatch.setattr(inbound_service, "dispatch_outbound_message", send)
    inbound_service.run_inbound_iteration()
    inbound_service.run_inbound_iteration()
    assert deliveries == [outbound_id]
    assert len(calls) == 1
    assert queue.list_jobs(cid)[0]["status"] == "completed"
    assert fetch_one("SELECT COUNT(*) AS n FROM outbound_messages WHERE id = ?", (outbound_id,))["n"] == 1


def test_legacy_completed_receipt_is_not_replayed_after_upgrade(company_factory, empty_queue):
    cid = company_factory()
    repo = RuntimeRepository()
    message_id = f"old-receipt-{uuid4().hex}"
    assert repo.begin_inbound_message_processing(provider="whatsapp", provider_message_id=message_id, company_id=cid) == "started"
    repo.mark_inbound_message_processing_completed(provider="whatsapp", provider_message_id=message_id, company_id=cid)
    result = queue.enqueue_batch([item(cid, message_id=message_id)])
    assert result[0]["duplicate"] is True
    assert queue.list_jobs(cid) == []


def test_model_or_orchestrator_failure_becomes_visible_review(monkeypatch, company_factory, empty_queue):
    cid = company_factory()
    queue.enqueue_batch([item(cid)])
    def fail(**kwargs):
        raise RuntimeError("Synthetic error body must not appear in diagnostics")
    monkeypatch.setattr(inbound_service, "process_whatsapp_webhook", fail)
    inbound_service.run_inbound_iteration()
    row = queue.list_jobs(cid)[0]
    assert row["status"] == "review"
    assert row["last_error"] == "RuntimeError"
    assert row["outbound_message_id"] is None


def test_disabled_company_is_not_processed(monkeypatch, company_factory, empty_queue):
    cid = company_factory()
    queue.enqueue_batch([item(cid)])
    execute_rowcount("UPDATE companies SET active = 0 WHERE id = ?", (cid,))
    monkeypatch.setattr(inbound_service, "process_whatsapp_webhook", lambda **_: pytest.fail("Inactive company"))
    inbound_service.run_inbound_iteration()
    assert queue.list_jobs(cid)[0]["status"] == "review"


def test_delivery_failure_does_not_repeat_conversation(monkeypatch, company_factory, empty_queue):
    cid = company_factory()
    RuntimeRepository().save_contact("synthetic-sender", "Synthetic", company_id=cid)
    queue.enqueue_batch([item(cid)])
    job = queue.claim_next()
    queue.save_result(job, {"auto_reply": True, "reply": "Resposta"})
    monkeypatch.setattr(inbound_service, "process_whatsapp_webhook", lambda **_: pytest.fail("Must not regenerate"))
    def fail(*args, **kwargs):
        raise TimeoutError("Delivery uncertain")
    monkeypatch.setattr(inbound_service, "dispatch_outbound_message", fail)
    inbound_service.run_inbound_iteration()
    assert queue.list_jobs(cid)[0]["status"] == "review"
    assert queue.claim_next() is None


def test_review_resolution_requires_role_and_company_and_never_replays(monkeypatch, company_factory, admin_principal_factory, empty_queue):
    own, other = company_factory(), company_factory()
    job_id = queue.enqueue_batch([item(own)])[0]["job_id"]
    execute_rowcount("UPDATE inbound_jobs SET status = 'review' WHERE id = ?", (job_id,))
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_operator_token", "synthetic-review-operator")
    monkeypatch.setattr(settings, "admin_viewer_token", "synthetic-review-viewer")
    monkeypatch.setattr(inbound_service, "process_whatsapp_webhook", lambda **_: pytest.fail("Resolution must not replay"))
    url = f"/admin/inbound/jobs/{job_id}/resolve"
    viewer = {"Authorization": "Bearer synthetic-review-viewer"}
    principal = admin_principal_factory(global_role="operator", company_ids=[own])
    operator = {"X-CSRF-Token": principal["csrf"],
                "Cookie": f"{admin_security.COOKIE_NAME}={principal['token']}; {admin_security.CSRF_COOKIE_NAME}={principal['csrf']}"}
    assert client.post(url, params={"company_id": own}, headers=viewer).status_code == 403
    assert client.post(url, params={"company_id": other}, headers=operator).status_code == 403
    assert client.get("/admin/inbound/jobs", params={"company_id": other}, headers=operator).status_code == 403
    response = client.post(url, params={"company_id": own}, headers=operator)
    assert response.status_code == 200
    # Job sem resposta vinculada: nada a retirar de circulação.
    assert response.json() == {"resolved": True, "replayed": False, "outbound": "none"}
    assert queue.list_jobs(own)[0]["status"] == "completed"
    assert fetch_one("SELECT id FROM audit_events WHERE action = 'inbound.review.resolved' AND company_id = ?", (own,))
    assert client.post(url, params={"company_id": own}, headers=operator).status_code == 409
