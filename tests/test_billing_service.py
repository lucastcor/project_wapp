from datetime import UTC, datetime, timedelta

from app.services import billing_service
from app.services.billing_service import (
    PLANS,
    TRIAL_DAYS,
    TRIAL_PLAN,
    BillingError,
    activate_subscription,
    cancel_subscription,
    change_plan,
    check_limit,
    get_plans,
    get_usage_dashboard,
    reactivate_subscription,
    record_usage,
    seed_plans,
    start_trial,
)


class FakeBillingRepository:
    def __init__(self):
        self._plans = {}
        self._subscriptions = {}
        self._usage = []
        self._next_plan_id = 1
        self._next_sub_id = 1
        self._next_usage_id = 1

    def upsert_billing_plan(self, plan_key, name, price_monthly_cents,
                            price_annual_monthly_cents=None,
                            limits_json=None, features_json=None,
                            display_order=0):
        for pid, p in self._plans.items():
            if p["plan_key"] == plan_key:
                p.update({
                    "name": name,
                    "price_monthly_cents": price_monthly_cents,
                    "price_annual_monthly_cents": price_annual_monthly_cents,
                    "limits_json": limits_json,
                    "features_json": features_json,
                    "display_order": display_order,
                })
                return pid
        pid = self._next_plan_id
        self._next_plan_id += 1
        self._plans[pid] = {
            "id": pid,
            "plan_key": plan_key,
            "name": name,
            "price_monthly_cents": price_monthly_cents,
            "price_annual_monthly_cents": price_annual_monthly_cents,
            "limits_json": limits_json,
            "features_json": features_json,
            "display_order": display_order,
            "active": 1,
        }
        return pid

    def get_billing_plans(self, active_only=True):
        plans = list(self._plans.values())
        if active_only:
            plans = [p for p in plans if p.get("active", 1)]
        return plans

    def get_billing_plan(self, plan_key):
        for p in self._plans.values():
            if p["plan_key"] == plan_key:
                return p
        return None

    def create_billing_subscription(self, company_id, plan_key, status,
                                     billing_cycle, current_period_start,
                                     current_period_end, trial_ends_at=None,
                                     stripe_customer_id=None,
                                     stripe_subscription_id=None):
        sid = self._next_sub_id
        self._next_sub_id += 1
        self._subscriptions[company_id] = {
            "id": sid,
            "company_id": company_id,
            "plan_key": plan_key,
            "status": status,
            "billing_cycle": billing_cycle,
            "current_period_start": current_period_start,
            "current_period_end": current_period_end,
            "trial_ends_at": trial_ends_at,
            "cancelled_at": None,
            "cancel_reason": None,
        }
        return sid

    def get_billing_subscription(self, company_id):
        return self._subscriptions.get(company_id)

    def update_billing_subscription(self, sub_id, **kwargs):
        for sub in self._subscriptions.values():
            if sub["id"] == sub_id:
                sub.update(kwargs)
                return

    def record_billing_usage(self, company_id, metric, period_start,
                              period_end, quantity=1, subscription_id=None):
        uid = self._next_usage_id
        self._next_usage_id += 1
        self._usage.append({
            "id": uid,
            "company_id": company_id,
            "subscription_id": subscription_id,
            "metric": metric,
            "quantity": quantity,
            "period_start": period_start,
            "period_end": period_end,
            "recorded_at": datetime.now(UTC).isoformat(),
        })
        return uid

    def get_billing_usage_totals(self, company_id, period_start, period_end):
        totals = {}
        for u in self._usage:
            if u["company_id"] == company_id:
                m = u["metric"]
                totals[m] = totals.get(m, 0) + u["quantity"]
        return totals

    def get_billing_usage_daily(self, company_id, metric, period_start, period_end):
        return []


def _patch(monkeypatch, repo=None):
    fake = repo or FakeBillingRepository()
    monkeypatch.setattr(billing_service, "repository", fake)
    return fake


# ── seed_plans / get_plans ───────────────────────────────────────────


def test_seed_plans(monkeypatch):
    repo = _patch(monkeypatch)
    results = seed_plans()
    assert len(results) == len(PLANS)
    for r in results:
        assert r["plan_key"] in PLANS
        assert r["id"] is not None
    assert len(repo._plans) == len(PLANS)


def test_get_plans_seeds_when_empty(monkeypatch):
    _patch(monkeypatch)
    plans = get_plans()
    assert len(plans) == len(PLANS)
    for p in plans:
        assert "plan_key" in p
        assert "price_monthly" in p
        assert "limits" in p
        assert "features" in p


