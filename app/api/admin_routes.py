from fastapi import APIRouter, HTTPException
from app.api.schemas import (
    CompanyContextPayload,
    MessageExampleCreatePayload,
    PendingQuestionAnswerPayload,
)

from app.storage.repository import RuntimeRepository

from app.core.company_context import (
    CompanyContextError,
    get_company_context,
    save_company_context,
)
from app.services.handoff_service import normalize_owner_resolution
from app.services.knowledge_service import (
    build_knowledge_entry_from_resolution,
    should_store_resolution_as_knowledge,
)

from app.services.training_export_service import (
    export_finetuning_bundle_as_json,
    export_finetuning_train_jsonl,
    export_finetuning_validation_jsonl,
    export_training_records_as_json,
    export_training_records_as_jsonl,
    export_training_review_report_as_json,
    export_training_split_as_json,
)

from app.services.outbound_dispatch_service import (
    OutboundDispatchError,
    dispatch_outbound_message,
    dispatch_pending_outbound_messages,
)

from app.services.finetuning_service import FineTuningError, FineTuningService

admin_router = APIRouter(prefix="/admin", tags=["admin"])

repository = RuntimeRepository()
finetuning_service = FineTuningService()


@admin_router.get("/state")
def read_state() -> dict:
    return repository.get_state()


@admin_router.get("/messages")
def read_messages() -> list[dict]:
    return repository.get_messages()


@admin_router.get("/contacts")
def read_contacts() -> list[dict]:
    return repository.get_contacts()


