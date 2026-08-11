---
title: Issues Identificados
type: improvement
tags: [melhorias, issues, dívida-técnica]
updated: 2026-07-26
---

# Issues Identificados (auditoria 2026-04-28, revisada em 2026-07-26)

Cada item: **observação concreta no código → impacto → recomendação**.

## I-001 — Comparação de token admin não é timing-safe — ✅ **RESOLVIDO (Release 1A)**

- **Onde**: `app/services/admin_auth_service.py:resolve_admin_role_from_token`.
- **Correção**: agora usa `secrets_match` (`hmac.compare_digest` sobre bytes UTF-8)
  e percorre todos os tokens configurados antes de retornar, para o tempo de
  resposta não revelar qual papel casou.

## I-002 — Repositório monolítico

- **Onde**: `app/storage/repository.py` (~5 800 linhas).
- **Impacto**: alta fricção em mudanças; risco de regressão lateral; testes lentos para validar todas as áreas tocadas.
- **Recomendação**: refatorar por agregado: `TenantRepository`, `ConversationRepository`, `CalendarRepository`, `TrainingRepository`, `BillingRepository`, `AuditRepository`. Pode ser graduado.

## I-003 — Admin routes monolítico

- **Onde**: `app/api/admin_routes.py` (~2 400 linhas; billing já foi extraído).
- **Impacto**: idem ao I-002 mas no nível de API.
- **Recomendação**: dividir em `app/api/admin/` com rotas por domínio (`calendar.py`, `training.py`, `billing.py`, etc.) sob o mesmo router.

## I-004 — Worker sem locking distribuído — ✅ **RESOLVIDO (Release 1B)**

- **Correção**: lembretes, ofertas e outbound usam claim/lease atômico no banco,
  com recuperação de lease expirado. Réplicas concorrentes não processam o
  mesmo item enquanto o claim estiver válido.
- **Prova**: `tests/test_reminder_reliability.py`,
  `tests/test_outbound_reliability.py` e smoke do worker one-shot.

## I-005 — Outbound sem retry com backoff — ✅ **RESOLVIDO (Release 1B)**

- **Correção**: falhas transitórias voltam para a fila com backoff e
  `next_retry_at`; o item só entra em dead-letter depois de esgotar tentativas.
- **Prova**: `tests/test_outbound_reliability.py`.

## I-006 — Sanitização de e-mail incompleta

- **Onde**: `app/core/training_config.py:REDACTION_PATTERNS = [("@", "[EMAIL_REDACTED]")]`.
- **Impacto**: substitui apenas `@`, não captura formato completo. Marca presença sem anonimizar.
- **Recomendação**: regex `[\w\.-]+@[\w\.-]+\.\w+` → `[EMAIL]`; idem para CPF/CNPJ/CEP/telefone.

## I-007 — Rate limit in-memory — ✅ **RESOLVIDO (Release 1B)**

- **Correção**: Redis executa a decisão de rate limit atomicamente e compartilha
  o contador entre processos. O fallback em memória permanece apenas para
  desenvolvimento explícito.
- **Prova**: `tests/test_redis_integration.py` reinicializa o estado local entre
  duas decisões e confirma que o limite compartilhado continua aplicado.

## I-008 — Verify token do webhook não usa compare_digest — ✅ **RESOLVIDO (Release 1A)**

- **Onde**: `app/api/routes.py:verify_whatsapp_webhook`.
- **Correção**: passou a usar `secrets_match`.

## I-009 — Arquivos legados em `app/tools/`

- **Onde**: `app/tools/__init__` (sem extensão), `app/tools/registry.py` (vazio), `app/llm/prompts/system_output.py` (vazio), `app/storage/memory_store.py` (mínimo).
- **Impacto**: ruído no repo; confusão para novos devs.
- **Recomendação**: remover ou documentar propósito.

## I-010 — `app/data/company_contexts/string.json`

- **Onde**: arquivo placeholder com nome `string.json`.
- **Impacto**: provavelmente lixo de teste ou criado sem intenção (slug "string").
- **Recomendação**: limpar ou renomear se ainda for útil.

## I-011 — Dispatch outbound sem lock idempotente — ✅ **RESOLVIDO (Release 1B)**

