---
title: Estado Atual do Produto
type: status
tags: [estado, produto, maturidade]
updated: 2026-07-26
---

# Estado Atual do Produto

Snapshot da maturidade por dimensão, em **2026-07-26**, baseado em código real
(não em backlog declarado). Inclui os Releases 1A–1C e validação real em
SQLite, PostgreSQL, Redis e Docker.

## Maturidade por área

| Área | Status | Evidência (arquivo) | Observação |
|------|--------|---------------------|------------|
| Recepção / orquestração de mensagem | ✅ Pronto | `app/core/orchestrator.py` | Idempotência por `provider_message_id`. |
| Classificação | ✅ Pronto | `app/services/classification_service.py` | `openai` ou heurística mock. |
| Decisão auto-reply / handoff | ✅ Pronto | `app/services/decision_service.py`, `handoff_service.py` | Cobre desconto, estoque, baixa confiança etc. |
| Resposta comercial | ✅ Pronto | `app/services/reply_services.py`, `app/llm/providers/openai_provider.py` | Suporta finetuned model via flag. |
| Memória / pipeline | ✅ Pronto | `memory_service.py`, `pipeline_service.py` | Resumo + estágio + temperatura. |
| Knowledge base incremental | ✅ Pronto | `knowledge_service.py` | Resoluções viram `knowledge_entries`. |
| Outbound + dispatch | ✅ Pronto | `outbound_dispatch_service.py`, `whatsapp_provider.py` | Status pending → sent/failed. |
| Webhook WhatsApp | ✅ Pronto | `app/api/routes.py` | Handshake + HMAC `X-Hub-Signature-256` obrigatório para todo payload Meta. |
| Idempotência webhook | ✅ Pronto | `inbound_message_receipts` | Migração 018. |
| Compatibilidade PostgreSQL | ✅ Suíte completa passa | `tests/test_postgres_compatibility.py` | I-016 e I-017 resolvidos; CI roda os dois backends. |
| Vínculo canal→empresa (WhatsApp) | ✅ Pronto | `whatsapp_channel_bindings` | Entrada e saída escopadas por tenant. |
| Ingress interno autenticado | ✅ Pronto | `services/ingress_auth_service.py`, `api/ingress_security.py` | Modo normalizado off por padrão; segredo dedicado com comparação constant-time. |
| Multi-tenant | ✅ Aplicação + banco | `company_memberships`, migração 035 | Membership limita leitura/mutação; FKs e guards compostos rejeitam órfãos e referências cross-tenant. |
| Agenda + bookings | ✅ Pronto | `calendar_service.py`, tabelas 005 | Provider mock + perfis por empresa. |
| Conversa natural agenda | ✅ Pronto | `conversation_workflow_service.py` | Cliente + operador interno. |
| Fila de remanejamento | ✅ Pronto | `appointment_queue_service.py` | Ofertas com janela de resposta. |
| Lembretes (worker) | ✅ Claim/retry/dead-letter | `reminder_service.py`, `app/tools/reminder_worker.py` | Duas réplicas não duplicam outbound; claims abandonados são recuperados. |
| Importação histórica | ✅ Pronto | `training_import_service.py` | CSV/JSON com dry-run. |
| Curadoria / export dataset | ✅ Pronto | `training_export_service.py` | Dedup, qualidade mínima 8.0, split 80/20. |
| Fine-tuning jobs | 🟡 Pipeline pronto, falta job real | `finetuning_service.py` | `ENABLE_FINETUNING_PIPELINE` default false. |
| Calendário externo (Google/CalDAV) | 🟡 Preparado | `calendar_service.py` | Default `mock`, falta plugar credenciais reais. |
| Plataforma web `/platform` | ✅ SaaS self-service | `app/platform/` | Login por usuário, cadastro opcional, convite e recuperação; sessão opaca HTTP-only + CSRF. |
| Painel Streamlit | ✅ Apoio | `app/admin_app.py`, `app/ui/pages/` | Mantido como contingência. |
| RBAC | ✅ Matriz explícita | `api/admin_permissions.py` | Toda rota mutante declarada e verificada por teste; viewer é read-only. |
| Auditoria | ✅ Persistida | `audit_events` + `_record_audit_event()` | Retenção configurável `AUDIT_RETENTION_DAYS`. |
| Observabilidade | ✅ JSON + métricas | `app/core/logging.py`, `/admin/observability/metrics` | Sem tracing distribuído. |
| Rate limiting | ✅ Redis atômico | `rate_limit_service.py` | Compartilhado entre réplicas; fallback local somente quando permitido. |
| Migrações | ✅ Versionadas | `app/storage/db.py` | 37 migrações; comando one-shot, advisory lock no PostgreSQL e `BEGIN IMMEDIATE` no SQLite. |
| Backup / cleanup | ✅ CLI endurecida | `tools/export_runtime_backup.py`, `cleanup_operational_data.py` | Backup v3 sanitizado, claims efêmeros removidos, publicação atômica e restore transacional. |
| Billing / planos | ✅ Stripe + enforcement | `stripe_billing_service.py`, `billing_routes.py` | Checkout, Portal, webhook assinado/idempotente, reconciliação e limites nos fluxos reais. |
| Realtime (SSE) | ✅ Redis + isolamento | `realtime_service.py`, `api/platform_routes.py` | Fan-out entre réplicas, sessão obrigatória e entrega por membership/tenant. |
| Company context | ✅ Persistido no banco | `core/company_context.py`, tabela `company_contexts` | Transacional, versionado, no backup; JSON vira só seed. |
| Identidade humana centralizada | ✅ Pronto | `identity_service.py`, tabelas 029/030 | Usuários, scrypt, memberships, convite, reset e sessões revogáveis. |
| Email transacional | ✅ Provider preparado | `transactional_email_service.py` | Resend para convite/reset; readiness falha se self-service estiver ativo sem entrega configurada. |
| Tracing distribuído | 🔴 Ausente | — | Apenas logs + request_id. |
| Deploy Docker Compose | ✅ Verificado | `Dockerfile`, `compose.yaml` | Migração one-shot, PostgreSQL, Redis, API, worker e Streamlit separados e saudáveis. |
| Deploy Vercel | ✅ Artefato pronto | `app/index.py`, `vercel.json` | Runtime stateless; migração deliberadamente fora do cold start. |

