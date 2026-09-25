"""Conexão da agenda Google de uma empresa por OAuth (authorization code).

O fluxo tem duas metades que rodam em requisições diferentes:

1. `build_authorization_url`: o painel pede a URL de consentimento. Gravamos
   um `state` ligado à empresa, ao usuário e ao modo de autenticação, e é
   ele que carrega a empresa até o retorno — a URL de callback não aceita
   `company_id` de parâmetro nenhum.
2. `complete_callback`: o Google devolve `code` + `state` numa navegação de
   topo. Consumimos o state (uma vez só), conferimos que a sessão que chegou
   é a mesma que iniciou o fluxo, trocamos o código pelo refresh token e o
   guardamos cifrado dentro de `config_json`. Client id e secret do projeto
   Google vêm das variáveis de ambiente e não são copiados para a linha.

Todo erro do callback vira um redirect para o painel com `google=error&reason=`
em vez de uma página HTML: a resposta nunca renderiza dado do Google e o
painel traduz o código para o dono do negócio.

`GoogleOAuthClient` isola as chamadas de rede (troca do código, revogação,
listagem de calendários) para que a suíte injete um cliente falso e nada
aqui precise de credencial real.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from app.core.config import is_missing_or_placeholder, is_production_like, settings
from app.core.credential_vault import (
    CredentialVaultError,
    decrypt_secret,
    encrypt_secret,
    is_encrypted,
    vault_is_configured,
)
from app.core.logging import get_logger, get_request_context, log_event
from app.services.calendar_service import CALENDAR_SCOPE, public_calendar_integration
from app.storage import google_oauth_repository as oauth_repository
from app.storage.repository import RuntimeRepository

logger = get_logger(__name__)

PROVIDER = oauth_repository.GOOGLE_CALENDAR_PROVIDER
CALLBACK_PATH = "/platform/integrations/google/callback"
GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint público, não credencial
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
DEFAULT_CALENDAR_ID = "primary"
MAX_CALENDAR_ID_LENGTH = 512

# Papéis na empresa que podem conectar a agenda dela (sessão de identidade).
CONNECTING_IDENTITY_ROLES = frozenset({"owner", "admin"})

# Motivos que o callback devolve ao painel. Fechado de propósito: o painel
# traduz cada um, e um motivo fora da lista viraria texto sem tradução.
CALLBACK_ERROR_REASONS = frozenset({
    "state_invalid",
    "forbidden",
    "exchange_failed",
    "no_refresh_token",
    "vault_unavailable",
    "access_denied",
})


class GoogleOAuthError(Exception):
    """Erro do fluxo com um `reason` estável e um `detail` em pt-BR."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class GoogleOAuthConfigError(GoogleOAuthError):
    """A instalação não tem client id/secret ou URL pública para o OAuth."""

    def __init__(self, detail: str) -> None:
        super().__init__("not_configured", detail)


class GoogleOAuthNotConnectedError(GoogleOAuthError):
    """A empresa não tem integração Google ativa para operar."""

    def __init__(self, detail: str = "Nenhuma agenda Google conectada para esta empresa.") -> None:
        super().__init__("not_connected", detail)


class GoogleOAuthClientError(GoogleOAuthError):
    """Falha ao falar com o Google. Nunca carrega token nem o corpo cru."""

    def __init__(self, reason: str, detail: str, *, provider_code: str | None = None) -> None:
        super().__init__(reason, detail)
        self.provider_code = provider_code


def _has_real_value(value: str | None) -> bool:
    return not is_missing_or_placeholder(value)


def _public_url() -> str:
    public = str(settings.platform_public_url or "").strip()
    if not _has_real_value(public):
        # Sem URL pública o redirect fica relativo: o callback é servido pelo
        # mesmo host do painel, então o navegador resolve certo.
        return ""
    return public.rstrip("/")


def _resolve_redirect_url() -> str | None:
    explicit = str(settings.google_oauth_redirect_url or "").strip()
    if _has_real_value(explicit):
        return explicit
    public = _public_url()
    if public:
        return public + CALLBACK_PATH
    return None