- **Correção**: transição condicional para `dispatching`, identificador de claim,
  lease e checagem de ownership antes de enviar/finalizar.
- **Prova**: `tests/test_outbound_reliability.py` cobre disputa concorrente,
  recuperação de claim abandonado, retry e dead-letter.

## I-012 — Pool psycopg sem retry-on-disconnect explícito

- **Onde**: `app/storage/db.py:_get_postgres_pool`.
- **Impacto**: falhas de conexão transientes podem propagar como 500.
- **Recomendação**: configurar `kwargs.connect_timeout` e tratar `psycopg.OperationalError` com retry curto.

## I-013 — Logs de payload bruto em alguns pontos

- **Onde**: A revisar em handlers admin que persistem `metadata` em audit; alguns payloads carregam dados sensíveis.
- **Impacto**: PII em audit_events.
- **Recomendação**: helper de scrubbing para `metadata` antes de persistir.

## I-014 — `app/core/state.py` redundante

- **Onde**: `app/core/state.py` é uma camada fina sobre `RuntimeRepository`.
- **Impacto**: duplicação semântica.
- **Recomendação**: avaliar remoção ou expandir para algo útil (cache, estado in-memory).

## I-015 — Sem testes específicos para provider/rate limit — ✅ **RESOLVIDO (Release 1B)**

- **Correção**: `tests/test_outbound_dispatch_service.py` e
  `tests/test_whatsapp_tenant_binding.py` cobrem provider e erros HTTP;
  `tests/test_routes.py` cobre limites locais; `tests/test_redis_integration.py`
  valida rate limit compartilhado contra Redis real.

## I-016 — Repositório vazava conexão Postgres quando a query falhava — ✅ **RESOLVIDO (Release 1A.1)**

- **Onde estava**: a maioria dos métodos de `app/storage/repository.py` fazia
  `connection = get_connection(); ... ; connection.close()` sem `try/finally`.
- **Impacto**: no backend Postgres, qualquer erro no meio do método deixava a
  conexão fora do pool em `idle in transaction (aborted)`. Sob erros repetidos o
  pool esgotava e **todas** as requisições bloqueavam até o timeout.
- **Correção**: ver [[20-Modules/07-Storage e Repository]].
  1. `PostgresConnectionWrapper` e `SqliteConnectionWrapper` têm `close()`
     idempotente que faz rollback antes de devolver ao pool, e recusam reuso
     depois de liberadas.
  2. `db_session()` / `db_transaction()` são os context managers para código
     novo e ferramentas.
  3. `connection_scope()` embrulha **todo** método público de
     `RuntimeRepository`, tornando a liberação incondicional mesmo nos métodos
     que só fechavam no caminho de sucesso.
- **Prova**: `tests/test_postgres_compatibility.py` injeta falhas em
  `execute`/`cursor`, roda mais falhas do que `DATABASE_POOL_SIZE` e verifica que
  a próxima requisição é atendida; roda no SQLite e no Postgres.

## I-017 — Filtro `(? IS NULL OR col = ?)` quebrava no Postgres com parâmetro NULL — ✅ **RESOLVIDO (Release 1A.1)**

- **Onde estava**: 63 ocorrências em `app/storage/repository.py` (listagens,
  updates e subconsultas).
- **Impacto**: no Postgres, chamar sem `company_id` levantava
  `psycopg.errors.IndeterminateDatatype: could not determine data type of parameter $1`,
  virando 500. Em SQLite funcionava, então o defeito só aparecia em produção.
- **Correção**: `_optional_equals(column, value)` monta a condição
  (`col = ?` com parâmetro ligado) ou a condição neutra `1 = 1` quando o filtro
  não se aplica — nenhum placeholder sem tipo chega ao banco. Nomes de coluna são
  literais do módulo, nunca entrada do chamador.
- **Prova**: `tests/test_postgres_compatibility.py` cobre 14 categorias de filtro
  com `None`, valor válido, valor sem correspondência e filtros combinados, além
  de um teste que falha se `? IS NULL OR` reaparecer no repositório.

## I-018 — Integridade referencial de tenant não é imposta pelo banco — ✅ **RESOLVIDO (Release 1B)**

