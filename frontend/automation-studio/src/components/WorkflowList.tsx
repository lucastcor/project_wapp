/** Lista de fluxos: busca, estado e criação. */

import { useCallback, useEffect, useMemo, useState } from "react"

import { api } from "@/api/client"
import type { WorkflowSummary } from "@/api/types"
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  StateBadge,
  formatDateTime,
  type Toast,
} from "./primitives"

const KEY_PATTERN = /^[a-z0-9][a-z0-9-]{1,63}$/

function suggestKey(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
}

interface WorkflowListProps {
  readonly companyId: number | null
  readonly canCreate: boolean
  readonly onOpen: (workflowId: number) => void
  readonly onToast: (toast: Omit<Toast, "id">) => void
}

export function WorkflowList({
  companyId,
  canCreate,
  onOpen,
  onToast,
}: WorkflowListProps): JSX.Element {
  const [workflows, setWorkflows] = useState<readonly WorkflowSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [includeArchived, setIncludeArchived] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState("")
  const [key, setKey] = useState("")
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (): Promise<void> => {
    setError(null)
    try {
      setWorkflows(await api.listWorkflows(companyId, { includeArchived }))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível listar os fluxos.")
    }
  }, [companyId, includeArchived])

  useEffect(() => {
    void load()
  }, [load])

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (term.length === 0) return workflows ?? []
    return (workflows ?? []).filter(
      (workflow) =>
        workflow.name.toLowerCase().includes(term) ||
        workflow.workflow_key.toLowerCase().includes(term),
    )
  }, [workflows, search])

  const keyIsValid = KEY_PATTERN.test(key)

  async function handleCreate(): Promise<void> {
    setBusy(true)
    try {
      const created = await api.createWorkflow(companyId, {
        workflow_key: key,
        name: name.trim(),
      })
      setCreateOpen(false)
      setName("")
      setKey("")
      onToast({ tone: "success", message: "Fluxo criado. Comece adicionando o gatilho." })
      onOpen(created.id)
    } catch (caught) {
      onToast({
        tone: "error",
        message: caught instanceof Error ? caught.message : "Não foi possível criar o fluxo.",
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

  if (workflows === null) {
    return <LoadingState label="Carregando fluxos…" />
  }

  return (
    <div className="page">
      <div className="page__head">
        <div>
          <h1 className="page__title">Automações</h1>
          <p className="page__subtitle">
            Fluxos visuais desta empresa. Publicar cria uma versão pausada; ativar é uma ação
            separada.
          </p>
        </div>
        <span className="page__spacer" />
        {canCreate && (
          <button type="button" className="btn btn--primary" onClick={() => setCreateOpen(true)}>
            Novo fluxo
          </button>
        )}
      </div>

      <div className="toolbar">
        <input
          className="palette__search"
          style={{ maxWidth: 300, height: 32 }}
          type="search"
          value={search}
          placeholder="Buscar por nome ou chave…"
          aria-label="Buscar fluxo"
          onChange={(event) => setSearch(event.target.value)}
        />
        <label className="checkbox">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(event) => setIncludeArchived(event.target.checked)}
          />
          <span>Mostrar arquivados</span>
        </label>
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          title={search.length > 0 ? "Nenhum fluxo encontrado" : "Nenhum fluxo ainda"}
          text={
            search.length > 0
              ? "Ajuste a busca para encontrar o fluxo."
              : "Crie o primeiro fluxo para automatizar o que acontece depois de uma mensagem processada."
          }
          action={
            canCreate && search.length === 0 ? (
              <button type="button" className="btn btn--primary" onClick={() => setCreateOpen(true)}>
                Criar fluxo
              </button>
            ) : undefined
          }
        />
      ) : (
        <div className="card-grid">
          {filtered.map((workflow) => (
            <button
              key={workflow.id}
              type="button"
              className="wf-card"
              onClick={() => onOpen(workflow.id)}
            >
              <div className="wf-card__head">
                <div style={{ flex: 1, minWidth: 0 }}>
                  <p className="wf-card__name">{workflow.name}</p>
                  <p className="wf-card__key">{workflow.workflow_key}</p>
                </div>
                <StateBadge state={workflow.state} />
              </div>

              {workflow.description !== null && workflow.description.length > 0 && (
                <p className="wf-card__desc">{workflow.description}</p>
              )}

              <div className="wf-card__foot">
                <span>{workflow.trigger_type ?? "sem gatilho publicado"}</span>
                <span style={{ flex: 1 }} />
                <span>{formatDateTime(workflow.updated_at)}</span>
              </div>
            </button>
          ))}
        </div>
      )}

      {createOpen && (
        <Modal
          title="Novo fluxo"
          text="A chave identifica o fluxo dentro da empresa e não muda depois."
          confirmLabel="Criar"
          onConfirm={handleCreate}
          onCancel={() => setCreateOpen(false)}
          confirmDisabled={busy || name.trim().length === 0 || !keyIsValid}
        >
          <div className="field">
            <label className="field__label" htmlFor="wf-name">
              Nome<span className="field__required">*</span>
            </label>
            <input
              id="wf-name"
              className="input"
              value={name}
              maxLength={120}
              onChange={(event) => {
                setName(event.target.value)
                if (key.length === 0 || key === suggestKey(name)) {
                  setKey(suggestKey(event.target.value))
                }
              }}
            />
          </div>

          <div className="field">
            <label className="field__label" htmlFor="wf-key">
              Chave<span className="field__required">*</span>
            </label>
            <input
              id="wf-key"
              className="input"
              value={key}
              maxLength={64}
              aria-invalid={key.length > 0 && !keyIsValid}
              onChange={(event) => setKey(event.target.value.toLowerCase())}
            />
            {key.length > 0 && !keyIsValid && (
              <p className="field__error">
                Use apenas letras minúsculas, números e hífen, de 2 a 64 caracteres.
              </p>
            )}
          </div>
        </Modal>
      )}
    </div>
  )
}
