from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# Inbound payload size limits. WhatsApp text bodies cap at 4096 characters and
# E.164 identifiers never exceed a handful of digits, so anything larger is abuse.
MAX_CONTACT_ID_LENGTH = 64
MAX_CONTACT_NAME_LENGTH = 200
MAX_MESSAGE_LENGTH = 4096
MAX_PROVIDER_MESSAGE_ID_LENGTH = 200
MAX_WEBHOOK_EVENT_LENGTH = 100


class IncomingMessageRequest(BaseModel):
    contact_id: str = Field(..., min_length=1, max_length=MAX_CONTACT_ID_LENGTH)
    contact_name: str | None = Field(default=None, max_length=MAX_CONTACT_NAME_LENGTH)
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)


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
    email: str | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, min_length=1, max_length=256)


class PlatformSessionResponse(BaseModel):
    authenticated: bool
    role: str
    actor: str
    auth_mode: str
    expires_at: int
    user_id: int | None = None
    email: str | None = None
    memberships: list[dict[str, Any]] = Field(default_factory=list)


class IdentityRegistrationPayload(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=12, max_length=256)
    display_name: str = Field(..., min_length=1, max_length=120)
    company_name: str = Field(..., min_length=1, max_length=160)
    company_slug: str = Field(..., min_length=3, max_length=63)
    business_type: str | None = Field(default=None, max_length=120)


class IdentityInvitationCreatePayload(BaseModel):
    company_id: int = Field(..., gt=0)
    email: str = Field(..., min_length=3, max_length=254)
    role: str = Field(default="operator")


class IdentityInvitationAcceptPayload(BaseModel):
    token: str = Field(..., min_length=20, max_length=512)
    password: str = Field(..., min_length=12, max_length=256)
    display_name: str = Field(..., min_length=1, max_length=120)


class IdentityMembershipUpdatePayload(BaseModel):
    role: str | None = None
    active: bool | None = None


class PasswordResetRequestPayload(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


class PasswordResetConfirmPayload(BaseModel):
    token: str = Field(..., min_length=20, max_length=512)
    password: str = Field(..., min_length=12, max_length=256)


class HealthResponse(BaseModel):
    status: str


class WhatsAppWebhookRequest(BaseModel):
    event: str = Field(..., min_length=1, max_length=MAX_WEBHOOK_EVENT_LENGTH)
    from_number: str = Field(..., min_length=1, max_length=MAX_CONTACT_ID_LENGTH)
    from_name: str | None = Field(default=None, max_length=MAX_CONTACT_NAME_LENGTH)
    text: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)


class NormalizedWhatsAppPayload(BaseModel):
    """Validated shape of the internal (non-Meta) WhatsApp webhook payload."""

    event: str = Field(default="message.received", min_length=1, max_length=MAX_WEBHOOK_EVENT_LENGTH)
    from_number: str = Field(..., min_length=1, max_length=MAX_CONTACT_ID_LENGTH)
    from_name: str | None = Field(default=None, max_length=MAX_CONTACT_NAME_LENGTH)
    text: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)
    provider_message_id: str | None = Field(default=None, max_length=MAX_PROVIDER_MESSAGE_ID_LENGTH)
    # `text` para mensagem digitada; `interactive`/`button` para resposta a
    # botão/lista (o título vira `text`, o id vai em `reply_id`); `unsupported`
    # para áudio, imagem e demais tipos sem texto, que recebem orientação.
    content_type: str = Field(default="text", pattern=r"^(text|interactive|button|unsupported)$")
    reply_id: str | None = Field(default=None, max_length=256)
    media_type: str | None = Field(default=None, max_length=32)
    context_message_id: str | None = Field(default=None, max_length=MAX_PROVIDER_MESSAGE_ID_LENGTH)


class ConversationOwnershipRequest(BaseModel):
    """Assumir ou devolver uma conversa (P06). `force` só vale para administrador."""

    reason: str | None = Field(default=None, max_length=200)
    force: bool = False


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
    # Optimistic concurrency token; when provided it must match the stored revision.
    expected_revision: int | None = Field(default=None, ge=0)


class CompanyContextRecordResponse(BaseModel):
    company_id: int | None
    company_slug: str | None
    revision: int
    schema_version: int
    source: str
    created_at: datetime | None
    updated_at: datetime | None
    updated_by: str | None
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
    created_at: datetime
    updated_at: datetime | None


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
    created_at: datetime


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


class BookingOutcomePayload(BaseModel):
    outcome: str = Field(..., pattern="^(attended|no_show|late_arrival)$")
    notes: str | None = None


class CancellationReasonPayload(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class OnboardingServiceItem(BaseModel):
    name: str = Field(..., min_length=1)
    duration_minutes: int | None = None
    price: float | None = None
    description: str | None = None
    category: str | None = None


class OnboardingSetupPayload(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=200)
    business_type_key: str = Field(..., min_length=1)
    segment: str | None = None
    company_description: str | None = None
    phone: str | None = None
    email: str | None = None
    instagram: str | None = None
    website: str | None = None
    timezone: str = "America/Sao_Paulo"
    weekday_hours: str = "08:00-18:00"
    saturday_hours: str = "fechado"
    sunday_hours: str = "fechado"
    slot_duration_minutes: int = Field(default=60, ge=10, le=480)
    minimum_notice_minutes: int = Field(default=120, ge=0, le=10080)
    reminder_offsets_days: list[int] | None = None
    brand_style: str | None = None
    custom_rules: list[str] | None = None
    services: list[OnboardingServiceItem] | None = None
    calendar_provider: str | None = None
    calendar_config: dict[str, Any] | None = None


class SubscriptionActivatePayload(BaseModel):
    plan_key: str = Field(..., min_length=1)
    billing_cycle: str = Field(default="monthly", pattern="^(monthly|annual)$")


class PlanChangePayload(BaseModel):
    new_plan_key: str = Field(..., min_length=1)


class CancelSubscriptionPayload(BaseModel):
    reason: str | None = None


class ReactivateSubscriptionPayload(BaseModel):
    plan_key: str | None = None
    billing_cycle: str = Field(default="monthly", pattern="^(monthly|annual)$")


class UsageRecordPayload(BaseModel):
    metric: str = Field(..., min_length=1)
    quantity: int = Field(default=1, ge=1)


class StripeCheckoutPayload(BaseModel):
    plan_key: str = Field(..., min_length=1, max_length=64)
    billing_cycle: str = Field(default="monthly", pattern="^(monthly|annual)$")
    customer_email: str | None = Field(default=None, max_length=254)


class WhatsAppBindingPayload(BaseModel):
    """Bind a Meta business number to a company. Never carries tokens/secrets."""

    company_id: int = Field(..., gt=0)
    phone_number_id: str = Field(..., min_length=1, max_length=64)
    display_phone_number: str | None = Field(default=None, max_length=32)
    active: bool = True


class WhatsAppBindingStatePayload(BaseModel):
    active: bool


class WhatsAppBindingResponse(BaseModel):
    id: int
    company_id: int
    company_slug: str | None = None
    phone_number_id: str
    display_phone_number: str | None
    active: int
    created_at: datetime | None
    updated_at: datetime | None
    updated_by: str | None
