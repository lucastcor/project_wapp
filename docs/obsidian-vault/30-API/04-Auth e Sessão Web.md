---
title: Auth e Sessão Web
type: api
tags: [api, auth, sessão, plataforma]
updated: 2026-07-26
---

# Auth e Sessão Web

## Dois mecanismos coexistentes

1. **Identidade SaaS (principal)** — email/senha, usuário durável, membership por
   empresa e sessão opaca revogável.
2. **Token administrativo (break-glass)** — header
   `Authorization: Bearer <token>`/`X-Admin-Token`, ou cookie HMAC legado, para
   CLI, Streamlit e recuperação operacional.

Implementação em `identity_service.py`, `platform_session_service.py` e
`admin_security.py`. A autorização tenta a sessão primeiro; token é fallback.

## Login

```http
POST /platform/auth/session
Content-Type: application/json

{
  "email": "owner@empresa.com",
  "password": "senha-forte-com-12+"
}
```

O login:

1. normaliza o email e verifica o hash scrypt em constant-time;
2. carrega somente memberships e empresas ativas;
3. cria token e CSRF aleatórios;
4. persiste apenas hashes em `user_sessions`;
5. emite cookie HTTP-only e cookie CSRF legível pelo frontend.

TTL: `IDENTITY_SESSION_TTL_SECONDS` (default 12h). Logout revoga a sessão no
banco, não apenas remove o cookie.

O login legado ainda aceita `admin_token`/`admin_actor` e emite cookie
`payload.signature`, com TTL `PLATFORM_SESSION_TTL_SECONDS`.

Cookie attrs:
- `HttpOnly`
- `SameSite=Lax`
- `Path=/`
- `Secure` apenas se `ENVIRONMENT=production` (para localhost dev funcionar).
- `Max-Age = TTL`.

Para identidade há também `project_wapp_csrf` (`SameSite=Lax`, não HTTP-only).
Toda mutação autenticada por identidade exige `X-CSRF-Token` correspondente.

## Cadastro, convite e recuperação

| Método | Path | Regra |
|--------|------|-------|
| GET | `/platform/auth/capabilities` | Expõe se cadastro/reset estão disponíveis. |
| POST | `/platform/auth/register` | Cria usuário owner + empresa quando `ENABLE_SELF_SERVICE_SIGNUP=true`. |
| POST | `/platform/identity/invitations` | Owner/admin convida membro; envio via Resend. |
| POST | `/platform/auth/invitations/accept` | Cria/associa usuário e inicia sessão. |
| GET | `/platform/identity/members?company_id=` | Owner/admin lista os membros do tenant. |
| PATCH | `/platform/identity/members/{id}?company_id=` | Altera papel/estado; preserva ao menos um owner ativo. |
| POST | `/platform/auth/password-reset/request` | Resposta uniforme, sem enumerar conta. |
| POST | `/platform/auth/password-reset/confirm` | Token single-use + senha forte; revoga sessões anteriores. |

`IDENTITY_EXPOSE_ACTION_TOKENS=true` existe apenas para desenvolvimento/teste.
Em produção os tokens devem aparecer somente nos emails.

## Inspecionar sessão

```http
GET /platform/auth/session
```

Retorna `{authenticated, role, actor, expires_at, auth_mode, user_id, email,
memberships}`.

Se cookie ausente, inválido ou expirado → `401`. O frontend trata o 401 abrindo a
tela de login.

## Logout

```http
DELETE /platform/auth/session
```

`platform_session_service.clear_platform_session_cookie(response)` usa `delete_cookie` com mesmas flags.

## Como o cookie é validado

`read_platform_session()` distingue os formatos:

- token sem ponto: sessão opaca de identidade; busca hash, expiração, usuário e
  memberships atuais no banco;
- token com ponto: cookie HMAC legado; valida assinatura, papel e expiração.

Erros: `PlatformSessionError` (causas: token ausente, assinatura, payload, role, expirado).

### Escopo de tenant da sessão

Em identidade o escopo **não** é gravado no cookie: memberships são relidas do
banco a cada request. Remover/desativar membership vale imediatamente.

Para o modo legado, `ADMIN_*_COMPANY_IDS` continua sendo re-derivado da
configuração. Query string nunca amplia o escopo em nenhum modo.

Configuração em `ADMIN_VIEWER_COMPANY_IDS`, `ADMIN_OPERATOR_COMPANY_IDS`,
`ADMIN_SUPERADMIN_COMPANY_IDS` (CSV de ids internos; vazio = todas).
Valor configurado porém não parseável resolve para lista vazia (fail closed).

## Segredo da sessão legada

`_get_session_secret()`:

1. `PLATFORM_SESSION_SECRET` (preferido).
2. Fallback: `ADMIN_SUPERADMIN_TOKEN` → `ADMIN_API_TOKEN` → `WHATSAPP_APP_SECRET` → string fixa `"project-wapp-platform-dev-secret"`.

> ⚠️ Em produção, **sempre** definir `PLATFORM_SESSION_SECRET` forte. Mesmo que
> usuários usem sessões opacas, o segredo protege o caminho break-glass.

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

Resultado: o frontend nunca armazena token de sessão em JS;
`credentials: "same-origin"` envia o cookie, e mutações de identidade adicionam
o header CSRF.

## Sessão como requisito do stream de eventos

`GET /platform/events` exige sessão válida (`require_platform_session`), retornando
`401` para conexão anônima, cookie adulterado ou sessão expirada. O escopo de
empresa da sessão determina quais eventos são entregues. Ver
[[20-Modules/04-Plataforma Frontend]].

## Próximas leituras

- [[50-Security/02-Auth Admin]]
- [[30-API/05-RBAC e Papéis]]
- [[20-Modules/04-Plataforma Frontend]]
