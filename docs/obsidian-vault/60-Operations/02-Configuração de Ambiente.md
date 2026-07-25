---
title: Configuração de Ambiente
type: operations
tags: [operação, env, configuração]
updated: 2026-04-28
---

# Configuração de Ambiente

## Arquivos

| Arquivo | Versionado | Uso |
|---------|-----------|-----|
| `.env.example` | ✅ | Referência. Apenas placeholders. |
| `.env` | ❌ | Dev local quando você roda `uvicorn` no host. |
| `.env.production` | ❌ | Homologação/produção via Docker Compose. |

Carregamento: `python-dotenv` em `app/core/config.py:5` (`load_dotenv()`). Para Docker, passar via `--env-file`.

## Cenários

### Dev local simples

```bash
cp .env.example .env
# editar .env (OPENAI_API_KEY, DATABASE_URL=sqlite:///project_wapp.db)
uvicorn app.main:app --reload
```

### Dev local com Postgres

```bash
docker compose --env-file .env.production up -d postgres
uvicorn app.main:app --reload
```

### Produção / homologação completa

```bash
docker compose --env-file .env.production up --build -d
```

## Variáveis críticas

Lista completa em [[90-Reference/01-Variáveis de Ambiente]]. Aqui as **críticas**:

| Variável | Default | Crítico? |
|----------|---------|---------|
| `ENVIRONMENT` | `development` | sim — endurece checks. |
| `STRICT_RUNTIME_VALIDATION` | `false` | sim em prod (`true`). |
| `ENABLE_ADMIN_AUTH` | `false` | sim em prod (`true`). |
| `ADMIN_*_TOKEN` | — | obrigatório se admin auth ligado. |
| `PLATFORM_SESSION_SECRET` | fallback fraco | sim em prod (gerar forte). |
| `OPENAI_API_KEY` | — | sim se classification = openai. |
| `OUTBOUND_PROVIDER` | `mock` | trocar para `whatsapp` em prod. |
| `WHATSAPP_*` | — | sim para canal real. |
| `DATABASE_URL` | sqlite local | trocar para Postgres em prod. |
| `CALENDAR_PROVIDER` | `mock` | trocar quando Google/CalDAV plugado. |

## Coerência host vs compose

Quando rodando no host (sem Docker):
```env
DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/db
POSTGRES_DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/db
```

Quando rodando dentro do compose:
```env
COMPOSE_DATABASE_URL=postgresql://user:pass@postgres:5432/db
COMPOSE_POSTGRES_DATABASE_URL=postgresql://user:pass@postgres:5432/db
```

`compose.yaml` injeta `DATABASE_URL=$COMPOSE_DATABASE_URL` dentro do container. **Não tente** usar `127.0.0.1` lá dentro.

## Validação

```bash
python -m app.tools.preflight_check
python -m app.tools.preflight_check --require-scale-ready
```

Falha (`blocked`) impede subida em modo strict.

## Próximas leituras

- [[60-Operations/01-Deploy Docker Compose]]
- [[60-Operations/04-Healthchecks e Readiness]]
- [[90-Reference/01-Variáveis de Ambiente]]
