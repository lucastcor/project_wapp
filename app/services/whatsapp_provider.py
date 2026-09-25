import logging
from datetime import UTC, datetime
from typing import Any, NoReturn

from app.core.config import is_missing_or_placeholder, settings
from app.core.credential_vault import CredentialVaultError, decrypt_secret
from app.core.logging import get_logger, log_event, mask_identifier
from app.storage.whatsapp_credentials_repository import (
    get_credentials_for_company,
    mark_needs_reconnection,
    touch_last_verified,
)

logger = get_logger(__name__)

# Códigos da Cloud API em que repetir mais tarde é seguro e esperado
# (limite de taxa, indisponibilidade temporária, erro desconhecido do lado da
# Meta). Referência: developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes
TRANSIENT_ERROR_CODES = frozenset({1, 2, 4, 17, 341, 80007, 130429, 131000, 131016, 131056})
# Token inválido ou expirado: a Meta não vai aceitar de novo até a empresa
# reconectar o número pelo painel.
TOKEN_INVALID_ERROR_CODE = 190
PROVIDER_ERROR_KINDS = frozenset({"configuration", "permanent", "transient", "uncertain"})
_MAX_ERROR_TITLE_LENGTH = 160
_RECONNECT_HINT = "Conecte ou reconecte o número pelo painel (tela Canal) para usar as credenciais da empresa."


def default_graph_base_url() -> str:
    """Base da Graph API quando a instalação não fixa `WHATSAPP_API_BASE_URL`.

    O token de negócio obtido pelo Embedded Signup fala com a Graph API
    oficial; a versão vem de `META_GRAPH_API_VERSION` para que uma
    depreciação seja um ajuste de ambiente, e não um deploy.
    """
    version = str(settings.meta_graph_api_version or "").strip().strip("/") or "v23.0"
    return f"https://graph.facebook.com/{version}"


