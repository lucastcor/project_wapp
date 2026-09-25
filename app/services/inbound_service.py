"""Consume persisted ingress outside the HTTP request lifetime."""
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.orchestrator import process_whatsapp_webhook
from app.services.execution_lease import bind_job_lease
from app.services.outbound_dispatch_service import OutboundDispatchError, dispatch_outbound_message
from app.services.realtime_service import publish_incoming_message_event
from app.storage import inbound_repository as queue
from app.storage.repository import RuntimeRepository

# Espera mínima/máxima antes de tentar de novo um envio sem horário do outbound.
_RETRY_FLOOR_SECONDS = 5
_RETRY_CEILING_SECONDS = 300


class StuckOutboundDispatchError(RuntimeError):
    """Outbound preso em `processing` além do TTL: pode ter saído; revisão, não reenvio."""


def _parse_moment(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _retry_at(job: dict[str, Any], outbound: dict[str, Any] | None) -> datetime:
    """Próxima tentativa: o horário do backoff do outbound, ou espera exponencial curta."""
    now = datetime.now(UTC)
    attempts = int(job.get("dispatch_attempts") or 0)
    fallback = now + timedelta(seconds=min(_RETRY_CEILING_SECONDS, _RETRY_FLOOR_SECONDS * (2 ** attempts)))
    scheduled = _parse_moment((outbound or {}).get("next_attempt_at"))
    if scheduled is not None and scheduled > now:
        return scheduled
    return fallback


def _release_dispatch(job: dict[str, Any], recovered: int) -> dict[str, Any]:
    outbound = RuntimeRepository().get_outbound_message(job["outbound_message_id"], company_id=job["company_id"])
    outcome = queue.release_for_retry(job, available_at=_retry_at(job, outbound))
    return {"processed": True, "job_id": job["id"], "recovered": recovered, "retry": outcome == "released",
            "review": outcome == "review"}


def _processing_is_stale(outbound: dict[str, Any] | None) -> bool:
    claimed_at = _parse_moment((outbound or {}).get("claimed_at"))
    if claimed_at is None:
        return False
    return datetime.now(UTC) - claimed_at > timedelta(seconds=max(30, settings.outbound_claim_ttl_seconds))


def run_inbound_iteration() -> dict[str, Any]:
    recovered = queue.recover_abandoned()
    job = queue.claim_next()
    if job is None:
        return {"processed": False, "recovered": recovered}
    try:
        company = RuntimeRepository().get_company(job["company_id"])
        if not company or not company.get("active"):
            raise RuntimeError("Inbound company is inactive or missing.")
        if job["status"] == "processing":
            payload = json.loads(job["payload_json"])
            # Fencing (P06): effects inside the orchestrator renew this lease first,
            # so a worker that lost the job cannot cancel or book anything.
            with bind_job_lease(job):
                result = process_whatsapp_webhook(
                    from_number=payload["from_number"], from_name=payload.get("from_name"), text=payload["text"],
                    company_id=job["company_id"], company_slug=payload.get("company_slug"),
                    # Stable per provider message: a redelivered "sim" must not confirm twice.
                    message_ref=f"whatsapp:{job['provider_message_id']}",
                    content_type=str(payload.get("content_type") or "text"),
                )
            queue.save_result(job, result)
            # Realtime notification failure cannot invalidate committed work.
            try:
                publish_incoming_message_event(result, contact_id=job["contact_id"])
            except Exception:
                pass
        else:
            try:
                result = dispatch_outbound_message(job["outbound_message_id"], channel="whatsapp", company_id=job["company_id"])
            except OutboundDispatchError as exc:
                if exc.kind == "uncertain":
                    # Pode ter sido aceita pela Meta: revisão, nunca novo envio.
                    raise
                if exc.retryable:
                    # Limite de taxa/indisponibilidade: o job volta para a fila só
                    # depois da espera do outbound, sem ocupar o worker das outras
                    # empresas; a ordem do contato é mantida.
                    return _release_dispatch(job, recovered)
                # Bloqueio de política ou recusa definitiva: o dead letter fica
                # visível no outbound e a conversa não trava esperando revisão.
                queue.finish_job(job)
                return {"processed": True, "job_id": job["id"], "recovered": recovered, "dead_letter": True}
            if result["delivery_status"] == "already_processing":
                current = RuntimeRepository().get_outbound_message(
                    job["outbound_message_id"], company_id=job["company_id"]
                )
                if _processing_is_stale(current):
                    raise StuckOutboundDispatchError("Outbound claim expired while processing.")
                return _release_dispatch(job, recovered)
            if result["delivery_status"] not in {"sent", "already_sent"}:
                raise RuntimeError("Outbound delivery has an unresolved state.")
            queue.finish_job(job)
    except Exception as exc:
        # No raw prompt, contact or provider error body in queue diagnostics.
        queue.finish_job(job, error=type(exc).__name__)
    return {"processed": True, "job_id": job["id"], "recovered": recovered}
