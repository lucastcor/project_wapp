---
title: RBAC e Papéis
type: api
tags: [api, rbac, segurança]
updated: 2026-07-26
---

# RBAC e Papéis Administrativos

## Dois níveis de papel

O caminho SaaS usa um papel de identidade **por empresa**:

```python
ROLE_WEIGHT = {"viewer": 1, "operator": 2, "admin": 3, "owner": 4}
```

Ao entrar em `/admin/*`, o papel daquela membership é convertido para a
permissão administrativa compatível: `viewer→viewer`, `operator→operator` e
`admin/owner→superadmin`. Isso reaproveita a matriz de endpoints sem permitir
que o papel alto de uma empresa seja usado em outra.

Owner/admin podem listar equipe e convidar; somente owner concede
admin/owner. A atualização do último owner ativo falha com `409`.

## Hierarquia administrativa

`app/services/admin_auth_service.py:ADMIN_ROLE_HIERARCHY`:

```python
ADMIN_ROLE_HIERARCHY = {
    "viewer": 1,
    "operator": 2,
    "superadmin": 3,
}
```

Comparação por peso: `superadmin` cobre `operator` e `viewer`. `operator` cobre `viewer`.

## Tokens por papel (break-glass)

Configurados em variáveis de ambiente:

| Variável | Papel | Comentário |
|----------|-------|------------|
| `ADMIN_VIEWER_TOKEN` | viewer | Leitura. |
| `ADMIN_OPERATOR_TOKEN` | operator | Operação cotidiana. |
| `ADMIN_SUPERADMIN_TOKEN` | superadmin | Configuração estrutural. |
| `ADMIN_API_TOKEN` | superadmin (fallback) | Usado se `ADMIN_SUPERADMIN_TOKEN` ausente. |

## Resolução

`resolve_admin_role_from_token(provided_token, settings_obj)`:
- Compara o token recebido com o `role_token_map` usando `secrets_match`
  (`hmac.compare_digest` sobre bytes UTF-8), em tempo constante.
- Percorre **todos** os tokens configurados antes de retornar, para o tempo de
  resposta não revelar qual papel casou.
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

## Matriz endpoint → permissão

A política é declarada explicitamente em `app/api/admin_permissions.py`
(`ADMIN_ENDPOINT_PERMISSIONS`). Ela é a especificação; `tests/test_admin_rbac_matrix.py`
percorre a aplicação em execução e falha quando uma rota mutante está ausente da
matriz, sobrando na matriz, ou ligada a um papel diferente do declarado.

Política do modelo de papéis atual:

- **viewer** — somente leitura; nenhuma mutação administrativa.
- **operator** — escritas operacionais do dia a dia.
- **superadmin** — administração privilegiada de empresa, onboarding, segurança e billing.

### superadmin

| Método | Rota |
|--------|------|
| POST | `/admin/audit-events/cleanup` |
| POST | `/admin/companies` |
| PUT | `/admin/company-context` |
| POST | `/admin/onboarding/setup` |
| PUT | `/admin/whatsapp/bindings` |
| POST | `/admin/whatsapp/bindings/{binding_id}/state` |
| POST | `/admin/calendar/integrations` |
| POST | `/admin/billing/trial` |
| POST | `/admin/billing/activate` |
| POST | `/admin/billing/cancel` |
| POST | `/admin/billing/reactivate` |
| POST | `/admin/billing/change-plan` |
| POST | `/admin/billing/usage/record` |

As rotas administrativas legadas de billing exigem `superadmin`. O caminho de
produto (`/admin/billing/checkout` e `/admin/billing/portal`) faz uma validação
adicional e aceita somente a membership `owner`; token break-glass global
continua disponível para recuperação operacional.

### operator

