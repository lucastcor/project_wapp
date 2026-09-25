"""Conexão do número WhatsApp da empresa pelo Embedded Signup da Meta.

O popup da Meta (SDK do Facebook no painel) termina com um `code` de curta
duração e, opcionalmente, os ids do número e da conta WhatsApp Business
(WABA) escolhidos pelo dono. Este serviço faz o resto, no servidor, porque é
onde está o segredo do app:

1. troca o `code` por um token de negócio (Business Integration System User
   token, válido enquanto o app estiver instalado na WABA);
2. descobre a WABA e o número quando o popup não os informou (`debug_token`
   e `/{waba}/phone_numbers`);
3. confere, ainda sem tocar na Meta, que o número não está ativo em outra
   empresa e que o cofre consegue guardar o token;
4. lê o perfil do número (nome verificado, qualidade) — leitura que também
   prova que o token alcança o número informado;
5. inscreve o app nos webhooks da WABA — sem isso as mensagens recebidas
   nunca chegam;
6. registra o número na Cloud API com o PIN de seis dígitos do número
   (reaproveitado quando já existe; gerado aqui na primeira vez);
7. guarda tudo no binding da empresa, com token e PIN cifrados.

Só os passos 5 e 6 alteram algo na Meta, e eles vêm depois de toda
validação: uma conexão recusada (409, 503, id inválido) não deixa efeito
colateral no número de ninguém.

O token nunca aparece em log, auditoria, resposta de API nem mensagem de
erro: `MetaGraphClient` reduz qualquer erro da Graph a código, tipo e título
curto, redigindo o token se ele aparecer no texto. As mensagens de erro
voltadas ao dono do negócio são em pt-BR.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any

from app.core.config import is_missing_or_placeholder, is_production_like, settings
from app.core.credential_vault import (
    CredentialVaultError,
    decrypt_secret,
    encrypt_secret,
    vault_is_configured,
)
from app.core.logging import get_logger, log_event, mask_identifier
from app.services.whatsapp_provider import (
    TRANSIENT_ERROR_CODES,
    default_graph_base_url,
    get_whatsapp_channel_status,
)
from app.storage.whatsapp_credentials_repository import (
    clear_connection,
    get_credentials_for_company,
    get_registration_pin_encrypted,
    list_bindings_with_token,
    store_connection,
    waba_in_use_elsewhere,
)

logger = get_logger(__name__)

SOURCE_EMBEDDED_SIGNUP = "embedded_signup"
SOURCE_EMBEDDED_SIGNUP_COEXISTENCE = "embedded_signup_coexistence"
WHATSAPP_MANAGEMENT_SCOPE = "whatsapp_business_management"
PHONE_PROFILE_FIELDS = "id,display_phone_number,verified_name,quality_rating,code_verification_status,platform_type"
# Um número já registrado na Cloud API (reconexão) responde com erro ao
# `register`; para a conexão isso é sucesso, não falha.
_ALREADY_REGISTERED_CODES = frozenset({133005, 133006})
_ALREADY_REGISTERED_TEXT = "already registered"
_MAX_ERROR_TITLE_LENGTH = 160
_REDACTED = "[redacted]"
# Ids da Meta (número, WABA, negócio) são sempre numéricos. Qualquer outra
# coisa vinda do popup viraria caminho na Graph (`{id}/register`) e um
# identificador canônico que nenhum webhook alcança.
_META_ID_PATTERN = re.compile(r"[0-9]{1,32}")


class SignupError(Exception):
    """Falha ao conectar ou desconectar, com detalhe em pt-BR para o dono.

    `kind` classifica para log e auditoria; `provider_code` é o código da
    Graph quando houve um; `status_code` é o HTTP que a rota deve devolver.
    O texto nunca contém token, código de autorização nem PIN.
    """

    status_code = 502

    def __init__(
        self,
        kind: str,
        detail: str,
        provider_code: int | None = None,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.provider_code = provider_code
        if status_code is not None:
            self.status_code = status_code


class SignupConflictError(SignupError):
    """O número está ativo em outra empresa; nunca re-apontar tráfego."""

    status_code = 409

    def __init__(self, detail: str = "Este número já está conectado a outra empresa.") -> None:
        super().__init__("conflict", detail)


class SignupUnavailableError(SignupError):
    """A instalação não pode concluir a conexão (app da Meta ou cofre ausente)."""

    status_code = 503


class SignupNotConnectedError(SignupError):
    status_code = 404


class MetaGraphError(Exception):
    """Erro da Graph API já higienizado.

    Guarda só o que serve para decidir e diagnosticar: HTTP, `code`,
    `error_subcode`, `type` e o título curto. O corpo cru fica de fora
    porque `error_data.details` pode trazer número de telefone ou token.
    """

    def __init__(
        self,
        kind: str,
        *,
        http_status: int | None = None,
        provider_code: int | None = None,
        provider_subcode: int | None = None,
        provider_type: str | None = None,
        title: str = "",
    ) -> None:
        summary = f"Graph API {kind} (http {http_status if http_status is not None else '-'}, code {provider_code if provider_code is not None else '-'})"
        super().__init__(f"{summary}: {title}" if title else summary)
        self.kind = kind
        self.http_status = http_status
        self.provider_code = provider_code
        self.provider_subcode = provider_subcode
        self.provider_type = provider_type
        self.title = title


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _sanitize_title(raw: Any, *, secrets_to_redact: tuple[str, ...]) -> str:
    title = str(raw or "").strip()
    for secret in secrets_to_redact:
        if secret:
            title = title.replace(secret, _REDACTED)
    return title[:_MAX_ERROR_TITLE_LENGTH]


class MetaGraphClient:
    """Cliente mínimo da Graph API para o fluxo de conexão.

    `requests` é importado tarde, como no provider de envio, para que o
    módulo carregue em ambientes sem a dependência (a rota `/config` não
    fala com a Meta). Os métodos devolvem o JSON já decodificado e lançam
    `MetaGraphError` para qualquer HTTP >= 400 ou falha de rede.

    Segredos (app secret, code, token a inspecionar) viajam no corpo de um
    POST (`data`) ou no cabeçalho `Authorization`, nunca na query string:
    a URL completa aparece no log de depuração do `urllib3` e na mensagem
    das exceções de rede, e nenhum dos dois é lugar para credencial.
    """

    def __init__(self, base_url: str | None = None, timeout: int = 20) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/") or default_graph_base_url()
        self.timeout = timeout

    def get(self, path: str, params: dict[str, Any] | None = None, token: str | None = None) -> dict[str, Any]:
        return self._request("GET", path, params=params, token=token)

    def post(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        token: str | None = None,
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, json=json, data=data, token=token)

    def delete(self, path: str, token: str | None = None) -> dict[str, Any]:
        return self._request("DELETE", path, token=token)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        try:
            import requests
        except ModuleNotFoundError as exc:
            raise MetaGraphError("configuration", title="requests package is not installed") from exc

        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # Segredos que não podem sobreviver numa mensagem de erro: o token
        # do cabeçalho e qualquer parâmetro da troca de código ou do
        # `debug_token` (o app token é `app_id|app_secret`, daí o split).
        secrets_to_redact: list[str] = []
        for value in [token, *(params or {}).values(), *(data or {}).values()]:
            text = str(value or "")
            for piece in (text, *text.split("|")):
                if len(piece) >= 8 and piece not in secrets_to_redact:
                    secrets_to_redact.append(piece)
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                data=data,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            # Sem encadear (`from None`): a exceção do `requests` carrega a
            # URL completa, e um `logger.exception` futuro a imprimiria.
            raise MetaGraphError("network", title=type(exc).__name__) from None

        try:
            body = response.json()
        except ValueError:
            body = None

        if int(response.status_code) >= 400:
            raise self._error_from_body(int(response.status_code), body, tuple(secrets_to_redact))
        if not isinstance(body, dict):
            raise MetaGraphError("invalid_response", http_status=int(response.status_code), title="non-object JSON")
        return body

    @staticmethod
    def _error_from_body(http_status: int, body: Any, secrets_to_redact: tuple[str, ...]) -> MetaGraphError:
        error = body.get("error") if isinstance(body, dict) else None
        code: int | None = None
        subcode: int | None = None
        provider_type: str | None = None
        title = ""
        if isinstance(error, dict):
            code = _as_int(error.get("code"))
            subcode = _as_int(error.get("error_subcode"))
            provider_type = str(error.get("type") or "").strip()[:40] or None
            title = _sanitize_title(error.get("message"), secrets_to_redact=secrets_to_redact)

        if http_status in {401, 403} or code == 190:
            kind = "auth"
        elif http_status == 429 or http_status >= 500 or code in TRANSIENT_ERROR_CODES:
            kind = "transient"
        else:
            kind = "rejected"
        return MetaGraphError(
            kind,
            http_status=http_status,
            provider_code=code,
            provider_subcode=subcode,
            provider_type=provider_type,
            title=title,
        )


def embedded_signup_config() -> dict[str, Any]:
    """O que o painel precisa para abrir o popup, e se pode abri-lo.

    Só ids públicos saem daqui (app id e config id aparecem no HTML da
    Meta de qualquer forma). O app secret entra na decisão de `enabled`,
    mas nunca na resposta.
    """
    missing: list[str] = []
    if is_missing_or_placeholder(settings.meta_app_id):
        missing.append("META_APP_ID")
    if is_missing_or_placeholder(settings.meta_embedded_signup_config_id):
        missing.append("META_EMBEDDED_SIGNUP_CONFIG_ID")
    if is_missing_or_placeholder(settings.whatsapp_app_secret):
        missing.append("WHATSAPP_APP_SECRET")
    # Fora de produção o cofre deriva uma chave do segredo de sessão; em
    # produção a chave explícita é obrigatória para guardar o token.
    if not (vault_is_configured() or not is_production_like()):
        missing.append("CREDENTIALS_ENCRYPTION_KEY")

    enabled = not missing
    reason = None if enabled else f"Faltam variáveis de ambiente na instalação: {', '.join(missing)}."
    return {
        "enabled": enabled,
        "app_id": None if is_missing_or_placeholder(settings.meta_app_id) else str(settings.meta_app_id).strip(),
        "config_id": (
            None
            if is_missing_or_placeholder(settings.meta_embedded_signup_config_id)
            else str(settings.meta_embedded_signup_config_id).strip()
        ),
        "graph_api_version": str(settings.meta_graph_api_version or "").strip() or "v23.0",
        "reason": reason,
    }


def _clean(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _require_meta_id(value: str | None, field: str, *, status_code: int = 400) -> str | None:
    """Um id da Meta limpo e numérico, ou erro; `None` quando ausente.

    Do popup (corpo da requisição) a falha é 400; de uma resposta da própria
    Graph (descoberta) é 502, porque aí o problema não é do painel.
    """
    cleaned = _clean(value)
    if cleaned is None:
        return None
    if _META_ID_PATTERN.fullmatch(cleaned) is None:
        raise SignupError(
            "invalid_identifier",
            f"A Meta devolveu um identificador inválido ({field}). Refaça a conexão pelo popup da Meta.",
            status_code=status_code,
        )
    return cleaned


def _generate_pin() -> str:
    """PIN de verificação em duas etapas: seis dígitos, aleatório de verdade."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _stored_registration_pin(phone_number_id: str) -> str | None:
    """PIN já gravado para o número, decifrado; `None` se não há ou não dá para ler."""
    encrypted = get_registration_pin_encrypted(phone_number_id)
    if not encrypted:
        return None
    try:
        return decrypt_secret(encrypted)
    except CredentialVaultError as exc:
        log_event(
            logger,
            logging.WARNING,
            "whatsapp.signup.stored_pin_unreadable",
            phone_number_id=mask_identifier(phone_number_id),
            error_type=type(exc).__name__,
        )
        return None


