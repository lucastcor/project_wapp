---
title: Auth e Sessão Web
type: api
tags: [api, auth, sessão, plataforma]
updated: 2026-04-28
---

# Auth e Sessão Web

## Dois mecanismos coexistentes

1. **Token administrativo** — header `Authorization: Bearer <token>` ou `X-Admin-Token`. Para clientes técnicos (CLI, Streamlit, integrações).
2. **Sessão web** — cookie `project_wapp_platform_session` HTTP-only assinado com HMAC-SHA256, emitido pela plataforma `/platform`.

Implementação em `app/api/admin_security.py:build_require_admin_access`. Tenta sessão **primeiro**; fallback para token.

## Login

```http
POST /platform/auth/session
Content-Type: application/json

{
  "admin_token": "operator-token-real",
  "admin_actor": "platform-admin"
}
```

`platform_session_service.authenticate_platform_login()`:
1. Se `ENABLE_ADMIN_AUTH=false`, emite sessão `superadmin` (modo dev).
2. Senão resolve papel via `resolve_admin_role_from_token`.
3. `issue_platform_session(role, actor)` → JWT-like custom: `base64(payload).hmac_signature`.

Payload assinado:
```json
{ "role":"operator", "actor":"platform-admin", "iat": 1770000000, "exp": 1770028800, "token":"..." }
```

TTL configurável: `PLATFORM_SESSION_TTL_SECONDS` (default `28800` = 8h, mínimo 300).

Cookie attrs:
- `HttpOnly`
- `SameSite=Lax`
- `Path=/`
- `Secure` apenas se `ENVIRONMENT=production` (para localhost dev funcionar).
- `Max-Age = TTL`.

## Inspecionar sessão

```http
GET /platform/auth/session
```

Retorna `{authenticated, role, actor, expires_at, auth_mode}`.

Se cookie inválido/expirado → `{authenticated: false}` (não 401, para o frontend tratar).

## Logout

```http
DELETE /platform/auth/session
```

`platform_session_service.clear_platform_session_cookie(response)` usa `delete_cookie` com mesmas flags.

## Como o cookie é validado

`platform_session_service.read_platform_session(token)`:

1. Split em payload + signature.
2. `_sign(payload_segment)` → recalcula HMAC-SHA256 com `_get_session_secret()`.
3. `hmac.compare_digest(expected, signature)` (timing-safe).
4. Decodifica base64 → JSON.
5. Valida `role ∈ {viewer, operator, superadmin}` e `exp > now`.

Erros: `PlatformSessionError` (causas: token ausente, assinatura, payload, role, expirado).

## Segredo da sessão

`_get_session_secret()`:

1. `PLATFORM_SESSION_SECRET` (preferido).
2. Fallback: `ADMIN_SUPERADMIN_TOKEN` → `ADMIN_API_TOKEN` → `WHATSAPP_APP_SECRET` → string fixa `"project-wapp-platform-dev-secret"`.

> ⚠️ Em produção, **sempre** definir `PLATFORM_SESSION_SECRET` forte. O fallback final é placeholder marcado como inválido em `is_missing_or_placeholder`.

Tracking em [[50-Security/05-Secrets e Ambiente]].

## Como `/admin/*` aceita sessão

`admin_security.require_admin_access`:

```python
cookie_token = request.cookies.get(COOKIE_NAME)
if cookie_token:
    try:
        session = read_platform_session(cookie_token)
        resolved_role = session["role"]
        request.state.admin_actor = session["actor"]
        auth_mode = "session"
    except PlatformSessionError:
        resolved_role = None

if resolved_role is None:
    resolved_role = resolve_admin_role_from_token(provided_token, ...)
```

Resultado: o frontend nunca precisa armazenar token em JS — `credentials: "same-origin"` envia o cookie automaticamente.

## Próximas leituras

- [[50-Security/02-Auth Admin]]
- [[30-API/05-RBAC e Papéis]]
- [[20-Modules/04-Plataforma Frontend]]
