import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()

TRIAL_DAYS = 7
TRIAL_PLAN = "avancado"

PLANS: dict[str, dict[str, Any]] = {
    "essencial": {
        "name": "Essencial",
        "price_monthly_cents": 44700,
        "price_annual_monthly_cents": 39700,
        "display_order": 1,
        "limits": {
            "messages": 3000,
            "bookings": 200,
            "outbound_campaigns": 2,
            "classified_contacts": 300,
            "finetuning_jobs": 0,
            "handoffs": 50,
            "reminders_per_booking": 2,
            "automation_workflows": 3,
            "automation_executions": 2000,
        },
        "features": {
            "finetuning": False,
            "priority_support": False,
            "dedicated_support": False,
            "automations": True,
        },
    },
    "avancado": {
        "name": "Avancado",
        "price_monthly_cents": 69700,
        "price_annual_monthly_cents": 59700,
        "display_order": 2,
        "limits": {
            "messages": 10000,
            "bookings": 800,
            "outbound_campaigns": 10,
            "classified_contacts": 1500,
            "finetuning_jobs": 1,
            "handoffs": 200,
            "reminders_per_booking": 3,
            "automation_workflows": 15,
            "automation_executions": 15000,
        },
        "features": {
            "finetuning": True,
            "priority_support": True,
            "dedicated_support": False,
            "automations": True,
        },
    },
    "premium": {
        "name": "Premium",
        "price_monthly_cents": 119700,
        "price_annual_monthly_cents": 99700,
        "display_order": 3,
        "limits": {
            "messages": 30000,
            "bookings": 3000,
            "outbound_campaigns": 50,
            "classified_contacts": 5000,
            "finetuning_jobs": 5,
            "handoffs": 1000,
            "reminders_per_booking": 5,
            "automation_workflows": 60,
            "automation_executions": 60000,
        },
        "features": {
            "finetuning": True,
            "priority_support": True,
            "dedicated_support": True,
            "automations": True,
        },
    },
}

USAGE_METRICS = {
    "messages",
    "bookings",
    "outbound_campaigns",
    "handoffs",
    "finetuning_jobs",
    # Cada inscrição num fluxo consome orçamento. É a única forma honesta de
    # cobrar automação: o custo é por execução, não por fluxo desenhado.
    "automation_executions",
}

DEGRADED_METRICS = {"outbound_campaigns", "handoffs", "finetuning_jobs", "automation_executions"}
HARD_LIMIT_METRICS = {"bookings", "outbound_campaigns", "finetuning_jobs"}
ALERT_THRESHOLD = 0.80


class BillingError(Exception):
    pass


class BillingLimitError(BillingError):
    pass


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _subscription_access(sub: dict[str, Any]) -> tuple[bool, str]:
    now = datetime.now(UTC)
    status = str(sub.get("status") or "")
    if status == "active":
        return True, "active"
    if status == "trial":
        trial_end = _parse_datetime(sub.get("trial_ends_at"))
        return (
            (True, "trial")
            if trial_end is None or now <= trial_end
            else (False, "trial_expired")
        )
    if status == "past_due":
        grace_end = _parse_datetime(sub.get("grace_period_ends_at"))
        return (
            (True, "payment_grace_period")
            if grace_end is not None and now <= grace_end
            else (False, "payment_failed")
        )
    if status == "cancelled":
        period_end = _parse_datetime(sub.get("current_period_end"))
        return (
            (True, "cancelled_access_until_period_end")
            if period_end is not None and now <= period_end
            else (False, "subscription_expired")
        )
    return False, f"subscription_{status or 'unknown'}"