def _waba_id_from_debug_token(payload: dict[str, Any]) -> str | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    scopes = data.get("granular_scopes") if isinstance(data, dict) else None
    for scope in scopes or []:
        if not isinstance(scope, dict) or scope.get("scope") != WHATSAPP_MANAGEMENT_SCOPE:
            continue
        targets = scope.get("target_ids") or []
        if isinstance(targets, list) and targets:
            return _clean(str(targets[0]))
    return None


def _first_phone_number_id(payload: dict[str, Any]) -> str | None:
    numbers = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(numbers, list) and numbers and isinstance(numbers[0], dict):
        return _clean(str(numbers[0].get("id") or ""))
    return None


def _already_registered(exc: MetaGraphError) -> bool:
    return exc.provider_code in _ALREADY_REGISTERED_CODES or _ALREADY_REGISTERED_TEXT in exc.title.lower()


def _translate(exc: MetaGraphError, kind: str, detail: str) -> SignupError:
    """Erro da Graph vira erro de conexão com texto pt-BR e código preservado."""
    if exc.kind == "network":
        detail = f"{detail} Não foi possível falar com a Meta agora; tente de novo em instantes."
    log_event(
        logger,
        logging.WARNING,
        "whatsapp.signup.graph_error",
        step=kind,
        graph_kind=exc.kind,
        http_status=exc.http_status,
        provider_code=exc.provider_code,
        provider_subcode=exc.provider_subcode,
        provider_type=exc.provider_type,
    )
    return SignupError(kind, detail, provider_code=exc.provider_code)