## Entregue no Release 1A

- `POST /webhooks/whatsapp` modo normalizado **desabilitado por padrão**; quando
  habilitado exige `INTERNAL_INGRESS_SECRET`, e falha fechado (`503`) se o segredo
  não estiver configurado.
- `POST /messages/incoming` deixa de ser anônimo em produção
  (`ENVIRONMENT=production` ou `STRICT_RUNTIME_VALIDATION=true`).
- Simulador da plataforma migrado para `POST /admin/simulator/messages`,
  autenticado e com papel `operator`.
- Empresa informada nas rotas públicas é resolvida contra `companies`; slug/id
  inexistente retorna `404` em vez de cair na empresa padrão.
- `GET /platform/events` exige sessão; eventos carregam `company_id` e só são
  entregues a conexões autorizadas para aquela empresa.
- Matriz endpoint→permissão explícita, com billing/onboarding/empresa exigindo
  `superadmin` e auditoria de `record_usage`.
- Company context persistido em `company_contexts` com revisão, concorrência
  otimista, importação idempotente de seeds e inclusão no backup.

## Entregue no Release 1A.1

- Todos os 63 filtros opcionais do repositório deixaram de enviar placeholder sem
  tipo; a suíte completa passa no PostgreSQL (I-017).
- Toda conexão adquirida é liberada exatamente uma vez, com rollback antes de
  voltar ao pool; rajadas de falha não esgotam o pool (I-016).
- Webhook Meta assinado resolve o tenant apenas por `phone_number_id`, com
  seletores de query/header ignorados e falha fechada.
- Resposta de saída usa o número vinculado à empresa da própria mensagem.
- Criação concorrente de company context produz conflito controlado, nunca uma
  violação de unicidade crua.
- CI executa a suíte completa contra SQLite e PostgreSQL.

## Entregue nos Releases 1A.2 e 1A.2.1

- Conflitos concorrentes de vínculo WhatsApp são decididos pelo banco e viram
  `409`, não `500`.
- Backup v3 cobre todo o runtime, sanitiza segredos, publica arquivo atômico
  `0600` e restaura dados/sequences em uma única transação.
- Migração SQLite→PostgreSQL introspecta tabelas e colunas, preserva todos os
  campos operacionais e aborta diante de schema não coberto.
