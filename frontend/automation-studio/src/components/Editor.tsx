/**
 * Editor de um fluxo: barra de estado, paleta, canvas, inspector e painel.
 *
 * Decisões de UX que sustentam a tela:
 *
 * - **um lugar só para estado.** Publicado/ativo, rascunho salvo e conflito
 *   aparecem juntos na barra superior. O usuário nunca precisa procurar.
 * - **salvar é explícito.** Autosave silencioso com concorrência otimista
 *   produziria 409 sem que ninguém tivesse pedido nada; aqui o botão diz
 *   quando há o que salvar.
 * - **publicar e ativar são dois botões.** Espelha o backend: publicar cria
 *   uma versão pausada; ativar é uma decisão separada.
 */

import { useCallback, useEffect, useMemo, useState } from "react"

import { api, DraftConflictError, GraphRejectedError } from "@/api/client"
import type {
  CatalogResponse,
  ConfigValue,
  DiffResult,
  NodeSpec,
  SimulationResponse,
  ValidationResult,
  WorkflowDetail,
  WorkflowGraph,
} from "@/api/types"
import {
  addNode,
  behaviourSignature,
  canRedo,
  canUndo,
  connect,
  disconnect,
  initialHistory,
  moveNode,
  pushHistory,
  redo,
  removeNode,
  undo,
  updateNodeConfig,
  type EditorHistory,
} from "@/state/graph"
import { Canvas } from "./Canvas"
import { Drawer, type DrawerTab } from "./Drawer"
import { Inspector } from "./Inspector"
import { Palette } from "./Palette"
import { LoadingState, Modal, StateBadge, type Toast } from "./primitives"

const SAMPLE_CONTEXT: Record<string, unknown> = {
  classification: "orcamento",
  confidence: 0.91,
  pipeline_stage: "qualificacao",
  customer_profile: "new_customer",
  handoff_required: false,
  reply_source: "llm",
  has_relevant_source: true,
  auto_reply_sent: true,
  contact_id: "contato-de-exemplo",
}

interface EditorProps {
  readonly companyId: number | null
  readonly workflowId: number
  readonly catalog: CatalogResponse
  readonly capabilities: readonly string[]
  readonly canEdit: boolean
  readonly onBack: () => void
  readonly onToast: (toast: Omit<Toast, "id">) => void
}