def google_oauth_config() -> dict[str, Any]:
    """O que o painel precisa para decidir se mostra "Conectar Google Agenda".

    Só ids públicos e o motivo do que falta; o client secret nunca sai daqui.
    """
    missing: list[str] = []
    if not _has_real_value(settings.google_calendar_client_id):
        missing.append("GOOGLE_CALENDAR_CLIENT_ID")
    if not _has_real_value(settings.google_calendar_client_secret):
        missing.append("GOOGLE_CALENDAR_CLIENT_SECRET")
    redirect_url = _resolve_redirect_url()
    if redirect_url is None:
        missing.append("PLATFORM_PUBLIC_URL (ou GOOGLE_OAUTH_REDIRECT_URL)")
    if not (vault_is_configured() or not is_production_like()):
        missing.append("CREDENTIALS_ENCRYPTION_KEY")
    enabled = not missing
    return {
        "enabled": enabled,
        "reason": None if enabled else f"Conexão pelo painel desabilitada: configure {', '.join(missing)}.",
        "redirect_url": redirect_url,
    }


def _require_redirect_url() -> str:
    config = google_oauth_config()
    if not config["enabled"] or not config["redirect_url"]:
        raise GoogleOAuthConfigError(str(config["reason"]))
    return str(config["redirect_url"])


