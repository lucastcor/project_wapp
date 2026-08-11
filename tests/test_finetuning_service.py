import pytest

from app.core.config import settings
from app.services import finetuning_service
from app.services.finetuning_service import FineTuningError, FineTuningService


class FakeRepository:
    def __init__(self) -> None:
        self.created_records: list[dict] = []
        self.updated_records: list[dict] = []
        self.records_by_job_id: dict[str, dict] = {}
        self.record_lookup_calls: list[dict] = []

    def create_finetuning_job_record(self, **kwargs) -> int:
        self.created_records.append(kwargs)
        self.records_by_job_id[kwargs["job_id"]] = dict(kwargs)
        return len(self.created_records)

    def update_finetuning_job_record(self, **kwargs) -> None:
        self.updated_records.append(kwargs)
        record = self.records_by_job_id.get(kwargs["job_id"], {"job_id": kwargs["job_id"]})
        record.update(kwargs)
        self.records_by_job_id[kwargs["job_id"]] = record

    def get_finetuning_job_record(self, job_id: str, company_id: int | None = None) -> dict | None:
        self.record_lookup_calls.append(
            {
                "job_id": job_id,
                "company_id": company_id,
            }
        )
        record = self.records_by_job_id.get(job_id)
        if record is None:
            return None
        if company_id is not None and record.get("company_id") != company_id:
            return None
        return dict(record)

    def get_finetuning_job_records(self, company_id: int | None = None) -> list[dict]:
        records = [dict(record) for record in self.records_by_job_id.values()]
        if company_id is None:
            return records
        return [record for record in records if record.get("company_id") == company_id]


class FakeUploadedFile:
    def __init__(self, file_id: str, filename: str) -> None:
        self.id = file_id
        self.purpose = "fine-tune"
        self.status = "processed"
        self.bytes = 123
        self.filename = filename


class FakeJob:
    def __init__(self, job_id: str, status: str = "validating_files") -> None:
        self.id = job_id
        self.status = status
        self.model = "gpt-4o-mini-2024-07-18"
        self.fine_tuned_model = None
        self.training_file = "file-train-123"
        self.validation_file = "file-validation-123"
        self.created_at = 1773709705
        self.finished_at = None
        self.trained_tokens = None
        self.error = None


class FakeJobsAPI:
    def __init__(self) -> None:
        self.created_jobs: list[dict] = []

    def create(self, **kwargs):
        self.created_jobs.append(kwargs)
        return FakeJob("ftjob-test-123")

    def retrieve(self, job_id: str):
        job = FakeJob(job_id, status="failed")
        job.finished_at = 1773709777

        class ErrorPayload:
            code = "server_error"
            message = "Temporary provider issue."

        job.error = ErrorPayload()
        return job

    def list(self, limit: int = 10):
        class FakeListResponse:
            data = [FakeJob("ftjob-test-123", status="failed")]

        response = FakeListResponse()
        response.data[0].finished_at = 1773709777

        class ErrorPayload:
            code = "server_error"
            message = "Temporary provider issue."

        response.data[0].error = ErrorPayload()
        return response


class FakeFilesAPI:
    def create(self, file, purpose: str):
        return FakeUploadedFile(
            file_id=f"file-{file.name}",
            filename=file.name,
        )

    def retrieve(self, file_id: str):
        filename = file_id.replace("file-", "")
        return FakeUploadedFile(file_id=file_id, filename=filename)


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.files = FakeFilesAPI()

        class FineTuningNamespace:
            def __init__(self) -> None:
                self.jobs = FakeJobsAPI()

        self.fine_tuning = FineTuningNamespace()


def test_create_finetuning_job_raises_when_pipeline_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_finetuning_pipeline", False)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    service = FineTuningService()

    with pytest.raises(FineTuningError, match="disabled"):
        service.create_finetuning_job(company_id=1, dataset_version_id=1)


