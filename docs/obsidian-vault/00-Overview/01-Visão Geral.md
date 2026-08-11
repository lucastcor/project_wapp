---
title: Visão Geral
type: overview
tags: [overview, produto, mvp]
updated: 2026-07-26
---

# Visão Geral do Project WAPP

## Pitch curto

**Project WAPP** é um backend de atendimento conversacional **multiempresa** orientado a operação comercial, construído em **FastAPI** (Python 3.13) com persistência em **Postgres** (SQLite como fallback). Ele recebe mensagens de clientes (genéricas ou via webhook do WhatsApp Cloud API), classifica intenção, decide se a IA pode responder sozinha, escala para humano quando preciso, gerencia agenda/lembretes e prepara dataset para fine-tuning.

## Quatro frentes principais

1. **Recepção e decisão** — receber mensagens, classificar com OpenAI ou heurística e decidir auto-reply, review ou handoff.
2. **Resposta humana assistida** — pending questions com normalização, customer-facing rewrite e knowledge base incremental.
3. **Operação comercial** — pipeline por estágio, memória do contato, outbound persistido com dispatch desacoplado.
4. **Agenda viva** — bookings, perfis de agendamento, fila de remanejamento, lembretes e fluxo conversacional natural.

Detalhe técnico em [[10-Architecture/01-Arquitetura em Camadas]].

## Capacidades já entregues

- Orquestrador único em `app/core/orchestrator.py` que coordena classificação, decisão, reply, memória, pipeline e handoff.
- Conversa natural para **agendar / cancelar / reagendar** pelo cliente, e **operação interna** ("marca o cliente Maria amanhã às 10h") por contatos autorizados em `internal_operation.operator_contact_ids`.
- **Pipeline de fine-tuning** com curadoria, dedup, split treino/validação, export JSON/JSONL e jobs persistidos.
- Provider real do WhatsApp Cloud API em `app/services/whatsapp_provider.py` com handshake e validação HMAC SHA-256 do header `X-Hub-Signature-256`.
- **Plataforma web dedicada** em `/platform` (SPA sem framework) com Control
  Tower, Analytics, Bot Studio, Flow Studio, Agenda, Contacts, Training, Equipe,
  Billing e Setup Wizard. Detalhes em [[20-Modules/04-Plataforma Frontend]].
- **Painel Streamlit** em `app/admin_app.py` mantido como apoio/contingência.
- **Identidade SaaS e RBAC** com usuários, memberships e papéis
  owner/admin/operator/viewer; tokens ficam como break-glass.
- **Sessão web opaca e revogável**, cookie HTTP-only + CSRF.
- **Migrações versionadas** internas (37 migrações, ver [[40-Data/03-Migrações]]).
- **Worker confiável** com lease, claims, retries e dead-letter.
- **Billing Stripe**: Checkout, Portal, webhook reconciliado, 3 planos e
  enforcement transacional de limites.
- **Observabilidade**: logs JSON estruturados, `X-Request-ID`, `/admin/observability/summary`, `/admin/observability/metrics` (Prometheus text).
- **Redis** para rate limiting atômico e fan-out realtime entre réplicas.
- **Idempotência** de webhook por `provider_message_id` (`inbound_message_receipts`).

## O que ainda não existe / está mock

- WhatsApp em produção real ainda exige hardening operacional (retries, métricas dedicadas, observabilidade).
- Google Calendar / CalDAV preparados mas não plugados em produção (provider padrão ainda é `mock`).
- MFA/SSO/OIDC opcional ainda não foi implementado; a identidade própria já é funcional.
- Tracing distribuído / exportação para Stack externa de observabilidade.
- Job de fine-tuning concluído com sucesso em produção real e carregado em `OPENAI_FINETUNED_MODEL`.

Detalhe completo em [[80-Improvements/01-Limites Conhecidos]].

## Stack

| Camada | Escolha |
|--------|---------|
| Linguagem | Python 3.13+ |
| API | FastAPI + Uvicorn |
| Validação | Pydantic |
| Persistência runtime | Postgres (psycopg + pool) — SQLite fallback |
| LLM | OpenAI Python SDK (Responses API) |
| Calendar | mock (default), Google Calendar API, CalDAV (iCloud) |
| WhatsApp | Cloud API oficial (Graph) |
| Painel legado | Streamlit |
| Frontend dedicado | HTML+CSS+JS vanilla servido pela própria FastAPI em `/platform` |
| Estado compartilhado | Redis |
| Pagamento/email | Stripe + Resend |
| Container | Docker, Compose com `postgres`, `redis`, `migrate`, `api`, `worker`, `admin` |
| Testes | pytest |

## Estrutura macro do código

```
app/
├── api/          # rotas públicas, admin, plataforma + segurança
├── core/         # orchestrator, config, company_context, logging, exceptions
├── data/         # seeds de company context (store de runtime é o banco)
├── llm/          # providers (openai, mock) + prompts
├── platform/     # SPA estática (HTML + CSS + JS)
├── services/     # ~25 serviços de negócio
├── storage/      # db.py (DDL+migrations) e repository.py (acessos)
├── tools/        # CLIs operacionais (preflight, backup, worker, migração)
├── ui/           # Streamlit (admin_app.py + pages)
└── main.py       # bootstrap FastAPI
```

Detalhe arquivo-a-arquivo em [[90-Reference/03-Mapa de Arquivos]].

## Próximas leituras recomendadas

- [[10-Architecture/01-Arquitetura em Camadas]]
- [[10-Architecture/03-Fluxo Mensagem Recebida]]
- [[40-Data/02-Schema do Banco]]
- [[50-Security/01-Modelo de Ameaças]]
- [[80-Improvements/05-Backlog de Produto]]