def _ensure_number_is_free(company_id: int, phone_number_id: str) -> None:
    """409 antes de qualquer efeito na Meta quando o número é de outra empresa.

    `store_connection` repete a checagem na gravação (é o índice único quem
    decide numa corrida); esta é a que evita inscrever e registrar o número
    de um tenant com o token de outro só para descobrir o conflito depois.
    """
    from app.storage.repository import RuntimeRepository

    owner = RuntimeRepository().get_whatsapp_binding_by_phone_number_id(phone_number_id, active_only=True)
    if owner is not None and int(owner["company_id"]) != int(company_id):
        raise SignupConflictError()


def _unsubscribe_quietly(
    graph: MetaGraphClient,
    *,
    company_id: int,
    binding_id: int,
    waba_id: str,
    token: str,
    reason: str,
) -> None:
    """DELETE /{waba}/subscribed_apps como cortesia: falhar só gera aviso."""
    try:
        graph.delete(f"{waba_id}/subscribed_apps", token=token)
    except MetaGraphError as exc:
        log_event(
            logger,
            logging.WARNING,
            "whatsapp.signup.unsubscribe_failed",
            company_id=company_id,
            binding_id=binding_id,
            reason=reason,
            error_type=type(exc).__name__,
            provider_code=exc.provider_code,
        )


