"""Semântica central: a única interpretação das regras do fluxo.

Simulação e motor consomem daqui. `evaluate_node` decide **o que** acontece
num bloco; quem chama decide o que fazer com isso — a simulação avança o
relógio em linha, o motor suspende e agenda um job durável. É essa separação
que impede que as duas leituras divirjam com o tempo, e
`tests/test_automation_conformance.py` prova que continuam de acordo.

Módulo puro: sem I/O, sem `datetime.now()` implícito, sem banco. O relógio
entra como parâmetro.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.automations.catalog import (
    ACTION_CREATE_HANDOFF,
    ACTION_CREATE_REMINDER,
    ACTION_SEND_MESSAGE,
    ACTION_UPDATE_PIPELINE_STAGE,
    AI_CLASSIFY_OR_ROUTE,
    AI_COMPOSE_MESSAGE,
    AI_EXTRACT_STRUCTURED,
    CONDITION_CLASSIFICATION,
    CONDITION_CONFIDENCE,
    CONDITION_CUSTOMER_PROFILE,
    CONDITION_HANDOFF_REQUIRED,
    CONDITION_HAS_KNOWLEDGE_SOURCE,
    CONDITION_PIPELINE_STAGE,
    CONTROL_HUMAN_APPROVAL,
    CONTROL_IF,
    CONTROL_SWITCH,
    DELAY_WAIT,
    HANDLE_APPROVED,
    HANDLE_FALSE,
    HANDLE_NEXT,
    HANDLE_REJECTED,
    HANDLE_TRUE,
    TERMINAL_CANCELED,
    TERMINAL_DONE,
    TERMINAL_ERROR,
    get_spec,
    outputs_for,
)
from app.automations.domain import WorkflowGraph, WorkflowNode

# --- resultados possíveis de um passo -------------------------------------------------

OUTCOME_CONTINUE = "continue"
OUTCOME_WAIT = "wait"
OUTCOME_AWAIT_HUMAN = "await_human"
OUTCOME_EFFECT = "effect"
OUTCOME_TERMINAL = "terminal"

TERMINAL_STATUS_BY_NODE = {
    TERMINAL_DONE: "succeeded",
    TERMINAL_ERROR: "failed",
    TERMINAL_CANCELED: "canceled",
}


@dataclass(frozen=True)
class EffectIntent:
    """O que o fluxo **quer** que aconteça. Nada é executado aqui.

    A simulação registra a intenção e segue; o motor a transforma numa
    entrada do effect ledger e só então chama o adapter. Um nó de IA produz
    conteúdo, nunca afirma que uma ação externa ocorreu.
    """

    kind: str
    node_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "node_id": self.node_id, "payload": dict(self.payload)}


@dataclass(frozen=True)
class NodeOutcome:
    """Decisão sobre um bloco, sem nenhum efeito aplicado."""

    outcome: str
    node_id: str
    node_type: str
    chosen_handle: str | None = None
    next_node_id: str | None = None
    # OUTCOME_WAIT: quando a execução deve retomar.
    resume_at: datetime | None = None
    # OUTCOME_EFFECT: intenção a ser materializada pelo chamador.
    effect: EffectIntent | None = None
    # OUTCOME_TERMINAL: status final da execução.
    terminal_status: str | None = None
    # Alterações que o bloco propõe ao contexto da execução.
    context_updates: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "chosen_handle": self.chosen_handle,
            "next_node_id": self.next_node_id,
            "resume_at": self.resume_at.isoformat() if self.resume_at else None,
            "effect": self.effect.as_dict() if self.effect else None,
            "terminal_status": self.terminal_status,
            "context_updates": dict(self.context_updates),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class AiOutput:
    """Resultado de um nó de IA já produzido pelo chamador.

    A semântica é pura: ela não chama modelo. O motor executa a IA (com os
    providers reais) e informa o resultado; a simulação passa um resultado
    fictício claramente marcado.
    """

    text: str | None = None
    structured: dict[str, Any] = field(default_factory=dict)
    label: str | None = None
    simulated: bool = False


def find_trigger(graph: WorkflowGraph) -> WorkflowNode | None:
    from app.automations.catalog import TRIGGER_TYPES

    for node in graph.nodes:
        if node.node_type in TRIGGER_TYPES:
            return node
    return None


def resolve_next(graph: WorkflowGraph, node_id: str, handle: str | None) -> str | None:
    """Próximo nó por uma alça. `None` quando a alça não leva a lugar nenhum."""
    node = graph.node(node_id)
    if node is None:
        return None
    handles = outputs_for(node.node_type, node.config)
    resolved = handle or (handles[0] if len(handles) == 1 else None)
    for edge in graph.outgoing(node_id):
        edge_handle = edge.source_handle or (handles[0] if len(handles) == 1 else None)
        if edge_handle == resolved:
            return edge.target
    return None


def _branch(graph: WorkflowGraph, node: WorkflowNode, matched: bool, detail: dict[str, Any]):
    handle = HANDLE_TRUE if matched else HANDLE_FALSE
    return NodeOutcome(
        outcome=OUTCOME_CONTINUE,
        node_id=node.id,
        node_type=node.node_type,
        chosen_handle=handle,
        next_node_id=resolve_next(graph, node.id, handle),
        detail=detail,
    )


def _as_text(value: Any) -> str:
    return str(value if value is not None else "").strip().lower()


def evaluate_node(
    *,
    graph: WorkflowGraph,
    node: WorkflowNode,
    context: dict[str, Any],
    now: datetime,
    ai_output: AiOutput | None = None,
) -> NodeOutcome:
    """Decide o que acontece neste bloco. Não aplica nada.

    `context` é o estado acumulado da execução (dados do evento mais o que os
    blocos anteriores acrescentaram). `ai_output` só é usado por blocos de IA
    e é responsabilidade do chamador produzi-lo.
    """
    node_type = node.node_type
    spec = get_spec(node_type)
    if spec is None:
        return NodeOutcome(
            outcome=OUTCOME_TERMINAL,
            node_id=node.id,
            node_type=node_type,
            terminal_status="failed",
            detail={"reason": "unknown_node_type"},
        )

    # --- gatilho: só encaminha ---
    if spec.category == "trigger":
        return NodeOutcome(
            outcome=OUTCOME_CONTINUE,
            node_id=node.id,
            node_type=node_type,
            chosen_handle=HANDLE_NEXT,
            next_node_id=resolve_next(graph, node.id, HANDLE_NEXT),
        )

    # --- terminais ---
    if node_type in TERMINAL_STATUS_BY_NODE:
        return NodeOutcome(
            outcome=OUTCOME_TERMINAL,
            node_id=node.id,
            node_type=node_type,
            terminal_status=TERMINAL_STATUS_BY_NODE[node_type],
        )

    # --- condições ---
    if node_type == CONDITION_CLASSIFICATION:
        accepted = {_as_text(value) for value in node.config.get("values") or []}
        actual = _as_text(context.get("classification"))
        return _branch(graph, node, actual in accepted, {"classification": actual})

    if node_type == CONDITION_CONFIDENCE:
        comparator = str(node.config.get("comparator") or "gte")
        threshold = float(node.config.get("threshold") or 0.0)
        raw = context.get("confidence")
        confidence = float(raw) if isinstance(raw, (int, float)) else 0.0
        matched = confidence >= threshold if comparator == "gte" else confidence <= threshold
        return _branch(
            graph,
            node,
            matched,
            {"confidence": confidence, "threshold": threshold, "comparator": comparator},
        )

    if node_type == CONDITION_PIPELINE_STAGE:
        accepted = {_as_text(value) for value in node.config.get("values") or []}
        actual = _as_text(context.get("pipeline_stage"))
        return _branch(graph, node, actual in accepted, {"pipeline_stage": actual})

    if node_type == CONDITION_CUSTOMER_PROFILE:
        accepted = {_as_text(value) for value in node.config.get("values") or []}
        actual = _as_text(context.get("customer_profile"))
        return _branch(graph, node, actual in accepted, {"customer_profile": actual})

    if node_type == CONDITION_HAS_KNOWLEDGE_SOURCE:
        matched = bool(context.get("has_relevant_source"))
        return _branch(graph, node, matched, {"has_relevant_source": matched})

    if node_type == CONDITION_HANDOFF_REQUIRED:
        matched = bool(context.get("handoff_required"))
        return _branch(graph, node, matched, {"handoff_required": matched})

    # --- controle ---
    if node_type == CONTROL_IF:
        context_key = str(node.config.get("context_key") or "")
        expected = _as_text(node.config.get("equals"))
        actual = _as_text(context.get(context_key))
        return _branch(
            graph,
            node,
            actual == expected,
            {"context_key": context_key, "actual": actual, "expected": expected},
        )

    if node_type == CONTROL_SWITCH:
        context_key = str(node.config.get("context_key") or "")
        actual = _as_text(context.get(context_key))
        cases = [str(case) for case in node.config.get("cases") or []]
        handle = next((case for case in cases if _as_text(case) == actual), "default")
        return NodeOutcome(
            outcome=OUTCOME_CONTINUE,
            node_id=node.id,
            node_type=node_type,
            chosen_handle=handle,
            next_node_id=resolve_next(graph, node.id, handle),
            detail={"context_key": context_key, "actual": actual},
        )

    if node_type == CONTROL_HUMAN_APPROVAL:
        expires_in_hours = node.config.get("expires_in_hours")
        expires_at = (
            now + timedelta(hours=float(expires_in_hours))
            if isinstance(expires_in_hours, (int, float))
            else None
        )
        return NodeOutcome(
            outcome=OUTCOME_AWAIT_HUMAN,
            node_id=node.id,
            node_type=node_type,
            detail={
                "title": str(node.config.get("title") or "Aprovação necessária"),
                "summary": str(node.config.get("summary") or ""),
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
        )

    # --- tempo ---
    if node_type == DELAY_WAIT:
        amount = float(node.config.get("amount") or 0)
        unit = str(node.config.get("unit") or "hours")
        delta = {
            "minutes": timedelta(minutes=amount),
            "hours": timedelta(hours=amount),
            "days": timedelta(days=amount),
        }.get(unit, timedelta(hours=amount))
        return NodeOutcome(
            outcome=OUTCOME_WAIT,
            node_id=node.id,
            node_type=node_type,
            chosen_handle=HANDLE_NEXT,
            next_node_id=resolve_next(graph, node.id, HANDLE_NEXT),
            resume_at=now + delta,
            detail={"amount": amount, "unit": unit},
        )

    # --- IA: produz dado, nunca afirma efeito externo ---
    if node_type == AI_COMPOSE_MESSAGE:
        text = (ai_output.text if ai_output else None) or ""
        return NodeOutcome(
            outcome=OUTCOME_CONTINUE,
            node_id=node.id,
            node_type=node_type,
            chosen_handle=HANDLE_NEXT,
            next_node_id=resolve_next(graph, node.id, HANDLE_NEXT),
            context_updates={"composed_message": text},
            detail={
                "objective": str(node.config.get("objective") or ""),
                "simulated": bool(ai_output.simulated) if ai_output else True,
            },
        )

    if node_type == AI_EXTRACT_STRUCTURED:
        structured = dict(ai_output.structured) if ai_output else {}
        return NodeOutcome(
            outcome=OUTCOME_CONTINUE,
            node_id=node.id,
            node_type=node_type,
            chosen_handle=HANDLE_NEXT,
            next_node_id=resolve_next(graph, node.id, HANDLE_NEXT),
            context_updates={"extracted": structured},
            detail={"field_count": len(structured)},
        )

    if node_type == AI_CLASSIFY_OR_ROUTE:
        target = _as_text(node.config.get("target_label"))
        label = _as_text(ai_output.label if ai_output else None)
        return _branch(graph, node, label == target and bool(target), {"label": label})

    # --- ações: intenção de efeito externo ---
    if node_type in {
        ACTION_CREATE_HANDOFF,
        ACTION_CREATE_REMINDER,
        ACTION_SEND_MESSAGE,
        ACTION_UPDATE_PIPELINE_STAGE,
    }:
        return NodeOutcome(
            outcome=OUTCOME_EFFECT,
            node_id=node.id,
            node_type=node_type,
            chosen_handle=HANDLE_NEXT,
            next_node_id=resolve_next(graph, node.id, HANDLE_NEXT),
            effect=EffectIntent(
                kind=node_type,
                node_id=node.id,
                payload=_effect_payload(node, context),
            ),
        )

    return NodeOutcome(
        outcome=OUTCOME_TERMINAL,
        node_id=node.id,
        node_type=node_type,
        terminal_status="failed",
        detail={"reason": "unhandled_node_type"},
    )


def _effect_payload(node: WorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
    """Dados que o adapter precisa, já resolvidos a partir do contexto."""
    if node.node_type == ACTION_CREATE_HANDOFF:
        return {
            "reason": str(node.config.get("reason") or ""),
            "priority": str(node.config.get("priority") or "normal"),
            "contact_id": context.get("contact_id"),
        }
    if node.node_type == ACTION_CREATE_REMINDER:
        return {
            "message": str(node.config.get("message") or ""),
            "due_in_hours": float(node.config.get("due_in_hours") or 24),
            "contact_id": context.get("contact_id"),
        }
    if node.node_type == ACTION_SEND_MESSAGE:
        source = str(node.config.get("source") or "ai_node")
        text = (
            str(context.get("composed_message") or "")
            if source == "ai_node"
            else str(node.config.get("text") or "")
        )
        return {
            "source": source,
            "text": text,
            "contact_id": context.get("contact_id"),
        }
    if node.node_type == ACTION_UPDATE_PIPELINE_STAGE:
        return {
            "stage": str(node.config.get("stage") or ""),
            "contact_id": context.get("contact_id"),
        }
    return {}


def approval_handle(decision: str) -> str:
    """Alça de saída de um portão humano a partir da decisão registrada."""
    return HANDLE_APPROVED if decision == "approved" else HANDLE_REJECTED
