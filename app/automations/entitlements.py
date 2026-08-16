"""Entitlements do Automation Studio, derivados do plano contratado.

O catálogo declara `required_capability` por nó. Este módulo é o único lugar
que traduz capability em direito contratual, e a tradução é para o plano
canônico de `app/services/billing_service.py` — não para uma segunda tabela de
features paralela ao billing, que divergiria no dia em que um plano mudasse.

## Onde a checagem acontece, e por quê

A capability é verificada ao **publicar e ao ativar**, não ao executar.

Verificar só na execução significa que o fluxo entra no ar, roda metade dos
passos e falha no nó de envio — deixando efeito parcial aplicado e uma
execução em `failed` que o operador não pediu. Verificar na publicação
transforma o mesmo erro numa mensagem de formulário, antes de existir
qualquer execução.

O **orçamento de execução** é o oposto: só pode ser cobrado por execução, e
portanto é medido em runtime, via `billing_service.consume_usage`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.automations.catalog import NODE_CATALOG
from app.automations.domain import WorkflowGraph

# capability do catálogo -> métrica de limite no plano.
#
# Um nó que produz efeito externo consome o mesmo direito que a funcionalidade
# equivalente fora da automação: automatizar o envio não pode ser um caminho
# lateral para exceder o que o plano permite enviar.
CAPABILITY_TO_PLAN_LIMIT: dict[str, str] = {
    "outbound": "outbound_campaigns",
    "reminders": "reminders_per_booking",
    "scheduling": "bookings",
}

# Direito de usar o Automation Studio, e o teto de execuções por ciclo.
AUTOMATION_FEATURE = "automations"
AUTOMATION_EXECUTION_METRIC = "automation_executions"
AUTOMATION_WORKFLOW_LIMIT = "automation_workflows"


class EntitlementError(Exception):
    """O plano contratado não cobre o que o fluxo pede."""


@dataclass(frozen=True)
class CapabilityRequirement:
    """Uma capability exigida pelo grafo, e quais nós a exigem."""

    capability: str
    plan_limit: str
    node_ids: tuple[str, ...]
    node_types: tuple[str, ...]


def required_capabilities(graph: WorkflowGraph) -> tuple[CapabilityRequirement, ...]:
    """Capabilities que este grafo exige, agrupadas por capability.

    Agrupa por capability e não por nó para que a mensagem de erro diga "o
    plano não cobre envio ativo, usado nos nós A e C" em vez de repetir o
    mesmo texto uma vez por nó.
    """
    grouped: dict[str, list[tuple[str, str]]] = {}
    for node in graph.nodes:
        spec = NODE_CATALOG.get(node.node_type)
        if spec is None or not spec.required_capability:
            continue
        grouped.setdefault(spec.required_capability, []).append((node.id, node.node_type))

    requirements: list[CapabilityRequirement] = []
    for capability in sorted(grouped):
        entries = sorted(grouped[capability])
        requirements.append(
            CapabilityRequirement(
                capability=capability,
                plan_limit=CAPABILITY_TO_PLAN_LIMIT.get(capability, capability),
                node_ids=tuple(node_id for node_id, _ in entries),
                node_types=tuple(sorted({node_type for _, node_type in entries})),
            )
        )
    return tuple(requirements)


def missing_capabilities(
    graph: WorkflowGraph,
    *,
    plan_limits: dict[str, int],
) -> tuple[CapabilityRequirement, ...]:
    """As capabilities exigidas que o plano não cobre.

    Um limite ausente ou <= 0 significa "não faz parte do plano". É a mesma
    convenção de `billing_service.check_limit`, deliberadamente: duas leituras
    diferentes de "limite zero" é como um plano acaba permitindo por um
    caminho o que nega pelo outro.
    """
    return tuple(
        requirement
        for requirement in required_capabilities(graph)
        if int(plan_limits.get(requirement.plan_limit, 0) or 0) <= 0
    )


def describe_missing(requirements: tuple[CapabilityRequirement, ...]) -> str:
    """Mensagem de erro que nomeia os nós, para o editor poder destacá-los."""
    parts = [
        f"{requirement.capability} (nós: {', '.join(requirement.node_ids)})"
        for requirement in requirements
    ]
    return "; ".join(parts)
