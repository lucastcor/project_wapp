from __future__ import annotations

from uuid import uuid4

import pytest

from app.storage.inbound_repository import enqueue_batch
from app.tools import capacity_test


def test_percentile_uses_linear_interpolation() -> None:
    assert capacity_test.percentile([], 95) is None
    assert capacity_test.percentile([0.2], 95) == pytest.approx(0.2)
    assert capacity_test.percentile([0.1, 0.2, 0.3, 0.4], 50) == pytest.approx(0.25)
    assert capacity_test.latency_summary([0.1, 0.2])["p95_ms"] == pytest.approx(195)


def test_conversation_load_preserves_message_order_and_stable_contact(monkeypatch) -> None:
    observed: list[dict] = []

    def fake_send_one(_session, **kwargs):
        observed.append(kwargs)
        return capacity_test.AckResult(
            kwargs["provider_message_id"],
            True,
            0.01,
            200,
        )

    monkeypatch.setattr(capacity_test, "_send_one", fake_send_one)

    results = capacity_test._send_conversation(
        base_url="http://127.0.0.1:8010",
        company_id=7,
        ingress_secret="synthetic-secret",
        run_id="run-a",
        conversation_index=2,
        messages_per_conversation=5,
        scenario="mixed",
        request_timeout_seconds=2,
        think_time_seconds=0,
    )

    assert len(results) == 5
    assert [item["provider_message_id"] for item in observed] == [
        f"load-run-a-c002-m{index:03d}" for index in range(1, 6)
    ]
    assert {item["contact_id"] for item in observed} == {"load-run-a-c002"}
    assert [item["text"] for item in observed] == [
        "Oi, tudo bem?",
        "Quais serviços estão disponíveis?",
        "Como funciona o atendimento?",
        "Quais são os horários de atendimento?",
        "Oi, tudo bem?",
    ]


def test_fetch_jobs_reads_only_requested_company_and_ids(company_factory) -> None:
    company_id = company_factory("capacity-test")
    other_company_id = company_factory("capacity-test-other")
    message_id = f"capacity-{uuid4().hex}"
    other_message_id = f"capacity-{uuid4().hex}"
    enqueue_batch(
        [
            {
                "company_id": company_id,
                "provider_message_id": message_id,
                "from_number": "synthetic-contact-a",
                "text": "Oi",
            },
            {
                "company_id": other_company_id,
                "provider_message_id": other_message_id,
                "from_number": "synthetic-contact-b",
                "text": "Oi",
            },
        ]
    )

    jobs = capacity_test._fetch_jobs(company_id, [message_id, other_message_id])

    assert list(jobs) == [message_id]
    assert jobs[message_id]["status"] == "pending"
