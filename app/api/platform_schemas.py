"""Schemas HTTP dos subsistemas da plataforma multiempresa.

Validação de taxonomia acontece aqui, na borda: valor desconhecido é
rejeitado antes de chegar ao domínio.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.taxonomy import TaxonomyError, normalize_or_raise


def _normalize_dimension(dimension: str, value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return normalize_or_raise(dimension, value)
    except TaxonomyError as exc:
        raise ValueError(str(exc)) from exc


class ChannelBindingPayload(BaseModel):
    provider: str = Field(default="whatsapp", min_length=1)
    external_id: str = Field(..., min_length=1)
    channel_type: str = Field(default="whatsapp", min_length=1)
    label: str | None = None
    active: bool = True


class WhatsAppTemplatePayload(BaseModel):
    """Template já aprovado na Meta, cadastrado pela empresa (P07).

    O painel não submete template para aprovação: registra nome, idioma e
    situação exatamente como estão no WhatsApp Manager.
    """

    purpose: str = Field(..., min_length=1, max_length=40)
    name: str = Field(..., min_length=1, max_length=512)
    language_code: str = Field(..., pattern=r"^[a-z]{2,3}(_[A-Z]{2})?$")
    category: str = Field(default="utility", min_length=1, max_length=40)
    status: str = Field(..., min_length=1, max_length=20)
    parameter_count: int = Field(default=0, ge=0, le=10)
    active: bool = True


class ContactConsentPayload(BaseModel):
    status: str = Field(..., pattern=r"^(granted|revoked)$")
    evidence_ref: str | None = Field(default=None, max_length=200)


class CompanyConfigPublishPayload(BaseModel):
    version_id: int = Field(..., gt=0)


class CompanyCapabilitiesPayload(BaseModel):
    context: dict[str, Any]
    capabilities: dict[str, Any] | None = None
    policies: dict[str, Any] | None = None
    change_note: str | None = None
    publish: bool = True


class KnowledgeDocumentPayload(BaseModel):
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    document_type: str = Field(default="general_faq", min_length=1)
    external_ref: str | None = None
    source_type: str = Field(default="manual")
    priority: int = 0
    valid_from: str | None = None
    valid_until: str | None = None
    approve: bool = False


class KnowledgeSearchPayload(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int | None = Field(default=None, ge=1, le=20)


class ConversationImportPayload(BaseModel):
    batch_key: str = Field(..., min_length=1)
    source_format: str = Field(..., min_length=1)
    raw_text: str | None = None
    records: list[dict[str, Any]] = Field(default_factory=list)
    source_label: str | None = None
    delimiter: str = ","
    conversation_ref: str = "whatsapp-export"
    agent_authors: list[str] = Field(default_factory=list)
    customer_profile: str | None = None
    dry_run: bool = True

    @field_validator("source_format")
    @classmethod
    def _validate_source_format(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"csv", "json", "whatsapp_txt", "whatsapp", "txt"}:
            raise ValueError("source_format must be 'csv', 'json' or 'whatsapp_txt'.")
        return normalized

    @field_validator("customer_profile")
    @classmethod
    def _validate_customer_profile(cls, value: str | None) -> str | None:
        return _normalize_dimension("customer_profile", value)


class CandidateReviewPayload(BaseModel):
    approved: bool
    review_notes: str | None = None
    scenario_type: str | None = None
    customer_profile: str | None = None
    quality_score: float | None = Field(default=None, ge=0, le=10)
    promote_to_example: bool = True

    @field_validator("scenario_type")
    @classmethod
    def _validate_scenario(cls, value: str | None) -> str | None:
        return _normalize_dimension("scenario_type", value)

    @field_validator("customer_profile")
    @classmethod
    def _validate_profile(cls, value: str | None) -> str | None:
        return _normalize_dimension("customer_profile", value)


class DatasetVersionPayload(BaseModel):
    name: str | None = None
    seed: int = 42
    validation_ratio: float = Field(default=0.2, ge=0.0, le=0.5)
    holdout_ratio: float = Field(default=0.1, ge=0.0, le=0.5)


class DatasetFreezePayload(BaseModel):
    confirm: bool = True


class EvaluationCasePayload(BaseModel):
    case_key: str = Field(..., min_length=1)
    scenario_type: str = Field(default="general")
    customer_message: str = Field(..., min_length=1)
    version: int = Field(default=1, ge=1)
    customer_profile: str | None = None
    context: list[dict[str, str]] = Field(default_factory=list)
    expected_behavior: str | None = None
    expected_keywords: list[str] = Field(default_factory=list)
    forbidden_keywords: list[str] = Field(default_factory=list)
    requires_handoff: bool = False
    requires_source_grounding: bool = False
    requires_action_tool: bool = False

    @field_validator("scenario_type")
    @classmethod
    def _validate_scenario(cls, value: str) -> str:
        normalized = _normalize_dimension("scenario_type", value)
        if normalized is None:
            raise ValueError("scenario_type must not be empty.")
        return normalized

    @field_validator("customer_profile")
    @classmethod
    def _validate_profile(cls, value: str | None) -> str | None:
        return _normalize_dimension("customer_profile", value)


class EvaluationRunPayload(BaseModel):
    approach: str = Field(..., min_length=1)
    dataset_version_id: int | None = None
    model_ref: str | None = None
    run_key: str | None = None
    # Execução offline determinística é o padrão; respostas reais exigem
    # habilitação explícita do operador.
    canned_replies: dict[str, str] = Field(default_factory=dict)
    default_reply: str | None = None

    @field_validator("approach")
    @classmethod
    def _validate_approach(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"base", "base_rag", "finetuned"}:
            raise ValueError("approach must be 'base', 'base_rag' or 'finetuned'.")
        return normalized


class EvaluationComparePayload(BaseModel):
    run_ids: list[int] = Field(..., min_length=1)


class ModelRegistrationPayload(BaseModel):
    model_ref: str = Field(..., min_length=1)
    base_model: str | None = None
    dataset_version_id: int | None = None
    finetuning_job_id: str | None = None
    evaluation_run_id: int | None = None
    notes: str | None = None


class ModelActivationPayload(BaseModel):
    require_evaluation: bool = True


class FineTuningJobPayload(BaseModel):
    dataset_version_id: int = Field(..., gt=0)
    require_evaluation: bool = True


class TenantCredentialPayload(BaseModel):
    description: str | None = None
    scopes: list[str] = Field(default_factory=lambda: ["messages:write"])
    expires_at: str | None = None


class PrincipalPayload(BaseModel):
    principal_key: str = Field(..., min_length=3, max_length=120)
    display_name: str = Field(..., min_length=1, max_length=200)
    global_role: str = Field(default="viewer")
    is_global: bool = False
    company_ids: list[int] = Field(default_factory=list)
    description: str | None = None
    expires_at: str | None = None

    @field_validator("global_role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"viewer", "operator", "superadmin"}:
            raise ValueError("global_role must be 'viewer', 'operator' or 'superadmin'.")
        return normalized


class MembershipPayload(BaseModel):
    company_id: int = Field(..., gt=0)
    role: str = Field(default="viewer")

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"viewer", "operator", "superadmin"}:
            raise ValueError("role must be 'viewer', 'operator' or 'superadmin'.")
        return normalized


class MessageExampleApprovalPayload(BaseModel):
    approved: bool
    review_notes: str | None = None
    used_for_training: bool | None = None
