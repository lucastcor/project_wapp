import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from app.api.schemas import (
    AppointmentBookingPayload,
    AppointmentBookingCancellationPayload,
    AppointmentBookingReschedulePayload,
    AppointmentQueueEntryPayload,
    AppointmentQueueOfferResponsePayload,
    CalendarAvailabilityQueryPayload,
    CalendarIntegrationPayload,
    CompanyContextPayload,
    CompanyCreatePayload,
    CompanyResponse,
    ContactUpdatePayload,
    InternalReminderPayload,
    MessageExampleCreatePayload,
    PendingQuestionAnswerPayload,
    TrainingImportPayload,
)
from app.api.admin_security import (
    build_require_admin_access,
    build_require_admin_role,
)
from app.api.admin_support import build_prometheus_metrics, record_audit_event

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
from app.services.training_import_service import TrainingImportError, import_training_records

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
    cancel_calendar_booking,
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
from app.services.reminder_service import (
    ReminderServiceError,
    create_internal_reminder,
    dispatch_due_reminders,
    list_reminders,
)
from app.services.appointment_queue_service import (
    AppointmentQueueError,
    create_queue_entry,
    list_queue_entries,
    list_queue_offers,
    release_booking_slot,
    respond_to_queue_offer,
)

from app.core.config import settings
from app.services.finetuning_service import FineTuningError, FineTuningService
from app.core.logging import get_logger, log_event

repository = RuntimeRepository()
logger = get_logger(__name__)
_build_prometheus_metrics = build_prometheus_metrics


def _get_finetuning_service() -> FineTuningService:
    return FineTuningService()


def _get_repository() -> RuntimeRepository:
    return repository


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
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
                    dispatch_outbound_message(confirmation_outbound_id)
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
        metadata={
            "provider": booking["provider"],
            "starts_at": booking["starts_at"],
            "ends_at": booking["ends_at"],
            "contact_id": booking.get("contact_id"),
            "scheduling_profile_key": booking.get("scheduling_profile_key"),
            "customer_profile": booking.get("customer_profile"),
            "request_bot_confirmation": payload.request_bot_confirmation,
            "confirmation_outbound_message_id": confirmation_outbound_id,
        },
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
) -> dict:
    try:
        booking = reschedule_calendar_booking(
            booking_id=booking_id,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            timezone_name=payload.timezone,
            title=payload.title,
            description=payload.description,
            scheduling_profile_key=payload.scheduling_profile_key,
            customer_profile=payload.customer_profile,
        )
    except CalendarProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="reschedule",
        resource_type="appointment_booking",
        resource_id=str(booking_id),
        company_id=booking.get("company_id"),
        metadata={
            "starts_at": booking.get("starts_at"),
            "ends_at": booking.get("ends_at"),
            "scheduling_profile_key": booking.get("scheduling_profile_key"),
            "customer_profile": booking.get("customer_profile"),
            "external_sync_status": booking.get("external_sync", {}).get("status"),
        },
    )
    return booking


@admin_router.post(
    "/calendar/bookings/{booking_id}/cancel",
    dependencies=[Depends(require_admin_role("operator"))],
)
def cancel_calendar_booking_endpoint(
    booking_id: int,
    payload: AppointmentBookingCancellationPayload,
    request: Request,
) -> dict:
    try:
        if payload.trigger_queue_recovery:
            result = release_booking_slot(booking_id=booking_id)
        else:
            result = cancel_calendar_booking(booking_id=booking_id)
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="cancel",
        resource_type="appointment_booking",
        resource_id=str(booking_id),
        company_id=result["booking"].get("company_id") if result.get("booking") else None,
        metadata={
            "trigger_queue_recovery": payload.trigger_queue_recovery,
            "reason": payload.reason,
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
) -> dict:
    try:
        result = respond_to_queue_offer(
            offer_id=offer_id,
            response_status=payload.response_status,
            wait_minutes=payload.wait_minutes,
            response_notes=payload.response_notes,
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
    contacts = repository.get_contacts()
    if company_id is not None:
        contacts = [contact for contact in contacts if contact.get("company_id") == company_id]
    return contacts[:limit]


@admin_router.get("/contacts/{contact_id}")
def read_contact(contact_id: str) -> dict:
    contact = repository.get_contact(contact_id)
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
    contact = repository.get_contact(contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found.")

    repository.update_contact(
        contact_id,
        contact_name=payload.contact_name,
        company_id=company_id or contact.get("company_id"),
        customer_profile=payload.customer_profile,
        notes=payload.notes,
    )
    updated_contact = repository.get_contact(contact_id)
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
