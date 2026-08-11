import logging
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.llm.providers.openai_provider import OpenAIProvider

logger = get_logger(__name__)


def fallback_classification(message: str) -> dict[str, Any]:
    normalized = message.lower()

    scheduling_keywords = [
        "agendar",
        "agendamento",
        "marcar horário",
        "marcar horario",
        "disponibilidade",
        "consulta",
        "reunião",
        "reuniao",
    ]
    reminder_keywords = [
        "me lembra",
        "me lembre",
        "lembrete",
        "me avisa",
        "me avise",
    ]

    commercial_keywords = [
        "preço",
        "valor",
        "orçamento",
        "quanto custa",
        "serviço",
        "produto",
    ]

    if any(keyword in normalized for keyword in scheduling_keywords):
        return {
            "classification": "agendamento",
            "confidence": 0.82,
            "reason": "Heurística detectou intenção de agendamento.",
        }

    if any(keyword in normalized for keyword in reminder_keywords):
        return {
            "classification": "lembrete",
            "confidence": 0.84,
            "reason": "Heurística detectou intenção de lembrete.",
        }

    if any(keyword in normalized for keyword in commercial_keywords):
        return {
            "classification": "lead_novo",
            "confidence": 0.65,
            "reason": "Heurística detectou intenção comercial.",
        }

    return {
        "classification": "indefinido",
        "confidence": 0.40,
        "reason": "Sem sinal suficiente para classificação precisa.",
    }


def classify_message(message: str) -> dict[str, Any]:
    if settings.classification_provider != "openai":
        return fallback_classification(message)

    try:
        provider = OpenAIProvider()
        result = provider.classify_message(message)

        return {
            "classification": result["classification"],
            "confidence": float(result["confidence"]),
            "reason": result["reason"],
        }
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "classification.provider.fallback",
            provider="openai",
            error_type=type(exc).__name__,
        )
        return fallback_classification(message)
