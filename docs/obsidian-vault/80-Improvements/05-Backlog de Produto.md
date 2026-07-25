---
title: Backlog de Produto
type: improvement
tags: [melhorias, backlog, produto]
updated: 2026-04-28
---

# Backlog de Produto

Funcionalidades que aumentam **valor entregue ao cliente final**, organizadas por tema.

## Conversação

### B-001 — Job de fine-tuning real concluído
- Ativar `ENABLE_FINETUNING_PIPELINE=true`.
- Curar ≥10 exemplos elegíveis.
- Treinar e plugar em `OPENAI_FINETUNED_MODEL`.

### B-002 — Suporte a mídia (imagem, áudio)
- Webhook recebe `type=image|audio|document`.
- Repository persiste URL + tipo.
- LLM responde com referência cumpridora.

### B-003 — Templates WhatsApp para outbound proativo
- Permitir disparo fora da janela 24h via templates aprovados pelo Meta.
- Painel para criar/aprovar templates.

### B-004 — Promoção automática `knowledge_entries` → `company_context`
- Quando `applies_globally=true` ganha confidence alta, promove para `business_rules`.
- Criar `knowledge_action=promote_to_company_context` end-to-end.

### B-005 — Provider Instagram Direct
- Replicar padrão `whatsapp_provider.py`.
- Adicionar `OUTBOUND_PROVIDER=instagram` e webhook análogo.

## Agenda

### B-006 — Plugar Google Calendar real em piloto
- Setup OAuth + refresh token.
- Validar criação/cancel/reagenda em calendário real.

### B-007 — Plugar CalDAV (Apple iCloud)
- Igual a B-006, com `caldav` lib.

### B-008 — UI cliente self-service
- Cliente acessa link e escolhe horário sem operador.

### B-009 — Multi-staff
- Calendar profile por funcionário.
- Roteamento por especialidade.

## Operação

### B-010 — Identidade humana centralizada
- OIDC. Ver [[80-Improvements/03-Melhorias de Segurança]] S-001.

### B-011 — Hardening WhatsApp produtivo
- Retries, métricas, dashboard de delivery rate.

### B-012 — Política de retenção e arquivamento
- Mensagens antigas → cold storage.
- Auditoria → S3/Glacier.

### B-013 — Rotação automatizada de tokens
- Secret Manager + reload.

### B-014 — Painel de incidentes na própria plataforma
- "Algo está com falha?" em tempo real.

### B-015 — Importação avançada de histórico
- Multi-turno, anexos, exports legados sujos.
- Pipeline ETL dedicado.

## UX da plataforma

### B-016 — PWA / mobile dedicado
- Operação em telefone.

### B-017 — Realtime WebSocket
- Pending questions e novas mensagens em tempo real para o operador.

### B-018 — Inbox compartilhada
- Múltiplos operadores resolvendo handoffs com atribuição.

## Billing

### B-019 — Enforcement automático de limites
- Hoje `check_limit` é lookup; falta gate no caminho real (mensagem, booking, finetuning).

### B-020 — Integração com gateway de pagamento
- Stripe ou similar para cobrar planos automaticamente.

### B-021 — Dashboard financeiro
- Receita por tenant, churn, MRR.

## Treinamento e qualidade

### B-022 — Avaliação automática de qualidade
- Métricas (BLEU/cosine/LLM judge) sobre `message_examples`.

### B-023 — A/B testing de respostas
- Comparar fine-tuned vs base.

### B-024 — Métricas de impacto de bot
- % de auto-reply, tempo médio, satisfação.

## Próximas leituras

- [[00-Overview/04-Roadmap e Próximos Passos]]
- [[80-Improvements/01-Limites Conhecidos]]
- [[80-Improvements/03-Melhorias de Segurança]]
- [[80-Improvements/04-Melhorias de Escalabilidade]]
