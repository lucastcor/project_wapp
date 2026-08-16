"""Ponta a ponta do Automation Studio e integração com o orquestrador.

Cenário obrigatório 26:
    evento -> condição -> IA fake -> espera -> aprovação -> envio fake -> terminal

E o cenário 23: o fluxo disparado por `message.processed` não duplica a
resposta automática do caminho legado.

Nenhuma chamada externa: provider em mock, canal em mock, IA substituída por
um gerador determinístico.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.automations import engine
from app.automations.domain import EXECUTION_AWAITING_HUMAN, EXECUTION_SUCCEEDED
from app.automations.events import EVENT_MESSAGE_PROCESSED
from app.core.config import settings
from app.services import automation_approval_service as approvals
from app.services import automation_event_service as events
from app.services import automation_execution_service as executions
from app.services import automation_workflow_service as workflows
from app.services.automation_trigger_service import emit_manual_trigger
from app.storage import automation_repository as repo
from app.storage.repository import RuntimeRepository
from tests.support.automation_graphs import SAMPLE_MESSAGE_CONTEXT, full_e2e_graph

repository = RuntimeRepository()


@pytest.fixture(autouse=True)
def _enable_engine(monkeypatch):
    monkeypatch.setattr(settings, "automation_engine_enabled", True)


@pytest.fixture(autouse=True)
def _isolated_queues():
    from app.storage.sql_support import execute

    execute("DELETE FROM automation_jobs")
    execute("DELETE FROM automation_outbox")
    execute("DELETE FROM automation_approval_gates")
    yield


@pytest.fixture()
def deterministic_ai(monkeypatch):
    """Nó de IA determinístico. Nenhum provider é chamado."""
    from app.automations.semantics import AiOutput

    composed = "Segue o resumo do seu orçamento, conforme combinamos."

    def _fake_ai(*, company_id, node, context):
        return AiOutput(text=composed, simulated=False)

    monkeypatch.setattr(engine, "_resolve_ai_output", _fake_ai)
    return composed


def _publish_and_activate(company_id: int, graph_payload: dict, key: str) -> int:
    created = workflows.create_workflow(
        company_id=company_id,
        workflow_key=key,
        name="Follow-up de orçamento",
        created_by="tester",
    )
    workflow_id = int(created["id"])
    workflows.save_draft(
        company_id=company_id,
        workflow_id=workflow_id,
        graph=graph_payload,
        expected_revision=int(created["draft"]["revision"]),
        updated_by="tester",
    )
    workflows.publish_workflow(
        company_id=company_id,
        workflow_id=workflow_id,
        published_by="tester",
    )
    workflows.set_workflow_state(
        company_id=company_id,
        workflow_id=workflow_id,
        state="active",
        actor="tester",
    )
    return workflow_id


def _drain(company_id: int, *, limit: int = 40) -> None:
    for _ in range(limit):
        if not executions.run_due_jobs(limit=5)["processed"]:
            return


def test_full_lifecycle_event_condition_ai_wait_approval_send_terminal(
    company_factory,
    deterministic_ai,
) -> None:
    """Cenário 26: o caminho completo, com espera e portão humano reais."""
    company_id = company_factory("e2e")
    contact_id = "e2e-contact-0001"
    repository.save_contact(contact_id, "Contato de teste", company_id=company_id)

    workflow_id = _publish_and_activate(company_id, full_e2e_graph(), "e2e-followup")

    # 1. evento
    emitted = emit_manual_trigger(
        company_id=company_id,
        workflow_id=workflow_id,
        triggered_by="tester",
        contact_id=contact_id,
        nonce="e2e-run",
    )
    assert emitted["emitted"] is True

    # O gatilho do grafo é messageProcessed; o disparo manual não o alcança.
    # Emite o fato canônico correto para este fluxo.
    events.emit_event(
        company_id=company_id,
        event_type=EVENT_MESSAGE_PROCESSED,
        subject="message:e2e-1",
        payload={**SAMPLE_MESSAGE_CONTEXT, "contact_id": contact_id},
    )
    dispatched = events.dispatch_pending_events()
    assert dispatched["enrolled"] == 1

    execution = repo.list_executions(company_id=company_id)[0]
    execution_id = int(execution["id"])

    # 2. condição -> 3. IA -> 4. entra na espera
    _drain(company_id)
    execution = repo.get_execution(company_id=company_id, execution_id=execution_id)
    assert execution["status"] == "waiting"
    assert execution["context"]["composed_message"] == deterministic_ai

    # A espera é durável: nada pronto agora.
    assert executions.run_due_jobs(limit=5)["processed"] == 0

    waiting_job = repo.list_jobs(
        company_id=company_id,
        execution_id=execution_id,
        status="queued",
    )[0]
    assert waiting_job["run_at"] > datetime.now(UTC).isoformat()

    # 5. o tempo passa
    repo.finish_job(
        company_id=company_id,
        job_id=int(waiting_job["id"]),
        status="queued",
        run_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    _drain(company_id)

    execution = repo.get_execution(company_id=company_id, execution_id=execution_id)
    assert execution["status"] == EXECUTION_AWAITING_HUMAN

    # 6. aprovação humana
    pending = approvals.list_pending_gates(company_id=company_id)
    assert len(pending) == 1
    assert pending[0]["title"] == "Aprovar follow-up de orçamento"

    gate_id = int(pending[0]["id"])
    approvals.claim_gate(company_id=company_id, gate_id=gate_id, claimed_by="operador-real")
    decided = approvals.decide_gate(
        company_id=company_id,
        gate_id=gate_id,
        decision="approved",
        decided_by="operador-real",
        note="Texto revisado.",
    )
    assert decided["decided"] is True
    assert decided["resumed"] is True

    # 7. envio (enfileirado pelo adapter) -> 8. terminal
    _drain(company_id)

    execution = repo.get_execution(company_id=company_id, execution_id=execution_id)
    assert execution["status"] == EXECUTION_SUCCEEDED

    effects = repo.list_effects(company_id=company_id, execution_id=execution_id)
    assert len(effects) == 1
    assert effects[0]["effect_kind"] == "action.sendMessage"
    assert effects[0]["status"] == "succeeded"

    outbound = repository.get_outbound_messages(company_id=company_id)
    automation_messages = [
        message
        for message in outbound
        if str(message.get("source") or "").startswith("automation:")
    ]
    assert len(automation_messages) == 1
    assert automation_messages[0]["message"] == deterministic_ai

    # O histórico de passos conta a história inteira, em ordem.
    steps = repo.list_steps(company_id=company_id, execution_id=execution_id)
    assert [step["node_id"] for step in steps] == [
        "trigger",
        "cond",
        "compose",
        "wait",
        "gate",
        "send",
        "done",
    ]
    assert [step["chosen_handle"] for step in steps][:2] == ["next", "true"]

    # E a decisão ficou auditada com o principal real.
    gate = repo.get_gate(company_id=company_id, gate_id=gate_id)
    assert gate["decided_by"] == "operador-real"
    assert gate["decision"] == "approved"


def test_the_full_run_is_idempotent_end_to_end(company_factory, deterministic_ai) -> None:
    """Reentregar o mesmo evento não produz um segundo envio."""
    company_id = company_factory("e2e-idem")
    contact_id = "e2e-idem-0001"
    repository.save_contact(contact_id, "Contato", company_id=company_id)
    _publish_and_activate(company_id, full_e2e_graph(), "e2e-idem")

    for _ in range(2):
        events.emit_event(
            company_id=company_id,
            event_type=EVENT_MESSAGE_PROCESSED,
            subject="message:idem-1",
            payload={**SAMPLE_MESSAGE_CONTEXT, "contact_id": contact_id},
        )
        events.dispatch_pending_events()

    assert len(repo.list_executions(company_id=company_id)) == 1


def test_execution_context_is_redacted_for_inspection(company_factory, deterministic_ai) -> None:
    """Cenário de redação: a tela de execuções não expõe o identificador."""
    company_id = company_factory("e2e-redact")
    contact_id = "5511999998888"
    repository.save_contact(contact_id, "Contato", company_id=company_id)
    _publish_and_activate(company_id, full_e2e_graph(), "e2e-redact")

    events.emit_event(
        company_id=company_id,
        event_type=EVENT_MESSAGE_PROCESSED,
        subject="message:redact-1",
        payload={**SAMPLE_MESSAGE_CONTEXT, "contact_id": contact_id},
    )
    events.dispatch_pending_events()

    listed = executions.list_executions(company_id=company_id)

    assert listed
    assert listed[0]["context"]["contact_id"] != contact_id
    assert contact_id not in str(listed[0])


# --- 23: integração com o orquestrador, sem duplicar a resposta legada ------------------


def test_processing_a_message_emits_the_canonical_event_transactionally(
    company_factory,
) -> None:
    from app.core import orchestrator

    company_id = company_factory("orq-evento")

    result = orchestrator.process_incoming_message(
        contact_id="orq-contact-0001",
        contact_name="Contato",
        message="Quero um orçamento para o serviço.",
        company_id=company_id,
    )

    events_recorded = [
        entry
        for entry in repo.list_outbox_entries(company_id=company_id)
        if entry["status"] == "pending"
    ]
    assert len(events_recorded) == 1

    event = repo.get_event(
        company_id=company_id,
        event_id=int(events_recorded[0]["event_id"]),
    )
    assert event["event_type"] == EVENT_MESSAGE_PROCESSED
    assert event["payload"]["classification"] == result["classification"]
    assert event["payload"]["auto_reply_sent"] == bool(result["auto_reply"] and result["reply"])


def test_the_canonical_event_never_carries_conversation_content(
    company_factory,
) -> None:
    """O payload é redigido por allowlist: texto de conversa não entra."""
    from app.core import orchestrator

    company_id = company_factory("orq-redacao")
    secret_message = "Meu CPF e 529.982.247-25 e quero cancelar tudo"

    orchestrator.process_incoming_message(
        contact_id="orq-contact-0002",
        contact_name="Contato",
        message=secret_message,
        company_id=company_id,
    )

    entry = repo.list_outbox_entries(company_id=company_id)[0]
    event = repo.get_event(company_id=company_id, event_id=int(entry["event_id"]))

    serialized = str(event["payload"])
    assert secret_message not in serialized
    assert "529.982.247-25" not in serialized
    assert "message" not in event["payload"]
    assert "reply" not in event["payload"]


def test_a_flow_cannot_send_immediately_when_the_legacy_path_replied(
    company_factory,
) -> None:
    """Cenário 23: envio imediato no mesmo evento é recusado na publicação."""
    from tests.support.automation_graphs import edge, graph, node

    company_id = company_factory("orq-conflito")
    immediate_send = graph(
        [
            node("t", "trigger.messageProcessed"),
            node("s", "action.sendMessage", source="static", text="Oi de novo!"),
            node("d", "terminal.done"),
        ],
        [edge("t", "s", "next"), edge("s", "d", "next")],
    )

    created = workflows.create_workflow(
        company_id=company_id,
        workflow_key="conflito-legado",
        name="Conflito",
    )
    workflow_id = int(created["id"])
    workflows.save_draft(
        company_id=company_id,
        workflow_id=workflow_id,
        graph=immediate_send,
        expected_revision=1,
    )

    from app.automations.exceptions import GraphValidationError

    with pytest.raises(GraphValidationError):
        workflows.publish_workflow(company_id=company_id, workflow_id=workflow_id)


def test_a_disabled_engine_emits_nothing(company_factory, monkeypatch) -> None:
    """Flag desligada: o orquestrador funciona e nada de automação é gravado."""
    from app.core import orchestrator

    company_id = company_factory("orq-desligado")
    monkeypatch.setattr(settings, "automation_engine_enabled", False)

    result = orchestrator.process_incoming_message(
        contact_id="orq-contact-0003",
        contact_name="Contato",
        message="Bom dia, tudo bem?",
        company_id=company_id,
    )

    assert result["classification"]
    assert repo.list_outbox_entries(company_id=company_id) == []


def test_an_automation_failure_never_breaks_the_conversation(
    company_factory,
    monkeypatch,
) -> None:
    """Automação é pós-orquestração: um erro nela não derruba o atendimento."""
    from app.core import orchestrator
    from app.services import automation_trigger_service

    company_id = company_factory("orq-falha")

    def _explode(**kwargs):
        raise RuntimeError("automação quebrada")

    monkeypatch.setattr(
        automation_trigger_service,
        "build_message_processed_event",
        _explode,
    )
    monkeypatch.setattr(orchestrator, "build_message_processed_event", _explode)

    result = orchestrator.process_incoming_message(
        contact_id="orq-contact-0004",
        contact_name="Contato",
        message="Preciso de ajuda com o pedido.",
        company_id=company_id,
    )

    assert result["classification"]
    assert repository.get_messages(company_id=company_id)
    assert repo.list_outbox_entries(company_id=company_id) == []


# --- worker ---------------------------------------------------------------------------------


def test_the_worker_iteration_covers_recovery_dispatch_and_execution(
    company_factory,
    deterministic_ai,
) -> None:
    from app.tools.automation_worker import run_worker_iteration

    company_id = company_factory("worker")
    contact_id = "worker-contact-0001"
    repository.save_contact(contact_id, "Contato", company_id=company_id)
    _publish_and_activate(company_id, full_e2e_graph(), "worker-flow")

    events.emit_event(
        company_id=company_id,
        event_type=EVENT_MESSAGE_PROCESSED,
        subject="message:worker-1",
        payload={**SAMPLE_MESSAGE_CONTEXT, "contact_id": contact_id},
    )

    result = run_worker_iteration(job_limit=10)

    assert result["dispatched"]["dispatched"] == 1
    assert result["dispatched"]["enrolled"] == 1
    assert result["executed"]["processed"] >= 1
    assert "recovered_jobs" in result["recovered"]
