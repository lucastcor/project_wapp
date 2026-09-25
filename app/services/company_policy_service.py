"""Política efetiva da empresa — contrato congelado em P01.

Ver `docs/piloto/CONTRATOS.md`, seção "Política efetiva". Precedência:
restrições fixas de segurança > `policies` publicadas da empresa > preset da
vertical > `POLICY_DEFAULTS`. A mescla preset/override acontece em
`vertical_packs.merge_policies`; este módulo transforma o resultado em tipos
pequenos e é o único lugar que interpreta esses campos. Decisão, handoff,
orquestrador e agenda recebem os tipos daqui em vez de lerem dicionários
soltos, cada um do seu jeito.

Um campo só entra aqui com consumidor executável e teste — configuração que
não muda comportamento não pode ser apresentada como funcional.
"""

from __future__ import annotations

import copy
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.taxonomy import CLASSIFICATION_ALIASES, CLASSIFICATIONS
from app.core.vertical_packs import POLICY_DEFAULTS

if TYPE_CHECKING:
    from app.services.company_config_service import CompanyConfig

logger = get_logger(__name__)

AFTER_HOURS_MODES: tuple[str, ...] = ("continue", "notice_only")
OUTSIDE_WINDOW_ACTIONS: tuple[str, ...] = ("handoff", "deny")
BOOKING_CHANGE_RULE_KEYS: tuple[str, ...] = ("cancellation", "reschedule")
MAX_POLICY_MESSAGE_LENGTH = 1000
MAX_MIN_NOTICE_MINUTES = 60 * 24 * 30
MAX_PROPOSAL_TTL_MINUTES = 24 * 60
# Janela em que um contato não recebe o mesmo aviso automático de novo: cada
# mensagem gerando o mesmo aviso vira spam do próprio bot.
AFTER_HOURS_NOTICE_COOLDOWN = timedelta(hours=12)
HANDOFF_NOTICE_COOLDOWN = timedelta(minutes=30)
UNSUPPORTED_CONTENT_NOTICE_COOLDOWN = timedelta(minutes=30)

DEFAULT_AUTO_REPLY_INTENTS = frozenset({"lead_novo", "cliente_existente", "suporte"})
DEFAULT_HANDOFF_INTENTS = frozenset({"financeiro", "fornecedor"})
# Intenções que a política da empresa não pode liberar para resposta
# automática: spam é sempre ignorado; `workflow` é rótulo interno.
_NON_CONFIGURABLE_INTENTS = frozenset({"spam", "workflow"})

_CONVERSATION_KEYS: dict[str, type] = {
    "greeting_enabled": bool,
    "greeting_message": str,
    "clarification_message": str,
    "handoff_notice_enabled": bool,
    "handoff_notice_message": str,
    "unsupported_content_message": str,
    "opt_out_confirmation_message": str,
    "opt_in_confirmation_message": str,
    "after_hours_mode": str,
    "after_hours_message": str,
}
_BOOKING_RULE_FIELDS = ("allowed", "min_notice_minutes", "outside_window")
_CONFIRMATION_KEYS = ("proposal_ttl_minutes",)
_CHANNEL_KEYS = ("template_language",)
_TEMPLATE_LANGUAGE_PATTERN = re.compile(r"[a-z]{2,3}(_[A-Z]{2})?")
# Mesmas chaves e precedência de `calendar_service._resolve_workday_windows`:
# chave do dia primeiro, `weekdays` para segunda a sexta.
_WEEKDAY_KEYS: dict[int, tuple[str, ...]] = {
    0: ("monday", "segunda"),
    1: ("tuesday", "terca"),
    2: ("wednesday", "quarta"),
    3: ("thursday", "quinta"),
    4: ("friday", "sexta"),
    5: ("saturday", "sabado"),
    6: ("sunday", "domingo"),
}
_BUSINESS_HOURS_DAY_KEYS = frozenset(
    {"weekdays", *(key for keys in _WEEKDAY_KEYS.values() for key in keys)}
)
_CLOSED_DAY_VALUES = frozenset({"fechado", "closed", "off"})
_HOURS_WINDOW_PATTERN = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


class CompanyPolicyError(ValueError):
    """Configuração de política inválida, com mensagem apresentável ao operador."""


@dataclass(frozen=True)
class AutonomyPolicy:
    confidence_threshold: float
    handoff_on_low_confidence: bool
    auto_reply_intents: frozenset[str]
    handoff_intents: frozenset[str]

    def is_low_confidence(self, classification: str, confidence: float) -> bool:
        return classification == "indefinido" or confidence < self.confidence_threshold


