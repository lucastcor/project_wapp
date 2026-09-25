"""Explicit provider selection; an unsupported provider never becomes a mock."""
from collections.abc import Callable

from app.core.config import settings
from app.llm.providers.business_provider import BusinessLLMProvider
from app.llm.providers.openai_provider import OpenAIProvider


def get_llm_provider(
    *, provider: str | None = None,
    openai_factory: Callable[[], BusinessLLMProvider] = OpenAIProvider,
) -> BusinessLLMProvider:
    selected = (provider or settings.classification_provider).strip().lower()
    if selected == "openai":
        return openai_factory()
    if selected == "ollama":
        from app.llm.providers.ollama_provider import OllamaProvider
        return OllamaProvider()
    raise ValueError(f"Unsupported LLM provider: {selected}")
