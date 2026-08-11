"""Taxonomia canônica do produto.

Fonte única para `classification`, `scenario_type`, `customer_profile` e
`profile_name`. Antes desta camada os mesmos conceitos apareciam com nomes
divergentes em português e inglês espalhados pelo código, o que quebrava
filtros de dataset e seleção de exemplos de estilo.

Regras:
- na borda HTTP, valor desconhecido é rejeitado (`normalize_or_raise`);
- na importação de base histórica, valor desconhecido cai em um fallback
  documentado e é reportado (`normalize_with_fallback`).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any


class TaxonomyError(ValueError):
    """Valor fora da taxonomia canônica."""


def _fold(value: str | None) -> str:
    """Normaliza para comparação: minúsculas, sem acento, separador `_`."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return "_".join(part for part in stripped.replace("-", " ").replace("_", " ").split())


# --- classification -----------------------------------------------------------

CLASSIFICATIONS: tuple[str, ...] = (
    "lead_novo",
    "cliente_existente",
    "suporte",
    "financeiro",
    "fornecedor",
    "agendamento",
    "lembrete",
    "workflow",
    "spam",
    "indefinido",
)
DEFAULT_CLASSIFICATION = "indefinido"

CLASSIFICATION_ALIASES: dict[str, str] = {
    "lead": "lead_novo",
    "novo_lead": "lead_novo",
    "new_lead": "lead_novo",
    "novo_cliente": "lead_novo",
    "prospect": "lead_novo",
    "comercial": "lead_novo",
    "cliente": "cliente_existente",
    "existing_customer": "cliente_existente",
    "returning_customer": "cliente_existente",
    "pos_venda": "cliente_existente",
    "support": "suporte",
    "atendimento": "suporte",
    "financial": "financeiro",
    "finance": "financeiro",
    "cobranca": "financeiro",
    "pagamento": "financeiro",
    "supplier": "fornecedor",
    "vendor": "fornecedor",
    "scheduling": "agendamento",
    "schedule": "agendamento",
    "booking": "agendamento",
    "reschedule": "agendamento",
    "reagendamento": "agendamento",
    "remarcacao": "agendamento",
    "cancelamento": "agendamento",
    "reminder": "lembrete",
    "flow": "workflow",
    "fluxo": "workflow",
    "undefined": "indefinido",
    "unknown": "indefinido",
    "desconhecido": "indefinido",
    "outro": "indefinido",
    "other": "indefinido",
}


# --- scenario_type ------------------------------------------------------------

SCENARIO_TYPES: tuple[str, ...] = (
    "lead",
    "qualification",
    "closing",
    "support",
    "financial",
    "scheduling",
    "reminder",
    "retention",
    "general",
)
DEFAULT_SCENARIO_TYPE = "general"

SCENARIO_TYPE_ALIASES: dict[str, str] = {
    "lead_novo": "lead",
    "novo_lead": "lead",
    "new_lead": "lead",
    "prospeccao": "lead",
    "primeiro_contato": "lead",
    "qualificacao": "qualification",
    "qualificando": "qualification",
    "descoberta": "qualification",
    "fechamento": "closing",
    "venda": "closing",
    "proposta": "closing",
    "negociacao": "closing",
    "suporte": "support",
    "atendimento": "support",
    "problema": "support",
    "financeiro": "financial",
    "cobranca": "financial",
    "pagamento": "financial",
    "boleto": "financial",
    "agendamento": "scheduling",
    "agenda": "scheduling",
    "reagendamento": "scheduling",
    "reschedule": "scheduling",
    "remarcacao": "scheduling",
    "cancelamento": "scheduling",
    "booking": "scheduling",
    "lembrete": "reminder",
    "followup": "reminder",
    "follow_up": "reminder",
    "retorno": "retention",
    "recompra": "retention",
    "reativacao": "retention",
    "fidelizacao": "retention",
    "geral": "general",
    "outro": "general",
    "other": "general",
}

# Cenários que nunca entram em dataset de fine-tuning: envolvem fato mutável
# (valor, cobrança) ou incidente sensível, que devem vir de RAG/serviço.
EXCLUDED_TRAINING_SCENARIOS: frozenset[str] = frozenset({"financial", "support"})


# --- customer_profile ---------------------------------------------------------

CUSTOMER_PROFILES: tuple[str, ...] = (
    "new_customer",
    "returning_customer",
    "vip",
    "inactive",
    "partner",
    "unknown",
)
DEFAULT_CUSTOMER_PROFILE = "unknown"

CUSTOMER_PROFILE_ALIASES: dict[str, str] = {
    "novo": "new_customer",
    "novo_cliente": "new_customer",
    "cliente_novo": "new_customer",
    "lead": "new_customer",
    "lead_novo": "new_customer",
    "new": "new_customer",
    "retorno": "returning_customer",
    "recorrente": "returning_customer",
    "cliente_recorrente": "returning_customer",
    "cliente_existente": "returning_customer",
    "returning": "returning_customer",
    "fiel": "returning_customer",
    "premium": "vip",
    "especial": "vip",
    "inativo": "inactive",
    "churn": "inactive",
    "parceiro": "partner",
    "fornecedor": "partner",
    "desconhecido": "unknown",
    "indefinido": "unknown",
    "": "unknown",
}


