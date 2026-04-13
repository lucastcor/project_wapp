from typing import Any

from pydantic import BaseModel, Field


class IncomingMessageRequest(BaseModel):
    contact_id: str = Field(..., min_length=1)
    contact_name: str | None = None
    message: str = Field(..., min_length=1)


class IncomingMessageResponse(BaseModel):
    contact_id: str
    classification: str
    classification_reason: str
    classification_confidence: float
    reply: str
    source: str
    auto_reply: bool
    action: str
    priority: str
    pipeline_stage: str
    next_action: str
    lead_temperature: str
    handoff_required: bool
    pending_question_id: int | None
    question_type: str | None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Mensagem enviada pelo usuário.")


class ChatResponse(BaseModel):
    reply: str
    source: str


class PlatformSessionLoginPayload(BaseModel):
    admin_token: str | None = None
    admin_actor: str | None = None


class PlatformSessionResponse(BaseModel):
    authenticated: bool
    role: str
    actor: str
    auth_mode: str
    expires_at: int


class HealthResponse(BaseModel):
    status: str


class WhatsAppWebhookRequest(BaseModel):
    event: str = Field(..., min_length=1)
    from_number: str = Field(..., min_length=1)
    from_name: str | None = None
    text: str = Field(..., min_length=1)


class WhatsAppWebhookResponse(BaseModel):
    contact_id: str
    classification: str
    classification_reason: str
    classification_confidence: float
    reply: str
    source: str
    auto_reply: bool
    action: str
    priority: str
    pipeline_stage: str
    next_action: str
    lead_temperature: str
    handoff_required: bool
    pending_question_id: int | None
    question_type: str | None


class CompanyContextPayload(BaseModel):
    data: dict[str, Any]


class ContactUpdatePayload(BaseModel):
    contact_name: str | None = None
    customer_profile: str | None = None
    notes: str | None = None


class PendingQuestionAnswerPayload(BaseModel):
    owner_answer: str = Field(..., min_length=1)
    resolution_type: str | None = None
    customer_followup_question: str | None = None
    applies_globally: bool = False
    knowledge_action: str | None = None


class CompanyCreatePayload(BaseModel):
    slug: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    business_type: str | None = None
    active: bool = True


class CompanyResponse(BaseModel):
    id: int
    slug: str
    name: str
    business_type: str | None
    active: int
    created_at: str
    updated_at: str | None


# Schemas for approved message examples (used in style retrieval and fine-tuning preparation)
class MessageExampleCreatePayload(BaseModel):
    profile_name: str = Field(..., min_length=1)
    scenario_type: str = Field(..., min_length=1)
    customer_message: str = Field(..., min_length=1)
    approved_reply: str = Field(..., min_length=1)
    style_notes: str | None = None
    quality_score: float = 0
    used_for_training: bool = False
    active: bool = True


class MessageExampleResponse(BaseModel):
    id: int
    profile_name: str
    scenario_type: str
    customer_message: str
    approved_reply: str
    style_notes: str | None
    quality_score: float
    used_for_training: int
    active: int
    created_at: str


class TrainingImportPayload(BaseModel):
    format: str = "csv"
    raw_text: str | None = None
    records: list[dict[str, Any]] = Field(default_factory=list)
    delimiter: str = ","
    dry_run: bool = True
    update_contacts: bool = True
    create_message_examples: bool = True
    auto_promote_eligible_examples: bool = False
    default_profile_name: str = "default"
    default_scenario_type: str = "general"
    default_quality_score: float = 7.0
    active_default: bool = True


class CalendarIntegrationPayload(BaseModel):
    provider: str = Field(..., min_length=1)
    external_calendar_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


class CalendarAvailabilityQueryPayload(BaseModel):
    start_date: str = Field(..., min_length=10)
    end_date: str = Field(..., min_length=10)
    duration_minutes: int = Field(..., gt=0)
    limit: int = Field(default=12, gt=0, le=100)
    scheduling_profile_key: str | None = None
    customer_profile: str | None = None
    service_name: str | None = None
    request_context: str | None = None


class AppointmentBookingPayload(BaseModel):
    contact_id: str | None = None
    title: str = Field(..., min_length=1)
    starts_at: str = Field(..., min_length=10)
    ends_at: str = Field(..., min_length=10)
    timezone: str | None = None
    description: str | None = None
    scheduling_profile_key: str | None = None
    customer_profile: str | None = None
    request_bot_confirmation: bool = False


class AppointmentBookingReschedulePayload(BaseModel):
    starts_at: str = Field(..., min_length=10)
    ends_at: str = Field(..., min_length=10)
    timezone: str | None = None
    title: str | None = None
    description: str | None = None
    scheduling_profile_key: str | None = None
    customer_profile: str | None = None


class AppointmentBookingCancellationPayload(BaseModel):
    trigger_queue_recovery: bool = True
    reason: str | None = None


class AppointmentQueueEntryPayload(BaseModel):
    contact_id: str = Field(..., min_length=1)
    desired_title: str = Field(..., min_length=1)
    scheduling_profile_key: str | None = None
    customer_profile: str | None = None
    request_summary: str | None = None
    timezone: str | None = None
    earliest_starts_at: str | None = None
    latest_starts_at: str | None = None
    preferred_periods: list[str] = Field(default_factory=list)
    priority_tier: str = "normal"
    priority_score: int = 0
    accepts_short_notice: bool = True
    original_booking_id: int | None = None
    source: str = "manual"
    notes: str | None = None


class AppointmentQueueOfferResponsePayload(BaseModel):
    response_status: str = Field(..., min_length=1)
    wait_minutes: int | None = Field(default=None, gt=0)
    response_notes: str | None = None


class InternalReminderPayload(BaseModel):
    owner_contact_id: str = Field(..., min_length=1)
    message_text: str = Field(..., min_length=1)
    timezone: str | None = None