@admin_router.get("/contacts/{contact_id}")
def read_contact(contact_id: str) -> dict:
    contact = repository.get_contact(contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found.")
    return contact


@admin_router.get("/company-context")
def read_company_context() -> dict:
    return get_company_context()


@admin_router.put("/company-context")
def update_company_context(payload: CompanyContextPayload) -> dict:
    try:
        return save_company_context(payload.data)
    except CompanyContextError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/pending-questions")
def read_pending_questions() -> list[dict]:
    return repository.get_pending_questions()


@admin_router.get("/pending-questions/{question_id}")
def read_pending_question(question_id: int) -> dict:
    pending_question = repository.get_pending_question(question_id)
    if not pending_question:
        raise HTTPException(status_code=404, detail="Pending question not found.")
    return pending_question


@admin_router.post("/pending-questions/{question_id}/answer")
def answer_pending_question(question_id: int, payload: PendingQuestionAnswerPayload) -> dict:
    pending_question = repository.get_pending_question(question_id)
    if not pending_question:
        raise HTTPException(status_code=404, detail="Pending question not found.")

    try:
        normalized_resolution = normalize_owner_resolution(
            {
                **payload.model_dump(),
                "company_context": get_company_context(),
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    repository.resolve_pending_question(
        question_id=question_id,
        owner_answer=normalized_resolution["owner_answer"],
        resolution_type=normalized_resolution["resolution_type"],
        customer_followup_question=normalized_resolution["customer_followup_question"],
        applies_globally=normalized_resolution["applies_globally"],
        knowledge_action=normalized_resolution["knowledge_action"],
    )
    outbound_message_id: int | None = None
    if normalized_resolution["resolution_type"] in {"needs_customer_clarification", "final_answer"}:
        outbound_text = (
            normalized_resolution["customer_followup_question"]
            if normalized_resolution["resolution_type"] == "needs_customer_clarification"
            else normalized_resolution["owner_answer"]
        )
        if outbound_text:
            outbound_message_id = repository.create_outbound_message(
                contact_id=pending_question["contact_id"],
                pending_question_id=question_id,
                message=outbound_text,
                source="owner_resolution",
                status="pending",
            )
    if should_store_resolution_as_knowledge(normalized_resolution):
        knowledge_entry = build_knowledge_entry_from_resolution(
            pending_question=pending_question,
            resolution=normalized_resolution,
        )
        repository.create_knowledge_entry(
            title=knowledge_entry["title"],
            content=knowledge_entry["content"],
            knowledge_type=knowledge_entry["knowledge_type"],
            source_question_id=knowledge_entry["source_question_id"],
            contact_id=knowledge_entry["contact_id"],
            applies_globally=knowledge_entry["applies_globally"],
            status=knowledge_entry["status"],
        )
    updated_pending_question = repository.get_pending_question(question_id)
    if not updated_pending_question:
        raise HTTPException(status_code=404, detail="Pending question not found after update.")

    return {
        **updated_pending_question,
        "outbound_message_id": outbound_message_id,
    }


@admin_router.get("/knowledge-entries")
def read_knowledge_entries() -> list[dict]:
    return repository.get_knowledge_entries()


@admin_router.get("/knowledge-entries/{knowledge_entry_id}")
def read_knowledge_entry(knowledge_entry_id: int) -> dict:
    knowledge_entry = repository.get_knowledge_entry(knowledge_entry_id)
    if not knowledge_entry:
        raise HTTPException(status_code=404, detail="Knowledge entry not found.")
    return knowledge_entry


@admin_router.post("/message-examples")
def create_message_example(payload: MessageExampleCreatePayload) -> dict:
    message_example_id = repository.create_message_example(
        profile_name=payload.profile_name,
        scenario_type=payload.scenario_type,
        customer_message=payload.customer_message,
        approved_reply=payload.approved_reply,
        style_notes=payload.style_notes,
        quality_score=payload.quality_score,
        used_for_training=payload.used_for_training,
        active=payload.active,
    )

    message_example = repository.get_message_example(message_example_id)
    if not message_example:
        raise HTTPException(status_code=404, detail="Message example not found after creation.")
    return message_example


@admin_router.get("/message-examples")
def read_message_examples() -> list[dict]:
    return repository.get_message_examples()


@admin_router.get("/message-examples/{message_example_id}")
def read_message_example(message_example_id: int) -> dict:
    message_example = repository.get_message_example(message_example_id)
    if not message_example:
        raise HTTPException(status_code=404, detail="Message example not found.")
    return message_example


@admin_router.get("/training-dataset/json")
def read_training_dataset_json() -> dict:
    return {"dataset": export_training_records_as_json()}


@admin_router.get("/training-dataset/jsonl")
def read_training_dataset_jsonl() -> dict:
    return {"dataset": export_training_records_as_jsonl()}



@admin_router.get("/training-dataset/split")
def read_training_dataset_split() -> dict:
    return {"dataset": export_training_split_as_json()}


@admin_router.get("/training-dataset/review")
def read_training_dataset_review() -> dict:
    return {"dataset": export_training_review_report_as_json()}


@admin_router.get("/finetuning-dataset/train-jsonl")
def read_finetuning_train_jsonl() -> dict:
    return {"dataset": export_finetuning_train_jsonl()}


@admin_router.get("/finetuning-dataset/validation-jsonl")
def read_finetuning_validation_jsonl() -> dict:
    return {"dataset": export_finetuning_validation_jsonl()}


@admin_router.get("/finetuning-dataset/bundle")
def read_finetuning_dataset_bundle() -> dict:
    return {"dataset": export_finetuning_bundle_as_json()}


@admin_router.get("/outbound-messages")
def read_outbound_messages() -> list[dict]:
    return repository.get_outbound_messages()


@admin_router.get("/outbound-messages/{outbound_message_id}")
def read_outbound_message(outbound_message_id: int) -> dict:
    outbound_message = repository.get_outbound_message(outbound_message_id)
    if not outbound_message:
        raise HTTPException(status_code=404, detail="Outbound message not found.")
    return outbound_message


@admin_router.post("/outbound-messages/{outbound_message_id}/mark-sent")
def mark_outbound_message_sent(outbound_message_id: int) -> dict:
    outbound_message = repository.get_outbound_message(outbound_message_id)
    if not outbound_message:
        raise HTTPException(status_code=404, detail="Outbound message not found.")

    repository.mark_outbound_message_sent(outbound_message_id)
    updated_outbound_message = repository.get_outbound_message(outbound_message_id)
    if not updated_outbound_message:
        raise HTTPException(status_code=404, detail="Outbound message not found after update.")

    return updated_outbound_message


@admin_router.post("/outbound-messages/{outbound_message_id}/dispatch")
def dispatch_single_outbound_message(outbound_message_id: int, channel: str = "generic") -> dict:
    try:
        return dispatch_outbound_message(
            outbound_message_id=outbound_message_id,
            channel=channel,
        )
    except OutboundDispatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post("/outbound-messages/dispatch-pending")
def dispatch_all_pending_outbound_messages(channel: str = "generic") -> list[dict]:
    try:
        return dispatch_pending_outbound_messages(channel=channel)
    except OutboundDispatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post("/finetuning/jobs")
def create_finetuning_job() -> dict:
    try:
        return finetuning_service.create_finetuning_job()
    except FineTuningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/finetuning/jobs")
def list_finetuning_jobs(limit: int = 10) -> list[dict]:
    try:
        return finetuning_service.list_finetuning_jobs(limit=limit)
    except FineTuningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/finetuning/jobs/{job_id}")
def read_finetuning_job(job_id: str) -> dict:
    try:
        return finetuning_service.retrieve_finetuning_job(job_id)
    except FineTuningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/finetuning/jobs-db")
def read_persisted_finetuning_jobs() -> list[dict]:
    return repository.get_finetuning_job_records()


@admin_router.get("/finetuning/jobs-db/{job_id}")
def read_persisted_finetuning_job(job_id: str) -> dict:
    job_record = repository.get_finetuning_job_record(job_id)
    if not job_record:
        raise HTTPException(status_code=404, detail="Persisted fine-tuning job not found.")
    return job_record