- Readiness Docker/CI é autoritativa; o workflow testa SQLite e PostgreSQL.
- Inicialização concorrente de dois workers no SQLite é serializada antes da
  leitura do ledger de migrações.
- O healthcheck `--validate-only` do worker fecha o pool PostgreSQL e termina; o
  painel Streamlit tem healthcheck próprio em `8501/_stcore/health`.
- Gate histórico: **592 passed + 14 skipped no SQLite** e **606 passed no
  PostgreSQL**, além de Compose completo saudável e sem erro de startup.

## Entregue no Release 1B

- Identidade durável com usuário, senha scrypt, memberships por tenant,
  convites, recuperação, sessões opacas revogáveis e CSRF.
- Integridade multi-tenant no banco, inclusive referências compostas
  `recurso + company_id` e rejeição de órfãos.
- Outbound e lembretes com claim atômico, retry exponencial, dead-letter,
  recuperação de claim abandonado e lease do worker.
- Redis real para rate limiting e fan-out realtime entre réplicas.
- Dependências separadas e fixadas com hashes, imagens `api`/`admin`, migração
  one-shot, Compose com PostgreSQL + Redis e entrada stateless para Vercel.
- Email transacional de convite/reset via Resend, sem enumeração de conta.
- Tela de Equipe na SPA para listar membros, convidar, alterar papel/estado e
  aplicar as proteções de owner sem recorrer à API manualmente.

## Entregue no Release 1C

- Stripe Checkout e Customer Portal restritos ao owner do tenant.
- Webhook assinado com ledger, hash do payload, deduplicação, retry de evento
  falho/abandonado e proteção contra regressão fora de ordem.
- Reconciliação canônica de assinatura, invoice, pagamento, cancelamento,
  trial e grace period.
- Enforcement transacional em bookings, campanhas e fine-tuning; mensagens e
  handoffs permanecem fail-open com metering para preservar o canal do cliente.
- Frontend de billing usa somente Checkout/Portal; ativações administrativas
  legadas deixam de ser o caminho de produto.

## Gate final 1B/1C

- SQLite + Redis: **631 passed, 14 skipped**.
- PostgreSQL + Redis: **644 passed, 1 skipped**.
- `mypy app`: **86 arquivos sem erros**.
- Ruff crítico, Bandit médio/alto e `pip-audit`: aprovados.
- API com dois workers, admin, migração one-shot e worker `--run-once`:
  saudáveis em imagens Docker reais.
- Fluxos web de login, cadastro, recuperação, aceite de convite e gestão de
  equipe: renderizados e sem erros de console no navegador.

## Linhas vermelhas conhecidas

- **Tokens administrativos estáticos** continuam disponíveis somente como
  break-glass/compatibilidade. O caminho SaaS normal é identidade + membership.
- **Sanitização de dataset** ainda é básica (markers + redação de `@`) — exige etapa dedicada antes de operar com dados reais. Detalhe em [[50-Security/06-PII e Dataset]].
- **Calendar provider default = mock** — em produção real precisa virar `google_calendar` ou `caldav`.
- **Integrações externas ainda precisam ser ativadas**: credenciais live/test
  de Stripe, Resend, WhatsApp, calendário e OpenAI não pertencem ao repositório.
- **Vercel não hospeda o worker contínuo**: ele deve rodar em serviço durável ou
  scheduler separado, apontando para o mesmo PostgreSQL/Redis.
- **Tracing distribuído** ainda não existe; há logs JSON, request id e métricas,
  mas o go-live deve adicionar APM/error tracking.

## Volume de código

| Arquivo | Linhas |
|---------|-------|
| `app/storage/repository.py` | ~4 859 |
| `app/api/admin_routes.py` | ~2 160 |
| `app/storage/db.py` | ~2 120 |
| `app/services/calendar_service.py` | ~1 503 |
| `app/services/conversation_workflow_service.py` | ~1 467 |
| `app/services/system_readiness_service.py` | ~870 |

`repository.py` e `admin_routes.py` concentram operação. Mexer neles **sempre** com revisão lateral.

## Próximas leituras

- [[80-Improvements/01-Limites Conhecidos]]
- [[80-Improvements/05-Backlog de Produto]]
- [[10-Architecture/01-Arquitetura em Camadas]]
