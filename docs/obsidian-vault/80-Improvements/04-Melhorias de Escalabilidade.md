---
title: Melhorias de Escalabilidade
type: improvement
tags: [melhorias, escalabilidade]
updated: 2026-07-26
---

# Melhorias de Escalabilidade

## Eixos

### E-001 — Rate limit compartilhado
- **Estado**: concluído no Release 1B.
- **Hoje**: decisão atômica e contador compartilhado no Redis; fallback local
  restrito a desenvolvimento.

### E-002 — Worker robusto
- **Estado**: fundação concluída no Release 1B.
- **Hoje**: claim/lease no banco, retry com backoff, dead-letter e recuperação
  de claims abandonados permitem múltiplas réplicas.
- **Depois**: adotar uma fila dedicada somente quando volume/latência justificarem.

### E-003 — Tracing distribuído
- **Hoje**: apenas `X-Request-ID`.
- **Para**: OpenTelemetry SDK + collector + Jaeger/Tempo. Trace cross-service (api ↔ worker ↔ DB ↔ OpenAI).

### E-004 — Métricas de LLM
- **Hoje**: nada explícito.
- **Para**: latência, tokens, custo por `request_id`/`company_id` em `/admin/observability/metrics`.

### E-005 — Migração para Alembic (ou similar)
- **Hoje**: lista interna `MIGRATIONS` em `db.py`.
- **Para**: Alembic com `alembic.ini`, `versions/`. Permite downgrade e dry-run.

### E-006 — Particionamento por tenant
- **Hoje**: tudo na mesma instância Postgres, mesma tabela.
- **Para**: schema por empresa em volumes grandes ou DB por empresa em casos extremos.

### E-007 — Refatoração `repository.py` por agregado
- **Tracking**: I-002.

### E-008 — Refatoração `admin_routes.py` por domínio
- **Tracking**: I-003.

### E-009 — Cache de leitura
- **Hoje**: cada request lê banco direto.
- **Para**: cache leve (Redis ou `cachetools`) para `company_context`, `billing_plans`, etc.

### E-010 — Connection pool por tenant
- **Hoje**: pool global.
- **Para**: pools por `company_id` em deploys com tenants pesados.

### E-011 — Realtime WebSocket dedicado
- **Hoje**: SSE mantém conexões por processo e Redis Pub/Sub distribui eventos
  entre réplicas.
- **Para**: adicionar replay/cursor durável; WebSocket apenas se comunicação
  bidirecional em tempo real se tornar necessária.

### E-012 — Async I/O para chamadas de provider
- **Hoje**: `requests` síncrono em `whatsapp_provider`.
- **Para**: `httpx` async. Ganho real só com `await` no caminho de envio.

### E-013 — Prometheus exporter completo
- **Hoje**: `/admin/observability/metrics` exporta texto.
- **Para**: instrumentação dedicada (`prometheus_client` lib) com counters, histograms, gauges padronizados.

### E-014 — Index audit
- **Hoje**: índices criados, mas sem benchmark documentado.
- **Para**: rodar `EXPLAIN ANALYZE` em queries críticas (mensagens por contato/empresa, bookings por janela) e ajustar.

### E-015 — Read-replicas Postgres
- **Para**: leituras pesadas (analytics) em replica.

## Próximas leituras

- [[80-Improvements/01-Limites Conhecidos]]
- [[80-Improvements/02-Issues Identificados]]
- [[80-Improvements/05-Backlog de Produto]]
