"""Pacotes de domínio por `business_type`.

`business_type` fornece **presets**, nunca ramificação de comportamento no
código. O comportamento efetivo vem de `capabilities` e `policies` resolvidas
para a empresa, com overrides explícitos por cima do preset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Capacidades reconhecidas. Um valor ausente herda o default do pacote.
CAPABILITY_KEYS: tuple[str, ...] = (
    "scheduling",
    "reminders",
    "appointment_queue",
    "outbound",
    "internal_operation",
    "knowledge_rag",
    "style_examples",
    "conversation_import",
    "finetuning",
)

# Políticas reconhecidas, com o default global do produto.
POLICY_DEFAULTS: dict[str, Any] = {
    # standard | healthcare_strict
    "pii_policy": "standard",
    "handoff_on_low_confidence": True,
    "handoff_confidence_threshold": 0.6,
    # Quando True, afirmação factual sem fonte recuperada vira esclarecimento
    # ou handoff em vez de resposta inventada.
    "require_source_for_factual_claims": True,
    "allow_price_quotes": True,
    "max_knowledge_chunks": 6,
    "training_data_retention_days": 365,
    # Cenários que esta vertical nunca envia para fine-tuning, somados aos
    # cenários excluídos globalmente pela taxonomia.
    "extra_excluded_training_scenarios": [],
}


@dataclass(frozen=True)
class VerticalPack:
    key: str
    label: str
    capabilities: dict[str, bool] = field(default_factory=dict)
    policies: dict[str, Any] = field(default_factory=dict)
    default_profile_name: str = "default"
    priority_scenarios: tuple[str, ...] = ()


_BASE_CAPABILITIES: dict[str, bool] = {
    "scheduling": False,
    "reminders": False,
    "appointment_queue": False,
    "outbound": True,
    "internal_operation": False,
    "knowledge_rag": True,
    "style_examples": True,
    "conversation_import": True,
    "finetuning": False,
}


VERTICAL_PACKS: dict[str, VerticalPack] = {
    "general": VerticalPack(
        key="general",
        label="Serviços gerais",
        capabilities={},
        policies={},
        priority_scenarios=("lead", "qualification", "general"),
    ),
    "services": VerticalPack(
        key="services",
        label="Prestação de serviços com agenda",
        capabilities={
            "scheduling": True,
            "reminders": True,
            "appointment_queue": True,
            "internal_operation": True,
        },
        policies={},
        default_profile_name="consultative",
        priority_scenarios=("lead", "qualification", "scheduling", "closing"),
    ),
    "clinic": VerticalPack(
        key="clinic",
        label="Saúde e clínicas",
        capabilities={
            "scheduling": True,
            "reminders": True,
            "appointment_queue": True,
            "internal_operation": True,
        },
        policies={
            # Dado de saúde exige anonimização mais agressiva e proíbe
            # promover conteúdo clínico para treino.
            "pii_policy": "healthcare_strict",
            "allow_price_quotes": False,
            "require_source_for_factual_claims": True,
            "handoff_confidence_threshold": 0.75,
            "training_data_retention_days": 180,
            "extra_excluded_training_scenarios": ["scheduling"],
        },
        default_profile_name="consultative",
        priority_scenarios=("scheduling", "reminder", "qualification"),
    ),
    "salon": VerticalPack(
        key="salon",
        label="Beleza e estética",
        capabilities={
            "scheduling": True,
            "reminders": True,
            "appointment_queue": True,
        },
        policies={},
        priority_scenarios=("scheduling", "retention", "lead"),
    ),
    "retail": VerticalPack(
        key="retail",
        label="Varejo e comércio",
        capabilities={"scheduling": False, "reminders": False},
        policies={"allow_price_quotes": True},
        default_profile_name="objective",
        priority_scenarios=("lead", "closing", "retention"),
    ),
    "education": VerticalPack(
        key="education",
        label="Educação e cursos",
        capabilities={"scheduling": True, "reminders": True},
        policies={},
        default_profile_name="consultative",
        priority_scenarios=("lead", "qualification", "closing"),
    ),
    "real_estate": VerticalPack(
        key="real_estate",
        label="Imobiliário",
        capabilities={"scheduling": True, "reminders": True, "internal_operation": True},
        policies={"allow_price_quotes": False},
        default_profile_name="consultative",
        priority_scenarios=("lead", "qualification", "scheduling"),
    ),
}

DEFAULT_VERTICAL_PACK = "general"


def resolve_vertical_pack(business_type: str | None) -> VerticalPack:
    key = str(business_type or "").strip().lower().replace("-", "_")
    return VERTICAL_PACKS.get(key, VERTICAL_PACKS[DEFAULT_VERTICAL_PACK])


def build_default_capabilities(business_type: str | None) -> dict[str, bool]:
    pack = resolve_vertical_pack(business_type)
    return {**_BASE_CAPABILITIES, **pack.capabilities}


def build_default_policies(business_type: str | None) -> dict[str, Any]:
    pack = resolve_vertical_pack(business_type)
    return {**POLICY_DEFAULTS, **pack.policies}


def merge_capabilities(business_type: str | None, overrides: dict[str, Any] | None) -> dict[str, bool]:
    """Preset da vertical + overrides explícitos da empresa."""
    resolved = build_default_capabilities(business_type)
    for key, value in (overrides or {}).items():
        if key not in CAPABILITY_KEYS:
            continue
        resolved[key] = bool(value)
    return resolved


def merge_policies(business_type: str | None, overrides: dict[str, Any] | None) -> dict[str, Any]:
    resolved = build_default_policies(business_type)
    for key, value in (overrides or {}).items():
        if key not in POLICY_DEFAULTS:
            continue
        resolved[key] = value
    return resolved


def list_vertical_packs() -> list[dict[str, Any]]:
    return [
        {
            "key": pack.key,
            "label": pack.label,
            "capabilities": build_default_capabilities(pack.key),
            "policies": build_default_policies(pack.key),
            "default_profile_name": pack.default_profile_name,
            "priority_scenarios": list(pack.priority_scenarios),
        }
        for pack in VERTICAL_PACKS.values()
    ]
