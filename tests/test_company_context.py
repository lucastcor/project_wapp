"""Validation rules of the company context payload."""

import copy

import pytest

from app.core.company_context import (
    CompanyContextError,
    get_company_context,
    save_company_context,
)

COMPANY_SLUG = "context-validation-tenant"


def test_save_company_context_accepts_conversation_pipelines() -> None:
    updated_context = copy.deepcopy(get_company_context())
    updated_context["conversation_pipelines"] = [
        {
            "key": "lead_capture",
            "label": "Entrada",
            "trigger_terms": ["oi", "interesse"],
            "next_keys": ["appointment_management"],
            "canvas_position": {"x": 120, "y": 80},
        },
        {
            "key": "appointment_management",
            "label": "Agenda",
            "trigger_terms": ["agendar"],
            "next_keys": [],
            "canvas_position": {"x": 420, "y": 80},
        },
    ]

    result = save_company_context(updated_context, company_slug=COMPANY_SLUG)

    assert result["conversation_pipelines"][0]["key"] == "lead_capture"
    assert result["conversation_pipelines"][1]["next_keys"] == []
    assert get_company_context(company_slug=COMPANY_SLUG)["conversation_pipelines"][0]["key"] == "lead_capture"


def test_save_company_context_rejects_unknown_pipeline_transition() -> None:
    updated_context = copy.deepcopy(get_company_context())
    updated_context["conversation_pipelines"] = [
        {
            "key": "lead_capture",
            "label": "Entrada",
            "next_keys": ["unknown_stage"],
        }
    ]

    with pytest.raises(CompanyContextError):
        save_company_context(updated_context, company_slug=COMPANY_SLUG)


def test_save_company_context_rejects_missing_required_keys() -> None:
    with pytest.raises(CompanyContextError):
        save_company_context({"company_name": "Sem chaves"}, company_slug=COMPANY_SLUG)
