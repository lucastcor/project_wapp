"""Seleção, rollout e rollback de modelo por empresa.

Antes desta camada, `OPENAI_FINETUNED_MODEL` e `USE_FINETUNED_MODEL_FOR_REPLIES`
eram globais, o que impedia empresas diferentes usarem modelos diferentes.
Agora a empresa manda; as variáveis globais viram apenas fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.tenancy import require_company_id
from app.storage import model_repository

logger = get_logger(__name__)


class ModelRegistryError(Exception):
    pass


@dataclass(frozen=True)
class ModelSelection:
    model_ref: str
    status: str
    reply_source: str
    company_model_id: int | None


def resolve_reply_model(*, company_id: int) -> ModelSelection:
    """Modelo usado para responder por esta empresa.

    Ordem: modelo ativo da empresa -> fine-tuned global (compatibilidade) ->
    modelo base. Um fine-tune de outra empresa nunca é alcançável daqui.
    """
    resolved_company_id = require_company_id(company_id, context="reply model resolution")

    active_model = model_repository.get_active_model(company_id=resolved_company_id)
    if active_model and str(active_model.get("model_ref") or "").strip():
        return ModelSelection(
            model_ref=str(active_model["model_ref"]),
            status="company_active",
            reply_source="openai_finetuned",
            company_model_id=int(active_model["id"]),
        )

    if settings.use_finetuned_model_for_replies and (settings.openai_finetuned_model or "").strip():
        return ModelSelection(
            model_ref=str(settings.openai_finetuned_model).strip(),
            status="global_finetuned_fallback",
            reply_source="openai_finetuned",
            company_model_id=None,
        )

    return ModelSelection(
        model_ref=settings.openai_model,
        status="base_model",
        reply_source="openai",
        company_model_id=None,
    )


def register_candidate_model(
    *,
    company_id: int,
    model_ref: str,
    base_model: str | None = None,
    dataset_version_id: int | None = None,
    finetuning_job_id: str | None = None,
    evaluation_run_id: int | None = None,
    notes: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="model registration")
    stored = model_repository.register_model(
        company_id=resolved_company_id,
        model_ref=model_ref,
        base_model=base_model,
        dataset_version_id=dataset_version_id,
        finetuning_job_id=finetuning_job_id,
        evaluation_run_id=evaluation_run_id,
        status=model_repository.STATUS_CANDIDATE,
        notes=notes,
        created_by=created_by,
    )
    log_event(
        logger,
        logging.INFO,
        "model.candidate.registered",
        company_id=resolved_company_id,
        model_id=stored.get("id"),
    )
    return stored


def activate_model(
    *,
    company_id: int,
    model_id: int,
    activated_by: str | None,
    require_evaluation: bool = True,
    override_reason: str | None = None,
) -> dict[str, Any]:
    """Promove um modelo a `active`, aposentando o anterior.

    A validação é da cadeia inteira — empresa, job concluído, modelo real,
    dataset e avaliação model-backed —, não apenas de `evaluation_run_id` não
    ser nulo.
    """
    from app.services.finetuning_readiness_service import (
        evaluate_promotion_readiness,
    )

    resolved_company_id = require_company_id(company_id, context="model activation")
    model = model_repository.get_model(company_id=resolved_company_id, model_id=model_id)
    if not model:
        raise ModelRegistryError(
            f"Model {model_id} not found for company_id={resolved_company_id}."
        )

    if not require_evaluation:
        # Bypass não existe na superfície normal: precisa estar habilitado por
        # configuração, ter motivo e gerar auditoria de alta severidade.
        if not settings.allow_promotion_gate_override:
            raise ModelRegistryError(
                "Promotion gate override is disabled. "
                "Set ALLOW_PROMOTION_GATE_OVERRIDE=true to enable emergency activation."
            )
        if not str(override_reason or "").strip():
            raise ModelRegistryError("Emergency activation requires an explicit reason.")
        log_event(
            logger,
            logging.CRITICAL,
            "model.activation.gate_overridden",
            company_id=resolved_company_id,
            model_id=model_id,
            model_ref=model.get("model_ref"),
            override_reason=str(override_reason).strip()[:200],
        )
    else:
        readiness = evaluate_promotion_readiness(
            company_id=resolved_company_id,
            model_ref=str(model.get("model_ref") or ""),
            dataset_version_id=model.get("dataset_version_id"),
            finetuning_job_id=model.get("finetuning_job_id"),
        )
        if not readiness["is_ready"]:
            raise ModelRegistryError(
                "Promotion readiness failed. Blocking checks: "
                + ", ".join(readiness["blocking_failures"])
            )

    activated = model_repository.activate_model(
        company_id=resolved_company_id,
        model_id=model_id,
        activated_by=activated_by,
    )
    if not activated:
        raise ModelRegistryError(f"Failed to activate model {model_id}.")

    log_event(
        logger,
        logging.INFO,
        "model.activated",
        company_id=resolved_company_id,
        model_id=model_id,
        model_status=activated.get("status"),
    )
    return activated


def rollback_active_model(*, company_id: int, actor: str | None) -> dict[str, Any]:
    """Aposenta o modelo ativo e restaura o anterior, ou cai no modelo base."""
    resolved_company_id = require_company_id(company_id, context="model rollback")
    current = model_repository.get_active_model(company_id=resolved_company_id)
    if not current:
        raise ModelRegistryError("There is no active company model to roll back.")

    previous = model_repository.get_previous_active_model(
        company_id=resolved_company_id,
        exclude_model_id=int(current["id"]),
    )

    model_repository.set_model_status(
        company_id=resolved_company_id,
        model_id=int(current["id"]),
        status=model_repository.STATUS_RETIRED,
    )

    restored: dict[str, Any] | None = None
    if previous:
        restored = model_repository.activate_model(
            company_id=resolved_company_id,
            model_id=int(previous["id"]),
            activated_by=actor,
        )

    selection = resolve_reply_model(company_id=resolved_company_id)
    log_event(
        logger,
        logging.WARNING,
        "model.rolled_back",
        company_id=resolved_company_id,
        retired_model_id=int(current["id"]),
        restored_model_id=int(restored["id"]) if restored else None,
        resolved_model_status=selection.status,
    )
    return {
        "company_id": resolved_company_id,
        "retired_model": current,
        "restored_model": restored,
        "effective_model_ref": selection.model_ref,
        "effective_model_status": selection.status,
    }


def list_company_models(*, company_id: int) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="model listing")
    return model_repository.list_models(company_id=resolved_company_id)


def sync_model_from_job(
    *,
    company_id: int,
    finetuning_job_id: str,
    job_status: str,
    fine_tuned_model: str | None,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    """Reflete o desfecho do job no candidato de modelo.

    Sucesso substitui o placeholder `pending:<job>` pelo modelo real.
    Falha ou cancelamento aposenta o candidato — nunca ativa nada.
    """
    resolved_company_id = require_company_id(company_id, context="model job sync")
    candidate = model_repository.get_model_by_job(
        company_id=resolved_company_id,
        finetuning_job_id=finetuning_job_id,
    )
    if candidate is None:
        return None

    normalized_status = str(job_status or "").strip().lower()

    if normalized_status == "succeeded" and str(fine_tuned_model or "").strip():
        updated = model_repository.replace_model_ref(
            company_id=resolved_company_id,
            model_id=int(candidate["id"]),
            model_ref=str(fine_tuned_model).strip(),
            job_status=normalized_status,
        )
        log_event(
            logger,
            logging.INFO,
            "model.candidate.linked_to_trained_model",
            company_id=resolved_company_id,
            model_id=int(candidate["id"]),
            finetuning_job_id=finetuning_job_id,
        )
        return updated

    if normalized_status in {"failed", "cancelled", "canceled"}:
        retired = model_repository.mark_model_failed(
            company_id=resolved_company_id,
            model_id=int(candidate["id"]),
            job_status=normalized_status,
            failure_reason=failure_reason,
        )
        log_event(
            logger,
            logging.WARNING,
            "model.candidate.retired_after_job_failure",
            company_id=resolved_company_id,
            model_id=int(candidate["id"]),
            finetuning_job_id=finetuning_job_id,
            job_status=normalized_status,
        )
        return retired

    return model_repository.set_job_status(
        company_id=resolved_company_id,
        model_id=int(candidate["id"]),
        job_status=normalized_status,
    )
