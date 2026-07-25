---
title: Auth Admin
type: security
tags: [segurança, auth, admin]
updated: 2026-04-28
---

# Auth Admin

Detalhe de fluxo em [[30-API/04-Auth e Sessão Web]] e [[30-API/05-RBAC e Papéis]]. Esta nota foca **modelo de segurança**.

## Ordem de validação

Em `app/api/admin_security.py:require_admin_access`:

1. Se `ENABLE_ADMIN_AUTH=false` → libera tudo como superadmin (modo dev).
2. Lê cookie `project_wapp_platform_session`. Se válido → sessão web.
3. Lê `Authorization: Bearer <token>` ou `X-Admin-Token: <token>` → resolve papel.
4. Se nenhum funcionar → 401 + `audit_events status=failed reason=invalid_admin_credentials`.
5. Se nenhum token configurado → 500 + `audit_events status=misconfigured`.

## Cookie de sessão

`app/services/platform_session_service.py`:

- Formato: `base64url(payload).hmac_sha256_signature`.
- Payload: `{role, actor, iat, exp, token}`.
- TTL: `PLATFORM_SESSION_TTL_SECONDS` (default 8h).
- Assinatura: HMAC-SHA256 com `PLATFORM_SESSION_SECRET`.
- Validação timing-safe via `hmac.compare_digest`.
- Cookie attrs: `HttpOnly; SameSite=Lax; Path=/; Secure(prod)`.

### Segredo da sessão

Hierarquia de fallback (`_get_session_secret`):
1. `PLATFORM_SESSION_SECRET`.
2. `ADMIN_SUPERADMIN_TOKEN`.
3. `ADMIN_API_TOKEN`.
4. `WHATSAPP_APP_SECRET`.
5. `"project-wapp-platform-dev-secret"` (placeholder; flagado como inválido).

> ⚠️ Em produção, **sempre** definir `PLATFORM_SESSION_SECRET` exclusivo. Senão `is_missing_or_placeholder` rodaria em readiness.

## Tokens administrativos

Carregados de env em `app/services/admin_auth_service.py`:

```
ADMIN_VIEWER_TOKEN     -> viewer
ADMIN_OPERATOR_TOKEN   -> operator
ADMIN_SUPERADMIN_TOKEN -> superadmin (preferido)
ADMIN_API_TOKEN        -> superadmin (fallback se SUPERADMIN não setado)
```

Tokens são strings opacas; comparados com `==` em `resolve_admin_role_from_token`. Idealmente seriam comparados com `hmac.compare_digest` para evitar timing attack — **gap menor** registrado em [[80-Improvements/03-Melhorias de Segurança]].

## Defesas implementadas

| Defesa | Onde |
|--------|------|
| Auth opcional desabilitável apenas em dev | `ENABLE_ADMIN_AUTH` + readiness check |
| Cookie HttpOnly + Secure prod | `attach_platform_session_cookie` |
| HMAC timing-safe na sessão | `_decode` em platform_session_service |
| Auditoria persistida | `_record_audit_event` em todos endpoints sensíveis |
| RBAC por peso | `ADMIN_ROLE_HIERARCHY` |
| Logs com `actor_id`, `request_id`, `auth_mode`, `admin_role` | `admin_security.py` |
| Rate limit por escopo `admin` e `platform_auth` | `rate_limit_service.py` |

## Defesas faltantes

- **Comparação de token via `compare_digest`** (em `==` hoje).
- **Bloqueio temporário** (lockout) após N falhas — não implementado.
- **MFA** para login.
- **Identidade individual humana** + revogação imediata.
- **Rotação automática** de secrets.
- **Lista de IPs permitidos** para `/admin/*` (apenas a aplicação que fica atrás de um proxy reverso pode aplicar isso).

Tracking em [[80-Improvements/03-Melhorias de Segurança]].

## Recomendação operacional

1. Setar tokens fortes (UUID v4 ou random 32 bytes hex).
2. Setar `PLATFORM_SESSION_SECRET` exclusivo.
3. Setar `STRICT_RUNTIME_VALIDATION=true` em produção (faz a API falhar se tokens placeholder).
4. Trocar tokens periodicamente (manualmente, hoje).
5. Monitorar `audit_events` filtrado por `status=failed action=authenticate`.

## Próximas leituras

- [[30-API/04-Auth e Sessão Web]]
- [[30-API/05-RBAC e Papéis]]
- [[50-Security/04-Rate Limiting]]
