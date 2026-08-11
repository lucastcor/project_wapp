/**
 * Operações de grafo e histórico.
 *
 * O foco é o que o editor garante sozinho: identificadores estáveis, ausência
 * de aresta órfã, ausência de fan-out implícito e uma assinatura de
 * comportamento que ignora posição — a mesma regra do backend.
 */

import { describe, expect, it } from "vitest"

import type { NodeSpec, WorkflowGraph } from "@/api/types"
import {
  addNode,
  behaviourSignature,
  canRedo,
  canUndo,
  connect,
  defaultConfig,
  disconnect,
  EMPTY_GRAPH,
  initialHistory,
  makeNodeId,
  moveNode,
  outputsFor,
  pushHistory,
  redo,
  removeNode,
  undo,
  updateNodeConfig,
} from "@/state/graph"

function spec(overrides: Partial<NodeSpec> = {}): NodeSpec {
  return {
    node_type: "condition.classification",
    category: "condition",
    label: "Classificação",
    description: "",
    outputs: ["true", "false"],
    fields: [],
    required_capability: null,
    produces_effect: false,
    suspends: false,
    accepts_input: true,
    is_terminal: false,
    ...overrides,
  }
}

describe("identificadores de nó", () => {
  it("deriva um nome legível do tipo", () => {
    expect(makeNodeId("trigger.manual", [])).toBe("manual1")
    expect(makeNodeId("action.sendMessage", [])).toBe("sendMessage1")
  })

  it("nunca colide com um id existente", () => {
    const graph = addNode(EMPTY_GRAPH, spec(), { x: 0, y: 0 })
    const second = addNode(graph, spec(), { x: 0, y: 0 })

    const ids = second.nodes.map((node) => node.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids).toEqual(["classification1", "classification2"])
  })
})

describe("configuração inicial", () => {
  it("usa os defaults declarados pelo catálogo", () => {
    const withDefaults = spec({
      fields: [
        {
          key: "unit",
          label: "Unidade",
          type: "select",
          required: true,
          options: ["minutes", "hours"],
          min_value: null,
          max_value: null,
          max_length: null,
          default: "hours",
          help_text: "",
        },
        {
          key: "amount",
          label: "Quantidade",
          type: "number",
          required: true,
          options: [],
          min_value: 1,
          max_value: 10,
          max_length: null,
          default: null,
          help_text: "",
        },
      ],
    })

    expect(defaultConfig(withDefaults)).toEqual({ unit: "hours" })
  })
})

describe("arestas", () => {
  const base = (): WorkflowGraph => {
    let graph = addNode(EMPTY_GRAPH, spec({ node_type: "trigger.manual", outputs: ["next"] }), {
      x: 0,
      y: 0,
    })
    graph = addNode(graph, spec({ node_type: "terminal.done", outputs: [] }), { x: 0, y: 100 })
    graph = addNode(graph, spec({ node_type: "terminal.canceled", outputs: [] }), { x: 0, y: 200 })
    return graph
  }

  it("conecta dois blocos por uma alça", () => {
    const graph = connect(base(), "manual1", "done1", "next")

    expect(graph.edges).toHaveLength(1)
    expect(graph.edges[0]).toMatchObject({ source: "manual1", target: "done1", source_handle: "next" })
  })

  it("substitui a conexão quando a mesma saída é reutilizada", () => {
    // Fan-out implícito não existe na v1: reconectar troca o destino.
    let graph = connect(base(), "manual1", "done1", "next")
    graph = connect(graph, "manual1", "canceled1", "next")

    expect(graph.edges).toHaveLength(1)
    expect(graph.edges[0]?.target).toBe("canceled1")
  })

  it("mantém saídas diferentes independentes", () => {
    let graph = base()
    graph = connect(graph, "manual1", "done1", "true")
    graph = connect(graph, "manual1", "canceled1", "false")

    expect(graph.edges).toHaveLength(2)
  })

  it("recusa auto-conexão", () => {
    const graph = connect(base(), "manual1", "manual1", "next")

    expect(graph.edges).toHaveLength(0)
  })

  it("remove arestas órfãs junto com o bloco", () => {
    let graph = connect(base(), "manual1", "done1", "next")
    graph = removeNode(graph, "done1")

    expect(graph.nodes.map((node) => node.id)).not.toContain("done1")
    expect(graph.edges).toHaveLength(0)
  })

  it("desconecta pelo id da aresta", () => {
    const graph = connect(base(), "manual1", "done1", "next")
    const edgeId = graph.edges[0]?.id ?? ""

    expect(disconnect(graph, edgeId).edges).toHaveLength(0)
  })
})

