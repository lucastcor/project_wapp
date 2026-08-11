"""Readiness de fine-tuning, em dois portões distintos.

Antes havia um só, e ele exigia avaliação do modelo fine-tuned para *criar* o
job — um ciclo impossível: o modelo só existe depois do treino.

- `evaluate_pretraining_readiness`: o que precisa ser verdade para **treinar**
  (dataset congelado, volume, qualidade, PII, split, cobertura). Não exige
  avaliação do modelo.
- `evaluate_promotion_readiness`: o que precisa ser verdade para **ativar**
  (job concluído, modelo real, avaliação model-backed aprovada, vínculos
  consistentes entre empresa, dataset, job e modelo).
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.tenancy import require_company_id
from app.services.company_config_service import get_company_config
from app.services.dataset_service import check_split_leakage
from app.services.evaluation_service import get_promotion_gate, list_evaluation_cases
from app.services.pii_service import POLICY_STANDARD, contains_pii
from app.storage import dataset_repository
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


def _check(code: str, passed: bool, detail: str, *, blocking: bool = True) -> dict[str, Any]:
    return {
        "code": code,
        "status": "pass" if passed else "fail",
        "blocking": blocking,
        "detail": detail,
    }


def evaluate_pretraining_readiness(
    *,
    company_id: int,
    dataset_version_id: int,
) -> dict[str, Any]:
    """Readiness para criar o job. Não depende de avaliar o modelo treinado."""
    resolved_company_id = require_company_id(company_id, context="fine-tuning readiness")
    config = get_company_config(resolved_company_id)
    pii_policy = str(config.policy("pii_policy", POLICY_STANDARD))

    checks: list[dict[str, Any]] = []

    version = dataset_repository.get_version(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )
    if not version:
        return {
            "company_id": resolved_company_id,
            "dataset_version_id": dataset_version_id,
            "is_ready": False,
            "checks": [
                _check("dataset_version_exists", False, "Dataset version not found for this company.")
            ],
            "blocking_failures": ["dataset_version_exists"],
        }

    checks.append(_check("dataset_version_exists", True, "Dataset version found."))

    checks.append(
        _check(
            "capability_enabled",
            config.has_capability("finetuning"),
            "Fine-tuning capability must be enabled for this company.",
        )
    )

    items = dataset_repository.get_items(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )
    train_items = [item for item in items if item["split"] == dataset_repository.SPLIT_TRAIN]
    validation_items = [
        item for item in items if item["split"] == dataset_repository.SPLIT_VALIDATION
    ]

    checks.append(
        _check(
            "minimum_examples",
            len(train_items) >= settings.finetuning_min_examples,
            f"Train split has {len(train_items)} items; minimum is {settings.finetuning_min_examples}.",
        )
    )
    checks.append(
        _check(
            "validation_present",
            len(validation_items) >= 1,
            f"Validation split has {len(validation_items)} items; at least 1 is required.",
        )
    )

    scenario_counts: dict[str, int] = {}
    for item in train_items:
        scenario = str(item["scenario_type"])
        scenario_counts[scenario] = scenario_counts.get(scenario, 0) + 1

    covered_scenarios = [
        scenario
        for scenario, count in scenario_counts.items()
        if count >= settings.finetuning_min_examples_per_scenario
    ]
    checks.append(
        _check(
            "scenario_coverage",
            len(covered_scenarios) >= settings.finetuning_min_scenarios,
            (
                f"{len(covered_scenarios)} scenarios reach "
                f"{settings.finetuning_min_examples_per_scenario} examples; "
                f"minimum is {settings.finetuning_min_scenarios}."
            ),
        )
    )

    low_quality = [item for item in items if float(item["quality_score"] or 0) < 8.0]
    checks.append(
        _check(
            "quality_threshold",
            not low_quality,
            f"{len(low_quality)} items are below the minimum quality score.",
        )
    )

    pii_hits = [
        item
        for item in items
        if contains_pii(item["customer_message"], policy=pii_policy)
        or contains_pii(item["approved_reply"], policy=pii_policy)
    ]
    checks.append(
        _check(
            "pii_clean",
            not pii_hits,
            f"{len(pii_hits)} items still contain detectable PII under policy '{pii_policy}'.",
        )
    )

    leakage = check_split_leakage(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )
    checks.append(
        _check(
            "split_integrity",
            not leakage["has_leakage"],
            (
                f"{leakage['leaked_group_count']} groups and "
                f"{leakage['leaked_near_duplicate_count']} near-duplicates cross splits."
            ),
        )
    )

    checks.append(
        _check(
            "dataset_frozen",
            version["status"] == dataset_repository.STATUS_FROZEN,
            f"Dataset version status is '{version['status']}'; it must be frozen.",
        )
    )

    # A suíte de avaliação precisa existir antes do treino, mas o resultado do
    # modelo treinado só pode ser exigido depois que ele existir.
    suite_size = len(list_evaluation_cases(company_id=resolved_company_id))
    checks.append(
        _check(
            "evaluation_suite_registered",
            suite_size > 0,
            f"{suite_size} caso(s) de avaliação registrado(s).",
        )
    )

    blocking_failures = [
        check["code"] for check in checks if check["status"] == "fail" and check["blocking"]
    ]

    return {
        "company_id": resolved_company_id,
        "dataset_version_id": dataset_version_id,
        "gate": "pretraining",
        "is_ready": not blocking_failures,
        "checks": checks,
        "blocking_failures": blocking_failures,
        "train_items": len(train_items),
        "validation_items": len(validation_items),
        "scenario_distribution": dict(sorted(scenario_counts.items())),
    }


def evaluate_promotion_readiness(
    *,
    company_id: int,
    model_ref: str,
    dataset_version_id: int | None = None,
    finetuning_job_id: str | None = None,
) -> dict[str, Any]:
    """Readiness para ativar um modelo já treinado."""
    resolved_company_id = require_company_id(company_id, context="promotion readiness")
    checks: list[dict[str, Any]] = []

    job_record = (
        repository.get_finetuning_job_record(finetuning_job_id, company_id=resolved_company_id)
        if finetuning_job_id
        else None
    )
    if finetuning_job_id:
        checks.append(
            _check(
                "job_belongs_to_company",
                job_record is not None,
                f"Job {finetuning_job_id} must belong to company {resolved_company_id}.",
            )
        )
        job_status = str((job_record or {}).get("status") or "")
        checks.append(
            _check(
                "job_succeeded",
                job_status == "succeeded",
                f"Fine-tuning job status is '{job_status or 'unknown'}'; it must be 'succeeded'.",
            )
        )
        real_model = str((job_record or {}).get("fine_tuned_model") or "")
        checks.append(
            _check(
                "model_matches_job",
                bool(real_model) and real_model == model_ref,
                "Model reference must be the fine_tuned_model produced by the job.",
            )
        )
        if dataset_version_id is not None and job_record is not None:
            checks.append(
                _check(
                    "job_dataset_matches",
                    job_record.get("dataset_version_id") is not None
                    and int(job_record["dataset_version_id"]) == int(dataset_version_id),
                    "Job must have been created from the same dataset version.",
                )
            )
    else:
        checks.append(
            _check(
                "job_reference_present",
                False,
                "A completed fine-tuning job reference is required to promote a model.",
            )
        )

    checks.append(
        _check(
            "model_ref_is_not_placeholder",
            not str(model_ref or "").startswith("pending:"),
            "A pending placeholder model reference cannot be activated.",
        )
    )

    gate = get_promotion_gate(
        company_id=resolved_company_id,
        min_pass_rate=settings.finetuning_min_eval_pass_rate,
        model_ref=model_ref,
        dataset_version_id=dataset_version_id,
        finetuning_job_id=finetuning_job_id,
    )
    checks.append(
        _check(
            "promotion_gate",
            bool(gate["passed"]),
            f"Promotion gate reasons: {', '.join(gate['reasons']) or 'none'}.",
        )
    )

    blocking_failures = [
        check["code"] for check in checks if check["status"] == "fail" and check["blocking"]
    ]
    return {
        "company_id": resolved_company_id,
        "gate": "promotion",
        "model_ref": model_ref,
        "dataset_version_id": dataset_version_id,
        "finetuning_job_id": finetuning_job_id,
        "is_ready": not blocking_failures,
        "checks": checks,
        "blocking_failures": blocking_failures,
        "promotion_gate": gate,
    }


def evaluate_finetuning_readiness(
    *,
    company_id: int,
    dataset_version_id: int,
    require_evaluation: bool = True,
) -> dict[str, Any]:
    """Compatibilidade com o contrato anterior.

    `require_evaluation` deixou de existir como conceito: a avaliação do modelo
    treinado pertence ao portão de promoção, não ao de treino. O parâmetro é
    aceito e ignorado para não quebrar chamadores.
    """
    return evaluate_pretraining_readiness(
        company_id=company_id,
        dataset_version_id=dataset_version_id,
    )