def consume_usage(
    *,
    company_id: int,
    metric: str,
    quantity: int = 1,
) -> dict[str, Any]:
    """Atomically enforce hard limits and record real product usage."""
    if metric not in USAGE_METRICS:
        raise BillingError(f"Invalid metric: {metric}")
    if quantity <= 0:
        raise BillingError("quantity must be greater than zero.")
    subscription = repository.get_billing_subscription(company_id)
    if not subscription:
        if settings.enable_billing_enforcement:
            return {"allowed": False, "recorded": False, "reason": "no_subscription"}
        return {
            "allowed": True,
            "recorded": False,
            "reason": "billing_enforcement_disabled",
        }

    has_access, access_reason = _subscription_access(subscription)
    if not has_access and settings.enable_billing_enforcement:
        return {"allowed": False, "recorded": False, "reason": access_reason}

    plan = PLANS.get(str(subscription.get("plan_key")), PLANS["essencial"])
    limit = int(plan["limits"].get(metric, 0) or 0)
    hard_limit = metric in HARD_LIMIT_METRICS and settings.enable_billing_enforcement
    if hard_limit and limit <= 0:
        return {
            "allowed": False,
            "recorded": False,
            "reason": "feature_not_in_plan",
            "limit": limit,
        }

    result = repository.consume_billing_usage(
        company_id=company_id,
        subscription_id=int(subscription["id"]),
        metric=metric,
        period_start=str(subscription.get("current_period_start") or ""),
        period_end=str(subscription.get("current_period_end") or ""),
        quantity=quantity,
        limit=limit if hard_limit else None,
    )
    return {
        "allowed": bool(result["consumed"]) or not hard_limit,
        "recorded": bool(result["consumed"]),
        "reason": result["reason"] if result["consumed"] else (
            result["reason"] if hard_limit else access_reason
        ),
        "used": result.get("used"),
        "limit": limit,
        "hard_limit": hard_limit,
        "usage_record_id": result.get("usage_record_id"),
    }


def require_usage_capacity(
    *,
    company_id: int,
    metric: str,
    quantity: int = 1,
) -> dict[str, Any]:
    decision = consume_usage(
        company_id=company_id,
        metric=metric,
        quantity=quantity,
    )
    if not decision["allowed"]:
        raise BillingLimitError(
            f"Billing policy blocked {metric}: {decision['reason']}."
        )
    return decision


def refund_usage_reservation(*, company_id: int, decision: dict[str, Any]) -> None:
    usage_record_id = decision.get("usage_record_id")
    if usage_record_id is not None:
        repository.refund_billing_usage(
            usage_record_id=int(usage_record_id),
            company_id=company_id,
        )


def seed_plans() -> list[dict[str, Any]]:
    results = []
    stripe_prices = {
        "essencial": (
            settings.stripe_price_essencial_monthly,
            settings.stripe_price_essencial_annual,
        ),
        "avancado": (
            settings.stripe_price_avancado_monthly,
            settings.stripe_price_avancado_annual,
        ),
        "premium": (
            settings.stripe_price_premium_monthly,
            settings.stripe_price_premium_annual,
        ),
    }
    for plan_key, plan in PLANS.items():
        raw_monthly_price_id, raw_annual_price_id = stripe_prices[plan_key]
        # `.env` files ship the STRIPE_PRICE_* keys empty (`KEY=`), which
        # reaches here as "" rather than None. The gateway columns carry a
        # unique index, so writing "" for every plan collides on the second
        # row and the whole Billing screen answered 500 before any Stripe
        # price was configured.
        monthly_price_id = (raw_monthly_price_id or "").strip() or None
        annual_price_id = (raw_annual_price_id or "").strip() or None
        plan_id = repository.upsert_billing_plan(
            plan_key=plan_key,
            name=plan["name"],
            price_monthly_cents=plan["price_monthly_cents"],
            price_annual_monthly_cents=plan.get("price_annual_monthly_cents"),
            limits_json=json.dumps(plan["limits"]),
            features_json=json.dumps(plan["features"]),
            display_order=plan["display_order"],
        )
        if hasattr(repository, "update_billing_plan_gateway_mapping"):
            repository.update_billing_plan_gateway_mapping(
                plan_id,
                currency="brl",
                stripe_monthly_price_id=monthly_price_id,
                stripe_annual_price_id=annual_price_id,
            )
        results.append({"plan_key": plan_key, "id": plan_id})
    return results


def get_plans() -> list[dict[str, Any]]:
    # Also refresh immutable Stripe price mappings from deployment config.
    seed_plans()
    db_plans = repository.get_billing_plans(active_only=True)

    plans = []
    for row in db_plans:
        limits = json.loads(row.get("limits_json") or "{}")
        features = json.loads(row.get("features_json") or "{}")
        plans.append({
            "plan_key": row["plan_key"],
            "name": row["name"],
            "price_monthly": row["price_monthly_cents"] / 100,
            "price_annual_monthly": (row.get("price_annual_monthly_cents") or 0) / 100,
            "limits": limits,
            "features": features,
            "display_order": row.get("display_order", 0),
            "currency": row.get("currency") or "brl",
            "stripe_checkout_ready": bool(row.get("stripe_monthly_price_id")),
        })
    return plans


