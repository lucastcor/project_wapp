---
title: RBAC e Papéis
type: api
tags: [api, rbac, segurança]
updated: 2026-04-28
---

# RBAC e Papéis Administrativos

## Hierarquia

`app/services/admin_auth_service.py:ADMIN_ROLE_HIERARCHY`:

```python
ADMIN_ROLE_HIERARCHY = {
    "viewer": 1,
    "operator": 2,
    "superadmin": 3,
}
```

Comparação por peso: `superadmin` cobre `operator` e `viewer`. `operator` cobre `viewer`.

## Tokens por papel

Configurados em variáveis de ambiente:

| Variável | Papel | Comentário |
|----------|-------|------------|
| `ADMIN_VIEWER_TOKEN` | viewer | Leitura. |
| `ADMIN_OPERATOR_TOKEN` | operator | Operação cotidiana. |
| `ADMIN_SUPERADMIN_TOKEN` | superadmin | Configuração estrutural. |
| `ADMIN_API_TOKEN` | superadmin (fallback) | Usado se `ADMIN_SUPERADMIN_TOKEN` ausente. |

## Resolução

`resolve_admin_role_from_token(provided_token, settings_obj)`:
- Compara o token recebido com o `role_token_map`.
- Retorna o papel correspondente ou `None`.

## Decorator de papel

`build_require_admin_role(required_role, ...)` cria um `Depends` que:
- Lê `request.state.admin_role` (preenchido em `require_admin_access`).
- Compara peso atual com peso requerido.
- Se insuficiente → 403 + `audit_events status=denied`.

Uso típico:

```python
@admin_router.post(
    "/calendar/integrations",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_calendar_integration_endpoint(...):
    ...
```

## Convenção por endpoint

Resumida em [[30-API/02-Endpoints Admin]]. Heurística:

| Tipo de operação | Papel mínimo |
|------------------|---------------|
| Leitura | viewer |
| Mudar dados operacionais (booking, outbound, contato, exemplo) | operator |
| Mudar config estrutural (company, integration, contexto, cleanup auditoria) | superadmin |

## Auditoria

Tudo que muda estado registra `audit_events` com:
- `actor_id` (header `X-Admin-Actor`)
- `request_id`
- `action`, `resource_type`, `resource_id`, `status`
- `metadata` (JSON livre, ex.: payload normalizado)

Detalhe em [[50-Security/07-Auditoria]].

## Modo desabilitado

Quando `ENABLE_ADMIN_AUTH=false`:
- `require_admin_access` define `admin_role=superadmin` para todo request.
- Útil só para dev local.
- Em produção (`ENVIRONMENT=production` ou `STRICT_RUNTIME_VALIDATION=true`), `runtime_guard_service` bloqueia subida se admin auth está desabilitado.

## Limites

- **Tokens estáticos** — sem rotação automática, sem identidade individual além do header `X-Admin-Actor`.
- **Sem audit por papel separado** — tabela `audit_events` não armazena papel resolvido.
- **Sem expiração de token** — só sessão web tem TTL.

Tracking de evolução em [[80-Improvements/03-Melhorias de Segurança]].

## Próximas leituras

- [[30-API/04-Auth e Sessão Web]]
- [[50-Security/02-Auth Admin]]
- [[50-Security/07-Auditoria]]
