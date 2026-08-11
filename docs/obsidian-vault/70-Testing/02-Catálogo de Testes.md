---
title: Catálogo de Testes
type: testing
tags: [testes, catálogo]
updated: 2026-07-26
---

# Catálogo de Testes

Mapa arquivo-a-arquivo com o objetivo principal. Gate final do Release 1B/1C:
**631 passed + 14 skipped no SQLite/Redis** e **644 passed + 1 skipped no
PostgreSQL/Redis**.

| Arquivo | Foco |
|---------|------|
| `tests/conftest.py` | Isolamento de ambiente (`DISABLE_DOTENV`, limpeza de segredos), bootstrap do banco de teste, reset de rate limit. |
| `tests/test_admin_rbac_matrix.py` | Matriz endpoint→permissão: viewer não muta nada, operator só o permitido, superadmin autorizado, auditoria de negação e de mutação privilegiada. |
| `tests/test_admin_routes.py` | Endpoints administrativos: agenda, contatos, empresa, treino, RBAC. |
| `tests/test_analytics_service.py` | Dashboard, trend, reminder effectiveness. |
| `tests/test_appointment_queue_service.py` | Fila, ofertas, follow-up, interpretação natural. |
| `tests/test_billing_service.py` | Planos, trial, ativação, limites, usage. |
| `tests/test_business_scenarios.py` | Cenários e2e de cliente real (lead → handoff → resposta → follow-up). |
| `tests/test_calendar_service.py` | Disponibilidade, perfis, bookings, sync. |
| `tests/test_company_context.py` | Validação do payload, pipelines visuais. |
| `tests/test_company_context_concurrency.py` | Criação e atualização concorrentes: exatamente um criador de revisão 1, conflito controlado para os perdedores, ausência de escrita parcial, consistência final de revisão/payload, pool utilizável após rajada. |
| `tests/test_db_migration_concurrency.py` | Dois inicializadores SQLite concorrentes leem o ledger em série e deixam exatamente as 37 migrações únicas. |
| `tests/test_backup_restore.py` | Backup v3: cobertura/schema manifest, snapshot, checksum, sanitização de credenciais/claims, arquivo `0600` atômico e restore transacional. |
| `tests/test_saas_identity.py` | Cadastro, login, memberships, convite, reset, sessão revogável, CSRF, gestão de membros, proteção do último owner e isolamento entre empresas. |
| `tests/test_transactional_email_service.py` | Resend para convite/reset, URLs públicas e falha segura sem exposição de token. |
| `tests/test_billing_enforcement.py` | Enforcement e reservas/refunds de quota em mensagens, booking, campanha e fine-tuning. |
| `tests/test_stripe_billing_service.py` | Checkout/Portal, assinatura, ledger, replay, ordem de eventos, reconciliação e falha de pagamento. |
| `tests/test_outbound_reliability.py` | Claim atômico, retry/backoff, dead-letter e recuperação de claim abandonado. |
| `tests/test_reminder_reliability.py` | Duas réplicas geram um único outbound e recuperam claims stale. |
| `tests/test_tenant_database_integrity.py` | FKs/guards de tenant e rejeição de referências cross-tenant em SQLite/PostgreSQL. |
| `tests/test_redis_integration.py` | Rate limit compartilhado e publicação realtime contra Redis real. |
| `tests/test_sqlite_to_postgres_migration.py` | Migração ponta a ponta contra PostgreSQL real (sem mock): cobertura de tabelas **e colunas**, seis campos de booking antes perdidos, sanitização de provider secrets, defaults legados, rollback do destino, contexts, bindings, perfis, FKs, sequences, isolamento e readiness. |
| `tests/test_postgres_compatibility.py` | Regressão de I-016 e I-017: 14 categorias de filtro opcional (None / valor / sem correspondência / combinados), injeção de falhas em SELECT/INSERT, idempotência de `close()`, escopos aninhados, rajada de falhas acima de `DATABASE_POOL_SIZE`, migração falha liberando conexão e advisory lock. |
| `tests/test_whatsapp_tenant_binding.py` | Evento Meta assinado resolve tenant só por `phone_number_id`; tentativas de redirecionar via query/header são ignoradas; desconhecido/inativo/ambíguo falham fechado; sem fallback para empresa padrão; recibo de idempotência com tenant correto; outbound usa o número do próprio tenant; RBAC e auditoria dos vínculos. |
| `tests/test_company_context_persistence.py` | Migração em banco novo, idempotência, import de seed, precedência do banco, rollback de payload inválido, conflito de revisão, visibilidade API↔worker, persistência entre restarts, isolamento por tenant, backup. |
| `tests/test_ingress_security.py` | Assinatura Meta (ausente/inválida/válida), ingress normalizado off por padrão, segredo interno ausente/inválido/válido, `/messages/incoming` anônimo em produção, autorização de empresa, limites de tamanho, ausência de segredo em log. |
| `tests/test_conversation_workflow_service.py` | Agenda conversacional, cancel, reschedule, operador interno. |
| `tests/test_db_backend.py` | Compatibilidade SQLite ↔ Postgres (com SQLite em testes). |
| `tests/test_finetuning_service.py` | Criação de jobs, persistência, leitura. |
| `tests/test_memory_service.py` | Resumo conversacional. |
| `tests/test_onboarding_service.py` | Presets e setup operacional. |
| `tests/test_operational_rules.py` | Regras de negócio operacionais. |
| `tests/test_orchestrator.py` | Caminho completo do orquestrador. |
| `tests/test_orchestrator_flow.py` | Variações de fluxo. |
| `tests/test_orchestrator_no_auto_reply.py` | Casos onde bot não deve responder. |
| `tests/test_outbound_dispatch_service.py` | Dispatch + erros de provider. |
| `tests/test_pipeline_service.py` | Inferência de pipeline. |
| `tests/test_pipeline_service_validation.py` | Validação adicional do pipeline. |
| `tests/test_reminder_service.py` | Criação e disparo de lembretes. |
| `tests/test_reminder_worker.py` | Caminhos one-shot/healthcheck fecham o pool PostgreSQL antes de sair. |
| `tests/test_platform_events.py` | SSE: rejeição anônima/expirada, escopo vindo da sessão, tenant A não recebe eventos de B, admin global recebe tudo, limpeza na desconexão, higiene do payload. |
| `tests/test_readiness_security_checks.py` | Blockers de ingress inseguro, segredo de sessão ausente, contexto fora do banco; boot fail-closed de API e worker; defaults de desenvolvimento; independência do `.env` local. |
| `tests/test_reply_service.py` | Geração de resposta comercial. |
| `tests/test_routes.py` | Rotas públicas, webhook, plataforma. |
| `tests/test_system_readiness_service.py` | Readiness, perfil, onboarding. |
| `tests/test_tenant_isolation.py` | Vazamento cross-tenant nas queries. |
| `tests/test_tools.py` | CLIs (preflight, backup, cleanup). |
| `tests/test_training_export_service.py` | Filtragem, dedup, split, export. |
| `tests/test_training_import_service.py` | CSV/JSON dry-run + persist. |
| `tests/test_data/conversation_scenarios.py` | Dados reutilizáveis em testes conversacionais. |

