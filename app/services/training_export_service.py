import json
from collections import Counter
import random
from typing import Any

from app.core.training_config import (
    DEDUP_NORMALIZE_CASE,
    EXCLUDED_SCENARIO_TYPES,
    MAX_EXAMPLES_PER_SCENARIO,
    MIN_QUALITY_SCORE,
    REDACTION_PATTERNS,
    SENSITIVE_MARKERS,
    VALIDATION_RATIO,
)

from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()


SYSTEM_PROMPT_TEMPLATE = (
    "Você é um atendente comercial da empresa. Responda em português do Brasil, "
    "com tom profissional, cordial, objetivo e natural."
)


FINE_TUNING_SYSTEM_PROMPT_TEMPLATE = SYSTEM_PROMPT_TEMPLATE
MINIMUM_REQUIRED_FINETUNING_EXAMPLES = 10



def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    for pattern, replacement in REDACTION_PATTERNS:
        if pattern in text:
            text = text.replace(pattern, replacement)
    return text



def _looks_sensitive(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in SENSITIVE_MARKERS)


def _dedup_key(example: dict[str, Any]) -> tuple[str, str, str, str]:
    profile_name = _normalize_text(example.get("profile_name") or "default")
    scenario_type = _normalize_text(example.get("scenario_type") or "general")
    customer_message = _normalize_text(example.get("customer_message"))
    approved_reply = _normalize_text(example.get("approved_reply"))

    if DEDUP_NORMALIZE_CASE:
        profile_name = profile_name.lower()
        scenario_type = scenario_type.lower()
        customer_message = customer_message.lower()
        approved_reply = approved_reply.lower()

    return (
        profile_name,
        scenario_type,
        customer_message,
        approved_reply,
    )


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
        scenario_type = str(example.get("scenario_type") or "general").strip().lower()
        current_count = scenario_counts.get(scenario_type, 0)
        if current_count >= max_examples_per_scenario:
            continue

        limited_examples.append(example)
        scenario_counts[scenario_type] = current_count + 1

    return limited_examples


def is_example_eligible_for_training(example: dict[str, Any]) -> bool:
    quality_score = float(example.get("quality_score") or 0)
    scenario_type = str(example.get("scenario_type") or "general").strip().lower()
    customer_message = _normalize_text(example.get("customer_message"))
    approved_reply = _normalize_text(example.get("approved_reply"))

    if quality_score < MIN_QUALITY_SCORE:
        return False

    if scenario_type in EXCLUDED_SCENARIO_TYPES:
        return False

    if not customer_message or not approved_reply:
        return False

    if _looks_sensitive(customer_message) or _looks_sensitive(approved_reply):
        return False

    return True


def get_curated_training_examples(company_id: int | None = None) -> list[dict[str, Any]]:
    examples = repository.get_exportable_message_examples(company_id=company_id)
    eligible_examples = [example for example in examples if is_example_eligible_for_training(example)]
    deduplicated_examples = deduplicate_examples(eligible_examples)
    curated_examples = limit_examples_per_scenario(deduplicated_examples)
    return curated_examples



def summarize_curated_training_examples(company_id: int | None = None) -> dict[str, Any]:
    curated_examples = get_curated_training_examples(company_id=company_id)

    scenario_counter = Counter(
        str(example.get("scenario_type") or "general").strip().lower()
        for example in curated_examples
    )
    profile_counter = Counter(
        str(example.get("profile_name") or "default").strip().lower()
        for example in curated_examples
    )

    quality_scores = [float(example.get("quality_score") or 0) for example in curated_examples]
    average_quality_score = (
        round(sum(quality_scores) / len(quality_scores), 4)
        if quality_scores
        else 0.0
    )

    return {
        "total_curated_examples": len(curated_examples),
        "average_quality_score": average_quality_score,
        "scenario_distribution": dict(sorted(scenario_counter.items())),
        "profile_distribution": dict(sorted(profile_counter.items())),
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
    total_count = train_count + validation_count

    return {
        "train_examples": train_count,
        "validation_examples": validation_count,
        "total_examples": total_count,
        "validation_ratio_requested": validation_ratio,
        "seed": seed,
    }


def get_finetuning_readiness_report(company_id: int | None = None) -> dict[str, Any]:
    curated_examples = get_curated_training_examples(company_id=company_id)
    curated_count = len(curated_examples)
    missing_examples_count = max(0, MINIMUM_REQUIRED_FINETUNING_EXAMPLES - curated_count)

    scenario_counter = Counter(
        str(example.get("scenario_type") or "general").strip().lower()
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
    if not examples:
        return {"train": [], "validation": []}

    shuffled = examples[:]
    random.Random(seed).shuffle(shuffled)

    validation_size = max(1, int(len(shuffled) * validation_ratio)) if len(shuffled) > 1 else 0
    validation_examples = shuffled[:validation_size]
    train_examples = shuffled[validation_size:]

    return {
        "train": train_examples,
        "validation": validation_examples,
    }


def build_training_record(example: dict[str, Any]) -> dict[str, Any]:
    style_notes = _normalize_text(example.get("style_notes"))
    scenario_type = _normalize_text(example.get("scenario_type") or "general") or "general"
    profile_name = _normalize_text(example.get("profile_name") or "default") or "default"
    customer_message = _normalize_text(example.get("customer_message"))
    approved_reply = _normalize_text(example.get("approved_reply"))

    user_content = (
        f"Perfil de atendimento: {profile_name}\n"
        f"Tipo de cenário: {scenario_type}\n"
        f"Observações de estilo: {style_notes}\n"
        f"Mensagem do cliente: {customer_message}"
    ).strip()

    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": approved_reply},
        ],
        "metadata": {
            "message_example_id": example.get("id"),
            "profile_name": profile_name,
            "scenario_type": scenario_type,
            "quality_score": float(example.get("quality_score") or 0),
            "created_at": example.get("created_at"),
        },
    }


def build_finetuning_record(example: dict[str, Any]) -> dict[str, Any]:
    style_notes = _normalize_text(example.get("style_notes"))
    scenario_type = _normalize_text(example.get("scenario_type") or "general") or "general"
    profile_name = _normalize_text(example.get("profile_name") or "default") or "default"
    customer_message = _normalize_text(example.get("customer_message"))
    approved_reply = _normalize_text(example.get("approved_reply"))

    user_content = (
        f"Perfil de atendimento: {profile_name}\n"
        f"Tipo de cenário: {scenario_type}\n"
        f"Observações de estilo: {style_notes}\n"
        f"Mensagem do cliente: {customer_message}"
    ).strip()

    return {
        "messages": [
            {"role": "system", "content": FINE_TUNING_SYSTEM_PROMPT_TEMPLATE},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": approved_reply},
        ]
    }


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

    shuffled = records[:]
    random.Random(seed).shuffle(shuffled)

    validation_size = max(1, int(len(shuffled) * validation_ratio)) if len(shuffled) > 1 else 0
    validation_records = shuffled[:validation_size]
    train_records = shuffled[validation_size:]

    return {
        "train": train_records,
        "validation": validation_records,
    }


def export_training_records_as_json(company_id: int | None = None) -> str:
    records = get_exportable_training_records(company_id=company_id)
    return json.dumps(records, ensure_ascii=False, indent=2)


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
        "validation": [build_finetuning_record(example) for example in split_curated_examples["validation"]],
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
    split_records = get_finetuning_split_records(
        validation_ratio=validation_ratio,
        seed=seed,
        company_id=company_id,
    )
    return json.dumps(split_records, ensure_ascii=False, indent=2)


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