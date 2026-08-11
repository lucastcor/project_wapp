"""Motor de execução durável.

O motor avança **um passo por job**. Isso é deliberado: cada passo tem seu
próprio lease, sua própria tentativa e sua própria linha no histórico, então
uma falha no meio do fluxo não reexecuta o que já deu certo.

Contrato de cada passo:

1. carrega a execução e a **versão com que ela começou** (nunca a mais
   recente: republicar não troca a versão de uma execução em voo);
2. avalia o nó pela `semantics` — a mesma que a simulação usa;
3. materializa o resultado:
   - `continue` → grava o passo e enfileira o próximo job;
   - `wait` → grava o passo e enfileira um job com `run_at` no futuro;
   - `await_human` → cria o portão durável e para;
   - `effect` → reserva no ledger, executa o adapter **fora** da transação,
     registra o resultado e segue;
   - `terminal` → fecha a execução.

Pausa não interrompe execução em andamento. Ela impede **novas entradas**: a
verificação de deployment ativo acontece no dispatch, não aqui. Uma execução
que já está esperando retoma quando o wait vencer, com a versão original.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.automations.catalog import get_spec
from app.automations.domain import (
    EFFECT_FAILED,
    EFFECT_SUCCEEDED,
    EXECUTION_AWAITING_HUMAN,
    EXECUTION_CANCELED,
    EXECUTION_DEAD_LETTER,
    EXECUTION_FAILED,
    EXECUTION_RUNNING,
    EXECUTION_SUCCEEDED,
    EXECUTION_WAITING,
    JOB_CANCELED,
    JOB_DEAD_LETTER,
    JOB_QUEUED,
    JOB_SUCCEEDED,
    TERMINAL_EXECUTION_STATES,
    effect_key,
    parse_graph,
)
from app.automations.effects import apply_effect
from app.automations.exceptions import PermanentExecutionError, RetryableExecutionError
from app.automations.semantics import (
    OUTCOME_AWAIT_HUMAN,
    OUTCOME_EFFECT,
    OUTCOME_TERMINAL,
    OUTCOME_WAIT,
    AiOutput,
    evaluate_node,
)
from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.storage import automation_repository as repo

logger = get_logger(__name__)

JOB_KIND_ADVANCE = "advance"
JOB_KIND_RESUME_WAIT = "resume_wait"
JOB_KIND_RESUME_GATE = "resume_gate"

# Ocorrência lógica de um nó dentro de uma execução.
#
# Na v1 é sempre 1, e isso é uma consequência do validador: ciclos são
# rejeitados, então cada nó é visitado no máximo uma vez por execução. O valor
# precisa ser **estável entre tentativas** — derivá-lo de `steps_executed`
# faria a chave de efeito mudar a cada retry, e o ledger deixaria de
# reconhecer o efeito já aplicado. Quando loops existirem, este passa a ser o
# índice da iteração.
SINGLE_PASS_OCCURRENCE = 1

STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"
STEP_WAITING = "waiting"
STEP_AWAITING_HUMAN = "awaiting_human"


@dataclass
class StepReport:
    """O que aconteceu neste passo. Usado pelo worker e pelos testes."""

    execution_id: int
    status: str
    node_id: str | None = None
    node_type: str | None = None
    outcome: str | None = None
    chosen_handle: str | None = None
    detail: dict[str, Any] | None = None
    scheduled_job_id: int | None = None
    gate_id: int | None = None
    effect_reused: bool = False


def backoff_seconds(attempt: int) -> float:
    """Backoff exponencial com jitter limitado.

    O jitter evita que várias execuções que falharam juntas voltem juntas —
    o padrão que transforma uma indisponibilidade curta em tempestade.

    A fonte de aleatoriedade é `secrets`, não `random`. Não é preciosismo:
    um cronograma de retry previsível é observável por quem consegue provocar
    falhas, e não há nenhum ganho em usar um PRNG determinístico aqui — o
    jitter não precisa ser reproduzível.
    """
    base = float(settings.automation_retry_base_seconds)
    ceiling = float(settings.automation_retry_max_seconds)
    raw = min(base * (2 ** max(0, attempt - 1)), ceiling)

    jitter_ceiling_ms = int(min(raw * 0.25, 30.0) * 1000)
    jitter = secrets.randbelow(jitter_ceiling_ms + 1) / 1000 if jitter_ceiling_ms > 0 else 0.0
    return min(raw + jitter, ceiling)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _load_execution_and_graph(company_id: int, execution_id: int):
    execution = repo.get_execution(company_id=company_id, execution_id=execution_id)
    if execution is None:
        raise PermanentExecutionError(f"Execution {execution_id} not found in this company.")

    # A versão vem da execução, não do deployment corrente. É isso que prende
    # a execução à versão com que ela começou.
    version = repo.get_version(company_id=company_id, version_id=int(execution["version_id"]))
    if version is None:
        raise PermanentExecutionError(
            f"Version {execution['version_id']} pinned by execution {execution_id} is missing."
        )
    return execution, version, parse_graph(version.get("graph") or {})


def _schedule_next(
    *,
    company_id: int,
    execution_id: int,
    node_id: str | None,
    job_kind: str = JOB_KIND_ADVANCE,
    run_at: str | None = None,
) -> int:
    return repo.enqueue_job(
        company_id=company_id,
        execution_id=execution_id,
        job_kind=job_kind,
        node_id=node_id,
        run_at=run_at,
        max_attempts=settings.automation_max_attempts,
    )


def _finish_execution(
    *,
    company_id: int,
    execution_id: int,
    status: str,
    failure_reason: str | None = None,
) -> None:
    repo.update_execution(
        company_id=company_id,
        execution_id=execution_id,
        status=status,
        failure_reason=failure_reason,
        finished=True,
    )
    log_event(
        logger,
        logging.INFO,
        "automation.execution.finished",
        company_id=company_id,
        execution_id=execution_id,
        execution_status=status,
    )


def _resolve_ai_output(
    *,
    company_id: int,
    node,
    context: dict[str, Any],
) -> AiOutput:
    """Executa o nó de IA reutilizando o provider e o RAG já existentes.

    Não há segundo sistema de prompt aqui: quem compõe é
    `reply_services.generate_business_reply`, o mesmo caminho do atendimento.
    """
    from app.automations.catalog import (
        AI_CLASSIFY_OR_ROUTE,
        AI_COMPOSE_MESSAGE,
        AI_EXTRACT_STRUCTURED,
    )

    if node.node_type == AI_COMPOSE_MESSAGE:
        from app.services.reply_services import generate_business_reply

        objective = str(node.config.get("objective") or "")
        reply = generate_business_reply(
            classification=str(context.get("classification") or "duvida_geral"),
            message=objective,
            contact_name=None,
            company_id=int(company_id),
            customer_profile=str(context.get("customer_profile") or "unknown"),
            conversation_context=[],
        )
        text = reply if isinstance(reply, str) else str((reply or {}).get("reply") or "")
        return AiOutput(text=text, simulated=False)

    if node.node_type == AI_EXTRACT_STRUCTURED:
        # Extração usa o que o orquestrador já apurou. Sem inventar campo: o
        # que não estiver no contexto sai ausente, não sai adivinhado.
        requested = [str(field) for field in node.config.get("fields") or []]
        structured = {
            field: context[field] for field in requested if context.get(field) is not None
        }
        return AiOutput(structured=structured, simulated=False)

    if node.node_type == AI_CLASSIFY_OR_ROUTE:
        return AiOutput(label=str(context.get("classification") or ""), simulated=False)

    return AiOutput(simulated=False)


def execute_step(*, job: dict[str, Any]) -> StepReport:
    """Executa um passo a partir de um job já reservado pelo worker."""
    company_id = int(job["company_id"])
    execution_id = int(job["execution_id"])

    if int(job.get("cancel_requested") or 0):
        _finish_execution(
            company_id=company_id,
            execution_id=execution_id,
            status=EXECUTION_CANCELED,
            failure_reason="canceled_by_operator",
        )
        return StepReport(execution_id=execution_id, status=EXECUTION_CANCELED)

    execution, _version, graph = _load_execution_and_graph(company_id, execution_id)

    if str(execution["status"]) in TERMINAL_EXECUTION_STATES:
        # Job duplicado de uma execução já encerrada: nada a fazer.
        return StepReport(execution_id=execution_id, status=str(execution["status"]))

    if int(execution["steps_executed"]) >= settings.automation_max_steps_per_execution:
        _finish_execution(
            company_id=company_id,
            execution_id=execution_id,
            status=EXECUTION_FAILED,
            failure_reason="max_steps_per_execution_reached",
        )
        return StepReport(execution_id=execution_id, status=EXECUTION_FAILED)

    node_id = str(job.get("node_id") or execution.get("current_node_id") or "")
    node = graph.node(node_id)
    if node is None:
        _finish_execution(
            company_id=company_id,
            execution_id=execution_id,
            status=EXECUTION_FAILED,
            failure_reason=f"node_not_in_pinned_version:{node_id}",
        )
        return StepReport(execution_id=execution_id, status=EXECUTION_FAILED, node_id=node_id)

    context = dict(execution.get("context") or {})
    repo.update_execution(
        company_id=company_id,
        execution_id=execution_id,
        status=EXECUTION_RUNNING,
        current_node_id=node_id,
    )

    spec = get_spec(node.node_type)
    ai_output = (
        _resolve_ai_output(company_id=company_id, node=node, context=context)
        if spec is not None and spec.category == "ai"
        else None
    )

    outcome = evaluate_node(
        graph=graph,
        node=node,
        context=context,
        now=datetime.now(UTC),
        ai_output=ai_output,
    )
    context.update(outcome.context_updates)
    attempt = int(job.get("attempts") or 0) + 1

    # --- terminal ---
    if outcome.outcome == OUTCOME_TERMINAL:
        repo.append_step(
            company_id=company_id,
            execution_id=execution_id,
            node_id=node.id,
            node_type=node.node_type,
            status=STEP_SUCCEEDED,
            attempt=attempt,
            detail=outcome.detail,
        )
        repo.update_execution(
            company_id=company_id,
            execution_id=execution_id,
            context=context,
            increment_steps=1,
        )
        status = {
            "succeeded": EXECUTION_SUCCEEDED,
            "failed": EXECUTION_FAILED,
            "canceled": EXECUTION_CANCELED,
        }.get(str(outcome.terminal_status), EXECUTION_SUCCEEDED)
        _finish_execution(
            company_id=company_id,
            execution_id=execution_id,
            status=status,
            failure_reason=(
                str(outcome.detail.get("reason") or "") or None
                if status == EXECUTION_FAILED
                else None
            ),
        )
        return StepReport(
            execution_id=execution_id,
            status=status,
            node_id=node.id,
            node_type=node.node_type,
            outcome=outcome.outcome,
            detail=outcome.detail,
        )

    # --- portão humano ---
    if outcome.outcome == OUTCOME_AWAIT_HUMAN:
        occurrence = SINGLE_PASS_OCCURRENCE
        gate_id = repo.create_gate(
            company_id=company_id,
            execution_id=execution_id,
            node_id=node.id,
            occurrence=occurrence,
            title=str(outcome.detail.get("title") or "Aprovação necessária"),
            summary=str(outcome.detail.get("summary") or "") or None,
            payload={"context_keys": sorted(context.keys())},
            expires_at=outcome.detail.get("expires_at"),
        )
        if gate_id is None:
            # Retry do mesmo passo: o portão já existe e continua valendo.
            existing = repo.get_open_gate_for_node(
                company_id=company_id,
                execution_id=execution_id,
                node_id=node.id,
                occurrence=occurrence,
            )
            gate_id = int(existing["id"]) if existing else None
        else:
            repo.append_step(
                company_id=company_id,
                execution_id=execution_id,
                node_id=node.id,
                node_type=node.node_type,
                status=STEP_AWAITING_HUMAN,
                attempt=attempt,
                detail=outcome.detail,
            )
            repo.update_execution(
                company_id=company_id,
                execution_id=execution_id,
                context=context,
                increment_steps=1,
            )

        repo.update_execution(
            company_id=company_id,
            execution_id=execution_id,
            status=EXECUTION_AWAITING_HUMAN,
            current_node_id=node.id,
        )
        return StepReport(
            execution_id=execution_id,
            status=EXECUTION_AWAITING_HUMAN,
            node_id=node.id,
            node_type=node.node_type,
            outcome=outcome.outcome,
            gate_id=gate_id,
        )

    # --- efeito externo ---
    effect_reused = False
    if outcome.outcome == OUTCOME_EFFECT and outcome.effect is not None:
        occurrence = SINGLE_PASS_OCCURRENCE
        key = effect_key(
            company_id=company_id,
            workflow_id=int(execution["workflow_id"]),
            execution_id=execution_id,
            node_id=node.id,
            occurrence=occurrence,
        )
        reservation = repo.reserve_effect(
            company_id=company_id,
            execution_id=execution_id,
            effect_key=key,
            effect_kind=outcome.effect.kind,
            node_id=node.id,
            occurrence=occurrence,
            request=outcome.effect.payload,
        )
        if not reservation["reserved"]:
            # O efeito já aconteceu (ou seu resultado é indeterminado): o
            # adapter não é chamado de novo.
            effect_reused = True
            reservation_outcome = str(reservation.get("outcome") or "already_succeeded")
            log_event(
                logger,
                logging.WARNING if reservation_outcome == "indeterminate" else logging.INFO,
                "automation.effect.not_reapplied",
                company_id=company_id,
                execution_id=execution_id,
                effect_kind=outcome.effect.kind,
                node_id=node.id,
                reservation_outcome=reservation_outcome,
            )
        else:
            effect_id = int(reservation["effect"]["id"])
            try:
                # Fora de qualquer transação: o adapter pode falhar sem
                # deixar a transação aberta, e a reserva já garante que uma
                # segunda tentativa não duplica.
                result = apply_effect(
                    kind=outcome.effect.kind,
                    company_id=company_id,
                    execution_id=execution_id,
                    payload=outcome.effect.payload,
                )
            except PermanentExecutionError as exc:
                repo.complete_effect(
                    company_id=company_id,
                    effect_id=effect_id,
                    status=EFFECT_FAILED,
                    failure_reason=str(exc),
                )
                repo.append_step(
                    company_id=company_id,
                    execution_id=execution_id,
                    node_id=node.id,
                    node_type=node.node_type,
                    status=STEP_FAILED,
                    attempt=attempt,
                    failure_reason=str(exc),
                    detail=outcome.detail,
                )
                _finish_execution(
                    company_id=company_id,
                    execution_id=execution_id,
                    status=EXECUTION_FAILED,
                    failure_reason=str(exc),
                )
                return StepReport(
                    execution_id=execution_id,
                    status=EXECUTION_FAILED,
                    node_id=node.id,
                    node_type=node.node_type,
                    outcome=outcome.outcome,
                )
            except RetryableExecutionError as exc:
                repo.complete_effect(
                    company_id=company_id,
                    effect_id=effect_id,
                    status=EFFECT_FAILED,
                    failure_reason=str(exc),
                )
                raise
            repo.complete_effect(
                company_id=company_id,
                effect_id=effect_id,
                status=EFFECT_SUCCEEDED if result.succeeded else EFFECT_FAILED,
                result=result.detail,
                failure_reason=result.failure_reason,
            )

    repo.append_step(
        company_id=company_id,
        execution_id=execution_id,
        node_id=node.id,
        node_type=node.node_type,
        status=STEP_WAITING if outcome.outcome == OUTCOME_WAIT else STEP_SUCCEEDED,
        attempt=attempt,
        chosen_handle=outcome.chosen_handle,
        detail={**outcome.detail, "effect_reused": effect_reused} if effect_reused else outcome.detail,
    )
    repo.update_execution(
        company_id=company_id,
        execution_id=execution_id,
        context=context,
        increment_steps=1,
    )

    next_node_id = outcome.next_node_id
    if next_node_id is None:
        # Alça sem conexão. A validação impede publicar assim, mas uma versão
        # antiga pode ter passado por regra anterior: encerra sem inventar
        # caminho.
        _finish_execution(
            company_id=company_id,
            execution_id=execution_id,
            status=EXECUTION_SUCCEEDED,
            failure_reason=None,
        )
        return StepReport(
            execution_id=execution_id,
            status=EXECUTION_SUCCEEDED,
            node_id=node.id,
            node_type=node.node_type,
            outcome=outcome.outcome,
            chosen_handle=outcome.chosen_handle,
            effect_reused=effect_reused,
        )

    if outcome.outcome == OUTCOME_WAIT:
        resume_at = outcome.resume_at or datetime.now(UTC)
        job_id = _schedule_next(
            company_id=company_id,
            execution_id=execution_id,
            node_id=next_node_id,
            job_kind=JOB_KIND_RESUME_WAIT,
            run_at=_iso(resume_at),
        )
        repo.update_execution(
            company_id=company_id,
            execution_id=execution_id,
            status=EXECUTION_WAITING,
            current_node_id=next_node_id,
        )
        return StepReport(
            execution_id=execution_id,
            status=EXECUTION_WAITING,
            node_id=node.id,
            node_type=node.node_type,
            outcome=outcome.outcome,
            chosen_handle=outcome.chosen_handle,
            scheduled_job_id=job_id,
            detail=outcome.detail,
        )

    job_id = _schedule_next(
        company_id=company_id,
        execution_id=execution_id,
        node_id=next_node_id,
    )
    repo.update_execution(
        company_id=company_id,
        execution_id=execution_id,
        status=EXECUTION_RUNNING,
        current_node_id=next_node_id,
    )
    return StepReport(
        execution_id=execution_id,
        status=EXECUTION_RUNNING,
        node_id=node.id,
        node_type=node.node_type,
        outcome=outcome.outcome,
        chosen_handle=outcome.chosen_handle,
        scheduled_job_id=job_id,
        effect_reused=effect_reused,
        detail=outcome.detail,
    )


def process_job(*, job: dict[str, Any]) -> StepReport:
    """Executa um passo e resolve o destino do job.

    Distingue falha permanente de transitória: a primeira mata a execução, a
    segunda reagenda com backoff até `max_attempts` e depois vai para
    dead-letter — onde fica consultável e com retry manual.
    """
    company_id = int(job["company_id"])
    job_id = int(job["id"])
    attempts = int(job.get("attempts") or 0)
    max_attempts = int(job.get("max_attempts") or settings.automation_max_attempts)

    try:
        report = execute_step(job=job)
    except PermanentExecutionError as exc:
        repo.finish_job(company_id=company_id, job_id=job_id, status=JOB_CANCELED, error=str(exc))
        _finish_execution(
            company_id=company_id,
            execution_id=int(job["execution_id"]),
            status=EXECUTION_FAILED,
            failure_reason=str(exc),
        )
        log_event(
            logger,
            logging.ERROR,
            "automation.job.permanent_failure",
            company_id=company_id,
            job_id=job_id,
            execution_id=int(job["execution_id"]),
        )
        return StepReport(execution_id=int(job["execution_id"]), status=EXECUTION_FAILED)
    except Exception as exc:
        next_attempt = attempts + 1
        if next_attempt >= max_attempts:
            repo.finish_job(
                company_id=company_id,
                job_id=job_id,
                status=JOB_DEAD_LETTER,
                error=str(exc)[:500],
            )
            _finish_execution(
                company_id=company_id,
                execution_id=int(job["execution_id"]),
                status=EXECUTION_DEAD_LETTER,
                failure_reason=str(exc)[:500],
            )
            log_event(
                logger,
                logging.ERROR,
                "automation.job.dead_letter",
                company_id=company_id,
                job_id=job_id,
                execution_id=int(job["execution_id"]),
                attempts=next_attempt,
            )
            return StepReport(
                execution_id=int(job["execution_id"]),
                status=EXECUTION_DEAD_LETTER,
            )

        retry_at = _iso(datetime.now(UTC) + timedelta(seconds=backoff_seconds(next_attempt)))
        repo.finish_job(
            company_id=company_id,
            job_id=job_id,
            status=JOB_QUEUED,
            error=str(exc)[:500],
            run_at=retry_at,
        )
        log_event(
            logger,
            logging.WARNING,
            "automation.job.retry_scheduled",
            company_id=company_id,
            job_id=job_id,
            execution_id=int(job["execution_id"]),
            attempts=next_attempt,
            retry_at=retry_at,
        )
        return StepReport(execution_id=int(job["execution_id"]), status="retry_scheduled")

    repo.finish_job(company_id=company_id, job_id=job_id, status=JOB_SUCCEEDED)
    return report
