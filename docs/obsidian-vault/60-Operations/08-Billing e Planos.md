---
title: Billing e Planos
type: operations
tags: [operação, billing, planos]
updated: 2026-04-28
---

# Billing e Planos

`app/services/billing_service.py` + tabelas `billing_*`.

## 3 Planos

| Plano | Preço mensal (cents) | Preço anual mensal (cents) | Mensagens | Bookings | Outbound campaigns | Handoffs | Finetuning jobs | Reminders/booking |
|-------|---------------------|--------------------------|-----------|----------|--------------------|----------|-----------------|-------------------|
| Essencial | 44 700 | 39 700 | 3 000 | 200 | 2 | 50 | 0 | 2 |
| Avancado | 69 700 | 59 700 | 10 000 | 800 | 10 | 200 | 1 | 3 |
| Premium | 119 700 | 99 700 | 30 000 | 3 000 | 50 | 1 000 | 5 | 5 |

Features:
- `finetuning`: avancado+ e premium.
- `priority_support`: avancado+ e premium.
- `dedicated_support`: premium.

## Trial

`TRIAL_DAYS=7`, `TRIAL_PLAN="avancado"`. POST `/admin/billing/trial`.

## Métricas de uso

`USAGE_METRICS = {messages, bookings, outbound_campaigns, handoffs, finetuning_jobs}`. Persistidas em `billing_usage_records`.

`DEGRADED_METRICS = {outbound_campaigns, handoffs, finetuning_jobs}` — ao atingir limite, degrada operação dessa funcionalidade (não bloqueia mensagem do cliente).

`ALERT_THRESHOLD = 0.80` — emite warning quando atinge 80% do limite.

## Endpoints

| Método | Path | Função |
|--------|------|--------|
| GET | `/admin/billing/plans` | Lista planos. |
| POST | `/admin/billing/trial` | Inicia trial de 7 dias. |
| POST | `/admin/billing/activate` | Ativa subscription paga. |
| POST | `/admin/billing/cancel` | Cancela. |
| POST | `/admin/billing/reactivate` | Reativa cancelada. |
| POST | `/admin/billing/change-plan` | Upgrade/downgrade. |
| GET | `/admin/billing/usage?company_id=` | Resumo de uso atual. |
| POST | `/admin/billing/usage/record` | Registra uso (idempotente por período). |
| GET | `/admin/billing/check-limit?company_id=&metric=` | Verifica se está dentro do limite. |

## Como o sistema consome

Hoje `check_billing_limit` é exposto, mas o **enforcement** automático no fluxo do bot ainda é parcial. Ou seja, a integração entre billing e operação é **lookup-based** mais que **bloqueante**. Tracking em [[80-Improvements/05-Backlog de Produto]].

## Próximas leituras

- [[20-Modules/05-Services]] (linha billing_service)
- [[40-Data/02-Schema do Banco]] (tabelas billing_*)