describe("assinatura de comportamento", () => {
  it("ignora a posição do bloco", () => {
    const graph = addNode(EMPTY_GRAPH, spec(), { x: 0, y: 0 })
    const moved = moveNode(graph, "classification1", { x: 900, y: -400 })

    expect(behaviourSignature(moved)).toBe(behaviourSignature(graph))
  })

  it("muda quando a configuração muda", () => {
    const graph = addNode(EMPTY_GRAPH, spec(), { x: 0, y: 0 })
    const edited = updateNodeConfig(graph, "classification1", "values", ["orcamento"])

    expect(behaviourSignature(edited)).not.toBe(behaviourSignature(graph))
  })

  it("não depende da ordem dos nós", () => {
    let graph = addNode(EMPTY_GRAPH, spec({ node_type: "trigger.manual" }), { x: 0, y: 0 })
    graph = addNode(graph, spec({ node_type: "terminal.done" }), { x: 0, y: 100 })
    const reversed: WorkflowGraph = { ...graph, nodes: [...graph.nodes].reverse() }

    expect(behaviourSignature(reversed)).toBe(behaviourSignature(graph))
  })
})

describe("histórico", () => {
  it("desfaz e refaz mantendo a ordem", () => {
    const first = addNode(EMPTY_GRAPH, spec({ node_type: "trigger.manual" }), { x: 0, y: 0 })
    const second = addNode(first, spec({ node_type: "terminal.done" }), { x: 0, y: 100 })

    let history = initialHistory(EMPTY_GRAPH)
    history = pushHistory(history, first)
    history = pushHistory(history, second)

    expect(canUndo(history)).toBe(true)
    history = undo(history)
    expect(history.present.nodes).toHaveLength(1)

    expect(canRedo(history)).toBe(true)
    history = redo(history)
    expect(history.present.nodes).toHaveLength(2)
  })

  it("descarta o futuro quando uma alteração nova entra", () => {
    let history = initialHistory(EMPTY_GRAPH)
    history = pushHistory(history, addNode(EMPTY_GRAPH, spec(), { x: 0, y: 0 }))
    history = undo(history)
    expect(canRedo(history)).toBe(true)

    history = pushHistory(history, addNode(EMPTY_GRAPH, spec({ node_type: "trigger.manual" }), { x: 0, y: 0 }))

    expect(canRedo(history)).toBe(false)
  })

  it("é seguro desfazer no estado inicial", () => {
    const history = initialHistory(EMPTY_GRAPH)

    expect(canUndo(history)).toBe(false)
    expect(undo(history)).toBe(history)
    expect(redo(history)).toBe(history)
  })
})

describe("saídas de um bloco", () => {
  it("usa as saídas declaradas pelo catálogo", () => {
    const node = { id: "c1", node_type: "condition.classification", position: { x: 0, y: 0 }, config: {} }

    expect(outputsFor(spec(), node)).toEqual(["true", "false"])
  })

  it("deriva as saídas de um switch da configuração", () => {
    const node = {
      id: "s1",
      node_type: "control.switch",
      position: { x: 0, y: 0 },
      config: { cases: ["a", "b"] },
    }

    expect(outputsFor(spec({ node_type: "control.switch", outputs: [] }), node)).toEqual([
      "a",
      "b",
      "default",
    ])
  })
})
