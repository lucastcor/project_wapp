from app.core.config import settings
from app.services import pipeline_service


def test_pipeline_service_invalid_openai_output_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "openai")

    class FakeProvider:
        def infer_pipeline_state(self, **kwargs):
            return {
                "pipeline_stage": "estado_invalido",
                "next_action": "Algo qualquer",
                "lead_temperature": "muito_quente",
            }

    monkeypatch.setattr(pipeline_service, "OpenAIProvider", lambda: FakeProvider())

    result = pipeline_service.infer_pipeline_state(
        conversation_summary="Contato demonstrou interesse.",
        latest_message="Quero saber valores.",
        latest_classification="lead_novo",
    )

    assert result["pipeline_stage"] == "novo_lead"
    assert result["lead_temperature"] == "warm"
