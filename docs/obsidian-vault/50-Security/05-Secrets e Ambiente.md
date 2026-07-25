---
title: Secrets e Ambiente
type: security
tags: [segurança, secrets, env]
updated: 2026-04-28
---

# Secrets e Ambiente

## Política

- **Nunca** commitar `.env` real. `.gitignore` cobre.
- `.env.example` mostra apenas placeholders (`change-me-...`).
- `.env.production` é **local, não versionado** — deve receber as chaves reais.
- Para Docker: `docker compose --env-file .env.production up`.

## Detector de placeholders

`app/core/config.py:is_missing_or_placeholder(value)`:

```python
_PLACEHOLDER_PREFIXES = ("change-me", "changeme", "example-", "example_", "example")
_PLACEHOLDER_VALUES = {"project-wapp-platform-dev-secret"}

def is_missing_or_placeholder(value):
    normalized = str(value or "").strip().lower()
    if not normalized: return True
    if normalized in _PLACEHOLDER_VALUES: return True
    return any(normalized.startswith(p) for p in _PLACEHOLDER_PREFIXES)
```

Usado no readiness para flaggar:
- `PLATFORM_SESSION_SECRET`
- `ADMIN_*_TOKEN`
- `OPENAI_API_KEY`
- `WHATSAPP_APP_SECRET`
- `WHATSAPP_API_TOKEN`
- URLs Postgres (parcialmente)

## Categorias de secret

### Internas (você controla)
- `POSTGRES_PASSWORD`
- `ADMIN_API_TOKEN`
- `ADMIN_VIEWER_TOKEN`
- `ADMIN_OPERATOR_TOKEN`
- `ADMIN_SUPERADMIN_TOKEN`
- `PLATFORM_SESSION_SECRET`

### Externas (provider real)
- `OPENAI_API_KEY`
- `GOOGLE_CALENDAR_CLIENT_ID/_SECRET/_REFRESH_TOKEN`
- `WHATSAPP_API_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_WEBHOOK_VERIFY_TOKEN`
- `WHATSAPP_APP_SECRET`
- `CALDAV_USERNAME` / `CALDAV_PASSWORD`

## Como o app valida no boot

`app/main.py:_validate_production_config`:
- Se `ENVIRONMENT=production`:
  - Loga warning se `PLATFORM_SESSION_SECRET` placeholder.
  - Loga warning se `ENABLE_ADMIN_AUTH=true` mas sem tokens.
  - Loga warning se classification = openai mas sem key.
- Sempre loga warning se `OUTBOUND_PROVIDER=mock` ou `CALENDAR_PROVIDER=mock`.

`STRICT_RUNTIME_VALIDATION=true` faz `runtime_guard_service.validate_runtime_environment` levantar `RuntimeError` na subida se há blockers.

## Onde aparecem em logs

- URLs Postgres: passam por `_redact_database_url` antes de qualquer log/saída pública.
- `contact_id`: passa por `mask_identifier` antes de logar.
- Mensagem completa: **não** é logada (só metadata).
- Tokens: nunca logados.

## Recomendações operacionais

1. Gerar secrets com `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.
2. Manter `.env.production` apenas no servidor de deploy ou em cofre (Vault/Secret Manager).
3. Trocar tokens admin a cada rotação documentada.
4. Fazer dry-run de readiness antes do deploy: `python -m app.tools.preflight_check`.
5. Em CI, **nunca** colocar token de produção; use ambiente separado.

## Hardening pendente

- Carregar secrets de Secret Manager (AWS, GCP, Vault) em vez de env file.
- Rotação automatizada.
- Detecção de leak no git (`gitleaks`, `trufflehog`).

Tracking em [[80-Improvements/03-Melhorias de Segurança]].

## Próximas leituras

- [[60-Operations/02-Configuração de Ambiente]]
- [[50-Security/01-Modelo de Ameaças]]
