/**
 * Painel inferior: validação, simulação e diff.
 *
 * Fica fechado por padrão e abre sozinho quando aparece um **erro** — não
 * quando aparece qualquer resultado. Um painel que abre a cada tecla digitada
 * rouba a tela do canvas, que é o conteúdo.
 */

import type { DiffResult, SimulationResponse, ValidationIssue, ValidationResult } from "@/api/types"

export type DrawerTab = "validation" | "simulation" | "diff"

interface DrawerProps {
  readonly open: boolean
  readonly tab: DrawerTab
  readonly validation: ValidationResult | null
  readonly simulation: SimulationResponse | null
  readonly diff: DiffResult | null
  readonly busy: boolean
  readonly onTab: (tab: DrawerTab) => void
  readonly onToggle: () => void
  readonly onFocusNode: (nodeId: string) => void
}

export function Drawer({
  open,
  tab,
  validation,
  simulation,
  diff,
  busy,
  onTab,
  onToggle,
  onFocusNode,
}: DrawerProps): JSX.Element {
  const errorCount = validation?.error_count ?? 0
  const warningCount = validation?.warning_count ?? 0

  return (
    <section className="drawer" aria-label="Resultados de validação e simulação">
      <div className="drawer__tabs" role="tablist">
        <button
          type="button"
          role="tab"
          className="drawer__tab"
          aria-selected={open && tab === "validation"}
          onClick={() => onTab("validation")}
        >
          Validação
          {validation !== null && (
            <span
              className={`drawer__count ${
                errorCount > 0
                  ? "drawer__count--error"
                  : warningCount > 0
                    ? "drawer__count--warn"
                    : "drawer__count--ok"
              }`}
            >
              {errorCount > 0 ? errorCount : warningCount > 0 ? warningCount : "✓"}
            </span>
          )}
        </button>

        <button
          type="button"
          role="tab"
          className="drawer__tab"
          aria-selected={open && tab === "simulation"}
          onClick={() => onTab("simulation")}
        >
          Simulação
          {simulation !== null && (
            <span className="drawer__count">{simulation.simulation.path.length}</span>
          )}
        </button>

        <button
          type="button"
          role="tab"
          className="drawer__tab"
          aria-selected={open && tab === "diff"}
          onClick={() => onTab("diff")}
        >
          Diferenças
          {diff !== null && !diff.identical && <span className="drawer__count">•</span>}
        </button>

        <span style={{ flex: 1 }} />
        {busy && <span className="spinner" aria-label="Processando" />}
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onToggle}
          aria-expanded={open}
        >
          {open ? "Recolher" : "Expandir"}
        </button>
      </div>

      {open && (
        <div className="drawer__body" role="tabpanel">
          {tab === "validation" && <ValidationPanel validation={validation} onFocusNode={onFocusNode} />}
          {tab === "simulation" && <SimulationPanel response={simulation} onFocusNode={onFocusNode} />}
          {tab === "diff" && <DiffPanel diff={diff} />}
        </div>
      )}
    </section>
  )
}

function ValidationPanel({
  validation,
  onFocusNode,
}: {
  readonly validation: ValidationResult | null
  readonly onFocusNode: (nodeId: string) => void
}): JSX.Element {
  if (validation === null) {
    return <p className="state__text">Valide o fluxo para ver os problemas encontrados.</p>
  }
  if (validation.issues.length === 0) {
    return (
      <p className="state__text">
        Nenhum problema. O fluxo pode ser publicado
        {validation.trigger_type !== null && (
          <>
            {" "}
            e dispara em <span className="mono">{validation.trigger_type}</span>
          </>
        )}
        .
      </p>
    )
  }

  // Erros primeiro: eles bloqueiam a publicação, avisos não.
  const ordered = [...validation.issues].sort((left, right) =>
    left.severity === right.severity ? 0 : left.severity === "error" ? -1 : 1,
  )

  return (
    <div>
      {ordered.map((issue, index) => (
        <IssueRow
          key={`${issue.code}-${issue.node_id ?? issue.edge_id ?? index}`}
          issue={issue}
          onFocusNode={onFocusNode}
        />
      ))}
    </div>
  )
}