class WhatsAppProviderError(Exception):
    """Falha de envio com classificação acionável.

    `kind`:
    - `configuration`: faltam credenciais ou vínculo do número (corrigível).
    - `permanent`: a Meta recusou definitivamente (fora da janela, template
      inexistente, token expirado); repetir não muda o resultado.
    - `transient`: limite de taxa/indisponibilidade; repetir com espera.
    - `uncertain`: a mensagem pode ter sido aceita (timeout de resposta,
      conexão caída depois de aberta); repetir poderia duplicar.
    """

    def __init__(self, message: str, *, kind: str = "permanent", code: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind if kind in PROVIDER_ERROR_KINDS else "permanent"
        self.code = code


def _connection_never_opened(exc: Exception) -> bool:
    """True quando a falha ocorreu antes de qualquer byte chegar à Meta."""
    try:
        from urllib3.exceptions import MaxRetryError, NameResolutionError, NewConnectionError
    except ImportError:  # pragma: no cover - urllib3 acompanha requests
        return False
    for candidate in exc.args:
        reason = candidate.reason if isinstance(candidate, MaxRetryError) else candidate
        if isinstance(reason, (NewConnectionError, NameResolutionError)):
            return True
    return False


def _error_from_response(response: Any) -> WhatsAppProviderError:
    status_code = int(response.status_code)
    code: int | None = None
    title = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        raw_code = error.get("code")
        if isinstance(raw_code, int) and not isinstance(raw_code, bool):
            code = raw_code
        elif str(raw_code or "").isdigit():
            code = int(str(raw_code))
        # Só o título curto: `error_data.details` pode trazer o número do cliente.
        title = str(error.get("message") or "").strip()[:_MAX_ERROR_TITLE_LENGTH]

    transient = status_code == 429 or status_code >= 500 or code in TRANSIENT_ERROR_CODES
    kind = "transient" if transient else "permanent"
    summary = f"WhatsApp API returned status {status_code} (code {code if code is not None else '-'})"
    return WhatsAppProviderError(f"{summary}: {title}" if title else summary, kind=kind, code=code)


class WhatsAppProvider:
    """WhatsApp Cloud API client scoped to one tenant's business number.

    The sending ``phone_number_id`` is resolved per company from
    ``whatsapp_channel_bindings`` so Tenant B's reply can never leave through
    Tenant A's number. The access token follows the same binding: a company
    that connected its number through Embedded Signup sends with its own
    business token (decrypted only here, at send time); a company without one
    keeps using the installation-wide ``WHATSAPP_API_TOKEN`` so earlier
    deployments do not break.
    """

    def __init__(self, *, company_id: int | None = None) -> None:
        self.base_url: str | None = settings.whatsapp_api_base_url
        self.api_token: str | None = settings.whatsapp_api_token
        self.company_id = company_id
        self.binding_id: int | None = None
        self.has_company_token = False
        self.needs_reconnection = False
        # A decryption failure is remembered, not raised: the constructor runs
        # inside dispatch paths that expect provider errors from the send call,
        # where they are classified as `configuration` and retried later.
        self._credential_error: str | None = None
        self.phone_number_id: str | None = None
        self._resolve_binding(company_id)

    def _resolve_binding(self, company_id: int | None) -> None:
        if company_id is None:
            # No tenant context: fall back to the single configured number.
            self.phone_number_id = settings.whatsapp_phone_number_id
            return

        binding = get_credentials_for_company(int(company_id))
        if not binding:
            return
        self.binding_id = int(binding["id"])
        self.phone_number_id = str(binding["phone_number_id"])
        self.needs_reconnection = bool(binding.get("needs_reconnection"))

        encrypted_token = str(binding.get("access_secret_encrypted") or "")
        if not encrypted_token:
            return
        try:
            self.api_token = decrypt_secret(encrypted_token)
        except CredentialVaultError as exc:
            # The token exists but cannot be read: never fall back to the
            # global token silently, or a key rotation would send Tenant A's
            # messages with whatever credential the installation still has.
            self._credential_error = (
                "The company's WhatsApp credential cannot be decrypted "
                f"({type(exc).__name__}); check CREDENTIALS_ENCRYPTION_KEY. {_RECONNECT_HINT}"
            )
            return
        self.has_company_token = True
        self.base_url = str(settings.whatsapp_api_base_url or "").strip() or default_graph_base_url()

    def _validate_configuration(self) -> None:
        if self._credential_error:
            raise WhatsAppProviderError(self._credential_error, kind="configuration")

        missing_fields: list[str] = []

        if not self.base_url:
            missing_fields.append("WHATSAPP_API_BASE_URL")
        if is_missing_or_placeholder(self.api_token):
            missing_fields.append("WHATSAPP_API_TOKEN")

        if missing_fields:
            hint = f" {_RECONNECT_HINT}" if self.company_id is not None else ""
            raise WhatsAppProviderError(
                "WhatsApp provider is not fully configured. Missing: "
                + ", ".join(missing_fields)
                + hint,
                kind="configuration",
            )

        if not self.phone_number_id:
            if self.company_id is not None:
                raise WhatsAppProviderError(
                    f"Company {self.company_id} has no single active WhatsApp channel binding; "
                    f"outbound delivery is blocked until exactly one is configured. {_RECONNECT_HINT}",
                    kind="configuration",
                )
            raise WhatsAppProviderError(
                "WhatsApp provider is not fully configured. Missing: WHATSAPP_PHONE_NUMBER_ID",
                kind="configuration",
            )

    def _raise_from_response(self, response: Any) -> NoReturn:
        """Classify a Graph error and flag the binding when the token is dead.

        Code 190 means Meta will keep rejecting this credential until the
        company reconnects; the flag is what lets the panel say so instead of
        the number silently failing. Only the company's own token earns the
        flag: a rejected installation-wide ``WHATSAPP_API_TOKEN`` is fixed by
        rotating the environment, and telling the owner to "reconnect" would
        send them to a button that cannot help. Flagging never masks the
        original error.
        """
        error = _error_from_response(response)
        if error.code == TOKEN_INVALID_ERROR_CODE and self.binding_id is not None and self.has_company_token:
            try:
                mark_needs_reconnection(self.binding_id, True)
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "whatsapp.provider.reconnection_flag_failed",
                    company_id=self.company_id,
                    binding_id=self.binding_id,
                    error_type=type(exc).__name__,
                )
        raise error

    def _build_url(self) -> str:
        # Re-checked rather than asserted: assertions are stripped under -O, and
        # a missing phone_number_id here would silently build a wrong-tenant URL.
        self._validate_configuration()
        return f"{str(self.base_url).rstrip('/')}/{self.phone_number_id}/messages"

    def _build_phone_url(self) -> str:
        self._validate_configuration()
        return f"{str(self.base_url).rstrip('/')}/{self.phone_number_id}"

    def check_connection(self) -> None:
        """Confirm Meta credentials and number access without sending a message."""
        self._validate_configuration()
        try:
            import requests
        except ModuleNotFoundError as exc:
            raise WhatsAppProviderError(
                "The 'requests' package is required for the WhatsApp provider runtime.",
                kind="configuration",
            ) from exc

        try:
            response = requests.get(
                self._build_phone_url(),
                headers={"Authorization": f"Bearer {self.api_token}"},
                params={"fields": "id,display_phone_number,verified_name"},
                timeout=20,
            )
        except (requests.ConnectTimeout, requests.ConnectionError) as exc:
            raise WhatsAppProviderError(
                "WhatsApp API connection could not be completed.",
                kind="transient",
            ) from exc
        except requests.RequestException as exc:
            raise WhatsAppProviderError(
                "WhatsApp API connection check failed.",
                kind="transient",
            ) from exc

        if response.status_code >= 400:
            self._raise_from_response(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise WhatsAppProviderError(
                "WhatsApp API connection check returned invalid JSON.",
                kind="uncertain",
            ) from exc
        if not isinstance(body, dict) or str(body.get("id") or "") != str(self.phone_number_id):
            raise WhatsAppProviderError(
                "WhatsApp API did not confirm access to the configured business number.",
                kind="permanent",
            )

    def send_message(self, contact_id: str, message: str) -> dict[str, Any]:
        self._validate_configuration()

        contact = contact_id.strip()
        text = message.strip()
        if not contact:
            raise WhatsAppProviderError("contact_id must be a non-empty string.")
        if not text:
            raise WhatsAppProviderError("message must be a non-empty string.")

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": contact,
            "type": "text",
            "text": {
                "body": text,
            },
        }
        message_id, provider_response = self._post(payload)
        return {
            "channel": "whatsapp",
            "contact_id": contact,
            "message": text,
            "provider_message_id": message_id,
            "provider_response": provider_response,
        }

    def send_template(
        self,
        contact_id: str,
        *,
        template_name: str,
        language_code: str,
        parameters: list[str],
    ) -> dict[str, Any]:
        """Envia template aprovado (fora da janela de atendimento de 24 h)."""
        self._validate_configuration()

        contact = contact_id.strip()
        if not contact:
            raise WhatsAppProviderError("contact_id must be a non-empty string.")
        if not template_name.strip() or not language_code.strip():
            raise WhatsAppProviderError("template_name and language_code are required.")

        template: dict[str, Any] = {"name": template_name.strip(), "language": {"code": language_code.strip()}}
        if parameters:
            template["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": value} for value in parameters]}
            ]
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": contact,
            "type": "template",
            "template": template,
        }
        message_id, provider_response = self._post(payload)
        return {
            "channel": "whatsapp",
            "contact_id": contact,
            "message": f"[template:{template_name.strip()}]",
            "provider_message_id": message_id,
            "provider_response": provider_response,
        }

    def _post(self, payload: dict[str, Any]) -> tuple[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        try:
            import requests
        except ModuleNotFoundError as exc:
            raise WhatsAppProviderError(
                "The 'requests' package is required for the WhatsApp provider runtime. Install it to enable real WhatsApp dispatch.",
                kind="configuration",
            ) from exc

        try:
            response = requests.post(
                self._build_url(),
                headers=headers,
                json=payload,
                timeout=20,
            )
        except requests.ConnectTimeout as exc:
            raise WhatsAppProviderError(
                "WhatsApp API connection timed out before the request was sent.",
                kind="transient",
            ) from exc
        except requests.ConnectionError as exc:
            if _connection_never_opened(exc):
                raise WhatsAppProviderError(
                    "WhatsApp API connection could not be opened.",
                    kind="transient",
                ) from exc
            raise WhatsAppProviderError(
                "WhatsApp API connection dropped; delivery outcome is unknown.",
                kind="uncertain",
            ) from exc
        except requests.RequestException as exc:
            raise WhatsAppProviderError(
                f"WhatsApp API request failed ({type(exc).__name__}); delivery outcome is unknown.",
                kind="uncertain",
            ) from exc

        if response.status_code >= 400:
            self._raise_from_response(response)

        try:
            provider_response = response.json()
        except ValueError as exc:
            raise WhatsAppProviderError(
                "WhatsApp API accepted the request but did not return valid JSON; delivery outcome is unknown.",
                kind="uncertain",
            ) from exc

        message_id = None
        messages = provider_response.get("messages") if isinstance(provider_response, dict) else None
        if isinstance(messages, list) and messages:
            first_message = messages[0]
            if isinstance(first_message, dict):
                message_id = first_message.get("id")
        if not message_id:
            # Sem id não há como correlacionar status de entrega nem provar aceite.
            raise WhatsAppProviderError(
                "WhatsApp API response has no message id; delivery outcome is unknown.",
                kind="uncertain",
            )
        return str(message_id), provider_response


def get_whatsapp_channel_status(company_id: int) -> dict[str, Any]:
    """Return the canonical runtime binding and credential presence, sanitized.

    Identifiers are masked and no token, PIN or app secret leaves this
    function: the panel only needs to know *whether* credentials exist and
    where they came from (Embedded Signup or the installation's environment).
    """
    from app.services.whatsapp_signup_service import embedded_signup_config
    from app.storage.repository import RuntimeRepository

    active_bindings = RuntimeRepository().get_whatsapp_bindings(company_id=company_id, active_only=True)
    binding = get_credentials_for_company(int(company_id)) if len(active_bindings) == 1 else None
    binding_ready = binding is not None

    has_company_token = bool(binding and binding.get("access_secret_encrypted"))
    # Presence is not enough: after a key rotation the row still holds a
    # token the provider refuses to use (it never falls back to the global
    # one), so the status must say "unreadable", not "configured". The
    # plaintext is discarded here; only the outcome matters.
    credential_unreadable = False
    if has_company_token and binding is not None:
        try:
            decrypt_secret(str(binding.get("access_secret_encrypted") or ""))
        except CredentialVaultError:
            credential_unreadable = True
    global_token_present = not is_missing_or_placeholder(settings.whatsapp_api_token)
    global_base_url_present = bool(str(settings.whatsapp_api_base_url or "").strip())
    token_present = has_company_token or global_token_present
    # A company token always has a base URL: the Graph API default applies
    # when the installation does not pin one.
    base_url_present = global_base_url_present or has_company_token
    verify_token_present = not is_missing_or_placeholder(settings.whatsapp_webhook_verify_token)
    app_secret_present = not is_missing_or_placeholder(settings.whatsapp_app_secret)
    # An unreadable token needs the same remedy as a rejected one: reconnect
    # (or restore the key); the panel offers the button on this flag.
    needs_reconnection = credential_unreadable or (bool(binding.get("needs_reconnection")) if binding else False)

    delivery_configured = base_url_present and token_present and binding_ready and not credential_unreadable
    inbound_configured = verify_token_present and app_secret_present and binding_ready
    ready = delivery_configured and inbound_configured
    can_connect = bool(embedded_signup_config()["enabled"])
    can_send = delivery_configured

    # The source describes the credential in use now, not the row's past:
    # a historical Embedded Signup row re-activated without its token sends
    # with the installation's global token, which is what "manual" means.
    connection_source: str | None = None
    if binding is not None:
        connection_source = (str(binding.get("credential_source") or "") or None) if has_company_token else "manual"

    if len(active_bindings) > 1:
        details = "Há mais de um número ativo; o envio fica bloqueado até restar um único vínculo."
    elif not binding_ready:
        details = (
            "Nenhum número conectado. Conecte o WhatsApp da empresa pelo painel."
            if can_connect
            else "Nenhum número ativo está vinculado a esta empresa."
        )
    elif credential_unreadable:
        details = (
            "A credencial deste número está gravada, mas o servidor não consegue lê-la "
            "(CREDENTIALS_ENCRYPTION_KEY mudou?). Peça ao administrador da instalação para "
            "restaurar a chave ou reconecte o número pelo painel."
        )
    elif needs_reconnection:
        details = "A Meta recusou o token deste número; reconecte o número pelo painel."
    elif ready:
        details = "Credenciais de envio, validação do webhook e vínculo canônico estão configurados."
    elif not delivery_configured:
        details = "O vínculo existe, mas faltam credenciais para consultar e enviar pela Meta."
        if can_connect:
            details += " Reconecte o número pelo painel."
    else:
        details = "O envio está configurado, mas faltam credenciais para validar eventos recebidos da Meta."

    public_binding = None
    if binding is not None:
        public_binding = {
            "id": int(binding["id"]),
            "phone_number_id": mask_identifier(str(binding.get("phone_number_id") or "")),
            "display_phone_number": mask_identifier(str(binding.get("display_phone_number") or "")),
            "active": bool(binding.get("active")),
            "created_at": binding.get("created_at"),
            "updated_at": binding.get("updated_at"),
        }

    return {
        "provider": "whatsapp_cloud_api",
        "company_id": int(company_id),
        "ready": ready,
        "delivery_configured": delivery_configured,
        "inbound_configured": inbound_configured,
        "active_binding_count": len(active_bindings),
        "binding": public_binding,
        "credentials": {
            "api_base_url_present": base_url_present,
            "api_token_present": token_present,
            "webhook_verify_token_present": verify_token_present,
            "app_secret_present": app_secret_present,
        },
        "connection_source": connection_source,
        "has_company_token": has_company_token,
        "credential_unreadable": credential_unreadable,
        "needs_reconnection": needs_reconnection,
        "can_connect": can_connect,
        "can_send": can_send,
        "waba_id_masked": mask_identifier(str(binding.get("waba_id") or "")) if binding else None,
        "verified_name": (binding.get("verified_name") if binding else None),
        "display_phone_number": (
            mask_identifier(str(binding.get("display_phone_number") or "")) if binding else None
        ),
        "quality_rating": (binding.get("quality_rating") if binding else None),
        "platform_type": (binding.get("platform_type") if binding else None),
        "connected_at": (binding.get("connected_at") if binding else None),
        "last_verified_at": (binding.get("last_verified_at") if binding else None),
        "details": details,
    }


def check_whatsapp_connection(company_id: int) -> dict[str, Any]:
    """Run a read-only Meta Graph check and return no provider payload or secret."""
    status = get_whatsapp_channel_status(company_id)
    checked_at = datetime.now(UTC).isoformat()
    if not status["delivery_configured"]:
        return {
            "provider": "whatsapp_cloud_api",
            "configured": False,
            "connected": False,
            "checked_at": checked_at,
            "error_kind": "configuration",
            "details": (
                str(status["details"])
                if status.get("credential_unreadable")
                else (
                    "É preciso um único número ativo e credenciais da empresa (conexão pelo painel) "
                    "ou globais da instalação."
                )
            ),
        }

    provider = WhatsAppProvider(company_id=company_id)
    try:
        provider.check_connection()
    except WhatsAppProviderError as exc:
        if exc.kind == "configuration":
            # Meta was never contacted: blaming it would send the owner to
            # reconnect when the fix is on this server.
            hint = (
                " Verifique CREDENTIALS_ENCRYPTION_KEY no servidor."
                if "CREDENTIALS_ENCRYPTION_KEY" in str(exc)
                else ""
            )
            return {
                "provider": "whatsapp_cloud_api",
                "configured": False,
                "connected": False,
                "checked_at": checked_at,
                "error_kind": "configuration",
                "provider_code": None,
                "details": "As credenciais do número não puderam ser preparadas no servidor; a Meta não foi consultada."
                + hint,
            }
        return {
            "provider": "whatsapp_cloud_api",
            "configured": True,
            "connected": False,
            "checked_at": checked_at,
            "error_kind": exc.kind,
            "provider_code": exc.code,
            "details": "A Meta não confirmou acesso ao número vinculado.",
        }

    if provider.binding_id is not None:
        try:
            touch_last_verified(provider.binding_id)
        except Exception as exc:
            # The check itself succeeded; failing to record the timestamp
            # must not turn a healthy number into a "not connected" report.
            log_event(
                logger,
                logging.WARNING,
                "whatsapp.provider.last_verified_update_failed",
                company_id=company_id,
                binding_id=provider.binding_id,
                error_type=type(exc).__name__,
            )

    return {
        "provider": "whatsapp_cloud_api",
        "configured": True,
        "connected": True,
        "checked_at": checked_at,
        "details": "Token e acesso ao número confirmados; nenhuma mensagem foi enviada.",
    }
