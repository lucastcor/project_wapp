"""Atomic queue operations. Global claims are worker-only; reads require a tenant."""
from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.tenancy import require_company_id
from app.storage.channel_delivery_repository import record_inbound_with_cursor
from app.storage.sql_support import execute_rowcount, fetch_all, fetch_one, transaction

CHANNEL_COMMAND_ACTIONS = frozenset({"opt_out", "opt_in"})
CHANNEL_REPLY_SOURCE = "whatsapp_channel_reply"


def now_iso() -> str:
    # Largura fixa: leases e ordem são comparados como texto em SQL, e
    # `isoformat()` sem timespec omite a fração quando ela é zero.
    return _iso(datetime.now(UTC))


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _received_at(value: Any) -> str:
    """Horário informado pelo provider, nunca no futuro; senão, agora."""
    now = datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return _iso(now)
    if parsed.tzinfo is None:
        return _iso(now)
    return _iso(min(parsed, now))


def enqueue_batch(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accepted = []
    with transaction() as cursor:
        for item in items:
            cid = require_company_id(item.get("company_id"), context="inbound enqueue")
            message_id = str(item["provider_message_id"]).strip()
            if not message_id:
                raise ValueError("provider_message_id is required for durable ingress.")
            cursor.execute("""
                SELECT status FROM inbound_message_receipts
                WHERE company_id = ? AND provider = 'whatsapp' AND provider_message_id = ?
            """, (cid, message_id))
            legacy = cursor.fetchone()
            if legacy and legacy["status"] == "completed":
                accepted.append({"job_id": None, "status": "completed", "duplicate": True})
                continue
            now = now_iso()
            cursor.execute("""
                INSERT INTO inbound_jobs (company_id, provider_message_id, contact_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, provider_message_id) DO NOTHING
            """, (cid, message_id, item["from_number"], json.dumps(item, ensure_ascii=False), now, now))
            inserted = cursor.rowcount == 1
            if inserted and legacy:
                cursor.execute("""
                    UPDATE inbound_jobs SET status = 'review', last_error = 'legacy_processing_result_uncertain'
                    WHERE company_id = ? AND provider_message_id = ?
                """, (cid, message_id))
            # Janela de atendimento de 24 h: conta a partir da mensagem do
            # cliente, gravada na mesma transação que a persiste (071).
            record_inbound_with_cursor(
                cursor,
                company_id=cid,
                contact_id=str(item["from_number"]),
                received_at=_received_at(item.get("received_at")),
            )
            cursor.execute("SELECT id, status FROM inbound_jobs WHERE company_id = ? AND provider_message_id = ?", (cid, message_id))
            row = cursor.fetchone()
            accepted.append({"job_id": row["id"], "status": row["status"], "duplicate": not inserted})
    return accepted


def recover_abandoned() -> int:
    # The orchestrator includes calendar effects. An interrupted attempt must
    # not be replayed blindly; retain it and block that conversation for review.
    return execute_rowcount("""
        UPDATE inbound_jobs SET status = 'review', last_error = 'worker_lease_expired_result_uncertain',
            claim_token = NULL, lease_expires_at = NULL, updated_at = ?
        WHERE status IN ('processing', 'dispatching') AND lease_expires_at <= ?
    """, (now_iso(), now_iso()))


def claim_next() -> dict[str, Any] | None:
    token = secrets.token_urlsafe(24)
    now = now_iso()
    expires = _iso(datetime.now(UTC) + timedelta(seconds=settings.inbound_job_lease_seconds))
    with transaction() as cursor:
        # Um job em espera (073) não é elegível, mas continua bloqueando os
        # seguintes do mesmo contato; outras conversas seguem normalmente.
        cursor.execute("""
            UPDATE inbound_jobs SET status = CASE WHEN status = 'pending' THEN 'processing' ELSE 'dispatching' END,
                claim_token = ?, lease_expires_at = ?, updated_at = ?
            WHERE id = (
                SELECT j.id FROM inbound_jobs j
                WHERE j.status IN ('pending', 'ready')
                AND (j.available_at IS NULL OR j.available_at <= ?)
                AND NOT EXISTS (
                    SELECT 1 FROM inbound_jobs prior
                    WHERE prior.company_id = j.company_id AND prior.contact_id = j.contact_id
                      AND prior.id < j.id AND prior.status != 'completed'
                )
                ORDER BY j.id LIMIT 1
            ) AND status IN ('pending', 'ready')
            RETURNING *
        """, (token, expires, now, now))
        row = cursor.fetchone()
        return dict(row) if row else None


def save_result(job: dict[str, Any], result: dict[str, Any]) -> None:
    # Result and outbound are one commit. No network IO holds this transaction.
    with transaction() as cursor:
        cursor.execute("""
            UPDATE inbound_jobs SET result_json = ?, status = 'ready', updated_at = ?,
                claim_token = NULL, lease_expires_at = NULL
            WHERE id = ? AND company_id = ? AND claim_token = ? AND status = 'processing' AND lease_expires_at > ?
        """, (json.dumps(result, ensure_ascii=False), now_iso(), job["id"], job["company_id"], job["claim_token"], now_iso()))
        if cursor.rowcount != 1:
            raise RuntimeError("Inbound processing claim expired or was lost.")
        if result.get("auto_reply") and str(result.get("reply") or "").strip():
            # SAIR/VOLTAR respondem ao pedido explícito do cliente e valem mesmo
            # com humano na conversa; qualquer outra resposta do bot, não.
            channel_reply = str(result.get("action") or "") in CHANNEL_COMMAND_ACTIONS
            if not channel_reply:
                cursor.execute(
                    "SELECT owner FROM conversation_ownerships WHERE company_id = ? AND contact_id = ?",
                    (job["company_id"], job["contact_id"]),
                )
                owner_row = cursor.fetchone()
                if owner_row and owner_row["owner"] == "human":
                    # Operador assumiu enquanto a resposta era gerada (P06, H01):
                    # a mensagem do cliente já foi registrada; a resposta não sai.
                    cursor.execute("UPDATE inbound_jobs SET status = 'completed' WHERE id = ? AND company_id = ?",
                                   (job["id"], job["company_id"]))
                    return
            cursor.execute("""
                INSERT INTO outbound_messages (company_id, contact_id, message, source, status)
                VALUES (?, ?, ?, ?, 'pending')
            """, (job["company_id"], job["contact_id"], result["reply"],
                  CHANNEL_REPLY_SOURCE if channel_reply else "whatsapp_auto_reply"))
            outbound_id = cursor.lastrowid
            cursor.execute("UPDATE inbound_jobs SET outbound_message_id = ? WHERE id = ? AND company_id = ?",
                           (outbound_id, job["id"], job["company_id"]))
        else:
            cursor.execute("UPDATE inbound_jobs SET status = 'completed' WHERE id = ? AND company_id = ?",
                           (job["id"], job["company_id"]))


def renew_lease(*, job_id: int, company_id: int, claim_token: str) -> bool:
    """Estende a lease somente para quem ainda a detém (fencing, P06)."""
    now = datetime.now(UTC)
    expires = _iso(now + timedelta(seconds=settings.inbound_job_lease_seconds))
    return execute_rowcount("""
        UPDATE inbound_jobs SET lease_expires_at = ?, updated_at = ?
        WHERE id = ? AND company_id = ? AND claim_token = ? AND status = 'processing' AND lease_expires_at > ?
    """, (expires, _iso(now), job_id, company_id, claim_token, _iso(now))) == 1


def release_for_retry(job: dict[str, Any], *, available_at: datetime) -> str:
    """Devolve à fila um job de envio com falha transitória, sem reivindicação antes de `available_at`.

    Retorna `released`, `review` (tentativas esgotadas: a conversa vai para
    revisão em vez de girar para sempre) ou `lost` (a lease já não é deste worker).
    """
    now = now_iso()
    limit = max(1, settings.inbound_dispatch_max_attempts)
    with transaction() as cursor:
        cursor.execute("""
            UPDATE inbound_jobs SET
                dispatch_attempts = dispatch_attempts + 1,
                status = CASE WHEN dispatch_attempts + 1 >= ? THEN 'review' ELSE 'ready' END,
                last_error = CASE WHEN dispatch_attempts + 1 >= ? THEN 'dispatch_retry_exhausted' ELSE last_error END,
                available_at = ?, claim_token = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE id = ? AND company_id = ? AND claim_token = ? AND status = 'dispatching'
            RETURNING status
        """, (limit, limit, _iso(available_at), now, job["id"], job["company_id"], job["claim_token"]))
        row = cursor.fetchone()
    if row is None:
        return "lost"
    return "review" if row["status"] == "review" else "released"


def finish_job(job: dict[str, Any], *, error: str | None = None) -> None:
    execute_rowcount("""
        UPDATE inbound_jobs SET status = ?, last_error = ?, claim_token = NULL, lease_expires_at = NULL, updated_at = ?
        WHERE id = ? AND company_id = ? AND claim_token = ? AND status IN ('processing', 'dispatching')
    """, ("review" if error else "completed", error, now_iso(), job["id"], job["company_id"], job["claim_token"]))


INBOUND_JOB_STATUSES = ("pending", "processing", "ready", "dispatching", "completed", "review")


def list_jobs(company_id: int, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
    cid = require_company_id(company_id, context="inbound listing")
    # Contexto mínimo para a tela de revisão (P07): contato para a tomada humana,
    # sem o texto da mensagem nem o resultado do modelo.
    columns = """
        SELECT id, company_id, contact_id, provider_message_id, status, outbound_message_id, last_error,
               dispatch_attempts, available_at, created_at, updated_at
        FROM inbound_jobs
    """
    if status is None:
        return fetch_all(columns + " WHERE company_id = ? ORDER BY id DESC LIMIT ?", (cid, min(500, max(1, limit))))
    if status not in INBOUND_JOB_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(INBOUND_JOB_STATUSES)}.")
    return fetch_all(
        columns + " WHERE company_id = ? AND status = ? ORDER BY id DESC LIMIT ?",
        (cid, status, min(500, max(1, limit))),
    )


def review_count() -> int:
    row = fetch_one("SELECT COUNT(*) AS n FROM inbound_jobs WHERE status = 'review'")
    return int(row["n"]) if row else 0
