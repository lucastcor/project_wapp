"""Endpoints administrativos dos subsistemas da plataforma multiempresa.

Módulo separado de `admin_routes.py` para manter aquele arquivo focado no
domínio operacional já existente. Todas as rotas herdam a autenticação
administrativa e exigem `company_id`/`company_slug` resolvido: não há
superfície global para dado privado de empresa.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.api.admin_routes import (
    _record_audit_event,
    require_admin_access,
    require_admin_role,
    require_company_scope,
)
from app.api.admin_security import get_request_principal
from app.api.company_scope import require_authorized_company_id
from app.api.platform_schemas import (
    CandidateReviewPayload,
    ChannelBindingPayload,
    CompanyCapabilitiesPayload,
    CompanyConfigPublishPayload,
    ConversationImportPayload,
    DatasetFreezePayload,
    DatasetVersionPayload,
    EvaluationCasePayload,
    EvaluationComparePayload,
    EvaluationRunPayload,
    FineTuningJobPayload,
    KnowledgeDocumentPayload,
    KnowledgeSearchPayload,
    MembershipPayload,
    ModelActivationPayload,
    ModelRegistrationPayload,
    PrincipalPayload,
    TenantCredentialPayload,
)
from app.core.config import settings
from app.core.credentials import mask_secret
from app.core.taxonomy import describe_taxonomy
from app.core.vertical_packs import list_vertical_packs
from app.services.admin_principal_service import (
    AdminPrincipalError,
    create_principal_with_token,
    describe_principal,
    grant_membership,
    list_principals,
    revoke_membership,
    revoke_principal_token,
)
from app.services.company_config_service import (
    CompanyConfigError,
    bootstrap_company_config,
    get_company_config,
    list_company_config_versions,
    publish_company_config,
    save_company_config,
)
from app.services.conversation_import_service import (
    ConversationImportError,
    get_conversation_detail,
    import_conversations,
    review_candidate,
)
from app.services.dataset_service import (
    DatasetError,
    build_dataset_version,
    check_split_leakage,
    freeze_dataset_version,
    get_dataset_version,
    list_dataset_versions,
    rebuild_dataset_version_items,
)
from app.services.evaluation_service import (
    EXECUTION_MODE_SIMULATION,
    EvaluationError,
    build_offline_reply_generator,
    compare_runs,
    get_promotion_gate,
    list_evaluation_cases,
    list_evaluation_runs,
    register_evaluation_case,
    run_evaluation,
)
from app.services.finetuning_readiness_service import (
    evaluate_pretraining_readiness,
)
from app.services.knowledge_document_service import (
    KnowledgeDocumentError,
    approve_document,
    archive_document,
    import_legacy_knowledge_entries,
    ingest_document,
    list_documents,
)
from app.services.knowledge_retrieval_service import retrieve_knowledge
from app.services.model_registry_service import (
    ModelRegistryError,
    activate_model,
    list_company_models,
    register_candidate_model,
    resolve_reply_model,
    rollback_active_model,
)
from app.services.tenant_credential_service import (
    TenantCredentialError,
    issue_tenant_credential,
    list_tenant_credentials,
    revoke_tenant_credential,
)
from app.storage import channel_repository, import_repository

platform_admin_router = APIRouter(
    prefix="/admin",
    tags=["platform-admin"],
    dependencies=[Depends(require_admin_access), Depends(require_company_scope)],
)


def _require_company(request: Request) -> int:
    """Empresa autorizada da requisição, obrigatória.

    Delega ao escopo central. Os parâmetros `company_id`/`company_slug`
    continuam declarados nos handlers para documentação e para o cliente,
    mas quem os transforma em empresa autorizada é `require_company_scope`.
    """
    return require_authorized_company_id(request)


def _actor(request: Request) -> str | None:
    return getattr(request.state, "admin_actor", None)


# --- taxonomia e pacotes de domínio ------------------------------------------


@platform_admin_router.get("/taxonomy")
def read_taxonomy() -> dict[str, Any]:
    return describe_taxonomy()


@platform_admin_router.get("/vertical-packs")
def read_vertical_packs() -> list[dict[str, Any]]:
    return list_vertical_packs()


# --- vínculo de canal ---------------------------------------------------------


@platform_admin_router.get("/channel-bindings")
def read_channel_bindings(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    include_all_companies: bool = Query(default=False),
    reveal_external_id: bool = Query(default=False),
) -> list[dict[str, Any]]:
    """Vínculos de canal da empresa autorizada.

    Listagem global existe apenas para superadmin e só com opt-in explícito.
    O `external_id` é mascarado por padrão: quem administra o vínculo raramente
    precisa do identificador completo do provider.
    """
    principal = get_request_principal(request)

    if include_all_companies:
        if not principal.is_global:
            raise HTTPException(
                status_code=403,
                detail="Listing every company requires a global superadmin principal.",
            )
        bindings = channel_repository.get_channel_bindings(company_id=None)
        scope_company_id: int | None = None
    else:
        scope_company_id = _require_company(request)
        bindings = channel_repository.get_channel_bindings(company_id=scope_company_id)

    # F-01: `has_role("superadmin")` sem empresa cai no papel **global**, e
    # rebaixar alguém a viewer dentro da empresa não tirava dele o
    # `external_id` do provider. O papel que decide é o da empresa cujos
    # vínculos estão sendo lidos. Na listagem global não há empresa: ali o
    # `is_global` acima já é o portão.
    can_reveal = reveal_external_id and principal.has_role(
        "superadmin", company_id=scope_company_id
    )
    return [
        {
            **binding,
            "external_id": (
                binding.get("external_id")
                if can_reveal
                else mask_secret(str(binding.get("external_id") or ""))
            ),
            "external_id_masked": not can_reveal,
        }
        for binding in bindings
    ]


@platform_admin_router.post(
    "/channel-bindings",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_channel_binding(
    payload: ChannelBindingPayload,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    binding_id = channel_repository.create_channel_binding(
        company_id=resolved_company_id,
        provider=payload.provider,
        external_id=payload.external_id,
        channel_type=payload.channel_type,
        label=payload.label,
        active=payload.active,
    )
    _record_audit_event(
        request=request,
        action="create",
        resource_type="channel_binding",
        resource_id=str(binding_id),
        company_id=resolved_company_id,
        metadata={"provider": payload.provider, "channel_type": payload.channel_type},
    )
    return {"binding_id": binding_id, "company_id": resolved_company_id}


@platform_admin_router.delete(
    "/channel-bindings/{binding_id}",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def deactivate_channel_binding(
    binding_id: int,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    if not channel_repository.deactivate_channel_binding(
        binding_id=binding_id,
        company_id=resolved_company_id,
    ):
        raise HTTPException(status_code=404, detail="Channel binding not found.")
    _record_audit_event(
        request=request,
        action="deactivate",
        resource_type="channel_binding",
        resource_id=str(binding_id),
        company_id=resolved_company_id,
    )
    return {"binding_id": binding_id, "active": False}


# --- configuração da empresa --------------------------------------------------


@platform_admin_router.get("/company-config")
def read_company_config(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        config = get_company_config(resolved_company_id)
    except CompanyConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "company_id": config.company_id,
        "company_slug": config.company_slug,
        "company_name": config.company_name,
        "business_type": config.business_type,
        "vertical_pack": config.vertical_pack,
        "version": config.version,
        "status": config.status,
        "source": config.source,
        "capabilities": config.capabilities,
        "policies": config.policies,
        "context": config.context,
    }


@platform_admin_router.get("/company-config/versions")
def read_company_config_versions(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    resolved_company_id = _require_company(request)
    return list_company_config_versions(company_id=resolved_company_id)


@platform_admin_router.post(
    "/company-config/versions",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_company_config_version(
    payload: CompanyCapabilitiesPayload,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        stored = save_company_config(
            company_id=resolved_company_id,
            context=payload.context,
            capabilities=payload.capabilities,
            policies=payload.policies,
            author=_actor(request),
            change_note=payload.change_note,
            publish=payload.publish,
        )
    except CompanyConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="create",
        resource_type="company_context_version",
        resource_id=str(stored["id"]),
        company_id=resolved_company_id,
        metadata={"version": stored["version"], "status": stored["status"]},
    )
    return stored


@platform_admin_router.post(
    "/company-config/publish",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def publish_company_config_version(
    payload: CompanyConfigPublishPayload,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        stored = publish_company_config(
            company_id=resolved_company_id,
            version_id=payload.version_id,
            author=_actor(request),
        )
    except CompanyConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="publish",
        resource_type="company_context_version",
        resource_id=str(payload.version_id),
        company_id=resolved_company_id,
        metadata={"version": stored["version"]},
    )
    return stored


@platform_admin_router.post(
    "/company-config/bootstrap",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def bootstrap_company_config_endpoint(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        config = bootstrap_company_config(
            company_id=resolved_company_id,
            author=_actor(request) or "bootstrap",
        )
    except CompanyConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="bootstrap",
        resource_type="company_context_version",
        resource_id=str(config.version),
        company_id=resolved_company_id,
    )
    return {"company_id": config.company_id, "version": config.version, "source": config.source}


# --- knowledge ----------------------------------------------------------------


@platform_admin_router.get("/knowledge/documents")
def read_knowledge_documents(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    resolved_company_id = _require_company(request)
    return list_documents(company_id=resolved_company_id, status=status)


@platform_admin_router.post(
    "/knowledge/documents",
    dependencies=[Depends(require_admin_role("operator"))],
)
def create_knowledge_document(
    payload: KnowledgeDocumentPayload,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        stored = ingest_document(
            company_id=resolved_company_id,
            title=payload.title,
            content=payload.content,
            document_type=payload.document_type,
            external_ref=payload.external_ref,
            source_type=payload.source_type,
            priority=payload.priority,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            author=_actor(request),
            approve=payload.approve,
        )
    except KnowledgeDocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="ingest",
        resource_type="knowledge_document",
        resource_id=str(stored["document_id"]),
        company_id=resolved_company_id,
        metadata={
            "version": stored["version"],
            "chunk_count": stored["chunk_count"],
            "injection_flagged": stored["injection_flagged"],
        },
    )
    return stored


@platform_admin_router.post(
    "/knowledge/documents/{document_id}/approve",
    dependencies=[Depends(require_admin_role("operator"))],
)
def approve_knowledge_document(
    document_id: int,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        document = approve_document(
            company_id=resolved_company_id,
            document_id=document_id,
            approved_by=_actor(request),
        )
    except KnowledgeDocumentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="approve",
        resource_type="knowledge_document",
        resource_id=str(document_id),
        company_id=resolved_company_id,
    )
    return document


@platform_admin_router.post(
    "/knowledge/documents/{document_id}/archive",
    dependencies=[Depends(require_admin_role("operator"))],
)
def archive_knowledge_document(
    document_id: int,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        document = archive_document(company_id=resolved_company_id, document_id=document_id)
    except KnowledgeDocumentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="archive",
        resource_type="knowledge_document",
        resource_id=str(document_id),
        company_id=resolved_company_id,
    )
    return document


@platform_admin_router.post(
    "/knowledge/import-legacy",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def import_legacy_knowledge(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    result = import_legacy_knowledge_entries(
        company_id=resolved_company_id,
        author=_actor(request) or "legacy-migration",
    )
    _record_audit_event(
        request=request,
        action="import_legacy",
        resource_type="knowledge_document",
        company_id=resolved_company_id,
        metadata=result,
    )
    return result


@platform_admin_router.post("/knowledge/search")
def search_knowledge(
    request: Request,
    payload: KnowledgeSearchPayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    result = retrieve_knowledge(
        company_id=resolved_company_id,
        query=payload.query,
        limit=payload.limit,
    )
    return {
        **result.as_trace(),
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "document_title": chunk.document_title,
                "document_type": chunk.document_type,
                "score": chunk.score,
                "content": chunk.content,
                "injection_flagged": chunk.injection_flagged,
            }
            for chunk in result.chunks
        ],
    }


# --- importação de conversas ---------------------------------------------------


@platform_admin_router.get("/conversation-import/batches")
def read_import_batches(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    resolved_company_id = _require_company(request)
    return import_repository.list_batches(company_id=resolved_company_id)


@platform_admin_router.post(
    "/conversation-import",
    dependencies=[Depends(require_admin_role("operator"))],
)
def run_conversation_import(
    payload: ConversationImportPayload,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        result = import_conversations(
            company_id=resolved_company_id,
            batch_key=payload.batch_key,
            source_format=payload.source_format,
            raw_payload=payload.raw_text,
            records=payload.records,
            source_label=payload.source_label,
            delimiter=payload.delimiter,
            conversation_ref=payload.conversation_ref,
            agent_authors=payload.agent_authors,
            customer_profile=payload.customer_profile,
            dry_run=payload.dry_run,
            actor=_actor(request),
        )
    except ConversationImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not payload.dry_run:
        _record_audit_event(
            request=request,
            action="import",
            resource_type="conversation_import_batch",
            resource_id=str(result.get("batch_id")),
            company_id=resolved_company_id,
            metadata={
                "batch_key": payload.batch_key,
                "candidates_created": result.get("candidates_created"),
                "candidates_existing": result.get("candidates_existing"),
            },
        )
    return result


@platform_admin_router.get("/conversation-import/batches/{batch_id}/conversations")
def read_batch_conversations(
    request: Request,
    batch_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    resolved_company_id = _require_company(request)
    return import_repository.get_staged_conversations(
        company_id=resolved_company_id,
        batch_id=batch_id,
    )


@platform_admin_router.get("/conversation-import/conversations/{conversation_id}")
def read_staged_conversation(
    request: Request,
    conversation_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    return get_conversation_detail(
        company_id=resolved_company_id,
        conversation_id=conversation_id,
    )


@platform_admin_router.get("/training-candidates")
def read_training_candidates(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    review_status: str | None = Query(default=None),
    batch_id: int | None = Query(default=None),
) -> list[dict[str, Any]]:
    resolved_company_id = _require_company(request)
    return import_repository.list_candidates(
        company_id=resolved_company_id,
        review_status=review_status,
        batch_id=batch_id,
    )


@platform_admin_router.post(
    "/training-candidates/{candidate_id}/review",
    dependencies=[Depends(require_admin_role("operator"))],
)
def review_training_candidate(
    candidate_id: int,
    payload: CandidateReviewPayload,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        result = review_candidate(
            company_id=resolved_company_id,
            candidate_id=candidate_id,
            approved=payload.approved,
            reviewer=_actor(request),
            review_notes=payload.review_notes,
            scenario_type=payload.scenario_type,
            customer_profile=payload.customer_profile,
            quality_score=payload.quality_score,
            promote_to_example=payload.promote_to_example,
        )
    except ConversationImportError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="review",
        resource_type="training_candidate",
        resource_id=str(candidate_id),
        company_id=resolved_company_id,
        metadata={
            "review_status": result["review_status"],
            "promoted_message_example_id": result["promoted_message_example_id"],
        },
    )
    return result


# --- dataset -------------------------------------------------------------------


@platform_admin_router.get("/dataset-versions")
def read_dataset_versions(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    resolved_company_id = _require_company(request)
    return list_dataset_versions(company_id=resolved_company_id)


@platform_admin_router.post(
    "/dataset-versions",
    dependencies=[Depends(require_admin_role("operator"))],
)
def create_dataset_version(
    payload: DatasetVersionPayload,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    version = build_dataset_version(
        company_id=resolved_company_id,
        name=payload.name,
        seed=payload.seed,
        validation_ratio=payload.validation_ratio,
        holdout_ratio=payload.holdout_ratio,
        created_by=_actor(request),
    )
    _record_audit_event(
        request=request,
        action="create",
        resource_type="dataset_version",
        resource_id=str(version.get("id")),
        company_id=resolved_company_id,
        metadata={"version": version.get("version"), "stats": version.get("stats")},
    )
    return version


@platform_admin_router.get("/dataset-versions/{dataset_version_id}")
def read_dataset_version(
    request: Request,
    dataset_version_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        return get_dataset_version(
            company_id=resolved_company_id,
            dataset_version_id=dataset_version_id,
        )
    except DatasetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@platform_admin_router.post(
    "/dataset-versions/{dataset_version_id}/rebuild",
    dependencies=[Depends(require_admin_role("operator"))],
)
def rebuild_dataset_version(
    dataset_version_id: int,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        version = rebuild_dataset_version_items(
            company_id=resolved_company_id,
            dataset_version_id=dataset_version_id,
        )
    except DatasetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="rebuild",
        resource_type="dataset_version",
        resource_id=str(dataset_version_id),
        company_id=resolved_company_id,
    )
    return version


@platform_admin_router.post(
    "/dataset-versions/{dataset_version_id}/freeze",
    dependencies=[Depends(require_admin_role("operator"))],
)
def freeze_dataset(
    dataset_version_id: int,
    request: Request,
    payload: DatasetFreezePayload = Body(default=DatasetFreezePayload()),
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    readiness = evaluate_pretraining_readiness(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )
    try:
        version = freeze_dataset_version(
            company_id=resolved_company_id,
            dataset_version_id=dataset_version_id,
            frozen_by=_actor(request),
            readiness=readiness,
        )
    except DatasetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="freeze",
        resource_type="dataset_version",
        resource_id=str(dataset_version_id),
        company_id=resolved_company_id,
        metadata={"content_hash": version.get("content_hash")},
    )
    return version


@platform_admin_router.get("/dataset-versions/{dataset_version_id}/leakage")
def read_dataset_leakage(
    request: Request,
    dataset_version_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    return check_split_leakage(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )


@platform_admin_router.get("/dataset-versions/{dataset_version_id}/readiness")
def read_dataset_readiness(
    request: Request,
    dataset_version_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    require_evaluation: bool = Query(default=True),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    return evaluate_pretraining_readiness(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )


# --- avaliação ------------------------------------------------------------------


@platform_admin_router.get("/evaluation/cases")
def read_evaluation_cases(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    resolved_company_id = _require_company(request)
    return list_evaluation_cases(company_id=resolved_company_id)


@platform_admin_router.post(
    "/evaluation/cases",
    dependencies=[Depends(require_admin_role("operator"))],
)
def create_evaluation_case(
    payload: EvaluationCasePayload,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    case_id = register_evaluation_case(
        company_id=resolved_company_id,
        case_key=payload.case_key,
        scenario_type=payload.scenario_type,
        customer_message=payload.customer_message,
        version=payload.version,
        customer_profile=payload.customer_profile,
        context=payload.context,
        expected_behavior=payload.expected_behavior,
        expected_keywords=payload.expected_keywords,
        forbidden_keywords=payload.forbidden_keywords,
        requires_handoff=payload.requires_handoff,
        requires_source_grounding=payload.requires_source_grounding,
        requires_action_tool=payload.requires_action_tool,
        created_by=_actor(request),
    )
    _record_audit_event(
        request=request,
        action="create",
        resource_type="evaluation_case",
        resource_id=str(case_id),
        company_id=resolved_company_id,
        metadata={"case_key": payload.case_key, "version": payload.version},
    )
    return {"case_id": case_id, "company_id": resolved_company_id}


@platform_admin_router.get("/evaluation/runs")
def read_evaluation_runs(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    resolved_company_id = _require_company(request)
    return list_evaluation_runs(company_id=resolved_company_id)


@platform_admin_router.post(
    "/evaluation/runs",
    dependencies=[Depends(require_admin_role("operator"))],
)
def create_evaluation_run(
    payload: EvaluationRunPayload,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Executa a suíte com gerador offline determinístico.

    Esta rota é **sempre** `execution_mode=simulation`: ela não executa modelo
    algum, então registrar o resultado como prova de um modelo seria falso. O
    run resultante nunca é promotion-eligible, mesmo com `approach=finetuned`.

    A avaliação model-backed é um procedimento operacional separado, com
    opt-in explícito, e não é exposta por esta superfície.
    """
    resolved_company_id = _require_company(request)
    generator = build_offline_reply_generator(
        canned_replies=payload.canned_replies or None,
        default_reply=payload.default_reply,
    )
    try:
        run = run_evaluation(
            company_id=resolved_company_id,
            approach=payload.approach,
            reply_generator=generator,
            dataset_version_id=payload.dataset_version_id,
            model_ref=payload.model_ref,
            run_key=payload.run_key,
            created_by=_actor(request),
            execution_mode=EXECUTION_MODE_SIMULATION,
        )
    except EvaluationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="evaluate",
        resource_type="evaluation_run",
        resource_id=str(run.get("id")),
        company_id=resolved_company_id,
        metadata={"approach": payload.approach, "scores": run.get("scores")},
    )
    return run