function IssueRow({
  issue,
  onFocusNode,
}: {
  readonly issue: ValidationIssue
  readonly onFocusNode: (nodeId: string) => void
}): JSX.Element {
  const where = issue.node_id ?? issue.edge_id
  return (
    <button
      type="button"
      className={`issue issue--${issue.severity}`}
      onClick={() => issue.node_id !== null && onFocusNode(issue.node_id)}
      disabled={issue.node_id === null}
    >
      <span className="issue__mark" aria-hidden="true" />
      <span>
        <span className="issue__message">{issue.message}</span>
        <span className="issue__where">
          {issue.code}
          {where !== null && ` · ${where}`}
          {issue.field_key !== null && ` · ${issue.field_key}`}
        </span>
      </span>
    </button>
  )
}

function SimulationPanel({
  response,
  onFocusNode,
}: {
  readonly response: SimulationResponse | null
  readonly onFocusNode: (nodeId: string) => void
}): JSX.Element {
  if (response === null) {
    return (
      <p className="state__text">
        Simule para ver o caminho que um evento fictício percorreria. Nada é executado.
      </p>
    )
  }

  const { simulation } = response

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <span className={`badge badge--${simulation.status === "succeeded" ? "active" : "paused"}`}>
          {simulation.status}
        </span>
        {simulation.stopped_reason !== null && (
          <span className="mono">{simulation.stopped_reason}</span>
        )}
        <span style={{ flex: 1 }} />
        <span className="badge badge--info">Nenhum efeito real</span>
      </div>

      <div className="trace">
        {simulation.steps.map((step, index) => (
          <span key={step.sequence} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <button
              type="button"
              className="trace__step"
              onClick={() => onFocusNode(step.node_id)}
              title={step.node_type}
            >
              {step.node_id}
              {step.chosen_handle !== null && step.chosen_handle !== "next" && (
                <span className="trace__handle">{step.chosen_handle}</span>
              )}
            </button>
            {index < simulation.steps.length - 1 && (
              <span className="trace__arrow" aria-hidden="true">
                →
              </span>
            )}
          </span>
        ))}
      </div>

      {simulation.effects.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <p className="palette__group-label">Efeitos que teriam sido produzidos</p>
          {simulation.effects.map((effect, index) => (
            <p key={`${effect.kind}-${index}`} className="issue__where" style={{ padding: "2px 0" }}>
              {effect.kind} · {effect.node_id}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

function DiffPanel({ diff }: { readonly diff: DiffResult | null }): JSX.Element {
  if (diff === null) {
    return <p className="state__text">Compare o rascunho com a versão publicada.</p>
  }
  if (!diff.has_published_version) {
    return <p className="state__text">Este fluxo ainda não tem uma versão publicada.</p>
  }
  if (diff.identical) {
    return (
      <p className="state__text">
        O rascunho é idêntico à versão {diff.published_version} em comportamento. Mudanças de
        posição no canvas não contam.
      </p>
    )
  }

  const groups: readonly { readonly label: string; readonly items: readonly string[] }[] = [
    { label: "Blocos adicionados", items: diff.nodes_added },
    { label: "Blocos removidos", items: diff.nodes_removed },
    { label: "Blocos alterados", items: diff.nodes_changed },
    { label: "Conexões adicionadas", items: diff.edges_added },
    { label: "Conexões removidas", items: diff.edges_removed },
  ]

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {groups
        .filter((group) => group.items.length > 0)
        .map((group) => (
          <div key={group.label}>
            <p className="palette__group-label">{group.label}</p>
            <div className="trace">
              {group.items.map((item) => (
                <span key={item} className="trace__step">
                  {item}
                </span>
              ))}
            </div>
          </div>
        ))}
    </div>
  )
}
