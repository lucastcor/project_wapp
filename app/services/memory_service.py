import logging

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.llm.providers.openai_provider import OpenAIProvider

MAX_SUMMARY_LENGTH = 800
logger = get_logger(__name__)


def _truncate_summary(text: str, limit: int = MAX_SUMMARY_LENGTH) -> str:
    normalized = " ".join(text.split())
    return normalized[:limit]


def _fallback_update_conversation_summary(
    previous_summary: str | None,
    latest_message: str,
    latest_classification: str,
    latest_reply: str | None = None,
) -> str:
    parts: list[str] = []

    if previous_summary:
        parts.append(f"Resumo anterior: {previous_summary}")

    parts.append(f"Nova mensagem do contato: {latest_message}")
    parts.append(f"Classificação atual: {latest_classification}")

    if latest_reply:
        parts.append(f"Última resposta enviada: {latest_reply}")

    return _truncate_summary(". ".join(parts))


def update_conversation_summary(
    previous_summary: str | None,
    latest_message: str,
    latest_classification: str,
    latest_reply: str | None = None,
) -> str:
    if settings.classification_provider != "openai":
        return _fallback_update_conversation_summary(
            previous_summary=previous_summary,
            latest_message=latest_message,
            latest_classification=latest_classification,
            latest_reply=latest_reply,
        )

    try:
        provider = OpenAIProvider()
        summary = provider.generate_conversation_summary(
            previous_summary=previous_summary,
            latest_message=latest_message,
            latest_classification=latest_classification,
            latest_reply=latest_reply,
        )
        return _truncate_summary(summary)
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "memory.provider.fallback",
            provider="openai",
            error_type=type(exc).__name__,
        )
        return _fallback_update_conversation_summary(
            previous_summary=previous_summary,
            latest_message=latest_message,
            latest_classification=latest_classification,
            latest_reply=latest_reply,
        )
