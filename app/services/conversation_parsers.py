"""Parsers de conversa bruta para o staging de importação.

Formatos suportados:
- `csv`: uma linha por mensagem
- `json`: lista de mensagens ou de conversas
- `whatsapp_txt`: export nativo "Conversa do WhatsApp com ..."

Todo parser devolve `ParsedMessage` normalizado. Linhas inválidas viram
`ParseIssue` em vez de derrubar o lote inteiro.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

MAX_MESSAGE_CHARS = 8000

CUSTOMER_ROLE = "customer"
AGENT_ROLE = "agent"
SYSTEM_ROLE = "system"

_ROLE_ALIASES = {
    "customer": CUSTOMER_ROLE,
    "cliente": CUSTOMER_ROLE,
    "contato": CUSTOMER_ROLE,
    "user": CUSTOMER_ROLE,
    "inbound": CUSTOMER_ROLE,
    "in": CUSTOMER_ROLE,
    "recebida": CUSTOMER_ROLE,
    "agent": AGENT_ROLE,
    "atendente": AGENT_ROLE,
    "empresa": AGENT_ROLE,
    "operador": AGENT_ROLE,
    "assistant": AGENT_ROLE,
    "outbound": AGENT_ROLE,
    "out": AGENT_ROLE,
    "enviada": AGENT_ROLE,
    "bot": AGENT_ROLE,
    "system": SYSTEM_ROLE,
    "sistema": SYSTEM_ROLE,
}

_FIELD_ALIASES: dict[str, list[str]] = {
    "conversation_id": ["conversation_id", "conversa_id", "thread_id", "chat_id", "session_id"],
    "contact_ref": ["contact_id", "contact_ref", "phone", "telefone", "whatsapp", "from_number", "cliente_id"],
    "role": ["role", "direction", "sender", "remetente", "tipo", "papel", "autor"],
    "content": ["content", "message", "mensagem", "texto", "text", "body"],
    "occurred_at": ["occurred_at", "timestamp", "created_at", "data", "datetime", "hora"],
}

# "12/03/2024 09:41 - Fulano: mensagem" e variações com [ ] e AM/PM.
_WHATSAPP_LINE = re.compile(
    r"^\[?(?P<date>\d{1,2}[/.]\d{1,2}[/.]\d{2,4})[,]?\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s*"
    r"(?P<meridiem>[APap]\.?[Mm]\.?)?\]?\s*[--]?\s*"
    r"(?P<author>[^:]{1,80}?):\s(?P<content>.*)$"
)
_WHATSAPP_SYSTEM_LINE = re.compile(
    r"^\[?\d{1,2}[/.]\d{1,2}[/.]\d{2,4}[,]?\s+\d{1,2}:\d{2}(?::\d{2})?\s*"
    r"(?:[APap]\.?[Mm]\.?)?\]?\s*[--]?\s*(?P<content>[^:]+)$"
)
_WHATSAPP_NOISE = (
    "as mensagens e as chamadas são criptografadas",
    "as mensagens e chamadas são criptografadas",
    "messages and calls are end-to-end encrypted",
    "<mídia oculta>",
    "<media omitted>",
    "mensagem apagada",
    "this message was deleted",
)


class ConversationParseError(Exception):
    pass


@dataclass(frozen=True)
class ParsedMessage:
    conversation_ref: str
    contact_ref: str
    role: str
    content: str
    occurred_at: str | None
    source_line: int | None


@dataclass(frozen=True)
class ParseIssue:
    source_line: int | None
    reason: str


@dataclass
class ParseResult:
    messages: list[ParsedMessage] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)

    @property
    def stats(self) -> dict[str, int]:
        return {"parsed_messages": len(self.messages), "invalid_records": len(self.issues)}


def normalize_role(value: Any, *, default: str = CUSTOMER_ROLE) -> str:
    normalized = str(value or "").strip().lower()
    return _ROLE_ALIASES.get(normalized, default)


def decode_payload(raw: bytes | str, *, max_bytes: int) -> str:
    """Decodifica com tolerância a encoding e limite explícito de tamanho."""
    data = raw.encode("utf-8", errors="replace") if isinstance(raw, str) else raw

    if len(data) > max_bytes:
        raise ConversationParseError(
            f"Import payload exceeds the configured limit of {max_bytes} bytes."
        )

    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract(record: dict[str, Any], field_name: str) -> Any:
    for alias in _FIELD_ALIASES.get(field_name, [field_name]):
        if alias in record and record[alias] not in (None, ""):
            return record[alias]
    return None


def _normalize_timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue

    # Um valor que não é timestamp **não vira** timestamp. Antes, o texto cru
    # era devolvido e acabava numa coluna `TIMESTAMP`: o SQLite aceita
    # qualquer coisa e o PostgreSQL recusa com
    # `invalid input syntax for type timestamp with time zone`.
    #
    # Isso não é só incompatibilidade de backend — é corrupção de dado. Um CSV
    # com vírgula não escapada desloca as colunas e o conteúdo da mensagem
    # chega aqui. Devolver `None` mantém a linha importável e deixa a ausência
    # explícita, em vez de gravar lixo num campo de data.
    return None


def _build_message(
    record: dict[str, Any],
    *,
    source_line: int,
    default_conversation_ref: str | None = None,
) -> ParsedMessage | ParseIssue:
    content = str(_extract(record, "content") or "").strip()
    if not content:
        return ParseIssue(source_line=source_line, reason="empty_content")
    if len(content) > MAX_MESSAGE_CHARS:
        content = content[:MAX_MESSAGE_CHARS]

    contact_ref = str(_extract(record, "contact_ref") or "").strip()
    conversation_ref = str(
        _extract(record, "conversation_id") or default_conversation_ref or contact_ref or ""
    ).strip()
    if not conversation_ref:
        return ParseIssue(source_line=source_line, reason="missing_conversation_reference")

    return ParsedMessage(
        conversation_ref=conversation_ref,
        contact_ref=contact_ref or conversation_ref,
        role=normalize_role(_extract(record, "role")),
        content=content,
        occurred_at=_normalize_timestamp(_extract(record, "occurred_at")),
        source_line=source_line,
    )


def parse_csv(text: str, *, delimiter: str = ",") -> ParseResult:
    result = ParseResult()
    reader = csv.DictReader(io.StringIO(text), delimiter=(delimiter or ",")[0])
    if not reader.fieldnames:
        raise ConversationParseError("CSV import requires a header row.")

    for index, row in enumerate(reader, start=2):
        normalized_row = {
            str(key or "").strip().lower(): value
            for key, value in row.items()
            if key is not None
        }
        built = _build_message(normalized_row, source_line=index)
        if isinstance(built, ParseIssue):
            result.issues.append(built)
        else:
            result.messages.append(built)
    return result


def parse_json(payload: str | list[Any] | dict[str, Any]) -> ParseResult:
    result = ParseResult()

    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ConversationParseError(f"Invalid JSON payload: {exc}") from exc
    else:
        parsed = payload

    records: Iterable[Any]
    if isinstance(parsed, dict):
        records = parsed.get("conversations") or parsed.get("records") or parsed.get("messages") or []
    elif isinstance(parsed, list):
        records = parsed
    else:
        raise ConversationParseError("JSON import must be a list or an object with records.")

    line = 0
    for record in records:
        line += 1
        if not isinstance(record, dict):
            result.issues.append(ParseIssue(source_line=line, reason="record_is_not_an_object"))
            continue

        nested_messages = record.get("messages")
        if isinstance(nested_messages, list):
            conversation_ref = str(
                record.get("conversation_id") or record.get("id") or f"conversation-{line}"
            )
            for nested in nested_messages:
                line += 1
                if not isinstance(nested, dict):
                    result.issues.append(
                        ParseIssue(source_line=line, reason="message_is_not_an_object")
                    )
                    continue
                merged = {
                    "contact_ref": record.get("contact_id") or record.get("contact_ref"),
                    **nested,
                }
                built = _build_message(
                    merged,
                    source_line=line,
                    default_conversation_ref=conversation_ref,
                )
                if isinstance(built, ParseIssue):
                    result.issues.append(built)
                else:
                    result.messages.append(built)
            continue

        built = _build_message(record, source_line=line)
        if isinstance(built, ParseIssue):
            result.issues.append(built)
        else:
            result.messages.append(built)

    return result


def _is_whatsapp_noise(content: str) -> bool:
    lowered = content.strip().lower()
    return any(marker in lowered for marker in _WHATSAPP_NOISE)


def _parse_whatsapp_timestamp(date_text: str, time_text: str, meridiem: str | None) -> str | None:
    normalized_date = date_text.replace(".", "/")
    normalized_time = time_text
    if meridiem:
        normalized_time = f"{time_text} {meridiem.upper().replace('.', '')}"

    for date_fmt in ("%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y", "%m/%d/%y"):
        for time_fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
            try:
                return datetime.strptime(
                    f"{normalized_date} {normalized_time}", f"{date_fmt} {time_fmt}"
                ).isoformat()
            except ValueError:
                continue
    return None


def parse_whatsapp_txt(
    text: str,
    *,
    conversation_ref: str,
    agent_authors: list[str] | None = None,
) -> ParseResult:
    """Parser do export TXT do WhatsApp.

    `agent_authors` diz quais autores são da empresa. Sem essa lista, o
    primeiro autor a responder é tratado como atendente, o que é a heurística
    mais comum em export de atendimento — e fica registrado no relatório.
    """
    result = ParseResult()
    normalized_agents = {str(name).strip().lower() for name in (agent_authors or []) if str(name).strip()}

    pending: list[tuple[int, str, str, str | None]] = []  # line, author, content, timestamp
    authors_in_order: list[str] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.replace("‎", "").replace(" ", " ").rstrip()
        if not line.strip():
            continue

        match = _WHATSAPP_LINE.match(line)
        if match:
            content = match.group("content").strip()
            author = match.group("author").strip()
            if _is_whatsapp_noise(content):
                result.issues.append(ParseIssue(source_line=line_number, reason="ignored_system_notice"))
                continue
            if not content:
                result.issues.append(ParseIssue(source_line=line_number, reason="empty_content"))
                continue
            timestamp = _parse_whatsapp_timestamp(
                match.group("date"), match.group("time"), match.group("meridiem")
            )
            pending.append((line_number, author, content[:MAX_MESSAGE_CHARS], timestamp))
            if author.lower() not in {existing.lower() for existing in authors_in_order}:
                authors_in_order.append(author)
            continue

        system_match = _WHATSAPP_SYSTEM_LINE.match(line)
        if system_match:
            result.issues.append(ParseIssue(source_line=line_number, reason="ignored_system_notice"))
            continue

        if pending:
            # Continuação de mensagem multilinha.
            line_number_prev, author, content, timestamp = pending[-1]
            merged = f"{content}\n{line.strip()}"[:MAX_MESSAGE_CHARS]
            pending[-1] = (line_number_prev, author, merged, timestamp)
            continue

        result.issues.append(ParseIssue(source_line=line_number, reason="unparsable_line"))

    if not pending:
        return result

    if normalized_agents:
        def resolve_role(author: str) -> str:
            return AGENT_ROLE if author.strip().lower() in normalized_agents else CUSTOMER_ROLE
    else:
        # Heurística documentada: o segundo autor distinto é o atendente.
        agent_author = authors_in_order[1].lower() if len(authors_in_order) > 1 else None

        def resolve_role(author: str) -> str:
            if agent_author and author.strip().lower() == agent_author:
                return AGENT_ROLE
            return CUSTOMER_ROLE

    for line_number, author, content, timestamp in pending:
        result.messages.append(
            ParsedMessage(
                conversation_ref=conversation_ref,
                contact_ref=author.strip() or conversation_ref,
                role=resolve_role(author),
                content=content,
                occurred_at=timestamp,
                source_line=line_number,
            )
        )

    return result


def parse_payload(
    *,
    source_format: str,
    raw_text: str,
    records: list[dict[str, Any]] | None = None,
    delimiter: str = ",",
    conversation_ref: str = "whatsapp-export",
    agent_authors: list[str] | None = None,
) -> ParseResult:
    normalized_format = str(source_format or "").strip().lower()
    if normalized_format == "csv":
        return parse_csv(raw_text, delimiter=delimiter)
    if normalized_format == "json":
        return parse_json(records if records else raw_text)
    if normalized_format in {"whatsapp_txt", "whatsapp", "txt"}:
        return parse_whatsapp_txt(
            raw_text,
            conversation_ref=conversation_ref,
            agent_authors=agent_authors,
        )
    raise ConversationParseError(
        f"Unsupported source_format: {source_format}. Use 'csv', 'json' or 'whatsapp_txt'."
    )
