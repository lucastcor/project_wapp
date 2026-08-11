"""Stripe Checkout and webhook-led billing reconciliation."""

from types import SimpleNamespace
from uuid import uuid4

from app.core.config import settings
from app.services import stripe_billing_service
from app.services.billing_service import seed_plans
from app.services.stripe_billing_service import (
    StripeBillingError,
    create_checkout_session,
    process_webhook_event,
)
from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()


class _FakeStripe:
    api_key = None
    customer_calls: list[dict] = []
    checkout_calls: list[dict] = []
    portal_calls: list[dict] = []
    subscriptions: dict[str, dict] = {}

    class Customer:
        @staticmethod
        def create(**kwargs):
            _FakeStripe.customer_calls.append(kwargs)
            return {"id": f"cus_{len(_FakeStripe.customer_calls)}"}

    class checkout:
        class Session:
            @staticmethod
            def create(**kwargs):
                _FakeStripe.checkout_calls.append(kwargs)
                return {
                    "id": f"cs_{len(_FakeStripe.checkout_calls)}",
                    "url": "https://checkout.stripe.test/session",
                }

    class billing_portal:
        class Session:
            @staticmethod
            def create(**kwargs):
                _FakeStripe.portal_calls.append(kwargs)
                return {"url": "https://billing.stripe.test/portal"}

    class Subscription:
        @staticmethod
        def retrieve(subscription_id):
            return _FakeStripe.subscriptions[str(subscription_id)]


def _configure_stripe(monkeypatch, suffix: str) -> str:
    price_id = f"price_monthly_{suffix}"
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_realistic")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_realistic")
    monkeypatch.setattr(settings, "stripe_success_url", "https://app.example.com/billing/success")
    monkeypatch.setattr(settings, "stripe_cancel_url", "https://app.example.com/billing/cancel")
    monkeypatch.setattr(settings, "stripe_portal_return_url", "https://app.example.com/billing")
    monkeypatch.setattr(settings, "stripe_price_essencial_monthly", price_id)
    monkeypatch.setattr(stripe_billing_service, "_stripe_module", lambda: _FakeStripe)
    _FakeStripe.customer_calls = []
    _FakeStripe.checkout_calls = []
    _FakeStripe.portal_calls = []
    _FakeStripe.subscriptions = {}
    seed_plans()
    return price_id


def _subscription(
    *,
    company_id: int,
    subscription_id: str,
    customer_id: str,
    price_id: str,
    status: str,
) -> dict:
    return {
        "id": subscription_id,
        "customer": customer_id,
        "status": status,
        "metadata": {"company_id": str(company_id)},
        "items": {
            "data": [
                {
                    "price": {"id": price_id},
                    "current_period_start": 1_800_000_000,
                    "current_period_end": 1_802_592_000,
                }
            ]
        },
        "trial_end": None,
        "cancel_at_period_end": False,
    }


def _event(event_id: str, event_type: str, created: int, obj: dict) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "livemode": False,
        "data": {"object": obj},
    }


def test_checkout_stays_incomplete_until_a_signed_webhook_reconciles(monkeypatch) -> None:
    suffix = uuid4().hex[:10]
    price_id = _configure_stripe(monkeypatch, suffix)
    company_id = repository.create_company(
        slug=f"stripe-{suffix}",
        name="Stripe Tenant",
    )

    checkout = create_checkout_session(
        company_id=company_id,
        customer_email=f"owner-{suffix}@example.com",
        plan_key="essencial",
        billing_cycle="monthly",
        idempotency_key=f"checkout-request-{suffix}",
    )

    assert checkout["status"] == "pending_webhook"
    pending = repository.get_billing_subscription(company_id)
    assert pending["status"] == "incomplete"
    assert pending["stripe_subscription_id"] is None
    assert _FakeStripe.customer_calls[0]["idempotency_key"] == (
        f"project-wapp:customer:{company_id}"
    )
    assert _FakeStripe.checkout_calls[0]["idempotency_key"].endswith(
        f"checkout-request-{suffix}"
    )

    subscription_id = f"sub_{suffix}"
    canonical = _subscription(
        company_id=company_id,
        subscription_id=subscription_id,
        customer_id=str(pending["stripe_customer_id"]),
        price_id=price_id,
        status="active",
    )
    _FakeStripe.subscriptions[subscription_id] = canonical
    result = process_webhook_event(
        _event(
            f"evt_checkout_{suffix}",
            "checkout.session.completed",
            200,
            {"subscription": subscription_id},
        ),
        f"payload-{suffix}".encode(),
    )

    assert result["status"] == "processed"
    active = repository.get_billing_subscription(company_id)
    assert active["status"] == "active"
    assert active["stripe_subscription_id"] == subscription_id
    assert active["plan_key"] == "essencial"


