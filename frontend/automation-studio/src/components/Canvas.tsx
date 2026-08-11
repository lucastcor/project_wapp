/**
 * Canvas do editor.
 *
 * O navegador **nunca executa** o fluxo: este componente desenha o grafo e
 * emite intenções de edição. Toda decisão de comportamento — validar, simular,
 * publicar, executar — acontece no servidor.
 */

import { useCallback, useMemo } from "react"
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
  type OnConnect,
  type OnNodesChange,
} from "@xyflow/react"

import "@xyflow/react/dist/style.css"

import type { GraphNode, NodeSpec, ValidationIssue, WorkflowGraph } from "@/api/types"
import { outputsFor } from "@/state/graph"
import { categoryColor } from "./primitives"

interface CanvasNodeData extends Record<string, unknown> {
  readonly graphNode: GraphNode
  readonly spec: NodeSpec | undefined
  readonly invalid: boolean
  readonly inPath: boolean
  readonly outputs: readonly string[]
}

function summarize(node: GraphNode, spec: NodeSpec | undefined): string {
  if (spec === undefined) return "Tipo desconhecido"
  const parts: string[] = []
  for (const field of spec.fields) {
    const value = node.config[field.key]
    if (value === null || value === undefined || value === "") continue
    const rendered = Array.isArray(value) ? value.join(", ") : String(value)
    parts.push(`${field.label}: ${rendered}`)
    if (parts.length === 2) break
  }
  return parts.length > 0 ? parts.join(" · ") : "Sem configuração"
}

function CanvasNode({ data, selected }: NodeProps): JSX.Element {
  const { graphNode, spec, invalid, inPath, outputs } = data as unknown as CanvasNodeData
  const color = spec ? categoryColor(spec.category) : "var(--text-faint)"
  const acceptsInput = spec?.accepts_input ?? true

  return (
    <div
      className="node"
      data-selected={selected ? "true" : "false"}
      data-invalid={invalid ? "true" : "false"}
      data-in-path={inPath ? "true" : "false"}
      style={{ ["--cat-color" as string]: color }}
    >
      {acceptsInput && <Handle type="target" position={Position.Top} />}

      <p className="node__type">{spec?.category ?? "?"}</p>
      <p className="node__label">{spec?.label ?? graphNode.node_type}</p>
      <p className="node__summary">{summarize(graphNode, spec)}</p>

      {(spec?.produces_effect === true || spec?.suspends === true) && (
        <div className="node__flags">
          {spec.produces_effect && <span className="node__flag node__flag--effect">efeito</span>}
          {spec.suspends && <span className="node__flag node__flag--suspend">suspende</span>}
        </div>
      )}

      {outputs.map((handle, index) => {
        // Saídas distribuídas na borda inferior, com rótulo quando há mais de
        // uma: sem o rótulo, "true" e "false" ficam indistinguíveis.
        const left = outputs.length === 1 ? 50 : ((index + 1) / (outputs.length + 1)) * 100
        return (
          <Handle
            key={handle}
            id={handle}
            type="source"
            position={Position.Bottom}
            style={{ left: `${left}%` }}
          />
        )
      })}

      {outputs.length > 1 && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-around",
            marginTop: 8,
            gap: 4,
          }}
        >
          {outputs.map((handle) => (
            <span key={handle} className="node__flag" style={{ position: "static" }}>
              {handle}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

const nodeTypes: NodeTypes = { automation: CanvasNode }

interface CanvasProps {
  readonly graph: WorkflowGraph
  readonly specs: ReadonlyMap<string, NodeSpec>
  readonly issues: readonly ValidationIssue[]
  readonly simulatedPath: readonly string[]
  readonly selectedNodeId: string | null
  readonly readOnly: boolean
  readonly onSelect: (nodeId: string | null) => void
  readonly onMove: (nodeId: string, position: { x: number; y: number }) => void
  readonly onConnect: (source: string, target: string, handle: string | null) => void
  readonly onDeleteEdge: (edgeId: string) => void
}

export function Canvas(props: CanvasProps): JSX.Element {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  )
}

function CanvasInner({
  graph,
  specs,
  issues,
  simulatedPath,
  selectedNodeId,
  readOnly,
  onSelect,
  onMove,
  onConnect,
  onDeleteEdge,
}: CanvasProps): JSX.Element {
  const invalidNodeIds = useMemo(
    () =>
      new Set(
        issues
          .filter((issue) => issue.severity === "error" && issue.node_id !== null)
          .map((issue) => issue.node_id as string),
      ),
    [issues],
  )
  const pathNodeIds = useMemo(() => new Set(simulatedPath), [simulatedPath])

  const nodes: Node[] = useMemo(
    () =>
      graph.nodes.map((node) => {
        const spec = specs.get(node.node_type)
        return {
          id: node.id,
          type: "automation",
          position: { x: node.position.x, y: node.position.y },
          selected: node.id === selectedNodeId,
          draggable: !readOnly,
          data: {
            graphNode: node,
            spec,
            invalid: invalidNodeIds.has(node.id),
            inPath: pathNodeIds.has(node.id),
            outputs: outputsFor(spec, node),
          } satisfies CanvasNodeData,
        }
      }),
    [graph.nodes, specs, selectedNodeId, readOnly, invalidNodeIds, pathNodeIds],
  )

  const edges: Edge[] = useMemo(
    () =>
      graph.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.source_handle,
        animated: pathNodeIds.has(edge.source) && pathNodeIds.has(edge.target),
        label: edge.source_handle !== null && edge.source_handle !== "next"
          ? edge.source_handle
          : undefined,
        labelStyle: { fill: "var(--text-faint)", fontSize: 10 },
        labelBgStyle: { fill: "var(--bg-1)" },
      })),
    [graph.edges, pathNodeIds],
  )

  const handleNodesChange: OnNodesChange = useCallback(
    (changes) => {
      for (const change of changes) {
        if (change.type === "position" && change.position && change.dragging === false) {
          onMove(change.id, change.position)
        }
        if (change.type === "select") {
          onSelect(change.selected ? change.id : null)
        }
      }
    },
    [onMove, onSelect],
  )

  const handleConnect: OnConnect = useCallback(
    (connection) => {
      if (readOnly || connection.source === null || connection.target === null) return
      onConnect(connection.source, connection.target, connection.sourceHandle ?? null)
    },
    [readOnly, onConnect],
  )

  return (
    <div className="canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={handleNodesChange}
        onConnect={handleConnect}
        onEdgesDelete={(deleted) => {
          if (readOnly) return
          for (const edge of deleted) onDeleteEdge(edge.id)
        }}
        onPaneClick={() => onSelect(null)}
        nodesConnectable={!readOnly}
        elementsSelectable
        fitView
        fitViewOptions={{ padding: 0.25, maxZoom: 1.1 }}
        minZoom={0.25}
        maxZoom={1.8}
        proOptions={{ hideAttribution: true }}
        deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="rgba(255,255,255,.06)" />
        <Controls showInteractive={false} position="bottom-right" />
        {graph.nodes.length > 6 && (
          <MiniMap
            pannable
            zoomable
            nodeColor={(node) => {
              const data = node.data as unknown as CanvasNodeData
              return data.spec ? categoryColor(data.spec.category) : "#7b8b96"
            }}
            maskColor="rgba(7,16,15,.72)"
          />
        )}
      </ReactFlow>

      {graph.nodes.length === 0 && (
        <div className="canvas__hint">
          <strong>Comece pelo gatilho</strong>
          <span>Escolha um bloco na paleta à esquerda para adicioná-lo ao fluxo.</span>
        </div>
      )}
    </div>
  )
}
