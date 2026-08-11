"""Execução durável: idempotência, concorrência, lease, retry e pausa.

Cobre os cenários obrigatórios 8-16. Nenhuma chamada externa: os efeitos usam
os serviços reais do repositório local e o nó de IA é substituído por um
gerador determinístico.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.automations import engine
from app.automations.domain import (
    EXECUTION_AWAITING_HUMAN,
    EXECUTION_DEAD_LETTER,
    EXECUTION_SUCCEEDED,
    EXECUTION_WAITING,
)
from app.automations.exceptions import RetryableExecutionError
from app.core.config import settings
from app.services import automation_event_service as events
from app.services import automation_execution_service as executions
from app.services import automation_workflow_service as workflows
from app.services.automation_trigger_service import emit_manual_trigger
from app.storage import automation_repository as repo
from tests.support.automation_graphs import (
    SAMPLE_MESSAGE_CONTEXT,
    approval_graph,
    effect_graph,
    minimal_graph,
    waiting_graph,
)


@pytest.fixture(autouse=True)
def _enable_engine(monkeypatch):
    monkeypatch.setattr(settings, "automation_engine_enabled", True)


@pytest.fixture(autouse=True)
def _isolated_queues():
    """Limpa as filas globais entre testes.

    O banco de teste é compartilhado pela sessão e o worker é global por
    natureza: ele varre a fila sem filtrar empresa. Sem esta limpeza, um job
    deixado por outro teste seria reivindicado aqui e as asserções de
    concorrência e de reconciliação mediriam a coisa errada.
    """
    from app.storage.sql_support import execute

    execute("DELETE FROM automation_jobs")
    execute("DELETE FROM automation_outbox")
    execute("DELETE FROM automation_approval_gates")
    yield


@pytest.fixture()
def published_workflow(company_factory):
    """Cria, publica e ativa um workflow. Devolve os ids relevantes."""

    def _build(graph_payload: dict, *, activate: bool = True, company_id: int | None = None):
        resolved_company_id = company_id or company_factory("automation")
        created = workflows.create_workflow(
            company_id=resolved_company_id,
            workflow_key=f"wf-{repo.new_lease_owner('k').split(':')[1][:10]}",
            name="Fluxo de teste",
            created_by="tester",
        )
        workflow_id = int(created["id"])
        workflows.save_draft(
            company_id=resolved_company_id,
            workflow_id=workflow_id,
            graph=graph_payload,
            expected_revision=int(created["draft"]["revision"]),
            updated_by="tester",
        )
        published = workflows.publish_workflow(
            company_id=resolved_company_id,
            workflow_id=workflow_id,
            published_by="tester",
        )
        if activate:
            workflows.set_workflow_state(
                company_id=resolved_company_id,
                workflow_id=workflow_id,
                state="active",
                actor="tester",
            )
        return {
            "company_id": resolved_company_id,
            "workflow_id": workflow_id,
            "version_id": int(published["version_id"]),
            "version": int(published["version"]),
        }

    return _build


def _trigger(context: dict, *, nonce: str = "n1"):
    return emit_manual_trigger(
        company_id=context["company_id"],
        workflow_id=context["workflow_id"],
        triggered_by="tester",
        contact_id=SAMPLE_MESSAGE_CONTEXT["contact_id"],
        nonce=nonce,
    )


def _drain(company_id: int, *, limit: int = 30) -> list[dict]:
    """Roda o worker até não sobrar job pronto."""
    reports: list[dict] = []
    for _ in range(limit):
        result = executions.run_due_jobs(limit=5)
        if not result["processed"]:
            break
        reports.extend(result["reports"])
    return reports


# --- 8/9: idempotência de evento e inscrição ------------------------------------------


def test_the_same_event_is_recorded_once(published_workflow) -> None:
    context = published_workflow(minimal_graph())

    first = _trigger(context, nonce="same-key")
    second = _trigger(context, nonce="same-key")

    assert first["event_id"] == second["event_id"]
    assert first["created"] is True
    assert second["created"] is False


def test_redelivering_an_event_does_not_create_a_second_execution(published_workflow) -> None:
    context = published_workflow(minimal_graph())
    company_id = context["company_id"]

    _trigger(context, nonce="dedupe-key")
    events.dispatch_pending_events()
    # Reentrega: mesmo fato, mesma chave.
    _trigger(context, nonce="dedupe-key")
    events.dispatch_pending_events()

    all_executions = repo.list_executions(company_id=company_id)
    assert len(all_executions) == 1


def test_a_second_dispatch_of_the_same_outbox_entry_is_a_noop(published_workflow) -> None:
    context = published_workflow(minimal_graph())
    company_id = context["company_id"]

    _trigger(context, nonce="outbox-key")
    event_id = int(repo.list_outbox_entries(company_id=company_id)[0]["event_id"])

    first = events.dispatch_event(company_id=company_id, event_id=event_id)
    second = events.dispatch_event(company_id=company_id, event_id=event_id)

    assert len(first["enrolled"]) == 1
    assert second["enrolled"] == []
    assert second["skipped"][0]["reason"] == "already_enrolled"


# --- 10/11: concorrência e lease --------------------------------------------------------


def test_two_workers_never_claim_the_same_job(published_workflow) -> None:
    context = published_workflow(minimal_graph())
    _trigger(context, nonce="concurrency")
    events.dispatch_pending_events()

    first = repo.claim_job(lease_owner="worker-a")
    second = repo.claim_job(lease_owner="worker-b")

    assert first is not None
    assert second is None


def test_concurrent_workers_process_each_job_exactly_once(published_workflow) -> None:
    """Cenário 10 com vários jobs e dois workers alternando."""
    context = published_workflow(minimal_graph())
    company_id = context["company_id"]
    for index in range(5):
        _trigger(context, nonce=f"multi-{index}")
    events.dispatch_pending_events()

    claimed_ids: list[int] = []
    for owner in ("worker-a", "worker-b") * 6:
        job = repo.claim_job(lease_owner=owner)
        if job is None:
            continue
        claimed_ids.append(int(job["id"]))
        repo.finish_job(company_id=company_id, job_id=int(job["id"]), status="succeeded")

    assert len(claimed_ids) == len(set(claimed_ids))
    assert len(claimed_ids) == 5


def test_an_expired_lease_is_recovered(published_workflow) -> None:
    context = published_workflow(minimal_graph())
    company_id = context["company_id"]
    _trigger(context, nonce="lease")
    events.dispatch_pending_events()

    job = repo.claim_job(lease_owner="dead-worker")
    assert job is not None
    # Nenhum outro worker consegue pegar enquanto o lease vale.
    assert repo.claim_job(lease_owner="live-worker") is None

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    recovered = repo.recover_expired_leases(older_than_iso=future)

    assert recovered == 1
    reclaimed = repo.claim_job(lease_owner="live-worker")
    assert reclaimed is not None
    assert int(reclaimed["id"]) == int(job["id"])
    assert repo.get_job(company_id=company_id, job_id=int(job["id"]))["last_error"] == (
        "lease_expired"
    )


# --- 12: retry não repete efeito já concluído --------------------------------------------


def test_retry_after_failure_does_not_repeat_a_completed_effect(
    published_workflow,
    monkeypatch,
) -> None:
    """Cenário 12: o effect ledger decide, não o motor."""
    context = published_workflow(effect_graph())
    company_id = context["company_id"]
    _trigger(context, nonce="effect-once")
    events.dispatch_pending_events()

    applied: list[str] = []

    def _counting_adapter(kind, company_id, execution_id, payload):
        applied.append(kind)
        from app.automations.effects import EffectResult

        return EffectResult(succeeded=True, detail={"applied": True})

    monkeypatch.setattr(
        engine,
        "apply_effect",
        lambda **kwargs: _counting_adapter(
            kwargs["kind"], kwargs["company_id"], kwargs["execution_id"], kwargs["payload"]
        ),
    )

    _drain(company_id)
    assert applied == ["action.updatePipelineStage"]

    execution = repo.list_executions(company_id=company_id)[0]
    execution_id = int(execution["id"])

    # Cenário real: o worker aplicou o efeito e morreu antes de fechar o job.
    # A recuperação de lease reenfileira exatamente o mesmo passo — mesmo nó,
    # mesma ocorrência lógica —, então a chave de efeito é a mesma.
    repo.update_execution(
        company_id=company_id,
        execution_id=execution_id,
        status="queued",
        current_node_id="act",
    )
    repo.enqueue_job(
        company_id=company_id,
        execution_id=execution_id,
        job_kind="advance",
        node_id="act",
    )
    reports = _drain(company_id)

    # O adapter não foi chamado de novo.
    assert applied == ["action.updatePipelineStage"]
    assert any(report["effect_reused"] for report in reports)
    effects = repo.list_effects(company_id=company_id, execution_id=execution_id)
    assert len(effects) == 1
    assert effects[0]["status"] == "succeeded"


def test_a_retryable_failure_is_rescheduled_with_backoff(published_workflow, monkeypatch) -> None:
    context = published_workflow(effect_graph())
    company_id = context["company_id"]
    _trigger(context, nonce="retryable")
    events.dispatch_pending_events()

    def _always_retryable(**kwargs):
        raise RetryableExecutionError("provider indisponível")

    monkeypatch.setattr(engine, "apply_effect", _always_retryable)

    executions.run_due_jobs(limit=1)  # trigger
    result = executions.run_due_jobs(limit=1)  # nó de efeito

    assert result["reports"][0]["status"] == "retry_scheduled"
    job = repo.list_jobs(company_id=company_id, status="queued")[0]
    assert int(job["attempts"]) == 1
    assert job["run_at"] > datetime.now(UTC).isoformat()


def test_exhausted_attempts_land_in_dead_letter(published_workflow, monkeypatch) -> None:
    context = published_workflow(effect_graph())
    company_id = context["company_id"]
    monkeypatch.setattr(settings, "automation_max_attempts", 2)
    monkeypatch.setattr(settings, "automation_retry_base_seconds", 0)
    _trigger(context, nonce="dead-letter")
    events.dispatch_pending_events()

    monkeypatch.setattr(
        engine,
        "apply_effect",
        lambda **kwargs: (_ for _ in ()).throw(RetryableExecutionError("sempre falha")),
    )

    for _ in range(6):
        if not executions.run_due_jobs(limit=1)["processed"]:
            break

    execution = repo.list_executions(company_id=company_id)[0]
    assert execution["status"] == EXECUTION_DEAD_LETTER

    dead_letter = executions.list_dead_letter(company_id=company_id)
    assert len(dead_letter) == 1
    assert dead_letter[0]["attempts"] >= 2


def test_dead_letter_can_be_retried_manually(published_workflow, monkeypatch) -> None:
    context = published_workflow(effect_graph())
    company_id = context["company_id"]
    monkeypatch.setattr(settings, "automation_max_attempts", 1)
    monkeypatch.setattr(settings, "automation_retry_base_seconds", 0)
    _trigger(context, nonce="manual-retry")
    events.dispatch_pending_events()

    failing = {"count": 0}

    def _fail_once(**kwargs):
        failing["count"] += 1
        if failing["count"] == 1:
            raise RetryableExecutionError("falha transitória")
        from app.automations.effects import EffectResult

        return EffectResult(succeeded=True, detail={})

    monkeypatch.setattr(engine, "apply_effect", _fail_once)

    for _ in range(4):
        if not executions.run_due_jobs(limit=1)["processed"]:
            break

    execution_id = int(repo.list_executions(company_id=company_id)[0]["id"])
    assert repo.get_execution(company_id=company_id, execution_id=execution_id)["status"] == (
        EXECUTION_DEAD_LETTER
    )

    retried = executions.retry_execution(company_id=company_id, execution_id=execution_id)
    assert retried["retried"] is True

    _drain(company_id)
    assert repo.get_execution(company_id=company_id, execution_id=execution_id)["status"] == (
        EXECUTION_SUCCEEDED
    )


# --- 13: wait sobrevive à reconstrução do serviço -----------------------------------------


def test_a_wait_survives_a_service_restart(published_workflow) -> None:
    """Cenário 13: a espera é uma linha no banco, não um timer em memória."""
    context = published_workflow(waiting_graph(amount=1, unit="hours"))
    company_id = context["company_id"]
    _trigger(context, nonce="wait")
    events.dispatch_pending_events()

    executions.run_due_jobs(limit=1)  # trigger
    executions.run_due_jobs(limit=1)  # entra no wait

    execution = repo.list_executions(company_id=company_id)[0]
    execution_id = int(execution["id"])
    assert execution["status"] == EXECUTION_WAITING

    # Nada pronto agora: o job está no futuro.
    assert executions.run_due_jobs(limit=5)["processed"] == 0

    # "Reinício": nenhum estado em memória é preservado, só o banco. O job
    # continua lá, com run_at no futuro.
    pending = repo.list_jobs(company_id=company_id, execution_id=execution_id, status="queued")
    assert len(pending) == 1
    assert pending[0]["run_at"] > datetime.now(UTC).isoformat()

    # Antecipa o relógio da fila e o fluxo retoma.
    repo.finish_job(
        company_id=company_id,
        job_id=int(pending[0]["id"]),
        status="queued",
        run_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    _drain(company_id)

    assert repo.get_execution(company_id=company_id, execution_id=execution_id)["status"] == (
        EXECUTION_SUCCEEDED
    )


# --- 14: pausa bloqueia entradas e preserva quem espera -------------------------------------


def test_pausing_blocks_new_enrollments(published_workflow) -> None:
    context = published_workflow(minimal_graph())
    company_id = context["company_id"]

    workflows.set_workflow_state(
        company_id=company_id,
        workflow_id=context["workflow_id"],
        state="paused",
        actor="tester",
    )
    _trigger(context, nonce="paused")
    events.dispatch_pending_events()

    assert repo.list_executions(company_id=company_id) == []


def test_pausing_preserves_a_waiting_execution(published_workflow) -> None:
    """Cenário 14: pausar não mata quem já está esperando."""
    context = published_workflow(waiting_graph(amount=1, unit="hours"))
    company_id = context["company_id"]
    _trigger(context, nonce="pause-waiting")
    events.dispatch_pending_events()

    executions.run_due_jobs(limit=2)
    execution_id = int(repo.list_executions(company_id=company_id)[0]["id"])
    assert repo.get_execution(company_id=company_id, execution_id=execution_id)["status"] == (
        EXECUTION_WAITING
    )

    workflows.set_workflow_state(
        company_id=company_id,
        workflow_id=context["workflow_id"],
        state="paused",
        actor="tester",
    )

    execution = repo.get_execution(company_id=company_id, execution_id=execution_id)
    assert execution["status"] == EXECUTION_WAITING
    assert repo.list_jobs(company_id=company_id, execution_id=execution_id, status="queued")


# --- 15: version pinning -------------------------------------------------------------------


def test_an_execution_stays_pinned_to_the_version_it_started_with(published_workflow) -> None:
    """Cenário 15: republicar não troca a versão de uma execução em voo."""
    context = published_workflow(waiting_graph(amount=1, unit="hours"))
    company_id = context["company_id"]
    workflow_id = context["workflow_id"]
    _trigger(context, nonce="pinned")
    events.dispatch_pending_events()
    executions.run_due_jobs(limit=2)

    execution_id = int(repo.list_executions(company_id=company_id)[0]["id"])
    original_version_id = int(
        repo.get_execution(company_id=company_id, execution_id=execution_id)["version_id"]
    )

    # Publica uma versão diferente e ativa.
    draft = repo.get_draft(company_id=company_id, workflow_id=workflow_id)
    workflows.save_draft(
        company_id=company_id,
        workflow_id=workflow_id,
        graph=waiting_graph(amount=9, unit="hours"),
        expected_revision=int(draft["revision"]),
        updated_by="tester",
    )
    republished = workflows.publish_workflow(
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

    assert int(republished["version_id"]) != original_version_id
    still = repo.get_execution(company_id=company_id, execution_id=execution_id)
    assert int(still["version_id"]) == original_version_id


def test_a_published_version_is_immutable(published_workflow) -> None:
    """Cenário 19: publicar de novo cria versão nova; a anterior não muda."""
    context = published_workflow(minimal_graph(), activate=False)
    company_id = context["company_id"]
    workflow_id = context["workflow_id"]

    original = repo.get_version(company_id=company_id, version_id=context["version_id"])
    original_hash = str(original["content_hash"])

    draft = repo.get_draft(company_id=company_id, workflow_id=workflow_id)
    workflows.save_draft(
        company_id=company_id,
        workflow_id=workflow_id,
        graph=waiting_graph(),
        expected_revision=int(draft["revision"]),
        updated_by="tester",
    )
    workflows.publish_workflow(company_id=company_id, workflow_id=workflow_id)

    unchanged = repo.get_version(company_id=company_id, version_id=context["version_id"])
    assert str(unchanged["content_hash"]) == original_hash
    assert len(repo.list_versions(company_id=company_id, workflow_id=workflow_id)) == 2


# --- 16: portão humano ------------------------------------------------------------------------


def test_the_gate_blocks_the_execution_until_a_decision(published_workflow) -> None:
    context = published_workflow(approval_graph())
    company_id = context["company_id"]
    _trigger(context, nonce="gate")
    events.dispatch_pending_events()
    _drain(company_id)

    execution = repo.list_executions(company_id=company_id)[0]
    assert execution["status"] == EXECUTION_AWAITING_HUMAN

    from app.services import automation_approval_service as approvals

    gates = approvals.list_pending_gates(company_id=company_id)
    assert len(gates) == 1
    assert gates[0]["title"] == "Revisar antes de seguir"


def test_only_one_operator_claims_a_gate(published_workflow) -> None:
    from app.automations.exceptions import ApprovalGateError
    from app.services import automation_approval_service as approvals

    context = published_workflow(approval_graph())
    company_id = context["company_id"]
    _trigger(context, nonce="gate-claim")
    events.dispatch_pending_events()
    _drain(company_id)

    gate_id = int(approvals.list_pending_gates(company_id=company_id)[0]["id"])

    approvals.claim_gate(company_id=company_id, gate_id=gate_id, claimed_by="operador-a")
    with pytest.raises(ApprovalGateError, match="already"):
        approvals.claim_gate(company_id=company_id, gate_id=gate_id, claimed_by="operador-b")


def test_deciding_twice_is_idempotent(published_workflow) -> None:
    from app.services import automation_approval_service as approvals

    context = published_workflow(approval_graph())
    company_id = context["company_id"]
    _trigger(context, nonce="gate-idempotent")
    events.dispatch_pending_events()
    _drain(company_id)

    gate_id = int(approvals.list_pending_gates(company_id=company_id)[0]["id"])
    first = approvals.decide_gate(
        company_id=company_id,
        gate_id=gate_id,
        decision="approved",
        decided_by="operador-a",
    )
    second = approvals.decide_gate(
        company_id=company_id,
        gate_id=gate_id,
        decision="rejected",
        decided_by="operador-b",
    )

    assert first["decided"] is True
    assert second["decided"] is False
    assert second["reason"] == "already_decided"
    assert second["decision"] == "approved"

    # E apenas um job de retomada foi criado.
    execution_id = int(repo.list_executions(company_id=company_id)[0]["id"])
    resume_jobs = [
        job
        for job in repo.list_jobs(company_id=company_id, execution_id=execution_id)
        if job["job_kind"] == "resume_gate"
    ]
    assert len(resume_jobs) == 1


def test_approval_and_rejection_follow_different_paths(published_workflow) -> None:
    from app.services import automation_approval_service as approvals

    for decision, expected_status in (("approved", "succeeded"), ("rejected", "canceled")):
        context = published_workflow(approval_graph())
        company_id = context["company_id"]
        _trigger(context, nonce=f"gate-{decision}")
        events.dispatch_pending_events()
        _drain(company_id)

        gate_id = int(approvals.list_pending_gates(company_id=company_id)[0]["id"])
        approvals.decide_gate(
            company_id=company_id,
            gate_id=gate_id,
            decision=decision,
            decided_by="operador",
        )
        _drain(company_id)

        execution = repo.list_executions(company_id=company_id)[0]
        assert execution["status"] == expected_status, decision


def test_a_paused_workflow_does_not_execute_the_gate(published_workflow) -> None:
    """Cenário 16: pausado registra a decisão, mas não avança."""
    from app.services import automation_approval_service as approvals

    context = published_workflow(approval_graph())
    company_id = context["company_id"]
    _trigger(context, nonce="gate-paused")
    events.dispatch_pending_events()
    _drain(company_id)

    workflows.set_workflow_state(
        company_id=company_id,
        workflow_id=context["workflow_id"],
        state="paused",
        actor="tester",
    )
    gate_id = int(approvals.list_pending_gates(company_id=company_id)[0]["id"])
    result = approvals.decide_gate(
        company_id=company_id,
        gate_id=gate_id,
        decision="approved",
        decided_by="operador",
    )

    assert result["decided"] is True
    assert result["resumed"] is False
    assert result["reason"] == "workflow_paused"

    execution_id = int(repo.list_executions(company_id=company_id)[0]["id"])
    assert repo.get_execution(company_id=company_id, execution_id=execution_id)["status"] == (
        EXECUTION_AWAITING_HUMAN
    )

    # Reativar retoma a decisão que ficou guardada.
    workflows.set_workflow_state(
        company_id=company_id,
        workflow_id=context["workflow_id"],
        state="active",
        actor="tester",
    )
    _drain(company_id)
    assert repo.get_execution(company_id=company_id, execution_id=execution_id)["status"] == (
        EXECUTION_SUCCEEDED
    )


def test_an_abandoned_gate_claim_returns_to_the_queue(published_workflow) -> None:
    from app.services import automation_approval_service as approvals

    context = published_workflow(approval_graph())
    company_id = context["company_id"]
    _trigger(context, nonce="gate-abandoned")
    events.dispatch_pending_events()
    _drain(company_id)

    gate_id = int(approvals.list_pending_gates(company_id=company_id)[0]["id"])
    approvals.claim_gate(company_id=company_id, gate_id=gate_id, claimed_by="operador-sumiu")
    assert approvals.list_pending_gates(company_id=company_id) == []

    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert repo.recover_expired_gate_claims(older_than_iso=future) == 1
    assert len(approvals.list_pending_gates(company_id=company_id)) == 1


def test_execution_cancellation_is_cooperative(published_workflow) -> None:
    context = published_workflow(waiting_graph(amount=5, unit="hours"))
    company_id = context["company_id"]
    _trigger(context, nonce="cancel")
    events.dispatch_pending_events()
    executions.run_due_jobs(limit=2)

    execution_id = int(repo.list_executions(company_id=company_id)[0]["id"])
    result = executions.cancel_execution(company_id=company_id, execution_id=execution_id)

    assert result["canceled"] is True
    assert repo.get_execution(company_id=company_id, execution_id=execution_id)["status"] == (
        "canceled"
    )
    assert result["canceled_jobs"] >= 1


def test_max_steps_per_execution_is_enforced(published_workflow, monkeypatch) -> None:
    context = published_workflow(effect_graph())
    company_id = context["company_id"]
    monkeypatch.setattr(settings, "automation_max_steps_per_execution", 1)
    _trigger(context, nonce="max-steps")
    events.dispatch_pending_events()

    _drain(company_id)

    execution = repo.list_executions(company_id=company_id)[0]
    assert execution["status"] == "failed"
    assert execution["failure_reason"] == "max_steps_per_execution_reached"
