from typing import TypedDict

from app.services.company_policy_service import DEFAULT_AUTONOMY_POLICY, AutonomyPolicy


class MessageDecision(TypedDict):
    auto_reply: bool
    action: str
    priority: str


_HANDOFF_PRIORITY = {"financeiro": "high"}


def decide_message_action(
    classification: str,
    confidence: float,
    autonomy: AutonomyPolicy | None = None,
) -> MessageDecision:
    """Ação para uma mensagem fora de fluxo ativo.

    `autonomy` vem da política publicada da empresa; sem ela vale o default
    documentado, que reproduz o comportamento anterior à política tipada.
    """
    policy = autonomy or DEFAULT_AUTONOMY_POLICY

    # Restrição fixa: spam nunca recebe resposta, qualquer que seja a política.
    if classification == "spam":
        return {
            "auto_reply": False,
            "action": "ignore",
            "priority": "low",
        }

    if classification in policy.handoff_intents:
        return {
            "auto_reply": False,
            "action": "handoff_human",
            "priority": _HANDOFF_PRIORITY.get(classification, "medium"),
        }

    if policy.is_low_confidence(classification, confidence):
        return {
            "auto_reply": False,
            "action": "review",
            "priority": "medium",
        }

    if classification in policy.auto_reply_intents:
        return {
            "auto_reply": True,
            "action": "auto_reply",
            "priority": "high" if classification == "lead_novo" else "medium",
        }

    if classification == "agendamento":
        return {
            "auto_reply": True,
            "action": "workflow",
            "priority": "high",
        }

    return {
        "auto_reply": False,
        "action": "review",
        "priority": "medium",
    }
