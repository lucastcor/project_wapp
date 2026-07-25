---
title: Roadmap e Próximos Passos
type: roadmap
tags: [roadmap, planejamento]
updated: 2026-04-28
---

# Roadmap e Próximos Passos

> Reflete o estado declarado em `README.md`, `docs/architecture.md` e o que efetivamente está pronto/falta no código. Use como direção, não como compromisso.

## Curto prazo (semanas)

1. **Concluir um job de fine-tuning real** e plugar em `OPENAI_FINETUNED_MODEL`.
   - Exige `ENABLE_FINETUNING_PIPELINE=true` e dataset com ≥10 exemplos elegíveis.
   - Validar `GET /admin/finetuning-dataset/bundle` antes.
2. **Endurecer WhatsApp em produção**: retries com backoff, logs por `provider_message_id`, métricas de delivery e fila de retry.
3. **Plugar Google Calendar real** em uma empresa piloto: credenciais OAuth refresh token + `GOOGLE_CALENDAR_ID`. Detalhe em [[60-Operations/07-Provider WhatsApp Real]] e [[20-Modules/05-Services]].
4. **Sanitização avançada de dataset** antes de usar `training_import_service.py` em base real (sub-etapa de PII redaction). Tracking em [[80-Improvements/03-Melhorias de Segurança]].
5. **Política de rotação de tokens admin** (procedimento documentado + audit_events).

## Médio prazo (meses)

1. **Identidade humana centralizada** — substituir tokens estáticos por OIDC/SAML. Hoje é o gargalo de governança RBAC.
2. **Tracing distribuído** — adicionar OpenTelemetry e exportar para um collector. Hoje só temos `X-Request-ID` nos logs.
3. **Rate limiting compartilhado** — Redis ou similar para suportar múltiplas réplicas (hoje é in-memory por processo).
4. **Provider Instagram** — replicar o padrão de `whatsapp_provider.py` para Instagram Direct.
5. **Alembic** ou ferramenta dedicada para o ciclo de migração (hoje é interno em `db.py:MIGRATIONS`).
6. **Tracing de LLM**: latência, token usage e custo por `request_id`/`company_id`.
7. **Job runner robusto** para `reminder_worker` (RQ/Celery/Arq) — hoje é um loop simples.

## Longo prazo (trimestres)

1. **Camada de dados separada por tenant** (schema por empresa ou DB por empresa) para volumes maiores.
2. **Federar conhecimento** — ETL dedicado para conversas brutas multi-turno e anexos.
3. **Roteamento humano avançado** com filas, SLAs e atribuição.
4. **Reescrita do `repository.py`** — hoje é monolito de ~4400 linhas.
5. **Tipagem mais forte** com Pydantic v2 dataclasses e modelos compartilhados entre serviço e API.
6. **Frontend dedicado para clientes finais** (visitor self-service de bookings).

## Origem do roadmap

Este roadmap consolida três fontes:

- "Próximos passos naturais" do `README.md` raiz.
- "Limites atuais conhecidos" e "Próximos passos recomendados" do `docs/architecture.md`.
- Auditoria de código feita ao construir este vault em 2026-04-28.

## Próximas leituras

- [[80-Improvements/01-Limites Conhecidos]]
- [[80-Improvements/05-Backlog de Produto]]
- [[60-Operations/06-Runbook Produção]]
