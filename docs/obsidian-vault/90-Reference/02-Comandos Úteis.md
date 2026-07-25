---
title: Comandos Úteis
type: reference
tags: [referência, comandos, cli]
updated: 2026-04-28
---

# Comandos Úteis

## Setup local

```bash
# venv
python -m venv .venv
source .venv/bin/activate

# deps
pip install -r requirements.txt

# .env (dev local)
cp .env.example .env

# rodar API direto
uvicorn app.main:app --reload

# rodar painel Streamlit
streamlit run app/admin_app.py
```

## Docker compose

```bash
# subir tudo
docker compose --env-file .env.production up --build -d

# subir só Postgres
docker compose --env-file .env.production up -d postgres

# logs
docker compose --env-file .env.production logs -f api worker

# ps
docker compose --env-file .env.production ps

# parar
docker compose --env-file .env.production down

# parar e remover volumes (cuidado!)
docker compose --env-file .env.production down -v

# rebuild
docker compose --env-file .env.production up --build -d api worker admin
```

## Pre-flight

```bash
# básico
python -m app.tools.preflight_check

# exigir prontidão de escala
python -m app.tools.preflight_check --require-scale-ready

# dentro do container
docker compose --env-file .env.production exec api \
  python -m app.tools.preflight_check --require-scale-ready
```

## Backup

```bash
python -m app.tools.export_runtime_backup
python -m app.tools.export_runtime_backup --output backups/<nome>.json
```

## Limpeza operacional

```bash
# dry-run
python -m app.tools.cleanup_operational_data --stale-after-days 30

# executar com backup automático
python -m app.tools.cleanup_operational_data --execute --stale-after-days 30
```

## Migração SQLite → Postgres

```bash
set -a; source .env.production; set +a
python -m app.tools.migrate_sqlite_to_postgres \
  --sqlite-path project_wapp.db \
  --postgres-url "$POSTGRES_DATABASE_URL"
```

## Worker

```bash
# rodar worker
python -m app.tools.reminder_worker

# validate-only (healthcheck)
python -m app.tools.reminder_worker --validate-only

# dispatch manual via API
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/admin/reminders/dispatch-due?audience=customer"
```

## Tests

```bash
pytest                                # tudo
pytest -vv                            # verbose
pytest tests/test_orchestrator.py     # arquivo
pytest -k tenant                      # match por nome
pytest -x                             # parar no primeiro fail
pytest --tb=short                     # traceback curto
```

## API exploration

```bash
# health
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/ready

# enviar mensagem
curl -X POST http://127.0.0.1:8000/messages/incoming \
  -H "Content-Type: application/json" \
  -d '{"contact_id":"5511999999999","contact_name":"Teste","message":"oi"}'

# admin: listar empresas
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/admin/companies

# admin: readiness
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/admin/system/readiness | jq

# admin: pending questions
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/admin/pending-questions?company_slug=empresa-teste"
```

## Login na plataforma via curl

```bash
curl -X POST http://127.0.0.1:8000/platform/auth/session \
  -H "Content-Type: application/json" \
  -d '{"admin_token":"'$TOKEN'","admin_actor":"manual"}' \
  -c /tmp/wapp_cookie.txt

curl -b /tmp/wapp_cookie.txt http://127.0.0.1:8000/admin/system/readiness
```

## Banco

```bash
# entrar no Postgres do compose
docker compose --env-file .env.production exec postgres \
  psql -U project_wapp -d project_wapp

# ver migrações aplicadas
SELECT version, name, applied_at FROM schema_migrations ORDER BY version;

# auditoria recente
SELECT created_at, actor_id, action, status FROM audit_events
  ORDER BY created_at DESC LIMIT 50;
```

## Próximas leituras

- [[60-Operations/06-Runbook Produção]]
- [[60-Operations/01-Deploy Docker Compose]]
