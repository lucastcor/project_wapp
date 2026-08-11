import io
import logging
import time
from typing import Any

from openai import APIError, OpenAI

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.tenancy import require_company_id
from app.services.dataset_service import DatasetError, get_dataset_version
from app.services.finetuning_readiness_service import evaluate_pretraining_readiness
from app.services.model_registry_service import register_candidate_model, sync_model_from_job
from app.services.training_export_service import export_dataset_version_jsonl
from app.storage import dataset_repository
from app.storage.repository import RuntimeRepository


class FineTuningError(Exception):
    pass


TRANSIENT_PROVIDER_ERROR_CODES = {"server_error", "internal_error"}
MAX_FINETUNING_JOB_RETRIES = 2
RETRY_DELAY_SECONDS = 3
repository = RuntimeRepository()
logger = get_logger(__name__)


class FineTuningService:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise FineTuningError("OPENAI_API_KEY is not configured.")

        self.client = OpenAI(api_key=settings.openai_api_key)
        self.base_model = settings.openai_finetuning_base_model
        self.suffix = settings.openai_finetuning_suffix

    def _extract_provider_error_details(self, exc: Exception) -> tuple[str | None, str]:
        error_code = None
        error_message = str(exc)

        response = getattr(exc, "response", None)
        if response is not None:
            try:
                payload = response.json()
                error_payload = payload.get("error", {}) if isinstance(payload, dict) else {}
                error_code = error_payload.get("code")
                error_message = error_payload.get("message") or error_message
            except Exception as parse_exc:
                logger.debug(
                    "finetuning.provider_error_payload_unavailable",
                    exc_info=parse_exc,
                )

        return error_code, error_message

    def _is_transient_provider_error(self, exc: Exception) -> bool:
        error_code, error_message = self._extract_provider_error_details(exc)
        message_lower = error_message.lower()

        if error_code in TRANSIENT_PROVIDER_ERROR_CODES:
            return True

        transient_markers = [
            "trouble accessing your files",
            "please try again later",
            "temporarily unavailable",
            "internal server error",
        ]
        return any(marker in message_lower for marker in transient_markers)

    def _upload_jsonl_content(self, content: str, filename: str) -> str:
        if not content.strip():
            raise FineTuningError(f"The file {filename} is empty.")

        file_buffer = io.BytesIO(content.encode("utf-8"))
        file_buffer.name = filename

        uploaded_file = self.client.files.create(
            file=file_buffer,
            purpose="fine-tune",
        )
        return uploaded_file.id

    def _verify_uploaded_file(self, file_id: str) -> dict[str, Any]:
        uploaded_file = self.client.files.retrieve(file_id)
        return {
            "id": uploaded_file.id,
            "purpose": getattr(uploaded_file, "purpose", None),
            "status": getattr(uploaded_file, "status", None),
            "bytes": getattr(uploaded_file, "bytes", None),
            "filename": getattr(uploaded_file, "filename", None),
        }

    def _require_company_id(self, company_id: int | None) -> int:
        try:
            return require_company_id(company_id, context="fine-tuning operations")
        except Exception as exc:
            raise FineTuningError("company_id must be provided for fine-tuning operations.") from exc

    def preflight_finetuning_job(
        self,
        *,
        company_id: int | None,
        dataset_version_id: int,
        require_evaluation: bool = True,
    ) -> dict[str, Any]:
        """Readiness de pré-treino. Nenhum arquivo é enviado aqui.

        `require_evaluation` é aceito por compatibilidade e ignorado: avaliar o
        modelo treinado pertence ao portão de promoção.
        """
        resolved_company_id = self._require_company_id(company_id)
        return evaluate_pretraining_readiness(
            company_id=resolved_company_id,
            dataset_version_id=dataset_version_id,
        )

    def create_finetuning_job(
        self,
        *,
        company_id: int | None,
        dataset_version_id: int,
        require_evaluation: bool = True,
        requested_by: str | None = None,
    ) -> dict[str, Any]:
        """Cria o job a partir de uma dataset_version congelada.

        O readiness bloqueia: com qualquer falha bloqueante, nenhum arquivo é
        enviado e nenhum job é criado.
        """
        if not settings.enable_finetuning_pipeline:
            raise FineTuningError(
                "Fine-tuning pipeline is disabled. Set ENABLE_FINETUNING_PIPELINE=true to enable it."
            )

        resolved_company_id = self._require_company_id(company_id)

        try:
            version = get_dataset_version(
                company_id=resolved_company_id,
                dataset_version_id=dataset_version_id,
            )
        except DatasetError as exc:
            raise FineTuningError(str(exc)) from exc

        if version["status"] != dataset_repository.STATUS_FROZEN:
            raise FineTuningError(
                f"Dataset version {dataset_version_id} must be frozen before creating a job; "
                f"current status is '{version['status']}'."
            )

        readiness = evaluate_pretraining_readiness(
            company_id=resolved_company_id,
            dataset_version_id=dataset_version_id,
        )
        if not readiness["is_ready"]:
            log_event(
                logger,
                logging.WARNING,
                "finetuning.job.creation.blocked",
                company_id=resolved_company_id,
                dataset_version_id=dataset_version_id,
                blocking_failures=readiness["blocking_failures"],
            )
            raise FineTuningError(
                "Fine-tuning readiness failed. Blocking checks: "
                + ", ".join(readiness["blocking_failures"])
            )

        log_event(
            logger,
            logging.INFO,
            "finetuning.job.creation.started",
            company_id=resolved_company_id,
            dataset_version_id=dataset_version_id,
            base_model=self.base_model,
            suffix=self.suffix,
        )
        train_jsonl = export_dataset_version_jsonl(
            company_id=resolved_company_id,
            dataset_version_id=dataset_version_id,
            split=dataset_repository.SPLIT_TRAIN,
        )
        validation_jsonl = export_dataset_version_jsonl(
            company_id=resolved_company_id,
            dataset_version_id=dataset_version_id,
            split=dataset_repository.SPLIT_VALIDATION,
        )

        training_file_id = self._upload_jsonl_content(
            content=train_jsonl,
            filename="finetuning-train.jsonl",
        )
        validation_file_id = self._upload_jsonl_content(
            content=validation_jsonl,
            filename="finetuning-validation.jsonl",
        )

        training_file_info = self._verify_uploaded_file(training_file_id)
        validation_file_info = self._verify_uploaded_file(validation_file_id)

        last_error_code: str | None = None
        last_error_message: str | None = None

        for attempt in range(1, MAX_FINETUNING_JOB_RETRIES + 1):
            try:
                job = self.client.fine_tuning.jobs.create(
                    training_file=training_file_id,
                    validation_file=validation_file_id,
                    model=self.base_model,
                    suffix=self.suffix,
                )
                repository.create_finetuning_job_record(
                    job_id=job.id,
                    company_id=resolved_company_id,
                    status=job.status,
                    base_model=self.base_model,
                    suffix=self.suffix,
                    training_file_id=training_file_id,
                    validation_file_id=validation_file_id,
                    dataset_version_id=dataset_version_id,
                    readiness=readiness,
                    requested_by=requested_by,
                )
                register_candidate_model(
                    company_id=resolved_company_id,
                    model_ref=f"pending:{job.id}",
                    base_model=self.base_model,
                    dataset_version_id=dataset_version_id,
                    finetuning_job_id=job.id,
                    notes="Registered on job creation; model_ref is replaced when training completes.",
                    created_by=requested_by,
                )
                log_event(
                    logger,
                    logging.INFO,
                    "finetuning.job.creation.completed",
                    company_id=resolved_company_id,
                    job_id=job.id,
                    status=job.status,
                    attempts_used=attempt,
                )
                return {
                    "job_id": job.id,
                    "status": job.status,
                    "base_model": self.base_model,
                    "suffix": self.suffix,
                    "dataset_version_id": dataset_version_id,
                    "readiness": readiness,
                    "training_file_id": training_file_id,
                    "validation_file_id": validation_file_id,
                    "training_file_info": training_file_info,
                    "validation_file_info": validation_file_info,
                    "attempts_used": attempt,
                }
            except APIError as exc:
                last_error_code, last_error_message = self._extract_provider_error_details(exc)
                log_event(
                    logger,
                    logging.WARNING,
                    "finetuning.job.creation.retryable_error",
                    company_id=resolved_company_id,
                    attempt=attempt,
                    provider_error_code=last_error_code,
                    provider_error_message=last_error_message,
                )
                if attempt < MAX_FINETUNING_JOB_RETRIES and self._is_transient_provider_error(exc):
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                raise FineTuningError(
                    "Failed to create fine-tuning job. "
                    f"provider_error_code={last_error_code or 'unknown'}; "
                    f"provider_error_message={last_error_message or str(exc)}"
                ) from exc
            except Exception as exc:
                raise FineTuningError(f"Failed to create fine-tuning job: {exc}") from exc

        raise FineTuningError(
            "Failed to create fine-tuning job after retries. "
            f"provider_error_code={last_error_code or 'unknown'}; "
            f"provider_error_message={last_error_message or 'unknown'}"
        )

    def retrieve_finetuning_job(self, job_id: str, *, company_id: int | None = None) -> dict[str, Any]:
        if not job_id.strip():
            raise FineTuningError("job_id must be a non-empty string.")

        try:
            job = self.client.fine_tuning.jobs.retrieve(job_id)
        except APIError as exc:
            error_code, error_message = self._extract_provider_error_details(exc)
            raise FineTuningError(
                "Failed to retrieve fine-tuning job. "
                f"provider_error_code={error_code or 'unknown'}; "
                f"provider_error_message={error_message}"
            ) from exc
        except Exception as exc:
            raise FineTuningError(f"Failed to retrieve fine-tuning job: {exc}") from exc

        error_payload = getattr(job, "error", None)
        provider_error_code = None
        provider_error_message = None
        if error_payload:
            provider_error_code = getattr(error_payload, "code", None)
            provider_error_message = getattr(error_payload, "message", None)

        finished_at_value = getattr(job, "finished_at", None)
        finished_at_str = str(finished_at_value) if finished_at_value is not None else None

        resolved_company_id: int | None
        if company_id is not None:
            resolved_company_id = self._require_company_id(company_id)
            existing_record = repository.get_finetuning_job_record(
                job.id,
                company_id=resolved_company_id,
            )
            if existing_record is None:
                raise FineTuningError(
                    f"Fine-tuning job {job.id} is not associated with company_id={resolved_company_id}."
                )
            record_company_id = existing_record.get("company_id")
            if record_company_id is None or int(record_company_id) != resolved_company_id:
                raise FineTuningError(
                    f"Fine-tuning job {job.id} is not associated with company_id={resolved_company_id}."
                )
        else:
            existing_record = repository.get_finetuning_job_record(job.id)
            resolved_company_id = (
                int(existing_record["company_id"])
                if existing_record and existing_record.get("company_id") is not None
                else None
            )

        if existing_record is None:
            repository.create_finetuning_job_record(
                job_id=job.id,
                company_id=resolved_company_id,
                status=job.status,
                base_model=getattr(job, "model", None),
                training_file_id=getattr(job, "training_file", None),
                validation_file_id=getattr(job, "validation_file", None),
                fine_tuned_model=getattr(job, "fine_tuned_model", None),
                provider_error_code=provider_error_code,
                provider_error_message=provider_error_message,
            )
        else:
            repository.update_finetuning_job_record(
                job_id=job.id,
                status=job.status,
                fine_tuned_model=getattr(job, "fine_tuned_model", None),
                provider_error_code=provider_error_code,
                provider_error_message=provider_error_message,
                finished_at=finished_at_str,
            )

        if resolved_company_id is not None:
            # O desfecho do job precisa refletir no candidato de modelo: sucesso
            # troca o placeholder pelo modelo real, falha aposenta o candidato.
            sync_model_from_job(
                company_id=resolved_company_id,
                finetuning_job_id=job.id,
                job_status=str(job.status),
                fine_tuned_model=getattr(job, "fine_tuned_model", None),
                failure_reason=provider_error_message,
            )

        serialized_error = None
        if error_payload:
            serialized_error = {
                "code": provider_error_code,
                "message": provider_error_message,
                "param": getattr(error_payload, "param", None),
            }

        result = {
            "job_id": job.id,
            "status": job.status,
            "base_model": getattr(job, "model", None),
            "fine_tuned_model": getattr(job, "fine_tuned_model", None),
            "training_file": getattr(job, "training_file", None),
            "validation_file": getattr(job, "validation_file", None),
            "created_at": getattr(job, "created_at", None),
            "finished_at": getattr(job, "finished_at", None),
            "trained_tokens": getattr(job, "trained_tokens", None),
            "error": serialized_error,
        }
        log_event(
            logger,
            logging.INFO,
            "finetuning.job.retrieved",
            job_id=job.id,
            status=job.status,
            fine_tuned_model=getattr(job, "fine_tuned_model", None),
        )
        return result

    def list_finetuning_jobs(
        self,
        limit: int = 10,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            raise FineTuningError("limit must be greater than zero.")

        try:
            jobs = self.client.fine_tuning.jobs.list(limit=limit)
        except APIError as exc:
            error_code, error_message = self._extract_provider_error_details(exc)
            raise FineTuningError(
                "Failed to list fine-tuning jobs. "
                f"provider_error_code={error_code or 'unknown'}; "
                f"provider_error_message={error_message}"
            ) from exc
        except Exception as exc:
            raise FineTuningError(f"Failed to list fine-tuning jobs: {exc}") from exc

        resolved_company_id = self._require_company_id(company_id) if company_id is not None else None
        results: list[dict[str, Any]] = []
        for job in jobs.data:
            error_payload = getattr(job, "error", None)
            provider_error_code = None
            provider_error_message = None
            if error_payload:
                provider_error_code = getattr(error_payload, "code", None)
                provider_error_message = getattr(error_payload, "message", None)

            finished_at_value = getattr(job, "finished_at", None)
            finished_at_str = str(finished_at_value) if finished_at_value is not None else None

            existing_record = repository.get_finetuning_job_record(
                job.id,
                company_id=resolved_company_id,
            )
            record_company_id = (
                int(existing_record["company_id"])
                if existing_record and existing_record.get("company_id") is not None
                else None
            )
            if resolved_company_id is not None and record_company_id != resolved_company_id:
                continue

            if existing_record is None:
                repository.create_finetuning_job_record(
                    job_id=job.id,
                    company_id=resolved_company_id,
                    status=job.status,
                    base_model=getattr(job, "model", None),
                    training_file_id=getattr(job, "training_file", None),
                    validation_file_id=getattr(job, "validation_file", None),
                    fine_tuned_model=getattr(job, "fine_tuned_model", None),
                    provider_error_code=provider_error_code,
                    provider_error_message=provider_error_message,
                )
            else:
                repository.update_finetuning_job_record(
                    job_id=job.id,
                    status=job.status,
                    fine_tuned_model=getattr(job, "fine_tuned_model", None),
                    provider_error_code=provider_error_code,
                    provider_error_message=provider_error_message,
                    finished_at=finished_at_str,
                )
            results.append(
                {
                    "job_id": job.id,
                    "status": job.status,
                    "base_model": getattr(job, "model", None),
                    "fine_tuned_model": getattr(job, "fine_tuned_model", None),
                    "created_at": getattr(job, "created_at", None),
                    "finished_at": getattr(job, "finished_at", None),
                }
            )

        log_event(
            logger,
            logging.INFO,
            "finetuning.jobs.listed",
            limit=limit,
            results_count=len(results),
        )
        return results
