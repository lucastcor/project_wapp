"""Ciclo de vida de documentos de conhecimento.

Ingestão, versionamento, chunking e aprovação. Documento só é recuperável em
runtime depois de aprovado e dentro da vigência.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.logging import get_logger, log_event
from app.core.tenancy import require_company_id
from app.services.knowledge_retrieval_service import build_chunk_records, detect_injection
from app.storage import knowledge_repository
from app.storage.repository import RuntimeRepository

logger = get_logger(__name__)
repository = RuntimeRepository()

VALID_DOCUMENT_TYPES = {
    "business_rule",
    "discount_policy",
    "availability_rule",
    "schedule_rule",
    "rental_rule",
    "product_info",
    "price",
    "procedure",
    "custom_case_guidance",
    "general_faq",
}


class KnowledgeDocumentError(Exception):
    pass


def normalize_document_type(document_type: str | None) -> str:
    normalized = str(document_type or "").strip().lower()
    return normalized if normalized in VALID_DOCUMENT_TYPES else "general_faq"


def ingest_document(
    *,
    company_id: int,
    title: str,
    content: str,
    document_type: str,
    external_ref: str | None = None,
    source_type: str = "manual",
    priority: int = 0,
    valid_from: str | None = None,
    valid_until: str | None = None,
    author: str | None = None,
    approve: bool = False,
) -> dict[str, Any]:
    """Cria/atualiza um documento e regenera seus chunks.

    Idempotente por `external_ref`: reenviar o mesmo conteúdo não versiona.
    """
    resolved_company_id = require_company_id(company_id, context="knowledge ingestion")
    normalized_title = str(title or "").strip()
    if not normalized_title:
        raise KnowledgeDocumentError("title must not be blank.")

    normalized_content = str(content or "").strip()
    if not normalized_content:
        raise KnowledgeDocumentError("content must not be blank.")

    injection_flagged = detect_injection(normalized_content)

    stored = knowledge_repository.upsert_document(
        company_id=resolved_company_id,
        title=normalized_title,
        content=normalized_content,
        document_type=normalize_document_type(document_type),
        external_ref=external_ref,
        source_type=source_type,
        priority=priority,
        valid_from=valid_from,
        valid_until=valid_until,
        author=author,
    )

    chunk_count = 0
    if stored["created_version"]:
        # Os chunks acompanham a versão criada. A recuperação só enxerga os
        # chunks da versão publicada, então gerar aqui não expõe o draft.
        chunks = build_chunk_records(normalized_content, title=normalized_title)
        chunk_count = knowledge_repository.append_chunks(
            company_id=resolved_company_id,
            document_id=stored["document_id"],
            document_version_id=stored["version_id"],
            chunks=chunks,
        )

    if approve:
        knowledge_repository.approve_document(
            company_id=resolved_company_id,
            document_id=stored["document_id"],
            approved_by=author,
            version_id=stored["version_id"],
        )

    log_event(
        logger,
        logging.INFO,
        "knowledge.document.ingested",
        company_id=resolved_company_id,
        document_id=stored["document_id"],
        version=stored["version"],
        created_version=stored["created_version"],
        chunk_count=chunk_count,
        injection_flagged=injection_flagged,
        approved=approve,
    )

    document = knowledge_repository.get_document(
        company_id=resolved_company_id,
        document_id=stored["document_id"],
    ) or {}
    return {
        **stored,
        "company_id": resolved_company_id,
        "chunk_count": chunk_count,
        "injection_flagged": injection_flagged,
        "status": document.get("status", knowledge_repository.DOCUMENT_STATUS_DRAFT),
        "published_version_id": document.get("current_published_version_id"),
        "working_version_id": document.get("working_version_id"),
    }


def approve_document(*, company_id: int, document_id: int, approved_by: str | None) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="knowledge approval")
    if not knowledge_repository.approve_document(
        company_id=resolved_company_id,
        document_id=document_id,
        approved_by=approved_by,
    ):
        raise KnowledgeDocumentError(
            f"Knowledge document {document_id} not found for company_id={resolved_company_id}."
        )
    document = knowledge_repository.get_document(
        company_id=resolved_company_id,
        document_id=document_id,
    )
    log_event(
        logger,
        logging.INFO,
        "knowledge.document.approved",
        company_id=resolved_company_id,
        document_id=document_id,
    )
    return document or {}


def archive_document(*, company_id: int, document_id: int) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="knowledge archive")
    if not knowledge_repository.archive_document(
        company_id=resolved_company_id,
        document_id=document_id,
    ):
        raise KnowledgeDocumentError(
            f"Knowledge document {document_id} not found for company_id={resolved_company_id}."
        )
    return knowledge_repository.get_document(
        company_id=resolved_company_id,
        document_id=document_id,
    ) or {}


def list_documents(*, company_id: int, status: str | None = None) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="knowledge listing")
    return knowledge_repository.list_documents(company_id=resolved_company_id, status=status)


def import_legacy_knowledge_entries(
    *,
    company_id: int,
    author: str = "legacy-migration",
    approve: bool = True,
) -> dict[str, Any]:
    """Traz `knowledge_entries` ativos da empresa para o modelo documental.

    Idempotente: usa `legacy:<id>` como `external_ref`. Só migra conhecimento
    global; entradas presas a um contato continuam no modelo antigo, porque não
    são conhecimento factual da empresa.
    """
    resolved_company_id = require_company_id(company_id, context="legacy knowledge migration")
    entries = repository.get_active_knowledge_entries(company_id=resolved_company_id)

    imported = 0
    skipped = 0
    for entry in entries:
        if not int(entry.get("applies_globally") or 0):
            skipped += 1
            continue
        ingest_document(
            company_id=resolved_company_id,
            title=str(entry.get("title") or "Conhecimento da empresa"),
            content=str(entry.get("content") or ""),
            document_type=str(entry.get("knowledge_type") or "general_faq"),
            external_ref=f"legacy:{entry['id']}",
            source_type="legacy_knowledge_entry",
            author=author,
            approve=approve,
        )
        imported += 1

    log_event(
        logger,
        logging.INFO,
        "knowledge.legacy.migrated",
        company_id=resolved_company_id,
        imported=imported,
        skipped=skipped,
    )
    return {
        "company_id": resolved_company_id,
        "imported_documents": imported,
        "skipped_contact_scoped_entries": skipped,
    }
