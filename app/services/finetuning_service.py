import io
import time
from typing import Any

from openai import APIError, OpenAI

from app.core.config import settings
from app.storage.repository import RuntimeRepository
from app.services.training_export_service import (
    export_finetuning_train_jsonl,
    export_finetuning_validation_jsonl,
)


class FineTuningError(Exception):
    pass


TRANSIENT_PROVIDER_ERROR_CODES = {"server_error", "internal_error"}
MAX_FINETUNING_JOB_RETRIES = 2
RETRY_DELAY_SECONDS = 3
repository = RuntimeRepository()


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
            except Exception:
                pass

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

    def create_finetuning_job(self) -> dict[str, Any]:
        if not settings.enable_finetuning_pipeline:
            raise FineTuningError(
                "Fine-tuning pipeline is disabled. Set ENABLE_FINETUNING_PIPELINE=true to enable it."
            )

        train_jsonl = export_finetuning_train_jsonl()
        validation_jsonl = export_finetuning_validation_jsonl()

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
                    status=job.status,
                    base_model=self.base_model,
                    suffix=self.suffix,
                    training_file_id=training_file_id,
                    validation_file_id=validation_file_id,
                )
                return {
                    "job_id": job.id,
                    "status": job.status,
                    "base_model": self.base_model,
                    "suffix": self.suffix,
                    "training_file_id": training_file_id,
                    "validation_file_id": validation_file_id,
                    "training_file_info": training_file_info,
                    "validation_file_info": validation_file_info,
                    "attempts_used": attempt,
                }
            except APIError as exc:
                last_error_code, last_error_message = self._extract_provider_error_details(exc)
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

    def retrieve_finetuning_job(self, job_id: str) -> dict[str, Any]:
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

        existing_record = repository.get_finetuning_job_record(job.id)
        if existing_record is None:
            repository.create_finetuning_job_record(
                job_id=job.id,
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

        serialized_error = None
        if error_payload:
            serialized_error = {
                "code": provider_error_code,
                "message": provider_error_message,
                "param": getattr(error_payload, "param", None),
            }

        return {
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

    def list_finetuning_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
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

            existing_record = repository.get_finetuning_job_record(job.id)
            if existing_record is None:
                repository.create_finetuning_job_record(
                    job_id=job.id,
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

        return results