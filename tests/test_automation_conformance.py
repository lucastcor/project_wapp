"""Conformidade: simulação e runtime precisam concordar sobre o fluxo.

Cenário obrigatório 17. Os dois caminhos consomem `semantics.evaluate_node`.
Estes testes rodam os **mesmos** grafos e o **mesmo** contexto nos dois e
comparam sequência de blocos e alças escolhidas. Se alguém reintroduzir uma
segunda interpretação das regras, isto falha.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.automations.domain import parse_graph
from app.automations.semantics import (
    OUTCOME_AWAIT_HUMAN,
    OUTCOME_TERMINAL,
    OUTCOME_WAIT,
    AiOutput,
    approval_handle,
    evaluate_node,
    find_trigger,
    resolve_next,
)
from app.automations.simulation import simulate
from tests.support.automation_graphs import (
    SAMPLE_MESSAGE_CONTEXT,
    approval_graph,
    branching_graph,
    effect_graph,
    full_e2e_graph,
    minimal_graph,
    waiting_graph,
)

FIXED_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def walk_with_semantics(
    payload: dict,
    context: dict,
    *,
    approval_decisions: dict[str, str] | None = None,
) -> tuple[list[str], list[str | None]]:
    """Caminha o grafo chamando `evaluate_node` diretamente.

    É a leitura "do runtime": um passo por vez, decidindo o próximo nó pela
    alça escolhida. Deliberadamente não usa `simulate` — o objetivo é provar
    que os dois chegam ao mesmo lugar sem compartilhar o loop.
    """
    graph = parse_graph(payload)
    state = dict(context)
    decisions = approval_decisions or {}
    clock = FIXED_NOW

    trigger = find_trigger(graph)
    assert trigger is not None

    path: list[str] = []
    handles: list[str | None] = []
    current_id: str | None = trigger.id

    for _ in range(60):
        if current_id is None:
            break
        node = graph.node(current_id)
        if node is None:
            break

        outcome = evaluate_node(
            graph=graph,
            node=node,
            context=state,
            now=clock,
            ai_output=AiOutput(
                text="[simulação] mensagem que a IA produziria aqui.",
                label=str(state.get("classification") or ""),
                simulated=True,
            ),
        )
        state.update(outcome.context_updates)
        path.append(node.id)

        if outcome.outcome == OUTCOME_TERMINAL:
            handles.append(outcome.chosen_handle)
            break

        if outcome.outcome == OUTCOME_AWAIT_HUMAN:
            decision = decisions.get(node.id)
            if decision is None:
                handles.append(None)
                break
            handle = approval_handle(decision)
            handles.append(handle)
            current_id = resolve_next(graph, node.id, handle)
            continue

        if outcome.outcome == OUTCOME_WAIT:
            # O runtime suspende aqui; para efeito de caminho, o relógio
            # avança exatamente como a simulação faz.
            clock = outcome.resume_at or clock

        handles.append(outcome.chosen_handle)
        current_id = outcome.next_node_id

    return path, handles


@pytest.mark.parametrize(
    ("payload", "context", "decisions"),
    [
        (minimal_graph(), {}, None),
        (branching_graph(), SAMPLE_MESSAGE_CONTEXT, None),
        (branching_graph(), {**SAMPLE_MESSAGE_CONTEXT, "classification": "spam"}, None),
        (waiting_graph(), {}, None),
        (effect_graph(), SAMPLE_MESSAGE_CONTEXT, None),
        (approval_graph(), {}, {"gate": "approved"}),
        (approval_graph(), {}, {"gate": "rejected"}),
        (full_e2e_graph(), SAMPLE_MESSAGE_CONTEXT, {"gate": "approved"}),
        (full_e2e_graph(), SAMPLE_MESSAGE_CONTEXT, {"gate": "rejected"}),
        (
            full_e2e_graph(),
            {**SAMPLE_MESSAGE_CONTEXT, "classification": "reclamacao"},
            {"gate": "approved"},
        ),
    ],
)
def test_simulation_and_semantics_choose_the_same_path(payload, context, decisions) -> None:
    simulated = simulate(
        parse_graph(payload),
        context=dict(context),
        now=FIXED_NOW,
        approval_decisions=decisions,
        ai_outputs={
            node["id"]: AiOutput(
                text="[simulação] mensagem que a IA produziria aqui.",
                label=str(context.get("classification") or ""),
                simulated=True,
            )
            for node in payload["nodes"]
        },
    )
    semantic_path, semantic_handles = walk_with_semantics(
        payload,
        dict(context),
        approval_decisions=decisions,
    )

    assert simulated.path == semantic_path
    assert simulated.handles == semantic_handles


def test_classification_decides_the_same_branch_in_both_readings() -> None:
    matching = {**SAMPLE_MESSAGE_CONTEXT, "classification": "orcamento"}
    non_matching = {**SAMPLE_MESSAGE_CONTEXT, "classification": "spam"}

    matched = simulate(parse_graph(branching_graph()), context=matching, now=FIXED_NOW)
    unmatched = simulate(parse_graph(branching_graph()), context=non_matching, now=FIXED_NOW)

    assert matched.path == ["trigger", "cond", "done"]
    assert unmatched.path == ["trigger", "cond", "stop"]
    assert walk_with_semantics(branching_graph(), matching)[0] == matched.path
    assert walk_with_semantics(branching_graph(), non_matching)[0] == unmatched.path


def test_simulation_never_produces_a_real_effect() -> None:
    result = simulate(
        parse_graph(effect_graph()),
        context=SAMPLE_MESSAGE_CONTEXT,
        now=FIXED_NOW,
    )

    assert result.as_dict()["produced_no_effects"] is True
    assert len(result.effects) == 1
    # A intenção é registrada, mas nada aconteceu: o efeito não tem resultado.
    assert result.effects[0]["kind"] == "action.updatePipelineStage"
    assert "result" not in result.effects[0]


def test_simulation_stops_at_a_gate_without_a_decision() -> None:
    result = simulate(parse_graph(approval_graph()), context={}, now=FIXED_NOW)

    assert result.status == "awaiting_human"
    assert result.stopped_reason == "no_simulated_decision_for:gate"
    assert result.path == ["trigger", "gate"]


def test_wait_advances_the_simulated_clock() -> None:
    result = simulate(parse_graph(waiting_graph(amount=3, unit="hours")), now=FIXED_NOW)

    wait_step = next(step for step in result.steps if step.node_type == "delay.wait")
    terminal_step = result.steps[-1]

    assert wait_step.simulated_at == FIXED_NOW.isoformat()
    assert terminal_step.simulated_at == "2026-03-01T15:00:00+00:00"


def test_max_steps_stops_the_simulation() -> None:
    result = simulate(parse_graph(full_e2e_graph()), context=SAMPLE_MESSAGE_CONTEXT, max_steps=2)

    assert result.status == "stopped"
    assert result.stopped_reason == "max_steps_reached"