@dataclass(frozen=True)
class ConversationPolicy:
    greeting_enabled: bool
    greeting_message: str
    clarification_message: str
    handoff_notice_enabled: bool
    handoff_notice_message: str
    unsupported_content_message: str
    opt_out_confirmation_message: str
    opt_in_confirmation_message: str
    after_hours_mode: str
    after_hours_message: str


@dataclass(frozen=True)
class ChannelPolicy:
    template_language: str


@dataclass(frozen=True)
class BookingChangeRule:
    allowed: bool
    min_notice_minutes: int
    outside_window: str


@dataclass(frozen=True)
class BookingRules:
    cancellation: BookingChangeRule
    reschedule: BookingChangeRule


@dataclass(frozen=True)
class ConfirmationPolicy:
    proposal_ttl_minutes: int
    # Fixo no piloto: confirmação embutida na primeira mensagem nunca executa
    # ação destrutiva; sempre existe uma proposta antes.
    execute_on_initial_message: bool = False


@dataclass(frozen=True)
class EffectivePolicy:
    company_id: int | None
    version: int | None
    source: str
    company_name: str
    autonomy: AutonomyPolicy
    conversation: ConversationPolicy
    booking_rules: BookingRules
    confirmation: ConfirmationPolicy
    channel: ChannelPolicy

    def render(self, template: str) -> str:
        return render_policy_message(template, company_name=self.company_name)


@dataclass(frozen=True)
class BookingChangeDecision:
    outcome: str  # allowed | requires_human | denied
    reason: str

    @property
    def allowed(self) -> bool:
        return self.outcome == "allowed"


# --- coerção tolerante (leitura) ------------------------------------------------
#
# Versões antigas podem ter sido gravadas antes da validação existir. Na
# leitura, um valor inválido volta ao default documentado em vez de derrubar o
# atendimento; a validação estrita acontece na escrita (`validate_policies`).