def _previous_waba_to_release(
    company_id: int,
    *,
    new_waba_id: str,
) -> tuple[int, str, str] | None:
    """A WABA anterior da empresa, com o token antigo, se for trocada e ninguém mais a usar.

    Devolve `(binding_id, waba_id, token)` ou `None`. O token é lido agora
    porque `store_connection` o apaga; a chamada à Meta só acontece depois
    da gravação, para uma conexão que falhou não derrubar o que estava
    funcionando.
    """
    current = get_credentials_for_company(company_id)
    if current is None:
        return None
    old_waba = _clean(str(current.get("waba_id") or ""))
    encrypted = str(current.get("access_secret_encrypted") or "")
    if not old_waba or not encrypted or old_waba == new_waba_id:
        return None
    if waba_in_use_elsewhere(old_waba, excluding_binding_id=int(current["id"])):
        return None
    try:
        return int(current["id"]), old_waba, decrypt_secret(encrypted)
    except CredentialVaultError:
        return None


def complete_embedded_signup(
    company_id: int,
    *,
    code: str,
    phone_number_id: str | None = None,
    waba_id: str | None = None,
    business_id: str | None = None,
    event: str | None = None,
    coexistence: bool = False,
    actor: str | None,
    client: MetaGraphClient | None = None,
) -> dict[str, Any]:
    """Conclui a conexão iniciada no popup e devolve o status do canal.

    `client` é injetável para testes; o padrão fala com a Graph oficial.
    """
    config = embedded_signup_config()
    if not config["enabled"]:
        raise SignupUnavailableError(
            "configuration",
            "A conexão pelo painel não está habilitada nesta instalação. "
            + str(config["reason"] or "")
            + " Peça ao administrador da instalação para configurar o app da Meta.",
        )
    authorization_code = str(code or "").strip()
    if not authorization_code:
        raise SignupError("invalid_code", "A Meta não devolveu um código de autorização. Tente conectar de novo.", status_code=400)
    resolved_phone_number_id = _require_meta_id(phone_number_id, "phone_number_id")
    resolved_waba_id = _require_meta_id(waba_id, "waba_id")
    resolved_business_id = _require_meta_id(business_id, "business_id")
    # Número informado pelo popup: o 409 sai antes de qualquer chamada à
    # Meta, sem nem gastar o code. Descoberto depois, a checagem vem em (c).
    if resolved_phone_number_id:
        _ensure_number_is_free(int(company_id), resolved_phone_number_id)

    app_id = str(settings.meta_app_id or "").strip()
    app_secret = str(settings.whatsapp_app_secret or "").strip()
    graph = client or MetaGraphClient()

    # (a) O code vira token de negócio. É a única chamada sem bearer: o que
    # a autentica é o app secret, que vai no corpo do POST, não na URL.
    try:
        exchange = graph.post(
            "oauth/access_token",
            data={"client_id": app_id, "client_secret": app_secret, "code": authorization_code},
        )
    except MetaGraphError as exc:
        raise _translate(exc, "exchange_failed", "A Meta não confirmou a autorização. Tente conectar de novo.") from exc
    access_token = str(exchange.get("access_token") or "").strip()
    if not access_token:
        raise SignupError("exchange_failed", "A Meta não devolveu um token de acesso. Tente conectar de novo.")

    # (b) Descoberta: o popup nem sempre entrega os ids (versões antigas do
    # SDK, sessão fechada cedo). O token sabe a que WABA pertence. O app
    # token (`app_id|app_secret`) autentica a inspeção pelo cabeçalho.
    if not resolved_waba_id:
        try:
            debug = graph.get("debug_token", params={"input_token": access_token}, token=f"{app_id}|{app_secret}")
        except MetaGraphError as exc:
            raise _translate(
                exc,
                "discovery_failed",
                "Não foi possível identificar a conta do WhatsApp Business autorizada.",
            ) from exc
        resolved_waba_id = _require_meta_id(_waba_id_from_debug_token(debug), "waba_id", status_code=502)
        if not resolved_waba_id:
            raise SignupError(
                "discovery_failed",
                "Não foi possível identificar a conta do WhatsApp Business autorizada. "
                "Refaça a conexão e conclua todos os passos do popup da Meta.",
            )

    if not resolved_phone_number_id:
        try:
            numbers = graph.get(f"{resolved_waba_id}/phone_numbers", token=access_token)
        except MetaGraphError as exc:
            raise _translate(exc, "discovery_failed", "Não foi possível listar os números da conta autorizada.") from exc
        resolved_phone_number_id = _require_meta_id(_first_phone_number_id(numbers), "phone_number_id", status_code=502)
        if not resolved_phone_number_id:
            raise SignupError(
                "discovery_failed",
                "A conta do WhatsApp Business não tem nenhum número. "
                "Adicione um número no Gerenciador do WhatsApp e conecte de novo.",
            )
        _ensure_number_is_free(int(company_id), resolved_phone_number_id)

    # (c) O que ainda pode recusar a conexão sem depender da Meta vem antes
    # de qualquer efeito colateral lá: um cofre que não consegue guardar o
    # token (o número de outra empresa já foi checado acima).
    try:
        encrypted_token = encrypt_secret(access_token)
    except CredentialVaultError as exc:
        raise SignupUnavailableError(
            "vault_unavailable",
            "O servidor não está configurado para guardar credenciais (CREDENTIALS_ENCRYPTION_KEY). "
            "Peça ao administrador da instalação.",
        ) from exc

    # (d) Perfil do número para o painel. Lido antes de inscrever e registrar
    # porque também prova que o token alcança exatamente o número informado:
    # um id trocado pelo popup falha aqui, sem ter tocado em nada.
    try:
        profile = graph.get(resolved_phone_number_id, params={"fields": PHONE_PROFILE_FIELDS}, token=access_token)
    except MetaGraphError as exc:
        raise _translate(exc, "profile_failed", "A Meta não devolveu os dados do número conectado.") from exc
    if str(profile.get("id") or "").strip() != resolved_phone_number_id:
        raise SignupError(
            "profile_failed",
            "A Meta devolveu os dados de outro número. Refaça a conexão pelo popup da Meta.",
        )

    # (e) Sem a inscrição do app, a Meta não entrega mensagens recebidas.
    try:
        graph.post(f"{resolved_waba_id}/subscribed_apps", token=access_token)
    except MetaGraphError as exc:
        raise _translate(
            exc,
            "subscribe_failed",
            "A Meta não permitiu inscrever este app para receber as mensagens da conta.",
        ) from exc

    # (f) Registro na Cloud API. O PIN é do número na Meta: se já gravamos um
    # (reconexão, número vindo de outra empresa desta instalação), é ele que
    # a Meta espera; só na primeira vez geramos um novo. Número já registrado
    # responde com erro específico e conta como sucesso — mas aí o PIN que
    # enviamos não foi aplicado, e o que fica gravado é o que já sabíamos
    # (ou nada, para nunca guardar um PIN que a Meta recusou).
    previous_pin = _stored_registration_pin(resolved_phone_number_id)
    pin = previous_pin or _generate_pin()
    registered_pin: str | None = pin
    try:
        graph.post(
            f"{resolved_phone_number_id}/register",
            json={"messaging_product": "whatsapp", "pin": pin},
            token=access_token,
        )
    except MetaGraphError as exc:
        if not _already_registered(exc):
            raise _translate(
                exc,
                "register_failed",
                "A Meta não concluiu o registro do número para a API. "
                "Confira no Gerenciador do WhatsApp se o número está verificado e tente de novo.",
            ) from exc
        registered_pin = previous_pin
        log_event(
            logger,
            logging.INFO,
            "whatsapp.signup.number_already_registered",
            company_id=company_id,
            provider_code=exc.provider_code,
            pin_known=previous_pin is not None,
        )

    try:
        encrypted_pin = encrypt_secret(registered_pin) if registered_pin else None
    except CredentialVaultError as exc:  # pragma: no cover - o token já passou pelo mesmo cofre
        raise SignupUnavailableError(
            "vault_unavailable",
            "O servidor não está configurado para guardar credenciais (CREDENTIALS_ENCRYPTION_KEY). "
            "Peça ao administrador da instalação.",
        ) from exc

    # (g) Persistência. A WABA anterior (se trocada) é lida antes, porque a
    # gravação apaga o token antigo; a Meta só é avisada depois do commit.
    release = _previous_waba_to_release(int(company_id), new_waba_id=resolved_waba_id)
    stored = store_connection(
        int(company_id),
        phone_number_id=resolved_phone_number_id,
        display_phone_number=_clean(str(profile.get("display_phone_number") or "")),
        waba_id=resolved_waba_id,
        business_id=resolved_business_id,
        verified_name=_clean(str(profile.get("verified_name") or "")),
        quality_rating=_clean(str(profile.get("quality_rating") or "")),
        platform_type=_clean(str(profile.get("platform_type") or "")),
        access_secret_encrypted=encrypted_token,
        registration_pin_encrypted=encrypted_pin,
        credential_source=SOURCE_EMBEDDED_SIGNUP_COEXISTENCE if coexistence else SOURCE_EMBEDDED_SIGNUP,
        connected_by=actor,
    )
    if stored is None:
        raise SignupConflictError()
    if release is not None:
        old_binding_id, old_waba, old_token = release
        _unsubscribe_quietly(
            graph,
            company_id=int(company_id),
            binding_id=old_binding_id,
            waba_id=old_waba,
            token=old_token,
            reason="waba_replaced",
        )

    log_event(
        logger,
        logging.INFO,
        "whatsapp.signup.connected",
        company_id=company_id,
        binding_id=stored["id"],
        phone_number_id=mask_identifier(resolved_phone_number_id),
        waba_id=mask_identifier(resolved_waba_id),
        credential_source=stored.get("credential_source"),
        signup_event=_clean(event),
        coexistence=coexistence,
        pin_reused=previous_pin is not None,
    )
    # (h) O status é o mesmo que o painel lê depois: sem segredo.
    return get_whatsapp_channel_status(int(company_id))


