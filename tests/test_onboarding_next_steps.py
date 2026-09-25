"""P07 — onboarding curto aponta para as telas que completam a ativação."""

from __future__ import annotations

from app.services.onboarding_service import _build_next_steps

PANEL_VIEWS = {"agenda", "policy", "studio", "channel", "team", "operations"}


def test_next_steps_cover_policy_templates_operators_and_operations() -> None:
    steps = _build_next_steps({"catalog": {"items": [{"name": "Consulta"}]}}, calendar_integration={"integration_id": 1})

    keys = [step["key"] for step in steps]
    assert keys == ["policy", "test", "whatsapp", "templates", "team", "operations"]
    assert {step["key"]: step.get("view") for step in steps} == {
        "policy": "policy",
        "test": "studio",
        "whatsapp": None,
        "templates": "channel",
        "team": "team",
        "operations": "operations",
    }


def test_missing_calendar_and_catalog_come_first_and_views_exist_in_the_panel() -> None:
    steps = _build_next_steps({"catalog": {"items": []}}, calendar_integration=None)

    assert [step["key"] for step in steps][:2] == ["calendar", "catalog"]
    assert all(step["view"] in PANEL_VIEWS for step in steps if step.get("view"))
    assert all(step["title"] and step["description"] for step in steps)
