"""Importador legado de pares curados pergunta/resposta.

Mantido para compatibilidade de `POST /admin/training-import`. O caminho
governado para conversas brutas multi-turno é
`POST /admin/conversation-import` (`conversation_import_service`).

Diferenças em relação à versão anterior:
- taxonomia normalizada pelos aliases canônicos;
- PII anonimizada antes de qualquer escrita;
- `used_for_training` nunca é ligado automaticamente: o exemplo entra como
  `approval_status='pending'` e só vira dataset após revisão humana.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.core.taxonomy import DEFAULT_PROFILE_NAME, normalize_with_fallback
from app.core.tenancy import require_company_id
from app.services.company_config_service import get_company_config
from app.services.pii_service import POLICY_STANDARD, anonymize_text
from app.services.training_export_service import is_example_eligible_for_training
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()

FIELD_ALIASES = {
    "contact_id": ["contact_id", "phone", "telefone", "whatsapp", "from_number"],
    "contact_name": ["contact_name", "name", "nome", "customer_name", "cliente"],
    "customer_profile": ["customer_profile", "perfil_cliente", "customer_type", "tipo_cliente"],
    "profile_name": ["profile_name", "perfil_atendimento", "profile"],
    "scenario_type": ["scenario_type", "cenario", "scenario"],
    "customer_message": ["customer_message", "message", "mensagem_cliente", "pergunta", "inbound_message"],
    "approved_reply": ["approved_reply", "agent_reply", "resposta", "reply", "outbound_message"],
    "style_notes": ["style_notes", "observacoes_estilo", "notes"],
    "quality_score": ["quality_score", "score", "nota_qualidade"],
    "used_for_training": ["used_for_training", "training", "usar_para_treino"],
}


class TrainingImportError(Exception):
    pass


def _normalize_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _extract_field(record: dict[str, Any], field_name: str) -> Any:
    aliases = FIELD_ALIASES.get(field_name, [field_name])
    for alias in aliases:
        if alias in record and record[alias] not in (None, ""):
            return record[alias]
    return None


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "sim", "yes", "y"}:
        return True
    if normalized in {"0", "false", "nao", "não", "no", "n"}:
        return False
    return default


def _parse_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_records(
    *,
    fmt: str,
    raw_text: str | None,
    records: list[dict[str, Any]],
    delimiter: str,
) -> list[dict[str, Any]]:
    normalized_format = str(fmt or "csv").strip().lower()
    if normalized_format == "json":
        if records:
            return [record for record in records if isinstance(record, dict)]
        if not raw_text:
            return []
        parsed = json.loads(raw_text)
        if isinstance(parsed, list):
            return [record for record in parsed if isinstance(record, dict)]
        if isinstance(parsed, dict):
            nested_records = parsed.get("records")
            if isinstance(nested_records, list):
                return [record for record in nested_records if isinstance(record, dict)]
        raise TrainingImportError("JSON import must be a list of records or an object with 'records'.")

    if records:
        return [record for record in records if isinstance(record, dict)]

    if not raw_text:
        return []

    reader = csv.DictReader(io.StringIO(raw_text), delimiter=(delimiter or ",")[0])
    return [dict(row) for row in reader]


def _normalize_import_record(
    record: dict[str, Any],
    *,
    default_profile_name: str,
    default_scenario_type: str,
    default_quality_score: float,
    active_default: bool,
    pii_policy: str,
) -> dict[str, Any]:
    raw_customer_profile = _normalize_text(_extract_field(record, "customer_profile"))
    customer_profile = None
    profile_fallback_used = False
    if raw_customer_profile:
        customer_profile, profile_fallback_used = normalize_with_fallback(
            "customer_profile", raw_customer_profile
        )

    profile_name, profile_name_fallback = normalize_with_fallback(
        "profile_name",
        _normalize_text(_extract_field(record, "profile_name")) or default_profile_name,
    )
    scenario_type, scenario_fallback = normalize_with_fallback(
        "scenario_type",
        _normalize_text(_extract_field(record, "scenario_type")) or default_scenario_type,
    )

    customer_message, customer_report = anonymize_text(
        _extract_field(record, "customer_message"), policy=pii_policy
    )
    approved_reply, reply_report = anonymize_text(
        _extract_field(record, "approved_reply"), policy=pii_policy
    )

    normalized = {
        "contact_id": _normalize_text(_extract_field(record, "contact_id")),
        "contact_name": _normalize_text(_extract_field(record, "contact_name")),
        "customer_profile": customer_profile,
        "profile_name": profile_name or DEFAULT_PROFILE_NAME,
        "scenario_type": scenario_type,
        "customer_message": customer_message.strip() or None,
        "approved_reply": approved_reply.strip() or None,
        "style_notes": _normalize_text(_extract_field(record, "style_notes")),
        "quality_score": _parse_float(_extract_field(record, "quality_score"), default_quality_score),
        "used_for_training": _parse_bool(_extract_field(record, "used_for_training"), False),
        "active": _parse_bool(record.get("active"), active_default),
        "pii_replacements": (customer_report.counts + reply_report.counts).total(),
        "taxonomy_fallbacks": [
            name
            for name, used in (
                ("customer_profile", profile_fallback_used),
                ("profile_name", profile_name_fallback),
                ("scenario_type", scenario_fallback),
            )
            if used
        ],
    }
    normalized["eligible_for_training"] = is_example_eligible_for_training(normalized)
    return normalized


def import_training_records(
    *,
    company_id: int | None,
    fmt: str,
    raw_text: str | None,
    records: list[dict[str, Any]],
    delimiter: str = ",",
    dry_run: bool = True,
    update_contacts: bool = True,
    create_message_examples: bool = True,
    auto_promote_eligible_examples: bool = False,
    default_profile_name: str = "default",
    default_scenario_type: str = "general",
    default_quality_score: float = 7.0,
    active_default: bool = True,
) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="training import")
    company_config = get_company_config(resolved_company_id)
    pii_policy = str(company_config.policy("pii_policy", POLICY_STANDARD))

    parsed_records = _parse_records(
        fmt=fmt,
        raw_text=raw_text,
        records=records,
        delimiter=delimiter,
    )
    if not parsed_records:
        raise TrainingImportError("No importable records were provided.")

    normalized_records = [
        _normalize_import_record(
            record,
            default_profile_name=default_profile_name,
            default_scenario_type=default_scenario_type,
            default_quality_score=default_quality_score,
            active_default=active_default,
            pii_policy=pii_policy,
        )
        for record in parsed_records
    ]

    contact_updates = 0
    examples_created = 0
    eligible_examples = 0
    skipped_examples = 0
    pii_replacements = 0
    taxonomy_fallbacks = 0
    errors: list[dict[str, Any]] = []
    preview: list[dict[str, Any]] = []

    for index, record in enumerate(normalized_records, start=1):
        if record["eligible_for_training"]:
            eligible_examples += 1
        pii_replacements += int(record["pii_replacements"])
        taxonomy_fallbacks += len(record["taxonomy_fallbacks"])

        if len(preview) < 20:
            preview.append(
                {
                    "row_number": index,
                    "contact_id": record.get("contact_id"),
                    "contact_name": record.get("contact_name"),
                    "customer_profile": record.get("customer_profile"),
                    "profile_name": record.get("profile_name"),
                    "scenario_type": record.get("scenario_type"),
                    "eligible_for_training": record.get("eligible_for_training"),
                    # Continua refletindo apenas o pedido do arquivo; a
                    # liberação real depende de revisão humana.
                    "used_for_training": record.get("used_for_training"),
                    "pii_replacements": record.get("pii_replacements"),
                    "taxonomy_fallbacks": record.get("taxonomy_fallbacks"),
                }
            )

        if update_contacts and record.get("contact_id"):
            if not dry_run:
                repository.save_contact(
                    contact_id=str(record["contact_id"]),
                    contact_name=record.get("contact_name"),
                    company_id=resolved_company_id,
                    customer_profile=record.get("customer_profile"),
                )
            contact_updates += 1

        if not create_message_examples:
            continue

        if not record.get("customer_message") or not record.get("approved_reply"):
            skipped_examples += 1
            errors.append(
                {
                    "row_number": index,
                    "reason": "customer_message and approved_reply are required to create a message example.",
                }
            )
            continue

        if not dry_run:
            repository.create_message_example(
                company_id=resolved_company_id,
                profile_name=str(record["profile_name"]),
                scenario_type=str(record["scenario_type"]),
                customer_profile=record.get("customer_profile"),
                customer_message=str(record["customer_message"]),
                approved_reply=str(record["approved_reply"]),
                style_notes=record.get("style_notes"),
                quality_score=float(record["quality_score"]),
                # Conteúdo importado entra pendente de revisão. Nada vira
                # dataset de treino sem aprovação humana explícita.
                used_for_training=False,
                approval_status="pending",
                source_type="training_import",
                pii_scrubbed=True,
                active=bool(record["active"]),
            )
        examples_created += 1

    return {
        "dry_run": dry_run,
        "company_id": resolved_company_id,
        "parsed_records": len(parsed_records),
        "contacts_to_update": contact_updates,
        "message_examples_to_create": examples_created,
        "eligible_examples": eligible_examples,
        "skipped_examples": skipped_examples,
        "pii_replacements": pii_replacements,
        "taxonomy_fallbacks": taxonomy_fallbacks,
        "pii_policy": pii_policy,
        "auto_promotion_disabled": True,
        "review_required": True,
        "errors": errors[:50],
        "preview": preview,
    }
