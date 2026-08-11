"""Transactional billing enforcement for product operations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.config import settings
from app.services.billing_service import (
    PLANS,
    check_limit,
    consume_usage,
)
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


def _company_with_subscription(
    *,
    plan_key: str = "essencial",
    status: str = "active",
    grace_period_ends_at: str | None = None,
) -> tuple[int, dict]:
    suffix = uuid4().hex[:12]
    company_id = repository.create_company(
        slug=f"billing-enforcement-{suffix}",
        name=f"Billing Enforcement {suffix}",
    )
    now = datetime.now(UTC)
    repository.create_billing_subscription(
        company_id=company_id,
        plan_key=plan_key,
        status=status,
        billing_cycle="monthly",
        current_period_start=(now - timedelta(days=1)).isoformat(),
        current_period_end=(now + timedelta(days=29)).isoformat(),
        gateway="test",
    )
    subscription = repository.get_billing_subscription(company_id)
    assert subscription is not None
    if grace_period_ends_at is not None:
        repository.update_billing_subscription(
            int(subscription["id"]),
            company_id=company_id,
            grace_period_ends_at=grace_period_ends_at,
        )
        subscription = repository.get_billing_subscription(company_id)
        assert subscription is not None
    return company_id, subscription


def test_enforcement_rollout_allows_company_without_subscription(monkeypatch) -> None:
    company_id = repository.create_company(
        slug=f"billing-rollout-{uuid4().hex[:12]}",
        name="Billing Rollout",
    )
    monkeypatch.setattr(settings, "enable_billing_enforcement", False)

    decision = consume_usage(company_id=company_id, metric="bookings")

    assert decision == {
        "allowed": True,
        "recorded": False,
        "reason": "billing_enforcement_disabled",
    }


def test_enforcement_blocks_company_without_subscription(monkeypatch) -> None:
    company_id = repository.create_company(
        slug=f"billing-required-{uuid4().hex[:12]}",
        name="Billing Required",
    )
    monkeypatch.setattr(settings, "enable_billing_enforcement", True)

    decision = consume_usage(company_id=company_id, metric="bookings")

    assert decision["allowed"] is False
    assert decision["reason"] == "no_subscription"


def test_booking_limit_is_consumed_atomically_across_replicas(monkeypatch) -> None:
    company_id, subscription = _company_with_subscription()
    limit = int(PLANS["essencial"]["limits"]["bookings"])
    repository.record_billing_usage(
        company_id=company_id,
        subscription_id=int(subscription["id"]),
        metric="bookings",
        period_start=str(subscription["current_period_start"]),
        period_end=str(subscription["current_period_end"]),
        quantity=limit - 1,
    )
    monkeypatch.setattr(settings, "enable_billing_enforcement", True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        decisions = list(
            executor.map(
                lambda _: consume_usage(company_id=company_id, metric="bookings"),
                range(2),
            )
        )

    assert sum(bool(item["allowed"]) for item in decisions) == 1
    assert {item["reason"] for item in decisions} == {"recorded", "limit_exceeded"}
    assert check_limit(company_id, "bookings")["allowed"] is False


def test_past_due_subscription_has_bounded_grace_period(monkeypatch) -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    company_id, subscription = _company_with_subscription(
        status="past_due",
        grace_period_ends_at=future,
    )
    monkeypatch.setattr(settings, "enable_billing_enforcement", True)

    during_grace = consume_usage(company_id=company_id, metric="bookings")
    repository.update_billing_subscription(
        int(subscription["id"]),
        company_id=company_id,
        grace_period_ends_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    after_grace = consume_usage(company_id=company_id, metric="bookings")

    assert during_grace["allowed"] is True
    assert during_grace["reason"] == "recorded"
    assert after_grace["allowed"] is False
    assert after_grace["reason"] == "payment_failed"


def test_message_limit_is_soft_but_usage_is_still_recorded(monkeypatch) -> None:
    company_id, subscription = _company_with_subscription()
    limit = int(PLANS["essencial"]["limits"]["messages"])
    repository.record_billing_usage(
        company_id=company_id,
        subscription_id=int(subscription["id"]),
        metric="messages",
        period_start=str(subscription["current_period_start"]),
        period_end=str(subscription["current_period_end"]),
        quantity=limit,
    )
    monkeypatch.setattr(settings, "enable_billing_enforcement", True)

    decision = consume_usage(company_id=company_id, metric="messages")

    assert decision["allowed"] is True
    assert decision["recorded"] is True
    assert decision["hard_limit"] is False
    assert decision["used"] == limit + 1
