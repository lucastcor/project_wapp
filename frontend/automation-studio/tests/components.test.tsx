/**
 * Componentes críticos: paleta e inspector.
 *
 * Os dois são onde a UI poderia divergir do backend — a paleta decide o que
 * pode ser adicionado, o inspector decide como cada campo é editado. Ambos
 * leem do catálogo do servidor, e é isso que estes testes provam.
 */

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import type { FieldSpec, GraphNode, NodeSpec } from "@/api/types"
import { Inspector } from "@/components/Inspector"
import { Palette } from "@/components/Palette"

function makeSpec(overrides: Partial<NodeSpec> = {}): NodeSpec {
  return {
    node_type: "action.sendMessage",
    category: "action",
    label: "Enviar mensagem",
    description: "Enfileira uma mensagem de saída.",
    outputs: ["next"],
    fields: [],
    required_capability: null,
    produces_effect: true,
    suspends: false,
    accepts_input: true,
    is_terminal: false,
    ...overrides,
  }
}

function makeField(overrides: Partial<FieldSpec> = {}): FieldSpec {
  return {
    key: "reason",
    label: "Motivo",
    type: "string",
    required: true,
    options: [],
    min_value: null,
    max_value: null,
    max_length: 200,
    default: null,
    help_text: "",
    ...overrides,
  }
}

const node: GraphNode = {
  id: "send1",
  node_type: "action.sendMessage",
  position: { x: 0, y: 0 },
  config: {},
}

describe("Palette", () => {
  it("agrupa os blocos por categoria", () => {
    render(
      <Palette
        specs={[makeSpec(), makeSpec({ node_type: "trigger.manual", category: "trigger", label: "Disparo manual" })]}
        enabledCapabilities={[]}
        hasTrigger={false}
        collapsed={false}
        onToggle={vi.fn()}
        onAdd={vi.fn()}
      />,
    )

    expect(screen.getByText("Gatilho")).toBeInTheDocument()
    expect(screen.getByText("Ação")).toBeInTheDocument()
  })

  it("desabilita o bloco cuja capacidade está desligada, dizendo o motivo", () => {
    render(
      <Palette
        specs={[makeSpec({ required_capability: "outbound" })]}
        enabledCapabilities={[]}
        hasTrigger={false}
        collapsed={false}
        onToggle={vi.fn()}
        onAdd={vi.fn()}
      />,
    )

    const button = screen.getByRole("button", { name: /Enviar mensagem/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute("title", expect.stringContaining("outbound"))
  })

  it("habilita o bloco quando a capacidade está ligada", () => {
    render(
      <Palette
        specs={[makeSpec({ required_capability: "outbound" })]}
        enabledCapabilities={["outbound"]}
        hasTrigger={false}
        collapsed={false}
        onToggle={vi.fn()}
        onAdd={vi.fn()}
      />,
    )

    expect(screen.getByRole("button", { name: /Enviar mensagem/ })).toBeEnabled()
  })

  it("impede um segundo gatilho", () => {
    render(
      <Palette
        specs={[makeSpec({ node_type: "trigger.manual", category: "trigger", label: "Disparo manual" })]}
        enabledCapabilities={[]}
        hasTrigger
        collapsed={false}
        onToggle={vi.fn()}
        onAdd={vi.fn()}
      />,
    )

    expect(screen.getByRole("button", { name: /Disparo manual/ })).toBeDisabled()
  })

  it("filtra pela busca", async () => {
    const user = userEvent.setup()
    render(
      <Palette
        specs={[makeSpec(), makeSpec({ node_type: "delay.wait", category: "time", label: "Esperar" })]}
        enabledCapabilities={[]}
        hasTrigger={false}
        collapsed={false}
        onToggle={vi.fn()}
        onAdd={vi.fn()}
      />,
    )

    await user.type(screen.getByLabelText("Buscar bloco"), "esper")

    expect(screen.getByRole("button", { name: /Esperar/ })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Enviar mensagem/ })).not.toBeInTheDocument()
  })

  it("avisa quando a busca não encontra nada", async () => {
    const user = userEvent.setup()
    render(
      <Palette
        specs={[makeSpec()]}
        enabledCapabilities={[]}
        hasTrigger={false}
        collapsed={false}
        onToggle={vi.fn()}
        onAdd={vi.fn()}
      />,
    )

    await user.type(screen.getByLabelText("Buscar bloco"), "zzzz")

    expect(screen.getByText(/Nenhum bloco corresponde/)).toBeInTheDocument()
  })

  it("emite o bloco escolhido", async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(
      <Palette
        specs={[makeSpec()]}
        enabledCapabilities={[]}
        hasTrigger={false}
        collapsed={false}
        onToggle={vi.fn()}
        onAdd={onAdd}
      />,
    )

    await user.click(screen.getByRole("button", { name: /Enviar mensagem/ }))

    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ node_type: "action.sendMessage" }))
  })
})

describe("Inspector", () => {
  it("desenha o formulário a partir do catálogo, não de código por tipo", () => {
    render(
      <Inspector
        node={node}
        spec={makeSpec({
          fields: [
            makeField(),
            makeField({ key: "priority", label: "Prioridade", type: "select", options: ["low", "high"] }),
          ],
        })}
        issues={[]}
        readOnly={false}
        onChange={vi.fn()}
        onRemove={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByLabelText(/Motivo/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Prioridade/)).toBeInTheDocument()
  })

  it("propaga a edição de um campo de texto", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <Inspector
        node={node}
        spec={makeSpec({ fields: [makeField()] })}
        issues={[]}
        readOnly={false}
        onChange={onChange}
        onRemove={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    await user.type(screen.getByLabelText(/Motivo/), "x")

    expect(onChange).toHaveBeenCalledWith("reason", "x")
  })

  it("mostra o erro de validação junto do campo", () => {
    render(
      <Inspector
        node={node}
        spec={makeSpec({ fields: [makeField()] })}
        issues={[
          {
            code: "missing_required_field",
            message: "'Motivo' é obrigatório.",
            severity: "error",
            node_id: "send1",
            edge_id: null,
            field_key: "reason",
          },
        ]}
        readOnly={false}
        onChange={vi.fn()}
        onRemove={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent("'Motivo' é obrigatório.")
    expect(screen.getByLabelText(/Motivo/)).toHaveAttribute("aria-invalid", "true")
  })

  it("alterna opções de um multiselect", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <Inspector
        node={node}
        spec={makeSpec({
          fields: [
            makeField({ key: "values", label: "Classificações", type: "multiselect", options: ["a", "b"] }),
          ],
        })}
        issues={[]}
        readOnly={false}
        onChange={onChange}
        onRemove={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    await user.click(screen.getByRole("button", { name: "a", pressed: false }))

    expect(onChange).toHaveBeenCalledWith("values", ["a"])
  })

  it("esconde a remoção em modo somente leitura", () => {
    render(
      <Inspector
        node={node}
        spec={makeSpec()}
        issues={[]}
        readOnly
        onChange={vi.fn()}
        onRemove={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    expect(screen.queryByRole("button", { name: "Remover bloco" })).not.toBeInTheDocument()
  })

  it("sinaliza que o bloco produz efeito externo", () => {
    render(
      <Inspector
        node={node}
        spec={makeSpec({ produces_effect: true })}
        issues={[]}
        readOnly={false}
        onChange={vi.fn()}
        onRemove={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText("Efeito externo")).toBeInTheDocument()
  })
})
