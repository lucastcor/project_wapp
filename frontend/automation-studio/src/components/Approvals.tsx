/**
 * Fila de aprovações humanas.
 *
 * Reservar antes de decidir é explícito na tela porque é explícito no
 * backend: o claim é atômico e evita duas pessoas decidindo o mesmo portão.
 * Quando outro operador chega primeiro, a resposta é 409 e a fila é
 * recarregada — em vez de a UI fingir que deu certo.
 */

import { useCallback, useEffect, useState } from "react"

import { api } from "@/api/client"
import type { ApprovalGate } from "@/api/types"
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  formatDateTime,
  type Toast,
} from "./primitives"

interface ApprovalsProps {
  readonly companyId: number | null
  readonly canDecide: boolean
  readonly onToast: (toast: Omit<Toast, "id">) => void
}

export function Approvals({ companyId, canDecide, onToast }: ApprovalsProps): JSX.Element {
  const [gates, setGates] = useState<readonly ApprovalGate[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<{
    readonly gate: ApprovalGate
    readonly decision: "approved" | "rejected"
  } | null>(null)
  const [note, setNote] = useState("")

  const load = useCallback(async (): Promise<void> => {
    setError(null)
    try {
      setGates(await api.listApprovals(companyId))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível carregar a fila.")
    }
  }, [companyId])

  useEffect(() => {
    void load()
  }, [load])

  async function decide(): Promise<void> {
    if (pending === null) return
    setBusy(true)
    try {
      // Reserva primeiro: se outro operador já pegou, o 409 acontece aqui e
      // nada é decidido.
      await api.claimApproval(companyId, pending.gate.id)
      const result = await api.decideApproval(
        companyId,
        pending.gate.id,
        pending.decision,
        note.trim() || undefined,
      )

      if (!result.decided) {
        onToast({ tone: "info", message: "Este portão já tinha sido decidido." })
      } else if (result.resumed === false) {
        onToast({
          tone: "info",
          message:
            result.reason === "workflow_paused"
              ? "Decisão registrada. O fluxo está pausado e só retoma quando for reativado."
              : "Decisão registrada, mas a execução não pôde retomar.",
        })
      } else {
        onToast({
          tone: "success",
          message: pending.decision === "approved" ? "Aprovado." : "Rejeitado.",
        })
      }

      setPending(null)
      setNote("")
      await load()
    } catch (caught) {
      onToast({
        tone: "error",
        message: caught instanceof Error ? caught.message : "Não foi possível decidir.",
      })
      setPending(null)
      await load()
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

  if (gates === null) return <LoadingState label="Carregando aprovações…" />

  return (
    <div className="page">
      <div className="page__head">
        <div>
          <h1 className="page__title">Aprovações</h1>
          <p className="page__subtitle">
            Execuções paradas esperando uma decisão humana. A decisão fica auditada com sua
            identidade.
          </p>
        </div>
        <span className="page__spacer" />
        <button type="button" className="btn" onClick={() => void load()}>
          Atualizar
        </button>
      </div>

      {gates.length === 0 ? (
        <EmptyState
          title="Nada aguardando decisão"
          text="Quando um fluxo chegar a um bloco de aprovação, ele aparece aqui."
        />
      ) : (
        <div className="card-grid">
          {gates.map((gate) => (
            <article key={gate.id} className="wf-card" style={{ cursor: "default" }}>
              <div className="wf-card__head">
                <div style={{ flex: 1, minWidth: 0 }}>
                  <p className="wf-card__name">{gate.title}</p>
                  <p className="wf-card__key">
                    {gate.workflow_name ?? `fluxo ${gate.workflow_id ?? "?"}`} · execução{" "}
                    {gate.execution_id}
                  </p>
                </div>
                {gate.expires_at !== null && (
                  <span className="badge badge--paused">expira {formatDateTime(gate.expires_at)}</span>
                )}
              </div>

              {gate.summary !== null && gate.summary.length > 0 && (
                <p className="wf-card__desc">{gate.summary}</p>
              )}

              <div className="wf-card__foot">
                <span>{formatDateTime(gate.created_at)}</span>
                <span style={{ flex: 1 }} />
                {canDecide ? (
                  <>
                    <button
                      type="button"
                      className="btn btn--sm btn--danger"
                      disabled={busy}
                      onClick={() => setPending({ gate, decision: "rejected" })}
                    >
                      Rejeitar
                    </button>
                    <button
                      type="button"
                      className="btn btn--sm btn--primary"
                      disabled={busy}
                      onClick={() => setPending({ gate, decision: "approved" })}
                    >
                      Aprovar
                    </button>
                  </>
                ) : (
                  <span className="mono">somente leitura</span>
                )}
              </div>
            </article>
          ))}
        </div>
      )}

      {pending !== null && (
        <Modal
          title={pending.decision === "approved" ? "Aprovar e seguir" : "Rejeitar"}
          text={
            pending.decision === "approved"
              ? "A execução retoma pelo caminho de aprovação. Ações externas passam pelo registro de efeitos e não são repetidas."
              : "A execução segue pelo caminho de rejeição do fluxo."
          }
          confirmLabel={pending.decision === "approved" ? "Aprovar" : "Rejeitar"}
          tone={pending.decision === "approved" ? "primary" : "danger"}
          confirmDisabled={busy}
          onConfirm={decide}
          onCancel={() => {
            setPending(null)
            setNote("")
          }}
        >
          <label className="field__label" htmlFor="gate-note">
            Observação (opcional)
          </label>
          <textarea
            id="gate-note"
            className="textarea"
            value={note}
            maxLength={600}
            placeholder="Por que você tomou esta decisão?"
            onChange={(event) => setNote(event.target.value)}
          />
        </Modal>
      )}
    </div>
  )
}
