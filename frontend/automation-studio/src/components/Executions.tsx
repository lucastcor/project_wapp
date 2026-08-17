/**
 * Execuções: lista filtrável, detalhe por passo e operação (retry/cancel).
 *
 * O contexto exibido já vem redigido do servidor. O cliente não tenta
 * "desmascarar" nada — se um identificador aparece mascarado é porque a tela
 * de inspeção não precisa dele.
 */

import { useCallback, useEffect, useState } from "react"

import { api } from "@/api/client"
import type { DeadLetterEntry, ExecutionDetail, ExecutionStatus, ExecutionSummary } from "@/api/types"
import {
  EmptyState,
  ErrorState,
  ExecutionBadge,
  LoadingState,
  formatDateTime,
  type Toast,
} from "./primitives"

const STATUS_FILTERS: readonly { readonly value: ExecutionStatus | ""; readonly label: string }[] = [
  { value: "", label: "Todas" },
  { value: "awaiting_human", label: "Aguardando pessoa" },
  { value: "waiting", label: "Aguardando tempo" },
  { value: "running", label: "Executando" },
  { value: "succeeded", label: "Concluídas" },
  { value: "failed", label: "Falharam" },
  { value: "dead_letter", label: "Dead-letter" },
]

interface ExecutionsProps {
  readonly companyId: number | null
  readonly canOperate: boolean
  readonly onToast: (toast: Omit<Toast, "id">) => void
}