def test_duplicate_event_is_acknowledged_without_second_reconciliation(monkeypatch) -> None:
    suffix = uuid4().hex[:10]
    price_id = _configure_stripe(monkeypatch, suffix)
    company_id = repository.create_company(
        slug=f"stripe-dup-{suffix}",
        name="Stripe Duplicate Tenant",
    )
    subscription_id = f"sub_dup_{suffix}"
    canonical = _subscription(
        company_id=company_id,
        subscription_id=subscription_id,
        customer_id=f"cus_dup_{suffix}",
        price_id=price_id,
        status="active",
    )
    _FakeStripe.subscriptions[subscription_id] = canonical
    event = _event(
        f"evt_dup_{suffix}",
        "customer.subscription.updated",
        300,
        canonical,
    )
    payload = f"duplicate-payload-{suffix}".encode()

    first = process_webhook_event(event, payload)
    _FakeStripe.subscriptions[subscription_id] = SimpleNamespace(
        id="must-not-be-read-on-duplicate"
    )
    second = process_webhook_event(event, payload)

    assert first["status"] == "processed"
    assert second == {
        "received": True,
        "duplicate": True,
        "status": "processed",
        "event_id": f"evt_dup_{suffix}",
    }


def test_out_of_order_event_cannot_regress_cancelled_subscription(monkeypatch) -> None:
    suffix = uuid4().hex[:10]
    price_id = _configure_stripe(monkeypatch, suffix)
    company_id = repository.create_company(
        slug=f"stripe-order-{suffix}",
        name="Stripe Ordering Tenant",
    )
    subscription_id = f"sub_order_{suffix}"
    cancelled = _subscription(
        company_id=company_id,
        subscription_id=subscription_id,
        customer_id=f"cus_order_{suffix}",
        price_id=price_id,
        status="canceled",
    )
    newer = process_webhook_event(
        _event(
            f"evt_newer_{suffix}",
            "customer.subscription.deleted",
            500,
            cancelled,
        ),
        f"newer-{suffix}".encode(),
    )
    assert newer["status"] == "processed"

    stale_active = {
        **cancelled,
        "status": "active",
    }
    _FakeStripe.subscriptions[subscription_id] = stale_active
    older = process_webhook_event(
        _event(
            f"evt_older_{suffix}",
            "customer.subscription.updated",
            400,
            stale_active,
        ),
        f"older-{suffix}".encode(),
    )

    assert older["status"] == "ignored"
    stored = repository.get_billing_subscription(company_id)
    assert stored["status"] == "cancelled"
    assert stored["gateway_event_created"] == 500


def test_event_id_replay_with_different_payload_is_rejected(monkeypatch) -> None:
    suffix = uuid4().hex[:10]
    price_id = _configure_stripe(monkeypatch, suffix)
    company_id = repository.create_company(
        slug=f"stripe-replay-{suffix}",
        name="Stripe Replay Tenant",
    )
    subscription_id = f"sub_replay_{suffix}"
    canonical = _subscription(
        company_id=company_id,
        subscription_id=subscription_id,
        customer_id=f"cus_replay_{suffix}",
        price_id=price_id,
        status="active",
    )
    _FakeStripe.subscriptions[subscription_id] = canonical
    event = _event(
        f"evt_replay_{suffix}",
        "customer.subscription.updated",
        550,
        canonical,
    )

    process_webhook_event(event, f"original-{suffix}".encode())

    try:
        process_webhook_event(event, f"tampered-{suffix}".encode())
    except StripeBillingError as exc:
        assert "different payload" in str(exc)
    else:
        raise AssertionError("Tampered webhook replay was accepted.")


def test_payment_grace_survives_subscription_updates_and_clears_on_paid(
    monkeypatch,
) -> None:
    suffix = uuid4().hex[:10]
    price_id = _configure_stripe(monkeypatch, suffix)
    company_id = repository.create_company(
        slug=f"stripe-grace-{suffix}",
        name="Stripe Grace Tenant",
    )
    subscription_id = f"sub_grace_{suffix}"
    canonical = _subscription(
        company_id=company_id,
        subscription_id=subscription_id,
        customer_id=f"cus_grace_{suffix}",
        price_id=price_id,
        status="active",
    )
    _FakeStripe.subscriptions[subscription_id] = canonical

    process_webhook_event(
        _event(
            f"evt_failed_{suffix}",
            "invoice.payment_failed",
            600,
            {"id": f"in_failed_{suffix}", "subscription": subscription_id},
        ),
        f"failed-{suffix}".encode(),
    )
    failed = repository.get_billing_subscription(company_id)
    assert failed["status"] == "past_due"
    assert failed["payment_status"] == "failed"
    assert failed["grace_period_ends_at"]
    grace_period_ends_at = failed["grace_period_ends_at"]

    canonical["status"] = "past_due"
    process_webhook_event(
        _event(
            f"evt_update_{suffix}",
            "customer.subscription.updated",
            601,
            canonical,
        ),
        f"updated-{suffix}".encode(),
    )
    updated = repository.get_billing_subscription(company_id)
    assert updated["payment_status"] == "failed"
    assert updated["grace_period_ends_at"] == grace_period_ends_at

    canonical["status"] = "active"
    process_webhook_event(
        _event(
            f"evt_paid_{suffix}",
            "invoice.paid",
            602,
            {"id": f"in_paid_{suffix}", "subscription": subscription_id},
        ),
        f"paid-{suffix}".encode(),
    )
    paid = repository.get_billing_subscription(company_id)
    assert paid["status"] == "active"
    assert paid["payment_status"] == "paid"
    assert paid["grace_period_ends_at"] is None
