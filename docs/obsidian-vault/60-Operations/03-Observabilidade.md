---
title: Observabilidade
type: operations
tags: [operação, observabilidade, logs, métricas]
updated: 2026-04-28
---

# Observabilidade

Três pilares atuais: **logs estruturados**, **request_id**, **métricas Prometheus text**.

## Logs

Configuração em `app/core/logging.py`. Variáveis:

| Env | Default |
|-----|---------|
| `LOG_LEVEL` | `INFO` |
| `LOG_JSON` | `true` |

JSON contém:
- `timestamp`
- `level`
- `event` (string descritiva, ex.: `conversation.processing.started`)
- `request_id`
- `http_method`, `http_path`, `client_ip` (do contexto da request)
- payload de keys passadas no `log_event`

Eventos comuns:

| Evento | Origem |
|--------|--------|
| `http.request.started` / `completed` / `failed` / `rate_limited` / `unhandled_exception` | Middleware. |
| `conversation.*` | `orchestrator.py`. |
| `api.whatsapp.webhook.*` | `routes.py`. |
| `admin.auth.*` | `admin_security.py`. |
| `reply.provider.fallback` | `reply_services.py`. |

## Request ID

Middleware `RequestObservabilityMiddleware` (`app/main.py:75-150`):

- Lê `X-Request-ID` do header ou gera `uuid.uuid4().hex`.
- Armazena em `request.state.request_id`.
- Envia para o context do logger.
- Devolve `X-Request-ID` na response.
- Inclui em `audit_events.request_id`.

## Resumo operacional

`GET /admin/observability/summary` retorna totais por entidade:
- contatos, mensagens, pending_questions por status, outbound por status, message_examples, knowledge_entries, finetuning_jobs.

`GET /admin/observability/metrics` retorna **Prometheus text format** (`Content-Type: text/plain; version=0.0.4`).

Métricas exportadas (em `app/api/admin_support.py:build_prometheus_metrics`):
- Totais agregados.
- Distribuições por classification, action, source.
- Status de empresas.

## Tracing

> **Não há tracing distribuído ainda.** Apenas `X-Request-ID` correlaciona request → logs → audit_events.

Próximo passo natural: OpenTelemetry SDK + collector. Tracking em [[80-Improvements/04-Melhorias de Escalabilidade]].

## Masking

`mask_identifier(contact_id)` em `app/core/logging.py` reduz exposição em logs. Mensagens completas **não** são logadas, só metadados (length, classification, source).

## Como debugar um incidente

1. Capturar `X-Request-ID` da resposta com erro.
2. Filtrar logs por `request_id`.
3. Cruzar com `audit_events.request_id` no banco.
4. Se for webhook: olhar `inbound_message_receipts.error_message`.
5. Se for outbound: olhar `outbound_messages.provider_response`.

## Próximas leituras

- [[60-Operations/06-Runbook Produção]]
- [[80-Improvements/04-Melhorias de Escalabilidade]]
