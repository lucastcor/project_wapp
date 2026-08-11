"""Transactional identity email delivery without exposing action tokens."""

from __future__ import annotations

import json
from html import escape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.core.config import is_missing_or_placeholder, settings


class TransactionalEmailError(Exception):
    pass


def _platform_action_url(parameter: str, token: str) -> str:
    base_url = str(settings.platform_public_url or "").strip().rstrip("/")
    if not base_url:
        raise TransactionalEmailError("PLATFORM_PUBLIC_URL is not configured.")
    return f"{base_url}/platform?{parameter}={quote(token, safe='')}"


def _send_email(
    *,
    recipient: str,
    subject: str,
    html: str,
    text: str,
) -> dict[str, Any]:
    provider = settings.transactional_email_provider
    if provider != "resend":
        raise TransactionalEmailError(
            "TRANSACTIONAL_EMAIL_PROVIDER must be configured as resend."
        )
    if is_missing_or_placeholder(settings.resend_api_key):
        raise TransactionalEmailError("RESEND_API_KEY is not configured.")
    sender = str(settings.transactional_email_from or "").strip()
    if "@" not in sender:
        raise TransactionalEmailError("TRANSACTIONAL_EMAIL_FROM is not configured.")

    body = json.dumps(
        {
            "from": sender,
            "to": [recipient],
            "subject": subject,
            "html": html,
            "text": text,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    # The provider endpoint is a fixed HTTPS constant, never caller-controlled.
    request = Request(  # nosec B310
        "https://api.resend.com/emails",
        data=body,
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "project-wapp/transactional-email",
        },
        method="POST",
    )
    try:
        # URL comes from the fixed request constructed above.
        # The Request above is built from a module-level constant HTTPS
        # endpoint; no caller-supplied URL or scheme reaches urlopen.
        with urlopen(  # nosec B310  # noqa: S310
            request,
            timeout=max(1, settings.transactional_email_timeout_seconds),
        ) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise TransactionalEmailError(
            f"Transactional email provider rejected the request ({exc.code})."
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise TransactionalEmailError(
            "Transactional email provider is temporarily unavailable."
        ) from exc

    message_id = str(response_payload.get("id") or "")
    if not message_id:
        raise TransactionalEmailError(
            "Transactional email provider returned no message id."
        )
    return {
        "delivered": True,
        "provider": "resend",
        "message_id": message_id,
    }


def send_invitation_email(
    *,
    recipient: str,
    company_name: str,
    role: str,
    token: str,
) -> dict[str, Any]:
    action_url = _platform_action_url("invitation_token", token)
    safe_company = escape(company_name)
    safe_role = escape(role)
    safe_url = escape(action_url, quote=True)
    return _send_email(
        recipient=recipient,
        subject=f"Convite para acessar {company_name}",
        html=(
            f"<p>Você foi convidado para acessar <strong>{safe_company}</strong> "
            f"com o papel <strong>{safe_role}</strong>.</p>"
            f'<p><a href="{safe_url}">Aceitar convite</a></p>'
            "<p>Se você não esperava este convite, ignore esta mensagem.</p>"
        ),
        text=(
            f"Você foi convidado para acessar {company_name} como {role}.\n"
            f"Aceite o convite: {action_url}\n"
            "Se você não esperava este convite, ignore esta mensagem."
        ),
    )


def send_password_reset_email(
    *,
    recipient: str,
    display_name: str,
    token: str,
) -> dict[str, Any]:
    action_url = _platform_action_url("password_reset_token", token)
    safe_name = escape(display_name)
    safe_url = escape(action_url, quote=True)
    return _send_email(
        recipient=recipient,
        subject="Redefinição de senha",
        html=(
            f"<p>Olá, {safe_name}.</p>"
            "<p>Recebemos uma solicitação para redefinir sua senha.</p>"
            f'<p><a href="{safe_url}">Criar nova senha</a></p>'
            "<p>Se não foi você, ignore esta mensagem.</p>"
        ),
        text=(
            f"Olá, {display_name}.\n"
            f"Crie uma nova senha: {action_url}\n"
            "Se não foi você, ignore esta mensagem."
        ),
    )
