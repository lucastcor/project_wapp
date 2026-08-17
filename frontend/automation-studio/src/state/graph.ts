/**
 * Operações puras sobre o grafo, com histórico para undo/redo.
 *
 * Puro de propósito: nenhuma dessas funções toca React, `@xyflow/react` ou
 * rede. É o que permite testá-las diretamente e é o que mantém a única
 * autoridade sobre semântica no backend — aqui só se edita a estrutura.
 */

import type { ConfigValue, GraphEdge, GraphNode, NodeSpec, WorkflowGraph } from "@/api/types"

export const SCHEMA_VERSION = "automation-graph/1"

export const EMPTY_GRAPH: WorkflowGraph = {
  schema_version: SCHEMA_VERSION,
  nodes: [],
  edges: [],
}

const MAX_HISTORY = 50

export interface EditorHistory {
  readonly past: readonly WorkflowGraph[]
  readonly present: WorkflowGraph
  readonly future: readonly WorkflowGraph[]
}

export function initialHistory(graph: WorkflowGraph): EditorHistory {
  return { past: [], present: graph, future: [] }
}

/** Registra uma alteração. O futuro é descartado: é o contrato do undo. */
export function pushHistory(history: EditorHistory, next: WorkflowGraph): EditorHistory {
  if (next === history.present) return history
  return {
    past: [...history.past, history.present].slice(-MAX_HISTORY),
    present: next,
    future: [],
  }
}

export function undo(history: EditorHistory): EditorHistory {
  const previous = history.past[history.past.length - 1]
  if (previous === undefined) return history
  return {
    past: history.past.slice(0, -1),
    present: previous,
    future: [history.present, ...history.future].slice(0, MAX_HISTORY),
  }
}

export function redo(history: EditorHistory): EditorHistory {
  const next = history.future[0]
  if (next === undefined) return history
  return {
    past: [...history.past, history.present].slice(-MAX_HISTORY),
    present: next,
    future: history.future.slice(1),
  }
}

export const canUndo = (history: EditorHistory): boolean => history.past.length > 0
export const canRedo = (history: EditorHistory): boolean => history.future.length > 0

/** Identificador legível e estável. Estável importa: ele vai na chave de efeito. */
export function makeNodeId(nodeType: string, existing: readonly GraphNode[]): string {
  const base = (nodeType.split(".")[1] ?? nodeType).replace(/[^A-Za-z0-9]/g, "") || "node"
  const taken = new Set(existing.map((node) => node.id))
  let index = 1
  let candidate = `${base}${index}`
  while (taken.has(candidate)) {
    index += 1
    candidate = `${base}${index}`
  }
  return candidate
}

/** Configuração inicial de um nó, a partir dos defaults do catálogo. */
export function defaultConfig(spec: NodeSpec): Record<string, ConfigValue> {
  const config: Record<string, ConfigValue> = {}
  for (const field of spec.fields) {
    if (field.default === null || field.default === undefined) continue
    config[field.key] = field.default as ConfigValue
  }
  return config
}

export function addNode(
  graph: WorkflowGraph,
  spec: NodeSpec,
  position: { x: number; y: number },
): WorkflowGraph {
  const node: GraphNode = {
    id: makeNodeId(spec.node_type, graph.nodes),
    node_type: spec.node_type,
    position,
    config: defaultConfig(spec),
  }
  return { ...graph, nodes: [...graph.nodes, node] }
}

/** Remove o nó e toda aresta que o tocava. Aresta órfã seria grafo quebrado. */
export function removeNode(graph: WorkflowGraph, nodeId: string): WorkflowGraph {
  return {
    ...graph,
    nodes: graph.nodes.filter((node) => node.id !== nodeId),
    edges: graph.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
  }
}

export function moveNode(
  graph: WorkflowGraph,
  nodeId: string,
  position: { x: number; y: number },
): WorkflowGraph {
  return {
    ...graph,
    nodes: graph.nodes.map((node) => (node.id === nodeId ? { ...node, position } : node)),
  }
}

export function updateNodeConfig(
  graph: WorkflowGraph,
  nodeId: string,
  key: string,
  value: ConfigValue,
): WorkflowGraph {
  return {
    ...graph,
    nodes: graph.nodes.map((node) =>
      node.id === nodeId ? { ...node, config: { ...node.config, [key]: value } } : node,
    ),
  }
}

export function edgeId(source: string, handle: string | null, target: string): string {
  return `${source}:${handle ?? "next"}->${target}`
}

/**
 * Conecta dois nós.
 *
 * Uma saída já usada é **substituída**, não duplicada: fan-out implícito não
 * existe na v1, e deixar o editor criar a segunda aresta só produziria um erro
 * de validação depois. Trocar o destino é o que o usuário quis dizer.
 */
export function connect(
  graph: WorkflowGraph,
  source: string,
  target: string,
  handle: string | null,
): WorkflowGraph {
  if (source === target) return graph

  const withoutSameOutput = graph.edges.filter(
    (edge) => !(edge.source === source && (edge.source_handle ?? null) === handle),
  )
  const edge: GraphEdge = {
    id: edgeId(source, handle, target),
    source,
    target,
    source_handle: handle,
  }
  return { ...graph, edges: [...withoutSameOutput, edge] }
}

export function disconnect(graph: WorkflowGraph, id: string): WorkflowGraph {
  return { ...graph, edges: graph.edges.filter((edge) => edge.id !== id) }
}

/** Comparação de comportamento: posição fora, exatamente como no backend. */
export function behaviourSignature(graph: WorkflowGraph): string {
  const nodes = [...graph.nodes]
    .map((node) => ({
      id: node.id,
      node_type: node.node_type,
      config: sortedConfig(node.config),
    }))
    .sort((left, right) => left.id.localeCompare(right.id))

  const edges = [...graph.edges]
    .map((edge) => ({
      source: edge.source,
      target: edge.target,
      source_handle: edge.source_handle,
    }))
    .sort((left, right) =>
      `${left.source}${left.source_handle ?? ""}${left.target}`.localeCompare(
        `${right.source}${right.source_handle ?? ""}${right.target}`,
      ),
    )

  return JSON.stringify({ schema_version: graph.schema_version, nodes, edges })
}

function sortedConfig(config: Readonly<Record<string, ConfigValue>>): Record<string, ConfigValue> {
  const result: Record<string, ConfigValue> = {}
  for (const key of Object.keys(config).sort()) {
    const value = config[key]
    if (value !== undefined) result[key] = value
  }
  return result
}

/** Saídas efetivas de um nó, já considerando um `control.switch` configurado. */
export function outputsFor(spec: NodeSpec | undefined, node: GraphNode): readonly string[] {
  if (spec === undefined) return []
  if (node.node_type !== "control.switch") return spec.outputs

  const cases = node.config["cases"]
  const named = Array.isArray(cases) ? cases.map(String).filter((entry) => entry.length > 0) : []
  return [...named, "default"]
}
