import json

import httpx
import pytest

from app.core.config import settings
from app.llm.factory import get_llm_provider
from app.llm.providers.ollama_provider import OllamaProvider, OllamaProviderError
from app.services import classification_service, memory_service, model_registry_service, pipeline_service


@pytest.fixture
def ollama(monkeypatch):
    monkeypatch.setattr(settings, "classification_provider", "ollama")
    monkeypatch.setattr(settings, "openai_api_key", None)
    requests = []
    replies = []
    original_client = httpx.Client

    def respond(request):
        requests.append(json.loads(request.content))
        content = replies.pop(0)
        return httpx.Response(200, json={"done": True, "done_reason": "stop", "message": {"content": content}})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    return requests, replies


def test_local_classification_memory_and_pipeline_use_ollama_without_cloud_key(ollama):
    requests, replies = ollama
    replies.extend([json.dumps({"classification": "financeiro", "confidence": 0.95, "reason": "boleto"}),
                    "Cliente precisa de boleto.", json.dumps({"pipeline_stage": "encerrado", "next_action": "Encaminhar", "lead_temperature": "cold"})])
    assert classification_service.classify_message("segunda via do boleto")["classification"] == "financeiro"
    assert memory_service.update_conversation_summary(None, "boleto", "financeiro") == "Cliente precisa de boleto."
    assert pipeline_service.infer_pipeline_state(None, "boleto", "financeiro")["pipeline_stage"] == "encerrado"
    assert len(requests) == 3
    assert all(r["think"] is False and r["stream"] is False for r in requests)
    assert requests[0]["format"]["properties"]["confidence"]["maximum"] == 1


@pytest.mark.parametrize("result", [
    {"classification": "financeiro", "confidence": 2, "reason": "bad"},
    {"classification": "invented", "confidence": 0.8, "reason": "bad"},
    {"classification": "financeiro", "confidence": True, "reason": "bad"},
    {"classification": "financeiro", "confidence": 0.8},
    {"classification": "financeiro", "confidence": 0.8, "reason": "bad", "extra": 1},
])
def test_model_output_is_validated_in_application(ollama, result):
    ollama[1].append(json.dumps(result))
    with pytest.raises(OllamaProviderError):
        OllamaProvider().classify_message("boleto")


def test_generation_empty_or_reasoning_is_rejected(ollama):
    for text in ["", "<think>internal</think>answer"]:
        ollama[1].append(text)
        with pytest.raises(OllamaProviderError):
            OllamaProvider().generate_text("hello")


def test_provider_factory_and_default_model_are_local(ollama, monkeypatch, company_factory):
    monkeypatch.setattr(settings, "use_finetuned_model_for_replies", False)
    assert isinstance(get_llm_provider(), OllamaProvider)
    selection = model_registry_service.resolve_reply_model(company_id=company_factory())
    assert selection.model_ref == settings.ollama_model
    assert selection.reply_source == "ollama"
    with pytest.raises(ValueError, match="Unsupported"):
        get_llm_provider(provider="typo")


def test_http_failure_never_falls_back_to_cloud(monkeypatch):
    monkeypatch.setattr(settings, "classification_provider", "ollama")
    original_client = httpx.Client
    seen = []
    def fail(request):
        seen.append(str(request.url))
        raise httpx.ReadTimeout("local timeout", request=request)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(fail), **kwargs))
    with pytest.raises(httpx.ReadTimeout):
        get_llm_provider().generate_text("hello")
    assert len(seen) == 1
    assert seen[0].startswith(settings.ollama_base_url)


def test_truncated_output_is_never_accepted(monkeypatch):
    original_client = httpx.Client
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"done": True, "done_reason": "length", "message": {"content": "partial"}}))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    with pytest.raises(OllamaProviderError, match="truncated"):
        OllamaProvider().generate_text("hello")
