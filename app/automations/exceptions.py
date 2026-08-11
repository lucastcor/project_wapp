"""Erros do domínio de automações."""

from __future__ import annotations


class AutomationError(Exception):
    """Base de todos os erros do Automation Studio."""


class GraphValidationError(AutomationError):
    """Grafo inválido. Carrega os problemas encontrados, não só o primeiro."""

    def __init__(self, issues: list[dict[str, object]]) -> None:
        self.issues = issues
        summary = "; ".join(str(issue.get("message")) for issue in issues[:3])
        suffix = f" (+{len(issues) - 3})" if len(issues) > 3 else ""
        super().__init__(f"Automation graph is invalid: {summary}{suffix}")


class WorkflowConflictError(AutomationError):
    """Rascunho editado a partir de uma revisão que já não é a corrente."""

    def __init__(self, expected_revision: int, actual_revision: int) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            f"Draft revision {expected_revision} is stale; current revision is {actual_revision}."
        )


class WorkflowNotFoundError(AutomationError):
    """Workflow inexistente **ou** de outra empresa.

    A mesma exceção nos dois casos é deliberada: distinguir permitiria
    enumerar workflows de outros tenants.
    """


class ExecutionNotFoundError(AutomationError):
    pass


class ApprovalGateError(AutomationError):
    pass


class PermanentExecutionError(AutomationError):
    """Falha que não melhora com retry: configuração inválida, nó inexistente.

    O motor marca a execução como `failed` sem reagendar.
    """


class RetryableExecutionError(AutomationError):
    """Falha transitória: indisponibilidade, timeout, contenção.

    O motor reagenda com backoff até `max_attempts`.
    """
