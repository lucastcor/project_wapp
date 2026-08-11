"""Catálogo de nós v1.

O catálogo é a fonte única do que existe: o validador, o inspector do
frontend e o motor leem daqui. Um nó fora do catálogo é rejeitado — não há
caminho para "tipo desconhecido, tenta executar mesmo assim".

O que **não** entra nesta versão, e por quê:

- execução arbitrária de código, shell, SQL livre, HTTP genérico e leitura de
  filesystem: cada um transforma o motor num executor remoto para quem
  conseguir editar um fluxo, e o modelo de ameaça do multi-tenant não
  suporta isso sem sandbox real;
- loops, fan-out e split/join implícitos: sem eles a execução é uma linha
  única e o custo por evento é previsível. Entram quando houver limite de
  passos, orçamento e visualização próprios.

Cada campo declara `type`, obrigatoriedade e limites. É o mesmo contrato que
o inspector usa para desenhar o formulário, então UI e validação não
divergem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# --- categorias -------------------------------------------------------------------

CATEGORY_TRIGGER = "trigger"
CATEGORY_CONDITION = "condition"
CATEGORY_AI = "ai"
CATEGORY_TIME = "time"
CATEGORY_CONTROL = "control"
CATEGORY_ACTION = "action"
CATEGORY_TERMINAL = "terminal"

# --- alças padrão -----------------------------------------------------------------

HANDLE_NEXT = "next"
HANDLE_TRUE = "true"
HANDLE_FALSE = "false"
HANDLE_APPROVED = "approved"
HANDLE_REJECTED = "rejected"

FIELD_TYPES = frozenset({"string", "text", "number", "boolean", "select", "multiselect"})


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    type: str
    required: bool = False
    options: tuple[str, ...] = ()
    min_value: float | None = None
    max_value: float | None = None
    max_length: int | None = None
    default: Any = None
    help_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "options": list(self.options),
            "min_value": self.min_value,
            "max_value": self.max_value,
            "max_length": self.max_length,
            "default": self.default,
            "help_text": self.help_text,
        }


@dataclass(frozen=True)
class NodeSpec:
    node_type: str
    category: str
    label: str
    description: str
    outputs: tuple[str, ...] = (HANDLE_NEXT,)
    fields: tuple[FieldSpec, ...] = ()
    # Capability da empresa exigida para usar o nó.
    required_capability: str | None = None
    # Efeito externo: passa por adapter server-side e entra no effect ledger.
    produces_effect: bool = False
    # Nó que suspende a execução (espera ou portão humano).
    suspends: bool = False

    @property
    def accepts_input(self) -> bool:
        return self.category != CATEGORY_TRIGGER

    @property
    def is_terminal(self) -> bool:
        return self.category == CATEGORY_TERMINAL

    def field(self, key: str) -> FieldSpec | None:
        for spec in self.fields:
            if spec.key == key:
                return spec
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_type": self.node_type,
            "category": self.category,
            "label": self.label,
            "description": self.description,
            "outputs": list(self.outputs),
            "fields": [spec.as_dict() for spec in self.fields],
            "required_capability": self.required_capability,
            "produces_effect": self.produces_effect,
            "suspends": self.suspends,
            "accepts_input": self.accepts_input,
            "is_terminal": self.is_terminal,
        }


# --- gatilhos ---------------------------------------------------------------------

TRIGGER_MESSAGE_PROCESSED = "trigger.messageProcessed"
TRIGGER_HANDOFF_CREATED = "trigger.handoffCreated"
TRIGGER_APPOINTMENT_BOOKED = "trigger.appointmentBooked"
TRIGGER_SCHEDULE = "trigger.schedule"
TRIGGER_MANUAL = "trigger.manual"

_TRIGGERS: tuple[NodeSpec, ...] = (
    NodeSpec(
        node_type=TRIGGER_MESSAGE_PROCESSED,
        category=CATEGORY_TRIGGER,
        label="Mensagem processada",
        description=(
            "Dispara depois que o orquestrador processou uma mensagem e "
            "persistiu o resultado. O fluxo é pós-atendimento: não substitui "
            "a resposta síncrona."
        ),
    ),
    NodeSpec(
        node_type=TRIGGER_HANDOFF_CREATED,
        category=CATEGORY_TRIGGER,
        label="Handoff criado",
        description="Dispara quando um atendimento é encaminhado para humano.",
    ),
    NodeSpec(
        node_type=TRIGGER_APPOINTMENT_BOOKED,
        category=CATEGORY_TRIGGER,
        label="Agendamento confirmado",
        description="Dispara quando um agendamento é criado com sucesso.",
        required_capability="scheduling",
    ),
    NodeSpec(
        node_type=TRIGGER_SCHEDULE,
        category=CATEGORY_TRIGGER,
        label="Agenda recorrente",
        description="Dispara em uma janela de tempo definida pelo operador.",
        fields=(
            FieldSpec(
                key="cadence",
                label="Cadência",
                type="select",
                required=True,
                options=("hourly", "daily", "weekly"),
                default="daily",
            ),
            FieldSpec(
                key="hour",
                label="Hora (0-23)",
                type="number",
                min_value=0,
                max_value=23,
                default=9,
            ),
        ),
    ),
    NodeSpec(
        node_type=TRIGGER_MANUAL,
        category=CATEGORY_TRIGGER,
        label="Disparo manual",
        description="Executado sob demanda por um operador autorizado.",
    ),
)

# --- condições --------------------------------------------------------------------

CONDITION_CLASSIFICATION = "condition.classification"
CONDITION_CONFIDENCE = "condition.confidence"
CONDITION_PIPELINE_STAGE = "condition.pipelineStage"
CONDITION_CUSTOMER_PROFILE = "condition.customerProfile"
CONDITION_HAS_KNOWLEDGE_SOURCE = "condition.hasKnowledgeSource"
CONDITION_HANDOFF_REQUIRED = "condition.handoffRequired"

_BRANCHING_OUTPUTS = (HANDLE_TRUE, HANDLE_FALSE)

_CONDITIONS: tuple[NodeSpec, ...] = (
    NodeSpec(
        node_type=CONDITION_CLASSIFICATION,
        category=CATEGORY_CONDITION,
        label="Classificação",
        description="Compara a classificação atribuída pelo orquestrador.",
        outputs=_BRANCHING_OUTPUTS,
        fields=(
            FieldSpec(
                key="values",
                label="Classificações aceitas",
                type="multiselect",
                required=True,
                options=(
                    "lead_novo",
                    "duvida_geral",
                    "orcamento",
                    "agendamento",
                    "reclamacao",
                    "pos_venda",
                    "spam",
                ),
            ),
        ),
    ),
    NodeSpec(
        node_type=CONDITION_CONFIDENCE,
        category=CATEGORY_CONDITION,
        label="Confiança",
        description="Compara a confiança da classificação com um limiar.",
        outputs=_BRANCHING_OUTPUTS,
        fields=(
            FieldSpec(
                key="comparator",
                label="Comparador",
                type="select",
                required=True,
                options=("gte", "lte"),
                default="gte",
            ),
            FieldSpec(
                key="threshold",
                label="Limiar",
                type="number",
                required=True,
                min_value=0.0,
                max_value=1.0,
                default=0.7,
            ),
        ),
    ),
    NodeSpec(
        node_type=CONDITION_PIPELINE_STAGE,
        category=CATEGORY_CONDITION,
        label="Estágio do funil",
        description="Verifica o estágio atual do contato no funil.",
        outputs=_BRANCHING_OUTPUTS,
        fields=(
            FieldSpec(
                key="values",
                label="Estágios aceitos",
                type="multiselect",
                required=True,
                options=(
                    "novo_lead",
                    "qualificacao",
                    "proposta",
                    "negociacao",
                    "fechado",
                    "perdido",
                ),
            ),
        ),
    ),
    NodeSpec(
        node_type=CONDITION_CUSTOMER_PROFILE,
        category=CATEGORY_CONDITION,
        label="Perfil do cliente",
        description="Verifica o perfil atribuído ao contato.",
        outputs=_BRANCHING_OUTPUTS,
        fields=(
            FieldSpec(
                key="values",
                label="Perfis aceitos",
                type="multiselect",
                required=True,
                options=("new_customer", "returning_customer", "vip", "unknown"),
            ),
        ),
    ),
    NodeSpec(
        node_type=CONDITION_HAS_KNOWLEDGE_SOURCE,
        category=CATEGORY_CONDITION,
        label="Tem fonte de conhecimento",
        description=(
            "Verdadeiro quando a resposta se apoiou em documento aprovado do "
            "tenant. Útil para separar resposta fundamentada de handoff."
        ),
        outputs=_BRANCHING_OUTPUTS,
    ),
    NodeSpec(
        node_type=CONDITION_HANDOFF_REQUIRED,
        category=CATEGORY_CONDITION,
        label="Precisa de humano",
        description="Verdadeiro quando o orquestrador decidiu encaminhar.",
        outputs=_BRANCHING_OUTPUTS,
    ),
)

# --- IA ---------------------------------------------------------------------------

AI_COMPOSE_MESSAGE = "ai.composeMessage"
AI_EXTRACT_STRUCTURED = "ai.extractStructured"
AI_CLASSIFY_OR_ROUTE = "ai.classifyOrRoute"

_AI_NODES: tuple[NodeSpec, ...] = (
    NodeSpec(
        node_type=AI_COMPOSE_MESSAGE,
        category=CATEGORY_AI,
        label="Compor mensagem",
        description=(
            "Gera um texto reutilizando provider, RAG e contrato de resposta "
            "já existentes. Produz conteúdo; não envia nada."
        ),
        # Sem capacidade exigida: o caminho de composição é o mesmo do
        # atendimento, que toda empresa já tem. O uso de RAG é governado pela
        # configuração de retrieval, não por um gate próprio do nó.
        fields=(
            FieldSpec(
                key="objective",
                label="Objetivo",
                type="text",
                required=True,
                max_length=500,
                help_text="O que a mensagem precisa alcançar.",
            ),
            FieldSpec(
                key="use_knowledge",
                label="Usar base de conhecimento",
                type="boolean",
                default=True,
            ),
        ),
    ),
    NodeSpec(
        node_type=AI_EXTRACT_STRUCTURED,
        category=CATEGORY_AI,
        label="Extrair dados",
        description="Extrai campos estruturados da conversa para uso posterior.",
        fields=(
            FieldSpec(
                key="fields",
                label="Campos",
                type="multiselect",
                required=True,
                options=("nome", "servico_desejado", "data_preferida", "orcamento", "urgencia"),
            ),
        ),
    ),
    NodeSpec(
        node_type=AI_CLASSIFY_OR_ROUTE,
        category=CATEGORY_AI,
        label="Classificar e rotear",
        description="Reclassifica a conversa para escolher o caminho seguinte.",
        outputs=_BRANCHING_OUTPUTS,
        fields=(
            FieldSpec(
                key="target_label",
                label="Rótulo alvo",
                type="string",
                required=True,
                max_length=80,
            ),
        ),
    ),
)

# --- tempo e controle --------------------------------------------------------------

DELAY_WAIT = "delay.wait"
CONTROL_IF = "control.if"
CONTROL_SWITCH = "control.switch"
CONTROL_HUMAN_APPROVAL = "control.humanApproval"

TERMINAL_DONE = "terminal.done"
TERMINAL_ERROR = "terminal.error"
TERMINAL_CANCELED = "terminal.canceled"

TIME_UNITS = ("minutes", "hours", "days")

_TIME_AND_CONTROL: tuple[NodeSpec, ...] = (
    NodeSpec(
        node_type=DELAY_WAIT,
        category=CATEGORY_TIME,
        label="Esperar",
        description=(
            "Suspende a execução por um intervalo. A espera é durável: "
            "sobrevive a reinício do processo porque vira um job com `run_at`."
        ),
        suspends=True,
        fields=(
            FieldSpec(
                key="amount",
                label="Quantidade",
                type="number",
                required=True,
                min_value=1,
                max_value=10_000,
                default=1,
            ),
            FieldSpec(
                key="unit",
                label="Unidade",
                type="select",
                required=True,
                options=TIME_UNITS,
                default="hours",
            ),
        ),
    ),
    NodeSpec(
        node_type=CONTROL_IF,
        category=CATEGORY_CONTROL,
        label="Se",
        description="Ramifica com base num campo do contexto da execução.",
        outputs=_BRANCHING_OUTPUTS,
        fields=(
            FieldSpec(
                key="context_key",
                label="Campo do contexto",
                type="select",
                required=True,
                options=(
                    "classification",
                    "pipeline_stage",
                    "customer_profile",
                    "reply_source",
                    "handoff_required",
                    "has_relevant_source",
                ),
            ),
            FieldSpec(
                key="equals",
                label="Igual a",
                type="string",
                required=True,
                max_length=120,
            ),
        ),
    ),
    NodeSpec(
        node_type=CONTROL_SWITCH,
        category=CATEGORY_CONTROL,
        label="Escolher caminho",
        description=(
            "Ramifica em várias saídas nomeadas. Cada caso vira uma alça; o "
            "que não casar sai por `default`."
        ),
        outputs=(),  # Determinadas pela configuração; ver `switch_outputs`.
        fields=(
            FieldSpec(
                key="context_key",
                label="Campo do contexto",
                type="select",
                required=True,
                options=("classification", "pipeline_stage", "customer_profile"),
            ),
            FieldSpec(
                key="cases",
                label="Casos",
                type="multiselect",
                required=True,
            ),
        ),
    ),
    NodeSpec(
        node_type=CONTROL_HUMAN_APPROVAL,
        category=CATEGORY_CONTROL,
        label="Aprovação humana",
        description=(
            "Estaciona a execução até um operador autorizado decidir. "
            "Aprovação e rejeição saem por alças distintas e ficam auditadas "
            "com o principal real."
        ),
        outputs=(HANDLE_APPROVED, HANDLE_REJECTED),
        suspends=True,
        fields=(
            FieldSpec(
                key="title",
                label="Título",
                type="string",
                required=True,
                max_length=120,
            ),
            FieldSpec(
                key="summary",
                label="Resumo para quem decide",
                type="text",
                max_length=600,
            ),
            FieldSpec(
                key="expires_in_hours",
                label="Expira em (horas)",
                type="number",
                min_value=1,
                max_value=720,
                help_text="Sem valor, o portão não expira.",
            ),
        ),
    ),
    NodeSpec(
        node_type=TERMINAL_DONE,
        category=CATEGORY_TERMINAL,
        label="Concluído",
        description="Encerra a execução com sucesso.",
        outputs=(),
    ),
    NodeSpec(
        node_type=TERMINAL_ERROR,
        category=CATEGORY_TERMINAL,
        label="Erro",
        description="Encerra a execução marcando falha de negócio.",
        outputs=(),
    ),
    NodeSpec(
        node_type=TERMINAL_CANCELED,
        category=CATEGORY_TERMINAL,
        label="Cancelado",
        description="Encerra a execução por cancelamento previsto no fluxo.",
        outputs=(),
    ),
)

# --- ações ------------------------------------------------------------------------

ACTION_CREATE_HANDOFF = "action.createHandoff"
ACTION_CREATE_REMINDER = "action.createReminder"
ACTION_SEND_MESSAGE = "action.sendMessage"
ACTION_UPDATE_PIPELINE_STAGE = "action.updatePipelineStage"

_ACTIONS: tuple[NodeSpec, ...] = (
    NodeSpec(
        node_type=ACTION_CREATE_HANDOFF,
        category=CATEGORY_ACTION,
        label="Criar handoff",
        description="Encaminha o atendimento para a fila humana.",
        produces_effect=True,
        fields=(
            FieldSpec(
                key="reason",
                label="Motivo",
                type="string",
                required=True,
                max_length=200,
            ),
            FieldSpec(
                key="priority",
                label="Prioridade",
                type="select",
                options=("low", "normal", "high"),
                default="normal",
            ),
        ),
    ),
    NodeSpec(
        node_type=ACTION_CREATE_REMINDER,
        category=CATEGORY_ACTION,
        label="Criar lembrete",
        description="Cria um lembrete interno para a equipe.",
        produces_effect=True,
        required_capability="reminders",
        fields=(
            FieldSpec(
                key="message",
                label="Mensagem",
                type="text",
                required=True,
                max_length=500,
            ),
            FieldSpec(
                key="due_in_hours",
                label="Vence em (horas)",
                type="number",
                required=True,
                min_value=1,
                max_value=8760,
                default=24,
            ),
        ),
    ),
    NodeSpec(
        node_type=ACTION_SEND_MESSAGE,
        category=CATEGORY_ACTION,
        label="Enviar mensagem",
        description=(
            "Enfileira uma mensagem de saída pelo adapter do canal. O envio "
            "passa pelo effect ledger: um retry não manda duas vezes."
        ),
        produces_effect=True,
        required_capability="outbound",
        fields=(
            FieldSpec(
                key="source",
                label="Origem do texto",
                type="select",
                required=True,
                options=("ai_node", "static"),
                default="ai_node",
                help_text="`ai_node` usa o texto do nó de IA anterior.",
            ),
            FieldSpec(
                key="text",
                label="Texto fixo",
                type="text",
                max_length=1000,
            ),
        ),
    ),
    NodeSpec(
        node_type=ACTION_UPDATE_PIPELINE_STAGE,
        category=CATEGORY_ACTION,
        label="Mudar estágio do funil",
        description="Move o contato para outro estágio.",
        produces_effect=True,
        fields=(
            FieldSpec(
                key="stage",
                label="Novo estágio",
                type="select",
                required=True,
                options=(
                    "novo_lead",
                    "qualificacao",
                    "proposta",
                    "negociacao",
                    "fechado",
                    "perdido",
                ),
            ),
        ),
    ),
)


NODE_CATALOG: dict[str, NodeSpec] = {
    spec.node_type: spec
    for spec in (*_TRIGGERS, *_CONDITIONS, *_AI_NODES, *_TIME_AND_CONTROL, *_ACTIONS)
}

TRIGGER_TYPES: frozenset[str] = frozenset(
    node_type
    for node_type, spec in NODE_CATALOG.items()
    if spec.category == CATEGORY_TRIGGER
)
TERMINAL_TYPES: frozenset[str] = frozenset(
    node_type
    for node_type, spec in NODE_CATALOG.items()
    if spec.category == CATEGORY_TERMINAL
)
EFFECT_TYPES: frozenset[str] = frozenset(
    node_type for node_type, spec in NODE_CATALOG.items() if spec.produces_effect
)


def get_spec(node_type: str) -> NodeSpec | None:
    return NODE_CATALOG.get(node_type)


def switch_outputs(config: dict[str, Any]) -> tuple[str, ...]:
    """Alças de um `control.switch`, derivadas dos casos configurados."""
    cases = config.get("cases") or []
    named = tuple(
        str(case).strip() for case in cases if isinstance(case, (str, int)) and str(case).strip()
    )
    return (*named, "default")


def outputs_for(node_type: str, config: dict[str, Any]) -> tuple[str, ...]:
    """Alças efetivas de um nó, já considerando a configuração."""
    if node_type == CONTROL_SWITCH:
        return switch_outputs(config)
    spec = get_spec(node_type)
    return spec.outputs if spec else ()


def describe_catalog() -> list[dict[str, Any]]:
    """Catálogo serializado para o frontend, em ordem estável."""
    return [
        NODE_CATALOG[node_type].as_dict()
        for node_type in sorted(NODE_CATALOG, key=lambda key: (NODE_CATALOG[key].category, key))
    ]
