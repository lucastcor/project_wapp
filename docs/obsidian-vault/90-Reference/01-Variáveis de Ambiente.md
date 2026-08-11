---
title: Variáveis de Ambiente
type: reference
tags: [referência, env]
updated: 2026-07-26
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
| `STRICT_RUNTIME_VALIDATION` | `false` | `true` faz API/worker falhar se há blocker; também endurece ingress e desliga fallback de seed. |
| `DISABLE_DOTENV` | `false` | `true` impede `app/core/config.py` de carregar `.env`. Usado pela suíte de testes para não herdar configuração local. |
| `TEST_DATABASE_URL` | — | Somente testes: aponta a suíte para outro banco (ex.: Postgres). Sem ela, a suíte usa um arquivo SQLite descartável. |
| `TEST_REDIS_URL` | — | Somente testes: habilita os testes de integração Redis. |
| `TRUST_PROXY_HEADERS` | Vercel: `true`; outros: `false` | Confia no primeiro hop encaminhado para IP/rate limit. |

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
| `REDIS_URL` | — | Estado compartilhado de rate limit e realtime. |
| `REQUIRE_SHARED_STATE` | `false` | `true` proíbe fallback in-memory e vira blocker de readiness sem Redis. |
| `REALTIME_REDIS_CHANNEL` | `project_wapp:realtime:v1` | Canal pub/sub dos eventos SSE. |

## Banco de dados

| Variável | Default | Função |
|----------|---------|--------|
| `DATABASE_URL` | `sqlite:///project_wapp.db` | URL principal. |
| `POSTGRES_DATABASE_URL` | — | URL para probe explícito. |
| `COMPOSE_DATABASE_URL` | — | URL dentro do compose (`@postgres:5432`). |
| `COMPOSE_POSTGRES_DATABASE_URL` | — | Probe dentro do compose. |
| `DATABASE_POOL_SIZE` | `10` | Pool psycopg. |
| `DATABASE_STATEMENT_TIMEOUT_MS` | `5000` | Timeout em ms. |
| `RUN_MIGRATIONS_ON_STARTUP` | host: `true`; Vercel: `false` | Em produção multi-réplica use `false` e rode migração one-shot. |

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
| `ADMIN_VIEWER_COMPANY_IDS` | — | CSV de ids de empresa visíveis ao papel `viewer` no stream SSE. Vazio = todas. |
| `ADMIN_OPERATOR_COMPANY_IDS` | — | Idem para `operator`. |
| `ADMIN_SUPERADMIN_COMPANY_IDS` | — | Idem para `superadmin`. |
| `PLATFORM_SESSION_SECRET` | — | HMAC do cookie de sessão. Blocker de readiness em produção quando ausente/placeholder. |
| `PLATFORM_SESSION_TTL_SECONDS` | `28800` (8h) | TTL da sessão. |
| `IDENTITY_SESSION_TTL_SECONDS` | `43200` (12h) | TTL da sessão opaca de usuário. |
| `IDENTITY_INVITATION_TTL_HOURS` | `72` | Validade do convite. |
| `IDENTITY_PASSWORD_RESET_TTL_MINUTES` | `30` | Validade do reset. |
| `ENABLE_SELF_SERVICE_SIGNUP` | `false` | Permite criar owner + workspace na UI. |
| `IDENTITY_EXPOSE_ACTION_TOKENS` | `false` | Apenas dev/test; nunca habilitar em produção. |
| `AUDIT_RETENTION_DAYS` | `90` | Janela default de auditoria. |
| `ADMIN_ACTOR` | `streamlit-admin` | Default `X-Admin-Actor` (Streamlit). |
| `ADMIN_APP_API_BASE_URL` | `http://127.0.0.1:8000` | Streamlit → API. |

## Email transacional

