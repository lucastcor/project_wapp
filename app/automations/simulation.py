"""Simulação: percorre o fluxo sem produzir nenhum efeito.

Usa exatamente a mesma `semantics.evaluate_node` do motor. A diferença é o
que se faz com o resultado:

- `OUTCOME_WAIT`: a simulação avança o relógio em linha; o motor agenda um job;
- `OUTCOME_AWAIT_HUMAN`: a simulação usa a decisão fornecida pelo operador; o
  motor cria um portão durável e para;
- `OUTCOME_EFFECT`: a simulação registra a intenção; o motor grava no ledger e
  chama o adapter.

Nada aqui toca banco, provider ou canal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.automations.domain import WorkflowGraph
from app.automations.semantics import (
    OUTCOME_AWAIT_HUMAN,
    OUTCOME_EFFECT,
    OUTCOME_TERMINAL,
    OUTCOME_WAIT,
    AiOutput,
    approval_handle,
    evaluate_node,
    find_trigger,
    resolve_next,
)

DEFAULT_MAX_STEPS = 60


@dataclass
class SimulationStep:
    sequence: int
    node_id: str
    node_type: str
    outcome: str
    chosen_handle: str | None
    simulated_at: str
    detail: dict[str, Any] = field(default_factory=dict)
    effect: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "outcome": self.outcome,
            "chosen_handle": self.chosen_handle,
            "simulated_at": self.simulated_at,
            "detail": self.detail,
            "effect": self.effect,
        }


@dataclass
class SimulationResult:
    status: str
    steps: list[SimulationStep] = field(default_factory=list)
    effects: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    stopped_reason: str | None = None

    @property
    def path(self) -> list[str]:
        """Sequência de blocos percorridos. É o que a conformidade compara."""
        return [step.node_id for step in self.steps]

    @property
    def handles(self) -> list[str | None]:
        return [step.chosen_handle for step in self.steps]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stopped_reason": self.stopped_reason,
            "path": self.path,
            "steps": [step.as_dict() for step in self.steps],
            "effects": self.effects,
            "context": self.context,
            # Deixa explícito para a UI: nada aqui aconteceu de verdade.
            "produced_no_effects": True,
        }


def simulate(
    graph: WorkflowGraph,
    *,
    context: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    approval_decisions: dict[str, str] | None = None,
    ai_outputs: dict[str, AiOutput] | None = None,
) -> SimulationResult:
    """Percorre o fluxo com um evento fictício.

    `approval_decisions` responde pelos portões humanos (`node_id -> approved
    /rejected`); sem resposta, a simulação para no portão e diz por quê — que
    é exatamente o que a execução real faria.
    """
    clock = now or datetime.now(UTC)
    state = dict(context or {})
    decisions = approval_decisions or {}
    outputs = ai_outputs or {}
    result = SimulationResult(status="running")

    trigger = find_trigger(graph)
    if trigger is None:
        result.status = "invalid"
        result.stopped_reason = "missing_trigger"
        return result

    current_id: str | None = trigger.id
    sequence = 0

    while current_id is not None:
        if sequence >= max_steps:
            result.status = "stopped"
            result.stopped_reason = "max_steps_reached"
            break

        node = graph.node(current_id)
        if node is None:
            result.status = "invalid"
            result.stopped_reason = "dangling_edge"
            break

        sequence += 1
        outcome = evaluate_node(
            graph=graph,
            node=node,
            context=state,
            now=clock,
            ai_output=outputs.get(node.id) or AiOutput(simulated=True),
        )
        state.update(outcome.context_updates)

        step = SimulationStep(
            sequence=sequence,
            node_id=node.id,
            node_type=node.node_type,
            outcome=outcome.outcome,
            chosen_handle=outcome.chosen_handle,
            simulated_at=clock.isoformat(),
            detail=dict(outcome.detail),
            effect=outcome.effect.as_dict() if outcome.effect else None,
        )
        result.steps.append(step)

        if outcome.outcome == OUTCOME_TERMINAL:
            result.status = outcome.terminal_status or "succeeded"
            break

        if outcome.outcome == OUTCOME_EFFECT and outcome.effect is not None:
            # Intenção registrada, nada executado.
            result.effects.append(outcome.effect.as_dict())

        if outcome.outcome == OUTCOME_WAIT:
            # A simulação adianta o relógio: é o que torna o resultado
            # imediato sem mentir sobre a ordem dos passos.
            clock = outcome.resume_at or clock

        if outcome.outcome == OUTCOME_AWAIT_HUMAN:
            decision = decisions.get(node.id)
            if decision is None:
                result.status = "awaiting_human"
                result.stopped_reason = f"no_simulated_decision_for:{node.id}"
                break
            handle = approval_handle(decision)
            step.chosen_handle = handle
            current_id = resolve_next(graph, node.id, handle)
            continue

        current_id = outcome.next_node_id
        if current_id is None:
            result.status = "stopped"
            result.stopped_reason = "unconnected_output"
            break

    if result.status == "running":
        result.status = "stopped"
        result.stopped_reason = result.stopped_reason or "no_terminal_reached"

    result.context = state
    return result
