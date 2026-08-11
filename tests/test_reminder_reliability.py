"""Reminder claims prevent duplicate outbounds across worker replicas."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.services.reminder_service import dispatch_due_reminders
from app.storage.db import db_transaction
from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()


def test_two_worker_replicas_enqueue_one_outbound_for_one_reminder() -> None:
    suffix = uuid4().hex[:12]
    company_id = repository.create_company(
        slug=f"reminder-race-{suffix}",
        name="Reminder Race",
    )
    contact_id = f"reminder-{suffix}"
    repository.save_contact(
        contact_id=contact_id,
        contact_name="Reminder Contact",
        company_id=company_id,
    )
    reminder_id = repository.create_customer_reminder(
        company_id=company_id,
        contact_id=contact_id,
        reminder_text="Lembrete confiável",
        reminder_summary="Lembrete confiável",
        remind_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        timezone="UTC",
        audience="customer",
        source="reliability_test",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        batches = list(
            executor.map(
                lambda _: dispatch_due_reminders(
                    audience="customer",
                    company_id=company_id,
                ),
                range(2),
            )
        )

    results = [item for batch in batches for item in batch]
    reminder = repository.get_customer_reminder(
        reminder_id,
        company_id=company_id,
    )
    outbounds = [
        item
        for item in repository.get_outbound_messages(company_id=company_id)
        if item["source"] == "reminder_customer"
    ]

    assert len(results) == 1
    assert reminder["status"] == "sent"
    assert reminder["attempt_count"] == 1
    assert len(outbounds) == 1
    assert int(reminder["outbound_message_id"]) == int(outbounds[0]["id"])


def test_lost_processing_claim_is_recovered_after_ttl() -> None:
    suffix = uuid4().hex[:12]
    company_id = repository.create_company(
        slug=f"reminder-recovery-{suffix}",
        name="Reminder Recovery",
    )
    contact_id = f"recovery-{suffix}"
    repository.save_contact(
        contact_id=contact_id,
        contact_name="Recovery Contact",
        company_id=company_id,
    )
    reminder_id = repository.create_customer_reminder(
        company_id=company_id,
        contact_id=contact_id,
        reminder_text="Recuperar claim",
        reminder_summary=None,
        remind_at=(datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
        timezone="UTC",
    )
    first = repository.claim_due_customer_reminder(
        datetime.now(UTC).isoformat(),
        claim_token=f"abandoned-{suffix}",
        company_id=company_id,
        claim_ttl_seconds=30,
    )
    assert first is not None

    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE customer_reminders
            SET claimed_at = ?
            WHERE id = ?
            """,
            (
                (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
                reminder_id,
            ),
        )

    recovered = repository.claim_due_customer_reminder(
        datetime.now(UTC).isoformat(),
        claim_token=f"recovered-{suffix}",
        company_id=company_id,
        claim_ttl_seconds=30,
    )

    assert recovered is not None
    assert recovered["claim_token"] == f"recovered-{suffix}"
    assert int(recovered["attempt_count"]) == 2
