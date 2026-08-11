"""Stripe Checkout, Customer Portal and webhook-led subscription reconciliation."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import is_missing_or_placeholder, settings
from app.services.billing_service import seed_plans
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()
SUPPORTED_WEBHOOK_EVENTS = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
}


class StripeBillingError(Exception):
    pass


def _stripe_module() -> Any:
    if is_missing_or_placeholder(settings.stripe_secret_key):
        raise StripeBillingError("STRIPE_SECRET_KEY is not configured.")
    try:
        import stripe
    except ImportError as exc:
        raise StripeBillingError("The Stripe SDK is not installed.") from exc
    stripe.api_key = settings.stripe_secret_key
    return stripe


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        current = _value(current, key)
        if current is None:
            return None
    return current


def _unix_timestamp(value: Any, *, fallback: datetime | None = None) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return (fallback or datetime.now(UTC)).isoformat()


def _price_mapping(plan_key: str, billing_cycle: str) -> tuple[dict[str, Any], str]:
    if billing_cycle not in {"monthly", "annual"}:
        raise StripeBillingError("billing_cycle must be monthly or annual.")
    seed_plans()
    plan = repository.get_billing_plan(plan_key)
    if not plan or not bool(plan.get("active")):
        raise StripeBillingError("Billing plan is unavailable.")
    column = (
        "stripe_annual_price_id"
        if billing_cycle == "annual"
        else "stripe_monthly_price_id"
    )
    price_id = str(plan.get(column) or "").strip()
    if not price_id:
        raise StripeBillingError(
            f"Stripe price is not configured for {plan_key}/{billing_cycle}."
        )
    return plan, price_id


def create_checkout_session(
    *,
    company_id: int,
    customer_email: str,
    plan_key: str,
    billing_cycle: str,
    idempotency_key: str,
) -> dict[str, Any]:
    stripe = _stripe_module()
    _plan, price_id = _price_mapping(plan_key, billing_cycle)
    success_url = str(settings.stripe_success_url or "").strip()
    cancel_url = str(settings.stripe_cancel_url or "").strip()
    if not success_url or not cancel_url:
        raise StripeBillingError("STRIPE_SUCCESS_URL and STRIPE_CANCEL_URL are required.")

    subscription = repository.get_billing_subscription(company_id)
    if (
        subscription
        and subscription.get("gateway") == "stripe"
        and subscription.get("stripe_subscription_id")
        and subscription.get("status") not in {"cancelled", "incomplete_expired"}
    ):
        raise StripeBillingError(
            "This company already has a Stripe subscription; use the Customer Portal."
        )

    customer_id = str(subscription.get("stripe_customer_id") or "") if subscription else ""
    if not customer_id:
        customer = stripe.Customer.create(
            email=customer_email,
            metadata={"company_id": str(company_id)},
            idempotency_key=f"project-wapp:customer:{company_id}",
        )
        customer_id = str(_value(customer, "id") or "")
        if not customer_id:
            raise StripeBillingError("Stripe did not return a customer id.")

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(company_id),
        metadata={
            "company_id": str(company_id),
            "plan_key": plan_key,
            "billing_cycle": billing_cycle,
        },
        subscription_data={
            "metadata": {
                "company_id": str(company_id),
                "plan_key": plan_key,
                "billing_cycle": billing_cycle,
            }
        },
        idempotency_key=f"project-wapp:checkout:{company_id}:{idempotency_key}",
    )
    session_id = str(_value(session, "id") or "")
    checkout_url = str(_value(session, "url") or "")
    if not session_id or not checkout_url:
        raise StripeBillingError("Stripe did not return a Checkout Session URL.")

    now = datetime.now(UTC).isoformat()
    if subscription:
        repository.update_billing_subscription(
            int(subscription["id"]),
            company_id=company_id,
            stripe_customer_id=customer_id,
            stripe_price_id=price_id,
            gateway="stripe",
        )
    else:
        repository.create_billing_subscription(
            company_id=company_id,
            plan_key=plan_key,
            status="incomplete",
            billing_cycle=billing_cycle,
            current_period_start=now,
            current_period_end=now,
            stripe_customer_id=customer_id,
            stripe_price_id=price_id,
            gateway="stripe",
        )
    return {
        "checkout_session_id": session_id,
        "checkout_url": checkout_url,
        "status": "pending_webhook",
    }


def create_customer_portal_session(
    *,
    company_id: int,
    idempotency_key: str,
) -> dict[str, Any]:
    stripe = _stripe_module()
    subscription = repository.get_billing_subscription(company_id)
    customer_id = (
        str(subscription.get("stripe_customer_id") or "")
        if subscription
        else ""
    )
    if not customer_id:
        raise StripeBillingError("No Stripe customer exists for this company.")
    return_url = str(
        settings.stripe_portal_return_url
        or settings.stripe_success_url
        or ""
    ).strip()
    if not return_url:
        raise StripeBillingError("STRIPE_PORTAL_RETURN_URL is required.")
    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
        idempotency_key=f"project-wapp:portal:{company_id}:{idempotency_key}",
    )
    portal_url = str(_value(portal, "url") or "")
    if not portal_url:
        raise StripeBillingError("Stripe did not return a Customer Portal URL.")
    return {"portal_url": portal_url}


def construct_webhook_event(payload: bytes, signature: str | None) -> Any:
    if is_missing_or_placeholder(settings.stripe_webhook_secret):
        raise StripeBillingError("STRIPE_WEBHOOK_SECRET is not configured.")
    if not signature:
        raise StripeBillingError("Missing Stripe-Signature header.")
    stripe = _stripe_module()
    try:
        return stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature,
            secret=settings.stripe_webhook_secret,
        )
    except Exception as exc:
        raise StripeBillingError("Invalid Stripe webhook signature or payload.") from exc


def _subscription_id_from_invoice(invoice: Any) -> str | None:
    candidates = (
        _value(invoice, "subscription"),
        _nested(invoice, "parent", "subscription_details", "subscription"),
        _nested(invoice, "lines", "data"),
    )
    for candidate in candidates[:2]:
        if candidate:
            return str(_value(candidate, "id", candidate))
    line_items = candidates[2]
    if isinstance(line_items, list) and line_items:
        subscription_id = _nested(
            line_items[0],
            "parent",
            "subscription_item_details",
            "subscription",
        )
        if subscription_id:
            return str(_value(subscription_id, "id", subscription_id))
    return None


def _canonical_subscription(event_type: str, event_object: Any) -> tuple[Any, str | None]:
    stripe = _stripe_module()
    invoice_id = None
    if event_type.startswith("customer.subscription."):
        subscription = event_object
        subscription_id = str(_value(subscription, "id") or "")
    elif event_type == "checkout.session.completed":
        subscription_id = str(_value(event_object, "subscription") or "")
        if not subscription_id:
            raise StripeBillingError("Checkout completed without a subscription id.")
        subscription = stripe.Subscription.retrieve(subscription_id)
    else:
        invoice_id = str(_value(event_object, "id") or "") or None
        subscription_id = _subscription_id_from_invoice(event_object) or ""
        if not subscription_id:
            raise StripeBillingError("Invoice event does not identify a subscription.")
        subscription = stripe.Subscription.retrieve(subscription_id)

    # A late update event must reconcile the provider's current object, not its
    # stale embedded snapshot. Deleted events already carry the terminal object.
    if event_type != "customer.subscription.deleted":
        subscription = stripe.Subscription.retrieve(subscription_id)
    return subscription, invoice_id


def _subscription_company_id(subscription: Any) -> int:
    metadata = _value(subscription, "metadata") or {}
    raw_company_id = _value(metadata, "company_id")
    if raw_company_id:
        try:
            company_id = int(raw_company_id)
        except (TypeError, ValueError) as exc:
            raise StripeBillingError("Stripe subscription has invalid company metadata.") from exc
        if repository.get_company(company_id):
            return company_id
        raise StripeBillingError("Stripe subscription references an unknown company.")

    subscription_id = str(_value(subscription, "id") or "")
    existing = repository.get_billing_subscription_by_stripe_id(subscription_id)
    if existing:
        return int(existing["company_id"])
    raise StripeBillingError("Stripe subscription is missing company metadata.")


def _subscription_price(subscription: Any) -> tuple[str, str, str]:
    items = _nested(subscription, "items", "data")
    if not isinstance(items, list) or not items:
        raise StripeBillingError("Stripe subscription has no price item.")
    price_id = str(_nested(items[0], "price", "id") or "")
    if not price_id:
        raise StripeBillingError("Stripe subscription price id is missing.")
    plan = repository.get_billing_plan_by_price_id(price_id)
    if not plan:
        seed_plans()
        plan = repository.get_billing_plan_by_price_id(price_id)
    if not plan:
        raise StripeBillingError("Stripe price is not mapped to an internal plan.")
    billing_cycle = (
        "annual"
        if price_id == str(plan.get("stripe_annual_price_id") or "")
        else "monthly"
    )
    return str(plan["plan_key"]), billing_cycle, price_id


def _normalize_subscription_status(raw_status: str) -> str:
    return {
        "trialing": "trial",
        "canceled": "cancelled",
    }.get(raw_status, raw_status)


def process_webhook_event(event: Any, payload: bytes) -> dict[str, Any]:
    event_id = str(_value(event, "id") or "")
    event_type = str(_value(event, "type") or "")
    event_created = int(_value(event, "created") or 0)
    if not event_id or not event_type or event_created <= 0:
        raise StripeBillingError("Stripe event metadata is incomplete.")
    payload_hash = hashlib.sha256(payload).hexdigest()
    try:
        claim = repository.claim_billing_webhook_event(
            provider="stripe",
            event_id=event_id,
            event_type=event_type,
            event_created=event_created,
            livemode=bool(_value(event, "livemode", False)),
            payload_hash=payload_hash,
            processing_timeout_seconds=settings.stripe_webhook_claim_ttl_seconds,
        )
    except RuntimeError as exc:
        raise StripeBillingError(str(exc)) from exc
    if not claim["claimed"]:
        return {
            "received": True,
            "duplicate": True,
            "status": claim["status"],
            "event_id": event_id,
        }

    company_id = None
    try:
        if event_type not in SUPPORTED_WEBHOOK_EVENTS:
            repository.complete_billing_webhook_event(
                provider="stripe",
                event_id=event_id,
                status="ignored",
            )
            return {
                "received": True,
                "duplicate": False,
                "status": "ignored",
                "event_id": event_id,
            }

        event_object = _nested(event, "data", "object")
        subscription, invoice_id = _canonical_subscription(event_type, event_object)
        company_id = _subscription_company_id(subscription)
        plan_key, billing_cycle, price_id = _subscription_price(subscription)
        raw_status = str(_value(subscription, "status") or "incomplete")
        status = _normalize_subscription_status(raw_status)
        customer_id = str(_value(subscription, "customer") or "")
        subscription_id = str(_value(subscription, "id") or "")
        if not customer_id or not subscription_id:
            raise StripeBillingError("Stripe subscription identifiers are incomplete.")

        item = _nested(subscription, "items", "data")
        first_item = item[0] if isinstance(item, list) and item else {}
        period_start = _value(subscription, "current_period_start") or _value(
            first_item,
            "current_period_start",
        )
        period_end = _value(subscription, "current_period_end") or _value(
            first_item,
            "current_period_end",
        )
        now = datetime.now(UTC)
        payment_status = None
        grace_period_ends_at = None
        if event_type == "invoice.paid":
            payment_status = "paid"
        elif event_type == "invoice.payment_failed":
            payment_status = "failed"
            if status == "active":
                status = "past_due"
            grace_period_ends_at = (
                now + timedelta(days=max(0, settings.billing_grace_period_days))
            ).isoformat()

        reconciliation = repository.reconcile_billing_subscription(
            company_id=company_id,
            plan_key=plan_key,
            status=status,
            billing_cycle=billing_cycle,
            current_period_start=_unix_timestamp(period_start, fallback=now),
            current_period_end=_unix_timestamp(period_end, fallback=now),
            trial_ends_at=(
                _unix_timestamp(_value(subscription, "trial_end"))
                if _value(subscription, "trial_end")
                else None
            ),
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            stripe_price_id=price_id,
            payment_status=payment_status,
            cancel_at_period_end=bool(_value(subscription, "cancel_at_period_end", False)),
            grace_period_ends_at=grace_period_ends_at,
            last_invoice_id=invoice_id,
            event_created=event_created,
            event_id=event_id,
        )
        ledger_status = "processed" if reconciliation["applied"] else "ignored"
        repository.complete_billing_webhook_event(
            provider="stripe",
            event_id=event_id,
            status=ledger_status,
            company_id=company_id,
        )
        return {
            "received": True,
            "duplicate": False,
            "status": ledger_status,
            "event_id": event_id,
            "company_id": company_id,
            "subscription_status": status,
            "reconciliation": reconciliation["reason"],
        }
    except Exception as exc:
        repository.complete_billing_webhook_event(
            provider="stripe",
            event_id=event_id,
            status="failed",
            company_id=company_id,
            error=str(exc),
        )
        if isinstance(exc, StripeBillingError):
            raise
        raise StripeBillingError("Stripe event reconciliation failed.") from exc