def test_create_finetuning_job_requires_company_scope(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_finetuning_pipeline", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(finetuning_service, "OpenAI", lambda api_key: FakeOpenAIClient())

    service = FineTuningService()

    with pytest.raises(FineTuningError, match="company_id must be provided"):
        service.create_finetuning_job(company_id=None, dataset_version_id=1)


def _install_ready_finetuning_stubs(monkeypatch, export_calls, *, is_ready=True, status="frozen"):
    monkeypatch.setattr(
        finetuning_service,
        "get_dataset_version",
        lambda *, company_id, dataset_version_id: {
            "id": dataset_version_id,
            "company_id": company_id,
            "status": status,
        },
    )
    monkeypatch.setattr(
        finetuning_service,
        "evaluate_pretraining_readiness",
        lambda *, company_id, dataset_version_id: {
            "is_ready": is_ready,
            "blocking_failures": [] if is_ready else ["minimum_examples", "evaluation_gate"],
        },
    )
    monkeypatch.setattr(
        finetuning_service,
        "export_dataset_version_jsonl",
        lambda *, company_id, dataset_version_id, split: (
            export_calls.append((split, company_id)) or '{"messages": []}'
        ),
    )
    monkeypatch.setattr(
        finetuning_service,
        "register_candidate_model",
        lambda **kwargs: {"id": 1, **kwargs},
    )


def test_create_finetuning_job_creates_scoped_record(monkeypatch) -> None:
    fake_repository = FakeRepository()
    export_calls: list[tuple[str, int | None]] = []

    monkeypatch.setattr(finetuning_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "enable_finetuning_pipeline", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_finetuning_base_model", "gpt-4o-mini-2024-07-18")
    monkeypatch.setattr(settings, "openai_finetuning_suffix", "project-wapp-style")
    _install_ready_finetuning_stubs(monkeypatch, export_calls)
    monkeypatch.setattr(finetuning_service, "OpenAI", lambda api_key: FakeOpenAIClient())

    service = FineTuningService()
    result = service.create_finetuning_job(company_id=7, dataset_version_id=42)

    assert result["job_id"] == "ftjob-test-123"
    assert result["status"] == "validating_files"
    assert result["dataset_version_id"] == 42
    assert result["training_file_info"]["status"] == "processed"
    assert result["validation_file_info"]["status"] == "processed"
    assert export_calls == [("train", 7), ("validation", 7)]
    assert len(fake_repository.created_records) == 1
    assert fake_repository.created_records[0]["job_id"] == "ftjob-test-123"
    assert fake_repository.created_records[0]["company_id"] == 7
    assert fake_repository.created_records[0]["dataset_version_id"] == 42


def test_create_finetuning_job_is_blocked_when_readiness_fails(monkeypatch) -> None:
    fake_repository = FakeRepository()
    export_calls: list[tuple[str, int | None]] = []

    monkeypatch.setattr(finetuning_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "enable_finetuning_pipeline", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    _install_ready_finetuning_stubs(monkeypatch, export_calls, is_ready=False)
    monkeypatch.setattr(finetuning_service, "OpenAI", lambda api_key: FakeOpenAIClient())

    service = FineTuningService()

    with pytest.raises(FineTuningError, match="readiness failed"):
        service.create_finetuning_job(company_id=7, dataset_version_id=42)

    assert export_calls == []
    assert fake_repository.created_records == []


def test_create_finetuning_job_requires_frozen_dataset_version(monkeypatch) -> None:
    fake_repository = FakeRepository()
    export_calls: list[tuple[str, int | None]] = []

    monkeypatch.setattr(finetuning_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "enable_finetuning_pipeline", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    _install_ready_finetuning_stubs(monkeypatch, export_calls, status="draft")
    monkeypatch.setattr(finetuning_service, "OpenAI", lambda api_key: FakeOpenAIClient())

    service = FineTuningService()

    with pytest.raises(FineTuningError, match="must be frozen"):
        service.create_finetuning_job(company_id=7, dataset_version_id=42)

    assert export_calls == []
    assert fake_repository.created_records == []


def test_retrieve_finetuning_job_updates_persisted_record(monkeypatch) -> None:
    fake_repository = FakeRepository()
    fake_repository.create_finetuning_job_record(
        job_id="ftjob-test-123",
        status="validating_files",
        base_model="gpt-4o-mini-2024-07-18",
        company_id=7,
    )

    monkeypatch.setattr(finetuning_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(finetuning_service, "OpenAI", lambda api_key: FakeOpenAIClient())

    service = FineTuningService()
    result = service.retrieve_finetuning_job("ftjob-test-123", company_id=7)

    assert result["job_id"] == "ftjob-test-123"
    assert result["status"] == "failed"
    assert result["error"]["code"] == "server_error"
    assert len(fake_repository.updated_records) == 1
    assert fake_repository.updated_records[0]["job_id"] == "ftjob-test-123"
    assert fake_repository.updated_records[0]["status"] == "failed"
    assert fake_repository.record_lookup_calls == [
        {
            "job_id": "ftjob-test-123",
            "company_id": 7,
        }
    ]


def test_retrieve_finetuning_job_rejects_mismatched_company(monkeypatch) -> None:
    fake_repository = FakeRepository()
    fake_repository.create_finetuning_job_record(
        job_id="ftjob-test-123",
        status="validating_files",
        base_model="gpt-4o-mini-2024-07-18",
        company_id=7,
    )

    monkeypatch.setattr(finetuning_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(finetuning_service, "OpenAI", lambda api_key: FakeOpenAIClient())

    service = FineTuningService()

    with pytest.raises(FineTuningError, match="company_id=9"):
        service.retrieve_finetuning_job("ftjob-test-123", company_id=9)


def test_list_finetuning_jobs_returns_structured_jobs(monkeypatch) -> None:
    fake_repository = FakeRepository()
    fake_repository.create_finetuning_job_record(
        job_id="ftjob-test-123",
        status="validating_files",
        base_model="gpt-4o-mini-2024-07-18",
        company_id=7,
    )

    monkeypatch.setattr(finetuning_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(finetuning_service, "OpenAI", lambda api_key: FakeOpenAIClient())

    service = FineTuningService()
    jobs = service.list_finetuning_jobs(limit=5, company_id=7)

    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "ftjob-test-123"
    assert jobs[0]["status"] == "failed"
    assert len(fake_repository.updated_records) == 1
    assert fake_repository.record_lookup_calls == [
        {
            "job_id": "ftjob-test-123",
            "company_id": 7,
        }
    ]


def test_list_finetuning_jobs_filters_other_companies(monkeypatch) -> None:
    fake_repository = FakeRepository()
    fake_repository.create_finetuning_job_record(
        job_id="ftjob-test-123",
        status="validating_files",
        base_model="gpt-4o-mini-2024-07-18",
        company_id=7,
    )

    monkeypatch.setattr(finetuning_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(finetuning_service, "OpenAI", lambda api_key: FakeOpenAIClient())

    service = FineTuningService()
    jobs = service.list_finetuning_jobs(limit=5, company_id=9)

    assert jobs == []
    assert fake_repository.updated_records == []