def start_trial(company_id: int) -> dict[str, Any]:
    existing = repository.get_billing_subscription(company_id)
    if existing:
        raise BillingError("Company already has a subscription.")

    now = datetime.now(UTC)
    trial_end = now + timedelta(days=TRIAL_DAYS)
    period_end = trial_end

    sub_id = repository.create_billing_subscription(
        company_id=company_id,
        plan_key=TRIAL_PLAN,
        status="trial",
        billing_cycle="monthly",
        current_period_start=now.isoformat(),
        current_period_end=period_end.isoformat(),
        trial_ends_at=trial_end.isoformat(),
    )

    return {
        "subscription_id": sub_id,
        "status": "trial",
        "plan_key": TRIAL_PLAN,
        "trial_ends_at": trial_end.isoformat(),
        "trial_days": TRIAL_DAYS,
    }


def activate_subscription(
    company_id: int,
    plan_key: str,
    billing_cycle: str = "monthly",
) -> dict[str, Any]:
    if plan_key not in PLANS:
        raise BillingError(f"Invalid plan: {plan_key}")
    if billing_cycle not in {"monthly", "annual"}:
        raise BillingError("billing_cycle must be 'monthly' or 'annual'.")

    sub = repository.get_billing_subscription(company_id)
    now = datetime.now(UTC)

    period_end = now + timedelta(days=365) if billing_cycle == "annual" else now + timedelta(days=30)

    if sub:
        repository.update_billing_subscription(
            sub["id"],
            plan_key=plan_key,
            status="active",
            billing_cycle=billing_cycle,
            current_period_start=now.isoformat(),
            current_period_end=period_end.isoformat(),
            trial_ends_at=None,
        )
        sub_id = int(sub["id"])
    else:
        sub_id = repository.create_billing_subscription(
            company_id=company_id,
            plan_key=plan_key,
            status="active",
            billing_cycle=billing_cycle,
            current_period_start=now.isoformat(),
            current_period_end=period_end.isoformat(),
        )

    plan = PLANS[plan_key]
    price = plan["price_annual_monthly_cents"] if billing_cycle == "annual" else plan["price_monthly_cents"]

    return {
        "subscription_id": sub_id,
        "status": "active",
        "plan_key": plan_key,
        "billing_cycle": billing_cycle,
        "price_monthly": price / 100,
        "current_period_end": period_end.isoformat(),
    }


def cancel_subscription(company_id: int, reason: str | None = None) -> dict[str, Any]:
    sub = repository.get_billing_subscription(company_id)
    if not sub:
        raise BillingError("No subscription found.")
    if sub["status"] == "cancelled":
        raise BillingError("Subscription is already cancelled.")

    now = datetime.now(UTC)
    repository.update_billing_subscription(
        sub["id"],
        status="cancelled",
        cancelled_at=now.isoformat(),
        cancel_reason=reason,
    )

    return {
        "subscription_id": int(sub["id"]),
        "status": "cancelled",
        "cancelled_at": now.isoformat(),
        "access_until": sub.get("current_period_end"),
    }


def reactivate_subscription(
    company_id: int,
    plan_key: str | None = None,
    billing_cycle: str = "monthly",
) -> dict[str, Any]:
    sub = repository.get_billing_subscription(company_id)
    if not sub:
        raise BillingError("No subscription found.")
    if sub["status"] != "cancelled":
        raise BillingError("Only cancelled subscriptions can be reactivated.")

    chosen_plan = plan_key or sub["plan_key"]
    if chosen_plan not in PLANS:
        raise BillingError(f"Invalid plan: {chosen_plan}")
    if billing_cycle not in {"monthly", "annual"}:
        raise BillingError("billing_cycle must be 'monthly' or 'annual'.")

    now = datetime.now(UTC)
    period_end = now + timedelta(days=365) if billing_cycle == "annual" else now + timedelta(days=30)

    repository.update_billing_subscription(
        sub["id"],
        plan_key=chosen_plan,
        status="active",
        billing_cycle=billing_cycle,
        current_period_start=now.isoformat(),
        current_period_end=period_end.isoformat(),
        cancelled_at=None,
        cancel_reason=None,
        trial_ends_at=None,
    )

    plan = PLANS[chosen_plan]
    price = plan["price_annual_monthly_cents"] if billing_cycle == "annual" else plan["price_monthly_cents"]

    return {
        "subscription_id": int(sub["id"]),
        "status": "active",
        "plan_key": chosen_plan,
        "billing_cycle": billing_cycle,
        "price_monthly": price / 100,
        "current_period_end": period_end.isoformat(),
        "reactivated": True,
    }


