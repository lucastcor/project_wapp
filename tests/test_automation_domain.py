"""Domínio do Automation Studio: catálogo, forma canônica e validação.

Cobre os cenários obrigatórios 18 (grafo inválido nunca publica), 21 (segredo
na configuração é rejeitado) e a base do 17 (semântica única).
"""

from __future__ import annotations

import pytest

from app.automations.catalog import NODE_CATALOG, describe_catalog, outputs_for
from app.automations.domain import (
    canonical_form,
    content_hash,
    effect_key,
    enrollment_key,
    parse_graph,
)
from app.automations.validation import ValidationContext, validate_graph
from tests.support.automation_graphs import (
    branching_graph,
    cyclic_graph,
    full_e2e_graph,
    graph,
    minimal_graph,
    node,
    secret_bearing_graph,
)
from tests.support.automation_graphs import edge as make_edge

# Nomes reais das capacidades, vindos de `app.core.vertical_packs`.
FULL_CAPABILITIES = frozenset(
    {"outbound", "reminders", "scheduling", "knowledge_rag", "style_examples"}
)


def _context(**kwargs) -> ValidationContext:
    return ValidationContext(
        company_id=kwargs.pop("company_id", 1),
        capabilities=kwargs.pop("capabilities", FULL_CAPABILITIES),
        **kwargs,
    )


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


# --- catálogo ---------------------------------------------------------------------


def test_catalog_is_serializable_and_stable() -> None:
    described = describe_catalog()

    assert len(described) == len(NODE_CATALOG)
    assert described == describe_catalog()
    assert all(entry["node_type"] in NODE_CATALOG for entry in described)


def test_catalog_excludes_arbitrary_execution_nodes() -> None:
    """A v1 não expõe execução de código, shell, SQL livre nem HTTP genérico."""
    forbidden = ("code", "shell", "exec", "sql", "http", "webhook", "filesystem")

    for node_type in NODE_CATALOG:
        assert not any(token in node_type.lower() for token in forbidden), node_type


def test_switch_outputs_come_from_configuration() -> None:
    handles = outputs_for("control.switch", {"cases": ["a", "b"]})

    assert handles == ("a", "b", "default")


# --- forma canônica ----------------------------------------------------------------


def test_moving_a_node_does_not_change_the_content_hash() -> None:
    """Posição é apresentação: mover um bloco não é mudança de comportamento."""
    original = parse_graph(minimal_graph())
    moved_payload = minimal_graph()
    moved_payload["nodes"][0]["position"] = {"x": 999, "y": -42}
    moved = parse_graph(moved_payload)

    assert content_hash(original) == content_hash(moved)


def test_changing_configuration_changes_the_content_hash() -> None:
    first = parse_graph(branching_graph())
    changed = branching_graph()
    changed["nodes"][1]["config"]["values"] = ["reclamacao"]

    assert content_hash(first) != content_hash(parse_graph(changed))


def test_canonical_form_is_order_independent() -> None:
    payload = branching_graph()
    shuffled = {
        **payload,
        "nodes": list(reversed(payload["nodes"])),
        "edges": list(reversed(payload["edges"])),
    }

    assert canonical_form(parse_graph(payload)) == canonical_form(parse_graph(shuffled))


def test_effect_key_is_stable_and_scoped() -> None:
    same = effect_key(company_id=1, workflow_id=2, execution_id=3, node_id="n", occurrence=1)
    again = effect_key(company_id=1, workflow_id=2, execution_id=3, node_id="n", occurrence=1)
    other_tenant = effect_key(
        company_id=2, workflow_id=2, execution_id=3, node_id="n", occurrence=1
    )
    other_occurrence = effect_key(
        company_id=1, workflow_id=2, execution_id=3, node_id="n", occurrence=2
    )

    assert same == again
    assert same != other_tenant
    assert same != other_occurrence


