---
title: Deploy Docker Compose
type: operations
tags: [operação, deploy, docker, compose]
updated: 2026-04-28
---

# Deploy via Docker Compose

`compose.yaml` na raiz orquestra 4 serviços.

## Serviços

```mermaid
flowchart TB
  Postgres[(postgres:17-alpine)]
  API[api: uvicorn]
  Worker[worker: reminder_worker]
  Admin[admin: streamlit]
  Postgres --> API
  Postgres --> Worker
  API --> Admin
```

| Serviço | Container | Porta host | Healthcheck |
|---------|-----------|-----------|-------------|
| `postgres` | `project_wapp_postgres` | `5432` | `pg_isready` |
| `api` | `project_wapp_api` | `8000` | `urllib /health/ready` |
| `worker` | `project_wapp_worker` | — | `python -m app.tools.reminder_worker --validate-only` |
| `admin` | `project_wapp_admin` | `8501` | — (Streamlit) |

`api` e `worker` ambos `depends_on: postgres { condition: service_healthy }`. `admin` depende de `api`.

## Volumes

- `postgres_data` — dados do banco.
- `backups_data` — `/app/backups` (export_runtime_backup).

## Subir

```bash
docker compose --env-file .env.production up --build -d
```

Verificar:

```bash
docker compose --env-file .env.production ps
docker compose --env-file .env.production logs -f api
```

## Subir só Postgres

Quando você quer rodar API/worker no host:

```bash
docker compose --env-file .env.production up -d postgres
```

## Variáveis "compose-only"

Quando o app roda **dentro** do compose, o host do Postgres é `postgres` (nome do serviço), não `127.0.0.1`. Por isso existem variáveis distintas:

| Variável | Uso |
|----------|-----|
| `DATABASE_URL` | Conexão default no host (fora do compose). |
| `POSTGRES_DATABASE_URL` | Probe explícito de Postgres. |
| `COMPOSE_DATABASE_URL` | Conexão dentro do compose (`@postgres:5432`). |
| `COMPOSE_POSTGRES_DATABASE_URL` | Probe dentro do compose. |

`compose.yaml` injeta `DATABASE_URL=${COMPOSE_DATABASE_URL:-...}` dentro dos containers.

## Pré-flight dentro do container

```bash
docker compose --env-file .env.production exec api \
  python -m app.tools.preflight_check --require-scale-ready
```

## Rebuild

```bash
docker compose --env-file .env.production up --build -d api worker admin
```

## Logs estruturados

`LOG_JSON=true` por default. Coletar via stdout dos containers (`docker logs` ou plataforma de logs).

## Healthchecks

| Serviço | Comando | Quando passa |
|---------|---------|--------------|
| postgres | `pg_isready -U $POSTGRES_USER` | Banco aceita conexões. |
| api | `urllib.urlopen('/health/ready')` | Banco OK + sem blockers. |
| worker | `python -m app.tools.reminder_worker --validate-only` | Conecta no banco e configura logger. |

Falhas → restart automático (`restart: unless-stopped`).

## Próximas leituras

- [[60-Operations/02-Configuração de Ambiente]]
- [[60-Operations/04-Healthchecks e Readiness]]
- [[60-Operations/06-Runbook Produção]]