| Variável | Default | Função |
|----------|---------|--------|
| `PLATFORM_PUBLIC_URL` | — | URL pública usada nos links de convite/reset. |
| `TRANSACTIONAL_EMAIL_PROVIDER` | `disabled` | `resend` habilita entrega real. |
| `TRANSACTIONAL_EMAIL_FROM` | — | Remetente validado. |
| `RESEND_API_KEY` | — | Credencial Resend. |
| `TRANSACTIONAL_EMAIL_TIMEOUT_SECONDS` | `10` | Timeout HTTP. |

Self-service ativo sem URL/email utilizáveis é blocker de readiness.

## Stripe / billing

| Variável | Default | Função |
|----------|---------|--------|
| `STRIPE_SECRET_KEY` | — | Chave server-side. |
| `STRIPE_WEBHOOK_SECRET` | — | Segredo de assinatura do webhook. |
| `STRIPE_PUBLISHABLE_KEY` | — | Chave pública exposta em capacidades de billing. |
| `STRIPE_SUCCESS_URL` | — | Retorno de Checkout aprovado. |
| `STRIPE_CANCEL_URL` | — | Retorno de Checkout cancelado. |
| `STRIPE_PORTAL_RETURN_URL` | — | Retorno do Customer Portal. |
| `STRIPE_PRICE_ESSENCIAL_MONTHLY/ANNUAL` | — | Price IDs imutáveis. |
| `STRIPE_PRICE_AVANCADO_MONTHLY/ANNUAL` | — | Price IDs imutáveis. |
| `STRIPE_PRICE_PREMIUM_MONTHLY/ANNUAL` | — | Price IDs imutáveis. |
| `BILLING_GRACE_PERIOD_DAYS` | `3` | Janela após falha de pagamento. |
| `STRIPE_WEBHOOK_CLAIM_TTL_SECONDS` | `300` | Recuperação de evento abandonado. |
| `ENABLE_BILLING_ENFORCEMENT` | `false` | Feature flag para aplicar quotas/assinatura. |

## Ingress interno

Autenticação das entradas que **não** carregam assinatura Meta. Ver
[[30-API/01-Endpoints Públicos]] e [[30-API/03-Webhook WhatsApp]].

| Variável | Default | Função |
|----------|---------|--------|
| `ENABLE_NORMALIZED_WHATSAPP_INGRESS` | `false` | Liga o modo normalizado de `POST /webhooks/whatsapp`. Habilitar sem segredo real é blocker de readiness e responde `503`. |
| `INTERNAL_INGRESS_SECRET` | — | Segredo do header `X-Internal-Ingress-Secret` / `Authorization: Bearer`. Obrigatório para o modo normalizado, e para `POST /messages/incoming` em produção. Valores `change-me*` contam como ausentes. |

## Worker

| Variável | Default |
|----------|---------|
| `REMINDER_WORKER_ENABLED` | `false` (compose: true) |
| `REMINDER_WORKER_POLL_SECONDS` | `60` |
| `REMINDER_WORKER_BATCH_SIZE` | `100` |
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
| `OUTBOUND_MAX_ATTEMPTS` | `5` |
| `OUTBOUND_RETRY_BASE_SECONDS` | `30` |
| `OUTBOUND_RETRY_MAX_SECONDS` | `3600` |
| `OUTBOUND_CLAIM_TTL_SECONDS` | `300` |
| `WORKER_LEASE_TTL_SECONDS` | `300` |

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

| Variável | Default | Função |
|----------|---------|--------|
| `WHATSAPP_API_BASE_URL` | — | Base da Cloud API. |
| `WHATSAPP_API_TOKEN` | — | Token de acesso; permanece em ambiente, nunca no banco. |
| `WHATSAPP_PHONE_NUMBER_ID` | — | Número default e alvo do check de readiness. O roteamento por tenant (entrada e saída) vem de `whatsapp_channel_bindings`, não desta variável. |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | — | Handshake do Meta. |
| `WHATSAPP_APP_SECRET` | — | HMAC do webhook. |

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
- [[60-Operations/04-Healthchecks e Readiness]]
