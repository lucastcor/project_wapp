"""Estado de canal WhatsApp por empresa e contato (migration 071).

Janela de atendimento, opt-out, consentimento por finalidade, templates e
status de entrega. Timestamps em UTC ISO de largura fixa: comparados como texto.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.tenancy import require_company_id
from app.storage.migrations_channel import (
    CONSENT_PURPOSES,
    DELIVERY_PURPOSES,
    DELIVERY_STATUSES,
    MAX_TEMPLATE_PARAMETERS,
    TEMPLATE_CATEGORIES,
    TEMPLATE_STATUSES,
)
from app.storage.sql_support import execute_rowcount, fetch_all, fetch_one, transaction

# Status de entrega que cada novo status pode substituir: a entrega só avança
# (sent < delivered < read) e `failed` só substitui envio ainda não entregue.
_REPLACEABLE_DELIVERY_STATUSES: dict[str, tuple[str, ...]] = {
    "sent": (),
    "delivered": ("sent",),
    "read": ("sent", "delivered"),
    "failed": ("sent",),
}


def iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _now(now: datetime | None) -> str:
    return iso(now or datetime.now(UTC))


def _require_contact(contact_id: Any) -> str:
    if not isinstance(contact_id, str) or not contact_id.strip():
        raise ValueError("contact_id must be a non-empty string.")
    return contact_id


# ── janela e opt-out ──────────────────────────────────────────────────


def record_inbound_with_cursor(cursor: Any, *, company_id: int, contact_id: str, received_at: str) -> None:
    """Registra a última entrada do cliente na transação de quem persiste a mensagem."""
    cursor.execute(
        """
        INSERT INTO whatsapp_contact_states (company_id, contact_id, last_inbound_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (company_id, contact_id) DO UPDATE SET
            last_inbound_at = CASE
                WHEN whatsapp_contact_states.last_inbound_at IS NULL
                  OR excluded.last_inbound_at > whatsapp_contact_states.last_inbound_at
                THEN excluded.last_inbound_at
                ELSE whatsapp_contact_states.last_inbound_at
            END,
            updated_at = excluded.updated_at
        """,
        (company_id, contact_id, received_at, received_at),
    )


def record_inbound(*, company_id: int, contact_id: str, received_at: datetime | None = None) -> None:
    cid = require_company_id(company_id, context="whatsapp inbound window")
    stamp = _now(received_at)
    with transaction() as cursor:
        record_inbound_with_cursor(cursor, company_id=cid, contact_id=_require_contact(contact_id), received_at=stamp)


def get_contact_state(*, company_id: int, contact_id: str) -> dict[str, Any] | None:
    cid = require_company_id(company_id, context="whatsapp contact state")
    return fetch_one(
        """
        SELECT company_id, contact_id, last_inbound_at, opted_out_at, opt_out_source, updated_at
        FROM whatsapp_contact_states
        WHERE company_id = ? AND contact_id = ?
        """,
        (cid, _require_contact(contact_id)),
    )


def set_opt_out(*, company_id: int, contact_id: str, source: str, now: datetime | None = None) -> None:
    cid = require_company_id(company_id, context="whatsapp opt-out")
    stamp = _now(now)
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO whatsapp_contact_states (company_id, contact_id, opted_out_at, opt_out_source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (company_id, contact_id) DO UPDATE SET
                opted_out_at = COALESCE(whatsapp_contact_states.opted_out_at, excluded.opted_out_at),
                opt_out_source = COALESCE(whatsapp_contact_states.opt_out_source, excluded.opt_out_source),
                updated_at = excluded.updated_at
            """,
            (cid, _require_contact(contact_id), stamp, source, stamp),
        )


def clear_opt_out(*, company_id: int, contact_id: str, now: datetime | None = None) -> bool:
    cid = require_company_id(company_id, context="whatsapp opt-in")
    return execute_rowcount(
        """
        UPDATE whatsapp_contact_states
        SET opted_out_at = NULL, opt_out_source = NULL, updated_at = ?
        WHERE company_id = ? AND contact_id = ? AND opted_out_at IS NOT NULL
        """,
        (_now(now), cid, _require_contact(contact_id)),
    ) == 1


