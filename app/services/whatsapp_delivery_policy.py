"""Decisão de envio WhatsApp no dispatch efetivo (P05, W03/W06).

Contrato: docs/piloto/CONTRATOS.md, "Canal WhatsApp". A decisão acontece quando
a mensagem sai, não quando entra na fila: opt-out registrado depois do
enfileiramento ainda barra o envio.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.storage import channel_delivery_repository as channel

CUSTOMER_SERVICE_WINDOW = timedelta(hours=24)
DEFAULT_TEMPLATE_LANGUAGE = "pt_BR"
MAX_TEMPLATE_PARAMETER_LENGTH = 1024
# Finalidades que não são contato proativo com o cliente final.
OPT_OUT_EXEMPT_PURPOSES = frozenset({"reply", "internal_notice"})
CONSENT_REQUIRED_PURPOSES = frozenset({"marketing"})

_SOURCE_PURPOSES: dict[str, str] = {
    "whatsapp_auto_reply": "reply",
    # Confirmação de SAIR/VOLTAR: resposta ao comando do próprio cliente.
    "whatsapp_channel_reply": "reply",
    "appointment_queue_waiting_ack": "reply",
    "appointment_queue_keep_waiting": "reply",
    "owner_resolution": "service_followup",
    "manual_booking_confirmation": "service_followup",
    "reminder_customer": "reminders",
    "reminder_internal": "internal_notice",
    "appointment_queue_offer": "queue_offers",
    "appointment_queue_waiting_followup": "queue_offers",
}

_OPT_OUT_COMMANDS = frozenset({"sair", "parar", "stop", "cancelar mensagens", "descadastrar"})
_OPT_IN_COMMANDS = frozenset({"voltar", "start"})


@dataclass(frozen=True)
class DeliveryDecision:
    mode: str  # text | template | blocked
    purpose: str
    reason: str
    template: dict[str, Any] | None = None
    parameters: list[str] = field(default_factory=list)


def purpose_for_source(source: str | None) -> str:
    normalized = str(source or "").strip()
    if normalized.startswith("automation:"):
        return "automation"
    # Origem desconhecida é tratada como contato proativo: a regra conservadora.
    return _SOURCE_PURPOSES.get(normalized, "service_followup")


def parse_channel_command(message: str | None) -> str | None:
    """`opt_out`, `opt_in` ou None. Só a mensagem inteira conta como comando."""
    decomposed = unicodedata.normalize("NFKD", str(message or "").strip().lower())
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
    normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", folded)).strip()
    if normalized in _OPT_OUT_COMMANDS:
        return "opt_out"
    if normalized in _OPT_IN_COMMANDS:
        return "opt_in"
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _template_parameters(outbound: dict[str, Any]) -> list[str] | None:
    raw = outbound.get("template_parameters_json")
    if raw in (None, ""):
        return []
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(values, list):
        return None
    parameters: list[str] = []
    for value in values:
        text = str(value or "").strip()
        # Parâmetro de template da Meta não aceita quebra de linha, tab nem
        # sequência longa de espaços.
        if not text or len(text) > MAX_TEMPLATE_PARAMETER_LENGTH or re.search(r"[\n\t]| {5,}", text):
            return None
        parameters.append(text)
    return parameters


def template_language_for(company_id: int) -> str:
    from app.services.company_config_service import CompanyConfigError, get_company_config
    from app.services.company_policy_service import resolve_effective_policy

    try:
        return resolve_effective_policy(get_company_config(company_id)).channel.template_language
    except CompanyConfigError:
        return DEFAULT_TEMPLATE_LANGUAGE


def decide_whatsapp_delivery(
    outbound: dict[str, Any],
    *,
    now: datetime,
    template_language: str | None = None,
) -> DeliveryDecision:
    purpose = purpose_for_source(outbound.get("source"))
    company_id = outbound.get("company_id")
    if company_id is None:
        # Modo legado de número único sem empresa: não há estado a consultar.
        return DeliveryDecision("text", purpose, "no_company_scope")
    cid = int(company_id)
    contact_id = str(outbound["contact_id"])

    state = channel.get_contact_state(company_id=cid, contact_id=contact_id) or {}
    if state.get("opted_out_at") and purpose not in OPT_OUT_EXEMPT_PURPOSES:
        return DeliveryDecision("blocked", purpose, "opted_out")

    if purpose != "reply":
        consent = channel.get_consent_status(company_id=cid, contact_id=contact_id, purpose=purpose)
        if consent == "revoked":
            return DeliveryDecision("blocked", purpose, "consent_revoked")
        if purpose in CONSENT_REQUIRED_PURPOSES and consent != "granted":
            return DeliveryDecision("blocked", purpose, "consent_required")

    last_inbound = _parse_timestamp(state.get("last_inbound_at"))
    if last_inbound is not None and now - last_inbound < CUSTOMER_SERVICE_WINDOW:
        return DeliveryDecision("text", purpose, "within_service_window")

    language = template_language or template_language_for(cid)
    template = channel.get_template(company_id=cid, purpose=purpose, language_code=language)
    expected_category = "marketing" if purpose == "marketing" else "utility"
    if (
        not template
        or template.get("status") != "approved"
        or not int(template.get("active") or 0)
        or template.get("category") != expected_category
    ):
        return DeliveryDecision("blocked", purpose, "outside_window_no_template")
    parameters = _template_parameters(outbound)
    if parameters is None or len(parameters) != int(template.get("parameter_count") or 0):
        return DeliveryDecision("blocked", purpose, "template_parameters_invalid", template=template)
    return DeliveryDecision("template", purpose, "outside_service_window", template=template, parameters=parameters)