@platform_admin_router.post("/evaluation/compare")
def compare_evaluation_runs(
    request: Request,
    payload: EvaluationComparePayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        return compare_runs(company_id=resolved_company_id, run_ids=payload.run_ids)
    except EvaluationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@platform_admin_router.get("/evaluation/promotion-gate")
def read_promotion_gate(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    min_pass_rate: float | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    return get_promotion_gate(
        company_id=resolved_company_id,
        min_pass_rate=(
            min_pass_rate if min_pass_rate is not None else settings.finetuning_min_eval_pass_rate
        ),
    )


# --- modelos por empresa ---------------------------------------------------------


@platform_admin_router.get("/models")
def read_company_models(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    selection = resolve_reply_model(company_id=resolved_company_id)
    return {
        "company_id": resolved_company_id,
        "effective_model_ref": selection.model_ref,
        "effective_model_status": selection.status,
        "models": list_company_models(company_id=resolved_company_id),
    }


@platform_admin_router.post(
    "/models",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def register_company_model(
    payload: ModelRegistrationPayload,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    stored = register_candidate_model(
        company_id=resolved_company_id,
        model_ref=payload.model_ref,
        base_model=payload.base_model,
        dataset_version_id=payload.dataset_version_id,
        finetuning_job_id=payload.finetuning_job_id,
        evaluation_run_id=payload.evaluation_run_id,
        notes=payload.notes,
        created_by=_actor(request),
    )
    _record_audit_event(
        request=request,
        action="register",
        resource_type="company_model",
        resource_id=str(stored.get("id")),
        company_id=resolved_company_id,
        metadata={"model_ref": payload.model_ref},
    )
    return stored


@platform_admin_router.post(
    "/models/{model_id}/activate",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def activate_company_model(
    model_id: int,
    request: Request,
    payload: ModelActivationPayload = Body(default=ModelActivationPayload()),
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        activated = activate_model(
            company_id=resolved_company_id,
            model_id=model_id,
            activated_by=_actor(request),
            require_evaluation=payload.require_evaluation,
        )
    except ModelRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="activate",
        resource_type="company_model",
        resource_id=str(model_id),
        company_id=resolved_company_id,
        metadata={"model_ref": activated.get("model_ref")},
    )
    return activated


@platform_admin_router.post(
    "/models/rollback",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def rollback_company_model(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    try:
        result = rollback_active_model(company_id=resolved_company_id, actor=_actor(request))
    except ModelRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="rollback",
        resource_type="company_model",
        resource_id=str(result["retired_model"].get("id")),
        company_id=resolved_company_id,
        metadata={
            "effective_model_ref": result["effective_model_ref"],
            "effective_model_status": result["effective_model_status"],
        },
    )
    return result


# --- fine-tuning governado --------------------------------------------------------


@platform_admin_router.post(
    "/finetuning/preflight",
    dependencies=[Depends(require_admin_role("operator"))],
)
def preflight_finetuning(
    request: Request,
    payload: FineTuningJobPayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Readiness sem efeito externo: nenhum arquivo é enviado."""
    resolved_company_id = _require_company(request)
    return evaluate_pretraining_readiness(
        company_id=resolved_company_id,
        dataset_version_id=payload.dataset_version_id,
    )


# --- identidade: principals e credenciais de tenant ---------------------------------


@platform_admin_router.get(
    "/principals",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def read_principals() -> list[dict[str, Any]]:
    return list_principals()


@platform_admin_router.post(
    "/principals",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_principal(payload: PrincipalPayload, request: Request) -> dict[str, Any]:
    """Cria um principal administrativo e emite seu token.

    O token em claro é devolvido uma única vez e nunca é persistido.
    """
    try:
        created = create_principal_with_token(
            principal_key=payload.principal_key,
            display_name=payload.display_name,
            global_role=payload.global_role,
            is_global=payload.is_global,
            company_ids=payload.company_ids,
            description=payload.description,
            expires_at=payload.expires_at,
            created_by=_actor(request),
        )
    except AdminPrincipalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="create",
        resource_type="platform_principal",
        resource_id=str(created["principal_id"]),
        metadata={
            "global_role": created["global_role"],
            "is_global": created["is_global"],
            "company_ids": created["company_ids"],
            "token_prefix": created["token_prefix"],
        },
    )
    return created


@platform_admin_router.get(
    "/principals/{principal_id}",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def read_principal(principal_id: int) -> dict[str, Any]:
    described = describe_principal(principal_id)
    if not described:
        raise HTTPException(status_code=404, detail="Principal not found.")
    return described


@platform_admin_router.post(
    "/principals/{principal_id}/memberships",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_principal_membership(
    principal_id: int,
    payload: MembershipPayload,
    request: Request,
) -> dict[str, Any]:
    try:
        membership_id = grant_membership(
            principal_id=principal_id,
            company_id=payload.company_id,
            role=payload.role,
            created_by=_actor(request),
        )
    except AdminPrincipalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="grant_membership",
        resource_type="platform_principal",
        resource_id=str(principal_id),
        company_id=payload.company_id,
        metadata={"membership_role": payload.role},
    )
    return {"membership_id": membership_id, "principal_id": principal_id}


@platform_admin_router.delete(
    "/principals/{principal_id}/memberships/{company_id}",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def delete_principal_membership(
    principal_id: int,
    company_id: int,
    request: Request,
) -> dict[str, Any]:
    removed = revoke_membership(principal_id=principal_id, company_id=company_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Membership not found.")
    _record_audit_event(
        request=request,
        action="revoke_membership",
        resource_type="platform_principal",
        resource_id=str(principal_id),
        company_id=company_id,
    )
    return {"principal_id": principal_id, "company_id": company_id, "removed": True}


@platform_admin_router.post(
    "/principals/tokens/{token_id}/revoke",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def revoke_principal_access_token(token_id: int, request: Request) -> dict[str, Any]:
    if not revoke_principal_token(token_id=token_id):
        raise HTTPException(status_code=404, detail="Token not found or already revoked.")
    _record_audit_event(
        request=request,
        action="revoke_token",
        resource_type="platform_access_token",
        resource_id=str(token_id),
    )
    return {"token_id": token_id, "revoked": True}


@platform_admin_router.get("/tenant-credentials")
def read_tenant_credentials(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    resolved_company_id = _require_company(request)
    return list_tenant_credentials(company_id=resolved_company_id)


@platform_admin_router.post(
    "/tenant-credentials",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_tenant_credential(
    payload: TenantCredentialPayload,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Emite a credencial de ingestão da empresa.

    Rotação: emitir uma nova credencial, migrar os chamadores e revogar a
    antiga. Revogação tem efeito imediato.
    """
    resolved_company_id = _require_company(request)
    try:
        issued = issue_tenant_credential(
            company_id=resolved_company_id,
            scopes=payload.scopes,
            description=payload.description,
            expires_at=payload.expires_at,
            created_by=_actor(request),
        )
    except TenantCredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="create",
        resource_type="tenant_api_credential",
        resource_id=str(issued["credential_id"]),
        company_id=resolved_company_id,
        # O token nunca entra na auditoria: só o prefixo identificador.
        metadata={"token_prefix": issued["token_prefix"], "scopes": issued["scopes"]},
    )
    return issued


@platform_admin_router.post(
    "/tenant-credentials/{credential_id}/revoke",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def revoke_tenant_credential_endpoint(
    credential_id: int,
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = _require_company(request)
    if not revoke_tenant_credential(
        company_id=resolved_company_id,
        credential_id=credential_id,
    ):
        raise HTTPException(status_code=404, detail="Credential not found or already revoked.")
    _record_audit_event(
        request=request,
        action="revoke",
        resource_type="tenant_api_credential",
        resource_id=str(credential_id),
        company_id=resolved_company_id,
    )
    return {"credential_id": credential_id, "revoked": True}
