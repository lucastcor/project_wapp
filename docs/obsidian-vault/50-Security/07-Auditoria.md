---
title: Auditoria
type: security
tags: [segurança, auditoria, audit-events]
updated: 2026-04-28
---

# Auditoria

Tabela `audit_events` (migração 002 + colunas de contexto na 020).

## O que é registrado

| Evento | Quando |
|--------|--------|
| `authenticate` | Tentativa de auth admin (success/failed/misconfigured). |
| `authorize` | Tentativa de acesso a papel insuficiente (denied). |
| `create_*`, `update_*`, `cancel_*`, `dispatch_*`, etc. | Ações operacionais administrativas. |
| `cleanup_audit_events` | Limpeza da própria auditoria. |
| `mark_outbound_sent`, `dispatch_outbound`, `dispatch_pending`. | Outbound. |
| `answer_pending_question`. | Resolver handoff. |
| `create_company`, `update_company_context`. | Configuração estrutural. |
| `create_calendar_*`, `cancel_booking`, `reschedule_booking`. | Agenda. |
| `import_training_records`, `create_finetuning_job`. | Treino. |
| `start_trial`, `change_plan`, `cancel_subscription`, etc. | Billing. |

## Schema relevante

```
audit_events:
  id, actor_id, action, resource_type, resource_id,
  company_id, status, request_id, metadata (JSON),
  http_method, http_path, created_at
```

Detalhe completo em [[40-Data/02-Schema do Banco]].

## Quem grava

- `app/api/admin_security.py` em `require_admin_access` e `build_require_admin_role` — auth/authorize.
- `app/api/admin_routes.py` em cada handler que muda estado, via `_record_audit_event(...)`.
- Helper centralizado: `app/api/admin_support.py:record_audit_event`.

## Como consultar

`GET /admin/audit-events?limit=&action=&resource_type=` (viewer).

Resposta: lista ordenada decrescente por `created_at`. Inclui `metadata` JSON parseado.

## Retenção

`AUDIT_RETENTION_DAYS=90` (env). Janela default. Cleanup:

```http
POST /admin/audit-events/cleanup?older_than_days=90
```

(superadmin). Operação em `repository.cleanup_audit_events`.

Para ambientes maiores, considerar archive externo (S3, Glacier) antes de deletar.

## Conteúdo de `metadata`

JSON livre. Por convenção, contém:
- `payload` resumido (sem segredos).
- `current_role` / `required_role` em authorize denied.
- `reason` em authenticate failed/misconfigured.
- `record_count` em imports.

Atenção: payloads admins podem incluir nomes/contatos/IDs. Trate `metadata` como dado sensível.

## Limites

- **Sem hash chain** entre eventos — não há proteção contra reescrita pelo próprio admin do banco.
- **Sem export contínuo** para SIEM externo.
- **Retenção curta** por padrão (90d).

## Recomendações

1. Exportar `audit_events` para S3/Glacier semanalmente em deploys reais.
2. Monitorar `status=failed` agregando por `actor_id` e `http_path`.
3. Considerar adicionar `previous_event_id` + hash para integridade futura.

## Próximas leituras

- [[30-API/05-RBAC e Papéis]]
- [[40-Data/02-Schema do Banco]]