## Isolamento de ambiente

`tests/conftest.py` define `DISABLE_DOTENV=true` antes de qualquer import de
`app.*`, então `app/core/config.py` **não** carrega o `.env` do desenvolvedor.
Além disso, variáveis sensíveis (tokens admin, segredo de ingress, credenciais
de WhatsApp/Google/CalDAV) são explicitamente removidas do ambiente; cada teste
que precisa de uma define via `monkeypatch`.

## Rodando contra PostgreSQL

```bash
TEST_DATABASE_URL="postgresql://user:pass@127.0.0.1:5432/wapp_test" \
TEST_REDIS_URL="redis://127.0.0.1:6379/0" \
pytest tests/
```

Sem `TEST_DATABASE_URL` a suíte usa um arquivo SQLite descartável.

A suíte **completa** passa nos dois backends desde o Release 1A.1, que resolveu
I-016 (vazamento de conexão) e I-017 (filtro com placeholder sem tipo). Ver
[[80-Improvements/02-Issues Identificados]].

## CI

O workflow tem três jobs:

| Job | Backend | Observação |
|-----|---------|-----------|
| `test` | SQLite + Redis 7.4 | Lint, mypy em todo `app`, Bandit, `pip-audit`, suíte completa e import do entrypoint Vercel. |
| `test-postgres` | PostgreSQL 16 (service container) | Suíte completa (`DATABASE_POOL_SIZE=5`) + suíte de regressão de portabilidade e vínculos com `DATABASE_POOL_SIZE=3`, incluindo a migração SQLite→PostgreSQL contra o serviço real. |
| `docker` | PostgreSQL 16 | Build explícito de API e admin + smoke em SQLite/PostgreSQL, readiness, migrações e worker one-shot. |

Ambos têm `timeout-minutes`, e o job Postgres espera o healthcheck do serviço
antes de rodar — um problema de pool falha em vez de travar indefinidamente.
As credenciais do container são exclusivas de teste.

## Pendências documentadas

- `tests/conftest_py` (sem extensão) **não existe** no checkout atual; era citação histórica do `codebase_map.md`. Confirmado com `ls tests/`. Não é placeholder pendente.
- Integrações live de Stripe, Resend, WhatsApp e calendário continuam
  dependentes de sandbox/credenciais externas; o repositório cobre contratos,
  assinaturas, idempotência e erros com doubles e serviços locais.

Tracking em [[80-Improvements/02-Issues Identificados]].

## Próximas leituras

- [[70-Testing/01-Estratégia de Testes]]
