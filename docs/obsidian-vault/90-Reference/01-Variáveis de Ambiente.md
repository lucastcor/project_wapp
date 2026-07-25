---
title: Variáveis de Ambiente
type: reference
tags: [referência, env]
updated: 2026-04-28
---

# Variáveis de Ambiente

Referência consolidada. Definições em `app/core/config.py:Settings`. Defaults entre parênteses; quando não há default, é obrigatório no contexto descrito.

## App / runtime

| Variável | Default | Função |
|----------|---------|--------|
| `APP_NAME` | `Chatbot API` | Título da API. |
| `APP_VERSION` | `1.0.0` | Versão exposta. |
| `ENVIRONMENT` | `development` | `production` endurece checks. |
| `DEBUG` | `true` | Detalhes de erro. |
| `LOG_LEVEL` | `INFO` | Nível dos logs. |
| `LOG_JSON` | `true` | Logs estruturados. |
| `STRICT_RUNTIME_VALIDATION` | `false` | `true` faz API/worker falhar se há blocker. |

## Rate limiting

| Variável | Default | Função |
|----------|---------|--------|
| `ENABLE_RATE_LIMITING` | `false` | Liga middleware. |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Janela. |
| `RATE_LIMIT_AUTH_REQUESTS` | `10` | `/platform/auth/session`. |
| `RATE_LIMIT_ADMIN_REQUESTS` | `180` | `/admin/*`. |
| `RATE_LIMIT_INCOMING_REQUESTS` | `120` | `/messages/incoming`. |
| `RATE_LIMIT_WEBHOOK_REQUESTS` | `300` | `/webhooks/whatsapp`. |
| `RATE_LIMIT_EXEMPT_LOCALHOST` | `false` | Pula 127.0.0.1. |

## Banco de dados

| Variável | Default | Função |
|----------|---------|--------|
| `DATABASE_URL` | `sqlite:///project_wapp.db` | URL principal. |
| `POSTGRES_DATABASE_URL` | — | URL para probe explícito. |
| `COMPOSE_DATABASE_URL` | — | URL dentro do compose (`@postgres:5432`). |
| `COMPOSE_POSTGRES_DATABASE_URL` | — | Probe dentro do compose. |
| `DATABASE_POOL_SIZE` | `10` | Pool psycopg. |
| `DATABASE_STATEMENT_TIMEOUT_MS` | `5000` | Timeout em ms. |

## Backups

| Variável | Default |
|----------|---------|
| `OPERATIONS_BACKUP_DIR` | `backups` |

## Admin auth / sessão

| Variável | Default | Função |
|----------|---------|--------|
| `ENABLE_ADMIN_AUTH` | `false` | Liga auth obrigatório. |
| `ADMIN_API_TOKEN` | — | Fallback superadmin. |
| `ADMIN_VIEWER_TOKEN` | — | Token viewer. |
| `ADMIN_OPERATOR_TOKEN` | — | Token operator. |
| `ADMIN_SUPERADMIN_TOKEN` | — | Token superadmin. |
| `PLATFORM_SESSION_SECRET` | — | HMAC do cookie de sessão. |
| `PLATFORM_SESSION_TTL_SECONDS` | `28800` (8h) | TTL da sessão. |
| `AUDIT_RETENTION_DAYS` | `90` | Janela default de auditoria. |
| `ADMIN_ACTOR` | `streamlit-admin` | Default `X-Admin-Actor` (Streamlit). |
| `ADMIN_APP_API_BASE_URL` | `http://127.0.0.1:8000` | Streamlit → API. |

## Worker

| Variável | Default |
|----------|---------|
| `REMINDER_WORKER_ENABLED` | `false` (compose: true) |
| `REMINDER_WORKER_POLL_SECONDS` | `60` |
| `REMINDER_WORKER_DISPATCH_IMMEDIATELY` | `true` |
| `REMINDER_WORKER_CHANNEL` | `""` |
| `REMINDER_WORKER_AUDIENCES` | `customer,internal` |
| `APPOINTMENT_QUEUE_AUTO_DISPATCH` | `true` |
| `APPOINTMENT_QUEUE_RESPONSE_WINDOW_MINUTES` | `30` |
| `APPOINTMENT_QUEUE_WAIT_FOLLOWUP_MINUTES` | `30` |

## LLM

| Variável | Default |
|----------|---------|
| `OPENAI_API_KEY` | — |
| `OPENAI_MODEL` | `gpt-4o-mini` |
| `OPENAI_FINETUNED_MODEL` | — |
| `USE_FINETUNED_MODEL_FOR_REPLIES` | `false` |
| `OPENAI_FINETUNING_BASE_MODEL` | `gpt-4o-mini-2024-07-18` |
| `OPENAI_FINETUNING_SUFFIX` | `project-wapp-style` |
| `ENABLE_FINETUNING_PIPELINE` | `false` |
| `CLASSIFICATION_PROVIDER` | `mock` |

## Outbound

| Variável | Default |
|----------|---------|
| `OUTBOUND_PROVIDER` | `mock` |
| `OUTBOUND_DEFAULT_CHANNEL` | `whatsapp` |

## Calendar

| Variável | Default |
|----------|---------|
| `CALENDAR_PROVIDER` | `mock` |
| `CALENDAR_TIMEZONE` | `America/Sao_Paulo` |
| `CALENDAR_DEFAULT_WORKDAY_START` | `09:00` |
| `CALENDAR_DEFAULT_WORKDAY_END` | `18:00` |
| `CALENDAR_SLOT_INCREMENT_MINUTES` | `30` |
| `GOOGLE_CALENDAR_CLIENT_ID` | — |
| `GOOGLE_CALENDAR_CLIENT_SECRET` | — |
| `GOOGLE_CALENDAR_REFRESH_TOKEN` | — |
| `GOOGLE_CALENDAR_ID` | — |
| `CALDAV_URL` | — |
| `CALDAV_USERNAME` | — |
| `CALDAV_PASSWORD` | — |
| `CALDAV_CALENDAR_NAME` | — |

## WhatsApp

| Variável | Default |
|----------|---------|
| `WHATSAPP_API_BASE_URL` | — |
| `WHATSAPP_API_TOKEN` | — |
| `WHATSAPP_PHONE_NUMBER_ID` | — |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | — |
| `WHATSAPP_APP_SECRET` | — |

## Postgres (compose)

| Variável | Default |
|----------|---------|
| `POSTGRES_DB` | `project_wapp` |
| `POSTGRES_USER` | `project_wapp` |
| `POSTGRES_PASSWORD` | `project_wapp` |
| `POSTGRES_PORT` | `5432` |
| `APP_PORT` | `8000` |
| `ADMIN_PORT` | `8501` |

## Próximas leituras

- [[60-Operations/02-Configuração de Ambiente]]
- [[50-Security/05-Secrets e Ambiente]]
