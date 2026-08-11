---
title: Limites Conhecidos
type: improvement
tags: [melhorias, limites]
updated: 2026-07-26
---

# Limites Conhecidos

Snapshot honesto do que **ainda não está pronto** ou tem dívida arquitetural.

## Funcionais

| Limite | Onde | Tracking |
|--------|------|----------|
| Job de fine-tuning concluído com sucesso em produção real | `finetuning_service.py` | [[80-Improvements/05-Backlog de Produto]] |
| Provider de WhatsApp em produção sem hardening operacional (retries/backoff/métricas) | `whatsapp_provider.py` | [[80-Improvements/02-Issues Identificados]] |
| Calendar provider default = `mock` | `calendar_service.py` | [[80-Improvements/05-Backlog de Produto]] |
| Provider Instagram | — | [[80-Improvements/05-Backlog de Produto]] |
| Promoção automática `knowledge_entries` → `company_context` | `knowledge_service.py` | [[80-Improvements/05-Backlog de Produto]] |
| Templates WhatsApp para outbound proativo fora da janela 24h | — | Backlog |
| Suporte a mídia (imagem/áudio) no webhook | `routes.py` | Backlog |

## Arquiteturais

| Limite | Detalhe |
|--------|---------|
| Migrações internas (não Alembic) | Funcional, mas sem rollback nem dry-run; evolução desejável. |
| `repository.py` monolito (~4 526 linhas) | Refatorar por agregado (Tenant, Conversation, Calendar, Training, Billing, Audit). |
| `admin_routes.py` (~2 054 linhas) | Mesmo problema; dividir por domínio. |
| Hub SSE mantém conexões em processo | Fan-out já usa Redis; cursor/replay durável ainda não existe. |
| Sem RLS no banco | Há FKs e guards cross-tenant; RLS continua como defesa adicional futura. |
| Modelo duplo de contato | `contacts.company_id` legado convive com `contact_company_profiles`. Ver I-019. |
| Sem connection pool por tenant | Pool global compartilhado. |
| Worker contínuo fora do Vercel | Exige serviço durável/scheduler; lease já impede duplicidade entre réplicas. |

## Segurança

| Limite | Detalhe |
|--------|---------|
| Tokens estáticos ainda existem | Somente break-glass/compatibilidade; rotação automática pendente. |
| Sem MFA/SSO | Identidade própria existe, mas sem segundo fator/OIDC. |
| Sem WAF | CSP e headers existem; WAF/rede dependem do ambiente. |
| Sanitização de PII rudimentar | Markers + redação de `@`. |
| Sem hash chain em audit_events | Sem proteção contra reescrita por admin do banco. |

## Observabilidade

| Limite | Detalhe |
|--------|---------|
| Sem tracing distribuído | Apenas `X-Request-ID` nos logs. |
| Sem export contínuo de logs/audits para SIEM | Operação manual. |
| Sem métricas de LLM (latência, token usage, custo) | Tracking implícito apenas. |

## UX / Plataforma

| Limite | Detalhe |
|--------|---------|
| SPA sem framework | Boa simplicidade, mas escalabilidade de UI futura tem custo. |
| Sem PWA / mobile dedicado | — |
| SSE sem replay/cursor | Redis entrega fan-out, mas reconexão não repõe eventos perdidos. |

## Streamlit (legado)

- Não tem RBAC granular (usa token superadmin).
- Re-render completo a cada interação.

## Onde "não" significa "não está implementado" vs "não é possível"

A maioria dos limites é "ainda não fiz" — o sistema está num bom estado para piloto. Os limites mais críticos para sair de piloto:
1. Hardening WhatsApp produtivo (retries, backoff, métricas de entrega).
2. Ativação de MFA/SSO opcional e operação dos providers externos em staging.
3. Tracing distribuído.
4. Sanitização PII robusta antes do fine-tuning real.

## Próximas leituras

- [[80-Improvements/02-Issues Identificados]]
- [[80-Improvements/03-Melhorias de Segurança]]
- [[80-Improvements/04-Melhorias de Escalabilidade]]
- [[80-Improvements/05-Backlog de Produto]]
