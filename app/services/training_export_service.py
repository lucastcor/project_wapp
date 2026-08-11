"""Curadoria e exportação de dataset de treino.

Duas superfícies coexistem:
- a legada, derivada diretamente de `message_examples` aprovados, mantida para
  compatibilidade dos endpoints existentes;
- a governada, derivada de uma `dataset_version` congelada, que é a única
  aceita pelo pipeline de fine-tuning.

Ambas produzem registros no mesmo contrato de prompt do runtime
(`app.core.reply_contract`), para que o modelo treinado veja em produção a
mesma estrutura que viu no treino.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.core.reply_contract import REPLY_SYSTEM_PROMPT, build_reply_user_content
from app.core.taxonomy import (
    DEFAULT_PROFILE_NAME,
    DEFAULT_SCENARIO_TYPE,
    is_excluded_from_training,
    normalize_with_fallback,
)
from app.core.tenancy import require_company_id
from app.core.training_config import (
    DEDUP_NORMALIZE_CASE,
    MAX_EXAMPLES_PER_SCENARIO,
    MIN_QUALITY_SCORE,
    VALIDATION_RATIO,
)
from app.services.dataset_service import DatasetError, _deterministic_rank, get_dataset_version
from app.services.pii_service import POLICY_STANDARD, anonymize_text, contains_pii
from app.storage import dataset_repository
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()

SYSTEM_PROMPT_TEMPLATE = REPLY_SYSTEM_PROMPT
FINE_TUNING_SYSTEM_PROMPT_TEMPLATE = REPLY_SYSTEM_PROMPT
MINIMUM_REQUIRED_FINETUNING_EXAMPLES = 10


def _normalize_text(value: Any, *, policy: str = POLICY_STANDARD) -> str:
    """Normaliza e anonimiza. Nenhum item de dataset sai daqui com PII."""
    sanitized, _ = anonymize_text(str(value or "").strip(), policy=policy)
    return sanitized


def _looks_sensitive(text: str) -> bool:
    """PII residual detectada por valor, não por menção à palavra."""
    return contains_pii(text)


def _dedup_key(example: dict[str, Any]) -> tuple[str, str, str, str]:
    profile_name = _normalize_text(example.get("profile_name") or DEFAULT_PROFILE_NAME)
    scenario_type = _normalize_text(example.get("scenario_type") or DEFAULT_SCENARIO_TYPE)
    customer_message = _normalize_text(example.get("customer_message"))
    approved_reply = _normalize_text(example.get("approved_reply"))

    if DEDUP_NORMALIZE_CASE:
        profile_name = profile_name.lower()
        scenario_type = scenario_type.lower()
        customer_message = customer_message.lower()
        approved_reply = approved_reply.lower()

    return (profile_name, scenario_type, customer_message, approved_reply)


def deduplicate_examples(examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique_examples: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, str]] = set()

    for example in examples:
        key = _dedup_key(example)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_examples.append(example)

    return unique_examples


def limit_examples_per_scenario(
    examples: list[dict[str, Any]],
    max_examples_per_scenario: int = MAX_EXAMPLES_PER_SCENARIO,
) -> list[dict[str, Any]]:
    scenario_counts: dict[str, int] = {}
    limited_examples: list[dict[str, Any]] = []

    for example in examples:
        scenario_type, _ = normalize_with_fallback("scenario_type", example.get("scenario_type"))
        current_count = scenario_counts.get(scenario_type, 0)
        if current_count >= max_examples_per_scenario:
            continue

        limited_examples.append(example)
        scenario_counts[scenario_type] = current_count + 1

    return limited_examples


def is_example_eligible_for_training(example: dict[str, Any]) -> bool:
    """Elegibilidade de um exemplo para o dataset de treino.

    O filtro de cenário usa a taxonomia canônica, então aliases em português
    como `financeiro` e `suporte` também são barrados.
    """
    quality_score = float(example.get("quality_score") or 0)
    customer_message = _normalize_text(example.get("customer_message"))
    approved_reply = _normalize_text(example.get("approved_reply"))

    if quality_score < MIN_QUALITY_SCORE:
        return False

    if is_excluded_from_training(example.get("scenario_type")):
        return False

    if not customer_message or not approved_reply:
        return False

    # Após a anonimização não deve restar PII detectável.
    return not (_looks_sensitive(customer_message) or _looks_sensitive(approved_reply))


def get_curated_training_examples(company_id: int | None = None) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="training curation")
    examples = repository.get_exportable_message_examples(company_id=resolved_company_id)
    eligible_examples = [example for example in examples if is_example_eligible_for_training(example)]
    deduplicated_examples = deduplicate_examples(eligible_examples)
    return limit_examples_per_scenario(deduplicated_examples)


def summarize_curated_training_examples(company_id: int | None = None) -> dict[str, Any]:
    curated_examples = get_curated_training_examples(company_id=company_id)

    scenario_counter = Counter(
        normalize_with_fallback("scenario_type", example.get("scenario_type"))[0]
        for example in curated_examples
    )
    profile_counter = Counter(
        normalize_with_fallback("profile_name", example.get("profile_name"))[0]
        for example in curated_examples
    )
    customer_profile_counter = Counter(
        normalize_with_fallback("customer_profile", example.get("customer_profile"))[0]
        for example in curated_examples
    )

    quality_scores = [float(example.get("quality_score") or 0) for example in curated_examples]
    average_quality_score = (
        round(sum(quality_scores) / len(quality_scores), 4) if quality_scores else 0.0
    )

    return {
        "total_curated_examples": len(curated_examples),
        "average_quality_score": average_quality_score,
        "scenario_distribution": dict(sorted(scenario_counter.items())),
        "profile_distribution": dict(sorted(profile_counter.items())),
        "customer_profile_distribution": dict(sorted(customer_profile_counter.items())),
    }


def summarize_finetuning_split(
    validation_ratio: float = VALIDATION_RATIO,
    seed: int = 42,
    company_id: int | None = None,
) -> dict[str, Any]:
    split_records = get_finetuning_split_records(
        validation_ratio=validation_ratio,
        seed=seed,
        company_id=company_id,
    )

    train_count = len(split_records["train"])
    validation_count = len(split_records["validation"])

    return {
        "train_examples": train_count,
        "validation_examples": validation_count,
        "total_examples": train_count + validation_count,
        "validation_ratio_requested": validation_ratio,
        "seed": seed,
    }


def get_finetuning_readiness_report(company_id: int | None = None) -> dict[str, Any]:
    """Readiness legado, sobre `message_examples`.

    O readiness que bloqueia a criação de job é
    `finetuning_readiness_service.evaluate_finetuning_readiness`, que opera
    sobre uma `dataset_version` congelada.
    """
    curated_examples = get_curated_training_examples(company_id=company_id)
    curated_count = len(curated_examples)
    missing_examples_count = max(0, MINIMUM_REQUIRED_FINETUNING_EXAMPLES - curated_count)

    scenario_counter = Counter(
        normalize_with_fallback("scenario_type", example.get("scenario_type"))[0]
        for example in curated_examples
    )

    underrepresented_scenarios = {
        scenario: count
        for scenario, count in sorted(scenario_counter.items())
        if count < 2
    }

    return {
        "is_ready_for_finetuning": curated_count >= MINIMUM_REQUIRED_FINETUNING_EXAMPLES,
        "eligible_examples_count": curated_count,
        "minimum_required_examples": MINIMUM_REQUIRED_FINETUNING_EXAMPLES,
        "missing_examples_count": missing_examples_count,
        "underrepresented_scenarios": underrepresented_scenarios,
    }


def split_examples(
    examples: list[dict[str, Any]],
    validation_ratio: float = VALIDATION_RATIO,
    seed: int = 42,
) -> dict[str, list[dict[str, Any]]]:
    """Split legado, agrupado por identidade de origem.

    Mesmo no caminho legado o agrupamento é respeitado: itens promovidos da
    mesma conversa compartilham `source_reference`.
    """
    if not examples:
        return {"train": [], "validation": []}

    groups: dict[str, list[dict[str, Any]]] = {}
    for example in examples:
        group_key = str(example.get("source_reference") or f"example:{example.get('id')}")
        groups.setdefault(group_key, []).append(example)

    # Ordenação determinística e estável entre versões do Python, igual ao
    # split governado. Ver `dataset_service._deterministic_rank`.
    group_keys = sorted(groups.keys(), key=lambda key: _deterministic_rank(key, seed))

    validation_group_count = (
        max(1, int(len(group_keys) * validation_ratio)) if len(group_keys) > 1 else 0
    )
    validation_keys = set(group_keys[:validation_group_count])

    train_examples: list[dict[str, Any]] = []
    validation_examples: list[dict[str, Any]] = []
    for group_key in group_keys:
        target = validation_examples if group_key in validation_keys else train_examples
        target.extend(groups[group_key])

    return {"train": train_examples, "validation": validation_examples}


def _build_record_messages(example: dict[str, Any]) -> list[dict[str, str]]:
    scenario_type, _ = normalize_with_fallback("scenario_type", example.get("scenario_type"))
    profile_name, _ = normalize_with_fallback("profile_name", example.get("profile_name"))
    customer_profile = None
    if example.get("customer_profile"):
        customer_profile, _ = normalize_with_fallback(
            "customer_profile", example.get("customer_profile")
        )

    user_content = build_reply_user_content(
        customer_profile=customer_profile,
        profile_name=profile_name,
        scenario_type=scenario_type,
        style_notes=_normalize_text(example.get("style_notes")) or None,
        conversation_context=example.get("context") or None,
        customer_message=_normalize_text(example.get("customer_message")),
    )

    return [
        {"role": "system", "content": REPLY_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": _normalize_text(example.get("approved_reply"))},
    ]


def build_training_record(example: dict[str, Any]) -> dict[str, Any]:
    scenario_type, _ = normalize_with_fallback("scenario_type", example.get("scenario_type"))
    profile_name, _ = normalize_with_fallback("profile_name", example.get("profile_name"))

    return {
        "messages": _build_record_messages(example),
        "metadata": {
            "message_example_id": example.get("id"),
            "profile_name": profile_name,
            "scenario_type": scenario_type,
            "customer_profile": example.get("customer_profile"),
            "quality_score": float(example.get("quality_score") or 0),
            "created_at": example.get("created_at"),
        },
    }


def build_finetuning_record(example: dict[str, Any]) -> dict[str, Any]:
    return {"messages": _build_record_messages(example)}


def get_exportable_training_records(company_id: int | None = None) -> list[dict[str, Any]]:
    curated_examples = get_curated_training_examples(company_id=company_id)
    return [build_training_record(example) for example in curated_examples]


def split_training_records(
    records: list[dict[str, Any]],
    validation_ratio: float = VALIDATION_RATIO,
    seed: int = 42,
) -> dict[str, list[dict[str, Any]]]:
    if not records:
        return {"train": [], "validation": []}

    shuffled = sorted(
        records,
        key=lambda record: _deterministic_rank(
            json.dumps(record, sort_keys=True, ensure_ascii=False), seed
        ),
    )

    validation_size = max(1, int(len(shuffled) * validation_ratio)) if len(shuffled) > 1 else 0
    return {
        "train": shuffled[validation_size:],
        "validation": shuffled[:validation_size],
    }


def export_training_records_as_json(company_id: int | None = None) -> str:
    return json.dumps(get_exportable_training_records(company_id=company_id), ensure_ascii=False, indent=2)


def export_training_records_as_jsonl(company_id: int | None = None) -> str:
    records = get_exportable_training_records(company_id=company_id)
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in records)


def export_training_split_as_json(
    validation_ratio: float = VALIDATION_RATIO,
    company_id: int | None = None,
) -> str:
    records = get_exportable_training_records(company_id=company_id)
    split_records = split_training_records(records, validation_ratio=validation_ratio)
    return json.dumps(split_records, ensure_ascii=False, indent=2)


def get_finetuning_split_records(
    validation_ratio: float = VALIDATION_RATIO,
    seed: int = 42,
    company_id: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    curated_examples = get_curated_training_examples(company_id=company_id)
    split_curated_examples = split_examples(
        curated_examples,
        validation_ratio=validation_ratio,
        seed=seed,
    )

    return {
        "train": [build_finetuning_record(example) for example in split_curated_examples["train"]],
        "validation": [
            build_finetuning_record(example) for example in split_curated_examples["validation"]
        ],
    }


def export_finetuning_train_jsonl(
    validation_ratio: float = VALIDATION_RATIO,
    seed: int = 42,
    company_id: int | None = None,
) -> str:
    split_records = get_finetuning_split_records(
        validation_ratio=validation_ratio,
        seed=seed,
        company_id=company_id,
    )
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in split_records["train"])


def export_finetuning_validation_jsonl(
    validation_ratio: float = VALIDATION_RATIO,
    seed: int = 42,
    company_id: int | None = None,
) -> str:
    split_records = get_finetuning_split_records(
        validation_ratio=validation_ratio,
        seed=seed,
        company_id=company_id,
    )
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in split_records["validation"])


def export_finetuning_bundle_as_json(
    validation_ratio: float = VALIDATION_RATIO,
    seed: int = 42,
    company_id: int | None = None,
) -> str:
    return json.dumps(
        get_finetuning_split_records(
            validation_ratio=validation_ratio,
            seed=seed,
            company_id=company_id,
        ),
        ensure_ascii=False,
        indent=2,
    )


def export_training_review_report_as_json(
    validation_ratio: float = VALIDATION_RATIO,
    seed: int = 42,
    company_id: int | None = None,
) -> str:
    report = {
        "curation_summary": summarize_curated_training_examples(company_id=company_id),
        "split_summary": summarize_finetuning_split(
            validation_ratio=validation_ratio,
            seed=seed,
            company_id=company_id,
        ),
        "readiness_summary": get_finetuning_readiness_report(company_id=company_id),
    }
    return json.dumps(report, ensure_ascii=False, indent=2)


# --- exportação a partir de uma dataset_version congelada ---------------------


def build_dataset_version_records(
    *,
    company_id: int,
    dataset_version_id: int,
    split: str,
) -> list[dict[str, Any]]:
    """Registros JSONL derivados de uma versão congelada."""
    resolved_company_id = require_company_id(company_id, context="dataset export")
    version = get_dataset_version(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )
    if version["status"] != dataset_repository.STATUS_FROZEN:
        raise DatasetError(
            f"Dataset version {dataset_version_id} must be frozen before export; "
            f"current status is '{version['status']}'."
        )

    items = dataset_repository.get_items(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
        split=split,
    )
    return [build_finetuning_record(item) for item in items]


def export_dataset_version_jsonl(
    *,
    company_id: int,
    dataset_version_id: int,
    split: str,
) -> str:
    records = build_dataset_version_records(
        company_id=company_id,
        dataset_version_id=dataset_version_id,
        split=split,
    )
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
