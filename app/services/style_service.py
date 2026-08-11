"""Seleção de exemplos de estilo por empresa, perfil do cliente e cenário.

Ordem de fallback explícita e previsível:
1. `customer_profile` + `scenario_type`
2. `customer_profile`
3. `profile_name` + `scenario_type`
4. `scenario_type`
5. qualquer exemplo ativo da empresa

Só exemplos ativos e aprovados entram. `used_for_training` é uma dimensão
separada: um exemplo pode servir de few-shot sem estar liberado para treino.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.taxonomy import (
    DEFAULT_PROFILE_NAME,
    DEFAULT_SCENARIO_TYPE,
    normalize,
    scenario_type_for,
)
from app.core.tenancy import require_company_id
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


@dataclass(frozen=True)
class StyleSelection:
    examples: list[dict[str, Any]]
    strategy: str
    scenario_type: str
    customer_profile: str | None
    profile_name: str


def infer_style_scenario_type(classification: str, pipeline_stage: str | None = None) -> str:
    """Cenário de estilo canônico para a conversa atual."""
    return scenario_type_for(classification, pipeline_stage)


def select_style_examples(
    *,
    company_id: int,
    scenario_type: str | None = None,
    customer_profile: str | None = None,
    profile_name: str | None = None,
    limit: int = 3,
) -> StyleSelection:
    resolved_company_id = require_company_id(company_id, context="style example selection")
    scenario = normalize("scenario_type", scenario_type) or DEFAULT_SCENARIO_TYPE
    profile = normalize("customer_profile", customer_profile)
    style_profile = normalize("profile_name", profile_name) or DEFAULT_PROFILE_NAME

    attempts: list[tuple[str, dict[str, Any]]] = []
    if profile:
        attempts.append(
            ("customer_profile+scenario", {"customer_profile": profile, "scenario_type": scenario})
        )
        attempts.append(("customer_profile", {"customer_profile": profile}))
    attempts.append(
        ("profile_name+scenario", {"profile_name": style_profile, "scenario_type": scenario})
    )
    attempts.append(("scenario", {"scenario_type": scenario}))
    attempts.append(("company_default", {}))

    for strategy, filters in attempts:
        examples = repository.get_relevant_message_examples(
            company_id=resolved_company_id,
            limit=limit,
            **filters,
        )
        if examples:
            return StyleSelection(
                examples=examples,
                strategy=strategy,
                scenario_type=scenario,
                customer_profile=profile,
                profile_name=style_profile,
            )

    return StyleSelection(
        examples=[],
        strategy="none",
        scenario_type=scenario,
        customer_profile=profile,
        profile_name=style_profile,
    )


def get_relevant_style_examples(
    profile_name: str | None = None,
    scenario_type: str | None = None,
    limit: int = 3,
    company_id: int | None = None,
    customer_profile: str | None = None,
) -> list[dict[str, Any]]:
    """Compatibilidade com o contrato anterior de seleção de exemplos."""
    return select_style_examples(
        company_id=require_company_id(company_id, context="style example selection"),
        scenario_type=scenario_type,
        customer_profile=customer_profile,
        profile_name=profile_name,
        limit=limit,
    ).examples


def format_style_examples_for_prompt(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return "- Nenhum exemplo de estilo aprovado cadastrado"

    formatted_examples: list[str] = []
    for example in examples:
        formatted_examples.append(

                f"- Perfil de atendimento: {example.get('profile_name', DEFAULT_PROFILE_NAME)} "
                f"| Perfil do cliente: {example.get('customer_profile') or 'não informado'} "
                f"| Cenário: {example.get('scenario_type', DEFAULT_SCENARIO_TYPE)}\n"
                f"  Cliente: {example.get('customer_message', '')}\n"
                f"  Resposta aprovada: {example.get('approved_reply', '')}\n"
                f"  Notas de estilo: {example.get('style_notes', '')}"

        )

    return "\n".join(formatted_examples)
