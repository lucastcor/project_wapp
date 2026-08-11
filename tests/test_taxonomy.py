"""Taxonomia canônica: aliases, rejeição de desconhecidos e derivações."""

import pytest

from app.core.taxonomy import (
    EXCLUDED_TRAINING_SCENARIOS,
    TaxonomyError,
    is_excluded_from_training,
    normalize,
    normalize_or_raise,
    normalize_with_fallback,
    scenario_type_for,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("lead", "lead_novo"),
        ("novo_lead", "lead_novo"),
        ("NOVO LEAD", "lead_novo"),
        ("support", "suporte"),
        ("financial", "financeiro"),
        ("reagendamento", "agendamento"),
        ("unknown", "indefinido"),
    ],
)
def test_classification_aliases_are_normalized(raw: str, expected: str) -> None:
    assert normalize_or_raise("classification", raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("lead_novo", "lead"),
        ("qualificação", "qualification"),
        ("fechamento", "closing"),
        ("suporte", "support"),
        ("financeiro", "financial"),
        ("reschedule", "scheduling"),
        ("retorno", "retention"),
        ("geral", "general"),
    ],
)
def test_scenario_aliases_are_normalized(raw: str, expected: str) -> None:
    assert normalize_or_raise("scenario_type", raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("novo", "new_customer"),
        ("lead_novo", "new_customer"),
        ("retorno", "returning_customer"),
        ("cliente_existente", "returning_customer"),
        ("premium", "vip"),
        ("inativo", "inactive"),
    ],
)
def test_customer_profile_aliases_are_normalized(raw: str, expected: str) -> None:
    assert normalize_or_raise("customer_profile", raw) == expected


def test_unknown_values_are_rejected_at_the_edge() -> None:
    with pytest.raises(TaxonomyError, match="Unknown scenario_type"):
        normalize_or_raise("scenario_type", "coisa-inventada")

    with pytest.raises(TaxonomyError, match="Unknown classification"):
        normalize_or_raise("classification", "xpto")


def test_unknown_values_fall_back_with_a_documented_default() -> None:
    value, used_fallback = normalize_with_fallback("scenario_type", "coisa-inventada")
    assert value == "general"
    assert used_fallback is True

    value, used_fallback = normalize_with_fallback("scenario_type", "reschedule")
    assert value == "scheduling"
    assert used_fallback is False


def test_portuguese_aliases_are_excluded_from_training() -> None:
    # Antes da taxonomia, o filtro só barrava as formas em inglês.
    assert is_excluded_from_training("financeiro") is True
    assert is_excluded_from_training("suporte") is True
    assert is_excluded_from_training("cobranca") is True
    assert is_excluded_from_training("lead") is False
    assert frozenset({"financial", "support"}) == EXCLUDED_TRAINING_SCENARIOS


def test_scenario_derivation_respects_classification_over_stage() -> None:
    # Estágio comercial não transforma um chamado de suporte em fechamento.
    assert scenario_type_for("suporte", "aguardando_confirmacao") == "support"
    assert scenario_type_for("lead_novo", "aguardando_confirmacao") == "closing"
    assert scenario_type_for("lead_novo", None) == "lead"
    assert scenario_type_for("financeiro", "qualificando") == "financial"
    assert scenario_type_for(None, None) == "general"


def test_normalize_returns_none_for_unknown_values() -> None:
    assert normalize("customer_profile", "nao-existe") is None
    assert normalize("customer_profile", None) is None
