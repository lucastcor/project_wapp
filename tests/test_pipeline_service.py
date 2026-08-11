from app.core.config import settings
from app.services.pipeline_service import infer_pipeline_state


def test_pipeline_state_new_lead_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "mock")

    result = infer_pipeline_state(
        conversation_summary=None,
        latest_message="Olá, queria entender melhor como funciona o serviço.",
        latest_classification="lead_novo",
    )

    assert result["pipeline_stage"] == "novo_lead"
    assert result["lead_temperature"] == "warm"
    assert "Entender necessidade" in result["next_action"]


def test_pipeline_state_qualifying_price_signal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "mock")

    result = infer_pipeline_state(
        conversation_summary="Contato demonstrou interesse inicial.",
        latest_message="Queria saber valores e como funciona o orçamento.",
        latest_classification="lead_novo",
    )

    assert result["pipeline_stage"] == "qualificando"
    assert result["lead_temperature"] == "warm"


def test_pipeline_state_confirmation_hot(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "mock")

    result = infer_pipeline_state(
        conversation_summary="Contato já perguntou sobre valores e serviço.",
        latest_message="Gostei, quero contratar e seguir com a proposta.",
        latest_classification="lead_novo",
    )

    assert result["pipeline_stage"] == "aguardando_confirmacao"
    assert result["lead_temperature"] == "hot"


def test_pipeline_state_non_commercial_flow(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "mock")

    result = infer_pipeline_state(
        conversation_summary="Contato relatou problema técnico.",
        latest_message="Não consigo acessar a plataforma.",
        latest_classification="suporte",
    )

    assert result["pipeline_stage"] == "encerrado"
    assert result["lead_temperature"] == "cold"
