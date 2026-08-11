---
title: Melhorias de Segurança
type: improvement
tags: [melhorias, segurança]
updated: 2026-07-26
---

# Melhorias de Segurança

Alinhar com [[50-Security/01-Modelo de Ameaças]].

## Prioridade alta

### S-001 — Identidade humana centralizada
- **Concluído**: identidade individual, password scrypt, memberships,
  revogação, convite/reset e audit por usuário.
- **Próxima evolução**: MFA e OIDC/SAML opcionais.

### S-002 — Sanitização avançada de PII no dataset
- **Hoje**: markers + redação de `@`.
- **Para**: pipeline dedicado com regex (CPF, CNPJ, CEP, telefone, RG), NER para nomes próprios.
- **Onde**: pré-processamento antes de `is_example_eligible_for_training`.
- Tracking: I-006.

### S-003 — `compare_digest` em comparação de tokens
- **Concluído**: tokens, assinaturas e hashes sensíveis usam comparação
  constant-time.

### S-004 — Lockout temporário pós N falhas de login
- **Hoje**: tentativas ilimitadas (rate limit do IP é o único freio).
- **Para**: bloqueio por `actor_id` ou IP por X minutos após N falhas.
- **Onde**: `admin_security.require_admin_access`.

### S-005 — Hash chain em `audit_events`
- **Hoje**: registros independentes; admin do banco pode editar.
- **Para**: cada evento carrega `previous_event_hash` + `current_event_hash`. Verificável.

## Prioridade média

### S-006 — CSP estrita na plataforma
- **Concluído**: CSP e headers de segurança são enviados pela API.

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
