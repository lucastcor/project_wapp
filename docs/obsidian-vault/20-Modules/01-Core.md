---
title: Core (orchestrator, config, company_context)
type: module
tags: [módulo, core]
updated: 2026-04-28
---

# Módulo: Core

Tudo que está em `app/core/` — fundação compartilhada.

## Arquivos

| Arquivo | Linhas | Papel |
|---------|--------|-------|
| `app/core/orchestrator.py` | ~298 | Orquestrador principal. Centro do sistema. |
| `app/core/company_context.py` | ~210 | Leitura/validação/escrita do contrato JSON por empresa. |
| `app/core/config.py` | ~95 | Settings (env vars) + helper `is_missing_or_placeholder`. |
| `app/core/logging.py` | — | Logger JSON estruturado, contexto por request, masking. |
| `app/core/exceptions.py` | — | Exceções base. |
| `app/core/state.py` | — | Camada fina sobre `RuntimeRepository`. Praticamente legada. |
| `app/core/training_config.py` | 26 | Constantes de curadoria (qualidade mínima, markers sensíveis). |

## `orchestrator.py`

Função pública: `process_incoming_message()` e `process_whatsapp_webhook()`. Ambas chamam `_process_contact_message()`.

Fluxo detalhado em [[10-Architecture/03-Fluxo Mensagem Recebida]].

Pontos importantes:
- Cria `RuntimeRepository()` no nível de módulo.
- `mask_identifier()` em todos os logs com `contact_id`.
- Tenta primeiro `handle_conversation_workflow()` — se tratar, encerra.
- Caminho normal: classifica → decide → reply → handoff.

## `company_context.py`

Define o **contrato JSON** validado pelo backend. Chaves obrigatórias:

```
{company_name, brand_tone, enabled_modules, business_identity, contact_channels,
 business_hours, response_policy, commercial_policy, catalog, customer_intake,
 business_rules, llm_guidelines}
```

Chaves opcionais validadas:
- `scheduling_policy.profiles[]` — perfis de agendamento por empresa.
- `internal_operation.operator_contact_ids[]` — contatos autorizados a operar agenda.
- `conversation_pipelines[]` — playbooks visuais com `key`, `label`, `next_keys`, `canvas_position` (chaves únicas; `next_keys` deve referenciar keys existentes).

Persistência:
- Global: `app/data/company_context.json` (fallback).
- Por empresa: `app/data/company_contexts/{slug}.json`.

`save_company_context()` valida antes de gravar; se inválido, levanta `CompanyContextError`.

Detalhe em [[40-Data/05-Company Context]].

## `config.py`

Classe `Settings` com **TODAS** as variáveis de ambiente (via `python-dotenv`). Default seguro para dev local; produção exige override.

Helper crítico: `is_missing_or_placeholder(value)` — flagga valores como `"change-me-..."`, `"example-..."`, `"project-wapp-platform-dev-secret"`.

Categorias:
- App: `APP_NAME`, `APP_VERSION`, `ENVIRONMENT`, `DEBUG`, `LOG_*`, `STRICT_RUNTIME_VALIDATION`.
- Rate limiting: `ENABLE_RATE_LIMITING`, `RATE_LIMIT_*`.
- Banco: `DATABASE_URL`, `POSTGRES_DATABASE_URL`, `DATABASE_POOL_SIZE`, `DATABASE_STATEMENT_TIMEOUT_MS`.
- Backups: `OPERATIONS_BACKUP_DIR`.
- Admin: `ENABLE_ADMIN_AUTH`, `ADMIN_*_TOKEN`, `PLATFORM_SESSION_*`, `AUDIT_RETENTION_DAYS`.
- Worker: `REMINDER_WORKER_*`, `APPOINTMENT_QUEUE_*`.
- LLM: `OPENAI_*`, `CLASSIFICATION_PROVIDER`, `USE_FINETUNED_MODEL_FOR_REPLIES`.
- Outbound: `OUTBOUND_PROVIDER`, `OUTBOUND_DEFAULT_CHANNEL`.
- Calendário: `CALENDAR_*`, `GOOGLE_CALENDAR_*`, `CALDAV_*`.
- WhatsApp: `WHATSAPP_*`.

Lista completa em [[90-Reference/01-Variáveis de Ambiente]].

## `logging.py`

Provê:
- `configure_logging()` chamado em `main.py`.
- `get_logger(__name__)`.
- `log_event(logger, level, event, **kwargs)` — append de keys ao registro.
- `mask_identifier(value)` — mascara contact_id (ex. `5511***9999`).
- `set_request_context()`, `clear_request_context()`, `get_request_context()` — contexto por request thread/async.

`LOG_JSON=true` (default) → formato JSON. Caso contrário, plain text.

## `state.py`

Camada fina; importa repository. **Provavelmente removível** — pequena dívida técnica registrada em [[80-Improvements/02-Issues Identificados]].

## `training_config.py`

Constantes do pipeline de curadoria. Editar aqui altera qualidade do dataset:

```python
MIN_QUALITY_SCORE = 8.0
MAX_EXAMPLES_PER_SCENARIO = 50
VALIDATION_RATIO = 0.2
DEDUP_NORMALIZE_CASE = True
EXCLUDED_SCENARIO_TYPES = {"financial", "support"}
SENSITIVE_MARKERS = ["cpf", "cnpj", "cartão", ..., "boleto"]
REDACTION_PATTERNS = [("@", "[EMAIL_REDACTED]")]
```

## Próximas leituras

- [[20-Modules/05-Services]]
- [[20-Modules/07-Storage e Repository]]
- [[40-Data/05-Company Context]]
