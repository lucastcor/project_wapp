import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import PlainTextResponse

from app.api.admin_security import (
    build_require_admin_access,
    build_require_admin_role,
)
from app.api.admin_support import build_prometheus_metrics, record_audit_event
from app.api.company_scope import get_authorized_scope
from app.api.schemas import (
    AppointmentBookingCancellationPayload,
    AppointmentBookingPayload,
    AppointmentBookingReschedulePayload,
    AppointmentQueueEntryPayload,
    AppointmentQueueOfferResponsePayload,
    BookingOutcomePayload,
    CalendarAvailabilityQueryPayload,
    CalendarIntegrationPayload,
    CancellationReasonPayload,
    CancelSubscriptionPayload,
    CompanyContextPayload,
    CompanyContextRecordResponse,
    CompanyCreatePayload,
    CompanyResponse,
    ContactUpdatePayload,
    ConversationOwnershipRequest,
    IncomingMessageRequest,
    IncomingMessageResponse,
    InternalReminderPayload,
    MessageExampleCreatePayload,
    OnboardingSetupPayload,
    PendingQuestionAnswerPayload,
    PlanChangePayload,
    ReactivateSubscriptionPayload,
    StripeCheckoutPayload,
    SubscriptionActivatePayload,
    TrainingImportPayload,
    UsageRecordPayload,
    WhatsAppBindingPayload,
    WhatsAppBindingResponse,
    WhatsAppBindingStatePayload,
)
from app.core.company_context import (
    CompanyContextConflictError,
    CompanyContextError,
    get_company_context,
    get_company_context_record,
    save_company_context_record,
)
from app.core.config import settings
from app.core.logging import get_logger, log_event, mask_identifier
from app.core.orchestrator import process_incoming_message
from app.services.appointment_queue_service import (
    AppointmentQueueError,
    create_queue_entry,
    list_queue_entries,
    list_queue_offers,
    release_booking_slot,
    respond_to_queue_offer,
)
from app.services.calendar_service import (
    BookingConflictError,
    BookingNotFoundError,
    BookingPolicyError,
    CalendarProviderError,
    cancel_calendar_booking,
    check_calendar_connection,
    create_calendar_booking,
    create_calendar_integration,
    get_calendar_bookings,
    get_calendar_integrations,
    get_calendar_profile,
    get_calendar_timeline,
    list_supported_calendar_providers,
    query_calendar_availability,
    reschedule_calendar_booking,
)
from app.services.finetuning_service import FineTuningError, FineTuningService
from app.services.handoff_service import normalize_owner_resolution
from app.services.knowledge_service import (
    build_knowledge_entry_from_resolution,
    should_store_resolution_as_knowledge,
)
from app.services.outbound_dispatch_service import (
    OutboundDispatchError,
    dispatch_outbound_message,
    dispatch_pending_outbound_messages,
)
from app.services.realtime_service import (
    broadcast_event,
    publish_incoming_message_event,
)
from app.services.reminder_service import (
    ReminderServiceError,
    create_internal_reminder,
    dispatch_due_reminders,
    list_reminders,
)
from app.services.system_readiness_service import (
    get_onboarding_status,
    get_system_profile,
    get_system_readiness,
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
from app.services.training_import_service import (
    TrainingImportError,
    import_training_records,
)
from app.services.whatsapp_provider import (
    check_whatsapp_connection,
    get_whatsapp_channel_status,
)
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()
logger = get_logger(__name__)
_build_prometheus_metrics = build_prometheus_metrics


def _get_finetuning_service() -> FineTuningService:
    return FineTuningService()


def _get_repository() -> RuntimeRepository:
    return repository


def _deny_identity_for_platform_operation(request: Request) -> None:
    """Reserve cross-tenant platform operations for break-glass credentials."""
    if getattr(request.state, "admin_auth_mode", None) == "identity":
        raise HTTPException(
            status_code=403,
            detail="This platform-level operation requires break-glass administrator credentials.",
        )


def _require_company_owner_or_break_glass(request: Request) -> None:
    if (
        getattr(request.state, "admin_auth_mode", None) == "identity"
        and getattr(request.state, "admin_identity_role", None) != "owner"
    ):
        raise HTTPException(
            status_code=403,
            detail="Only the company owner can manage billing.",
        )


def _resolve_company_scope(
    *,
    company_id: int | None = None,
    company_slug: str | None = None,
    required: bool = False,
) -> int | None:
    resolved_company_id = repository.resolve_company_id(
        company_id=company_id,
        company_slug=company_slug,
    )
    if required and resolved_company_id is None:
        raise HTTPException(status_code=400, detail="company_id or company_slug must resolve to an existing company.")
    return resolved_company_id


def _broadcast_reminders_dispatched(results: list[dict]) -> None:
    """Emit one tenant-scoped event per company so scopes stay isolated."""
    counts_by_company: dict[int | None, int] = {}
    for result in results:
        raw_company_id = result.get("company_id")
        company_key = int(raw_company_id) if raw_company_id is not None else None
        counts_by_company[company_key] = counts_by_company.get(company_key, 0) + 1

    for company_key, count in counts_by_company.items():
        broadcast_event("reminders_dispatched", {"count": count}, company_id=company_key)


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
    record_audit_event(
        request=request,
        action=action,
        resource_type=resource_type,
        repository_getter=_get_repository,
        resource_id=resource_id,
        company_id=company_id,
        status=status,
        metadata=metadata or {},
    )


require_admin_access = build_require_admin_access(
    settings_obj=settings,
    logger=logger,
    repository_getter=_get_repository,
)


def require_admin_role(required_role: str):
    return build_require_admin_role(
        required_role,
        logger=logger,
        repository_getter=_get_repository,
    )


admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)


@admin_router.get("/state")
def read_state(company_id: int | None = Query(default=None)) -> dict:
    return repository.get_state() if company_id is None else repository.get_state(company_id=company_id)


@admin_router.get("/access/scope")
def read_access_scope(request: Request) -> dict:
    """Escopo que a plataforma resolveu para esta credencial.

    Introspecção honesta do que a autorização decidiu: qual empresa ficou
    ativa, quais estão ao alcance e por qual modo a identidade foi obtida.

    O cliente usa isto para montar o seletor de empresa sem adivinhar — sem
    esta rota, o frontend teria de inferir o escopo a partir de quais chamadas
    falham, o que é exatamente como uma UI acaba mostrando dados de uma empresa
    sob o rótulo de outra.
    """
    scope = get_authorized_scope(request)
    company_ids = getattr(request.state, "admin_company_ids", None)
    return {
        "actor": getattr(request.state, "admin_actor", None),
        "user_id": getattr(request.state, "admin_user_id", None),
        "email": getattr(request.state, "admin_email", None),
        "role": getattr(request.state, "admin_role", None),
        "identity_role": getattr(request.state, "admin_identity_role", None),
        "auth_mode": getattr(request.state, "admin_auth_mode", None),
        "is_global": bool(scope.is_global) if scope is not None else False,
        "authorized_company_id": scope.company_id if scope is not None else None,
        # `None` significa "sem restrição" (token de plataforma), e é
        # diferente de lista vazia, que é "nenhuma empresa ao alcance".
        "authorized_company_ids": sorted(company_ids) if company_ids is not None else None,
    }


@admin_router.get("/observability/summary")
def read_observability_summary(company_id: int | None = Query(default=None)) -> dict:
    summary = (
        repository.get_observability_summary()
        if company_id is None
        else repository.get_observability_summary(company_id=company_id)
    )
    log_event(
        logger,
        logging.INFO,
        "admin.observability.summary.read",
        total_messages=summary["totals"]["messages"],
        total_contacts=summary["totals"]["contacts"],
    )
    return summary