def test_get_plans_returns_existing(monkeypatch):
    _patch(monkeypatch)
    seed_plans()
    plans = get_plans()
    assert len(plans) == len(PLANS)


# ── start_trial ──────────────────────────────────────────────────────


def test_start_trial(monkeypatch):
    _patch(monkeypatch)
    result = start_trial(company_id=1)
    assert result["status"] == "trial"
    assert result["plan_key"] == TRIAL_PLAN
    assert result["trial_days"] == TRIAL_DAYS
    assert result["subscription_id"] is not None


def test_start_trial_rejects_duplicate(monkeypatch):
    _patch(monkeypatch)
    start_trial(company_id=1)
    try:
        start_trial(company_id=1)
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "already has" in str(exc)


# ── activate_subscription ───────────────────────────────────────────


def test_activate_new_subscription(monkeypatch):
    _patch(monkeypatch)
    result = activate_subscription(company_id=1, plan_key="essencial")
    assert result["status"] == "active"
    assert result["plan_key"] == "essencial"
    assert result["billing_cycle"] == "monthly"
    assert result["price_monthly"] == PLANS["essencial"]["price_monthly_cents"] / 100


def test_activate_from_trial(monkeypatch):
    repo = _patch(monkeypatch)
    start_trial(company_id=1)
    result = activate_subscription(company_id=1, plan_key="premium", billing_cycle="annual")
    assert result["status"] == "active"
    assert result["plan_key"] == "premium"
    assert result["billing_cycle"] == "annual"
    assert result["price_monthly"] == PLANS["premium"]["price_annual_monthly_cents"] / 100
    sub = repo.get_billing_subscription(1)
    assert sub["trial_ends_at"] is None


def test_activate_invalid_plan(monkeypatch):
    _patch(monkeypatch)
    try:
        activate_subscription(company_id=1, plan_key="nonexistent")
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "Invalid plan" in str(exc)


def test_activate_invalid_cycle(monkeypatch):
    _patch(monkeypatch)
    try:
        activate_subscription(company_id=1, plan_key="essencial", billing_cycle="weekly")
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "billing_cycle" in str(exc)


# ── cancel_subscription ─────────────────────────────────────────────


def test_cancel_subscription(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="avancado")
    result = cancel_subscription(company_id=1, reason="Too expensive")
    assert result["status"] == "cancelled"
    assert result["cancelled_at"] is not None
    assert result["access_until"] is not None


def test_cancel_no_subscription(monkeypatch):
    _patch(monkeypatch)
    try:
        cancel_subscription(company_id=1)
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "No subscription" in str(exc)