def build_authorization_url(
    *,
    company_id: int,
    user_id: int | None,
    actor: str | None,
    auth_mode: str | None,
) -> str:
    """URL de consentimento do Google com um `state` recém-gravado.

    `access_type=offline` + `prompt=consent` são o que faz o Google devolver um
    refresh token também em reconexões; sem `prompt=consent` a segunda
    autorização da mesma conta vem sem ele.
    """
    redirect_url = _require_redirect_url()
    if RuntimeRepository().get_company(int(company_id)) is None:
        # O token de plataforma chega com qualquer inteiro positivo na query;
        # `oauth_states.company_id` referencia `companies(id)`, e o INSERT
        # quebraria com 500 em vez de dizer que a empresa não existe.
        raise GoogleOAuthError("company_not_found", "Empresa não encontrada.")
    # Limpeza oportunista: states vencidos não têm outro momento para sair.
    oauth_repository.purge_expired_states()
    state = oauth_repository.create_state(
        provider=PROVIDER,
        company_id=int(company_id),
        user_id=user_id,
        actor=actor,
        auth_mode=auth_mode,
    )
    params = {
        "client_id": str(settings.google_calendar_client_id),
        "redirect_uri": redirect_url,
        "response_type": "code",
        "scope": CALENDAR_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    log_event(
        logger,
        logging.INFO,
        "google_oauth.authorization_started",
        company_id=int(company_id),
        auth_mode=auth_mode,
    )
    return f"{GOOGLE_AUTHORIZATION_URL}?{urlencode(params)}"


class GoogleOAuthClient:
    """Chamadas de rede do fluxo. Substituível por um duplo nos testes."""

    def __init__(self, timeout: float = 20) -> None:
        self.timeout = timeout

    def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        import requests

        try:
            response = requests.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": str(settings.google_calendar_client_id or ""),
                    "client_secret": str(settings.google_calendar_client_secret or ""),
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GoogleOAuthClientError(
                "exchange_failed",
                "Não foi possível falar com o Google para confirmar a autorização.",
            ) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code != 200 or not isinstance(payload, dict) or payload.get("error"):
            # Só o código de erro do Google ("invalid_grant") sai para o log;
            # o corpo pode conter tokens e nunca é registrado nem propagado.
            error_code = str(payload.get("error") or "") if isinstance(payload, dict) else ""
            log_event(
                logger,
                logging.WARNING,
                "google_oauth.exchange_failed",
                status_code=response.status_code,
                provider_code=error_code or None,
            )
            raise GoogleOAuthClientError(
                "exchange_failed",
                "O Google não confirmou a autorização.",
                provider_code=error_code or None,
            )
        return payload

    def revoke(self, token: str) -> bool:
        import requests

        try:
            response = requests.post(
                GOOGLE_REVOKE_URL,
                data={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GoogleOAuthClientError(
                "revoke_failed",
                "Não foi possível pedir ao Google que revogue o acesso.",
            ) from exc
        if response.status_code != 200:
            raise GoogleOAuthClientError(
                "revoke_failed",
                "O Google não confirmou a revogação do acesso.",
                provider_code=str(response.status_code),
            )
        return True

    def list_calendars(self, refresh_token: str) -> list[dict[str, Any]]:
        try:
            from google.oauth2.credentials import Credentials  # type: ignore
            from googleapiclient.discovery import build  # type: ignore
        except ImportError as exc:
            raise GoogleOAuthClientError(
                "calendar_list_failed",
                "Google Agenda requer google-auth e google-api-python-client instalados.",
            ) from exc

        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=GOOGLE_TOKEN_URL,
            client_id=str(settings.google_calendar_client_id or ""),
            client_secret=str(settings.google_calendar_client_secret or ""),
            scopes=[CALENDAR_SCOPE],
        )
        try:
            service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
            payload = service.calendarList().list().execute()
        except Exception as exc:
            # A exceção da biblioteca pode ecoar a requisição; registramos só o tipo.
            log_event(
                logger,
                logging.WARNING,
                "google_oauth.calendar_list_failed",
                error_type=type(exc).__name__,
            )
            raise GoogleOAuthClientError(
                "calendar_list_failed",
                "Não foi possível listar os calendários da conta Google.",
            ) from exc
        calendars: list[dict[str, Any]] = []
        for item in payload.get("items") or []:
            calendar_id = str(item.get("id") or "").strip()
            if not calendar_id:
                continue
            calendars.append(
                {
                    "id": calendar_id,
                    "summary": str(item.get("summary") or calendar_id),
                    "primary": bool(item.get("primary")),
                    "access_role": item.get("accessRole"),
                }
            )
        return calendars


def _redirect(company_id: int | None, **params: str) -> str:
    query: dict[str, str] = {"view": "agenda"}
    if company_id is not None:
        query["company_id"] = str(int(company_id))
    query.update(params)
    return f"{_public_url()}/platform?{urlencode(query)}"


def _error_result(company_id: int | None, reason: str, **log_fields: Any) -> dict[str, Any]:
    if reason not in CALLBACK_ERROR_REASONS:
        reason = "exchange_failed"
    log_event(
        logger,
        logging.WARNING,
        "google_oauth.callback_failed",
        reason=reason,
        company_id=company_id,
        **log_fields,
    )
    return {
        "company_id": company_id,
        "reason": reason,
        "redirect_to": _redirect(company_id, google="error", reason=reason),
    }


def _revoke_quietly(
    client: GoogleOAuthClient,
    token: str,
    *,
    company_id: int,
    context: str,
    integration_id: int | None = None,
) -> bool:
    """Revoga um token no Google; falha vira aviso no log, nunca erro.

    Serve tanto à desconexão quanto ao callback que trocou o código mas não
    conseguiu guardar o token: sem revogar, o Google mantém uma concessão viva
    que nenhuma linha referencia, e cada nova tentativa (`prompt=consent`)
    acumula mais uma na conta do dono.
    """
    try:
        return bool(client.revoke(token))
    except GoogleOAuthClientError as exc:
        log_event(
            logger,
            logging.WARNING,
            "google_oauth.revoke_failed",
            company_id=int(company_id),
            integration_id=integration_id,
            context=context,
            provider_code=exc.provider_code,
        )
        return False


def _session_matches_state(state_row: dict[str, Any], session: dict[str, Any]) -> bool:
    """A sessão que chegou ao callback é a mesma que iniciou a autorização.

    Sessão de identidade: mesmo usuário, e ainda owner/admin da empresa (um
    papel rebaixado no meio do fluxo não conecta). Token de plataforma: o
    state não tem usuário, então exige uma sessão de plataforma superadmin.
    """
    company_id = int(state_row["company_id"])
    state_user_id = state_row.get("user_id")
    if state_user_id is not None:
        if session.get("auth_mode") != "identity":
            return False
        session_user_id = session.get("user_id")
        if session_user_id is None or int(session_user_id) != int(state_user_id):
            return False
        roles = session.get("membership_roles") or {}
        role = roles.get(company_id, roles.get(str(company_id)))
        return str(role or "") in CONNECTING_IDENTITY_ROLES
    return session.get("auth_mode") != "identity" and str(session.get("role") or "") == "superadmin"


def _primary_account_email(calendars: list[dict[str, Any]]) -> str | None:
    for calendar in calendars:
        if calendar.get("primary"):
            return str(calendar.get("id") or "") or None
    return None


def _persist_connection(
    *,
    company_id: int,
    encrypted_refresh_token: str,
    account_email: str | None,
    actor: str | None,
) -> int:
    """Grava a conexão reutilizando a linha ativa quando ela existe.

    Manter o `integration_id` importa: os bookings apontam para ele e
    `reconcile_pending_reservations` reconcilia por integração.
    """
    config = {"refresh_token": encrypted_refresh_token, "calendar_id": DEFAULT_CALENDAR_ID}
    connected_at = datetime.now(UTC).isoformat()
    existing = oauth_repository.get_active_google_integration(company_id)
    if existing:
        integration_id = int(existing["id"])
    else:
        integration_id = RuntimeRepository().create_calendar_integration(
            company_id=company_id,
            provider=PROVIDER,
            external_calendar_id=DEFAULT_CALENDAR_ID,
            config=config,
            active=True,
        )
    oauth_repository.update_calendar_integration(
        integration_id,
        company_id=company_id,
        external_calendar_id=DEFAULT_CALENDAR_ID,
        config=config,
        active=True,
        connection_source="oauth",
        connected_account_email=account_email,
        connected_at=connected_at,
        connected_by=actor,
    )
    oauth_repository.deactivate_calendar_integrations(company_id, except_id=integration_id)
    return integration_id


def complete_callback(
    *,
    code: str | None,
    state: str | None,
    session: dict[str, Any],
    client: GoogleOAuthClient | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Segunda metade do fluxo. Sempre devolve `redirect_to`; nunca levanta.

    O state é consumido antes de qualquer outra verificação, inclusive quando
    o Google devolveu `error`: um state que sobrevive a um callback falho
    poderia ser reaproveitado.
    """
    state_row = oauth_repository.consume_state(str(state or ""), PROVIDER)
    if state_row is None:
        return _error_result(None, "state_invalid")
    company_id = int(state_row["company_id"])
    actor = str(session.get("actor") or state_row.get("actor") or "platform-user")

    if not _session_matches_state(state_row, session):
        return _error_result(company_id, "forbidden")
    if error:
        reason = "access_denied" if str(error).strip().lower() == "access_denied" else "exchange_failed"
        return _error_result(company_id, reason, provider_code=str(error)[:64])
    if not str(code or "").strip():
        return _error_result(company_id, "exchange_failed", provider_code="missing_code")

    try:
        redirect_uri = _require_redirect_url()
    except GoogleOAuthConfigError:
        return _error_result(company_id, "exchange_failed", provider_code="not_configured")

    oauth_client = client or GoogleOAuthClient()
    try:
        tokens = oauth_client.exchange_code(str(code).strip(), redirect_uri)
    except GoogleOAuthClientError as exc:
        return _error_result(company_id, "exchange_failed", provider_code=exc.provider_code)

    refresh_token = str(tokens.get("refresh_token") or "").strip()
    if not refresh_token:
        return _error_result(company_id, "no_refresh_token")

    # Daqui em diante o Google já emitiu o token: qualquer saída sem gravá-lo
    # revoga antes, para não deixar concessão órfã na conta do dono.
    try:
        calendars = oauth_client.list_calendars(refresh_token)
    except GoogleOAuthClientError as exc:
        _revoke_quietly(oauth_client, refresh_token, company_id=company_id, context="calendar_list_failed")
        return _error_result(company_id, "exchange_failed", provider_code=exc.reason)
    account_email = _primary_account_email(calendars)

    try:
        encrypted_refresh_token = encrypt_secret(refresh_token)
    except CredentialVaultError as exc:
        _revoke_quietly(oauth_client, refresh_token, company_id=company_id, context="vault_unavailable")
        return _error_result(company_id, "vault_unavailable", error_type=type(exc).__name__)

    integration_id = _persist_connection(
        company_id=company_id,
        encrypted_refresh_token=encrypted_refresh_token,
        account_email=account_email,
        actor=actor,
    )
    RuntimeRepository().create_audit_event(
        actor_id=actor,
        action="google_calendar_connected",
        resource_type="calendar_integration",
        resource_id=str(integration_id),
        company_id=company_id,
        auth_mode=str(session.get("auth_mode") or state_row.get("auth_mode") or ""),
        status="success",
        request_id=str(get_request_context().get("request_id") or "") or None,
        metadata={"account_email": account_email, "connection_source": "oauth"},
    )
    log_event(
        logger,
        logging.INFO,
        "google_oauth.connected",
        company_id=company_id,
        integration_id=integration_id,
    )
    return {
        "company_id": company_id,
        "integration_id": integration_id,
        "redirect_to": _redirect(company_id, google="connected"),
    }


def _resolve_refresh_token(integration: dict[str, Any], *, allow_global: bool = False) -> str | None:
    """Refresh token utilizável: decifra o cofre, aceita linha legada em claro.

    `allow_global` libera o `GOOGLE_CALENDAR_REFRESH_TOKEN` do ambiente para
    linhas legadas sem credencial própria. Só faz sentido em leitura: esse
    token é compartilhado pela instalação inteira, e nunca pode chegar a
    `revoke` por ação de uma empresa só.
    """
    config = integration.get("config") or {}
    stored = str(config.get("refresh_token") or "").strip()
    if not stored:
        if not allow_global:
            return None
        fallback = str(settings.google_calendar_refresh_token or "").strip()
        return fallback if _has_real_value(fallback) else None
    if not is_encrypted(stored):
        return stored
    try:
        return decrypt_secret(stored)
    except CredentialVaultError as exc:
        raise GoogleOAuthError(
            "vault_unavailable",
            "Não foi possível ler a credencial guardada da agenda Google; reconecte a agenda pelo painel.",
        ) from exc


def _require_active_integration(company_id: int) -> dict[str, Any]:
    integration = oauth_repository.get_active_google_integration(int(company_id))
    if not integration:
        raise GoogleOAuthNotConnectedError()
    return integration


def list_calendars(company_id: int, *, client: GoogleOAuthClient | None = None) -> list[dict[str, Any]]:
    """Calendários da conta conectada, com `selected` no que a empresa usa."""
    integration = _require_active_integration(company_id)
    # Leitura: uma linha legada sem credencial própria ainda lista a conta
    # compartilhada da instalação, como o provider faz ao consultar a agenda.
    refresh_token = _resolve_refresh_token(integration, allow_global=True)
    if not refresh_token:
        raise GoogleOAuthNotConnectedError(
            "A agenda Google não tem credencial guardada; conecte a agenda de novo."
        )
    config = integration.get("config") or {}
    selected = str(
        config.get("calendar_id") or integration.get("external_calendar_id") or DEFAULT_CALENDAR_ID
    )
    calendars = (client or GoogleOAuthClient()).list_calendars(refresh_token)
    for calendar in calendars:
        calendar["selected"] = calendar["id"] == selected or (
            selected == DEFAULT_CALENDAR_ID and bool(calendar.get("primary"))
        )
    return calendars


def set_calendar(company_id: int, calendar_id: str, *, actor: str | None) -> dict[str, Any]:
    """Troca o calendário usado; o refresh token cifrado segue como está."""
    normalized = str(calendar_id or "").strip()
    if not normalized or len(normalized) > MAX_CALENDAR_ID_LENGTH:
        raise GoogleOAuthError("invalid_calendar", "Informe um calendário válido.")
    integration = _require_active_integration(company_id)
    config = dict(integration.get("config") or {})
    config["calendar_id"] = normalized
    oauth_repository.update_calendar_integration(
        int(integration["id"]),
        company_id=int(company_id),
        external_calendar_id=normalized,
        config=config,
    )
    updated = oauth_repository.get_calendar_integration_row(int(integration["id"]), company_id=int(company_id))
    log_event(
        logger,
        logging.INFO,
        "google_oauth.calendar_selected",
        company_id=int(company_id),
        integration_id=int(integration["id"]),
        actor_id=actor,
    )
    return public_calendar_integration(updated or integration)


def disconnect(company_id: int, *, actor: str | None, client: GoogleOAuthClient | None = None) -> dict[str, Any]:
    """Revoga o acesso (melhor esforço) e apaga a credencial da linha.

    A linha fica inativa com `config` vazio, e não é apagada: bookings antigos
    ainda apontam para ela, e o histórico de conexão continua consultável.
    """
    integration = _require_active_integration(company_id)
    integration_id = int(integration["id"])
    revoked = False
    try:
        # Só o token da própria linha (`allow_global` desligado): uma linha
        # legada sem credencial usa o GOOGLE_CALENDAR_REFRESH_TOKEN da
        # instalação, compartilhado por todas as empresas, e revogá-lo por
        # uma delas derrubaria a agenda das outras.
        refresh_token = _resolve_refresh_token(integration)
    except GoogleOAuthError:
        # Sem chave para ler o token não há o que revogar; a desconexão local
        # é justamente o remédio para esse estado.
        refresh_token = None
    if refresh_token:
        revoked = _revoke_quietly(
            client or GoogleOAuthClient(),
            refresh_token,
            company_id=int(company_id),
            context="disconnect",
            integration_id=integration_id,
        )
    else:
        log_event(
            logger,
            logging.WARNING,
            "google_oauth.revoke_skipped",
            company_id=int(company_id),
            integration_id=integration_id,
            reason="no_company_credential",
        )
    oauth_repository.update_calendar_integration(
        integration_id,
        company_id=int(company_id),
        active=False,
        config={},
    )
    log_event(
        logger,
        logging.INFO,
        "google_oauth.disconnected",
        company_id=int(company_id),
        integration_id=integration_id,
        revoked=revoked,
        actor_id=actor,
    )
    return {
        "disconnected": True,
        "integration_id": integration_id,
        "revoked": revoked,
        "account_email": integration.get("connected_account_email"),
    }
