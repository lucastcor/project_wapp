from app.services.classification_service import fallback_classification
from app.services.decision_service import decide_message_action
from app.services.pipeline_service import infer_pipeline_state


def test_reminder_intent_is_not_treated_as_customer_workflow() -> None:
    classification = fallback_classification("Me lembra amanhã de te responder")

    assert classification["classification"] == "lembrete"

    decision = decide_message_action(
        classification=classification["classification"],
        confidence=float(classification["confidence"]),
    )

    assert decision["auto_reply"] is False
    assert decision["action"] == "review"


def test_pipeline_keeps_only_scheduling_as_confirmation_flow(monkeypatch) -> None:
    result = infer_pipeline_state(
        conversation_summary="Contato pediu um lembrete fora do fluxo operacional interno.",
        latest_message="Me lembra amanhã.",
        latest_classification="lembrete",
    )

    assert result["pipeline_stage"] == "encerrado"
    assert result["lead_temperature"] == "cold"
