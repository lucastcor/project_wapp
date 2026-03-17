from app.core.config import settings
from app.services.memory_service import update_conversation_summary


def test_memory_service_fallback_without_previous_summary(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "mock")

    summary = update_conversation_summary(
        previous_summary=None,
        latest_message="Queria saber mais sobre o serviço.",
        latest_classification="lead_novo",
        latest_reply="Claro, posso te ajudar com isso.",
    )

    assert "Nova mensagem do contato" in summary
    assert "Classificação atual: lead_novo" in summary
    assert "Última resposta enviada" in summary


def test_memory_service_fallback_with_previous_summary(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "mock")

    summary = update_conversation_summary(
        previous_summary="Contato já demonstrou interesse comercial.",
        latest_message="Agora queria entender os valores.",
        latest_classification="lead_novo",
        latest_reply="Os valores dependem do escopo.",
    )

    assert "Resumo anterior" in summary
    assert "Contato já demonstrou interesse comercial." in summary
    assert len(summary) <= 800