export function Editor({
  companyId,
  workflowId,
  catalog,
  capabilities,
  canEdit,
  onBack,
  onToast,
}: EditorProps): JSX.Element {
  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null)
  const [history, setHistory] = useState<EditorHistory | null>(null)
  const [savedSignature, setSavedSignature] = useState("")
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [paletteCollapsed, setPaletteCollapsed] = useState(false)

  const [validation, setValidation] = useState<ValidationResult | null>(null)
  const [simulation, setSimulation] = useState<SimulationResponse | null>(null)
  const [diff, setDiff] = useState<DiffResult | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("validation")

  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState<{ readonly current: number } | null>(null)
  const [publishOpen, setPublishOpen] = useState(false)
  const [publishNotes, setPublishNotes] = useState("")
  const [loadError, setLoadError] = useState<string | null>(null)

  const specs = useMemo(
    () => new Map<string, NodeSpec>(catalog.nodes.map((spec) => [spec.node_type, spec])),
    [catalog.nodes],
  )

  const load = useCallback(async (): Promise<void> => {
    try {
      const detail = await api.getWorkflow(companyId, workflowId)
      const graph = detail.draft?.graph ?? { schema_version: catalog.schema_version, nodes: [], edges: [] }
      setWorkflow(detail)
      setHistory(initialHistory(graph))
      setSavedSignature(behaviourSignature(graph))
      setConflict(null)
      setLoadError(null)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Não foi possível carregar o fluxo.")
    }
  }, [companyId, workflowId, catalog.schema_version])

  useEffect(() => {
    void load()
  }, [load])

  const graph = history?.present ?? null
  const dirty = graph !== null && behaviourSignature(graph) !== savedSignature

  const mutate = useCallback(
    (next: (current: WorkflowGraph) => WorkflowGraph): void => {
      setHistory((current) => (current === null ? current : pushHistory(current, next(current.present))))
    },
    [],
  )

  // Atalhos do editor. Sem eles, undo/redo só existiria em botão, e edição de
  // grafo é a operação em que errar e voltar é mais comum.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      const target = event.target as HTMLElement | null
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return
      const meta = event.metaKey || event.ctrlKey
      if (!meta) return

      if (event.key.toLowerCase() === "z" && !event.shiftKey) {
        event.preventDefault()
        setHistory((current) => (current === null ? current : undo(current)))
      } else if ((event.key.toLowerCase() === "z" && event.shiftKey) || event.key.toLowerCase() === "y") {
        event.preventDefault()
        setHistory((current) => (current === null ? current : redo(current)))
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  async function handleSave(): Promise<void> {
    if (workflow?.draft == null || graph === null) return
    setBusy(true)
    try {
      const updated = await api.saveDraft(companyId, workflowId, graph, workflow.draft.revision)
      setWorkflow(updated)
      setSavedSignature(behaviourSignature(graph))
      setConflict(null)
      onToast({ tone: "success", message: "Rascunho salvo." })
    } catch (error) {
      if (error instanceof DraftConflictError) {
        setConflict({ current: error.currentRevision })
        onToast({ tone: "error", message: error.message })
      } else {
        onToast({
          tone: "error",
          message: error instanceof Error ? error.message : "Não foi possível salvar.",
        })
      }
    } finally {
      setBusy(false)
    }
  }

  async function handleValidate(): Promise<void> {
    if (graph === null) return
    setBusy(true)
    try {
      const result = await api.validateGraph(companyId, graph)
      setValidation(result)
      setDrawerTab("validation")
      // Abre sozinho só quando há erro: aviso não interrompe o trabalho.
      setDrawerOpen(result.error_count > 0 || drawerOpen)
      if (result.is_valid) onToast({ tone: "success", message: "Fluxo válido." })
    } catch (error) {
      onToast({ tone: "error", message: error instanceof Error ? error.message : "Falha ao validar." })
    } finally {
      setBusy(false)
    }
  }

  async function handleSimulate(): Promise<void> {
    if (graph === null) return
    setBusy(true)
    try {
      const decisions: Record<string, "approved" | "rejected"> = {}
      for (const node of graph.nodes) {
        if (node.node_type === "control.humanApproval") decisions[node.id] = "approved"
      }
      const response = await api.simulateGraph(companyId, graph, SAMPLE_CONTEXT, decisions)
      setSimulation(response)
      setValidation(response.validation)
      setDrawerTab("simulation")
      setDrawerOpen(true)
    } catch (error) {
      onToast({ tone: "error", message: error instanceof Error ? error.message : "Falha ao simular." })
    } finally {
      setBusy(false)
    }
  }

  async function handleDiff(): Promise<void> {
    setBusy(true)
    try {
      setDiff(await api.diffDraft(companyId, workflowId))
      setDrawerTab("diff")
      setDrawerOpen(true)
    } catch (error) {
      onToast({ tone: "error", message: error instanceof Error ? error.message : "Falha ao comparar." })
    } finally {
      setBusy(false)
    }
  }

  async function handlePublish(): Promise<void> {
    setBusy(true)
    try {
      const result = await api.publish(companyId, workflowId, publishNotes.trim() || undefined)
      setPublishOpen(false)
      setPublishNotes("")
      await load()
      onToast({
        tone: "success",
        message: `Versão ${result.version} publicada e pausada. Ative quando quiser colocá-la no ar.`,
      })
    } catch (error) {
      if (error instanceof GraphRejectedError) {
        setValidation({
          is_valid: false,
          trigger_type: null,
          error_count: error.issues.length,
          warning_count: 0,
          issues: error.issues,
        })
        setDrawerTab("validation")
        setDrawerOpen(true)
        setPublishOpen(false)
        onToast({ tone: "error", message: "O fluxo tem erros e não pode ser publicado." })
      } else {
        onToast({
          tone: "error",
          message: error instanceof Error ? error.message : "Falha ao publicar.",
        })
      }
    } finally {
      setBusy(false)
    }
  }

  async function handleToggleState(): Promise<void> {
    if (workflow?.deployment == null) return
    const next = workflow.deployment.state === "active" ? "paused" : "active"
    setBusy(true)
    try {
      const updated = await api.setState(companyId, workflowId, next)
      setWorkflow(updated)
      onToast({
        tone: "success",
        message: next === "active" ? "Fluxo ativado." : "Fluxo pausado. Execuções em espera seguem preservadas.",
      })
    } catch (error) {
      onToast({
        tone: "error",
        message: error instanceof Error ? error.message : "Falha ao mudar o estado.",
      })
    } finally {
      setBusy(false)
    }
  }

  if (loadError !== null) {
    return (
      <div className="page">
        <div className="state state--error" role="alert">
          <p className="state__title">Não foi possível abrir o fluxo</p>
          <p className="state__text">{loadError}</p>
          <button type="button" className="btn" onClick={onBack}>
            Voltar para a lista
          </button>
        </div>
      </div>
    )
  }

  if (workflow === null || graph === null || history === null) {
    return <LoadingState label="Carregando o fluxo…" />
  }

  const selectedNode = graph.nodes.find((node) => node.id === selectedNodeId) ?? null
  const hasTrigger = graph.nodes.some(
    (node) => specs.get(node.node_type)?.category === "trigger",
  )
  const deploymentState = workflow.deployment?.state ?? "draft"
  const readOnly = !canEdit

  return (
    <div className="studio">
      <header className="topbar">
        <button type="button" className="topbar__back" onClick={onBack}>
          ← Fluxos
        </button>

        <div className="topbar__identity">
          <span className="topbar__title">{workflow.name}</span>
          <span className="topbar__meta">
            <span className="mono">{workflow.workflow_key}</span>
            {workflow.deployed_version !== null && ` · v${workflow.deployed_version.version}`}
          </span>
        </div>

        <StateBadge state={deploymentState} />

        <span
          className={`save-state ${
            conflict !== null ? "save-state--conflict" : dirty ? "save-state--dirty" : "save-state--saved"
          }`}
          role="status"
        >
          {conflict !== null
            ? `Conflito · revisão ${conflict.current}`
            : dirty
              ? "Alterações não salvas"
              : `Salvo · revisão ${workflow.draft?.revision ?? 1}`}
        </span>

        <span className="topbar__spacer" />

        <div className="topbar__actions">
          <button
            type="button"
            className="btn btn--ghost btn--icon"
            onClick={() => setHistory(undo(history))}
            disabled={!canUndo(history) || readOnly}
            aria-label="Desfazer"
            title="Desfazer (⌘Z)"
          >
            ↶
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--icon"
            onClick={() => setHistory(redo(history))}
            disabled={!canRedo(history) || readOnly}
            aria-label="Refazer"
            title="Refazer (⇧⌘Z)"
          >
            ↷
          </button>

          <button type="button" className="btn" onClick={handleValidate} disabled={busy}>
            Validar
          </button>
          <button type="button" className="btn" onClick={handleSimulate} disabled={busy}>
            Simular
          </button>
          <button type="button" className="btn" onClick={handleDiff} disabled={busy}>
            Diferenças
          </button>

          {!readOnly && (
            <>
              {conflict !== null ? (
                <button type="button" className="btn btn--danger" onClick={() => void load()}>
                  Recarregar
                </button>
              ) : (
                <button
                  type="button"
                  className="btn"
                  onClick={handleSave}
                  disabled={busy || !dirty}
                >
                  Salvar
                </button>
              )}

              <button
                type="button"
                className="btn"
                onClick={() => setPublishOpen(true)}
                disabled={busy || dirty}
                title={dirty ? "Salve o rascunho antes de publicar." : "Publicar uma versão pausada"}
              >
                Publicar
              </button>

              {workflow.deployment !== null && (
                <button
                  type="button"
                  className={deploymentState === "active" ? "btn" : "btn btn--primary"}
                  onClick={handleToggleState}
                  disabled={busy}
                >
                  {deploymentState === "active" ? "Pausar" : "Ativar"}
                </button>
              )}
            </>
          )}
        </div>
      </header>

      <div className="studio__body">
        <Palette
          specs={catalog.nodes}
          enabledCapabilities={capabilities}
          hasTrigger={hasTrigger}
          collapsed={paletteCollapsed}
          onToggle={() => setPaletteCollapsed((value) => !value)}
          onAdd={(spec) => {
            if (readOnly) return
            const offset = graph.nodes.length * 28
            mutate((current) => addNode(current, spec, { x: 220 + offset, y: 120 + offset }))
          }}
        />

        <main className="studio__main">
          <Canvas
            graph={graph}
            specs={specs}
            issues={validation?.issues ?? []}
            simulatedPath={simulation?.simulation.path ?? []}
            selectedNodeId={selectedNodeId}
            readOnly={readOnly}
            onSelect={setSelectedNodeId}
            onMove={(nodeId, position) => mutate((current) => moveNode(current, nodeId, position))}
            onConnect={(source, target, handle) =>
              mutate((current) => connect(current, source, target, handle))
            }
            onDeleteEdge={(id) => mutate((current) => disconnect(current, id))}
          />

          <Drawer
            open={drawerOpen}
            tab={drawerTab}
            validation={validation}
            simulation={simulation}
            diff={diff}
            busy={busy}
            onTab={(tab) => {
              setDrawerTab(tab)
              setDrawerOpen(true)
            }}
            onToggle={() => setDrawerOpen((value) => !value)}
            onFocusNode={setSelectedNodeId}
          />
        </main>

        {selectedNode !== null && (
          <Inspector
            node={selectedNode}
            spec={specs.get(selectedNode.node_type)}
            issues={validation?.issues ?? []}
            readOnly={readOnly}
            onChange={(key, value: ConfigValue) =>
              mutate((current) => updateNodeConfig(current, selectedNode.id, key, value))
            }
            onRemove={() => {
              mutate((current) => removeNode(current, selectedNode.id))
              setSelectedNodeId(null)
            }}
            onClose={() => setSelectedNodeId(null)}
          />
        )}
      </div>

      {publishOpen && (
        <Modal
          title="Publicar versão"
          text="A versão nasce pausada. Ativar é uma ação separada, para que publicar nunca coloque um fluxo no ar por engano."
          confirmLabel="Publicar"
          onConfirm={handlePublish}
          onCancel={() => setPublishOpen(false)}
          confirmDisabled={busy}
        >
          <label className="field__label" htmlFor="publish-notes">
            Notas da versão (opcional)
          </label>
          <textarea
            id="publish-notes"
            className="textarea"
            value={publishNotes}
            maxLength={600}
            placeholder="O que mudou nesta versão?"
            onChange={(event) => setPublishNotes(event.target.value)}
          />
        </Modal>
      )}
    </div>
  )
}