def test_enrollment_key_binds_workflow_and_event() -> None:
    assert enrollment_key(workflow_id=1, event_key="a") != enrollment_key(
        workflow_id=1, event_key="b"
    )
    assert enrollment_key(workflow_id=1, event_key="a") != enrollment_key(
        workflow_id=2, event_key="a"
    )


# --- validação estrutural -----------------------------------------------------------


def test_minimal_graph_is_valid() -> None:
    result = validate_graph(parse_graph(minimal_graph()), context=_context())

    assert result.is_valid, [issue.as_dict() for issue in result.errors]
    assert result.trigger_type == "trigger.manual"


def test_full_e2e_graph_is_valid() -> None:
    result = validate_graph(parse_graph(full_e2e_graph()), context=_context())

    assert result.is_valid, [issue.as_dict() for issue in result.errors]


def test_cycles_are_rejected() -> None:
    result = validate_graph(parse_graph(cyclic_graph()), context=_context())

    assert not result.is_valid
    assert "cycle_detected" in _codes(result)


def test_graph_without_trigger_is_rejected() -> None:
    payload = graph([node("done", "terminal.done")], [])

    result = validate_graph(parse_graph(payload), context=_context())

    assert not result.is_valid
    assert "missing_trigger" in _codes(result)


def test_two_triggers_are_rejected() -> None:
    payload = graph(
        [
            node("t1", "trigger.manual"),
            node("t2", "trigger.messageProcessed"),
            node("done", "terminal.done"),
        ],
        [make_edge("t1", "done", "next")],
    )

    result = validate_graph(parse_graph(payload), context=_context())

    assert "multiple_triggers" in _codes(result)


def test_graph_without_terminal_is_rejected() -> None:
    payload = graph(
        [node("t", "trigger.manual"), node("w", "delay.wait", amount=1, unit="hours")],
        [make_edge("t", "w", "next")],
    )

    result = validate_graph(parse_graph(payload), context=_context())

    assert not result.is_valid
    assert "missing_terminal" in _codes(result)


def test_unreachable_node_is_reported() -> None:
    payload = minimal_graph()
    payload["nodes"].append(node("orphan", "terminal.done"))

    result = validate_graph(parse_graph(payload), context=_context())

    codes = _codes(result)
    assert "unreachable_node" in codes


def test_trigger_cannot_receive_input() -> None:
    payload = graph(
        [
            node("t", "trigger.manual"),
            node("c", "control.if", context_key="classification", equals="x"),
            node("done", "terminal.done"),
        ],
        [
            make_edge("t", "c", "next"),
            make_edge("c", "t", "true"),
            make_edge("c", "done", "false"),
        ],
    )

    result = validate_graph(parse_graph(payload), context=_context())

    assert "trigger_has_input" in _codes(result)


def test_terminal_cannot_have_output() -> None:
    payload = minimal_graph()
    payload["nodes"].append(node("second", "terminal.canceled"))
    payload["edges"].append(make_edge("done", "second", "next"))

    result = validate_graph(parse_graph(payload), context=_context())

    assert "terminal_has_output" in _codes(result)


def test_invalid_handle_is_rejected() -> None:
    payload = branching_graph()
    payload["edges"][1]["source_handle"] = "maybe"

    result = validate_graph(parse_graph(payload), context=_context())

    assert "invalid_source_handle" in _codes(result)


def test_two_edges_on_the_same_output_are_rejected() -> None:
    """Fan-out implícito não existe na v1."""
    payload = branching_graph()
    payload["nodes"].append(node("extra", "terminal.done"))
    payload["edges"].append(make_edge("cond", "extra", "true"))

    result = validate_graph(parse_graph(payload), context=_context())

    assert "duplicate_output_edge" in _codes(result)


def test_unconnected_output_is_reported() -> None:
    payload = branching_graph()
    payload["edges"] = [entry for entry in payload["edges"] if entry["source_handle"] != "false"]

    result = validate_graph(parse_graph(payload), context=_context())

    assert "unconnected_output" in _codes(result)


