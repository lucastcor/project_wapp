"""Public provider webhooks; authentication is the provider signature."""

from fastapi import APIRouter, Header, HTTPException, Request

from app.services.stripe_billing_service import (
    StripeBillingError,
    construct_webhook_event,
    process_webhook_event,
)


billing_router = APIRouter(tags=["billing-webhooks"])


@billing_router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict:
    payload = await request.body()
    try:
        event = construct_webhook_event(payload, stripe_signature)
        return process_webhook_event(event, payload)
    except StripeBillingError as exc:
        message = str(exc)
        invalid_signature = (
            "signature" in message.lower()
            or "payload" in message.lower()
            or "missing stripe-signature" in message.lower()
        )
        raise HTTPException(
            status_code=400 if invalid_signature else 503,
            detail=message,
        ) from exc