# --- profile_name (perfil de atendimento / estilo) ----------------------------

PROFILE_NAMES: tuple[str, ...] = (
    "default",
    "consultative",
    "technical",
    "premium",
    "objective",
)
DEFAULT_PROFILE_NAME = "default"

PROFILE_NAME_ALIASES: dict[str, str] = {
    "padrao": "default",
    "standard": "default",
    "generico": "default",
    "consultivo": "consultative",
    "consultative_sales": "consultative",
    "tecnico": "technical",
    "suporte_tecnico": "technical",
    "premium": "premium",
    "vip": "premium",
    "objetivo": "objective",
    "direto": "objective",
}


@dataclass(frozen=True)
class TaxonomyDimension:
    name: str
    canonical_values: tuple[str, ...]
    aliases: dict[str, str]
    default_value: str


DIMENSIONS: dict[str, TaxonomyDimension] = {
    "classification": TaxonomyDimension(
        "classification", CLASSIFICATIONS, CLASSIFICATION_ALIASES, DEFAULT_CLASSIFICATION
    ),
    "scenario_type": TaxonomyDimension(
        "scenario_type", SCENARIO_TYPES, SCENARIO_TYPE_ALIASES, DEFAULT_SCENARIO_TYPE
    ),
    "customer_profile": TaxonomyDimension(
        "customer_profile", CUSTOMER_PROFILES, CUSTOMER_PROFILE_ALIASES, DEFAULT_CUSTOMER_PROFILE
    ),
    "profile_name": TaxonomyDimension(
        "profile_name", PROFILE_NAMES, PROFILE_NAME_ALIASES, DEFAULT_PROFILE_NAME
    ),
}


def _resolve(dimension_name: str, value: str | None) -> str | None:
    dimension = DIMENSIONS[dimension_name]
    folded = _fold(value)
    if not folded:
        return None
    if folded in dimension.canonical_values:
        return folded
    return dimension.aliases.get(folded)


def normalize(dimension_name: str, value: str | None) -> str | None:
    """Valor canônico, ou None quando não reconhecido."""
    return _resolve(dimension_name, value)


def normalize_or_raise(dimension_name: str, value: str | None) -> str:
    """Valor canônico. Rejeita desconhecidos. Use na borda HTTP."""
    resolved = _resolve(dimension_name, value)
    if resolved is None:
        dimension = DIMENSIONS[dimension_name]
        allowed = ", ".join(dimension.canonical_values)
        raise TaxonomyError(
            f"Unknown {dimension_name}: {value!r}. Allowed canonical values: {allowed}."
        )
    return resolved


def normalize_with_fallback(dimension_name: str, value: str | None) -> tuple[str, bool]:
    """Valor canônico e se houve queda para o default documentado.

    Use na importação de base histórica, onde rejeitar a linha inteira
    perderia dado útil.
    """
    resolved = _resolve(dimension_name, value)
    if resolved is not None:
        return resolved, False
    return DIMENSIONS[dimension_name].default_value, True


# --- derivações ---------------------------------------------------------------

_CLASSIFICATION_TO_SCENARIO: dict[str, str] = {
    "lead_novo": "lead",
    "cliente_existente": "retention",
    "suporte": "support",
    "financeiro": "financial",
    "agendamento": "scheduling",
    "lembrete": "reminder",
    "fornecedor": "general",
    "workflow": "general",
    "spam": "general",
    "indefinido": "general",
}

_PIPELINE_STAGE_TO_SCENARIO: dict[str, str] = {
    "aguardando_confirmacao": "closing",
    "proposta_em_preparacao": "closing",
    "qualificando": "qualification",
    "convertido": "retention",
}


def scenario_type_for(classification: str | None, pipeline_stage: str | None = None) -> str:
    """Cenário de estilo derivado da classificação e do estágio do funil.

    O estágio comercial só sobrepõe a classificação quando esta é comercial:
    um chamado de suporte continua sendo `support` mesmo com o contato em
    estágio de fechamento.
    """
    normalized_classification = normalize("classification", classification) or DEFAULT_CLASSIFICATION
    scenario = _CLASSIFICATION_TO_SCENARIO.get(normalized_classification, DEFAULT_SCENARIO_TYPE)

    if normalized_classification in {"lead_novo", "cliente_existente", "indefinido", "workflow"}:
        stage_scenario = _PIPELINE_STAGE_TO_SCENARIO.get(_fold(pipeline_stage))
        if stage_scenario:
            return stage_scenario

    return scenario


def is_excluded_from_training(scenario_type: str | None) -> bool:
    normalized, _ = normalize_with_fallback("scenario_type", scenario_type)
    return normalized in EXCLUDED_TRAINING_SCENARIOS


def describe_taxonomy() -> dict[str, dict[str, Any]]:
    """Catálogo publicável para UI e documentação."""
    described: dict[str, dict[str, Any]] = {
        name: {
            "canonical_values": list(dimension.canonical_values),
            "default_value": dimension.default_value,
            "aliases": dict(sorted(dimension.aliases.items())),
        }
        for name, dimension in DIMENSIONS.items()
    }
    described["excluded_training_scenarios"] = {
        "canonical_values": sorted(EXCLUDED_TRAINING_SCENARIOS),
        "default_value": None,
        "aliases": {},
    }
    return described
