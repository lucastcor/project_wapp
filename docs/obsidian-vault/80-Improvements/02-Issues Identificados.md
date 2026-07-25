---
title: Issues Identificados
type: improvement
tags: [melhorias, issues, dívida-técnica]
updated: 2026-04-28
---

# Issues Identificados (auditoria 2026-04-28)

Cada item: **observação concreta no código → impacto → recomendação**.

## I-001 — Comparação de token admin não é timing-safe

- **Onde**: `app/services/admin_auth_service.py:resolve_admin_role_from_token` faz `provided_token == configured_token`.
- **Impacto**: timing attack teórico. Em prática, baixo (HTTPS+rate-limit reduzem).
- **Recomendação**: trocar por `hmac.compare_digest`.

## I-002 — Repositório monolítico

- **Onde**: `app/storage/repository.py` (~4 386 linhas).
- **Impacto**: alta fricção em mudanças; risco de regressão lateral; testes lentos para validar todas as áreas tocadas.
- **Recomendação**: refatorar por agregado: `TenantRepository`, `ConversationRepository`, `CalendarRepository`, `TrainingRepository`, `BillingRepository`, `AuditRepository`. Pode ser graduado.

## I-003 — Admin routes monolítico

- **Onde**: `app/api/admin_routes.py` (~1 891 linhas).
- **Impacto**: idem ao I-002 mas no nível de API.
- **Recomendação**: dividir em `app/api/admin/` com rotas por domínio (`calendar.py`, `training.py`, `billing.py`, etc.) sob o mesmo router.

## I-004 — Worker sem locking distribuído

- **Onde**: `app/tools/reminder_worker.py`.
- **Impacto**: múltiplos workers podem dispatchar lembretes/ofertas em duplicidade.
- **Recomendação**: lock no banco (`SELECT FOR UPDATE SKIP LOCKED`) ou Redis lock; ou manter sempre 1 réplica com healthcheck.

## I-005 — Outbound sem retry com backoff

- **Onde**: `app/services/outbound_dispatch_service.py` + `whatsapp_provider.py`.
- **Impacto**: erros transientes (5xx Meta, network blip) viram `failed` definitivo; precisam re-dispatch manual.
- **Recomendação**: política de retry exponencial até N tentativas com `next_retry_at`; flagga como `failed` só após esgotar.

## I-006 — Sanitização de e-mail incompleta

- **Onde**: `app/core/training_config.py:REDACTION_PATTERNS = [("@", "[EMAIL_REDACTED]")]`.
- **Impacto**: substitui apenas `@`, não captura formato completo. Marca presença sem anonimizar.
- **Recomendação**: regex `[\w\.-]+@[\w\.-]+\.\w+` → `[EMAIL]`; idem para CPF/CNPJ/CEP/telefone.

## I-007 — Rate limit in-memory

- **Onde**: `app/services/rate_limit_service.py`.
- **Impacto**: contadores por processo, não global. Múltiplas réplicas → limite multiplicado.
- **Recomendação**: Redis com sliding window log ou token bucket.

## I-008 — Verify token do webhook não usa compare_digest

- **Onde**: `app/api/routes.py:verify_whatsapp_webhook`.
- **Impacto**: baixo (token só usado no handshake inicial).
- **Recomendação**: usar `hmac.compare_digest` por consistência.

## I-009 — Arquivos legados em `app/tools/`

- **Onde**: `app/tools/__init__` (sem extensão), `app/tools/registry.py` (vazio), `app/llm/prompts/system_output.py` (vazio), `app/storage/memory_store.py` (mínimo).
- **Impacto**: ruído no repo; confusão para novos devs.
- **Recomendação**: remover ou documentar propósito.

## I-010 — `app/data/company_contexts/string.json`

- **Onde**: arquivo placeholder com nome `string.json`.
- **Impacto**: provavelmente lixo de teste ou criado sem intenção (slug "string").
- **Recomendação**: limpar ou renomear se ainda for útil.

## I-011 — Dispatch outbound sem lock idempotente

- **Onde**: `app/services/outbound_dispatch_service.py`.
- **Impacto**: chamar `dispatch` em paralelo no mesmo `outbound_message_id` pendente pode tentar enviar duas vezes antes do status virar `sent`.
- **Recomendação**: `UPDATE outbound_messages SET status='dispatching' WHERE id=? AND status='pending'` (ou similar com check do `rowcount`) antes de enviar.

## I-012 — Pool psycopg sem retry-on-disconnect explícito

- **Onde**: `app/storage/db.py:_get_postgres_pool`.
- **Impacto**: falhas de conexão transientes podem propagar como 500.
- **Recomendação**: configurar `kwargs.connect_timeout` e tratar `psycopg.OperationalError` com retry curto.

## I-013 — Logs de payload bruto em alguns pontos

- **Onde**: A revisar em handlers admin que persistem `metadata` em audit; alguns payloads carregam dados sensíveis.
- **Impacto**: PII em audit_events.
- **Recomendação**: helper de scrubbing para `metadata` antes de persistir.

## I-014 — `app/core/state.py` redundante

- **Onde**: `app/core/state.py` é uma camada fina sobre `RuntimeRepository`.
- **Impacto**: duplicação semântica.
- **Recomendação**: avaliar remoção ou expandir para algo útil (cache, estado in-memory).

## I-015 — Sem testes específicos para `whatsapp_provider`, `rate_limit_service`, `realtime_service`

- **Onde**: `tests/`.
- **Impacto**: regressões em integração HTTP do WhatsApp / rate limit não pegas.
- **Recomendação**: adicionar testes mockando `requests` e cenários de limite excedido.

## Próximas leituras

- [[80-Improvements/03-Melhorias de Segurança]]
- [[80-Improvements/04-Melhorias de Escalabilidade]]
- [[80-Improvements/05-Backlog de Produto]]
