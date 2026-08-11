"""Importação governada de conversas históricas.

Fluxo: parse -> anonimização -> segmentação por sessão -> staging ->
extração de candidatos multi-turno -> revisão humana -> promoção.

Invariantes:
- nada de conteúdo bruto é persistido: o staging já guarda texto anonimizado;
- nenhum candidato vira exemplo de treino sem aprovação humana explícita;
- reimportar o mesmo `batch_key` substitui o staging e não duplica candidatos;
- `dry_run` executa o pipeline inteiro e não escreve nada.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.taxonomy import (
    DEFAULT_PROFILE_NAME,
    normalize_with_fallback,
    scenario_type_for,
)
from app.core.tenancy import require_company_id
from app.services.company_config_service import get_company_config
from app.services.conversation_parsers import (
    AGENT_ROLE,
    CUSTOMER_ROLE,
    ConversationParseError,
    ParsedMessage,
    decode_payload,
    parse_payload,
)
from app.services.pii_service import (
    POLICY_STANDARD,
    anonymize_text,
    merge_reports,
    pseudonymize_identifier,
)
from app.storage import import_repository
from app.storage.repository import RuntimeRepository

logger = get_logger(__name__)
repository = RuntimeRepository()

def _context_limits() -> tuple[int, int]:
    """Limites determinísticos e configuráveis do contexto multi-turno."""
    return (
        max(0, settings.import_max_context_turns),
        max(200, settings.import_max_context_chars),
    )


class ConversationImportError(Exception):
    pass


class CandidateStateError(ConversationImportError):
    """Transição de estado inválida na revisão de um candidato."""


@dataclass
class StagedConversation:
    external_conversation_id: str
    contact_ref_hash: str
    session_index: int
    started_at: str | None
    ended_at: str | None
    messages: list[dict[str, Any]]


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _segment_into_sessions(
    messages: list[ParsedMessage],
    *,
    gap_minutes: int,
) -> list[list[ParsedMessage]]:
    """Quebra a conversa em sessões por janela de inatividade."""
    if not messages:
        return []

    sessions: list[list[ParsedMessage]] = [[]]
    previous_time: datetime | None = None
    gap = timedelta(minutes=max(1, gap_minutes))

    for message in messages:
        current_time = _parse_timestamp(message.occurred_at)
        if (
            previous_time is not None
            and current_time is not None
            and current_time - previous_time > gap
            and sessions[-1]
        ):
            sessions.append([])
        sessions[-1].append(message)
        if current_time is not None:
            previous_time = current_time

    return [session for session in sessions if session]


def _stage_conversations(
    *,
    company_id: int,
    messages: list[ParsedMessage],
    pii_policy: str,
    gap_minutes: int,
) -> tuple[list[StagedConversation], dict[str, int]]:
    grouped: dict[str, list[ParsedMessage]] = {}
    for message in messages:
        grouped.setdefault(message.conversation_ref, []).append(message)

    staged: list[StagedConversation] = []
    pii_totals: dict[str, int] = {}

    for conversation_ref, conversation_messages in grouped.items():
        ordered = sorted(
            conversation_messages,
            key=lambda item: (item.occurred_at or "", item.source_line or 0),
        )
        contact_ref = ordered[0].contact_ref if ordered else conversation_ref
        contact_hash = pseudonymize_identifier(contact_ref, company_id=company_id)

        for session_index, session in enumerate(
            _segment_into_sessions(ordered, gap_minutes=gap_minutes)
        ):
            anonymized_messages: list[dict[str, Any]] = []
            for message in session:
                anonymized_content, report = anonymize_text(message.content, policy=pii_policy)
                for key, value in report.as_dict().items():
                    pii_totals[key] = pii_totals.get(key, 0) + value
                anonymized_messages.append(
                    {
                        "role": message.role,
                        "anonymized_content": anonymized_content,
                        "pii_report": report.as_dict(),
                        "occurred_at": message.occurred_at,
                        "source_line": message.source_line,
                    }
                )

            staged.append(
                StagedConversation(
                    external_conversation_id=f"{conversation_ref}#{session_index}",
                    contact_ref_hash=contact_hash,
                    session_index=session_index,
                    started_at=session[0].occurred_at,
                    ended_at=session[-1].occurred_at,
                    messages=anonymized_messages,
                )
            )

    return staged, pii_totals


def _candidate_hash(company_id: int, customer_message: str, approved_reply: str) -> str:
    payload = f"{company_id}|{customer_message.strip().lower()}|{approved_reply.strip().lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_candidates(
    *,
    company_id: int,
    conversation: StagedConversation,
    default_profile_name: str,
    customer_profile: str | None,
) -> list[dict[str, Any]]:
    """Extrai pares cliente->atendente preservando os turnos anteriores."""
    candidates: list[dict[str, Any]] = []
    messages = conversation.messages

    for index, message in enumerate(messages):
        if message["role"] != CUSTOMER_ROLE:
            continue

        reply_parts: list[str] = []
        cursor = index + 1
        while cursor < len(messages) and messages[cursor]["role"] == AGENT_ROLE:
            reply_parts.append(messages[cursor]["anonymized_content"])
            cursor += 1
        if not reply_parts:
            continue

        customer_message = str(message["anonymized_content"]).strip()
        approved_reply = "\n".join(part.strip() for part in reply_parts if part.strip()).strip()
        if not customer_message or not approved_reply:
            continue

        # Apenas turnos ANTERIORES: incluir mensagem futura ensinaria o modelo
        # a responder com informação que ele não teria no runtime.
        max_turns, max_chars = _context_limits()
        context: list[dict[str, str]] = []
        used_chars = 0
        for item in reversed(messages[max(0, index - max_turns) : index]):
            content = str(item["anonymized_content"])
            if used_chars + len(content) > max_chars:
                break
            context.append({"role": str(item["role"]), "content": content})
            used_chars += len(content)
        context.reverse()

        scenario_type = scenario_type_for(None, None)
        candidates.append(
            {
                "candidate_hash": _candidate_hash(company_id, customer_message, approved_reply),
                "group_key": conversation.contact_ref_hash,
                "scenario_type": scenario_type,
                "customer_profile": customer_profile,
                "profile_name": default_profile_name,
                "context": context,
                "customer_message": customer_message,
                "approved_reply": approved_reply,
                "quality_score": 0.0,
                "pii_report": merge_reports([]).as_dict(),
                "conversation_external_id": conversation.external_conversation_id,
            }
        )

    return candidates


def import_conversations(
    *,
    company_id: int,
    batch_key: str,
    source_format: str,
    raw_payload: bytes | str | None = None,
    records: list[dict[str, Any]] | None = None,
    source_label: str | None = None,
    delimiter: str = ",",
    conversation_ref: str = "whatsapp-export",
    agent_authors: list[str] | None = None,
    customer_profile: str | None = None,
    dry_run: bool = True,
    actor: str | None = None,
) -> dict[str, Any]:
    """Importa conversas brutas para o staging governado."""
    resolved_company_id = require_company_id(company_id, context="conversation import")
    normalized_batch_key = str(batch_key or "").strip()
    if not normalized_batch_key:
        raise ConversationImportError("batch_key must not be blank.")

    company_config = get_company_config(resolved_company_id)
    if not company_config.has_capability("conversation_import"):
        raise ConversationImportError(
            "Conversation import capability is disabled for this company."
        )
    pii_policy = str(company_config.policy("pii_policy", POLICY_STANDARD))

    try:
        text = decode_payload(raw_payload or "", max_bytes=settings.import_max_bytes)
    except ConversationParseError as exc:
        raise ConversationImportError(str(exc)) from exc

    try:
        parse_result = parse_payload(
            source_format=source_format,
            raw_text=text,
            records=records,
            delimiter=delimiter,
            conversation_ref=conversation_ref,
            agent_authors=agent_authors,
        )
    except ConversationParseError as exc:
        raise ConversationImportError(str(exc)) from exc

    if len(parse_result.messages) > settings.import_max_messages_per_batch:
        raise ConversationImportError(
            f"Batch has {len(parse_result.messages)} messages, above the configured limit "
            f"of {settings.import_max_messages_per_batch}."
        )

    staged_conversations, pii_totals = _stage_conversations(
        company_id=resolved_company_id,
        messages=parse_result.messages,
        pii_policy=pii_policy,
        gap_minutes=settings.import_session_gap_minutes,
    )

    normalized_customer_profile = None
    if customer_profile:
        normalized_customer_profile, _ = normalize_with_fallback("customer_profile", customer_profile)

    default_profile_name = (
        normalize_with_fallback("profile_name", company_config.context.get("default_profile_name"))[0]
        or DEFAULT_PROFILE_NAME
    )

    all_candidates: list[dict[str, Any]] = []
    for conversation in staged_conversations:
        all_candidates.extend(
            _extract_candidates(
                company_id=resolved_company_id,
                conversation=conversation,
                default_profile_name=default_profile_name,
                customer_profile=normalized_customer_profile,
            )
        )

    stats = {
        "parsed_messages": len(parse_result.messages),
        "invalid_records": len(parse_result.issues),
        "conversations": len(staged_conversations),
        "candidates": len(all_candidates),
        "pii_replacements": pii_totals,
        "pii_policy": pii_policy,
    }

    preview = [
        {
            "conversation": conversation.external_conversation_id,
            "session_index": conversation.session_index,
            "message_count": len(conversation.messages),
            "roles": [message["role"] for message in conversation.messages[:8]],
        }
        for conversation in staged_conversations[:10]
    ]
    issues_preview = [
        {"source_line": issue.source_line, "reason": issue.reason}
        for issue in parse_result.issues[:25]
    ]

    if dry_run:
        log_event(
            logger,
            logging.INFO,
            "conversation_import.dry_run",
            company_id=resolved_company_id,
            batch_key=normalized_batch_key,
            **{key: value for key, value in stats.items() if isinstance(value, int)},
        )
        return {
            "dry_run": True,
            "company_id": resolved_company_id,
            "batch_id": None,
            "batch_key": normalized_batch_key,
            "status": "previewed",
            "stats": stats,
            "preview": preview,
            "issues": issues_preview,
            "candidates_created": 0,
            "candidates_existing": 0,
        }

    existing_batch = import_repository.get_batch_by_key(
        company_id=resolved_company_id,
        batch_key=normalized_batch_key,
    )
    if existing_batch:
        batch_id = int(existing_batch["id"])
    else:
        batch_id = import_repository.create_batch(
            company_id=resolved_company_id,
            batch_key=normalized_batch_key,
            source_format=source_format,
            source_label=source_label,
            dry_run=False,
            created_by=actor,
        )

    staging_counts = import_repository.upsert_batch_staging(
        company_id=resolved_company_id,
        batch_id=batch_id,
        conversations=[
            {
                "external_conversation_id": conversation.external_conversation_id,
                "contact_ref_hash": conversation.contact_ref_hash,
                "session_index": conversation.session_index,
                "started_at": conversation.started_at,
                "ended_at": conversation.ended_at,
                "messages": conversation.messages,
            }
            for conversation in staged_conversations
        ],
    )

    conversation_id_by_ref = {
        str(row["external_conversation_id"]): int(row["id"])
        for row in import_repository.get_staged_conversations(
            company_id=resolved_company_id,
            batch_id=batch_id,
        )
    }

    created = 0
    existing = 0
    for candidate in all_candidates:
        candidate_row = {
            **candidate,
            "batch_id": batch_id,
            "conversation_id": conversation_id_by_ref.get(candidate["conversation_external_id"]),
        }
        _, was_created = import_repository.upsert_candidate(
            company_id=resolved_company_id,
            candidate=candidate_row,
        )
        if was_created:
            created += 1
        else:
            existing += 1

    stats["candidates_created"] = created
    stats["candidates_existing"] = existing
    stats["staged_conversations"] = staging_counts["conversations"]
    stats["staged_messages"] = staging_counts["messages"]

    import_repository.update_batch_status(
        company_id=resolved_company_id,
        batch_id=batch_id,
        status=import_repository.BATCH_STATUS_STAGED,
        stats=stats,
    )

    log_event(
        logger,
        logging.INFO,
        "conversation_import.staged",
        company_id=resolved_company_id,
        batch_id=batch_id,
        batch_key=normalized_batch_key,
        conversations=staging_counts["conversations"],
        messages=staging_counts["messages"],
        candidates_created=created,
        candidates_existing=existing,
    )

    return {
        "dry_run": False,
        "company_id": resolved_company_id,
        "batch_id": batch_id,
        "batch_key": normalized_batch_key,
        "status": import_repository.BATCH_STATUS_STAGED,
        "stats": stats,
        "preview": preview,
        "issues": issues_preview,
        "candidates_created": created,
        "candidates_existing": existing,
    }


def review_candidate(
    *,
    company_id: int,
    candidate_id: int,
    approved: bool,
    reviewer: str | None,
    review_notes: str | None = None,
    scenario_type: str | None = None,
    customer_profile: str | None = None,
    quality_score: float | None = None,
    promote_to_example: bool = True,
) -> dict[str, Any]:
    """Aprova ou rejeita um candidato. Só a aprovação promove para treino."""
    resolved_company_id = require_company_id(company_id, context="training candidate review")
    candidate = import_repository.get_candidate(
        company_id=resolved_company_id,
        candidate_id=candidate_id,
    )
    if not candidate:
        raise ConversationImportError(
            f"Training candidate {candidate_id} not found for company_id={resolved_company_id}."
        )

    current_status = str(candidate.get("review_status") or import_repository.REVIEW_PENDING)
    already_promoted_id = candidate.get("promoted_message_example_id")

    if not approved:
        if already_promoted_id is not None:
            # Rejeitar algo já promovido não é uma revisão: é uma reversão, que
            # precisa de um comando próprio para não deixar o exemplo órfão.
            raise CandidateStateError(
                f"Candidate {candidate_id} was already promoted to message_example "
                f"{already_promoted_id}. Revert the promotion explicitly before rejecting."
            )
        import_repository.set_candidate_review(
            company_id=resolved_company_id,
            candidate_id=candidate_id,
            review_status=import_repository.REVIEW_REJECTED,
            reviewed_by=reviewer,
            review_notes=review_notes,
            promotion_state="rejected",
        )
        return {
            "candidate_id": candidate_id,
            "review_status": import_repository.REVIEW_REJECTED,
            "promoted_message_example_id": None,
            "already_promoted": False,
        }

    if current_status == import_repository.REVIEW_REJECTED:
        raise CandidateStateError(
            f"Candidate {candidate_id} is rejected. Reopen it explicitly before approving."
        )

    # Aprovação é idempotente: a segunda chamada devolve o mesmo exemplo.
    existing_example = repository.get_message_example_by_candidate(
        company_id=resolved_company_id,
        source_candidate_id=candidate_id,
    )
    if existing_example is not None:
        return {
            "candidate_id": candidate_id,
            "review_status": import_repository.REVIEW_APPROVED,
            "promoted_message_example_id": int(existing_example["id"]),
            "already_promoted": True,
        }

    resolved_scenario, _ = normalize_with_fallback(
        "scenario_type",
        scenario_type or candidate.get("scenario_type"),
    )
    resolved_profile = None
    if customer_profile or candidate.get("customer_profile"):
        resolved_profile, _ = normalize_with_fallback(
            "customer_profile",
            customer_profile or candidate.get("customer_profile"),
        )
    resolved_profile_name, _ = normalize_with_fallback(
        "profile_name",
        candidate.get("profile_name"),
    )

    message_example_id = None
    if promote_to_example:
        conversation_context = json.loads(str(candidate.get("context_json") or "[]"))
        message_example_id = repository.create_message_example(
            company_id=resolved_company_id,
            profile_name=resolved_profile_name,
            scenario_type=resolved_scenario,
            customer_profile=resolved_profile,
            customer_message=str(candidate["customer_message"]),
            approved_reply=str(candidate["approved_reply"]),
            style_notes=review_notes,
            quality_score=float(
                quality_score if quality_score is not None else candidate.get("quality_score") or 0
            ),
            used_for_training=True,
            approval_status="approved",
            source_type="conversation_import",
            source_reference=f"training_candidate:{candidate_id}",
            # Lineage estruturado: o agrupamento do dataset vem daqui, não de
            # uma string sobrecarregada.
            source_group_key=str(candidate.get("group_key") or ""),
            conversation_context=conversation_context,
            source_batch_id=candidate.get("batch_id"),
            source_conversation_id=candidate.get("conversation_id"),
            source_candidate_id=candidate_id,
            approved_by=reviewer,
            pii_scrubbed=True,
            active=True,
        )

    import_repository.set_candidate_review(
        company_id=resolved_company_id,
        candidate_id=candidate_id,
        review_status=import_repository.REVIEW_APPROVED,
        reviewed_by=reviewer,
        review_notes=review_notes,
        promoted_message_example_id=message_example_id,
        promotion_state="promoted" if message_example_id is not None else "approved_not_promoted",
    )

    log_event(
        logger,
        logging.INFO,
        "conversation_import.candidate_approved",
        company_id=resolved_company_id,
        candidate_id=candidate_id,
        message_example_id=message_example_id,
    )
    return {
        "candidate_id": candidate_id,
        "review_status": import_repository.REVIEW_APPROVED,
        "promoted_message_example_id": message_example_id,
        "already_promoted": False,
    }


def get_conversation_detail(*, company_id: int, conversation_id: int) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="import staging read")
    messages = import_repository.get_staged_messages(
        company_id=resolved_company_id,
        conversation_id=conversation_id,
    )
    return {
        "company_id": resolved_company_id,
        "conversation_id": conversation_id,
        "messages": messages,
    }
