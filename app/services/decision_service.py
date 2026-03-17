from typing import Any


def decide_message_action(classification: str, confidence: float) -> dict[str, Any]:
    if classification == "spam":
        return {
            "auto_reply": False,
            "action": "ignore",
            "priority": "low",
        }

    if classification == "financeiro":
        return {
            "auto_reply": False,
            "action": "handoff_human",
            "priority": "high",
        }

    if classification == "fornecedor":
        return {
            "auto_reply": False,
            "action": "handoff_human",
            "priority": "medium",
        }

    if classification == "indefinido" or confidence < 0.60:
        return {
            "auto_reply": False,
            "action": "review",
            "priority": "medium",
        }

    if classification in {"lead_novo", "cliente_existente", "suporte"}:
        return {
            "auto_reply": True,
            "action": "auto_reply",
            "priority": "high" if classification == "lead_novo" else "medium",
        }

    return {
        "auto_reply": False,
        "action": "review",
        "priority": "medium",
    }