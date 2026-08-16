"""Validação de grafo. O backend é a autoridade.

O frontend valida para dar feedback imediato, mas nada publica sem passar
por aqui. O resultado é uma lista de problemas localizados (nó ou aresta),
não uma exceção no primeiro erro: quem está editando precisa ver tudo o que
falta de uma vez.

Severidade:
- `error` bloqueia publicação;
- `warning` publica, mas aparece no painel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.automations.catalog import (
    CATEGORY_TRIGGER,
    FIELD_TYPES,
    NODE_CATALOG,
    TERMINAL_TYPES,
    TRIGGER_TYPES,
    get_spec,
    outputs_for,
)
from app.automations.domain import SCHEMA_VERSION, WorkflowGraph

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

MAX_NODES = 120
MAX_EDGES = 240
MAX_CONFIG_BYTES = 16_000

# Qualquer chave que pareça segredo é recusada na configuração. Credencial de
# fluxo é um caminho para exfiltração: quem lê o graph_json passa a ler o
# segredo, e graph_json é visível para todo operador do tenant.
_SECRET_KEY_PATTERN = re.compile(
    r"(secret|token|password|passwd|senha|api[_-]?key|apikey|credential|private[_-]?key|"
    r"authorization|bearer|access[_-]?key)",
    re.IGNORECASE,
)
# Valores que parecem credencial mesmo sob um nome inocente.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"^sk-[A-Za-z0-9_\-]{16,}$"),
    re.compile(r"^Bearer\s+\S{12,}$", re.IGNORECASE),
    re.compile(r"^wapp(adm)?_[A-Za-z0-9_\-]{16,}$"),
    re.compile(r"^eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."),
)

_NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: str = SEVERITY_ERROR
    node_id: str | None = None
    edge_id: str | None = None
    field_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
            "field_key": self.field_key,
        }


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)
    trigger_type: str | None = None

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == SEVERITY_WARNING]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "trigger_type": self.trigger_type,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.as_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class ValidationContext:
    """O que a validação precisa saber sobre a empresa.

    Passado explicitamente para manter o módulo puro e testável sem banco.
    """

    company_id: int
    capabilities: frozenset[str] = frozenset()
    # Recursos tenant-scoped referenciáveis pela configuração, por tipo.
    available_references: dict[str, frozenset[str]] = field(default_factory=dict)
    # Fluxo legado responde de forma síncrona neste gatilho?
    legacy_auto_reply_triggers: frozenset[str] = frozenset()


def validate_graph(
    graph: WorkflowGraph,
    *,
    context: ValidationContext | None = None,
) -> ValidationResult:
    """Valida o grafo inteiro e devolve todos os problemas encontrados."""
    result = ValidationResult()
    add = result.issues.append

    if graph.schema_version != SCHEMA_VERSION:
        add(
            ValidationIssue(
                code="unknown_schema_version",
                message=(
                    f"Versão de schema '{graph.schema_version}' desconhecida; "
                    f"esperada '{SCHEMA_VERSION}'."
                ),
            )
        )
        # Sem schema conhecido não dá para interpretar o resto com segurança.
        return result

    if not graph.nodes:
        add(ValidationIssue(code="empty_graph", message="O fluxo não tem nenhum bloco."))
        return result

    if len(graph.nodes) > MAX_NODES:
        add(
            ValidationIssue(
                code="too_many_nodes",
                message=f"O fluxo tem {len(graph.nodes)} blocos; o limite é {MAX_NODES}.",
            )
        )
    if len(graph.edges) > MAX_EDGES:
        add(
            ValidationIssue(
                code="too_many_edges",
                message=f"O fluxo tem {len(graph.edges)} conexões; o limite é {MAX_EDGES}.",
            )
        )

    _validate_node_identity(graph, add)
    _validate_node_types(graph, add)
    _validate_configs(graph, add, context)
    _validate_edges(graph, add)
    trigger_type = _validate_trigger_and_terminals(graph, add)
    result.trigger_type = trigger_type
    _validate_reachability(graph, add)
    _validate_acyclicity(graph, add)
    _validate_termination(graph, add)

    if context is not None:
        _validate_capabilities(graph, add, context)
        _validate_references(graph, add, context)
        _validate_legacy_conflict(graph, add, context, trigger_type)

    return result


# --- identidade e tipos -------------------------------------------------------------


def _validate_node_identity(graph: WorkflowGraph, add) -> None:
    seen: set[str] = set()
    for node in graph.nodes:
        if not node.id:
            add(ValidationIssue(code="missing_node_id", message="Um bloco está sem identificador."))
            continue
        if not _NODE_ID_PATTERN.match(node.id):
            add(
                ValidationIssue(
                    code="invalid_node_id",
                    message=f"Identificador de bloco inválido: '{node.id}'.",
                    node_id=node.id,
                )
            )
        if node.id in seen:
            add(
                ValidationIssue(
                    code="duplicate_node_id",
                    message=f"Identificador de bloco repetido: '{node.id}'.",
                    node_id=node.id,
                )
            )
        seen.add(node.id)

    edge_ids: set[str] = set()
    for edge in graph.edges:
        if edge.id and edge.id in edge_ids:
            add(
                ValidationIssue(
                    code="duplicate_edge_id",
                    message=f"Identificador de conexão repetido: '{edge.id}'.",
                    edge_id=edge.id,
                )
            )
        edge_ids.add(edge.id)


def _validate_node_types(graph: WorkflowGraph, add) -> None:
    for node in graph.nodes:
        if node.node_type not in NODE_CATALOG:
            add(
                ValidationIssue(
                    code="unknown_node_type",
                    message=f"Tipo de bloco desconhecido: '{node.node_type}'.",
                    node_id=node.id,
                )
            )


# --- configuração -------------------------------------------------------------------


def _validate_configs(graph: WorkflowGraph, add, context: ValidationContext | None) -> None:
    for node in graph.nodes:
        spec = get_spec(node.node_type)
        if spec is None:
            continue

        _reject_secrets(node.id, node.config, add)

        serialized_size = len(str(node.config).encode("utf-8"))
        if serialized_size > MAX_CONFIG_BYTES:
            add(
                ValidationIssue(
                    code="config_too_large",
                    message=(
                        f"Configuração do bloco tem {serialized_size} bytes; "
                        f"o limite é {MAX_CONFIG_BYTES}."
                    ),
                    node_id=node.id,
                )
            )

        known_keys = {field_spec.key for field_spec in spec.fields}
        for key in node.config:
            if key not in known_keys:
                add(
                    ValidationIssue(
                        code="unknown_config_field",
                        message=f"Campo '{key}' não existe em '{spec.label}'.",
                        node_id=node.id,
                        field_key=key,
                        severity=SEVERITY_WARNING,
                    )
                )

        for field_spec in spec.fields:
            value = node.config.get(field_spec.key)
            missing = value is None or (isinstance(value, (str, list)) and len(value) == 0)
            if field_spec.required and missing:
                add(
                    ValidationIssue(
                        code="missing_required_field",
                        message=f"'{field_spec.label}' é obrigatório em '{spec.label}'.",
                        node_id=node.id,
                        field_key=field_spec.key,
                    )
                )
                continue
            if missing:
                continue
            _validate_field_value(node.id, spec.label, field_spec, value, add)


def _validate_field_value(node_id: str, spec_label: str, field_spec, value: Any, add) -> None:
    if field_spec.type not in FIELD_TYPES:
        add(
            ValidationIssue(
                code="unknown_field_type",
                message=f"Tipo de campo desconhecido: '{field_spec.type}'.",
                node_id=node_id,
                field_key=field_spec.key,
            )
        )
        return

    if field_spec.type in {"string", "text"}:
        if not isinstance(value, str):
            add(
                ValidationIssue(
                    code="invalid_field_type",
                    message=f"'{field_spec.label}' precisa ser texto.",
                    node_id=node_id,
                    field_key=field_spec.key,
                )
            )
            return
        if field_spec.max_length and len(value) > field_spec.max_length:
            add(
                ValidationIssue(
                    code="field_too_long",
                    message=(
                        f"'{field_spec.label}' tem {len(value)} caracteres; "
                        f"o limite é {field_spec.max_length}."
                    ),
                    node_id=node_id,
                    field_key=field_spec.key,
                )
            )
        return

    if field_spec.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            add(
                ValidationIssue(
                    code="invalid_field_type",
                    message=f"'{field_spec.label}' precisa ser um número.",
                    node_id=node_id,
                    field_key=field_spec.key,
                )
            )
            return
        if field_spec.min_value is not None and float(value) < field_spec.min_value:
            add(
                ValidationIssue(
                    code="field_below_minimum",
                    message=f"'{field_spec.label}' precisa ser no mínimo {field_spec.min_value}.",
                    node_id=node_id,
                    field_key=field_spec.key,
                )
            )
        if field_spec.max_value is not None and float(value) > field_spec.max_value:
            add(
                ValidationIssue(
                    code="field_above_maximum",
                    message=f"'{field_spec.label}' precisa ser no máximo {field_spec.max_value}.",
                    node_id=node_id,
                    field_key=field_spec.key,
                )
            )
        return

    if field_spec.type == "boolean":
        if not isinstance(value, bool):
            add(
                ValidationIssue(
                    code="invalid_field_type",
                    message=f"'{field_spec.label}' precisa ser verdadeiro ou falso.",
                    node_id=node_id,
                    field_key=field_spec.key,
                )
            )
        return

    if field_spec.type == "select":
        if field_spec.options and str(value) not in field_spec.options:
            add(
                ValidationIssue(
                    code="value_outside_catalog",
                    message=(
                        f"'{value}' não é uma opção válida de '{field_spec.label}' "
                        f"em '{spec_label}'."
                    ),
                    node_id=node_id,
                    field_key=field_spec.key,
                )
            )
        return

    # multiselect
    if not isinstance(value, (list, tuple)):
        add(
            ValidationIssue(
                code="invalid_field_type",
                message=f"'{field_spec.label}' precisa ser uma lista.",
                node_id=node_id,
                field_key=field_spec.key,
            )
        )
        return
    if field_spec.options:
        for item in value:
            if str(item) not in field_spec.options:
                add(
                    ValidationIssue(
                        code="value_outside_catalog",
                        message=(
                            f"'{item}' não é uma opção válida de '{field_spec.label}' "
                            f"em '{spec_label}'."
                        ),
                        node_id=node_id,
                        field_key=field_spec.key,
                    )
                )


def _reject_secrets(node_id: str, config: dict[str, Any], add, prefix: str = "") -> None:
    """Nenhum segredo dentro do grafo, por nome de chave ou forma do valor."""
    for key, value in config.items():
        path = f"{prefix}{key}"
        if _SECRET_KEY_PATTERN.search(str(key)):
            add(
                ValidationIssue(
                    code="secret_like_config_key",
                    message=(
                        f"O campo '{path}' tem nome de credencial. Segredos não podem "
                        "ficar na configuração do fluxo; use um recurso da empresa."
                    ),
                    node_id=node_id,
                    field_key=path,
                )
            )
        if isinstance(value, dict):
            _reject_secrets(node_id, value, add, prefix=f"{path}.")
            continue
        if isinstance(value, str):
            for pattern in _SECRET_VALUE_PATTERNS:
                if pattern.match(value.strip()):
                    add(
                        ValidationIssue(
                            code="secret_like_config_value",
                            message=(
                                f"O valor de '{path}' tem formato de credencial e não "
                                "pode ser guardado no fluxo."
                            ),
                            node_id=node_id,
                            field_key=path,
                        )
                    )
                    break


# --- arestas e alças -----------------------------------------------------------------


def _validate_edges(graph: WorkflowGraph, add) -> None:
    node_ids = {node.id for node in graph.nodes}
    seen_outputs: set[tuple[str, str]] = set()

    for edge in graph.edges:
        if edge.source not in node_ids:
            add(
                ValidationIssue(
                    code="edge_source_missing",
                    message=f"Conexão aponta para um bloco de origem inexistente: '{edge.source}'.",
                    edge_id=edge.id,
                )
            )
            continue
        if edge.target not in node_ids:
            add(
                ValidationIssue(
                    code="edge_target_missing",
                    message=f"Conexão aponta para um bloco de destino inexistente: '{edge.target}'.",
                    edge_id=edge.id,
                )
            )
            continue
        if edge.source == edge.target:
            add(
                ValidationIssue(
                    code="self_loop",
                    message="Um bloco não pode se conectar a si mesmo.",
                    edge_id=edge.id,
                    node_id=edge.source,
                )
            )
            continue

        source_node = graph.node(edge.source)
        target_node = graph.node(edge.target)
        if source_node is None or target_node is None:
            continue

        source_spec = get_spec(source_node.node_type)
        target_spec = get_spec(target_node.node_type)
        if source_spec is None or target_spec is None:
            continue

        if source_spec.is_terminal:
            add(
                ValidationIssue(
                    code="terminal_has_output",
                    message=f"'{source_spec.label}' encerra o fluxo e não pode ter saída.",
                    node_id=source_node.id,
                    edge_id=edge.id,
                )
            )
            continue

        if target_spec.category == CATEGORY_TRIGGER:
            add(
                ValidationIssue(
                    code="trigger_has_input",
                    message=f"'{target_spec.label}' é um gatilho e não pode receber entrada.",
                    node_id=target_node.id,
                    edge_id=edge.id,
                )
            )
            continue

        valid_handles = outputs_for(source_node.node_type, source_node.config)
        handle = edge.source_handle or (valid_handles[0] if len(valid_handles) == 1 else None)
        if handle is None:
            add(
                ValidationIssue(
                    code="missing_source_handle",
                    message=(
                        f"'{source_spec.label}' tem várias saídas; a conexão precisa "
                        "dizer qual delas usa."
                    ),
                    node_id=source_node.id,
                    edge_id=edge.id,
                )
            )
            continue
        if handle not in valid_handles:
            add(
                ValidationIssue(
                    code="invalid_source_handle",
                    message=(
                        f"'{handle}' não é uma saída de '{source_spec.label}'. "
                        f"Saídas válidas: {', '.join(valid_handles) or 'nenhuma'}."
                    ),
                    node_id=source_node.id,
                    edge_id=edge.id,
                )
            )
            continue

        # Uma saída, uma conexão. Sem split explícito, duas arestas na mesma
        # alça seriam fan-out implícito — e fan-out não existe na v1.
        key = (source_node.id, handle)
        if key in seen_outputs:
            add(
                ValidationIssue(
                    code="duplicate_output_edge",
                    message=(
                        f"A saída '{handle}' de '{source_spec.label}' já tem uma conexão. "
                        "Ramificação paralela não existe nesta versão."
                    ),
                    node_id=source_node.id,
                    edge_id=edge.id,
                )
            )
        seen_outputs.add(key)


# --- estrutura ------------------------------------------------------------------------


def _validate_trigger_and_terminals(graph: WorkflowGraph, add) -> str | None:
    triggers = [node for node in graph.nodes if node.node_type in TRIGGER_TYPES]
    terminals = [node for node in graph.nodes if node.node_type in TERMINAL_TYPES]

    if not triggers:
        add(
            ValidationIssue(
                code="missing_trigger",
                message="O fluxo precisa de exatamente um gatilho.",
            )
        )
    elif len(triggers) > 1:
        for trigger in triggers[1:]:
            add(
                ValidationIssue(
                    code="multiple_triggers",
                    message="O fluxo precisa ter apenas um gatilho.",
                    node_id=trigger.id,
                )
            )

    if not terminals:
        add(
            ValidationIssue(
                code="missing_terminal",
                message="O fluxo precisa de pelo menos um bloco de encerramento.",
            )
        )

    return triggers[0].node_type if len(triggers) == 1 else None


def _validate_reachability(graph: WorkflowGraph, add) -> None:
    triggers = [node for node in graph.nodes if node.node_type in TRIGGER_TYPES]
    if not triggers:
        return

    reachable: set[str] = set()
    frontier = [triggers[0].id]
    while frontier:
        current = frontier.pop()
        if current in reachable:
            continue
        reachable.add(current)
        frontier.extend(edge.target for edge in graph.outgoing(current))

    for node in graph.nodes:
        if node.id not in reachable:
            add(
                ValidationIssue(
                    code="unreachable_node",
                    message="Este bloco não é alcançável a partir do gatilho.",
                    node_id=node.id,
                )
            )


def _validate_acyclicity(graph: WorkflowGraph, add) -> None:
    """Ciclos são rejeitados: loop é evolução explícita, não efeito colateral."""
    color: dict[str, int] = {}  # 0=não visto, 1=na pilha, 2=fechado

    def visit(node_id: str) -> str | None:
        color[node_id] = 1
        for edge in graph.outgoing(node_id):
            state = color.get(edge.target, 0)
            if state == 1:
                return edge.target
            if state == 0:
                found = visit(edge.target)
                if found is not None:
                    return found
        color[node_id] = 2
        return None

    for node in graph.nodes:
        if color.get(node.id, 0) != 0:
            continue
        cycle_node = visit(node.id)
        if cycle_node is not None:
            add(
                ValidationIssue(
                    code="cycle_detected",
                    message=(
                        "O fluxo tem um ciclo. Repetição não existe nesta versão: "
                        "todo caminho precisa terminar."
                    ),
                    node_id=cycle_node,
                )
            )
            return


def _validate_termination(graph: WorkflowGraph, add) -> None:
    """Todo caminho precisa poder terminar num bloco de encerramento."""
    can_terminate: dict[str, bool] = {}

    def resolve(node_id: str, stack: set[str]) -> bool:
        if node_id in can_terminate:
            return can_terminate[node_id]
        if node_id in stack:
            # Ciclo: reportado por _validate_acyclicity; aqui não termina.
            return False

        node = graph.node(node_id)
        if node is None:
            return False
        if node.node_type in TERMINAL_TYPES:
            can_terminate[node_id] = True
            return True

        stack.add(node_id)
        outgoing = graph.outgoing(node_id)
        result = any(resolve(edge.target, stack) for edge in outgoing)
        stack.discard(node_id)
        can_terminate[node_id] = result
        return result

    for node in graph.nodes:
        if node.node_type in TERMINAL_TYPES:
            continue
        if not resolve(node.id, set()):
            add(
                ValidationIssue(
                    code="path_cannot_terminate",
                    message="Nenhum caminho a partir deste bloco chega a um encerramento.",
                    node_id=node.id,
                )
            )

    # Uma alça sem conexão também é um caminho que não termina.
    for node in graph.nodes:
        spec = get_spec(node.node_type)
        if spec is None or spec.is_terminal:
            continue
        handles = outputs_for(node.node_type, node.config)
        if not handles:
            continue
        connected = {
            edge.source_handle or (handles[0] if len(handles) == 1 else None)
            for edge in graph.outgoing(node.id)
        }
        for handle in handles:
            if handle not in connected:
                add(
                    ValidationIssue(
                        code="unconnected_output",
                        message=f"A saída '{handle}' de '{spec.label}' não leva a lugar nenhum.",
                        node_id=node.id,
                    )
                )


# --- contexto da empresa ---------------------------------------------------------------


def _validate_capabilities(graph: WorkflowGraph, add, context: ValidationContext) -> None:
    for node in graph.nodes:
        spec = get_spec(node.node_type)
        if spec is None or not spec.required_capability:
            continue
        if spec.required_capability not in context.capabilities:
            add(
                ValidationIssue(
                    code="capability_not_enabled",
                    message=(
                        f"'{spec.label}' exige a capacidade '{spec.required_capability}', "
                        "que não está habilitada para esta empresa."
                    ),
                    node_id=node.id,
                )
            )


def _validate_references(graph: WorkflowGraph, add, context: ValidationContext) -> None:
    """Referência tenant-scoped precisa existir **nesta** empresa.

    Sem isso, um fluxo poderia apontar para um recurso de outro tenant e o
    erro só apareceria na execução.
    """
    for node in graph.nodes:
        spec = get_spec(node.node_type)
        if spec is None:
            continue
        for field_spec in spec.fields:
            available = context.available_references.get(field_spec.key)
            if available is None:
                continue
            value = node.config.get(field_spec.key)
            if value is None:
                continue
            candidates = value if isinstance(value, (list, tuple)) else [value]
            for candidate in candidates:
                if str(candidate) not in available:
                    add(
                        ValidationIssue(
                            code="reference_not_in_company",
                            message=(
                                f"'{candidate}' não é um recurso desta empresa em "
                                f"'{field_spec.label}'."
                            ),
                            node_id=node.id,
                            field_key=field_spec.key,
                        )
                    )


def _validate_legacy_conflict(
    graph: WorkflowGraph,
    add,
    context: ValidationContext,
    trigger_type: str | None,
) -> None:
    """Detecta conflito com a resposta síncrona do orquestrador legado.

    Se o caminho legado já respondeu automaticamente neste gatilho, um envio
    imediato do fluxo produziria duas mensagens para o mesmo contato. Um
    follow-up **depois** de uma espera ou de uma aprovação é permitido: aí a
    intenção de mandar outra mensagem é explícita.
    """
    from app.automations.catalog import ACTION_SEND_MESSAGE

    if trigger_type is None or trigger_type not in context.legacy_auto_reply_triggers:
        return

    triggers = [node for node in graph.nodes if node.node_type in TRIGGER_TYPES]
    if not triggers:
        return

    # Caminha do gatilho marcando se já houve suspensão antes de cada nó.
    suspended_before: dict[str, bool] = {}
    frontier: list[tuple[str, bool]] = [(triggers[0].id, False)]
    visited: set[tuple[str, bool]] = set()

    while frontier:
        node_id, suspended = frontier.pop()
        if (node_id, suspended) in visited:
            continue
        visited.add((node_id, suspended))
        suspended_before[node_id] = suspended_before.get(node_id, True) and suspended

        node = graph.node(node_id)
        spec = get_spec(node.node_type) if node else None
        next_suspended = suspended or bool(spec and spec.suspends)
        frontier.extend((edge.target, next_suspended) for edge in graph.outgoing(node_id))

    for node in graph.nodes:
        if node.node_type != ACTION_SEND_MESSAGE:
            continue
        if suspended_before.get(node.id, False):
            continue
        add(
            ValidationIssue(
                code="conflicts_with_legacy_auto_reply",
                message=(
                    "O atendimento legado já responde automaticamente neste gatilho. "
                    "Enviar aqui produziria duas mensagens. Coloque uma espera ou "
                    "uma aprovação antes do envio."
                ),
                node_id=node.id,
            )
        )