def test_cancel_already_cancelled(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    cancel_subscription(company_id=1)
    try:
        cancel_subscription(company_id=1)
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "already cancelled" in str(exc)


# ── change_plan ──────────────────────────────────────────────────────


def test_change_plan(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    result = change_plan(company_id=1, new_plan_key="avancado")
    assert result["old_plan"] == "essencial"
    assert result["new_plan"] == "avancado"
    assert result["price_monthly"] == PLANS["avancado"]["price_monthly_cents"] / 100


def test_change_plan_invalid(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    try:
        change_plan(company_id=1, new_plan_key="nonexistent")
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "Invalid plan" in str(exc)


def test_change_plan_no_subscription(monkeypatch):
    _patch(monkeypatch)
    try:
        change_plan(company_id=1, new_plan_key="premium")
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "No subscription" in str(exc)


def test_change_plan_cancelled_subscription(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    cancel_subscription(company_id=1)
    try:
        change_plan(company_id=1, new_plan_key="premium")
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "active or trial" in str(exc)


# ── record_usage ─────────────────────────────────────────────────────


def test_record_usage(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    result = record_usage(company_id=1, metric="messages", quantity=5)
    assert result["recorded"] is True
    assert result["metric"] == "messages"
    assert result["quantity"] == 5


def test_record_usage_invalid_metric(monkeypatch):
    _patch(monkeypatch)
    try:
        record_usage(company_id=1, metric="invalid_metric")
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "Invalid metric" in str(exc)


def test_record_usage_no_subscription(monkeypatch):
    _patch(monkeypatch)
    result = record_usage(company_id=1, metric="messages")
    assert result["recorded"] is False
    assert result["reason"] == "no_subscription"


# ── get_usage_dashboard ──────────────────────────────────────────────


def test_usage_dashboard_no_subscription(monkeypatch):
    _patch(monkeypatch)
    dashboard = get_usage_dashboard(company_id=1)
    assert dashboard["has_subscription"] is False
    assert dashboard["plan"] is None


def test_usage_dashboard_with_usage(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    record_usage(company_id=1, metric="messages", quantity=2500)
    record_usage(company_id=1, metric="bookings", quantity=50)

    dashboard = get_usage_dashboard(company_id=1)
    assert dashboard["has_subscription"] is True
    assert dashboard["subscription"]["plan_key"] == "essencial"
    assert dashboard["subscription"]["status"] == "active"

    msg_usage = dashboard["usage"]["messages"]
    assert msg_usage["used"] == 2500
    assert msg_usage["limit"] == 3000
    assert msg_usage["remaining"] == 500
    assert msg_usage["exceeded"] is False

    book_usage = dashboard["usage"]["bookings"]
    assert book_usage["used"] == 50
    assert book_usage["limit"] == 200


def test_usage_dashboard_trial_days_remaining(monkeypatch):
    _patch(monkeypatch)
    start_trial(company_id=1)
    dashboard = get_usage_dashboard(company_id=1)
    assert dashboard["subscription"]["status"] == "trial"
    assert dashboard["subscription"]["trial_days_remaining"] is not None
    assert dashboard["subscription"]["trial_days_remaining"] >= 6


def test_usage_dashboard_alerts_warning(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    record_usage(company_id=1, metric="messages", quantity=2500)

    dashboard = get_usage_dashboard(company_id=1)
    warnings = [a for a in dashboard["alerts"] if a["type"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["metric"] == "messages"


def test_usage_dashboard_alerts_exceeded_degraded(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    record_usage(company_id=1, metric="handoffs", quantity=50)

    dashboard = get_usage_dashboard(company_id=1)
    exceeded = [a for a in dashboard["alerts"] if a["type"] == "exceeded"]
    assert len(exceeded) == 1
    assert exceeded[0]["degraded"] is True


# ── check_limit ──────────────────────────────────────────────────────


def test_check_limit_allowed(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    record_usage(company_id=1, metric="messages", quantity=100)
    result = check_limit(company_id=1, metric="messages")
    assert result["allowed"] is True
    assert result["used"] == 100
    assert result["limit"] == 3000
    assert result["remaining"] == 2900


def test_check_limit_exceeded_not_degraded(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    record_usage(company_id=1, metric="messages", quantity=3000)
    result = check_limit(company_id=1, metric="messages")
    assert result["allowed"] is True
    assert result["reason"] == "limit_exceeded"
    assert result["degraded"] is False


def test_check_limit_exceeded_degraded(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    record_usage(company_id=1, metric="handoffs", quantity=50)
    result = check_limit(company_id=1, metric="handoffs")
    assert result["allowed"] is False
    assert result["degraded"] is True


def test_check_limit_no_subscription(monkeypatch):
    _patch(monkeypatch)
    result = check_limit(company_id=1, metric="messages")
    assert result["allowed"] is True
    assert result["reason"] == "no_subscription"


def test_check_limit_cancelled_expired(monkeypatch):
    repo = _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    cancel_subscription(company_id=1)
    sub = repo.get_billing_subscription(1)
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    sub["current_period_end"] = past

    result = check_limit(company_id=1, metric="messages")
    assert result["allowed"] is False
    assert result["reason"] == "subscription_expired"


def test_check_limit_cancelled_still_in_period(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    cancel_subscription(company_id=1)
    result = check_limit(company_id=1, metric="messages")
    assert result["allowed"] is True


# ── Plan structure validation ────────────────────────────────────────


def test_all_plans_have_required_fields():
    for key, plan in PLANS.items():
        assert "name" in plan
        assert "price_monthly_cents" in plan
        assert "price_annual_monthly_cents" in plan
        assert "limits" in plan
        assert "features" in plan
        assert "display_order" in plan
        assert plan["price_annual_monthly_cents"] < plan["price_monthly_cents"]


def test_plan_limits_keys_consistent():
    keys = None
    for plan in PLANS.values():
        current_keys = set(plan["limits"].keys())
        if keys is None:
            keys = current_keys
        else:
            assert keys == current_keys, f"Limit keys differ: {keys} vs {current_keys}"


def test_plan_order_ascending():
    ordered = sorted(PLANS.values(), key=lambda p: p["display_order"])
    prices = [p["price_monthly_cents"] for p in ordered]
    assert prices == sorted(prices)


# ── reactivate_subscription ─────────────────────────────────────────


def test_reactivate_cancelled_subscription(monkeypatch):
    repo = _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    cancel_subscription(company_id=1)
    result = reactivate_subscription(company_id=1)
    assert result["status"] == "active"
    assert result["plan_key"] == "essencial"
    assert result["reactivated"] is True
    sub = repo.get_billing_subscription(1)
    assert sub["status"] == "active"
    assert sub["cancelled_at"] is None
    assert sub["cancel_reason"] is None


def test_reactivate_with_different_plan(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    cancel_subscription(company_id=1)
    result = reactivate_subscription(company_id=1, plan_key="premium", billing_cycle="annual")
    assert result["plan_key"] == "premium"
    assert result["billing_cycle"] == "annual"
    assert result["price_monthly"] == PLANS["premium"]["price_annual_monthly_cents"] / 100


def test_reactivate_active_subscription_fails(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    try:
        reactivate_subscription(company_id=1)
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "cancelled" in str(exc).lower()


def test_reactivate_no_subscription_fails(monkeypatch):
    _patch(monkeypatch)
    try:
        reactivate_subscription(company_id=1)
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "No subscription" in str(exc)


def test_reactivate_invalid_plan_fails(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    cancel_subscription(company_id=1)
    try:
        reactivate_subscription(company_id=1, plan_key="nonexistent")
        assert False, "Should have raised BillingError"
    except BillingError as exc:
        assert "Invalid plan" in str(exc)


# ── record_usage on expired subscription ────────────────────────────


def test_record_usage_expired_cancelled_subscription(monkeypatch):
    repo = _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    cancel_subscription(company_id=1)
    sub = repo.get_billing_subscription(1)
    sub["current_period_end"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    result = record_usage(company_id=1, metric="messages")
    assert result["recorded"] is False
    assert result["reason"] == "subscription_expired"


def test_record_usage_cancelled_still_in_grace_period(monkeypatch):
    _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    cancel_subscription(company_id=1)
    result = record_usage(company_id=1, metric="messages")
    assert result["recorded"] is True


def test_record_usage_expired_trial(monkeypatch):
    repo = _patch(monkeypatch)
    start_trial(company_id=1)
    sub = repo.get_billing_subscription(1)
    sub["trial_ends_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    result = record_usage(company_id=1, metric="messages")
    assert result["recorded"] is False
    assert result["reason"] == "subscription_expired"


# ── usage dashboard expired flags ───────────────────────────────────


def test_usage_dashboard_shows_expired_flag(monkeypatch):
    repo = _patch(monkeypatch)
    activate_subscription(company_id=1, plan_key="essencial")
    cancel_subscription(company_id=1)
    sub = repo.get_billing_subscription(1)
    sub["current_period_end"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    dashboard = get_usage_dashboard(company_id=1)
    assert dashboard["subscription"]["expired"] is True


def test_usage_dashboard_trial_expired_flag(monkeypatch):
    repo = _patch(monkeypatch)
    start_trial(company_id=1)
    sub = repo.get_billing_subscription(1)
    sub["trial_ends_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    dashboard = get_usage_dashboard(company_id=1)
    assert dashboard["subscription"]["trial_expired"] is True


# ── full lifecycle: trial → activate → cancel → reactivate ──────────


def test_full_billing_lifecycle(monkeypatch):
    repo = _patch(monkeypatch)

    # Start trial
    trial = start_trial(company_id=1)
    assert trial["status"] == "trial"

    # Activate
    activated = activate_subscription(company_id=1, plan_key="avancado")
    assert activated["status"] == "active"

    # Use some quota
    record_usage(company_id=1, metric="messages", quantity=100)
    dashboard = get_usage_dashboard(company_id=1)
    assert dashboard["usage"]["messages"]["used"] == 100

    # Cancel
    cancelled = cancel_subscription(company_id=1, reason="Testing lifecycle")
    assert cancelled["status"] == "cancelled"

    # Change plan should fail while cancelled
    try:
        change_plan(company_id=1, new_plan_key="premium")
        assert False
    except BillingError:
        pass

    # Reactivate with upgrade
    reactivated = reactivate_subscription(company_id=1, plan_key="premium")
    assert reactivated["status"] == "active"
    assert reactivated["plan_key"] == "premium"

    # Verify clean state
    sub = repo.get_billing_subscription(1)
    assert sub["cancelled_at"] is None
    assert sub["cancel_reason"] is None
