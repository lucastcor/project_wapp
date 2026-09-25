"""Billing must render before any Stripe price is configured.

`.env.example` ships every `STRIPE_PRICE_*` key empty. Those reach the
settings object as "" rather than None, and the gateway price columns are
unique, so seeding wrote the same empty string for every plan and the
Billing screen answered 500 on a fresh install.
"""

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.billing_service import PLANS, seed_plans
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()

_PRICE_FIELDS = (
    "stripe_price_essencial_monthly",
    "stripe_price_essencial_annual",
    "stripe_price_avancado_monthly",
    "stripe_price_avancado_annual",
    "stripe_price_premium_monthly",
    "stripe_price_premium_annual",
)


def test_seed_plans_treats_blank_stripe_price_ids_as_unset(monkeypatch) -> None:
    for field in _PRICE_FIELDS:
        monkeypatch.setattr(settings, field, "")

    results = seed_plans()
    assert len(results) == len(PLANS)

    rows = {row["plan_key"]: row for row in repository.get_billing_plans(active_only=False)}
    for plan_key in PLANS:
        assert rows[plan_key]["stripe_monthly_price_id"] is None
        assert rows[plan_key]["stripe_annual_price_id"] is None


def test_billing_plans_endpoint_answers_200_without_stripe_prices(monkeypatch) -> None:
    for field in _PRICE_FIELDS:
        monkeypatch.setattr(settings, field, "")
    with TestClient(app) as client:
        response = client.get("/admin/billing/plans")
        assert response.status_code == 200, response.text
        assert {plan["plan_key"] for plan in response.json()} == set(PLANS)
