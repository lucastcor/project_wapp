"""Modelo de domínio do Automation Studio.

Duas separações estruturam este módulo:

1. **Comportamento versus apresentação.** `position` e `viewport` descrevem o
   canvas e não influenciam a execução. Mover um nó não muda o
   `content_hash`, então não gera uma versão nova nem invalida o que está no
   ar. O comportamento vem de `node_type`, `config` e das arestas.

2. **Estado do controle versus estado da execução.** Um deployment tem
   `state` (active/paused); uma execução tem `status` (queued…dead_letter).
   Pausar o deployment nunca mexe no status de uma execução já em espera.

Módulo puro: sem I/O, sem relógio implícito, sem acesso a banco.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "automation-graph/1"

# --- estados ---------------------------------------------------------------------

DEPLOYMENT_ACTIVE = "active"
DEPLOYMENT_PAUSED = "paused"
DEPLOYMENT_STATES = frozenset({DEPLOYMENT_ACTIVE, DEPLOYMENT_PAUSED})

EXECUTION_QUEUED = "queued"
EXECUTION_RUNNING = "running"
EXECUTION_WAITING = "waiting"
EXECUTION_AWAITING_HUMAN = "awaiting_human"
EXECUTION_SUCCEEDED = "succeeded"
EXECUTION_FAILED = "failed"
EXECUTION_CANCELED = "canceled"
EXECUTION_DEAD_LETTER = "dead_letter"

EXECUTION_STATES = frozenset(
    {
        EXECUTION_QUEUED,
        EXECUTION_RUNNING,
        EXECUTION_WAITING,
        EXECUTION_AWAITING_HUMAN,
        EXECUTION_SUCCEEDED,
        EXECUTION_FAILED,
        EXECUTION_CANCELED,
        EXECUTION_DEAD_LETTER,
    }
)
# Estados terminais: nenhum job novo é criado a partir deles.
TERMINAL_EXECUTION_STATES = frozenset(
    {
        EXECUTION_SUCCEEDED,
        EXECUTION_FAILED,
        EXECUTION_CANCELED,
        EXECUTION_DEAD_LETTER,
    }
)

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"
JOB_CANCELED = "canceled"
JOB_DEAD_LETTER = "dead_letter"
JOB_CLAIMABLE_STATES = frozenset({JOB_QUEUED})

EFFECT_PENDING = "pending"
EFFECT_SUCCEEDED = "succeeded"
EFFECT_FAILED = "failed"

GATE_AWAITING_HUMAN = "awaiting_human"
GATE_PROCESSING = "processing"
GATE_COMPLETED = "completed"
GATE_FAILED = "failed"
GATE_EXPIRED = "expired"
GATE_CANCELED = "canceled"

DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"


# --- grafo ------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    node_type: str
    # Apresentação apenas. Fora do hash de conteúdo de propósito.
    position: tuple[float, float] = (0.0, 0.0)
    config: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "node_type": self.node_type,
            "position": {"x": self.position[0], "y": self.position[1]},
            "config": dict(self.config),
        }


@dataclass(frozen=True)
class WorkflowEdge:
    id: str
    source: str
    target: str
    # Alça de saída do nó de origem. Obrigatória em condições e switches.
    source_handle: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "source_handle": self.source_handle,
        }


@dataclass(frozen=True)
class WorkflowGraph:
    nodes: tuple[WorkflowNode, ...] = ()
    edges: tuple[WorkflowEdge, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def node(self, node_id: str) -> WorkflowNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def outgoing(self, node_id: str) -> tuple[WorkflowEdge, ...]:
        return tuple(edge for edge in self.edges if edge.source == node_id)

    def incoming(self, node_id: str) -> tuple[WorkflowEdge, ...]:
        return tuple(edge for edge in self.edges if edge.target == node_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "nodes": [node.as_dict() for node in self.nodes],
            "edges": [edge.as_dict() for edge in self.edges],
        }


def parse_graph(payload: dict[str, Any] | None) -> WorkflowGraph:
    """Converte o JSON do editor no grafo tipado.

    Tolerante em forma (aceita `type`/`node_type`, posição ausente) e estrito
    em conteúdo: qualquer coisa que não sirva para executar é rejeitada
    depois, por `validate_graph`, com mensagem localizada no nó.
    """
    data = payload or {}
    nodes: list[WorkflowNode] = []
    for raw in data.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        position = raw.get("position") or {}
        nodes.append(
            WorkflowNode(
                id=str(raw.get("id") or ""),
                node_type=str(raw.get("node_type") or raw.get("type") or ""),
                position=(
                    float(position.get("x") or 0.0),
                    float(position.get("y") or 0.0),
                ),
                config=dict(raw.get("config") or {}),
            )
        )

    edges: list[WorkflowEdge] = []
    for raw in data.get("edges") or []:
        if not isinstance(raw, dict):
            continue
        handle = raw.get("source_handle") or raw.get("sourceHandle")
        edges.append(
            WorkflowEdge(
                id=str(raw.get("id") or ""),
                source=str(raw.get("source") or ""),
                target=str(raw.get("target") or ""),
                source_handle=str(handle) if handle else None,
            )
        )

    return WorkflowGraph(
        nodes=tuple(nodes),
        edges=tuple(edges),
        schema_version=str(data.get("schema_version") or SCHEMA_VERSION),
    )


def canonical_form(graph: WorkflowGraph) -> str:
    """Forma canônica do comportamento do grafo.

    Ordena nós e arestas e descarta posição. Dois grafos com a mesma
    canonical form fazem exatamente a mesma coisa — reposicionar blocos no
    canvas não conta como mudança de comportamento.
    """
    payload = {
        "schema_version": graph.schema_version,
        "nodes": sorted(
            (
                {
                    "id": node.id,
                    "node_type": node.node_type,
                    "config": _canonical_config(node.config),
                }
                for node in graph.nodes
            ),
            key=lambda entry: str(entry["id"]),
        ),
        "edges": sorted(
            (
                {
                    "source": edge.source,
                    "target": edge.target,
                    "source_handle": edge.source_handle,
                }
                for edge in graph.edges
            ),
            key=lambda entry: (
                str(entry["source"]),
                str(entry["source_handle"] or ""),
                str(entry["target"]),
            ),
        ),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_config(config: dict[str, Any]) -> dict[str, Any]:
    """Config normalizada: chaves ordenadas e valores em forma estável."""
    normalized: dict[str, Any] = {}
    for key in sorted(config):
        value = config[key]
        if isinstance(value, (list, tuple)):
            normalized[key] = [_canonical_scalar(item) for item in value]
        elif isinstance(value, dict):
            normalized[key] = _canonical_config(value)
        else:
            normalized[key] = _canonical_scalar(value)
    return normalized


def _canonical_scalar(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return str(value)


def content_hash(graph: WorkflowGraph) -> str:
    return hashlib.sha256(canonical_form(graph).encode("utf-8")).hexdigest()


def enrollment_key(*, workflow_id: int, event_key: str) -> str:
    """Identidade lógica de uma inscrição.

    Único por (empresa, workflow, evento): reentregar o mesmo evento encontra
    a execução existente em vez de criar uma segunda.
    """
    return f"wf:{int(workflow_id)}|ev:{event_key}"


def effect_key(
    *,
    company_id: int,
    workflow_id: int,
    execution_id: int,
    node_id: str,
    occurrence: int,
) -> str:
    """Identidade de um efeito externo.

    Considera empresa, workflow, execução, nó estável e ocorrência lógica.
    Um retry do mesmo passo recalcula exatamente a mesma chave e encontra o
    efeito já concluído — é isso que impede o envio duplicado.
    """
    raw = (
        f"c:{int(company_id)}|w:{int(workflow_id)}|x:{int(execution_id)}"
        f"|n:{node_id}|o:{int(occurrence)}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]