def test_unknown_node_type_is_rejected() -> None:
    payload = graph(
        [node("t", "trigger.manual"), node("x", "action.runShellCommand"), node("d", "terminal.done")],
        [make_edge("t", "x", "next"), make_edge("x", "d", "next")],
    )

    result = validate_graph(parse_graph(payload), context=_context())

    assert "unknown_node_type" in _codes(result)


def test_unknown_schema_version_stops_validation() -> None:
    payload = {**minimal_graph(), "schema_version": "automation-graph/99"}

    result = validate_graph(parse_graph(payload), context=_context())

    assert not result.is_valid
    assert "unknown_schema_version" in _codes(result)


# --- validação de configuração ------------------------------------------------------


def test_missing_required_field_is_reported() -> None:
    payload = graph(
        [
            node("t", "trigger.manual"),
            node("c", "condition.classification"),
            node("d", "terminal.done"),
            node("e", "terminal.canceled"),
        ],
        [
            make_edge("t", "c", "next"),
            make_edge("c", "d", "true"),
            make_edge("c", "e", "false"),
        ],
    )

    result = validate_graph(parse_graph(payload), context=_context())

    assert "missing_required_field" in _codes(result)


def test_value_outside_catalog_is_rejected() -> None:
    payload = branching_graph()
    payload["nodes"][1]["config"]["values"] = ["classificacao_inventada"]

    result = validate_graph(parse_graph(payload), context=_context())

    assert "value_outside_catalog" in _codes(result)


def test_numeric_limits_are_enforced() -> None:
    payload = graph(
        [
            node("t", "trigger.manual"),
            node("w", "delay.wait", amount=999_999, unit="hours"),
            node("d", "terminal.done"),
        ],
        [make_edge("t", "w", "next"), make_edge("w", "d", "next")],
    )

    result = validate_graph(parse_graph(payload), context=_context())

    assert "field_above_maximum" in _codes(result)


@pytest.mark.parametrize(
    "config",
    [
        {"api_key": "sk-abcdefghijklmnop123"},
        {"token": "algum-valor"},
        {"password": "x"},
        {"authorization": "Bearer abcdefghijklmno"},
        {"senha_do_portal": "x"},
    ],
)
def test_secret_like_keys_are_rejected(config) -> None:
    payload = graph(
        [
            node("t", "trigger.manual"),
            node("a", "action.createHandoff", reason="ok", **config),
            node("d", "terminal.done"),
        ],
        [make_edge("t", "a", "next"), make_edge("a", "d", "next")],
    )

    result = validate_graph(parse_graph(payload), context=_context())

    assert "secret_like_config_key" in _codes(result)


def test_secret_like_values_are_rejected_even_under_an_innocent_key() -> None:
    payload = graph(
        [
            node("t", "trigger.manual"),
            node("a", "action.createHandoff", reason="sk-abcdefghijklmnop123456"),
            node("d", "terminal.done"),
        ],
        [make_edge("t", "a", "next"), make_edge("a", "d", "next")],
    )

    result = validate_graph(parse_graph(payload), context=_context())

    assert "secret_like_config_value" in _codes(result)


def test_secret_bearing_graph_is_invalid() -> None:
    result = validate_graph(parse_graph(secret_bearing_graph()), context=_context())

    assert not result.is_valid


# --- capacidades e conflito com o legado ---------------------------------------------


def test_node_requiring_a_disabled_capability_is_rejected() -> None:
    payload = graph(
        [
            node("t", "trigger.manual"),
            node("r", "action.createReminder", message="Retomar contato", due_in_hours=24),
            node("d", "terminal.done"),
        ],
        [make_edge("t", "r", "next"), make_edge("r", "d", "next")],
    )

    result = validate_graph(
        parse_graph(payload),
        context=_context(capabilities=frozenset({"outbound"})),
    )

    assert "capability_not_enabled" in _codes(result)


