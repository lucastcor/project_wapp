---
title: Melhorias de Segurança
type: improvement
tags: [melhorias, segurança]
updated: 2026-04-28
---

# Melhorias de Segurança

Alinhar com [[50-Security/01-Modelo de Ameaças]].

## Prioridade alta

### S-001 — Identidade humana centralizada
- **Hoje**: tokens estáticos por papel.
- **Para**: OIDC/SAML (Auth0, Cognito, Keycloak). Cada usuário identificado individualmente.
- **Benefícios**: revogação granular, MFA, audit por usuário real, rotação automática.

### S-002 — Sanitização avançada de PII no dataset
- **Hoje**: markers + redação de `@`.
- **Para**: pipeline dedicado com regex (CPF, CNPJ, CEP, telefone, RG), NER para nomes próprios.
- **Onde**: pré-processamento antes de `is_example_eligible_for_training`.
- Tracking: I-006.

### S-003 — `compare_digest` em comparação de tokens
- **Hoje**: `==` em `resolve_admin_role_from_token` e verify token.
- **Para**: `hmac.compare_digest`.
- **Custo**: trivial.
- Tracking: I-001, I-008.

### S-004 — Lockout temporário pós N falhas de login
- **Hoje**: tentativas ilimitadas (rate limit do IP é o único freio).
- **Para**: bloqueio por `actor_id` ou IP por X minutos após N falhas.
- **Onde**: `admin_security.require_admin_access`.

### S-005 — Hash chain em `audit_events`
- **Hoje**: registros independentes; admin do banco pode editar.
- **Para**: cada evento carrega `previous_event_hash` + `current_event_hash`. Verificável.

## Prioridade média

### S-006 — CSP estrita na plataforma
- **Hoje**: sem CSP definida.
- **Para**: header `Content-Security-Policy` no `/platform`.

### S-007 — Scrubbing de `metadata` em audit_events
- **Hoje**: payloads completos podem ir.
- **Para**: helper `scrub_audit_metadata(payload)` removendo chaves sensíveis.
- Tracking: I-013.

### S-008 — Rotação automatizada de secrets
- **Hoje**: manual.
- **Para**: integração com Secret Manager + reload sem downtime.

### S-009 — Revisão de logs por leak de PII
- **Hoje**: `mask_identifier`, mas há campos livres em logs.
- **Para**: review explícito de cada `log_event` para garantir que `customer_message`, `reply` etc. nunca viram log.

### S-010 — RLS no Postgres
- **Hoje**: tenant scope por queries.
- **Para**: Row Level Security nativo + policy por papel.
- **Custo**: alto (refatorar acessos do repository), mas defesa em profundidade real.

## Prioridade baixa

### S-011 — Webhook signing rotation
- **Hoje**: `WHATSAPP_APP_SECRET` único.
- **Para**: suporte a duas chaves (atual + previous) para rotação sem downtime.

### S-012 — IP allowlist para `/admin/*`
- **Hoje**: depende de proxy externo.
- **Para**: middleware opcional baseado em config.

### S-013 — Detection de leak no git
- **Para**: pre-commit hook com `gitleaks` / `trufflehog`.

### S-014 — Pen-test externo
- **Para**: contratar antes de operar dados reais em escala.

## Próximas leituras

- [[50-Security/01-Modelo de Ameaças]]
- [[80-Improvements/02-Issues Identificados]]
