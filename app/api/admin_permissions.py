"""Explicit endpoint-to-permission matrix for the administrative API.

Role model (see ``ADMIN_ROLE_HIERARCHY``):

* ``viewer``     — read-only, may not mutate any administrative resource.
* ``operator``   — day-to-day operational writes (agenda, contacts, outbound,
  reminders, curation, analytics outcomes).
* ``superadmin`` — privileged company, onboarding, security and billing
  administration.

Every mutating (POST/PUT/PATCH/DELETE) route under ``/admin`` must have an entry
here; ``tests/test_admin_rbac_matrix.py`` walks the running application and
fails when a route is missing, extra, or wired to a different role than the one
declared below. Authentication remains generic (``require_admin_access``); it
never implies authorization to mutate.
"""

from typing import Final

VIEWER: Final = "viewer"
OPERATOR: Final = "operator"
SUPERADMIN: Final = "superadmin"

# (HTTP method, route path) -> minimum required role.
ADMIN_ENDPOINT_PERMISSIONS: Final[dict[tuple[str, str], str]] = {
    ("POST", "/admin/inbound/jobs/{job_id}/resolve"): OPERATOR,
    # Posse humana da conversa (P06): quem atende assume e devolve.
    ("POST", "/admin/conversations/{contact_id}/take"): OPERATOR,
    ("POST", "/admin/conversations/{contact_id}/release"): OPERATOR,
    # Automation Studio.
    #
    # Autoria (criar, editar, publicar, ativar) exige superadmin: publicar põe
    # um fluxo no ar em nome da empresa. Operação do dia a dia (disparar,
    # repetir, cancelar, decidir aprovação) exige operator, que é quem cuida
    # do atendimento. Rodar o worker é operação de plataforma.
    ("POST", "/admin/automations/workflows"): SUPERADMIN,
    ("PATCH", "/admin/automations/workflows/{workflow_id}"): SUPERADMIN,
    ("PUT", "/admin/automations/workflows/{workflow_id}/draft"): SUPERADMIN,
    # Validar e simular são POST por causa do corpo, não por causa de
    # efeito. Ainda assim exigem operator: a matriz mantém a invariante de
    # que nenhuma mutação HTTP é gravável por viewer, e abrir uma exceção
    # aqui trocaria uma regra verificável por uma lista de casos especiais.
    # Um viewer continua lendo fluxos, execuções e aprovações.
    ("POST", "/admin/automations/workflows/validate"): OPERATOR,
    ("POST", "/admin/automations/workflows/simulate"): OPERATOR,
    ("POST", "/admin/automations/workflows/{workflow_id}/publish"): SUPERADMIN,
    ("POST", "/admin/automations/workflows/{workflow_id}/state"): SUPERADMIN,
    ("POST", "/admin/automations/workflows/{workflow_id}/trigger"): OPERATOR,
    ("POST", "/admin/automations/executions/{execution_id}/retry"): OPERATOR,
    ("POST", "/admin/automations/executions/{execution_id}/cancel"): OPERATOR,
    ("POST", "/admin/automations/approvals/{gate_id}/claim"): OPERATOR,
    ("POST", "/admin/automations/approvals/{gate_id}/decide"): OPERATOR,
    ("POST", "/admin/automations/worker/run-once"): SUPERADMIN,
    # Plataforma multiempresa: conhecimento, datasets, avaliação, modelos,
    # importação de conversas e configuração efetiva.
    #
    # A divisão segue quem responde pelo resultado. Curadoria e operação do
    # dia a dia (importar conversa, revisar candidato, montar dataset, rodar
    # avaliação, publicar documento) são operator. O que muda o que a produção
    # usa — ativar modelo, publicar configuração, vincular canal — é
    # superadmin: são as ações que alteram a resposta dada ao cliente final.
    #
    # `knowledge/search` e `evaluation/compare` são leituras que usam POST por
    # causa do corpo. Ainda assim exigem operator: a matriz mantém a invariante
    # de que nenhuma mutação HTTP é gravável por viewer, e trocar uma regra
    # verificável por uma lista de exceções sai mais caro que o incômodo.
    ("POST", "/admin/channel-bindings"): SUPERADMIN,
    ("DELETE", "/admin/channel-bindings/{binding_id}"): SUPERADMIN,
    # Canal WhatsApp (P07): cadastrar template aprovado é configuração da
    # empresa; registrar consentimento de um contato é operação do dia a dia.
    ("PUT", "/admin/whatsapp/templates"): SUPERADMIN,
    ("PUT", "/admin/whatsapp/contacts/{contact_id}/consents/{purpose}"): OPERATOR,
    ("POST", "/admin/company-config/bootstrap"): SUPERADMIN,
    ("POST", "/admin/company-config/publish"): SUPERADMIN,
    ("POST", "/admin/company-config/versions"): SUPERADMIN,
    ("POST", "/admin/conversation-import"): OPERATOR,
    ("POST", "/admin/dataset-versions"): OPERATOR,
    ("POST", "/admin/dataset-versions/{dataset_version_id}/freeze"): OPERATOR,
    ("POST", "/admin/dataset-versions/{dataset_version_id}/rebuild"): OPERATOR,
    ("POST", "/admin/evaluation/cases"): OPERATOR,
    ("POST", "/admin/evaluation/compare"): OPERATOR,
    ("POST", "/admin/evaluation/runs"): OPERATOR,
    ("POST", "/admin/finetuning/preflight"): OPERATOR,
    ("POST", "/admin/knowledge/documents"): OPERATOR,
    ("POST", "/admin/knowledge/documents/{document_id}/approve"): OPERATOR,
    ("POST", "/admin/knowledge/documents/{document_id}/archive"): OPERATOR,
    ("POST", "/admin/knowledge/import-legacy"): SUPERADMIN,
    ("POST", "/admin/knowledge/search"): OPERATOR,
    ("POST", "/admin/models"): SUPERADMIN,
    ("POST", "/admin/models/rollback"): SUPERADMIN,
    ("POST", "/admin/models/{model_id}/activate"): SUPERADMIN,
    ("POST", "/admin/training-candidates/{candidate_id}/review"): OPERATOR,
    # Security / audit administration
    ("POST", "/admin/audit-events/cleanup"): SUPERADMIN,
    # Company and onboarding administration
    ("POST", "/admin/companies"): SUPERADMIN,
    ("PUT", "/admin/company-context"): SUPERADMIN,
    ("POST", "/admin/onboarding/setup"): SUPERADMIN,
    ("POST", "/admin/calendar/integrations"): SUPERADMIN,
    ("POST", "/admin/calendar/test-connection"): SUPERADMIN,
    ("PUT", "/admin/whatsapp/bindings"): SUPERADMIN,
    ("POST", "/admin/whatsapp/bindings/{binding_id}/state"): SUPERADMIN,
    ("POST", "/admin/whatsapp/test-connection"): SUPERADMIN,
    # Billing administration (tenant-owner billing is not implemented yet)
    ("POST", "/admin/billing/trial"): SUPERADMIN,
    ("POST", "/admin/billing/activate"): SUPERADMIN,
    ("POST", "/admin/billing/cancel"): SUPERADMIN,
    ("POST", "/admin/billing/reactivate"): SUPERADMIN,
    ("POST", "/admin/billing/change-plan"): SUPERADMIN,
    ("POST", "/admin/billing/usage/record"): SUPERADMIN,
    ("POST", "/admin/billing/checkout"): SUPERADMIN,
    ("POST", "/admin/billing/portal"): SUPERADMIN,
    # Operational writes
    ("POST", "/admin/calendar/availability/query"): OPERATOR,
    ("POST", "/admin/calendar/queue"): OPERATOR,
    ("POST", "/admin/calendar/bookings"): OPERATOR,
    ("POST", "/admin/calendar/bookings/{booking_id}/reschedule"): OPERATOR,
    ("POST", "/admin/calendar/bookings/{booking_id}/cancel"): OPERATOR,
    ("POST", "/admin/calendar/queue-offers/{offer_id}/respond"): OPERATOR,
    ("POST", "/admin/reminders"): OPERATOR,
    ("POST", "/admin/reminders/dispatch-due"): OPERATOR,
    ("PATCH", "/admin/contacts/{contact_id}"): OPERATOR,
    ("POST", "/admin/pending-questions/{question_id}/answer"): OPERATOR,
    ("POST", "/admin/message-examples"): OPERATOR,
    ("POST", "/admin/training-import"): OPERATOR,
    ("POST", "/admin/outbound-messages/{outbound_message_id}/mark-sent"): OPERATOR,
    ("POST", "/admin/outbound-messages/{outbound_message_id}/dispatch"): OPERATOR,
    ("POST", "/admin/outbound-messages/dispatch-pending"): OPERATOR,
    ("POST", "/admin/finetuning/jobs"): OPERATOR,
    ("POST", "/admin/analytics/bookings/{booking_id}/outcome"): OPERATOR,
    ("POST", "/admin/analytics/bookings/{booking_id}/cancellation-reason"): OPERATOR,
    ("POST", "/admin/simulator/messages"): OPERATOR,
}

MUTATING_HTTP_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_required_role(method: str, path: str) -> str:
    """Return the role required to call a mutating admin endpoint."""
    try:
        return ADMIN_ENDPOINT_PERMISSIONS[(method.upper(), path)]
    except KeyError as exc:  # pragma: no cover - guarded by the matrix test
        raise KeyError(f"No admin permission declared for {method.upper()} {path}") from exc