def is_opted_out(*, company_id: int, contact_id: str) -> bool:
    state = get_contact_state(company_id=company_id, contact_id=contact_id)
    return bool(state and state.get("opted_out_at"))


# ── consentimento ─────────────────────────────────────────────────────


def set_consent(
    *,
    company_id: int,
    contact_id: str,
    purpose: str,
    status: str,
    source: str,
    evidence_ref: str | None = None,
    now: datetime | None = None,
) -> None:
    cid = require_company_id(company_id, context="contact consent")
    if purpose not in CONSENT_PURPOSES:
        raise ValueError(f"Unknown consent purpose: {purpose!r}.")
    if status not in ("granted", "revoked"):
        raise ValueError("Consent status must be granted or revoked.")
    if not str(source or "").strip():
        raise ValueError("Consent source is required.")
    stamp = _now(now)
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO contact_consents (
                company_id, contact_id, purpose, status, source, evidence_ref, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (company_id, contact_id, purpose) DO UPDATE SET
                status = excluded.status,
                source = excluded.source,
                evidence_ref = excluded.evidence_ref,
                updated_at = excluded.updated_at
            """,
            (cid, _require_contact(contact_id), purpose, status, source, evidence_ref, stamp, stamp),
        )


def get_consent_status(*, company_id: int, contact_id: str, purpose: str) -> str | None:
    cid = require_company_id(company_id, context="contact consent")
    row = fetch_one(
        "SELECT status FROM contact_consents WHERE company_id = ? AND contact_id = ? AND purpose = ?",
        (cid, _require_contact(contact_id), purpose),
    )
    return str(row["status"]) if row else None


# ── templates ─────────────────────────────────────────────────────────


def upsert_template(
    *,
    company_id: int,
    purpose: str,
    name: str,
    language_code: str,
    category: str,
    status: str,
    parameter_count: int,
    active: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    cid = require_company_id(company_id, context="whatsapp template")
    if purpose not in DELIVERY_PURPOSES:
        raise ValueError(f"Unknown delivery purpose: {purpose!r}.")
    if category not in TEMPLATE_CATEGORIES:
        raise ValueError(f"Unknown template category: {category!r}.")
    if status not in TEMPLATE_STATUSES:
        raise ValueError(f"Unknown template status: {status!r}.")
    if purpose != "marketing" and category == "marketing":
        # Publicidade não vira `utility` nem é enviada por finalidade de serviço.
        raise ValueError("Marketing templates are only allowed for the marketing purpose.")
    if isinstance(parameter_count, bool) or not isinstance(parameter_count, int) or not (
        0 <= parameter_count <= MAX_TEMPLATE_PARAMETERS
    ):
        raise ValueError(f"parameter_count must be between 0 and {MAX_TEMPLATE_PARAMETERS}.")
    if not str(name or "").strip() or not str(language_code or "").strip():
        raise ValueError("Template name and language_code are required.")
    stamp = _now(now)
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO whatsapp_templates (
                company_id, purpose, name, language_code, category, status,
                parameter_count, active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (company_id, purpose, language_code) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                status = excluded.status,
                parameter_count = excluded.parameter_count,
                active = excluded.active,
                updated_at = excluded.updated_at
            """,
            (cid, purpose, name.strip(), language_code.strip(), category, status,
             parameter_count, int(active), stamp, stamp),
        )
    template = get_template(company_id=cid, purpose=purpose, language_code=language_code.strip())
    if template is None:  # pragma: no cover - acabou de ser gravado
        raise RuntimeError("Template could not be read back.")
    return template


def get_template(*, company_id: int, purpose: str, language_code: str) -> dict[str, Any] | None:
    cid = require_company_id(company_id, context="whatsapp template")
    return fetch_one(
        """
        SELECT id, company_id, purpose, name, language_code, category, status,
               parameter_count, active, created_at, updated_at
        FROM whatsapp_templates
        WHERE company_id = ? AND purpose = ? AND language_code = ?
        """,
        (cid, purpose, language_code),
    )


def list_templates(*, company_id: int) -> list[dict[str, Any]]:
    cid = require_company_id(company_id, context="whatsapp template")
    return fetch_all(
        """
        SELECT id, company_id, purpose, name, language_code, category, status,
               parameter_count, active, created_at, updated_at
        FROM whatsapp_templates
        WHERE company_id = ?
        ORDER BY purpose, language_code
        """,
        (cid,),
    )