def test_the_same_node_is_accepted_once_the_capability_is_enabled() -> None:
    payload = graph(
        [
            node("t", "trigger.manual"),
            node("r", "action.createReminder", message="Retomar contato", due_in_hours=24),
            node("d", "terminal.done"),
        ],
        [make_edge("t", "r", "next"), make_edge("r", "d", "next")],
    )

    result = validate_graph(parse_graph(payload), context=_context())

    assert result.is_valid, [issue.as_dict() for issue in result.errors]


def test_every_required_capability_exists_in_the_vertical_packs() -> None:
    """Uma capacidade inventada nunca seria habilitada, e o nó ficaria morto."""
    from app.core.vertical_packs import CAPABILITY_KEYS

    required = {
        spec.required_capability
        for spec in NODE_CATALOG.values()
        if spec.required_capability
    }

    assert required <= set(CAPABILITY_KEYS), sorted(required - set(CAPABILITY_KEYS))


def test_reference_outside_the_company_is_rejected() -> None:
    payload = branching_graph()

    result = validate_graph(
        parse_graph(payload),
        context=ValidationContext(
            company_id=1,
            capabilities=FULL_CAPABILITIES,
            available_references={"values": frozenset({"lead_novo"})},
        ),
    )

    assert "reference_not_in_company" in _codes(result)


def test_immediate_send_conflicts_with_the_legacy_auto_reply() -> None:
    """Cenário 23: fluxo não pode duplicar a resposta automática legada."""
    payload = graph(
        [
            node("t", "trigger.messageProcessed"),
            node("s", "action.sendMessage", source="static", text="Olá de novo"),
            node("d", "terminal.done"),
        ],
        [make_edge("t", "s", "next"), make_edge("s", "d", "next")],
    )

    result = validate_graph(
        parse_graph(payload),
        context=ValidationContext(
            company_id=1,
            capabilities=FULL_CAPABILITIES,
            legacy_auto_reply_triggers=frozenset({"trigger.messageProcessed"}),
        ),
    )

    assert "conflicts_with_legacy_auto_reply" in _codes(result)


def test_send_after_a_wait_is_allowed() -> None:
    """Follow-up depois de espera é intenção explícita, não duplicação."""
    payload = graph(
        [
            node("t", "trigger.messageProcessed"),
            node("w", "delay.wait", amount=2, unit="hours"),
            node("s", "action.sendMessage", source="static", text="Ainda posso ajudar?"),
            node("d", "terminal.done"),
        ],
        [
            make_edge("t", "w", "next"),
            make_edge("w", "s", "next"),
            make_edge("s", "d", "next"),
        ],
    )

    result = validate_graph(
        parse_graph(payload),
        context=ValidationContext(
            company_id=1,
            capabilities=FULL_CAPABILITIES,
            legacy_auto_reply_triggers=frozenset({"trigger.messageProcessed"}),
        ),
    )

    assert "conflicts_with_legacy_auto_reply" not in _codes(result)
    assert result.is_valid, [issue.as_dict() for issue in result.errors]


def test_send_after_an_approval_gate_is_allowed() -> None:
    payload = graph(
        [
            node("t", "trigger.messageProcessed"),
            node("g", "control.humanApproval", title="Revisar"),
            node("s", "action.sendMessage", source="static", text="Segue o retorno."),
            node("d", "terminal.done"),
            node("c", "terminal.canceled"),
        ],
        [
            make_edge("t", "g", "next"),
            make_edge("g", "s", "approved"),
            make_edge("g", "c", "rejected"),
            make_edge("s", "d", "next"),
        ],
    )

    result = validate_graph(
        parse_graph(payload),
        context=ValidationContext(
            company_id=1,
            capabilities=FULL_CAPABILITIES,
            legacy_auto_reply_triggers=frozenset({"trigger.messageProcessed"}),
        ),
    )

    assert "conflicts_with_legacy_auto_reply" not in _codes(result)