export function Executions({ companyId, canOperate, onToast }: ExecutionsProps): JSX.Element {
  const [executions, setExecutions] = useState<readonly ExecutionSummary[] | null>(null)
  const [deadLetter, setDeadLetter] = useState<readonly DeadLetterEntry[]>([])
  const [status, setStatus] = useState<ExecutionStatus | "">("")
  const [selected, setSelected] = useState<ExecutionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (): Promise<void> => {
    setError(null)
    try {
      const [list, dead] = await Promise.all([
        api.listExecutions(companyId, status === "" ? {} : { status }),
        api.listDeadLetter(companyId),
      ])
      setExecutions(list)
      setDeadLetter(dead)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível listar as execuções.")
    }
  }, [companyId, status])

  useEffect(() => {
    void load()
  }, [load])

  async function openDetail(executionId: number): Promise<void> {
    try {
      setSelected(await api.getExecution(companyId, executionId))
    } catch (caught) {
      onToast({
        tone: "error",
        message: caught instanceof Error ? caught.message : "Não foi possível abrir a execução.",
      })
    }
  }

  async function operate(
    executionId: number,
    action: "retry" | "cancel",
  ): Promise<void> {
    setBusy(true)
    try {
      const result =
        action === "retry"
          ? await api.retryExecution(companyId, executionId)
          : await api.cancelExecution(companyId, executionId)
      const done = "retried" in result ? result.retried : result.canceled
      onToast({
        tone: done ? "success" : "info",
        message: done
          ? action === "retry"
            ? "Execução reenfileirada. Efeitos já concluídos não serão repetidos."
            : "Execução cancelada."
          : `Nada a fazer: ${result.reason ?? "estado não permite"}.`,
      })
      await load()
      if (selected?.id === executionId) await openDetail(executionId)
    } catch (caught) {
      onToast({
        tone: "error",
        message: caught instanceof Error ? caught.message : "A operação falhou.",
      })
    } finally {
      setBusy(false)
    }
  }

  if (error !== null) {
    return (
      <div className="page">
        <ErrorState
          title="Não foi possível carregar"
          text={error}
          action={
            <button type="button" className="btn" onClick={() => void load()}>
              Tentar de novo
            </button>
          }
        />
      </div>
    )
  }

  if (executions === null) return <LoadingState label="Carregando execuções…" />

  if (selected !== null) {
    return (
      <ExecutionDetailView
        detail={selected}
        canOperate={canOperate}
        busy={busy}
        onBack={() => setSelected(null)}
        onOperate={operate}
      />
    )
  }

  return (
    <div className="page">
      <div className="page__head">
        <div>
          <h1 className="page__title">Execuções</h1>
          <p className="page__subtitle">
            Cada execução fica presa à versão com que começou. Republicar não a move.
          </p>
        </div>
        <span className="page__spacer" />
        <button type="button" className="btn" onClick={() => void load()}>
          Atualizar
        </button>
      </div>

      <div className="toolbar">
        <div className="section-nav" role="group" aria-label="Filtrar por estado">
          {STATUS_FILTERS.map((filter) => (
            <button
              key={filter.value || "all"}
              type="button"
              className="section-nav__item"
              aria-current={status === filter.value ? "page" : undefined}
              onClick={() => setStatus(filter.value)}
            >
              {filter.label}
            </button>
          ))}
        </div>
        {deadLetter.length > 0 && (
          <span className="badge badge--danger">{deadLetter.length} em dead-letter</span>
        )}
      </div>

      {executions.length === 0 ? (
        <EmptyState
          title="Nenhuma execução"
          text="Assim que um fluxo ativo receber um evento, a execução aparece aqui."
        />
      ) : (
        <div className="table__wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Execução</th>
                <th>Estado</th>
                <th>Bloco atual</th>
                <th>Passos</th>
                <th>Início</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {executions.map((execution) => (
                <tr key={execution.id}>
                  <td>
                    <span className="mono">#{execution.id}</span>
                    <span style={{ color: "var(--text-faint)", marginLeft: 8 }}>
                      wf {execution.workflow_id} · v{execution.version_id}
                    </span>
                  </td>
                  <td>
                    <ExecutionBadge status={execution.status} />
                  </td>
                  <td className="mono">{execution.current_node_id ?? "—"}</td>
                  <td>{execution.steps_executed}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(execution.started_at)}</td>
                  <td>
                    <div className="table__actions">
                      <button
                        type="button"
                        className="btn btn--sm"
                        onClick={() => void openDetail(execution.id)}
                      >
                        Detalhe
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function ExecutionDetailView({
  detail,
  canOperate,
  busy,
  onBack,
  onOperate,
}: {
  readonly detail: ExecutionDetail
  readonly canOperate: boolean
  readonly busy: boolean
  readonly onBack: () => void
  readonly onOperate: (executionId: number, action: "retry" | "cancel") => Promise<void>
}): JSX.Element {
  const retryable = detail.status === "dead_letter" || detail.status === "failed"
  const cancelable = !["succeeded", "failed", "canceled", "dead_letter"].includes(detail.status)

  return (
    <div className="page">
      <div className="page__head">
        <button type="button" className="topbar__back" onClick={onBack}>
          ← Execuções
        </button>
        <div>
          <h1 className="page__title">Execução #{detail.id}</h1>
          <p className="page__subtitle">
            Fluxo {detail.workflow_id} · versão {detail.version_id}
          </p>
        </div>
        <span className="page__spacer" />
        <ExecutionBadge status={detail.status} />
        {canOperate && retryable && (
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => void onOperate(detail.id, "retry")}
          >
            Reprocessar
          </button>
        )}
        {canOperate && cancelable && (
          <button
            type="button"
            className="btn btn--danger"
            disabled={busy}
            onClick={() => void onOperate(detail.id, "cancel")}
          >
            Cancelar
          </button>
        )}
      </div>

      <div className="detail-grid" style={{ marginBottom: 18 }}>
        <div className="detail-cell">
          <p className="detail-cell__label">Passos</p>
          <p className="detail-cell__value">{detail.steps_executed}</p>
        </div>
        <div className="detail-cell">
          <p className="detail-cell__label">Início</p>
          <p className="detail-cell__value">{formatDateTime(detail.started_at)}</p>
        </div>
        <div className="detail-cell">
          <p className="detail-cell__label">Fim</p>
          <p className="detail-cell__value">{formatDateTime(detail.finished_at)}</p>
        </div>
        {detail.failure_reason !== null && (
          <div className="detail-cell">
            <p className="detail-cell__label">Falha</p>
            <p className="detail-cell__value" style={{ color: "var(--danger)" }}>
              {detail.failure_reason}
            </p>
          </div>
        )}
      </div>

      <p className="palette__group-label">Passos</p>
      <div className="table__wrap" style={{ marginBottom: 18 }}>
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Bloco</th>
              <th>Estado</th>
              <th>Saída</th>
              <th>Tentativa</th>
              <th>Fim</th>
            </tr>
          </thead>
          <tbody>
            {detail.steps.map((step) => (
              <tr key={step.id}>
                <td>{step.sequence}</td>
                <td>
                  <span className="mono">{step.node_id}</span>
                  <span style={{ color: "var(--text-faint)", marginLeft: 8 }}>{step.node_type}</span>
                </td>
                <td>{step.status}</td>
                <td className="mono">{step.chosen_handle ?? "—"}</td>
                <td>{step.attempt}</td>
                <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(step.finished_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detail.effects.length > 0 && (
        <>
          <p className="palette__group-label">Efeitos externos</p>
          <div className="table__wrap" style={{ marginBottom: 18 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Efeito</th>
                  <th>Bloco</th>
                  <th>Estado</th>
                  <th>Conclusão</th>
                </tr>
              </thead>
              <tbody>
                {detail.effects.map((effect) => (
                  <tr key={effect.id}>
                    <td className="mono">{effect.effect_kind}</td>
                    <td className="mono">{effect.node_id}</td>
                    <td>
                      <span
                        className={`badge badge--${effect.status === "succeeded" ? "active" : "danger"}`}
                      >
                        {effect.status}
                      </span>
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(effect.completed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {detail.jobs.length > 0 && (
        <>
          <p className="palette__group-label">Jobs</p>
          <div className="table__wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Tipo</th>
                  <th>Estado</th>
                  <th>Tentativas</th>
                  <th>Executa em</th>
                  <th>Último erro</th>
                </tr>
              </thead>
              <tbody>
                {detail.jobs.map((job) => (
                  <tr key={job.id}>
                    <td className="mono">#{job.id}</td>
                    <td>{job.job_kind}</td>
                    <td>{job.status}</td>
                    <td>
                      {job.attempts}/{job.max_attempts}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(job.run_at)}</td>
                    <td style={{ color: "var(--text-faint)" }}>{job.last_error ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
