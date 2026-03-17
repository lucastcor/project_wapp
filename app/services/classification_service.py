from typing import Any

from app.core.config import settings
from app.llm.providers.openai_provider import OpenAIProvider


def fallback_classification(message: str) -> dict[str, Any]:
    normalized = message.lower()

    commercial_keywords = [
        "preço",
        "valor",
        "orçamento",
        "quanto custa",
        "serviço",
        "produto",
    ]

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
    except Exception as e:
        print("OPENAI CLASSIFICATION ERROR:", repr(e))
        return fallback_classification(message)