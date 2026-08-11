---
title: Billing e Planos
type: operations
tags: [operação, billing, planos]
updated: 2026-07-26
---

# Billing e Planos

`app/services/billing_service.py`,
`app/services/stripe_billing_service.py`, `app/api/billing_routes.py` e tabelas
`billing_*`.

> [!important]
> O Stripe é autoritativo para pagamento. Redirect de Checkout nunca ativa uma
> assinatura; somente um webhook assinado e reconciliado altera o acesso.

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

## Endpoints de produto

| Método | Path | Função |
|--------|------|--------|
| POST | `/platform/billing/checkout` | Cria Stripe Checkout para owner. Exige `X-Idempotency-Key`. |
| POST | `/platform/billing/portal` | Cria sessão do Customer Portal para owner. |
| POST | `/webhooks/stripe` | Verifica assinatura, registra ledger e reconcilia o estado canônico. |

Os endpoints abaixo permanecem como operação administrativa/break-glass, não
como jornada normal do produto.

## Endpoints administrativos

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

## Webhook e reconciliação

- Assinatura validada por `STRIPE_WEBHOOK_SECRET`.
- Ledger único por `(provider, event_id)` com hash do payload.
- Replay com payload diferente falha fechado.
- Eventos em `processing` podem ser recuperados após
  `STRIPE_WEBHOOK_CLAIM_TTL_SECONDS`.
- Evento antigo ou regressão terminal não sobrescreve estado mais novo.
- Para invoice, o serviço busca a subscription canônica antes de reconciliar.
- `invoice.payment_failed` entra em grace period; `invoice.paid` limpa a falha.

## Enforcement real

Controlado por `ENABLE_BILLING_ENFORCEMENT` para rollout seguro:

- **Hard limit transacional**: bookings, campanhas outbound e jobs de
  fine-tuning reservam uso antes do efeito; falha posterior devolve a reserva.
- **Soft meter/fail-open**: mensagens e handoffs registram uso, mas telemetria
  de cobrança nunca derruba o canal do cliente.
- Assinatura expirada/cancelada é rejeitada fora do grace period.
- Métricas degradáveis mantêm a semântica definida em `DEGRADED_METRICS`.

## Variáveis Stripe

- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
  `STRIPE_PUBLISHABLE_KEY`;
- `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL`,
  `STRIPE_PORTAL_RETURN_URL`;
- seis `STRIPE_PRICE_<PLANO>_<CICLO>`;
- `BILLING_GRACE_PERIOD_DAYS`,
  `STRIPE_WEBHOOK_CLAIM_TTL_SECONDS`.

Readiness só exige a configuração Stripe completa quando enforcement está
ativo. Use inicialmente chaves e prices de test mode.

## Próximas leituras

- [[20-Modules/05-Services]] (linha billing_service)
- [[40-Data/02-Schema do Banco]] (tabelas billing_*)
