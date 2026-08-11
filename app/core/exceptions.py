class ApplicationError(Exception):
    """Erro base da aplicação."""


class InvalidMessageError(ApplicationError):
    """Erro para mensagem inválida."""


class ProviderError(ApplicationError):
    """Erro relacionado ao provider de resposta."""