def disconnect_targets(company_id: int) -> list[dict[str, Any]]:
    """Os bindings que "Desconectar" limpa, em ordem de id.

    O binding ativo único (com ou sem token) e toda linha da empresa que
    ainda guarde um token de negócio, ativa ou não: uma segunda linha ativa
    criada à mão ou uma desativada pelo endpoint legado de estado não podem
    deixar a credencial fora do alcance do dono.
    """
    targets: dict[int, dict[str, Any]] = {}
    active = get_credentials_for_company(int(company_id))
    if active is not None:
        targets[int(active["id"])] = active
    for row in list_bindings_with_token(int(company_id)):
        targets.setdefault(int(row["id"]), row)
    return [targets[key] for key in sorted(targets)]


def disconnect_embedded_signup(
    company_id: int,
    *,
    actor: str | None,
    client: MetaGraphClient | None = None,
) -> dict[str, Any]:
    """Desfaz a conexão: cancela a inscrição na WABA e apaga o token.

    A chamada à Meta é cortesia — se falhar (token já revogado, rede), o
    dono ainda consegue desconectar; o que importa é o segredo sair do banco.
    A inscrição só é cancelada quando nenhum outro número conectado depende
    da mesma WABA.
    """
    from app.storage.repository import RuntimeRepository

    targets = disconnect_targets(int(company_id))
    if not targets:
        active_count = len(RuntimeRepository().get_whatsapp_bindings(company_id=int(company_id), active_only=True))
        if active_count > 1:
            raise SignupError(
                "ambiguous",
                "Há mais de um número ativo nesta empresa e nenhum foi conectado pelo painel; "
                "desative os vínculos extras na lista de vínculos antes de desconectar.",
                status_code=409,
            )
        raise SignupNotConnectedError("not_connected", "Nenhum número conectado nesta empresa.")

    graph = client or MetaGraphClient()
    for binding in targets:
        binding_id = int(binding["id"])
        encrypted_token = str(binding.get("access_secret_encrypted") or "")
        waba_id = _clean(str(binding.get("waba_id") or ""))
        if encrypted_token and waba_id and not waba_in_use_elsewhere(waba_id, excluding_binding_id=binding_id):
            try:
                token = decrypt_secret(encrypted_token)
            except CredentialVaultError as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "whatsapp.signup.unsubscribe_failed",
                    company_id=company_id,
                    binding_id=binding_id,
                    reason="disconnect",
                    error_type=type(exc).__name__,
                )
            else:
                _unsubscribe_quietly(
                    graph,
                    company_id=int(company_id),
                    binding_id=binding_id,
                    waba_id=waba_id,
                    token=token,
                    reason="disconnect",
                )

        clear_connection(int(company_id), binding_id)
        log_event(
            logger,
            logging.INFO,
            "whatsapp.signup.disconnected",
            company_id=company_id,
            binding_id=binding_id,
            phone_number_id=mask_identifier(str(binding.get("phone_number_id") or "")),
            actor=actor,
        )
    return get_whatsapp_channel_status(int(company_id))
