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


class AppointmentBookingPayload(BaseModel):
    contact_id: str | None = None
    title: str = Field(..., min_length=1)
    starts_at: str = Field(..., min_length=10)
    ends_at: str = Field(..., min_length=10)
    timezone: str | None = None
    description: str | None = None


class InternalReminderPayload(BaseModel):
    owner_contact_id: str = Field(..., min_length=1)
    message_text: str = Field(..., min_length=1)
    timezone: str | None = None