- **Correção**: migrações 036/037 fazem auditoria/backfill e adicionam FKs ou
  triggers equivalentes nas relações de tenant, incluindo validações
  cross-tenant para vínculos compostos.
- **Prova**: `tests/test_tenant_database_integrity.py` roda em SQLite e
  PostgreSQL e rejeita empresas inexistentes e referências cruzadas.

## I-019 — Modelo duplo de contato (`contacts` vs `contact_company_profiles`)

- **Onde**: `contacts` tem `contact_id` como PK global mais uma coluna
  `company_id` legada; `contact_company_profiles` é a projeção correta por tenant
  (PK composta `contact_id, company_id`).
- **Impacto**: duas fontes de verdade para o mesmo conceito. O `company_id` em
  `contacts` só faz sentido para instalações de tenant único e pode divergir do
  perfil por tenant.
- **Recomendação**: tratar `contacts` como identidade global do contato
  (telefone) e mover todo atributo por tenant para `contact_company_profiles`,
  removendo `contacts.company_id` depois de confirmar que nenhum leitor depende
  dele. A migração `022` já criou o caminho; falta concluir a convergência.

## I-020 — Portabilidade perdia colunas e expunha configuração sensível — ✅ **RESOLVIDO (Release 1A.2.1)**

- **Onde estava**: `migrate_sqlite_to_postgres.py` mapeava a tabela
  `appointment_bookings`, mas omitia seis colunas; o guard comparava somente
  nomes de tabelas. `export_runtime_backup.py` exportava
  `calendar_integrations.config_json` sem sanitização e aceitava colunas vindas
  do JSON de restore.
- **Impacto**: migração concluía com dados operacionais transformados em `NULL`;
  backups podiam carregar `client_secret`/`refresh_token`; restore incompleto ou
  manipulado podia apagar/alterar dados.
- **Correção**:
  1. Introspecção real de tabelas e colunas nos dois backends.
  2. Sanitização recursiva compartilhada, desativando integrações que precisam
     de nova credencial.
  3. Backup v3 com manifesto, contagens, checksum, snapshot consistente e arquivo
     `0600` publicado atomicamente.
  4. Restore fail-closed, completo/legado/parcial explícito e uma única
     transação incluindo sequences.
- **Prova**: `tests/test_backup_restore.py` e
  `tests/test_sqlite_to_postgres_migration.py`, incluindo PostgreSQL real,
  rollback, injeção em identificador e os seis campos antes perdidos.

## I-021 — Docker podia parecer saudável antes de um worker falhar — ✅ **RESOLVIDO (Release 1A.2.1)**

- **Onde estava**: `init_db()` no SQLite iniciava transação deferred; dois
  workers liam o mesmo ledger e disputavam o `INSERT` em `schema_migrations`.
  O healthcheck do worker abria `psycopg_pool` e retornava sem fechá-lo. O admin
  herdava o healthcheck da imagem, apontado para a porta 8000 da API.
- **Impacto**: um `200` transitório mascarava a queda do processo pai; worker
  permanecia em `starting` e Streamlit ficava `unhealthy`.
- **Correção**: `BEGIN IMMEDIATE` antes do ledger SQLite, pool fechado em
  `finally`, healthcheck Streamlit dedicado e CI exigindo dois workers estáveis.
- **Prova**: `tests/test_db_migration_concurrency.py`,
  `tests/test_reminder_worker.py` e smoke Compose real com os quatro serviços
  saudáveis.

## I-022 — Build não é reproduzível por dependências não fixadas — ✅ **RESOLVIDO (Release 1B)**

- **Correção**: inputs `.in` separados para API, admin e desenvolvimento; locks
  transitivos com versões e hashes; imagens Docker instalam apenas o conjunto do
  serviço; Vercel usa o runtime da API.
- **Prova**: instalação com `--require-hashes`, build das duas imagens,
  `pip-audit` e jobs dedicados na CI.

## Próximas leituras

- [[80-Improvements/03-Melhorias de Segurança]]
- [[80-Improvements/04-Melhorias de Escalabilidade]]
- [[80-Improvements/05-Backlog de Produto]]
