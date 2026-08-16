"""Schemas HTTP do Automation Studio.

Validação de forma acontece aqui, na borda. Validação de **semântica** do
grafo continua em `app.automations.validation`: o schema garante que o
payload é um grafo bem formado; o validador garante que ele pode ser
executado.

`graph` chega como dicionário livre de propósito — a estrutura de nós e
arestas é responsabilidade do validador, que sabe localizar cada problema no
nó certo e devolver todos de uma vez. Um schema estrito aqui produziria um
422 genérico e uma UI que não consegue apontar onde está o erro.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_GRAPH_BYTES = 512_000


def _reject_oversized_graph(value: dict[str, Any]) -> dict[str, Any]:
    # Limite bruto antes de qualquer processamento: um grafo enorme não deve
    # nem chegar ao parser.
    size = len(str(value).encode("utf-8"))
    if size > MAX_GRAPH_BYTES:
        raise ValueError(f"graph is {size} bytes; the limit is {MAX_GRAPH_BYTES}.")
    return value


class WorkflowCreatePayload(BaseModel):
    workflow_key: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=600)


class WorkflowUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=600)
    archived: bool | None = None


class DraftSavePayload(BaseModel):
    graph: dict[str, Any]
    # Revisão que o editor tinha ao começar a alterar. É o que transforma
    # duas edições simultâneas em 409 em vez de last-write-wins.
    expected_revision: int = Field(..., ge=1)
    viewport: dict[str, Any] | None = None

    @field_validator("graph")
    @classmethod
    def _check_graph_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _reject_oversized_graph(value)


class GraphValidationPayload(BaseModel):
    graph: dict[str, Any]

    @field_validator("graph")
    @classmethod
    def _check_graph_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _reject_oversized_graph(value)


class GraphSimulationPayload(BaseModel):
    graph: dict[str, Any]
    # Evento fictício. A simulação não lê banco nem canal.
    sample_context: dict[str, Any] | None = None
    # Resposta para cada portão humano: `node_id -> approved|rejected`.
    approval_decisions: dict[str, str] | None = None

    @field_validator("graph")
    @classmethod
    def _check_graph_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _reject_oversized_graph(value)

    @field_validator("approval_decisions")
    @classmethod
    def _check_decisions(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        for node_id, decision in (value or {}).items():
            if decision not in {"approved", "rejected"}:
                raise ValueError(
                    f"approval_decisions['{node_id}'] must be 'approved' or 'rejected'."
                )
        return value


class WorkflowPublishPayload(BaseModel):
    notes: str | None = Field(default=None, max_length=600)


class WorkflowStatePayload(BaseModel):
    state: str = Field(..., pattern="^(active|paused)$")


class ManualTriggerPayload(BaseModel):
    contact_id: str | None = Field(default=None, max_length=120)
    # Chave de idempotência: dois cliques com a mesma chave produzem um
    # disparo. Sem ela, um duplo clique criaria duas execuções.
    idempotency_key: str = Field(..., min_length=8, max_length=120)


class GateDecisionPayload(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    note: str | None = Field(default=None, max_length=600)
