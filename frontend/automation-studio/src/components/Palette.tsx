/**
 * Paleta de blocos, agrupada por papel e filtrável.
 *
 * Um bloco cuja capacidade não está habilitada aparece **desabilitado com o
 * motivo**, em vez de sumir: esconder faria o operador procurar um recurso
 * que existe mas está desligado para a empresa dele.
 */

import { useMemo, useState, type CSSProperties } from "react"

import type { NodeSpec } from "@/api/types"
import { CATEGORY_LABEL, CATEGORY_ORDER, categoryColor } from "./primitives"

interface PaletteProps {
  readonly specs: readonly NodeSpec[]
  readonly enabledCapabilities: readonly string[]
  readonly hasTrigger: boolean
  readonly collapsed: boolean
  readonly onToggle: () => void
  readonly onAdd: (spec: NodeSpec) => void
}

function normalize(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
}

export function Palette({
  specs,
  enabledCapabilities,
  hasTrigger,
  collapsed,
  onToggle,
  onAdd,
}: PaletteProps): JSX.Element {
  const [query, setQuery] = useState("")

  const grouped = useMemo(() => {
    const term = normalize(query.trim())
    const matches = specs.filter(
      (spec) =>
        term.length === 0 ||
        normalize(spec.label).includes(term) ||
        normalize(spec.description).includes(term) ||
        normalize(spec.node_type).includes(term),
    )
    return CATEGORY_ORDER.map((category) => ({
      category,
      items: matches.filter((spec) => spec.category === category),
    })).filter((group) => group.items.length > 0)
  }, [specs, query])

  const capabilities = useMemo(() => new Set(enabledCapabilities), [enabledCapabilities])

  function blockedReason(spec: NodeSpec): string | null {
    if (spec.required_capability !== null && !capabilities.has(spec.required_capability)) {
      return `Requer a capacidade "${spec.required_capability}", desligada nesta empresa.`
    }
    // A validação exige exatamente um gatilho; impedir o segundo no editor
    // evita um erro que o operador só veria ao publicar.
    if (spec.category === "trigger" && hasTrigger) {
      return "O fluxo já tem um gatilho. Remova o atual para trocar."
    }
    return null
  }

  if (collapsed) {
    return (
      <aside className="palette" data-collapsed="true" aria-label="Blocos disponíveis">
        <div className="palette__head">
          <button
            type="button"
            className="btn btn--ghost btn--icon"
            onClick={onToggle}
            aria-label="Mostrar blocos"
            title="Mostrar blocos"
          >
            ›
          </button>
        </div>
      </aside>
    )
  }

  return (
    <aside className="palette" data-collapsed="false" aria-label="Blocos disponíveis">
      <div className="palette__head">
        <input
          className="palette__search"
          type="search"
          value={query}
          placeholder="Buscar bloco…"
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Buscar bloco"
        />
        <button
          type="button"
          className="btn btn--ghost btn--icon"
          onClick={onToggle}
          aria-label="Ocultar blocos"
          title="Ocultar blocos"
        >
          ‹
        </button>
      </div>

      <div className="palette__scroll">
        {grouped.length === 0 && (
          <p className="palette__empty">Nenhum bloco corresponde a “{query}”.</p>
        )}

        {grouped.map((group) => (
          <div className="palette__group" key={group.category}>
            <p className="palette__group-label">{CATEGORY_LABEL[group.category]}</p>
            {group.items.map((spec) => {
              const blocked = blockedReason(spec)
              return (
                <button
                  key={spec.node_type}
                  type="button"
                  className="palette__item"
                  style={{ "--cat-color": categoryColor(spec.category) } as CSSProperties}
                  onClick={() => onAdd(spec)}
                  disabled={blocked !== null}
                  title={blocked ?? spec.description}
                >
                  <span className="palette__swatch" aria-hidden="true" />
                  <span>
                    <span className="palette__item-label">{spec.label}</span>
                    <span className="palette__item-hint">{blocked ?? spec.description}</span>
                  </span>
                </button>
              )
            })}
          </div>
        ))}
      </div>
    </aside>
  )
}
