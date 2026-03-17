

from typing import Any

from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()


DEFAULT_PROFILE_NAME = "default"
DEFAULT_SCENARIO_TYPE = "general"


def infer_style_scenario_type(classification: str, pipeline_stage: str | None = None) -> str:
    if classification == "suporte":
        return "support"

    if classification == "financeiro":
        return "financial"

    if pipeline_stage in {"aguardando_confirmacao", "proposta_em_preparacao"}:
        return "closing"

    if pipeline_stage == "qualificando":
        return "qualification"

    if classification == "lead_novo":
        return "lead"

    return DEFAULT_SCENARIO_TYPE


def get_relevant_style_examples(
    profile_name: str | None = None,
    scenario_type: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    profile = profile_name or DEFAULT_PROFILE_NAME
    scenario = scenario_type or DEFAULT_SCENARIO_TYPE

    examples = repository.get_relevant_message_examples(
        profile_name=profile,
        scenario_type=scenario,
        limit=limit,
    )

    if examples:
        return examples

    examples = repository.get_relevant_message_examples(
        profile_name=profile,
        limit=limit,
    )
    if examples:
        return examples

    examples = repository.get_relevant_message_examples(
        scenario_type=scenario,
        limit=limit,
    )
    if examples:
        return examples

    return repository.get_relevant_message_examples(limit=limit)


def format_style_examples_for_prompt(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return "- Nenhum exemplo de estilo aprovado cadastrado"

    formatted_examples: list[str] = []
    for example in examples:
        formatted_examples.append(
            (
                f"- Perfil: {example.get('profile_name', DEFAULT_PROFILE_NAME)} "
                f"| Cenário: {example.get('scenario_type', DEFAULT_SCENARIO_TYPE)}\n"
                f"  Cliente: {example.get('customer_message', '')}\n"
                f"  Resposta aprovada: {example.get('approved_reply', '')}\n"
                f"  Notas de estilo: {example.get('style_notes', '')}"
            )
        )

    return "\n".join(formatted_examples)