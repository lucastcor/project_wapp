"""OpenAI transport for the shared business prompt contracts."""
from typing import Any, cast

from openai import OpenAI
from openai.types.responses import ResponseTextConfigParam

from app.core.config import settings
from app.llm.providers.business_provider import BusinessLLMProvider


class OpenAIProvider(BusinessLLMProvider):
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not configured.")
        self.client = OpenAI(api_key=settings.openai_api_key, timeout=settings.llm_timeout_seconds, max_retries=0)
        self.model = settings.openai_model
        self.finetuned_model = settings.openai_finetuned_model

    def _generate(self, *, model: str, input: str, text: dict[str, Any] | None = None) -> Any:
        if text is not None:
            return self.client.responses.create(model=model, input=input, text=cast(ResponseTextConfigParam, text))
        return self.client.responses.create(model=model, input=input)