def _default_section(name: str) -> dict[str, Any]:
    value = POLICY_DEFAULTS.get(name)
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _as_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_text(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()[:MAX_POLICY_MESSAGE_LENGTH]
    return default


def _as_number(value: Any, default: float, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    return number if minimum <= number <= maximum else default


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value if minimum <= value <= maximum else default


def _as_choice(value: Any, default: str, choices: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in choices else default


def canonical_intent(value: Any) -> str | None:
    """Rótulo canônico da taxonomia, ou None quando desconhecido."""
    lowered = str(value or "").strip().lower()
    if lowered in CLASSIFICATIONS:
        return lowered
    return CLASSIFICATION_ALIASES.get(lowered)


def _intent_set(value: Any, default: frozenset[str]) -> frozenset[str]:
    # Lista ausente herda o default; lista vazia é escolha explícita.
    if not isinstance(value, list):
        return default
    intents = {canonical_intent(item) for item in value}
    return frozenset(
        intent for intent in intents if intent and intent not in _NON_CONFIGURABLE_INTENTS
    )


def _booking_rule(section: dict[str, Any], key: str, fallback: dict[str, Any]) -> BookingChangeRule:
    candidate = section.get(key)
    raw: dict[str, Any] = candidate if isinstance(candidate, dict) else {}
    return BookingChangeRule(
        allowed=_as_bool(raw.get("allowed"), bool(fallback.get("allowed", True))),
        min_notice_minutes=_as_int(
            raw.get("min_notice_minutes"),
            int(fallback.get("min_notice_minutes", 0)),
            0,
            MAX_MIN_NOTICE_MINUTES,
        ),
        outside_window=_as_choice(
            raw.get("outside_window"),
            str(fallback.get("outside_window", "handoff")),
            OUTSIDE_WINDOW_ACTIONS,
        ),
    )


def build_effective_policy(
    *,
    context: dict[str, Any] | None,
    policies: dict[str, Any] | None,
    company_id: int | None = None,
    version: int | None = None,
    source: str = "unknown",
    company_name: str | None = None,
) -> EffectivePolicy:
    context_data: dict[str, Any] = context if isinstance(context, dict) else {}
    policies = policies if isinstance(policies, dict) else {}

    response_policy = context_data.get("response_policy")
    response_policy = response_policy if isinstance(response_policy, dict) else {}
    autonomy = AutonomyPolicy(
        confidence_threshold=_as_number(
            policies.get("handoff_confidence_threshold"),
            float(POLICY_DEFAULTS["handoff_confidence_threshold"]),
            0.0,
            1.0,
        ),
        handoff_on_low_confidence=_as_bool(
            policies.get("handoff_on_low_confidence"),
            bool(POLICY_DEFAULTS["handoff_on_low_confidence"]),
        ),
        auto_reply_intents=_intent_set(
            response_policy.get("auto_reply_allowed_for"), DEFAULT_AUTO_REPLY_INTENTS
        ),
        handoff_intents=_intent_set(
            response_policy.get("handoff_required_for"), DEFAULT_HANDOFF_INTENTS
        ),
    )

    conversation_defaults = _default_section("conversation")
    raw_conversation = policies.get("conversation")
    raw_conversation = raw_conversation if isinstance(raw_conversation, dict) else {}
    conversation = ConversationPolicy(
        greeting_enabled=_as_bool(
            raw_conversation.get("greeting_enabled"), conversation_defaults["greeting_enabled"]
        ),
        greeting_message=_as_text(
            raw_conversation.get("greeting_message"), conversation_defaults["greeting_message"]
        ),
        clarification_message=_as_text(
            raw_conversation.get("clarification_message"),
            conversation_defaults["clarification_message"],
        ),
        handoff_notice_enabled=_as_bool(
            raw_conversation.get("handoff_notice_enabled"),
            conversation_defaults["handoff_notice_enabled"],
        ),
        handoff_notice_message=_as_text(
            raw_conversation.get("handoff_notice_message"),
            conversation_defaults["handoff_notice_message"],
        ),
        unsupported_content_message=_as_text(
            raw_conversation.get("unsupported_content_message"),
            conversation_defaults["unsupported_content_message"],
        ),
        opt_out_confirmation_message=_as_text(
            raw_conversation.get("opt_out_confirmation_message"),
            conversation_defaults["opt_out_confirmation_message"],
        ),
        opt_in_confirmation_message=_as_text(
            raw_conversation.get("opt_in_confirmation_message"),
            conversation_defaults["opt_in_confirmation_message"],
        ),
        after_hours_mode=_as_choice(
            raw_conversation.get("after_hours_mode"),
            conversation_defaults["after_hours_mode"],
            AFTER_HOURS_MODES,
        ),
        after_hours_message=_as_text(
            raw_conversation.get("after_hours_message"),
            conversation_defaults["after_hours_message"],
        ),
    )

    booking_defaults = _default_section("booking_rules")
    raw_booking = policies.get("booking_rules")
    raw_booking = raw_booking if isinstance(raw_booking, dict) else {}
    booking_rules = BookingRules(
        cancellation=_booking_rule(raw_booking, "cancellation", booking_defaults["cancellation"]),
        reschedule=_booking_rule(raw_booking, "reschedule", booking_defaults["reschedule"]),
    )

    confirmation_defaults = _default_section("confirmation")
    raw_confirmation = policies.get("confirmation")
    raw_confirmation = raw_confirmation if isinstance(raw_confirmation, dict) else {}
    confirmation = ConfirmationPolicy(
        proposal_ttl_minutes=_as_int(
            raw_confirmation.get("proposal_ttl_minutes"),
            int(confirmation_defaults["proposal_ttl_minutes"]),
            1,
            MAX_PROPOSAL_TTL_MINUTES,
        ),
    )

    channel_defaults = _default_section("channel")
    raw_channel = policies.get("channel")
    raw_channel = raw_channel if isinstance(raw_channel, dict) else {}
    template_language = raw_channel.get("template_language")
    channel_policy = ChannelPolicy(
        template_language=(
            template_language
            if isinstance(template_language, str) and _TEMPLATE_LANGUAGE_PATTERN.fullmatch(template_language)
            else str(channel_defaults["template_language"])
        ),
    )

    display_name = (
        _as_text(context_data.get("company_name"), "")
        or _as_text(company_name, "")
        or "nossa empresa"
    )
    return EffectivePolicy(
        company_id=company_id,
        version=version,
        source=source,
        company_name=display_name,
        autonomy=autonomy,
        conversation=conversation,
        booking_rules=booking_rules,
        confirmation=confirmation,
        channel=channel_policy,
    )


def resolve_effective_policy(config: CompanyConfig) -> EffectivePolicy:
    return build_effective_policy(
        context=config.context,
        policies=config.policies,
        company_id=config.company_id,
        version=config.version,
        source=config.source,
        company_name=config.company_name,
    )


DEFAULT_AUTONOMY_POLICY = build_effective_policy(context=None, policies=None).autonomy


def render_policy_message(template: str, *, company_name: str) -> str:
    # Só um marcador suportado, por substituição literal: `str.format` sobre
    # texto digitado pela empresa quebraria com qualquer chave solta.
    return template.replace("{company_name}", company_name)


# --- validação estrita (escrita) ------------------------------------------------


def _require_bool(value: Any, path: str) -> None:
    if not isinstance(value, bool):
        raise CompanyPolicyError(f"{path} deve ser true ou false.")


def _require_text(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CompanyPolicyError(f"{path} deve ser um texto não vazio.")
    if len(value) > MAX_POLICY_MESSAGE_LENGTH:
        raise CompanyPolicyError(
            f"{path} deve ter no máximo {MAX_POLICY_MESSAGE_LENGTH} caracteres."
        )


def _require_int(value: Any, path: str, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CompanyPolicyError(f"{path} deve ser um inteiro entre {minimum} e {maximum}.")


def _require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CompanyPolicyError(f"{path} deve ser um objeto.")
    return value


def _reject_unknown(section: dict[str, Any], allowed: Any, path: str) -> None:
    unknown = sorted(set(section) - set(allowed))
    if unknown:
        raise CompanyPolicyError(
            f"{path} contém chaves desconhecidas: {', '.join(unknown)}. "
            f"Aceitas: {', '.join(sorted(allowed))}."
        )


def validate_policies(policies: Any) -> None:
    """Recusa override de política inválido antes de gravar uma versão."""
    if policies is None:
        return
    policies = _require_dict(policies, "policies")
    _reject_unknown(policies, POLICY_DEFAULTS, "policies")

    if "handoff_confidence_threshold" in policies:
        threshold = policies["handoff_confidence_threshold"]
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not 0.0 <= float(threshold) <= 1.0
        ):
            raise CompanyPolicyError(
                "policies.handoff_confidence_threshold deve ser um número entre 0 e 1."
            )
    if "handoff_on_low_confidence" in policies:
        _require_bool(policies["handoff_on_low_confidence"], "policies.handoff_on_low_confidence")

    if "conversation" in policies:
        conversation = _require_dict(policies["conversation"], "policies.conversation")
        _reject_unknown(conversation, _CONVERSATION_KEYS, "policies.conversation")
        for key, expected in _CONVERSATION_KEYS.items():
            if key not in conversation:
                continue
            path = f"policies.conversation.{key}"
            if expected is bool:
                _require_bool(conversation[key], path)
            else:
                _require_text(conversation[key], path)
        mode = conversation.get("after_hours_mode")
        if mode is not None and mode not in AFTER_HOURS_MODES:
            raise CompanyPolicyError(
                "policies.conversation.after_hours_mode deve ser um de: "
                + ", ".join(AFTER_HOURS_MODES)
                + "."
            )

    if "booking_rules" in policies:
        booking_rules = _require_dict(policies["booking_rules"], "policies.booking_rules")
        _reject_unknown(booking_rules, BOOKING_CHANGE_RULE_KEYS, "policies.booking_rules")
        for rule_key, raw_rule in booking_rules.items():
            path = f"policies.booking_rules.{rule_key}"
            rule = _require_dict(raw_rule, path)
            _reject_unknown(rule, _BOOKING_RULE_FIELDS, path)
            if "allowed" in rule:
                _require_bool(rule["allowed"], f"{path}.allowed")
            if "min_notice_minutes" in rule:
                _require_int(
                    rule["min_notice_minutes"], f"{path}.min_notice_minutes", 0, MAX_MIN_NOTICE_MINUTES
                )
            if "outside_window" in rule and rule["outside_window"] not in OUTSIDE_WINDOW_ACTIONS:
                raise CompanyPolicyError(
                    f"{path}.outside_window deve ser um de: {', '.join(OUTSIDE_WINDOW_ACTIONS)}."
                )

    if "channel" in policies:
        channel_section = _require_dict(policies["channel"], "policies.channel")
        _reject_unknown(channel_section, _CHANNEL_KEYS, "policies.channel")
        language = channel_section.get("template_language")
        if language is not None and not (
            isinstance(language, str) and _TEMPLATE_LANGUAGE_PATTERN.fullmatch(language)
        ):
            raise CompanyPolicyError(
                "policies.channel.template_language deve ser um código de idioma da Meta, ex.: pt_BR."
            )

    if "confirmation" in policies:
        confirmation = _require_dict(policies["confirmation"], "policies.confirmation")
        _reject_unknown(confirmation, _CONFIRMATION_KEYS, "policies.confirmation")
        if "proposal_ttl_minutes" in confirmation:
            _require_int(
                confirmation["proposal_ttl_minutes"],
                "policies.confirmation.proposal_ttl_minutes",
                1,
                MAX_PROPOSAL_TTL_MINUTES,
            )


def validate_context_policy_fields(context: Any) -> None:
    """Valida os campos do contexto que a política efetiva consome."""
    if not isinstance(context, dict):
        return
    response_policy = context.get("response_policy")
    if response_policy is not None:
        response_policy = _require_dict(response_policy, "response_policy")
        seen: dict[str, str] = {}
        for field in ("auto_reply_allowed_for", "handoff_required_for"):
            values = response_policy.get(field)
            if values is None:
                continue
            path = f"response_policy.{field}"
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                raise CompanyPolicyError(f"{path} deve ser uma lista de intenções.")
            for item in values:
                intent = canonical_intent(item)
                if intent is None:
                    raise CompanyPolicyError(
                        f"{path} contém intenção desconhecida: {item}. "
                        f"Aceitas: {', '.join(CLASSIFICATIONS)}."
                    )
                if intent in _NON_CONFIGURABLE_INTENTS:
                    raise CompanyPolicyError(f"{path} não pode incluir {intent}.")
                previous = seen.get(intent)
                if previous is not None and previous != field:
                    raise CompanyPolicyError(
                        f"A intenção {intent} não pode estar ao mesmo tempo em "
                        "auto_reply_allowed_for e handoff_required_for."
                    )
                seen[intent] = field

    business_hours = context.get("business_hours")
    if isinstance(business_hours, dict) and business_hours.get("timezone"):
        try:
            ZoneInfo(str(business_hours["timezone"]))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CompanyPolicyError(
                f"business_hours.timezone não é um fuso IANA válido: {business_hours['timezone']}."
            ) from exc
    if isinstance(business_hours, dict):
        for key, value in business_hours.items():
            if _fold(key) not in _BUSINESS_HOURS_DAY_KEYS:
                continue
            if _parse_hours_value(value) is None:
                raise CompanyPolicyError(
                    f"business_hours.{key} não é um horário válido. Use 'HH:MM-HH:MM' "
                    "(várias janelas separadas por vírgula) ou 'fechado'; uma janela que "
                    "passa da meia-noite deve ser dividida entre os dois dias."
                )


# --- consumidores puros ------------------------------------------------------------


def evaluate_booking_change(
    rule: BookingChangeRule,
    *,
    starts_at: datetime,
    now: datetime,
) -> BookingChangeDecision:
    """Aplica prazo/permissão de cancelamento ou remarcação.

    Datas sem timezone são recusadas: comparar horário local ingênuo com UTC é
    exatamente o erro de virada de dia que esta regra não pode cometer.
    """
    if starts_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("evaluate_booking_change exige datetimes com timezone.")
    blocked = "requires_human" if rule.outside_window == "handoff" else "denied"
    if not rule.allowed:
        return BookingChangeDecision(blocked, "change_not_allowed")
    if starts_at <= now:
        return BookingChangeDecision(blocked, "booking_already_started")
    if starts_at - now < timedelta(minutes=rule.min_notice_minutes):
        return BookingChangeDecision(blocked, "inside_minimum_notice")
    return BookingChangeDecision("allowed", "within_policy")


def _company_zone(business_hours: dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(str(business_hours.get("timezone") or settings.calendar_timezone))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(settings.calendar_timezone)


def _fold(value: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _parse_hours_value(value: Any) -> list[tuple[time, time]] | None:
    """Janelas de um dia: lista vazia = fechado; None = valor ilegível."""
    if isinstance(value, list):
        collected: list[tuple[time, time]] = []
        for item in value:
            parsed = _parse_hours_value(item)
            if parsed is None:
                return None
            collected.extend(parsed)
        return collected
    if not isinstance(value, str):
        return None
    folded = _fold(value)
    if folded in _CLOSED_DAY_VALUES:
        return []
    windows: list[tuple[time, time]] = []
    for chunk in re.split(r"[;,]+", folded):
        candidate = chunk.strip()
        if not candidate:
            continue
        match = _HOURS_WINDOW_PATTERN.fullmatch(candidate)
        if not match:
            return None
        start_hour, start_minute, end_hour, end_minute = (int(group) for group in match.groups())
        if max(start_hour, end_hour) > 23 or max(start_minute, end_minute) > 59:
            return None
        starts, ends = time(start_hour, start_minute), time(end_hour, end_minute)
        if starts >= ends:
            return None
        windows.append((starts, ends))
    return windows or None


def is_within_business_hours(company_context: dict[str, Any] | None, now: datetime) -> bool:
    """True quando `now` cai numa janela de atendimento da empresa.

    Empresa sem horário configurado, ou com valor ilegível, é tratada como
    aberta: sem dado confiável, o modo de fora do horário não pode silenciar o
    bot por suposição — e um horário malformado nunca pode derrubar o
    atendimento, já que esta checagem roda em toda mensagem.
    """
    if now.tzinfo is None:
        raise ValueError("is_within_business_hours exige datetime com timezone.")
    business_hours = (company_context or {}).get("business_hours")
    if not isinstance(business_hours, dict):
        return True
    folded_hours = {_fold(key): value for key, value in business_hours.items()}
    if not (_BUSINESS_HOURS_DAY_KEYS & set(folded_hours)):
        return True

    local_now = now.astimezone(_company_zone(business_hours))
    current: time = local_now.time().replace(tzinfo=None)
    weekday = local_now.weekday()
    day_keys = [key for key in _WEEKDAY_KEYS[weekday] if key in folded_hours]
    if not day_keys and weekday < 5 and "weekdays" in folded_hours:
        day_keys = ["weekdays"]

    if day_keys:
        windows = _parse_hours_value(folded_hours[day_keys[0]])
    else:
        # Dia sem chave própria: mesma janela default que a agenda usa.
        from app.services.calendar_service import resolve_workday_windows

        try:
            windows = resolve_workday_windows(local_now.date(), company_context or {})
        except ValueError:
            windows = None
    if windows is None:
        log_event(
            logger,
            logging.WARNING,
            "company_policy.business_hours_unreadable",
            weekday=weekday,
        )
        return True
    return any(starts <= current < ends for starts, ends in windows)


# Abertura é obrigatória: "beleza", "tudo certo" ou "opa" sozinhos costumam
# ser concordância no meio da conversa, não saudação — responder com a
# apresentação da empresa reiniciaria o atendimento.
_GREETING_OPENINGS: tuple[str, ...] = (
    "boa tarde",
    "boa noite",
    "bom dia",
    "e ai",
    "ola",
    "oie",
    "oi",
    "alo",
    "salve",
    "helo",
    "hi",
    "hey",
)
_GREETING_COMPLEMENTS: tuple[str, ...] = (
    "como voce esta",
    "como vc esta",
    "tudo tranquilo",
    "tudo certo",
    "tudo joia",
    "tudo bem",
    "tudo bom",
    "como vai",
    "td bem",
    "beleza",
    "blz",
    "opa",
)
_GREETING_FILLER = frozenset(
    {"e", "ai", "ae", "pesoal", "gente", "amigo", "amiga", "moca", "moco", "voce", "vc", "com", "por", "ta", "esta"}
)
_MAX_GREETING_LENGTH = 60


def _normalize_for_intent(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    alphanumeric = re.sub(r"[^a-z0-9]+", " ", without_accents)
    # "oiii", "olaaa": letras repetidas são ênfase, não outra palavra.
    collapsed = re.sub(r"([a-z])\1+", r"\1", alphanumeric)
    return re.sub(r"\s+", " ", collapsed).strip()


def is_plain_greeting(message: str | None) -> bool:
    """Saudação sem pedido: "Oi, tudo bem?" sim; "oi, quero agendar" não."""
    normalized = _normalize_for_intent(message or "")
    if not normalized or len(normalized) > _MAX_GREETING_LENGTH:
        return False
    padded = f" {normalized} "

    def _strip(phrases: tuple[str, ...], text: str) -> tuple[str, bool]:
        found = False
        for phrase in phrases:
            needle = f" {phrase} "
            while needle in text:
                text = text.replace(needle, " ", 1)
                found = True
        return text, found

    padded, _ = _strip(_GREETING_COMPLEMENTS, padded)
    padded, has_opening = _strip(_GREETING_OPENINGS, padded)
    remaining = [token for token in padded.split() if token]
    return has_opening and all(token in _GREETING_FILLER for token in remaining)