def change_plan(company_id: int, new_plan_key: str) -> dict[str, Any]:
    if new_plan_key not in PLANS:
        raise BillingError(f"Invalid plan: {new_plan_key}")

    sub = repository.get_billing_subscription(company_id)
    if not sub:
        raise BillingError("No subscription found.")
    if sub["status"] not in {"active", "trial"}:
        raise BillingError("Can only change plan on active or trial subscriptions.")

    old_plan = sub["plan_key"]
    cycle = sub.get("billing_cycle") or "monthly"
    plan = PLANS[new_plan_key]
    price = plan["price_annual_monthly_cents"] if cycle == "annual" else plan["price_monthly_cents"]

    repository.update_billing_subscription(
        sub["id"],
        plan_key=new_plan_key,
    )

    return {
        "subscription_id": int(sub["id"]),
        "old_plan": old_plan,
        "new_plan": new_plan_key,
        "price_monthly": price / 100,
    }


def _is_subscription_expired(sub: dict[str, Any]) -> bool:
    now = datetime.now(UTC)
    if sub["status"] == "cancelled":
        period_end = sub.get("current_period_end")
        if period_end:
            try:
                return now > datetime.fromisoformat(str(period_end))
            except (ValueError, TypeError):
                return True
        return True
    if sub["status"] == "trial":
        trial_ends = sub.get("trial_ends_at")
        if trial_ends:
            try:
                return now > datetime.fromisoformat(str(trial_ends))
            except (ValueError, TypeError):
                return False
    return False


def record_usage(company_id: int, metric: str, quantity: int = 1) -> dict[str, Any]:
    if metric not in USAGE_METRICS:
        raise BillingError(f"Invalid metric: {metric}")

    sub = repository.get_billing_subscription(company_id)
    if not sub:
        return {"recorded": False, "reason": "no_subscription"}

    if _is_subscription_expired(sub):
        return {"recorded": False, "reason": "subscription_expired"}

    period_start = sub.get("current_period_start") or datetime.now(UTC).isoformat()
    period_end = sub.get("current_period_end") or datetime.now(UTC).isoformat()

    repository.record_billing_usage(
        company_id=company_id,
        metric=metric,
        period_start=period_start,
        period_end=period_end,
        quantity=quantity,
        subscription_id=int(sub["id"]),
    )

    return {"recorded": True, "metric": metric, "quantity": quantity}


