import copy

import pytest

from app.core.company_context import CompanyContextError, get_company_context, save_company_context


def test_save_company_context_accepts_conversation_pipelines(tmp_path, monkeypatch) -> None:
    context = get_company_context()
    monkeypatch.setattr("app.core.company_context.COMPANY_CONTEXT_PATH", tmp_path / "company_context.json")
    updated_context = copy.deepcopy(context)
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

    result = save_company_context(updated_context)

    assert result["conversation_pipelines"][0]["key"] == "lead_capture"
    assert result["conversation_pipelines"][1]["next_keys"] == []


def test_save_company_context_rejects_unknown_pipeline_transition(tmp_path, monkeypatch) -> None:
    context = get_company_context()
    monkeypatch.setattr("app.core.company_context.COMPANY_CONTEXT_PATH", tmp_path / "company_context.json")
    updated_context = copy.deepcopy(context)
    updated_context["conversation_pipelines"] = [
        {
            "key": "lead_capture",
            "label": "Entrada",
            "next_keys": ["unknown_stage"],
        }
    ]

    with pytest.raises(CompanyContextError):
        save_company_context(updated_context)