| Método | Rota |
|--------|------|
| POST | `/admin/calendar/availability/query` |
| POST | `/admin/calendar/queue` |
| POST | `/admin/calendar/bookings` |
| POST | `/admin/calendar/bookings/{booking_id}/reschedule` |
| POST | `/admin/calendar/bookings/{booking_id}/cancel` |
| POST | `/admin/calendar/queue-offers/{offer_id}/respond` |
| POST | `/admin/reminders` |
| POST | `/admin/reminders/dispatch-due` |
| PATCH | `/admin/contacts/{contact_id}` |
| POST | `/admin/pending-questions/{question_id}/answer` |
| POST | `/admin/message-examples` |
| POST | `/admin/training-import` |
| POST | `/admin/outbound-messages/{outbound_message_id}/mark-sent` |
| POST | `/admin/outbound-messages/{outbound_message_id}/dispatch` |
| POST | `/admin/outbound-messages/dispatch-pending` |
| POST | `/admin/finetuning/jobs` |
| POST | `/admin/analytics/bookings/{booking_id}/outcome` |
| POST | `/admin/analytics/bookings/{booking_id}/cancellation-reason` |
| POST | `/admin/simulator/messages` |

### viewer

Nenhuma mutação. Rotas `GET` permanecem acessíveis a `viewer`, `operator` e
`superadmin` (a autenticação genérica continua sendo o único requisito de leitura).

> **Autenticação não implica autorização.** `require_admin_access` apenas resolve
> a identidade e o papel; toda rota mutante carrega adicionalmente
> `Depends(require_admin_role(...))`. Uma rota mutante sem essa dependência é um
> erro detectado pelo teste da matriz.

## Auditoria

Tudo que muda estado registra `audit_events` com:
- `actor_id` (header `X-Admin-Actor`)
- `admin_role` e `auth_mode` resolvidos
- `request_id`, `http_method`, `http_path`, `client_ip`, `user_agent`
- `action`, `resource_type`, `resource_id`, `company_id`, `status`
- `metadata` (JSON livre, ex.: payload normalizado)

Cobertura de auditoria relevante para esta release:
- Mutações privilegiadas bem-sucedidas (empresa, contexto, onboarding, billing).
- `POST /admin/billing/usage/record` (`action=record_usage`,
  `resource_type=billing_usage`), com `status=skipped` quando não há assinatura.
- Alterações de company context, com `revision` e `schema_version` nos metadados
  (o payload em si **não** vai para a auditoria).
- Autorizações negadas (`status=denied`, com `current_role` e `required_role`).

Nenhum token, segredo ou metadado de pagamento é gravado.

Detalhe em [[50-Security/07-Auditoria]].

## Modo desabilitado

Quando `ENABLE_ADMIN_AUTH=false`:
- `require_admin_access` define `admin_role=superadmin` para todo request.
- Útil só para dev local.
- Em produção (`ENVIRONMENT=production` ou `STRICT_RUNTIME_VALIDATION=true`), `runtime_guard_service` bloqueia subida se admin auth está desabilitado.

## Escopo de tenant

Para identidade, o escopo vem das memberships ativas relidas do banco a cada
request. A rota exige `company_id`/`company_slug` explícito quando o recurso é
tenant-scoped e recalcula o papel para a empresa alvo.

Para tokens legados, cada papel pode ser restrito a um conjunto de empresas via
`ADMIN_VIEWER_COMPANY_IDS`, `ADMIN_OPERATOR_COMPANY_IDS` e
`ADMIN_SUPERADMIN_COMPANY_IDS`.

Essas variáveis recebem lista separada por vírgula; vazio significa "todas as
empresas" para o mecanismo legado.

Um valor configurado mas não parseável resolve para lista vazia (fail closed:
nenhuma empresa), nunca para "todas".

## Limites

- **Tokens estáticos legados** — sem rotação automática; devem permanecer
  restritos a break-glass/CLI.
- **Sem MFA/SSO** — diretório, membership, sessão revogável e gestão de equipe
  existem, mas segundo fator/OIDC continuam no backlog.
- **Sem expiração de token legado** — sessões web de identidade têm TTL e
  revogação; os tokens break-glass dependem de rotação de segredo.

Tracking de evolução em [[80-Improvements/03-Melhorias de Segurança]].

## Próximas leituras

- [[30-API/04-Auth e Sessão Web]]
- [[50-Security/02-Auth Admin]]
- [[50-Security/07-Auditoria]]