def get_usage_dashboard(company_id: int) -> dict[str, Any]:
    sub = repository.get_billing_subscription(company_id)
    if not sub:
        return {
            "has_subscription": False,
            "plan": None,
            "usage": {},
            "limits": {},
            "alerts": [],
        }

    plan_key = sub["plan_key"]
    plan_def = PLANS.get(plan_key)
    if not plan_def:
        plan_def = PLANS["essencial"]

    limits = plan_def["limits"]
    features = plan_def["features"]

    period_start = sub.get("current_period_start") or ""
    period_end = sub.get("current_period_end") or ""

    usage_totals = repository.get_billing_usage_totals(
        company_id=company_id,
        period_start=period_start,
        period_end=period_end,
    )

    usage_details: dict[str, dict[str, Any]] = {}
    alerts: list[dict[str, Any]] = []

    for metric in USAGE_METRICS:
        used = usage_totals.get(metric, 0)
        limit = limits.get(metric, 0)
        pct = round(used / max(limit, 1) * 100, 1)

        usage_details[metric] = {
            "used": used,
            "limit": limit,
            "percentage": min(pct, 100),
            "remaining": max(limit - used, 0),
            "exceeded": used >= limit,
        }

        if limit > 0 and pct >= ALERT_THRESHOLD * 100 and pct < 100:
            alerts.append({
                "metric": metric,
                "type": "warning",
                "message": f"{metric} em {pct}% do limite ({used}/{limit}).",
            })
        elif limit > 0 and used >= limit:
            degraded = metric in DEGRADED_METRICS
            alerts.append({
                "metric": metric,
                "type": "exceeded",
                "degraded": degraded,
                "message": f"{metric} excedeu o limite ({used}/{limit})."
                + (" Funcionalidade degradada." if degraded else " Considere upgrade."),
            })

    now = datetime.now(UTC)
    trial_ends = sub.get("trial_ends_at")
    days_remaining = None
    trial_expired = False
    if sub["status"] == "trial" and trial_ends:
        try:
            trial_dt = datetime.fromisoformat(str(trial_ends))
            days_remaining = max((trial_dt - now).days, 0)
            trial_expired = now > trial_dt
        except (ValueError, TypeError):
            days_remaining = 0
            trial_expired = True

    cycle = sub.get("billing_cycle") or "monthly"
    price = plan_def["price_annual_monthly_cents"] if cycle == "annual" else plan_def["price_monthly_cents"]

    return {
        "has_subscription": True,
        "subscription": {
            "id": int(sub["id"]),
            "status": sub["status"],
            "gateway": sub.get("gateway"),
            "payment_status": sub.get("payment_status"),
            "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
            "grace_period_ends_at": sub.get("grace_period_ends_at"),
            "plan_key": plan_key,
            "plan_name": plan_def["name"],
            "billing_cycle": cycle,
            "price_monthly": price / 100,
            "current_period_start": period_start,
            "current_period_end": period_end,
            "trial_ends_at": trial_ends,
            "trial_days_remaining": days_remaining,
            "trial_expired": trial_expired,
            "expired": _is_subscription_expired(sub),
        },
        "usage": usage_details,
        "limits": limits,
        "features": features,
        "alerts": alerts,
    }


def get_plan_limits_for_company(company_id: int) -> dict[str, int]:
    """Limites efetivos do plano vigente da empresa.

    Sem assinatura, devolve os limites do plano de entrada em vez de um dicionário
    vazio. Um dicionário vazio faria toda capability parecer ausente e tornaria o
    produto inutilizável durante o trial, que é justamente quando a empresa
    precisa conseguir montar o primeiro fluxo.

    Assinatura sem acesso (expirada ou cancelada) devolve zeros: aí a ausência de
    direito é real e precisa aparecer como tal.
    """
    subscription = repository.get_billing_subscription(company_id)
    if not subscription:
        return dict(PLANS["essencial"]["limits"])

    has_access, _reason = _subscription_access(subscription)
    if not has_access:
        return dict.fromkeys(PLANS["essencial"]["limits"], 0)

    plan = PLANS.get(subscription["plan_key"], PLANS["essencial"])
    return dict(plan["limits"])


def check_limit(company_id: int, metric: str) -> dict[str, Any]:
    if metric not in USAGE_METRICS:
        raise BillingError(f"Invalid metric: {metric}")
    sub = repository.get_billing_subscription(company_id)
    if not sub:
        return {
            "allowed": True,
            "reason": "no_subscription",
        }

    has_access, access_reason = _subscription_access(sub)
    if not has_access:
        return {"allowed": False, "reason": access_reason}

    plan_key = sub["plan_key"]
    plan_def = PLANS.get(plan_key, PLANS["essencial"])
    limits = plan_def["limits"]
    limit = limits.get(metric, 0)

    if limit <= 0:
        return {
            "allowed": False,
            "reason": "feature_not_in_plan",
            "limit": limit,
        }

    period_start = sub.get("current_period_start") or ""
    period_end = sub.get("current_period_end") or ""

    usage_totals = repository.get_billing_usage_totals(
        company_id=company_id,
        period_start=period_start,
        period_end=period_end,
    )
    used = usage_totals.get(metric, 0)

    if used >= limit:
        hard_limit = metric in HARD_LIMIT_METRICS
        degraded = metric in DEGRADED_METRICS
        return {
            "allowed": not hard_limit and not degraded,
            "degraded": degraded,
            "hard_limit": hard_limit,
            "reason": "limit_exceeded",
            "used": used,
            "limit": limit,
        }

    return {
        "allowed": True,
        "used": used,
        "limit": limit,
        "remaining": limit - used,
    }
