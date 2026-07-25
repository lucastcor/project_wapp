---
title: Hardening Operacional
type: security
tags: [segurança, hardening, operação]
updated: 2026-04-28
---

# Hardening Operacional

Checklist de o que está ligado por padrão e o que deveria ser ligado em produção.

## Padrão de produção

`compose.yaml` já liga:

| Variável | Valor | Efeito |
|----------|-------|--------|
| `ENVIRONMENT=production` | sim | Endurece checks de readiness. |
| `STRICT_RUNTIME_VALIDATION=true` | sim | API/worker falham cedo se há blocker. |
| `ENABLE_ADMIN_AUTH=true` | sim | Tokens obrigatórios. |
| `ENABLE_RATE_LIMITING=true` | sim | Rate limit ativo. |
| `LOG_JSON=true` | sim | Logs estruturados. |
| `LOG_LEVEL=INFO` | sim | Sem debug em prod. |
| `REMINDER_WORKER_ENABLED=true` | sim | Worker dedicado sobe. |

Detalhe em [[60-Operations/01-Deploy Docker Compose]].

## Checklist pré-deploy

- [ ] Tokens admin reais (não `change-me-*`).
- [ ] `PLATFORM_SESSION_SECRET` exclusivo e forte.
- [ ] `WHATSAPP_APP_SECRET` igual ao do Meta Developer Portal.
- [ ] `WHATSAPP_WEBHOOK_VERIFY_TOKEN` longo e aleatório.
- [ ] HTTPS obrigatório no proxy reverso (Meta exige).
- [ ] `OPENAI_API_KEY` válida.
- [ ] Postgres com senha forte e rede restrita.
- [ ] `DATABASE_URL`, `POSTGRES_DATABASE_URL` apontando para o Postgres real.
- [ ] `COMPOSE_DATABASE_URL` apontando para `postgres:5432` dentro do Docker.
- [ ] Backups configurados (volume persistente + offload).
- [ ] `python -m app.tools.preflight_check --require-scale-ready` retornou `ok`.
- [ ] `/health/ready` retorna 200.
- [ ] Smoke test: enviar 1 mensagem real e ver dispatch outbound chegando.

## Hardening adicional recomendado

### Rede / proxy reverso

- Nginx ou Caddy à frente do FastAPI.
- HTTPS forçado.
- Redirect HTTP → HTTPS.
- HSTS.
- Content Security Policy (CSP) — **não** definida por default.
- WAF para `/admin/*` (bloqueio de geos não esperadas).

### Container

- Imagem oficial `python:3.13-slim` (mantida).
- Usuário não-root no Dockerfile (já feito: `USER app`).
- Volumes apenas para `backups_data` e `postgres_data`.

### Banco

- `pg_hba.conf` restrito ao container app.
- Backups automatizados (pg_dump diário em S3, por exemplo).
- Statement timeout configurado (default 5 000 ms).
- Pool sizes coerentes com carga.

### Logs

- Rotação de logs no host.
- Encaminhar para SIEM (CloudWatch, Datadog, ELK).
- Alertas para `status=failed` em `audit_events`.

### Workers

- Reiniciar worker via Docker `restart: unless-stopped` (já configurado).
- Healthcheck do worker via `--validate-only`.

## Pontos cegos atuais

- **CSP não definida**.
- **Sem WAF/IPS** documentado.
- **Sem MFA admin**.
- **Rate limit in-memory** — em escala horizontal não compartilhado.
- **Sem alertas** automáticos em `audit_events`.
- **Sem detecção de drift** de schema entre ambientes.
- **Sem chaves rotacionadas automaticamente**.

Tracking em [[80-Improvements/03-Melhorias de Segurança]] e [[80-Improvements/04-Melhorias de Escalabilidade]].

## Próximas leituras

- [[60-Operations/06-Runbook Produção]]
- [[60-Operations/01-Deploy Docker Compose]]
- [[50-Security/01-Modelo de Ameaças]]
