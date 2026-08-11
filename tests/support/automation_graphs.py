"""Grafos de teste do Automation Studio.

Fixtures fictícias. Nenhum identificador real de cliente, telefone ou empresa.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "automation-graph/1"


def node(node_id: str, node_type: str, **config: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "node_type": node_type,
        "position": {"x": 0, "y": 0},
        "config": config,
    }


def edge(source: str, target: str, handle: str | None = None) -> dict[str, Any]:
    return {
        "id": f"{source}->{handle or 'next'}->{target}",
        "source": source,
        "target": target,
        "source_handle": handle,
    }


def graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "nodes": nodes, "edges": edges}


def minimal_graph() -> dict[str, Any]:
    """Gatilho -> terminal. O menor fluxo publicável."""
    return graph(
        [
            node("trigger", "trigger.manual"),
            node("done", "terminal.done"),
        ],
        [edge("trigger", "done", "next")],
    )


def branching_graph() -> dict[str, Any]:
    """Gatilho -> condição -> dois terminais."""
    return graph(
        [
            node("trigger", "trigger.messageProcessed"),
            node("cond", "condition.classification", values=["orcamento"]),
            node("done", "terminal.done"),
            node("stop", "terminal.canceled"),
        ],
        [
            edge("trigger", "cond", "next"),
            edge("cond", "done", "true"),
            edge("cond", "stop", "false"),
        ],
    )


def waiting_graph(amount: int = 2, unit: str = "hours") -> dict[str, Any]:
    """Gatilho -> espera -> terminal. Exercita a durabilidade do wait."""
    return graph(
        [
            node("trigger", "trigger.manual"),
            node("wait", "delay.wait", amount=amount, unit=unit),
            node("done", "terminal.done"),
        ],
        [
            edge("trigger", "wait", "next"),
            edge("wait", "done", "next"),
        ],
    )


def approval_graph() -> dict[str, Any]:
    """Gatilho -> aprovação humana -> dois caminhos."""
    return graph(
        [
            node("trigger", "trigger.manual"),
            node("gate", "control.humanApproval", title="Revisar antes de seguir"),
            node("done", "terminal.done"),
            node("stop", "terminal.canceled"),
        ],
        [
            edge("trigger", "gate", "next"),
            edge("gate", "done", "approved"),
            edge("gate", "stop", "rejected"),
        ],
    )


def effect_graph(stage: str = "qualificacao") -> dict[str, Any]:
    """Gatilho -> ação com efeito externo -> terminal."""
    return graph(
        [
            node("trigger", "trigger.manual"),
            node("act", "action.updatePipelineStage", stage=stage),
            node("done", "terminal.done"),
        ],
        [
            edge("trigger", "act", "next"),
            edge("act", "done", "next"),
        ],
    )


def full_e2e_graph() -> dict[str, Any]:
    """evento -> condição -> IA -> espera -> aprovação -> envio -> terminal.

    É o cenário completo exigido: cobre ramificação, geração de conteúdo,
    espera durável, portão humano e efeito externo idempotente.
    """
    return graph(
        [
            node("trigger", "trigger.messageProcessed"),
            node("cond", "condition.classification", values=["orcamento", "lead_novo"]),
            node(
                "compose",
                "ai.composeMessage",
                objective="Retomar o contato com um resumo do orçamento.",
                use_knowledge=True,
            ),
            node("wait", "delay.wait", amount=1, unit="hours"),
            node(
                "gate",
                "control.humanApproval",
                title="Aprovar follow-up de orçamento",
                summary="Revise o texto antes do envio.",
            ),
            node("send", "action.sendMessage", source="ai_node"),
            node("done", "terminal.done"),
            node("stop", "terminal.canceled"),
            node("skipped", "terminal.done"),
        ],
        [
            edge("trigger", "cond", "next"),
            edge("cond", "compose", "true"),
            edge("cond", "skipped", "false"),
            edge("compose", "wait", "next"),
            edge("wait", "gate", "next"),
            edge("gate", "send", "approved"),
            edge("gate", "stop", "rejected"),
            edge("send", "done", "next"),
        ],
    )


def cyclic_graph() -> dict[str, Any]:
    """Ciclo explícito: precisa ser rejeitado pelo validador."""
    return graph(
        [
            node("trigger", "trigger.manual"),
            node("a", "control.if", context_key="classification", equals="x"),
            node("b", "control.if", context_key="classification", equals="y"),
            node("done", "terminal.done"),
        ],
        [
            edge("trigger", "a", "next"),
            edge("a", "b", "true"),
            edge("b", "a", "true"),
            edge("a", "done", "false"),
            edge("b", "done", "false"),
        ],
    )


def secret_bearing_graph() -> dict[str, Any]:
    """Configuração com chave de aparência de credencial."""
    return graph(
        [
            node("trigger", "trigger.manual"),
            node("act", "action.createHandoff", reason="ok", api_key="sk-abcdefghijklmnop123"),
            node("done", "terminal.done"),
        ],
        [
            edge("trigger", "act", "next"),
            edge("act", "done", "next"),
        ],
    )


SAMPLE_MESSAGE_CONTEXT: dict[str, Any] = {
    "classification": "orcamento",
    "confidence": 0.91,
    "pipeline_stage": "qualificacao",
    "customer_profile": "new_customer",
    "handoff_required": False,
    "reply_source": "llm",
    "has_relevant_source": True,
    "auto_reply_sent": False,
    "contact_id": "fixture-contact-0001",
}