@admin_router.get("/observability/metrics", response_class=PlainTextResponse)
def read_observability_metrics(
    company_id: int | None = Query(default=None),
) -> PlainTextResponse:
    summary = (
        repository.get_observability_summary()
        if company_id is None
        else repository.get_observability_summary(company_id=company_id)
    )
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
    company_id: int | None = Query(default=None),
) -> list[dict]:
    normalized_limit = max(1, min(limit, 500))
    return repository.get_audit_events(
        limit=normalized_limit,
        action=action,
        resource_type=resource_type,
        company_id=company_id,
    )


@admin_router.post(
    "/audit-events/cleanup",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def cleanup_audit_events(
    request: Request,
    older_than_days: int | None = Query(default=None),
) -> dict:
    _deny_identity_for_platform_operation(request)
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


@admin_router.post(
    "/calendar/test-connection",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def test_calendar_connection_endpoint(
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    result = check_calendar_connection(company_id=company_id)
    _record_audit_event(
        request=request,
        action="connection_test",
        resource_type="calendar_integration",
        company_id=company_id,
        status="success" if result["connected"] else "failure",
        metadata={
            "provider": result["provider"],
            "connected": result["connected"],
            "error_kind": result.get("error_kind"),
        },
    )
    return result


@admin_router.get("/calendar/timeline")
def read_calendar_timeline(
    company_id: int | None = Query(default=None),
    start_date: str | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=14),
) -> dict:
    return get_calendar_timeline(
        company_id=company_id,
        start_date=start_date,
        days=days,
    )


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
            scheduling_profile_key=payload.scheduling_profile_key,
            customer_profile=payload.customer_profile,
            service_name=payload.service_name,
            request_context=payload.request_context,
        )
    except CalendarProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/calendar/bookings")
def read_calendar_bookings(company_id: int | None = Query(default=None)) -> list[dict]:
    return get_calendar_bookings(company_id=company_id)


@admin_router.get("/calendar/queue")
def read_appointment_queue(company_id: int | None = Query(default=None)) -> list[dict]:
    try:
        return list_queue_entries(company_id=company_id)
    except AppointmentQueueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post(
    "/calendar/queue",
    dependencies=[Depends(require_admin_role("operator"))],
)
def create_appointment_queue_entry_endpoint(
    payload: AppointmentQueueEntryPayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    try:
        entry = create_queue_entry(
            company_id=company_id,
            contact_id=payload.contact_id,
            desired_title=payload.desired_title,
            scheduling_profile_key=payload.scheduling_profile_key,
            customer_profile=payload.customer_profile,
            request_summary=payload.request_summary,
            timezone=payload.timezone or settings.calendar_timezone,
            earliest_starts_at=payload.earliest_starts_at,
            latest_starts_at=payload.latest_starts_at,
            preferred_periods=payload.preferred_periods,
            priority_tier=payload.priority_tier,
            priority_score=payload.priority_score,
            accepts_short_notice=payload.accepts_short_notice,
            original_booking_id=payload.original_booking_id,
            source=payload.source,
            notes=payload.notes,
        )
    except AppointmentQueueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="create",
        resource_type="appointment_queue_entry",
        resource_id=str(entry["id"]),
        company_id=company_id,
        metadata={
            "priority_tier": entry["priority_tier"],
            "priority_score": entry["priority_score"],
            "original_booking_id": entry.get("original_booking_id"),
            "scheduling_profile_key": entry.get("scheduling_profile_key"),
            "customer_profile": entry.get("customer_profile"),
        },
    )
    return entry


@admin_router.get("/calendar/queue-offers")
def read_appointment_queue_offers(
    company_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[dict]:
    try:
        return list_queue_offers(company_id=company_id, status=status)
    except AppointmentQueueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
            scheduling_profile_key=payload.scheduling_profile_key,
            customer_profile=payload.customer_profile,
        )
    except CalendarProviderError as exc:
        raise _booking_http_error(exc) from exc

    confirmation_outbound_id: int | None = None
    if payload.request_bot_confirmation:
        target_contact_id = booking.get("contact_id") or payload.contact_id
        if target_contact_id:
            confirmation_message = (
                f"Olá! Queríamos confirmar seu agendamento de {payload.title} "
                f"em {booking.get('starts_at') or payload.starts_at}. "
                "Pode responder *SIM* para confirmar?"
            )
            try:
                confirmation_outbound_id = repository.create_outbound_message(
                    contact_id=str(target_contact_id),
                    company_id=booking.get("company_id") or company_id,
                    message=confirmation_message,
                    source="manual_booking_confirmation",
                    status="pending",
                )
                try:
                    dispatch_outbound_message(
                        confirmation_outbound_id,
                        company_id=booking.get("company_id") or company_id,
                    )
                except OutboundDispatchError as exc:
                    log_event(
                        logger,
                        logging.WARNING,
                        "admin.calendar.booking.confirmation_dispatch_failed",
                        booking_id=booking.get("id"),
                        outbound_message_id=confirmation_outbound_id,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
            except Exception as exc:  # pragma: no cover - defensive logging
                log_event(
                    logger,
                    logging.WARNING,
                    "admin.calendar.booking.confirmation_enqueue_failed",
                    booking_id=booking.get("id"),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
        else:
            log_event(
                logger,
                logging.INFO,
                "admin.calendar.booking.confirmation_skipped_no_contact",
                booking_id=booking.get("id"),
            )

    _record_audit_event(
        request=request,
        action="create",
        resource_type="appointment_booking",
        resource_id=str(booking["id"]),
        company_id=company_id,
        # No PostgreSQL `starts_at`/`ends_at` chegam como datetime; o JSON da
        # auditoria e do evento precisa de texto ISO.
        metadata=jsonable_encoder({
            "provider": booking["provider"],
            "starts_at": booking["starts_at"],
            "ends_at": booking["ends_at"],
            "contact_id": booking.get("contact_id"),
            "scheduling_profile_key": booking.get("scheduling_profile_key"),
            "customer_profile": booking.get("customer_profile"),
            "request_bot_confirmation": payload.request_bot_confirmation,
            "confirmation_outbound_message_id": confirmation_outbound_id,
        }),
    )
    broadcast_event(
        "new_booking",
        jsonable_encoder({
            "booking_id": booking.get("id"),
            "title": booking.get("title"),
            "starts_at": booking.get("starts_at"),
            "contact_id": mask_identifier(str(booking.get("contact_id") or "")) or None,
        }),
        company_id=booking.get("company_id") or company_id,
    )
    if confirmation_outbound_id is not None:
        return {**booking, "confirmation_outbound_message_id": confirmation_outbound_id}
    return booking

@admin_router.post(
    "/calendar/bookings/{booking_id}/reschedule",
    dependencies=[Depends(require_admin_role("operator"))],
)
def reschedule_calendar_booking_endpoint(
    booking_id: int,
    payload: AppointmentBookingReschedulePayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    try:
        booking = (
            reschedule_calendar_booking(
                booking_id=booking_id,
                starts_at=payload.starts_at,
                ends_at=payload.ends_at,
                timezone_name=payload.timezone,
                title=payload.title,
                description=payload.description,
                scheduling_profile_key=payload.scheduling_profile_key,
                customer_profile=payload.customer_profile,
                actor="operator",
            )
            if company_id is None
            else reschedule_calendar_booking(
                booking_id=booking_id,
                starts_at=payload.starts_at,
                ends_at=payload.ends_at,
                timezone_name=payload.timezone,
                title=payload.title,
                description=payload.description,
                scheduling_profile_key=payload.scheduling_profile_key,
                customer_profile=payload.customer_profile,
                company_id=company_id,
                actor="operator",
            )
        )
    except CalendarProviderError as exc:
        raise _booking_http_error(exc) from exc

    _record_audit_event(
        request=request,
        action="reschedule",
        resource_type="appointment_booking",
        resource_id=str(booking_id),
        company_id=booking.get("company_id"),
        metadata=jsonable_encoder({
            "starts_at": booking.get("starts_at"),
            "ends_at": booking.get("ends_at"),
            "scheduling_profile_key": booking.get("scheduling_profile_key"),
            "customer_profile": booking.get("customer_profile"),
            "external_sync_status": booking.get("external_sync", {}).get("status"),
            "policy_decision": booking.get("policy_decision"),
        }),
    )
    return booking


def _booking_http_error(exc: Exception) -> HTTPException:
    """Traduz erros de agenda para HTTP sem expor dados do cliente.

    A fila embrulha o erro de agenda em `AppointmentQueueError`; a causa
    original decide o status.
    """
    cause: BaseException | None = exc
    while isinstance(cause, AppointmentQueueError) and cause.__cause__ is not None:
        cause = cause.__cause__
    if isinstance(cause, BookingNotFoundError):
        return HTTPException(status_code=404, detail=str(cause))
    if isinstance(cause, BookingConflictError):
        return HTTPException(status_code=409, detail=str(cause))
    if isinstance(cause, BookingPolicyError):
        return HTTPException(
            status_code=422,
            detail={"message": str(cause), "outcome": cause.outcome, "reason": cause.reason},
        )
    return HTTPException(status_code=400, detail=str(exc))


@admin_router.post(
    "/calendar/bookings/{booking_id}/cancel",
    dependencies=[Depends(require_admin_role("operator"))],
)
def cancel_calendar_booking_endpoint(
    booking_id: int,
    payload: AppointmentBookingCancellationPayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    policy_decision: dict | None = None
    try:
        if payload.trigger_queue_recovery:
            result = (
                release_booking_slot(booking_id=booking_id, actor="operator")
                if company_id is None
                else release_booking_slot(booking_id=booking_id, company_id=company_id, actor="operator")
            )
        else:
            result = (
                cancel_calendar_booking(booking_id=booking_id, actor="operator")
                if company_id is None
                else cancel_calendar_booking(booking_id=booking_id, company_id=company_id, actor="operator")
            )
            policy_decision = result.get("policy_decision")
            result = {
                "booking": result["booking"],
                "cancelled_reminders": result["cancelled_reminders"],
                "external_sync": result["external_sync"],
                "queue_recovery": {
                    "released_booking_id": booking_id,
                    "candidate_count": 0,
                    "created_offer": None,
                },
            }
    except (AppointmentQueueError, CalendarProviderError) as exc:
        raise _booking_http_error(exc) from exc

    _record_audit_event(
        request=request,
        action="cancel",
        resource_type="appointment_booking",
        resource_id=str(booking_id),
        company_id=result["booking"].get("company_id") if result.get("booking") else None,
        metadata={
            "trigger_queue_recovery": payload.trigger_queue_recovery,
            "reason": payload.reason,
            "actor": "operator",
            "policy_decision": policy_decision,
            "external_sync_status": result.get("external_sync", {}).get("status"),
            "created_offer_id": (
                result["queue_recovery"]["created_offer"]["id"]
                if result.get("queue_recovery", {}).get("created_offer")
                else None
            ),
        },
    )
    return result


@admin_router.post(
    "/calendar/queue-offers/{offer_id}/respond",
    dependencies=[Depends(require_admin_role("operator"))],
)
def respond_to_appointment_queue_offer_endpoint(
    offer_id: int,
    payload: AppointmentQueueOfferResponsePayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    try:
        result = (
            respond_to_queue_offer(
                offer_id=offer_id,
                response_status=payload.response_status,
                wait_minutes=payload.wait_minutes,
                response_notes=payload.response_notes,
            )
            if company_id is None
            else respond_to_queue_offer(
                offer_id=offer_id,
                response_status=payload.response_status,
                wait_minutes=payload.wait_minutes,
                response_notes=payload.response_notes,
                company_id=company_id,
            )
        )
    except AppointmentQueueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CalendarProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="respond",
        resource_type="appointment_queue_offer",
        resource_id=str(offer_id),
        company_id=result["offer"].get("company_id") if result.get("offer") else None,
        metadata={
            "response_status": payload.response_status,
            "new_booking_id": (
                result.get("new_booking", {}).get("id")
                if isinstance(result.get("new_booking"), dict)
                else None
            ),
            "next_offer_id": (
                result.get("next_offer", {}).get("id")
                if isinstance(result.get("next_offer"), dict)
                else None
            ),
        },
    )
    return result


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
    company_id: int | None = Query(default=None),
) -> list[dict]:
    results = (
        dispatch_due_reminders(
            dispatch_immediately=dispatch_immediately,
            channel=channel,
            audience=audience,
        )
        if company_id is None
        else dispatch_due_reminders(
            dispatch_immediately=dispatch_immediately,
            channel=channel,
            audience=audience,
            company_id=company_id,
        )
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
    _broadcast_reminders_dispatched(results)
    return results


@admin_router.get("/messages")
def read_messages(company_id: int | None = Query(default=None)) -> list[dict]:
    return repository.get_messages(company_id=company_id)


@admin_router.get("/contacts")
def read_contacts(
    company_id: int | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    if q:
        return repository.search_contacts(
            query=q,
            company_id=company_id,
            limit=limit,
        )
    contacts = repository.get_contacts(company_id=company_id)
    return contacts[:limit]


@admin_router.get("/contacts/{contact_id}")
def read_contact(
    contact_id: str,
    company_id: int | None = Query(default=None),
) -> dict:
    contact = repository.get_contact(contact_id, company_id=company_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found.")
    return contact


@admin_router.patch(
    "/contacts/{contact_id}",
    dependencies=[Depends(require_admin_role("operator"))],
)
def update_contact_endpoint(
    contact_id: str,
    payload: ContactUpdatePayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    contact = repository.get_contact(contact_id, company_id=company_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found.")

    repository.update_contact(
        contact_id,
        contact_name=payload.contact_name,
        company_id=company_id or contact.get("company_id"),
        customer_profile=payload.customer_profile,
        notes=payload.notes,
    )
    updated_contact = repository.get_contact(contact_id, company_id=company_id)
    if not updated_contact:
        raise HTTPException(status_code=404, detail="Contact not found after update.")

    _record_audit_event(
        request=request,
        action="update",
        resource_type="contact",
        resource_id=contact_id,
        company_id=updated_contact.get("company_id"),
        metadata={
            "customer_profile": updated_contact.get("customer_profile"),
            "has_notes": bool(updated_contact.get("notes")),
        },
    )
    return updated_contact


@admin_router.post(
    "/companies",
    response_model=CompanyResponse,
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_company(payload: CompanyCreatePayload, request: Request) -> CompanyResponse:
    _deny_identity_for_platform_operation(request)
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
def read_companies(request: Request) -> list[CompanyResponse]:
    companies = repository.get_companies()
    company_ids = getattr(request.state, "admin_company_ids", None)
    if company_ids is not None:
        allowed = {int(company_id) for company_id in company_ids}
        companies = [company for company in companies if int(company["id"]) in allowed]
    return [CompanyResponse(**company) for company in companies]


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


@admin_router.get("/company-context/record", response_model=CompanyContextRecordResponse)
def read_company_context_record(
    company_slug: str | None = Query(default=None),
) -> CompanyContextRecordResponse:
    """Return the context plus its revision, for optimistic-concurrency updates."""
    try:
        record = get_company_context_record(company_slug=company_slug)
    except CompanyContextError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CompanyContextRecordResponse(**record)


@admin_router.put(
    "/company-context",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def update_company_context(
    payload: CompanyContextPayload,
    request: Request,
    company_slug: str | None = Query(default=None),
) -> dict:
    from app.services.company_config_service import (
        CompanyConfigError,
        publish_legacy_context_write,
        validate_company_config_input,
    )

    # Mesma validação da versão publicada, antes de gravar o legado: um
    # contexto aceito aqui e recusado na publicação deixaria as duas fontes
    # divergentes.
    try:
        validate_company_config_input(payload.data)
    except CompanyConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        record = save_company_context_record(
            payload.data,
            company_slug=company_slug,
            expected_revision=payload.expected_revision,
            updated_by=getattr(request.state, "admin_actor", None),
        )
    except CompanyContextConflictError as exc:
        _record_audit_event(
            request=request,
            action="update",
            resource_type="company_context",
            resource_id=company_slug or "global",
            status="conflict",
            metadata={
                "company_slug": company_slug or "global",
                "expected_revision": payload.expected_revision,
            },
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CompanyContextError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_event(
        logger,
        logging.INFO,
        "admin.company_context.updated",
        company_slug=company_slug or "global",
        company_id=record["company_id"],
        revision=record["revision"],
    )
    # D12: o runtime lê a versão publicada; a API legada publica versão para
    # continuar tendo efeito imediato.
    try:
        published_version = publish_legacy_context_write(
            company_slug=company_slug,
            context=payload.data,
            author=getattr(request.state, "admin_actor", None),
            change_note="Atualização pela API legada /admin/company-context",
        )
    except CompanyConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record_audit_event(
        request=request,
        action="update",
        resource_type="company_context",
        resource_id=company_slug or "global",
        company_id=record["company_id"],
        metadata={
            "company_slug": company_slug or "global",
            "revision": record["revision"],
            "schema_version": record["schema_version"],
            "top_level_keys": sorted(payload.data.keys()),
            "published_version": published_version["version"] if published_version else None,
        },
    )
    return record["data"]


@admin_router.get("/pending-questions")
def read_pending_questions(company_id: int | None = Query(default=None)) -> list[dict]:
    return repository.get_pending_questions(company_id=company_id)


@admin_router.get("/pending-questions/{question_id}")
def read_pending_question(
    question_id: int,
    company_id: int | None = Query(default=None),
) -> dict:
    pending_question = repository.get_pending_question(question_id, company_id=company_id)
    if not pending_question:
        raise HTTPException(status_code=404, detail="Pending question not found.")
    return pending_question


@admin_router.post(
    "/pending-questions/{question_id}/answer",
    dependencies=[Depends(require_admin_role("operator"))],
)
def answer_pending_question(
    question_id: int,
    payload: PendingQuestionAnswerPayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    pending_question = repository.get_pending_question(question_id, company_id=company_id)
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
        company_id=pending_question.get("company_id"),
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
    updated_pending_question = repository.get_pending_question(
        question_id,
        company_id=pending_question.get("company_id"),
    )
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
    return repository.get_knowledge_entries(company_id=company_id)


@admin_router.get("/knowledge-entries/{knowledge_entry_id}")
def read_knowledge_entry(
    knowledge_entry_id: int,
    company_id: int | None = Query(default=None),
) -> dict:
    knowledge_entry = repository.get_knowledge_entry(knowledge_entry_id, company_id=company_id)
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

    message_example = repository.get_message_example(message_example_id, company_id=company_id)
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
    return repository.get_message_examples(company_id=company_id)


@admin_router.get("/message-examples/{message_example_id}")
def read_message_example(
    message_example_id: int,
    company_id: int | None = Query(default=None),
) -> dict:
    message_example = repository.get_message_example(message_example_id, company_id=company_id)
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


@admin_router.post(
    "/training-import",
    dependencies=[Depends(require_admin_role("operator"))],
)
def import_training_records_endpoint(
    payload: TrainingImportPayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    try:
        result = import_training_records(
            company_id=company_id,
            fmt=payload.format,
            raw_text=payload.raw_text,
            records=payload.records,
            delimiter=payload.delimiter,
            dry_run=payload.dry_run,
            update_contacts=payload.update_contacts,
            create_message_examples=payload.create_message_examples,
            auto_promote_eligible_examples=payload.auto_promote_eligible_examples,
            default_profile_name=payload.default_profile_name,
            default_scenario_type=payload.default_scenario_type,
            default_quality_score=payload.default_quality_score,
            active_default=payload.active_default,
        )
    except (TrainingImportError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="import" if not payload.dry_run else "preview_import",
        resource_type="training_records",
        company_id=company_id,
        metadata={
            "format": payload.format,
            "dry_run": payload.dry_run,
            "parsed_records": result.get("parsed_records"),
            "contacts_to_update": result.get("contacts_to_update"),
            "message_examples_to_create": result.get("message_examples_to_create"),
            "eligible_examples": result.get("eligible_examples"),
        },
    )
    return result


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
def read_outbound_messages(company_id: int | None = Query(default=None)) -> list[dict]:
    return repository.get_outbound_messages(company_id=company_id)


@admin_router.get("/outbound-messages/{outbound_message_id}")
def read_outbound_message(
    outbound_message_id: int,
    company_id: int | None = Query(default=None),
) -> dict:
    outbound_message = repository.get_outbound_message(outbound_message_id, company_id=company_id)
    if not outbound_message:
        raise HTTPException(status_code=404, detail="Outbound message not found.")
    return outbound_message


@admin_router.post(
    "/outbound-messages/{outbound_message_id}/mark-sent",
    dependencies=[Depends(require_admin_role("operator"))],
)
def mark_outbound_message_sent(
    outbound_message_id: int,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    outbound_message = repository.get_outbound_message(outbound_message_id, company_id=company_id)
    if not outbound_message:
        raise HTTPException(status_code=404, detail="Outbound message not found.")

    repository.mark_outbound_message_sent(
        outbound_message_id,
        company_id=outbound_message.get("company_id"),
    )
    updated_outbound_message = repository.get_outbound_message(
        outbound_message_id,
        company_id=outbound_message.get("company_id"),
    )
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
def dispatch_single_outbound_message(
    outbound_message_id: int,
    request: Request,
    channel: str = "generic",
    company_id: int | None = Query(default=None),
) -> dict:
    try:
        result = dispatch_outbound_message(
            outbound_message_id=outbound_message_id,
            channel=channel,
            company_id=company_id,
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
            company_id=company_id,
            metadata={
                "channel": channel,
                "delivery_status": result.get("delivery_status"),
                "company_id": company_id,
            },
        )
        return result
    except OutboundDispatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post(
    "/outbound-messages/dispatch-pending",
    dependencies=[Depends(require_admin_role("operator"))],
)
def dispatch_all_pending_outbound_messages(
    request: Request,
    channel: str = "generic",
    company_id: int | None = Query(default=None),
) -> list[dict]:
    try:
        results = (
            dispatch_pending_outbound_messages(channel=channel)
            if company_id is None
            else dispatch_pending_outbound_messages(channel=channel, company_id=company_id)
        )
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
def create_finetuning_job(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict:
    resolved_company_id = _resolve_company_scope(
        company_id=company_id,
        company_slug=company_slug,
        required=True,
    )
    try:
        result = _get_finetuning_service().create_finetuning_job(company_id=resolved_company_id)
        log_event(
            logger,
            logging.INFO,
            "admin.finetuning.job.created",
            job_id=result.get("job_id"),
            status=result.get("status"),
            base_model=result.get("base_model"),
            company_id=resolved_company_id,
        )
        _record_audit_event(
            request=request,
            action="create",
            resource_type="finetuning_job",
            resource_id=str(result.get("job_id")),
            company_id=resolved_company_id,
            metadata={
                "status": result.get("status"),
                "base_model": result.get("base_model"),
            },
        )
        return result
    except FineTuningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/finetuning/jobs")
def list_finetuning_jobs(
    limit: int = 10,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> list[dict]:
    resolved_company_id = _resolve_company_scope(
        company_id=company_id,
        company_slug=company_slug,
    )
    try:
        results = _get_finetuning_service().list_finetuning_jobs(
            limit=limit,
            company_id=resolved_company_id,
        )
        log_event(
            logger,
            logging.INFO,
            "admin.finetuning.jobs.listed",
            limit=limit,
            results_count=len(results),
            company_id=resolved_company_id,
        )
        return results
    except FineTuningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/finetuning/jobs/{job_id}")
def read_finetuning_job(
    job_id: str,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict:
    resolved_company_id = _resolve_company_scope(
        company_id=company_id,
        company_slug=company_slug,
    )
    try:
        result = _get_finetuning_service().retrieve_finetuning_job(
            job_id,
            company_id=resolved_company_id,
        )
        log_event(
            logger,
            logging.INFO,
            "admin.finetuning.job.read",
            job_id=job_id,
            status=result.get("status"),
            company_id=resolved_company_id,
        )
        return result
    except FineTuningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/finetuning/jobs-db")
def read_persisted_finetuning_jobs(
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> list[dict]:
    resolved_company_id = _resolve_company_scope(
        company_id=company_id,
        company_slug=company_slug,
    )
    return repository.get_finetuning_job_records(company_id=resolved_company_id)


@admin_router.get("/finetuning/jobs-db/{job_id}")
def read_persisted_finetuning_job(
    job_id: str,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict:
    resolved_company_id = _resolve_company_scope(
        company_id=company_id,
        company_slug=company_slug,
    )
    job_record = repository.get_finetuning_job_record(job_id, company_id=resolved_company_id)
    if not job_record:
        raise HTTPException(status_code=404, detail="Persisted fine-tuning job not found.")
    return job_record


# ── Analytics ──────────────────────────────────────────────────────────


@admin_router.get("/analytics/dashboard")
def read_analytics_dashboard(
    company_id: int | None = Query(default=None),
    period: str = Query(default="30d"),
) -> dict:
    from app.services.analytics_service import AnalyticsError, get_no_show_dashboard

    if period not in {"7d", "30d", "90d", "6m", "12m", "all"}:
        raise HTTPException(status_code=400, detail="Invalid period. Use: 7d, 30d, 90d, 6m, 12m, all.")

    try:
        dashboard = get_no_show_dashboard(company_id=company_id, period=period)
        log_event(
            logger,
            logging.INFO,
            "admin.analytics.dashboard.read",
            period=period,
            company_id=company_id,
            total_bookings=dashboard["kpis"]["total_bookings"],
        )
        return dashboard
    except AnalyticsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post(
    "/analytics/bookings/{booking_id}/outcome",
    dependencies=[Depends(require_admin_role("operator"))],
)
def record_booking_outcome_endpoint(
    booking_id: int,
    payload: BookingOutcomePayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    from app.services.analytics_service import AnalyticsError, record_booking_outcome

    try:
        result = (
            record_booking_outcome(
                booking_id,
                outcome=payload.outcome,
                notes=payload.notes,
            )
            if company_id is None
            else record_booking_outcome(
                booking_id,
                outcome=payload.outcome,
                notes=payload.notes,
                company_id=company_id,
            )
        )
        _record_audit_event(
            request=request,
            action="record_outcome",
            resource_type="appointment_booking",
            resource_id=str(booking_id),
            metadata={"outcome": payload.outcome},
        )
        log_event(
            logger,
            logging.INFO,
            "admin.analytics.booking.outcome.recorded",
            booking_id=booking_id,
            outcome=payload.outcome,
        )
        return result
    except AnalyticsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post(
    "/analytics/bookings/{booking_id}/cancellation-reason",
    dependencies=[Depends(require_admin_role("operator"))],
)
def record_cancellation_reason_endpoint(
    booking_id: int,
    payload: CancellationReasonPayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    from app.services.analytics_service import (
        AnalyticsError,
        record_cancellation_reason,
    )

    try:
        result = (
            record_cancellation_reason(booking_id, reason=payload.reason)
            if company_id is None
            else record_cancellation_reason(
                booking_id,
                reason=payload.reason,
                company_id=company_id,
            )
        )
        _record_audit_event(
            request=request,
            action="record_cancellation_reason",
            resource_type="appointment_booking",
            resource_id=str(booking_id),
            metadata={"reason": payload.reason},
        )
        log_event(
            logger,
            logging.INFO,
            "admin.analytics.booking.cancellation_reason.recorded",
            booking_id=booking_id,
        )
        return result
    except AnalyticsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/analytics/trend")
def read_analytics_trend(
    company_id: int | None = Query(default=None),
    months: int = Query(default=6, ge=1, le=24),
) -> list[dict]:
    trend = repository.get_analytics_monthly_trend(
        company_id=company_id,
        months=months,
    )
    log_event(
        logger,
        logging.INFO,
        "admin.analytics.trend.read",
        company_id=company_id,
        months=months,
        data_points=len(trend),
    )
    return trend


@admin_router.get("/analytics/reminder-effectiveness")
def read_reminder_effectiveness(
    company_id: int | None = Query(default=None),
    period: str = Query(default="30d"),
) -> dict:
    from app.services.analytics_service import _resolve_period

    starts_after, starts_before = _resolve_period(period)
    effectiveness = repository.get_analytics_reminder_effectiveness(
        company_id=company_id,
        starts_after=starts_after,
        starts_before=starts_before,
    )
    log_event(
        logger,
        logging.INFO,
        "admin.analytics.reminder_effectiveness.read",
        company_id=company_id,
        period=period,
    )
    return effectiveness


# ── Onboarding ─────────────────────────────────────────────────────────


@admin_router.get("/onboarding/presets")
def read_onboarding_presets() -> dict:
    from app.services.onboarding_service import get_onboarding_presets

    return get_onboarding_presets()


@admin_router.post(
    "/onboarding/setup",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def run_onboarding_setup(
    payload: OnboardingSetupPayload,
    request: Request,
) -> dict:
    _deny_identity_for_platform_operation(request)
    from app.services.onboarding_service import OnboardingError, setup_company

    try:
        result = setup_company(
            company_name=payload.company_name,
            business_type_key=payload.business_type_key,
            segment=payload.segment,
            company_description=payload.company_description,
            phone=payload.phone,
            email=payload.email,
            instagram=payload.instagram,
            website=payload.website,
            timezone=payload.timezone,
            weekday_hours=payload.weekday_hours,
            saturday_hours=payload.saturday_hours,
            sunday_hours=payload.sunday_hours,
            slot_duration_minutes=payload.slot_duration_minutes,
            minimum_notice_minutes=payload.minimum_notice_minutes,
            reminder_offsets_days=payload.reminder_offsets_days,
            brand_style=payload.brand_style,
            custom_rules=payload.custom_rules,
            services=[s.model_dump() for s in payload.services] if payload.services else None,
            calendar_provider=payload.calendar_provider,
            calendar_config=payload.calendar_config,
        )
        _record_audit_event(
            request=request,
            action="onboarding_setup",
            resource_type="company",
            resource_id=str(result["company"]["id"]),
            metadata={
                "company_name": payload.company_name,
                "business_type_key": payload.business_type_key,
            },
        )
        log_event(
            logger,
            logging.INFO,
            "admin.onboarding.setup.completed",
            company_name=payload.company_name,
            business_type=payload.business_type_key,
            company_id=result["company"]["id"],
        )
        return result
    except OnboardingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/onboarding/setup-status/{company_id}")
def read_onboarding_setup_status(company_id: int) -> dict:
    from app.services.onboarding_service import OnboardingError, get_setup_status

    try:
        return get_setup_status(company_id)
    except OnboardingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── Billing ───────────────────────────────────────────────────────────


@admin_router.get("/billing/plans")
def read_billing_plans() -> list[dict]:
    from app.services.billing_service import get_plans

    plans = get_plans()
    log_event(logger, logging.INFO, "admin.billing.plans.read", count=len(plans))
    return plans


@admin_router.post(
    "/billing/checkout",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_stripe_checkout(
    payload: StripeCheckoutPayload,
    request: Request,
    company_id: int = Query(...),
    idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> dict:
    from app.services.stripe_billing_service import (
        StripeBillingError,
        create_checkout_session,
    )

    _require_company_owner_or_break_glass(request)
    normalized_key = str(idempotency_key or "").strip()
    if len(normalized_key) < 16 or len(normalized_key) > 200:
        raise HTTPException(
            status_code=400,
            detail="X-Idempotency-Key must contain 16-200 characters.",
        )
    customer_email = str(
        getattr(request.state, "admin_email", None)
        or payload.customer_email
        or ""
    ).strip()
    if not customer_email:
        raise HTTPException(status_code=400, detail="Customer email is required.")
    try:
        result = create_checkout_session(
            company_id=company_id,
            customer_email=customer_email,
            plan_key=payload.plan_key,
            billing_cycle=payload.billing_cycle,
            idempotency_key=normalized_key,
        )
    except StripeBillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record_audit_event(
        request=request,
        action="create_checkout",
        resource_type="billing_subscription",
        company_id=company_id,
        metadata={
            "plan_key": payload.plan_key,
            "billing_cycle": payload.billing_cycle,
            "status": result["status"],
        },
    )
    return result


@admin_router.post(
    "/billing/portal",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_stripe_portal(
    request: Request,
    company_id: int = Query(...),
    idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> dict:
    from app.services.stripe_billing_service import (
        StripeBillingError,
        create_customer_portal_session,
    )

    _require_company_owner_or_break_glass(request)
    normalized_key = str(idempotency_key or "").strip()
    if len(normalized_key) < 16 or len(normalized_key) > 200:
        raise HTTPException(
            status_code=400,
            detail="X-Idempotency-Key must contain 16-200 characters.",
        )
    try:
        result = create_customer_portal_session(
            company_id=company_id,
            idempotency_key=normalized_key,
        )
    except StripeBillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record_audit_event(
        request=request,
        action="create_portal",
        resource_type="billing_subscription",
        company_id=company_id,
    )
    return result


@admin_router.post(
    "/billing/trial",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def start_billing_trial(
    request: Request,
    company_id: int = Query(...),
) -> dict:
    _deny_identity_for_platform_operation(request)
    from app.services.billing_service import BillingError, start_trial

    try:
        result = start_trial(company_id)
        _record_audit_event(
            request=request,
            action="start_trial",
            resource_type="billing_subscription",
            resource_id=str(result["subscription_id"]),
            company_id=company_id,
            metadata={"plan_key": result["plan_key"], "trial_days": result["trial_days"]},
        )
        log_event(
            logger, logging.INFO, "admin.billing.trial.started",
            company_id=company_id, subscription_id=result["subscription_id"],
        )
        return result
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post(
    "/billing/activate",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def activate_billing_subscription(
    payload: SubscriptionActivatePayload,
    request: Request,
    company_id: int = Query(...),
) -> dict:
    _deny_identity_for_platform_operation(request)
    from app.services.billing_service import BillingError, activate_subscription

    try:
        result = activate_subscription(
            company_id=company_id,
            plan_key=payload.plan_key,
            billing_cycle=payload.billing_cycle,
        )
        _record_audit_event(
            request=request,
            action="activate",
            resource_type="billing_subscription",
            resource_id=str(result["subscription_id"]),
            company_id=company_id,
            metadata={
                "plan_key": payload.plan_key,
                "billing_cycle": payload.billing_cycle,
                "price_monthly": result["price_monthly"],
            },
        )
        log_event(
            logger, logging.INFO, "admin.billing.subscription.activated",
            company_id=company_id, plan_key=payload.plan_key,
            billing_cycle=payload.billing_cycle,
        )
        return result
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post(
    "/billing/cancel",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def cancel_billing_subscription(
    payload: CancelSubscriptionPayload,
    request: Request,
    company_id: int = Query(...),
) -> dict:
    _deny_identity_for_platform_operation(request)
    from app.services.billing_service import BillingError, cancel_subscription

    try:
        result = cancel_subscription(company_id=company_id, reason=payload.reason)
        _record_audit_event(
            request=request,
            action="cancel",
            resource_type="billing_subscription",
            resource_id=str(result["subscription_id"]),
            company_id=company_id,
            metadata={"reason": payload.reason},
        )
        log_event(
            logger, logging.INFO, "admin.billing.subscription.cancelled",
            company_id=company_id, subscription_id=result["subscription_id"],
        )
        return result
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post(
    "/billing/reactivate",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def reactivate_billing_subscription(
    payload: ReactivateSubscriptionPayload,
    request: Request,
    company_id: int = Query(...),
) -> dict:
    _deny_identity_for_platform_operation(request)
    from app.services.billing_service import BillingError, reactivate_subscription

    try:
        result = reactivate_subscription(
            company_id=company_id,
            plan_key=payload.plan_key,
            billing_cycle=payload.billing_cycle,
        )
        _record_audit_event(
            request=request,
            action="reactivate",
            resource_type="billing_subscription",
            resource_id=str(result["subscription_id"]),
            company_id=company_id,
            metadata={
                "plan_key": result["plan_key"],
                "billing_cycle": result["billing_cycle"],
            },
        )
        log_event(
            logger, logging.INFO, "admin.billing.subscription.reactivated",
            company_id=company_id, plan_key=result["plan_key"],
        )
        return result
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post(
    "/billing/change-plan",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def change_billing_plan(
    payload: PlanChangePayload,
    request: Request,
    company_id: int = Query(...),
) -> dict:
    _deny_identity_for_platform_operation(request)
    from app.services.billing_service import BillingError, change_plan

    try:
        result = change_plan(company_id=company_id, new_plan_key=payload.new_plan_key)
        _record_audit_event(
            request=request,
            action="change_plan",
            resource_type="billing_subscription",
            resource_id=str(result["subscription_id"]),
            company_id=company_id,
            metadata={
                "old_plan": result["old_plan"],
                "new_plan": result["new_plan"],
                "price_monthly": result["price_monthly"],
            },
        )
        log_event(
            logger, logging.INFO, "admin.billing.plan.changed",
            company_id=company_id,
            old_plan=result["old_plan"], new_plan=result["new_plan"],
        )
        return result
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/billing/usage")
def read_billing_usage(company_id: int = Query(...)) -> dict:
    from app.services.billing_service import get_usage_dashboard

    dashboard = get_usage_dashboard(company_id)
    log_event(
        logger, logging.INFO, "admin.billing.usage.read",
        company_id=company_id,
        has_subscription=dashboard["has_subscription"],
    )
    return dashboard


@admin_router.post(
    "/billing/usage/record",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def record_billing_usage(
    payload: UsageRecordPayload,
    request: Request,
    company_id: int = Query(...),
) -> dict:
    _deny_identity_for_platform_operation(request)
    from app.services.billing_service import BillingError, record_usage

    try:
        result = record_usage(
            company_id=company_id,
            metric=payload.metric,
            quantity=payload.quantity,
        )
        _record_audit_event(
            request=request,
            action="record_usage",
            resource_type="billing_usage",
            company_id=company_id,
            status="success" if result.get("recorded") else "skipped",
            metadata={
                "metric": payload.metric,
                "quantity": payload.quantity,
                "recorded": bool(result.get("recorded")),
                "reason": result.get("reason"),
            },
        )
        log_event(
            logger, logging.INFO, "admin.billing.usage.recorded",
            company_id=company_id, metric=payload.metric, quantity=payload.quantity,
        )
        return result
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/billing/check-limit")
def check_billing_limit(
    company_id: int = Query(...),
    metric: str = Query(...),
) -> dict:
    from app.services.billing_service import check_limit

    return check_limit(company_id=company_id, metric=metric)


# ── WhatsApp channel bindings ─────────────────────────────────────────


@admin_router.get("/whatsapp/channel-status")
def read_whatsapp_channel_status(
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    scope = get_authorized_scope(request)
    resolved_company_id = scope.company_id if scope is not None else None
    if resolved_company_id is None:
        resolved_company_id = _resolve_company_scope(company_id=company_id, required=True)
    if resolved_company_id is None:  # pragma: no cover - guarded by required=True
        raise HTTPException(status_code=400, detail="A company scope is required.")
    return get_whatsapp_channel_status(int(resolved_company_id))


@admin_router.post(
    "/whatsapp/test-connection",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def test_whatsapp_connection_endpoint(
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    scope = get_authorized_scope(request)
    resolved_company_id = scope.company_id if scope is not None else None
    if resolved_company_id is None:
        resolved_company_id = _resolve_company_scope(company_id=company_id, required=True)
    if resolved_company_id is None:  # pragma: no cover - guarded by required=True
        raise HTTPException(status_code=400, detail="A company scope is required.")
    result = check_whatsapp_connection(int(resolved_company_id))
    _record_audit_event(
        request=request,
        action="connection_test",
        resource_type="whatsapp_channel",
        company_id=int(resolved_company_id),
        status="success" if result["connected"] else "failure",
        metadata={
            "provider": result["provider"],
            "connected": result["connected"],
            "error_kind": result.get("error_kind"),
            "provider_code": result.get("provider_code"),
        },
    )
    return result


@admin_router.get("/whatsapp/bindings", response_model=list[WhatsAppBindingResponse])
def read_whatsapp_bindings(
    company_id: int | None = Query(default=None),
    active_only: bool = Query(default=False),
) -> list[WhatsAppBindingResponse]:
    bindings = repository.get_whatsapp_bindings(company_id=company_id, active_only=active_only)
    return [WhatsAppBindingResponse(**binding) for binding in bindings]


@admin_router.put(
    "/whatsapp/bindings",
    response_model=WhatsAppBindingResponse,
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def upsert_whatsapp_binding_endpoint(
    payload: WhatsAppBindingPayload,
    request: Request,
) -> WhatsAppBindingResponse:
    """Bind a Meta phone_number_id to a company (superadmin only)."""
    company = repository.get_company(payload.company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    binding = repository.upsert_whatsapp_binding(
        company_id=payload.company_id,
        phone_number_id=payload.phone_number_id,
        display_phone_number=payload.display_phone_number,
        active=payload.active,
        updated_by=getattr(request.state, "admin_actor", None),
    )
    if binding is None:
        raise HTTPException(
            status_code=409,
            detail="This WhatsApp phone number is already actively bound to another company.",
        )

    _record_audit_event(
        request=request,
        action="upsert",
        resource_type="whatsapp_channel_binding",
        resource_id=str(binding["id"]),
        company_id=payload.company_id,
        metadata={
            # Identifiers only: no access token or app secret is stored or logged.
            "phone_number_id": payload.phone_number_id,
            "active": payload.active,
        },
    )
    log_event(
        logger,
        logging.INFO,
        "admin.whatsapp.binding.upserted",
        company_id=payload.company_id,
        binding_id=binding["id"],
        active=payload.active,
    )
    return WhatsAppBindingResponse(**binding)


@admin_router.post(
    "/whatsapp/bindings/{binding_id}/state",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def set_whatsapp_binding_state(
    binding_id: int,
    payload: WhatsAppBindingStatePayload,
    request: Request,
    company_id: int | None = Query(default=None),
) -> dict:
    if company_id is not None:
        bindings = repository.get_whatsapp_bindings(company_id=company_id, active_only=False)
        if not any(int(binding["id"]) == binding_id for binding in bindings):
            raise HTTPException(status_code=404, detail="WhatsApp channel binding not found.")
    result = repository.set_whatsapp_binding_active(
        binding_id,
        active=payload.active,
        updated_by=getattr(request.state, "admin_actor", None),
    )
    if result == "not_found":
        raise HTTPException(status_code=404, detail="WhatsApp channel binding not found.")
    if result == "conflict":
        # Reactivating a historical binding whose number moved to another tenant.
        _record_audit_event(
            request=request,
            action="activate",
            resource_type="whatsapp_channel_binding",
            resource_id=str(binding_id),
            status="conflict",
            metadata={"active": payload.active},
        )
        raise HTTPException(
            status_code=409,
            detail="This WhatsApp phone number is already actively bound to another company.",
        )

    _record_audit_event(
        request=request,
        action="activate" if payload.active else "deactivate",
        resource_type="whatsapp_channel_binding",
        resource_id=str(binding_id),
        metadata={"active": payload.active},
    )
    return {"binding_id": binding_id, "active": payload.active}


# ── Simulator ─────────────────────────────────────────────────────────


@admin_router.post(
    "/simulator/messages",
    response_model=IncomingMessageResponse,
    dependencies=[Depends(require_admin_role("operator"))],
)
def simulate_incoming_message(
    payload: IncomingMessageRequest,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> IncomingMessageResponse:
    """Authenticated replacement for calling the public ingress from the platform UI.

    The company is resolved server-side and must exist; the caller cannot push a
    simulated conversation into an arbitrary tenant.
    """
    normalized_message = payload.message.strip()
    if not normalized_message:
        raise HTTPException(status_code=400, detail="message must not be blank.")

    resolved_company_id = None
    resolved_company_slug = None
    if company_id is not None or company_slug:
        resolved_company_id = _resolve_company_scope(
            company_id=company_id,
            company_slug=company_slug,
            required=True,
        )
        if resolved_company_id is None:  # Defensive guard for static and runtime safety.
            raise HTTPException(status_code=400, detail="Company scope could not be resolved.")
        company = repository.get_company(resolved_company_id)
        resolved_company_slug = str(company["slug"]) if company else None

    result = process_incoming_message(
        contact_id=payload.contact_id,
        contact_name=payload.contact_name,
        message=normalized_message,
        company_id=resolved_company_id,
        company_slug=resolved_company_slug,
    )
    _record_audit_event(
        request=request,
        action="simulate_message",
        resource_type="conversation",
        resource_id=mask_identifier(payload.contact_id),
        company_id=result.get("company_id"),
        metadata={
            "classification": result.get("classification"),
            "action": result.get("action"),
            "handoff_required": result.get("handoff_required"),
        },
    )
    log_event(
        logger,
        logging.INFO,
        "admin.simulator.message.processed",
        company_id=result.get("company_id"),
        classification=result.get("classification"),
        handoff_required=result.get("handoff_required"),
    )
    publish_incoming_message_event(result, contact_id=payload.contact_id)
    return IncomingMessageResponse(**result)


@admin_router.get("/inbound/jobs")
def read_inbound_jobs(
    company_id: int = Query(..., gt=0),
    status: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    from app.storage.inbound_repository import list_jobs
    try:
        return {"jobs": list_jobs(company_id, status=status, limit=limit)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@admin_router.post("/inbound/jobs/{job_id}/resolve", dependencies=[Depends(require_admin_role("operator"))])
def resolve_inbound_review(job_id: int, request: Request, company_id: int = Query(..., gt=0)) -> dict:
    """Acknowledge manual handling; never replay uncertain external effects.

    A resposta do bot vinculada ao job e ainda não enviada sai de circulação na
    mesma transação: desbloquear a conversa não pode liberar uma resposta velha,
    fora de ordem e talvez com humano no atendimento (revisão P06).
    """
    from datetime import UTC, datetime, timedelta

    from app.core.config import settings
    from app.storage.inbound_repository import now_iso
    from app.storage.sql_support import transaction

    now = datetime.now(UTC)
    outbound_outcome = "none"
    with transaction() as cursor:
        cursor.execute(
            "SELECT outbound_message_id FROM inbound_jobs WHERE id = ? AND company_id = ? AND status = 'review'",
            (job_id, company_id),
        )
        job = cursor.fetchone()
        if job is None:
            raise HTTPException(status_code=409, detail="Job not found in this company or not awaiting review.")
        if job["outbound_message_id"] is not None:
            cursor.execute(
                "SELECT status, claimed_at FROM outbound_messages WHERE id = ? AND company_id = ?",
                (job["outbound_message_id"], company_id),
            )
            outbound = cursor.fetchone()
            status = str(outbound["status"]) if outbound else "missing"
            stale_processing = False
            if status == "processing":
                try:
                    claimed_at = datetime.fromisoformat(str(outbound["claimed_at"]))
                    if claimed_at.tzinfo is None:
                        claimed_at = claimed_at.replace(tzinfo=UTC)
                    stale_processing = now - claimed_at > timedelta(seconds=max(30, settings.outbound_claim_ttl_seconds))
                except (TypeError, ValueError):
                    stale_processing = True
                if not stale_processing:
                    raise HTTPException(
                        status_code=409,
                        detail="O envio desta resposta ainda está em andamento; tente de novo em alguns minutos.",
                    )
            if status in {"pending", "retry"} or stale_processing:
                cursor.execute(
                    """
                    UPDATE outbound_messages
                    SET status = 'dead_letter', last_error = ?, failed_at = ?, claim_token = NULL, next_attempt_at = NULL
                    WHERE id = ? AND company_id = ? AND status = ?
                    """,
                    ("permanent:-:blocked:resolved_manually", now.isoformat(), job["outbound_message_id"], company_id,
                     status),
                )
                outbound_outcome = "withdrawn"
            else:
                outbound_outcome = status
        cursor.execute(
            "UPDATE inbound_jobs SET status = 'completed', updated_at = ? WHERE id = ? AND company_id = ? AND status = 'review'",
            (now_iso(), job_id, company_id),
        )
    _record_audit_event(request=request, action="inbound.review.resolved", resource_type="inbound_job",
                        resource_id=str(job_id), company_id=company_id, metadata={"outbound": outbound_outcome})
    return {"resolved": True, "replayed": False, "outbound": outbound_outcome}


# ── posse humana da conversa e resumo operacional (P06) ────────────────


@admin_router.get("/conversations/ownership")
def read_conversation_ownerships(company_id: int = Query(..., gt=0)) -> dict:
    from app.storage.ownership_repository import list_human_owned

    return {"conversations": list_human_owned(company_id=company_id)}


def _normalized_contact_id(contact_id: str) -> str:
    # "+55 (11) 90000-0001" e "5511900000001" são o mesmo contato do WhatsApp;
    # identificador de outro canal (sem formato de telefone) fica como veio.
    value = (contact_id or "").strip()
    if value and all(char in "0123456789 +-()." for char in value):
        value = "".join(char for char in value if char in "0123456789")
    if not value:
        raise HTTPException(status_code=422, detail="Informe o telefone do contato com DDI e DDD.")
    return value


def _ownership_user_id(request: Request) -> int:
    # Posse é de uma pessoa: token de serviço/break-glass não assume conversa.
    user_id = getattr(request.state, "admin_user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=422,
            detail="Assumir ou devolver conversa exige um usuário identificado por sessão.",
        )
    return int(user_id)


@admin_router.post(
    "/conversations/{contact_id}/take",
    dependencies=[Depends(require_admin_role("operator"))],
)
def take_conversation(
    contact_id: str,
    request: Request,
    payload: ConversationOwnershipRequest | None = None,
    company_id: int = Query(..., gt=0),
) -> dict:
    from app.storage.ownership_repository import take_ownership

    contact_id = _normalized_contact_id(contact_id)
    result = take_ownership(
        company_id=company_id,
        contact_id=contact_id,
        user_id=_ownership_user_id(request),
        reason=payload.reason if payload else None,
    )
    _record_audit_event(
        request=request,
        action="conversation.ownership.take",
        resource_type="conversation",
        resource_id=mask_identifier(contact_id),
        company_id=company_id,
        status="conflict" if result.outcome == "owned_by_other" else "success",
        metadata={"outcome": result.outcome},
    )
    if result.outcome == "owned_by_other":
        raise HTTPException(status_code=409, detail="A conversa já está com outro operador.")
    return {"outcome": result.outcome, "ownership": result.ownership}


@admin_router.post(
    "/conversations/{contact_id}/release",
    dependencies=[Depends(require_admin_role("operator"))],
)
def release_conversation(
    contact_id: str,
    request: Request,
    payload: ConversationOwnershipRequest | None = None,
    company_id: int = Query(..., gt=0),
) -> dict:
    from app.storage.ownership_repository import release_ownership

    contact_id = _normalized_contact_id(contact_id)
    force = bool(payload and payload.force)
    if force and getattr(request.state, "admin_role", None) != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="Somente o administrador da empresa devolve a conversa de outro operador.",
        )
    result = release_ownership(
        company_id=company_id,
        contact_id=contact_id,
        user_id=_ownership_user_id(request),
        allow_other_owner=force,
    )
    _record_audit_event(
        request=request,
        action="conversation.ownership.release",
        resource_type="conversation",
        resource_id=mask_identifier(contact_id),
        company_id=company_id,
        status="conflict" if result.outcome == "owned_by_other" else "success",
        metadata={"outcome": result.outcome, "forced": force},
    )
    if result.outcome == "owned_by_other":
        raise HTTPException(status_code=409, detail="A conversa está com outro operador.")
    return {"outcome": result.outcome, "ownership": result.ownership}


@admin_router.get("/operations/summary")
def read_operations_summary(company_id: int = Query(..., gt=0)) -> dict:
    from app.services.operations_summary_service import get_operations_summary

    return get_operations_summary(company_id)
