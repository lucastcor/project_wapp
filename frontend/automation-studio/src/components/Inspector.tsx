/**
 * Inspector declarativo: o formulário vem do catálogo do servidor.
 *
 * Não há formulário escrito à mão por tipo de bloco. Campo novo no backend
 * aparece aqui sozinho, com o mesmo rótulo, as mesmas opções e os mesmos
 * limites — é o que impede a UI de divergir da validação.
 *
 * Sem bloco selecionado, o painel some. Um inspector vazio ocupando um terço
 * da tela é ruído.
 */

import type { ConfigValue, FieldSpec, GraphNode, NodeSpec, ValidationIssue } from "@/api/types"
import { CATEGORY_LABEL, categoryColor } from "./primitives"

interface InspectorProps {
  readonly node: GraphNode
  readonly spec: NodeSpec | undefined
  readonly issues: readonly ValidationIssue[]
  readonly readOnly: boolean
  readonly onChange: (key: string, value: ConfigValue) => void
  readonly onRemove: () => void
  readonly onClose: () => void
}

export function Inspector({
  node,
  spec,
  issues,
  readOnly,
  onChange,
  onRemove,
  onClose,
}: InspectorProps): JSX.Element {
  const nodeIssues = issues.filter((issue) => issue.node_id === node.id)
  const generalIssues = nodeIssues.filter((issue) => issue.field_key === null)

  return (
    <aside className="inspector" aria-label={`Configuração do bloco ${node.id}`}>
      <div className="inspector__head">
        <span
          className="palette__swatch"
          style={{
            ["--cat-color" as string]: spec ? categoryColor(spec.category) : "var(--text-faint)",
            minHeight: 30,
          }}
          aria-hidden="true"
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <p className="inspector__title">{spec?.label ?? node.node_type}</p>
          <p className="inspector__subtitle">
            {spec ? CATEGORY_LABEL[spec.category] : "Tipo desconhecido"} ·{" "}
            <span className="mono">{node.id}</span>
          </p>
        </div>
        <button
          type="button"
          className="btn btn--ghost btn--icon"
          onClick={onClose}
          aria-label="Fechar configuração"
        >
          ✕
        </button>
      </div>

      <div className="inspector__scroll">
        {spec !== undefined && (
          <p style={{ fontSize: 12.5, color: "var(--text-faint)", lineHeight: 1.45 }}>
            {spec.description}
          </p>
        )}

        {generalIssues.length > 0 && (
          <div style={{ marginTop: 12 }}>
            {generalIssues.map((issue) => (
              <p
                key={`${issue.code}-${issue.message}`}
                className={issue.severity === "error" ? "field__error" : "field__help"}
                role={issue.severity === "error" ? "alert" : undefined}
              >
                {issue.message}
              </p>
            ))}
          </div>
        )}

        {spec !== undefined && spec.fields.length === 0 && (
          <p className="field__help" style={{ marginTop: 12 }}>
            Este bloco não tem configuração.
          </p>
        )}

        {spec?.fields.map((field) => (
          <Field
            key={field.key}
            field={field}
            value={node.config[field.key] ?? null}
            issue={nodeIssues.find((issue) => issue.field_key === field.key)}
            readOnly={readOnly}
            onChange={(value) => onChange(field.key, value)}
          />
        ))}

        {spec !== undefined && (
          <div style={{ marginTop: 18, display: "flex", flexWrap: "wrap", gap: 6 }}>
            {spec.produces_effect && (
              <span className="node__flag node__flag--effect">Efeito externo</span>
            )}
            {spec.suspends && <span className="node__flag node__flag--suspend">Suspende</span>}
            {spec.outputs.length > 1 && (
              <span className="node__flag">{spec.outputs.length} saídas</span>
            )}
          </div>
        )}

        {!readOnly && (
          <button
            type="button"
            className="btn btn--danger btn--sm"
            style={{ marginTop: 20, width: "100%" }}
            onClick={onRemove}
          >
            Remover bloco
          </button>
        )}
      </div>
    </aside>
  )
}

interface FieldProps {
  readonly field: FieldSpec
  readonly value: ConfigValue
  readonly issue: ValidationIssue | undefined
  readonly readOnly: boolean
  readonly onChange: (value: ConfigValue) => void
}

