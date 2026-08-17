/** Peças visuais reutilizadas. Sem estado de domínio, sem chamadas de rede. */

import { useEffect, type ReactNode } from "react"

import type { DeploymentState, ExecutionStatus, NodeCategory } from "@/api/types"

export const CATEGORY_LABEL: Record<NodeCategory, string> = {
  trigger: "Gatilho",
  condition: "Condição",
  ai: "Inteligência",
  time: "Tempo",
  control: "Controle",
  action: "Ação",
  terminal: "Encerramento",
}

/** Ordem da paleta: a do raciocínio de quem monta o fluxo, não a alfabética. */
export const CATEGORY_ORDER: readonly NodeCategory[] = [
  "trigger",
  "condition",
  "ai",
  "control",
  "time",
  "action",
  "terminal",
]

export function categoryColor(category: NodeCategory): string {
  return `var(--cat-${category})`
}

export function Spinner({ label }: { readonly label?: string }): JSX.Element {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <span className="spinner" aria-hidden="true" />
      <span className="sr-only">{label ?? "Carregando"}</span>
    </span>
  )
}

export function LoadingState({ label }: { readonly label: string }): JSX.Element {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p className="state__text">{label}</p>
    </div>
  )
}

export function EmptyState({
  title,
  text,
  action,
}: {
  readonly title: string
  readonly text: string
  readonly action?: ReactNode
}): JSX.Element {
  return (
    <div className="state">
      <p className="state__title">{title}</p>
      <p className="state__text">{text}</p>
      {action}
    </div>
  )
}

export function ErrorState({
  title,
  text,
  action,
}: {
  readonly title: string
  readonly text: string
  readonly action?: ReactNode
}): JSX.Element {
  return (
    <div className="state state--error" role="alert">
      <p className="state__title">{title}</p>
      <p className="state__text">{text}</p>
      {action}
    </div>
  )
}

const DEPLOYMENT_LABEL: Record<DeploymentState, string> = {
  active: "Ativo",
  paused: "Pausado",
  draft: "Rascunho",
}

export function StateBadge({ state }: { readonly state: DeploymentState }): JSX.Element {
  return (
    <span className={`badge badge--${state}`}>
      <span className="badge__dot" aria-hidden="true" />
      {DEPLOYMENT_LABEL[state]}
    </span>
  )
}

const EXECUTION_LABEL: Record<ExecutionStatus, string> = {
  queued: "Na fila",
  running: "Executando",
  waiting: "Aguardando tempo",
  awaiting_human: "Aguardando pessoa",
  succeeded: "Concluída",
  failed: "Falhou",
  canceled: "Cancelada",
  dead_letter: "Dead-letter",
}

const EXECUTION_TONE: Record<ExecutionStatus, string> = {
  queued: "draft",
  running: "info",
  waiting: "paused",
  awaiting_human: "paused",
  succeeded: "active",
  failed: "danger",
  canceled: "draft",
  dead_letter: "danger",
}

export function ExecutionBadge({ status }: { readonly status: ExecutionStatus }): JSX.Element {
  return (
    <span className={`badge badge--${EXECUTION_TONE[status] ?? "draft"}`}>
      {EXECUTION_LABEL[status] ?? status}
    </span>
  )
}

export interface Toast {
  readonly id: number
  readonly tone: "success" | "error" | "info"
  readonly message: string
}

export function ToastStack({
  toasts,
  onDismiss,
}: {
  readonly toasts: readonly Toast[]
  readonly onDismiss: (id: number) => void
}): JSX.Element | null {
  if (toasts.length === 0) return null
  return (
    <div className="toast-stack" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast--${toast.tone}`} role="status">
          <span style={{ flex: 1 }}>{toast.message}</span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => onDismiss(toast.id)}
            aria-label="Fechar aviso"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}

export function Modal({
  title,
  text,
  children,
  confirmLabel,
  onConfirm,
  onCancel,
  confirmDisabled,
  tone = "primary",
}: {
  readonly title: string
  readonly text?: string
  readonly children?: ReactNode
  readonly confirmLabel: string
  readonly onConfirm: () => void
  readonly onCancel: () => void
  readonly confirmDisabled?: boolean
  readonly tone?: "primary" | "danger"
}): JSX.Element {
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") onCancel()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onCancel])

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <p className="modal__title">{title}</p>
        {text !== undefined && <p className="modal__text">{text}</p>}
        {children}
        <div className="modal__actions">
          <button type="button" className="btn btn--ghost" onClick={onCancel}>
            Cancelar
          </button>
          <button
            type="button"
            className={tone === "danger" ? "btn btn--danger" : "btn btn--primary"}
            onClick={onConfirm}
            disabled={confirmDisabled ?? false}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

export function formatDateTime(value: string | null): string {
  if (!value) return "—"
  const parsed = new Date(value.includes("T") ? value : value.replace(" ", "T") + "Z")
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}