# ── status de entrega ─────────────────────────────────────────────────


def record_delivery_status(
    *,
    company_id: int,
    provider_message_id: str,
    status: str,
    provider_timestamp: str | None = None,
    error_code: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persiste um callback de status uma vez e avança o estado sem regredir.

    Duplicado não altera nada. Status fora de ordem (ex.: `delivered` depois de
    `read`) fica registrado como evento, mas não rebaixa a mensagem. Callback de
    mensagem desconhecida é guardado sem vínculo e não toca outra empresa.
    """
    cid = require_company_id(company_id, context="whatsapp delivery status")
    if status not in DELIVERY_STATUSES:
        raise ValueError(f"Unknown delivery status: {status!r}.")
    provider_id = str(provider_message_id or "").strip()
    if not provider_id:
        raise ValueError("provider_message_id is required.")
    received = _now(now)
    with transaction() as cursor:
        cursor.execute(
            """
            SELECT id, delivery_status
            FROM outbound_messages
            WHERE company_id = ? AND provider_message_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (cid, provider_id),
        )
        outbound = cursor.fetchone()
        outbound_id = int(outbound["id"]) if outbound else None
        cursor.execute(
            """
            INSERT INTO whatsapp_delivery_events (
                company_id, provider_message_id, outbound_message_id, status,
                provider_timestamp, error_code, received_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (company_id, provider_message_id, status) DO NOTHING
            """,
            (cid, provider_id, outbound_id, status, provider_timestamp, error_code, received),
        )
        if cursor.rowcount != 1:
            return {"recorded": False, "outbound_message_id": outbound_id}
        if outbound_id is None:
            return {"recorded": True, "outbound_message_id": None, "advanced": False}
        advanced = _advance_delivery_status(cursor, cid, outbound_id, status, received)
        return {"recorded": True, "outbound_message_id": outbound_id, "advanced": advanced}


def _advance_delivery_status(cursor: Any, company_id: int, outbound_id: int, status: str, stamp: str) -> bool:
    replaceable = _REPLACEABLE_DELIVERY_STATUSES[status]
    # Compare-and-set no próprio UPDATE: dois callbacks simultâneos não
    # conseguem rebaixar o estado entre a leitura e a escrita.
    if replaceable:
        placeholders = ", ".join("?" for _status in replaceable)
        cursor.execute(
            "UPDATE outbound_messages SET delivery_status = ?, delivery_updated_at = ? "
            "WHERE id = ? AND company_id = ? AND (delivery_status IS NULL OR delivery_status IN ("
            + placeholders
            + "))",
            (status, stamp, outbound_id, company_id, *replaceable),
        )
    else:
        cursor.execute(
            "UPDATE outbound_messages SET delivery_status = ?, delivery_updated_at = ? "
            "WHERE id = ? AND company_id = ? AND delivery_status IS NULL",
            (status, stamp, outbound_id, company_id),
        )
    return bool(cursor.rowcount == 1)


def link_orphan_delivery_events(
    *,
    company_id: int,
    provider_message_id: str,
    outbound_message_id: int,
    now: datetime | None = None,
) -> int:
    """Vincula callbacks que chegaram antes do `provider_message_id` ser gravado.

    Aplica os status na ordem de entrega, com a mesma regra monotônica.
    """
    cid = require_company_id(company_id, context="whatsapp delivery status link")
    stamp = _now(now)
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE whatsapp_delivery_events
            SET outbound_message_id = ?
            WHERE company_id = ? AND provider_message_id = ? AND outbound_message_id IS NULL
            """,
            (outbound_message_id, cid, provider_message_id),
        )
        linked = int(cursor.rowcount)
        if not linked:
            return 0
        cursor.execute(
            "SELECT status FROM whatsapp_delivery_events WHERE company_id = ? AND provider_message_id = ?",
            (cid, provider_message_id),
        )
        statuses = {str(row["status"]) for row in cursor.fetchall()}
        for status in DELIVERY_STATUSES:
            if status in statuses:
                _advance_delivery_status(cursor, cid, outbound_message_id, status, stamp)
        return linked
