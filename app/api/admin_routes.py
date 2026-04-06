import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from app.api.schemas import (
    AppointmentBookingPayload,
    CalendarAvailabilityQueryPayload,
    CalendarIntegrationPayload,
    CompanyContextPayload,
    CompanyCreatePayload,
    CompanyResponse,
    InternalReminderPayload,
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
from app.services.system_readiness_service import (
    get_onboarding_status,
    get_system_profile,
    get_system_readiness,
)
from app.services.calendar_service import (
    CalendarProviderError,
    create_calendar_booking,
    create_calendar_integration,
    get_calendar_bookings,
    get_calendar_integrations,
    get_calendar_profile,
    list_supported_calendar_providers,
    query_calendar_availability,
)
from app.services.reminder_service import (
    ReminderServiceError,
    create_internal_reminder,
    dispatch_due_reminders,
    list_reminders,
)

from app.core.config import settings
from app.services.finetuning_service import FineTuningError, FineTuningService
from app.core.logging import get_logger, get_request_context, log_event

repository = RuntimeRepository()
logger = get_logger(__name__)
ADMIN_ROLE_HIERARCHY = {
    "viewer": 1,
    "operator": 2,
    "superadmin": 3,
}


def _get_finetuning_service() -> FineTuningService:
    return FineTuningService()


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None

    normalized = authorization.strip()
    if not normalized.lower().startswith("bearer "):
        return None

    token = normalized[7:].strip()
    return token or None


def _get_role_token_map() -> dict[str, str]:
    role_token_map: dict[str, str] = {}

    if settings.admin_viewer_token:
        role_token_map["viewer"] = settings.admin_viewer_token.strip()
    if settings.admin_operator_token:
        role_token_map["operator"] = settings.admin_operator_token.strip()

    superadmin_token = (
        settings.admin_superadmin_token.strip()
        if settings.admin_superadmin_token
        else None
    )
    fallback_superadmin_token = (
        settings.admin_api_token.strip()
        if settings.admin_api_token
        else None
    )
    if superadmin_token:
        role_token_map["superadmin"] = superadmin_token
    elif fallback_superadmin_token:
        role_token_map["superadmin"] = fallback_superadmin_token

    return role_token_map


def _build_prometheus_metrics(summary: dict) -> str:
    lines: list[str] = []

    def add_metric(name: str, value: int, labels: dict[str, str] | None = None) -> None:
        if labels:
            label_text = ",".join(f'{key}="{value_}"' for key, value_ in labels.items())
            lines.append(f"{name}{{{label_text}}} {value}")
        else:
            lines.append(f"{name} {value}")

    for key, value in summary.get("totals", {}).items():
        add_metric("project_wapp_total", int(value), {"entity": str(key)})

    grouped_metric_map = {
        "pending_questions_by_status": ("project_wapp_pending_questions", "status"),
        "outbound_by_status": ("project_wapp_outbound_messages", "status"),
        "knowledge_by_status": ("project_wapp_knowledge_entries", "status"),
        "messages_by_classification": ("project_wapp_messages_by_classification", "classification"),
        "messages_by_action": ("project_wapp_messages_by_action", "action"),
        "messages_by_reply_source": ("project_wapp_messages_by_reply_source", "reply_source"),
        "companies_by_status": ("project_wapp_companies", "status"),
        "audit_events_by_status": ("project_wapp_audit_events", "status"),
    }

    for summary_key, (metric_name, label_name) in grouped_metric_map.items():
        for group_key, count in summary.get(summary_key, {}).items():
            add_metric(metric_name, int(count), {label_name: str(group_key)})

    return "\n".join(lines) + "\n"


def _record_audit_event(
    *,
    request: Request,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    company_id: int | None = None,
    status: str = "success",
    metadata: dict | None = None,
) -> None:
    actor_id = getattr(request.state, "admin_actor", "admin")
    request_id = get_request_context().get("request_id")
    repository.create_audit_event(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        company_id=company_id,
        status=status,
        request_id=str(request_id) if request_id else None,
        metadata=metadata or {},
    )


def require_admin_access(
    request: Request,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_admin_actor: str | None = Header(default=None, alias="X-Admin-Actor"),
) -> None:
    actor = (x_admin_actor or "admin").strip() or "admin"
    request.state.admin_actor = actor
    request.state.admin_role = "superadmin"

    if not settings.enable_admin_auth:
        request.state.admin_auth_mode = "disabled"
        return

    role_token_map = _get_role_token_map()
    provided_token = x_admin_token or _extract_bearer_token(authorization)

    if not role_token_map:
        log_event(
            logger,
            logging.ERROR,
            "admin.auth.misconfigured",
            actor_id=actor,
        )
        repository.create_audit_event(
            actor_id=actor,
            action="authenticate",
            resource_type="admin_access",
            status="misconfigured",
            request_id=str(get_request_context().get("request_id") or ""),
            metadata={"reason": "missing_admin_role_tokens"},
        )
        raise HTTPException(
            status_code=500,
            detail="Admin authentication is enabled but no admin tokens are configured.",
        )

    resolved_role = None
    for role, configured_token in role_token_map.items():
        if provided_token == configured_token:
            resolved_role = role
            break

    if resolved_role is None:
        log_event(
            logger,
            logging.WARNING,
            "admin.auth.failed",
            actor_id=actor,
        )
        repository.create_audit_event(
            actor_id=actor,
            action="authenticate",
            resource_type="admin_access",
            status="failed",
            request_id=str(get_request_context().get("request_id") or ""),
            metadata={"reason": "invalid_admin_credentials"},
        )
        raise HTTPException(status_code=401, detail="Invalid admin credentials.")

    request.state.admin_auth_mode = "token"
    request.state.admin_role = resolved_role
    log_event(
        logger,
        logging.INFO,
        "admin.auth.succeeded",
        actor_id=actor,
        auth_mode="token",
        admin_role=resolved_role,
    )


def require_admin_role(required_role: str):
    def dependency(request: Request) -> None:
        current_role = getattr(request.state, "admin_role", "viewer")
        current_weight = ADMIN_ROLE_HIERARCHY.get(current_role, 0)
        required_weight = ADMIN_ROLE_HIERARCHY.get(required_role, 0)
        if current_weight >= required_weight:
            return

        log_event(
            logger,
            logging.WARNING,
            "admin.authorization.denied",
            actor_id=getattr(request.state, "admin_actor", "admin"),
            current_role=current_role,
            required_role=required_role,
        )
        repository.create_audit_event(
            actor_id=getattr(request.state, "admin_actor", "admin"),
            action="authorize",
            resource_type="admin_access",
            status="denied",
            request_id=str(get_request_context().get("request_id") or ""),
            metadata={
                "current_role": current_role,
                "required_role": required_role,
            },
        )
        raise HTTPException(status_code=403, detail="Insufficient admin privileges.")

    return dependency


admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)