function Field({ field, value, issue, readOnly, onChange }: FieldProps): JSX.Element {
  const invalid = issue !== undefined && issue.severity === "error"
  const describedBy = `${field.key}-help`

  return (
    <div className="field">
      <label className="field__label" htmlFor={field.key}>
        {field.label}
        {field.required && (
          <span className="field__required" aria-label="obrigatório">
            *
          </span>
        )}
      </label>

      <FieldControl
        field={field}
        value={value}
        invalid={invalid}
        readOnly={readOnly}
        describedBy={describedBy}
        onChange={onChange}
      />

      {field.help_text.length > 0 && (
        <p className="field__help" id={describedBy}>
          {field.help_text}
        </p>
      )}
      {issue !== undefined && (
        <p className={invalid ? "field__error" : "field__help"} role={invalid ? "alert" : undefined}>
          {issue.message}
        </p>
      )}
    </div>
  )
}

function FieldControl({
  field,
  value,
  invalid,
  readOnly,
  describedBy,
  onChange,
}: {
  readonly field: FieldSpec
  readonly value: ConfigValue
  readonly invalid: boolean
  readonly readOnly: boolean
  readonly describedBy: string
  readonly onChange: (value: ConfigValue) => void
}): JSX.Element {
  const shared = {
    id: field.key,
    "aria-invalid": invalid,
    "aria-describedby": describedBy,
    disabled: readOnly,
  } as const

  switch (field.type) {
    case "text":
      return (
        <textarea
          {...shared}
          className="textarea"
          value={typeof value === "string" ? value : ""}
          maxLength={field.max_length ?? undefined}
          onChange={(event) => onChange(event.target.value)}
        />
      )

    case "number":
      return (
        <input
          {...shared}
          className="input"
          type="number"
          value={typeof value === "number" ? String(value) : ""}
          min={field.min_value ?? undefined}
          max={field.max_value ?? undefined}
          onChange={(event) => {
            const parsed = Number(event.target.value)
            onChange(event.target.value === "" || Number.isNaN(parsed) ? null : parsed)
          }}
        />
      )

    case "boolean":
      return (
        <label className="checkbox">
          <input
            {...shared}
            type="checkbox"
            checked={value === true}
            onChange={(event) => onChange(event.target.checked)}
          />
          <span>{value === true ? "Ativado" : "Desativado"}</span>
        </label>
      )

    case "select":
      return (
        <select
          {...shared}
          className="select"
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value === "" ? null : event.target.value)}
        >
          <option value="">Selecione…</option>
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      )

    case "multiselect":
      return <MultiSelect field={field} value={value} readOnly={readOnly} onChange={onChange} />

    case "string":
    default:
      return (
        <input
          {...shared}
          className="input"
          type="text"
          value={typeof value === "string" ? value : ""}
          maxLength={field.max_length ?? undefined}
          onChange={(event) => onChange(event.target.value)}
        />
      )
  }
}

function MultiSelect({
  field,
  value,
  readOnly,
  onChange,
}: {
  readonly field: FieldSpec
  readonly value: ConfigValue
  readonly readOnly: boolean
  readonly onChange: (value: ConfigValue) => void
}): JSX.Element {
  const selected = Array.isArray(value) ? value.map(String) : []

  function toggle(option: string): void {
    const next = selected.includes(option)
      ? selected.filter((entry) => entry !== option)
      : [...selected, option]
    onChange(next)
  }

  // Sem catálogo de opções o campo é livre (por exemplo os casos de um
  // `control.switch`), e a entrada vira uma lista separada por vírgula.
  if (field.options.length === 0) {
    return (
      <input
        id={field.key}
        className="input"
        type="text"
        disabled={readOnly}
        value={selected.join(", ")}
        placeholder="valor1, valor2"
        onChange={(event) =>
          onChange(
            event.target.value
              .split(",")
              .map((entry) => entry.trim())
              .filter((entry) => entry.length > 0),
          )
        }
      />
    )
  }

  return (
    <div className="options" role="group" aria-labelledby={field.key}>
      {field.options.map((option) => (
        <button
          key={option}
          type="button"
          className="option-chip"
          aria-pressed={selected.includes(option)}
          disabled={readOnly}
          onClick={() => toggle(option)}
        >
          {option}
        </button>
      ))}
    </div>
  )
}
