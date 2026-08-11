class ApplicationError(Exception):
    """Erro base da aplicação."""


class InvalidMessageError(ApplicationError):
    """Erro para mensagem inválida."""


class ProviderError(ApplicationError):
    """Erro relacionado ao provider de resposta."""

class TenantResourceNotFoundError(ApplicationError):
    """Recurso não existe **dentro do escopo autorizado**.

    A distinção importa: "não existe" e "existe, mas é de outro tenant" têm
    que ser indistinguíveis de fora. Diferenciar transforma qualquer rota com
    id sequencial num oráculo de enumeração — e os ids deste produto são
    sequenciais globais.

    As rotas mapeiam esta exceção para **404**. Não para 400: a requisição está
    bem formada. Não para 403: isso revelaria que o recurso existe.
    """
