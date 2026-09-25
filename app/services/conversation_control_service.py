"""Interpretação determinística de mensagens de controle da conversa (P02).

Contrato: docs/piloto/CONTRATOS.md, "Interpretação e confirmação". Classifica a
resposta do cliente a uma proposta em ``confirm | reject | change | question |
uncertain``. É uma função pura: sem rede, relógio, banco ou LLM.

Regras, em ordem:

1. Texto normalizado sem acento; comparação sempre por palavra ou frase inteira
   (nunca substring: "assim" não contém "sim").
2. Detalhes de mudança (horário, data, período, serviço, opção, "outro") tornam
   a mensagem ``change`` — "sim, mas às 16h" nunca confirma a proposta antiga.
   Valores conflitantes ("às 10h ou às 11h") são ``uncertain``.
3. Pergunta ("?" ou forma interrogativa) é ``question``.
4. Negação e desistência são ``reject`` (prioridade sobre qualquer aceite).
5. ``confirm`` só quando *todas* as palavras pertencem ao vocabulário de aceite
   (com ao menos um sinal de aceite). Qualquer palavra fora dele é ``uncertain``.

O vocabulário depende do tipo da proposta: "pode cancelar" confirma um
cancelamento, mas recusa uma nova reserva; "pode manter" recusa um cancelamento,
mas é ambíguo diante de uma nova reserva.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

CONFIRM = "confirm"
REJECT = "reject"
CHANGE = "change"
QUESTION = "question"
UNCERTAIN = "uncertain"
INTENTS = (CONFIRM, REJECT, CHANGE, QUESTION, UNCERTAIN)
TARGETED_KINDS = frozenset({"cancel", "reschedule"})

_CONFIRM_SIGNALS = frozenset(
    {
        "sim", "s", "ss", "pode", "ok", "okay", "confirmo", "confirma", "confirmar", "confirmado",
        "confirmada", "confirme", "claro", "certo", "correto", "perfeito", "fechado", "combinado",
        "beleza", "blz", "bora", "isso", "exato", "exatamente", "positivo", "quero", "yes", "aceito",
        "otimo", "show",
    }
)
# Frases de aceite removidas antes da checagem por palavra.
_CONFIRM_PHRASES = (
    "com certeza", "pode sim", "isso mesmo", "ta bom", "esta bom", "tudo certo", "tudo bem", "tudo ok",
    "muito bem", "sim senhor", "sim senhora",
)
_FILLER_PHRASES = ("por favor", "muito obrigado", "muito obrigada")
# Palavras neutras que podem acompanhar um aceite sem mudar seu sentido. Nunca
# incluir quantificadores ("todos", "tudo", "dois"): mudam o alvo da ação.
_FILLERS = frozenset(
    {
        "mesmo", "favor", "pf", "pfv", "pls", "obrigado", "obrigada", "obg", "brigado", "brigada", "valeu",
        "vlw", "entao", "e", "pra", "para", "mim", "eu", "aham", "uhum", "opa", "oba", "ai", "agora", "ja",
        "esse", "este", "essa", "horario", "agendamento", "atendimento", "o", "a", "de", "ta",
    }
)
_KIND_CONFIRM_VERBS: dict[str, frozenset[str]] = {
    "create": frozenset(
        {"marcar", "marca", "marque", "agendar", "agenda", "agende", "reservar", "reserva", "reserve"}
    ),
    "cancel": frozenset({"cancelar", "cancela", "cancele", "desmarcar", "desmarca", "desmarque"}),
    "reschedule": frozenset({"remarcar", "remarca", "remarque", "reagendar", "reagenda", "reagende"}),
}
_CANCEL_VERBS = _KIND_CONFIRM_VERBS["cancel"] | frozenset({"cancelo", "cancelem"})
_NEGATION_TOKENS = frozenset(
    {"nao", "n", "nem", "nunca", "jamais", "negativo", "nops", "nope", "naum", "nenhum", "nenhuma"}
)
_REJECT_PHRASES = (
    "deixa pra la", "deixa para la", "deixa pra depois", "deixa para depois", "desisto", "desistir",
    "desisti", "esquece", "esqueca", "nao quero mais", "de jeito nenhum",
)
_KEEP_TOKENS = frozenset({"manter", "mantem", "mantenha", "mantenho"})
_KEEP_PHRASES = ("deixa como esta", "deixa como estava", "fica como esta", "continua como esta", "deixa assim")
_INTERROGATIVE_STARTS = (
    "sera", "como", "quando", "qual", "quais", "quanto", "quanta", "quantos", "quantas", "onde",
    "por que", "porque", "pq", "o que", "oque", "e se", "tem como", "da pra", "da para", "e possivel",
    "isso cancela", "isso remarca", "isso confirma", "isso marca", "isso vai", "vai cancelar",
    "vai remarcar", "vai marcar", "quem", "posso",
)
_CHANGE_TOKENS = frozenset(
    {"outro", "outra", "outros", "outras", "trocar", "troca", "troque", "mudar", "muda", "mude", "alterar",
     "altera", "altere"}
)
_CHANGE_PHRASES = ("mais tarde", "mais cedo", "em vez", "ao inves")
_ALTERNATIVE_MARKERS = (
    "mas", "prefiro", "prefere", "preferia", "melhor", "que tal", "em vez", "ao inves", "pode ser", "outro",
    "outra", "troca", "trocar", "muda", "mudar",
)
_NEGATED_VERB = re.compile(
    r"\b(?:nao|n)\s+(?:quero|posso|da|consigo|vou|gostaria|preciso|rola|serve|funciona|pode|quer)\b"
)
_GREETING_PHRASES = ("bom dia", "boa tarde", "boa noite")
_WEEKDAYS = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")
_ORDINALS = {
    "primeira": 1, "primeiro": 1, "segunda": 2, "segundo": 2, "terceira": 3, "terceiro": 3,
    "quarta": 4, "quarto": 4, "quinta": 5, "quinto": 5,
}
_ORDINAL_OPTION = re.compile(
    r"\b(primeira|primeiro|segunda|segundo|terceira|terceiro|quarta|quarto|quinta|quinto)\s+(?:opcao|alternativa)\b"
)
_OPTION_NUMBER = re.compile(r"\b(?:opcao|alternativa|numero)\s+([1-9])\b")
_BARE_CHOICE = re.compile(r"^(?:a|o)?\s*(?:([1-9])|(primeira|primeiro|segundo|terceira|terceiro))$")
_TIME_WITH_MINUTES = re.compile(r"\b([01]?\d|2[0-3])(?::|h)([0-5]\d)\b")
_TIME_WITH_SUFFIX = re.compile(r"\b([01]?\d|2[0-3])\s*(?:h|hs|hr|hrs|hora|horas)\b")
_TIME_AFTER_PREPOSITION = re.compile(r"\b(?:as|pras)\s+([01]?\d|2[0-3])\b(?![:/\dh])")
_SHORT_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_DAY_NUMBER = re.compile(r"\bdia\s+(\d{1,2})\b(?![/\d])")


@dataclass(frozen=True)
class ControlChange:
    """Detalhes citados numa mudança. Datas são termos normalizados, não resolvidas."""

    times: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    periods: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    choice: int | None = None
    other_option: bool = False

    @property
    def has_concrete_values(self) -> bool:
        return bool(self.times or self.dates or self.periods or self.services or self.choice is not None)

    @property
    def has_details(self) -> bool:
        return self.has_concrete_values or self.other_option


@dataclass(frozen=True)
class ControlInterpretation:
    intent: str
    reason: str
    change: ControlChange = field(default_factory=ControlChange)

    @property
    def authorizes(self) -> bool:
        return self.intent == CONFIRM


def fold_text(message: str | None) -> str:
    """Minúsculas sem acento, preservando pontuação (usada por horários e datas)."""
    decomposed = unicodedata.normalize("NFKD", (message or "").lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _token_text(folded: str) -> str:
    alphanumeric = re.sub(r"[^a-z0-9]+", " ", folded)
    # "simmm", "naooo": três ou mais repetições são ênfase.
    collapsed = re.sub(r"([a-z])\1{2,}", r"\1", alphanumeric)
    return re.sub(r"\s+", " ", collapsed).strip()


def _has_phrase(padded: str, phrase: str) -> bool:
    return f" {phrase} " in padded


def _remove_phrases(padded: str, phrases: Iterable[str]) -> tuple[str, bool]:
    found = False
    for phrase in phrases:
        needle = f" {phrase} "
        while needle in padded:
            padded = padded.replace(needle, " ", 1)
            found = True
    return padded, found


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return tuple(ordered)


def _extract_times(folded: str) -> tuple[str, ...]:
    found: list[tuple[int, str]] = []
    covered: list[tuple[int, int]] = []
    for pattern in (_TIME_WITH_MINUTES, _TIME_WITH_SUFFIX, _TIME_AFTER_PREPOSITION):
        for match in pattern.finditer(folded):
            span = match.span()
            if any(start <= span[0] < end for start, end in covered):
                continue
            covered.append(span)
            hour = int(match.group(1))
            minute = int(match.group(2)) if pattern is _TIME_WITH_MINUTES else 0
            found.append((span[0], f"{hour:02d}:{minute:02d}"))
    return _unique(value for _position, value in sorted(found))


def _extract_dates(folded: str, padded_tokens: str) -> tuple[str, ...]:
    dates: list[str] = [match.group(0) for match in _ISO_DATE.finditer(folded)]
    dates.extend(match.group(0) for match in _SHORT_DATE.finditer(folded))
    dates.extend(f"dia {int(match.group(1))}" for match in _DAY_NUMBER.finditer(folded))
    text, after_tomorrow = _remove_phrases(padded_tokens, ("depois de amanha",))
    if after_tomorrow:
        dates.append("depois de amanha")
    dates.extend(word for word in ("amanha", "hoje") if _has_phrase(text, word))
    # "a segunda opção" é escolha de opção, não segunda-feira.
    text = _ORDINAL_OPTION.sub(" ", text)
    dates.extend(weekday for weekday in _WEEKDAYS if re.search(rf" {weekday}(?: feira)? ", text))
    return _unique(dates)


def _extract_periods(padded_tokens: str) -> tuple[str, ...]:
    text, _ = _remove_phrases(padded_tokens, (*_GREETING_PHRASES, "mais tarde", "mais cedo"))
    periods = []
    if _has_phrase(text, "manha"):
        periods.append("morning")
    if _has_phrase(text, "tarde"):
        periods.append("afternoon")
    if _has_phrase(text, "noite"):
        periods.append("evening")
    return tuple(periods)


def _extract_services(padded_tokens: str, service_names: Iterable[str]) -> tuple[str, ...]:
    found = []
    for name in service_names:
        normalized = _token_text(fold_text(str(name or "")))
        if normalized and _has_phrase(padded_tokens, normalized):
            found.append(str(name))
    return _unique(found)


def _extract_choice(tokens_text: str) -> int | None:
    bare = _BARE_CHOICE.match(tokens_text)
    if bare:
        return int(bare.group(1)) if bare.group(1) else _ORDINALS[bare.group(2)]
    ordinal = _ORDINAL_OPTION.search(tokens_text)
    if ordinal:
        return _ORDINALS[ordinal.group(1)]
    number = _OPTION_NUMBER.search(tokens_text)
    if number:
        return int(number.group(1))
    return None


def _extract_change(
    folded: str, tokens_text: str, tokens: list[str], kind: str | None, service_names: Iterable[str]
) -> ControlChange:
    padded = f" {tokens_text} "
    change_verbs = set(_CHANGE_TOKENS)
    if kind == "create":
        change_verbs |= _KIND_CONFIRM_VERBS["reschedule"]
    other_option = bool(change_verbs & set(tokens)) or any(_has_phrase(padded, p) for p in _CHANGE_PHRASES)
    return ControlChange(
        times=_extract_times(folded),
        dates=_extract_dates(folded, padded),
        periods=_extract_periods(padded),
        services=_extract_services(padded, service_names),
        choice=_extract_choice(tokens_text),
        other_option=other_option,
    )


def _is_question(raw: str, tokens_text: str) -> bool:
    if "?" in raw or "¿" in raw:
        return True
    return any(tokens_text == start or tokens_text.startswith(f"{start} ") for start in _INTERROGATIVE_STARTS)


def _has_negation(tokens: list[str], padded: str, kind: str | None) -> bool:
    if _NEGATION_TOKENS & set(tokens):
        return True
    if any(_has_phrase(padded, phrase) for phrase in _REJECT_PHRASES):
        return True
    # Diante de nova reserva ou remarcação, "cancela" é desistência da proposta.
    return kind in {"create", "reschedule"} and bool(_CANCEL_VERBS & set(tokens))


def _has_keep(tokens: list[str], padded: str, kind: str | None) -> bool:
    if kind not in TARGETED_KINDS:
        return False
    return bool(_KEEP_TOKENS & set(tokens)) or any(_has_phrase(padded, phrase) for phrase in _KEEP_PHRASES)


def _is_pure_confirmation(tokens_text: str, kind: str | None) -> bool:
    padded, signal = _remove_phrases(f" {tokens_text} ", _CONFIRM_PHRASES)
    padded, _ = _remove_phrases(padded, _FILLER_PHRASES)
    verbs = _KIND_CONFIRM_VERBS.get(kind or "", frozenset())
    for token in padded.split():
        if token in _CONFIRM_SIGNALS or token in verbs:
            signal = True
        elif token not in _FILLERS:
            return False
    return signal


def interpret_control_message(
    message: str | None,
    *,
    kind: str | None = None,
    service_names: Iterable[str] = (),
) -> ControlInterpretation:
    """Classifica a resposta a uma proposta do tipo ``kind`` (create/cancel/reschedule)."""
    raw = message or ""
    folded = fold_text(raw)
    tokens_text = _token_text(folded)
    if not tokens_text:
        return ControlInterpretation(UNCERTAIN, "empty")
    tokens = tokens_text.split()
    padded = f" {tokens_text} "
    change = _extract_change(folded, tokens_text, tokens, kind, tuple(service_names))
    negation = _has_negation(tokens, padded, kind)
    keep = _has_keep(tokens, padded, kind)

    if change.has_details:
        if keep:
            return ControlInterpretation(REJECT, "keep_requested", change)
        if len(change.times) > 1 or len(change.dates) > 1:
            return ControlInterpretation(UNCERTAIN, "conflicting_values", change)
        clause_text = re.sub(r"[^a-z0-9,.;!?]+", " ", folded)
        alternative = any(_has_phrase(padded, marker) for marker in _ALTERNATIVE_MARKERS)
        if negation and _NEGATED_VERB.search(clause_text) and not alternative:
            return ControlInterpretation(REJECT, "negated_change", change)
        return ControlInterpretation(CHANGE, "change_details", change)
    if _is_question(raw, tokens_text):
        return ControlInterpretation(QUESTION, "question", change)
    if negation or keep:
        return ControlInterpretation(REJECT, "negation", change)
    if _is_pure_confirmation(tokens_text, kind):
        return ControlInterpretation(CONFIRM, "confirmation_vocabulary", change)
    return ControlInterpretation(UNCERTAIN, "unrecognized_terms", change)


_ABANDON_FILLERS = frozenset(
    {"obrigado", "obrigada", "obg", "valeu", "vlw", "agora", "por", "favor", "pf", "mesmo", "e", "entao", "ok"}
)
_ABANDON_BLOCKERS = frozenset({"horario", "data", "dia", "hora", "servico", "opcao", "outro", "outra", "sei"})


def is_abandon_request(message: str | None, *, kind: str | None = None) -> bool:
    """Pedido para desistir do fluxo em andamento (sem proposta pendente).

    Durante coleta de nova reserva, "cancelar"/"desisto"/"não" encerram só a
    coleta. Com ``kind='cancel'`` os verbos de cancelar fazem parte do pedido e
    não são desistência. Perguntas e mensagens com data/horário não são.
    """
    raw = message or ""
    folded = fold_text(raw)
    tokens_text = _token_text(folded)
    if not tokens_text or _is_question(raw, tokens_text):
        return False
    tokens = tokens_text.split()
    padded = f" {tokens_text} "
    change = _extract_change(folded, tokens_text, tokens, None, ())
    if change.has_concrete_values or _ABANDON_BLOCKERS & set(tokens):
        return False
    if any(_has_phrase(padded, phrase) for phrase in _REJECT_PHRASES):
        return True
    if kind != "cancel" and _CANCEL_VERBS & set(tokens):
        return True
    if _has_keep(tokens, padded, kind):
        return True
    if _has_phrase(padded, "nao quero") or _has_phrase(padded, "nao precisa"):
        return True
    return bool(_NEGATION_TOKENS & set(tokens)) and all(
        token in _NEGATION_TOKENS or token in _ABANDON_FILLERS for token in tokens
    )