@admin_router.get("/state")
def read_state() -> dict:
    return repository.get_state()


@admin_router.get("/observability/summary")
def read_observability_summary() -> dict:
    summary = repository.get_observability_summary()
    log_event(
        logger,
        logging.INFO,
        "admin.observability.summary.read",
        total_messages=summary["totals"]["messages"],
        total_contacts=summary["totals"]["contacts"],
    )
    return summary


@admin_router.get("/observability/metrics", response_class=PlainTextResponse)
def read_observability_metrics() -> PlainTextResponse:
    summary = repository.get_observability_summary()
    metrics_payload = _build_prometheus_metrics(summary)
    log_event(
        logger,
        logging.INFO,
        "admin.observability.metrics.read",
        total_messages=summary["totals"]["messages"],
        total_contacts=summary["totals"]["contacts"],
    )
    return PlainTextResponse(
        content=metrics_payload,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@admin_router.get("/audit-events")
def read_audit_events(
    limit: int = 100,
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
) -> list[dict]:
    normalized_limit = max(1, min(limit, 500))
    return repository.get_audit_events(
        limit=normalized_limit,
        action=action,
        resource_type=resource_type,
    )


@admin_router.post(
    "/audit-events/cleanup",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def cleanup_audit_events(
    request: Request,
    older_than_days: int | None = Query(default=None),
) -> dict:
    retention_days = older_than_days or settings.audit_retention_days
    if retention_days <= 0:
        raise HTTPException(status_code=400, detail="older_than_days must be greater than zero.")

    deleted_count = repository.purge_audit_events_older_than(retention_days)
    log_event(
        logger,
        logging.INFO,
        "admin.audit_events.cleaned_up",
        retention_days=retention_days,
        deleted_count=deleted_count,
    )
    _record_audit_event(
        request=request,
        action="cleanup",
        resource_type="audit_event",
        metadata={
            "retention_days": retention_days,
            "deleted_count": deleted_count,
        },
    )
    return {
        "retention_days": retention_days,
        "deleted_count": deleted_count,
    }


@admin_router.get("/system/migrations")
def read_schema_migrations() -> list[dict]:
    return repository.get_schema_migrations()


@admin_router.get("/system/profile")
def read_system_profile() -> dict:
    profile = get_system_profile()
    log_event(
        logger,
        logging.INFO,
        "admin.system.profile.read",
        environment=profile["app"]["environment"],
        runtime_backend=profile["database"]["runtime"]["backend"],
    )
    return profile


@admin_router.get("/system/readiness")
def read_system_readiness() -> dict:
    readiness = get_system_readiness()
    log_event(
        logger,
        logging.INFO,
        "admin.system.readiness.read",
        overall_status=readiness["overall_status"],
        pilot_ready=readiness["pilot_ready"],
        scale_ready=readiness["scale_ready"],
    )
    return readiness


@admin_router.get("/system/onboarding")
def read_system_onboarding(
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict:
    onboarding = get_onboarding_status(company_id=company_id, company_slug=company_slug)
    log_event(
        logger,
        logging.INFO,
        "admin.system.onboarding.read",
        company_id=onboarding["scope"]["company_id"],
        company_slug=onboarding["scope"]["company_slug"],
        overall_status=onboarding["overall_status"],
    )
    return onboarding


@admin_router.get("/calendar/providers")
def read_calendar_providers() -> list[dict]:
    return list_supported_calendar_providers()


@admin_router.get("/calendar/profile")
def read_calendar_profile(company_id: int | None = Query(default=None)) -> dict:
    profile = get_calendar_profile(company_id=company_id)
    log_event(
        logger,
        logging.INFO,
        "admin.calendar.profile.read",
        company_id=company_id,
        provider=profile["integration"]["provider"],
    )
    return profile


@admin_router.get("/calendar/integrations")
def read_calendar_integrations(company_id: int | None = Query(default=None)) -> list[dict]:
    return get_calendar_integrations(company_id=company_id)


@admin_router.post(
    "/calendar/integrations",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_calendar_integration_endpoint(
    payload: CalendarIntegrationPayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    try:
        integration = create_calendar_integration(
            provider=payload.provider,
            company_id=company_id,
            external_calendar_id=payload.external_calendar_id,
            config=payload.config,
            active=payload.active,
        )
    except CalendarProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="create",
        resource_type="calendar_integration",
        resource_id=str(integration["id"]),
        company_id=company_id,
        metadata={
            "provider": integration["provider"],
            "active": integration["active"],
        },
    )
    return integration


@admin_router.post(
    "/calendar/availability/query",
    dependencies=[Depends(require_admin_role("operator"))],
)
def query_calendar_availability_endpoint(
    payload: CalendarAvailabilityQueryPayload,
    company_id: int | None = Query(default=None),
) -> dict:
    try:
        return query_calendar_availability(
            company_id=company_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            duration_minutes=payload.duration_minutes,
            limit=payload.limit,
        )
    except CalendarProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/calendar/bookings")
def read_calendar_bookings(company_id: int | None = Query(default=None)) -> list[dict]:
    return get_calendar_bookings(company_id=company_id)


@admin_router.post(
    "/calendar/bookings",
    dependencies=[Depends(require_admin_role("operator"))],
)
def create_calendar_booking_endpoint(
    payload: AppointmentBookingPayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    try:
        booking = create_calendar_booking(
            company_id=company_id,
            contact_id=payload.contact_id,
            title=payload.title,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            timezone_name=payload.timezone,
            description=payload.description,
        )
    except CalendarProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="create",
        resource_type="appointment_booking",
        resource_id=str(booking["id"]),
        company_id=company_id,
        metadata={
            "provider": booking["provider"],
            "starts_at": booking["starts_at"],
            "ends_at": booking["ends_at"],
            "contact_id": booking.get("contact_id"),
        },
    )
    return booking


@admin_router.get("/reminders")
def read_customer_reminders(
    company_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    audience: str | None = Query(default=None),
) -> list[dict]:
    return list_reminders(company_id=company_id, status=status, audience=audience)


@admin_router.post(
    "/reminders",
    dependencies=[Depends(require_admin_role("operator"))],
)
def create_internal_reminder_endpoint(
    payload: InternalReminderPayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    try:
        reminder = create_internal_reminder(
            company_id=company_id,
            owner_contact_id=payload.owner_contact_id,
            message_text=payload.message_text,
            timezone_name=payload.timezone,
        )
    except ReminderServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="create",
        resource_type="internal_reminder",
        resource_id=str(reminder["id"]),
        company_id=company_id,
        metadata={
            "owner_contact_id": payload.owner_contact_id,
            "audience": reminder.get("audience"),
        },
    )
    return reminder


@admin_router.post(
    "/reminders/dispatch-due",
    dependencies=[Depends(require_admin_role("operator"))],
)
def dispatch_due_customer_reminders(
    request: Request,
    dispatch_immediately: bool = Query(default=False),
    channel: str | None = Query(default=None),
    audience: str | None = Query(default=None),
) -> list[dict]:
    results = dispatch_due_reminders(
        dispatch_immediately=dispatch_immediately,
        channel=channel,
        audience=audience,
    )
    _record_audit_event(
        request=request,
        action="dispatch_due",
        resource_type="reminder",
        metadata={
            "dispatch_immediately": dispatch_immediately,
            "channel": channel,
            "audience": audience,
            "count": len(results),
        },
    )
    return results


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


@admin_router.post(
    "/companies",
    response_model=CompanyResponse,
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_company(payload: CompanyCreatePayload, request: Request) -> CompanyResponse:
    existing_company = repository.get_company_by_slug(payload.slug)
    if existing_company:
        raise HTTPException(status_code=400, detail="Company slug already exists.")

    company_id = repository.create_company(
        slug=payload.slug,
        name=payload.name,
        business_type=payload.business_type,
        active=payload.active,
    )
    company = repository.get_company(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found after creation.")
    log_event(
        logger,
        logging.INFO,
        "admin.company.created",
        company_id=company_id,
        company_slug=payload.slug,
        active=payload.active,
    )
    _record_audit_event(
        request=request,
        action="create",
        resource_type="company",
        resource_id=str(company_id),
        company_id=company_id,
        metadata={
            "company_slug": payload.slug,
            "business_type": payload.business_type,
            "active": payload.active,
        },
    )
    return CompanyResponse(**company)


@admin_router.get("/companies", response_model=list[CompanyResponse])
def read_companies() -> list[CompanyResponse]:
    return [CompanyResponse(**company) for company in repository.get_companies()]


@admin_router.get("/companies/{company_id}", response_model=CompanyResponse)
def read_company(company_id: int) -> CompanyResponse:
    company = repository.get_company(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")
    return CompanyResponse(**company)


@admin_router.get("/company-context")
def read_company_context(company_slug: str | None = Query(default=None)) -> dict:
    try:
        return get_company_context(company_slug=company_slug)
    except CompanyContextError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.put(
    "/company-context",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def update_company_context(
    payload: CompanyContextPayload,
    request: Request,
    company_slug: str | None = Query(default=None),
) -> dict:
    try:
        result = save_company_context(payload.data, company_slug=company_slug)
        log_event(
            logger,
            logging.INFO,
            "admin.company_context.updated",
            company_slug=company_slug or "global",
        )
        _record_audit_event(
            request=request,
            action="update",
            resource_type="company_context",
            resource_id=company_slug or "global",
            metadata={
                "company_slug": company_slug or "global",
                "top_level_keys": sorted(payload.data.keys()),
            },
        )
        return result
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


@admin_router.post(
    "/pending-questions/{question_id}/answer",
    dependencies=[Depends(require_admin_role("operator"))],
)
def answer_pending_question(question_id: int, payload: PendingQuestionAnswerPayload, request: Request) -> dict:
    pending_question = repository.get_pending_question(question_id)
    if not pending_question:
        raise HTTPException(status_code=404, detail="Pending question not found.")

    try:
        company_slug = None
        if pending_question.get("company_id") is not None:
            company = repository.get_company(int(pending_question["company_id"]))
            if company:
                company_slug = company.get("slug")

        normalized_resolution = normalize_owner_resolution(
            {
                **payload.model_dump(),
                "company_context": get_company_context(company_slug=company_slug),
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
                company_id=pending_question.get("company_id"),
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
            company_id=knowledge_entry["company_id"],
            applies_globally=knowledge_entry["applies_globally"],
            status=knowledge_entry["status"],
        )
    updated_pending_question = repository.get_pending_question(question_id)
    if not updated_pending_question:
        raise HTTPException(status_code=404, detail="Pending question not found after update.")

    log_event(
        logger,
        logging.INFO,
        "admin.pending_question.answered",
        question_id=question_id,
        company_id=pending_question.get("company_id"),
        resolution_type=normalized_resolution["resolution_type"],
        knowledge_action=normalized_resolution["knowledge_action"],
        outbound_message_id=outbound_message_id,
    )
    _record_audit_event(
        request=request,
        action="answer",
        resource_type="pending_question",
        resource_id=str(question_id),
        company_id=pending_question.get("company_id"),
        metadata={
            "resolution_type": normalized_resolution["resolution_type"],
            "knowledge_action": normalized_resolution["knowledge_action"],
            "outbound_message_id": outbound_message_id,
        },
    )

    return {
        **updated_pending_question,
        "outbound_message_id": outbound_message_id,
    }


@admin_router.get("/knowledge-entries")
def read_knowledge_entries(company_id: int | None = Query(default=None)) -> list[dict]:
    if company_id is None:
        return repository.get_knowledge_entries()
    return [
        entry
        for entry in repository.get_knowledge_entries()
        if entry.get("company_id") == company_id
    ]


@admin_router.get("/knowledge-entries/{knowledge_entry_id}")
def read_knowledge_entry(knowledge_entry_id: int) -> dict:
    knowledge_entry = repository.get_knowledge_entry(knowledge_entry_id)
    if not knowledge_entry:
        raise HTTPException(status_code=404, detail="Knowledge entry not found.")
    return knowledge_entry


@admin_router.post(
    "/message-examples",
    dependencies=[Depends(require_admin_role("operator"))],
)
def create_message_example(
    payload: MessageExampleCreatePayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    message_example_id = repository.create_message_example(
        company_id=company_id,
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
    log_event(
        logger,
        logging.INFO,
        "admin.message_example.created",
        message_example_id=message_example_id,
        company_id=company_id,
        scenario_type=payload.scenario_type,
        profile_name=payload.profile_name,
        used_for_training=payload.used_for_training,
    )
    _record_audit_event(
        request=request,
        action="create",
        resource_type="message_example",
        resource_id=str(message_example_id),
        company_id=company_id,
        metadata={
            "scenario_type": payload.scenario_type,
            "profile_name": payload.profile_name,
            "used_for_training": payload.used_for_training,
        },
    )
    return message_example


@admin_router.get("/message-examples")
def read_message_examples(company_id: int | None = Query(default=None)) -> list[dict]:
    if company_id is None:
        return repository.get_message_examples()
    return [
        example
        for example in repository.get_message_examples()
        if example.get("company_id") == company_id
    ]


@admin_router.get("/message-examples/{message_example_id}")
def read_message_example(message_example_id: int) -> dict:
    message_example = repository.get_message_example(message_example_id)
    if not message_example:
        raise HTTPException(status_code=404, detail="Message example not found.")
    return message_example


@admin_router.get("/training-dataset/json")
def read_training_dataset_json(company_id: int | None = Query(default=None)) -> dict:
    return {"dataset": export_training_records_as_json(company_id=company_id)}


@admin_router.get("/training-dataset/jsonl")
def read_training_dataset_jsonl(company_id: int | None = Query(default=None)) -> dict:
    return {"dataset": export_training_records_as_jsonl(company_id=company_id)}



@admin_router.get("/training-dataset/split")
def read_training_dataset_split(company_id: int | None = Query(default=None)) -> dict:
    return {"dataset": export_training_split_as_json(company_id=company_id)}


@admin_router.get("/training-dataset/review")
def read_training_dataset_review(company_id: int | None = Query(default=None)) -> dict:
    return {"dataset": export_training_review_report_as_json(company_id=company_id)}


@admin_router.get("/finetuning-dataset/train-jsonl")
def read_finetuning_train_jsonl(company_id: int | None = Query(default=None)) -> dict:
    return {"dataset": export_finetuning_train_jsonl(company_id=company_id)}


@admin_router.get("/finetuning-dataset/validation-jsonl")
def read_finetuning_validation_jsonl(company_id: int | None = Query(default=None)) -> dict:
    return {"dataset": export_finetuning_validation_jsonl(company_id=company_id)}


@admin_router.get("/finetuning-dataset/bundle")
def read_finetuning_dataset_bundle(company_id: int | None = Query(default=None)) -> dict:
    return {"dataset": export_finetuning_bundle_as_json(company_id=company_id)}


@admin_router.get("/outbound-messages")
def read_outbound_messages() -> list[dict]:
    return repository.get_outbound_messages()


@admin_router.get("/outbound-messages/{outbound_message_id}")
def read_outbound_message(outbound_message_id: int) -> dict:
    outbound_message = repository.get_outbound_message(outbound_message_id)
    if not outbound_message:
        raise HTTPException(status_code=404, detail="Outbound message not found.")
    return outbound_message


@admin_router.post(
    "/outbound-messages/{outbound_message_id}/mark-sent",
    dependencies=[Depends(require_admin_role("operator"))],
)
def mark_outbound_message_sent(outbound_message_id: int, request: Request) -> dict:
    outbound_message = repository.get_outbound_message(outbound_message_id)
    if not outbound_message:
        raise HTTPException(status_code=404, detail="Outbound message not found.")

    repository.mark_outbound_message_sent(outbound_message_id)
    updated_outbound_message = repository.get_outbound_message(outbound_message_id)
    if not updated_outbound_message:
        raise HTTPException(status_code=404, detail="Outbound message not found after update.")

    _record_audit_event(
        request=request,
        action="mark_sent",
        resource_type="outbound_message",
        resource_id=str(outbound_message_id),
        company_id=updated_outbound_message.get("company_id"),
        metadata={"status": updated_outbound_message.get("status")},
    )
    return updated_outbound_message


@admin_router.post(
    "/outbound-messages/{outbound_message_id}/dispatch",
    dependencies=[Depends(require_admin_role("operator"))],
)
def dispatch_single_outbound_message(outbound_message_id: int, request: Request, channel: str = "generic") -> dict:
    try:
        result = dispatch_outbound_message(
            outbound_message_id=outbound_message_id,
            channel=channel,
        )
        log_event(
            logger,
            logging.INFO,
            "admin.outbound_message.dispatched",
            outbound_message_id=outbound_message_id,
            channel=channel,
            delivery_status=result.get("delivery_status"),
        )
        _record_audit_event(
            request=request,
            action="dispatch",
            resource_type="outbound_message",
            resource_id=str(outbound_message_id),
            metadata={
                "channel": channel,
                "delivery_status": result.get("delivery_status"),
            },
        )
        return result
    except OutboundDispatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post(
    "/outbound-messages/dispatch-pending",
    dependencies=[Depends(require_admin_role("operator"))],
)
def dispatch_all_pending_outbound_messages(request: Request, channel: str = "generic") -> list[dict]:
    try:
        results = dispatch_pending_outbound_messages(channel=channel)
        log_event(
            logger,
            logging.INFO,
            "admin.outbound_messages.bulk_dispatch.completed",
            channel=channel,
            dispatched_count=len(results),
        )
        _record_audit_event(
            request=request,
            action="dispatch_bulk",
            resource_type="outbound_message",
            metadata={
                "channel": channel,
                "dispatched_count": len(results),
            },
        )
        return results
    except OutboundDispatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post(
    "/finetuning/jobs",
    dependencies=[Depends(require_admin_role("operator"))],
)
def create_finetuning_job(request: Request) -> dict:
    try:
        result = _get_finetuning_service().create_finetuning_job()
        log_event(
            logger,
            logging.INFO,
            "admin.finetuning.job.created",
            job_id=result.get("job_id"),
            status=result.get("status"),
            base_model=result.get("base_model"),
        )
        _record_audit_event(
            request=request,
            action="create",
            resource_type="finetuning_job",
            resource_id=str(result.get("job_id")),
            company_id=1,
            metadata={
                "status": result.get("status"),
                "base_model": result.get("base_model"),
            },
        )
        return result
    except FineTuningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/finetuning/jobs")
def list_finetuning_jobs(limit: int = 10) -> list[dict]:
    try:
        results = _get_finetuning_service().list_finetuning_jobs(limit=limit)
        log_event(
            logger,
            logging.INFO,
            "admin.finetuning.jobs.listed",
            limit=limit,
            results_count=len(results),
        )
        return results
    except FineTuningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/finetuning/jobs/{job_id}")
def read_finetuning_job(job_id: str) -> dict:
    try:
        result = _get_finetuning_service().retrieve_finetuning_job(job_id)
        log_event(
            logger,
            logging.INFO,
            "admin.finetuning.job.read",
            job_id=job_id,
            status=result.get("status"),
        )
        return result
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
