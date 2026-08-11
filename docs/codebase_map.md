# Mapa resumido do código

## Objetivo
Este arquivo serve como mapa rápido para manutenção. A ideia é responder duas perguntas:

1. em qual arquivo começo quando preciso mudar um comportamento;
2. o que cada arquivo guarda, de forma resumida.

## Por onde começar, dependendo do assunto

| Assunto | Arquivos de entrada |
| --- | --- |
| Mensagem recebida do cliente | `app/api/routes.py`, `app/core/orchestrator.py`, `app/services/reply_services.py` |
| Fluxos conversacionais de agenda, cancelamento e reagendamento | `app/services/conversation_workflow_service.py`, `app/services/calendar_service.py`, `app/services/appointment_queue_service.py` |
| Plataforma web `/platform` | `app/platform/index.html`, `app/platform/assets/app.js`, `app/api/platform_routes.py`, `app/api/admin_routes.py` |
| Segurança admin / sessão | `app/api/admin_security.py`, `app/services/admin_auth_service.py`, `app/services/platform_session_service.py` |
| Fine-tuning e importação de base | `app/services/training_import_service.py`, `app/services/training_export_service.py`, `app/services/finetuning_service.py` |
| Persistência e schema | `app/storage/repository.py`, `app/storage/db.py` |
| Painel Streamlit legado/apoio | `app/admin_app.py`, `app/ui/admin_api_client.py`, `app/ui/pages/*` |

## `app/`

### Raiz

- `app/__init__.py` - marca o pacote principal da aplicação.
- `app/admin_app.py` - painel administrativo em Streamlit; hoje funciona como contingência e apoio interno.
- `app/main.py` - ponto de entrada do FastAPI; sobe a API, middleware, healthchecks, router admin e frontend dedicado.

### `app/api/`

- `app/api/__init__.py` - marca o pacote de rotas da API.
- `app/api/admin_routes.py` - concentra a API administrativa: sistema, empresas, contexto, agenda, contatos, treino, outbound e auditoria.
- `app/api/admin_security.py` - autenticação e autorização admin; aceita token e sessão web da plataforma.
- `app/api/admin_support.py` - utilitários da camada admin, especialmente métricas Prometheus e gravação de auditoria.
- `app/api/platform_routes.py` - serve o frontend `/platform` e expõe login/logout/leitura da sessão web.
- `app/api/routes.py` - API pública do bot: entrada de mensagens, webhook do WhatsApp e auto-reply outbound.
- `app/api/schemas.py` - modelos Pydantic dos payloads e respostas HTTP.

### `app/core/`

- `app/core/__init__.py` - marca o pacote de núcleo.
- `app/core/company_context.py` - leitura, validação e escrita do `company_context`, inclusive `scheduling_policy` e `conversation_pipelines`.
- `app/core/config.py` - centraliza variáveis de ambiente e configurações de runtime.
- `app/core/exceptions.py` - exceções base da aplicação.
- `app/core/logging.py` - logging estruturado, contexto por request, mascaramento e utilitários de log.
- `app/core/orchestrator.py` - orquestra o fluxo principal da mensagem recebida e integra classificação, decisão, reply, memória, handoff e agenda.
- `app/core/state.py` - acesso simples ao estado runtime via repositório; hoje é uma camada fina.
- `app/core/training_config.py` - constantes de curadoria e filtros usados na preparação do dataset de treino.

### `app/data/`

- `app/data/company_context.json` - contexto padrão/global usado como fallback quando a empresa não tem arquivo próprio.
- `app/data/company_contexts/empresa-teste.json` - contexto de exemplo de uma empresa específica.
- `app/data/company_contexts/string.json` - contexto de empresa placeholder; vale revisar ou limpar em ambiente real.

### `app/llm/`

- `app/llm/__init__.py` - marca o pacote de LLM.
- `app/llm/base.py` - wrapper mínimo legado para gerar resposta via provider mock.
- `app/llm/prompts/system_output.py` - reservado para prompts/sistema; hoje está vazio.
- `app/llm/providers/__init__.py` - marca o pacote de providers de LLM.
- `app/llm/providers/mock_provider.py` - provider fake para desenvolvimento e testes simples.
- `app/llm/providers/openai_provider.py` - provider OpenAI usado para classificação, reply, interpretação natural e fine-tuning.

### `app/platform/`

- `app/platform/index.html` - shell HTML da plataforma dedicada.
- `app/platform/assets/app.js` - SPA da plataforma; contém estado, loaders, renderização e bind de eventos.
- `app/platform/assets/styles.css` - sistema visual da plataforma: layout, tipografia, cards, canvas e agenda visual.

### `app/services/`

- `app/services/admin_auth_service.py` - resolve papéis administrativos a partir dos tokens configurados.
- `app/services/appointment_queue_service.py` - fila de remanejamento, ofertas de vaga, follow-up, interpretação natural de aceite/espera/recusa.
- `app/services/calendar_service.py` - agenda, disponibilidade, bookings, integrações externas, políticas por perfil e timeline semanal.
- `app/services/classification_service.py` - classifica mensagens; possui fallback quando o provider não estiver disponível.
- `app/services/conversation_workflow_service.py` - coordena fluxos conversacionais de agenda, cancelamento, reagendamento e operação interna por linguagem natural.
- `app/services/decision_service.py` - decide se a mensagem pode ter auto-reply, handoff ou outro tratamento.
- `app/services/finetuning_service.py` - cria e consulta jobs de fine-tuning.
- `app/services/handoff_service.py` - decide quando abrir dúvida para humano e transforma respostas do responsável em texto customer-facing.
- `app/services/knowledge_service.py` - converte resoluções humanas em conhecimento estruturado quando fizer sentido.
- `app/services/memory_service.py` - atualiza o resumo de memória do contato.
- `app/services/outbound_dispatch_service.py` - despacha mensagens pendentes para provider/canal e atualiza status.
- `app/services/pipeline_service.py` - infere estágio/pipeline comercial a partir da conversa.
- `app/services/platform_session_service.py` - emite e valida a sessão web da plataforma via cookie assinado.
- `app/services/reminder_service.py` - cria e dispara lembretes internos e lembretes automáticos de booking.
- `app/services/reply_services.py` - gera a resposta comercial final usando contexto, conhecimento, memória, estilo e regras.
- `app/services/runtime_guard_service.py` - bloqueia subida de componentes quando o ambiente obrigatório não está pronto.
- `app/services/style_service.py` - seleciona exemplos de estilo e formata contexto de tom/linguagem.
- `app/services/system_readiness_service.py` - calcula readiness, onboarding e perfil operacional do sistema.
- `app/services/training_export_service.py` - filtra, deduplica, resume e exporta dataset para treino/fine-tuning.
- `app/services/training_import_service.py` - importa CSV/JSON histórico para atualizar contatos e promover exemplos curados.
- `app/services/whatsapp_provider.py` - integração do canal WhatsApp para envio outbound real.

### `app/storage/`

- `app/storage/__init__.py` - marca o pacote de persistência.
- `app/storage/db.py` - conexão SQLite/Postgres, criação de schema, índices, migrações e probes de banco.
- `app/storage/memory_store.py` - memória em dicionário; útil como artefato legado/minimalista.
- `app/storage/repository.py` - camada principal de acesso a dados; encapsula operações sobre contatos, mensagens, agenda, treino, auditoria e afins.

### `app/tools/`

- `app/tools/__init__` - arquivo vazio e sem extensão; hoje parece redundante/legado.
- `app/tools/__init__.py` - marca o pacote de ferramentas.
- `app/tools/calendar_mock.py` - helper de disponibilidade mock usado no MVP inicial.
- `app/tools/migrate_sqlite_to_postgres.py` - migra dados do SQLite para Postgres.
- `app/tools/preflight_check.py` - executa verificação operacional antes de publicar ou subir a stack.
- `app/tools/registry.py` - arquivo vazio reservado para futura centralização de ferramentas.
- `app/tools/reminder_worker.py` - worker recorrente de lembretes e follow-ups de agenda/fila.

### `app/ui/`

- `app/ui/__init__.py` - marca o pacote da UI Streamlit.
- `app/ui/admin_api_client.py` - cliente HTTP usado pelo painel Streamlit para conversar com a API.

### `app/ui/pages/`

- `app/ui/pages/__init__.py` - marca o pacote das páginas Streamlit.
- `app/ui/pages/calendar_page.py` - tela Streamlit de agenda, integrações, bookings e fila.
- `app/ui/pages/companies_page.py` - tela Streamlit para cadastro/listagem de empresas.
- `app/ui/pages/company_context_page.py` - tela Streamlit de edição do `company_context`, perfis de agenda e operação interna.
- `app/ui/pages/contacts_page.py` - tela Streamlit para busca e edição de contatos.
- `app/ui/pages/finetuning_page.py` - tela Streamlit para dataset, importação e jobs de fine-tuning.
- `app/ui/pages/knowledge_page.py` - tela Streamlit de leitura da knowledge base.
- `app/ui/pages/message_examples_page.py` - tela Streamlit para exemplos aprovados de mensagem.
- `app/ui/pages/outbound_page.py` - tela Streamlit para inspeção e dispatch de outbound.
- `app/ui/pages/pending_questions_page.py` - tela Streamlit para resolver handoffs/perguntas pendentes.
- `app/ui/pages/system_page.py` - tela Streamlit com readiness, onboarding, auditoria e perfil do sistema.

## `tests/`

### Base

- `tests/__init__.py` - marca o pacote de testes.
- `tests/conftest_py` - arquivo vazio com nome fora do padrão; vale revisar se deveria ser `conftest.py`.

### Cobertura por assunto

- `tests/test_admin_routes.py` - cobre endpoints administrativos, incluindo agenda, contatos, empresa e treino.
- `tests/test_appointment_queue_service.py` - cobre fila de remanejamento, ofertas e respostas naturais.
- `tests/test_business_scenarios.py` - valida cenários de negócio mais próximos do uso real.
- `tests/test_calendar_service.py` - testa disponibilidade, bookings, perfis e sync da agenda.
- `tests/test_company_context.py` - valida leitura e regras do `company_context`, inclusive pipelines visuais.
- `tests/test_conversation_workflow_service.py` - cobre agenda conversacional, cancelamento, reagendamento e operação interna.
- `tests/test_data/conversation_scenarios.py` - dados/cenários reutilizados nos testes conversacionais.
- `tests/test_db_backend.py` - cobre backend de banco e compatibilidade de persistência.
- `tests/test_finetuning_service.py` - cobre criação e leitura de jobs de fine-tuning.
- `tests/test_memory_service.py` - cobre atualização de memória do contato.
- `tests/test_operational_rules.py` - valida regras operacionais de negócio e proteção de comportamento.
- `tests/test_orchestrator.py` - cobre o orquestrador principal.
- `tests/test_orchestrator_flow.py` - cobre variações de fluxo do orquestrador.
- `tests/test_orchestrator_no_auto_reply.py` - garante cenários onde o bot não deve responder automaticamente.
- `tests/test_outbound_dispatch_service.py` - cobre despacho outbound e erros de provider.
- `tests/test_pipeline_service.py` - cobre inferência de pipeline comercial.
- `tests/test_pipeline_service_validation.py` - cobre validação/regras do pipeline.
- `tests/test_reminder_service.py` - cobre criação e disparo de lembretes.
- `tests/test_reply_service.py` - cobre geração de resposta comercial.
- `tests/test_routes.py` - cobre rotas públicas, webhook e frontend `/platform`.
- `tests/test_system_readiness_service.py` - cobre readiness, onboarding e perfil do sistema.
- `tests/test_tools.py` - cobre ferramentas auxiliares e scripts.
- `tests/test_training_export_service.py` - cobre filtro, deduplicação e exportação de dataset.
- `tests/test_training_import_service.py` - cobre importação histórica/CRM.

## Observações de manutenção

- A plataforma nova de produto está em `app/platform/`; o painel Streamlit em `app/ui/` continua existindo como apoio.
- `app/storage/repository.py` e `app/api/admin_routes.py` seguem como arquivos grandes; quando mexer neles, vale revisar impacto lateral.
- `app/tools/__init__`, `app/tools/registry.py` e `tests/conftest_py` parecem artefatos legados ou placeholders; estão documentados para não passarem despercebidos.
- Quando o comportamento envolve agenda, normalmente o fluxo cruza `conversation_workflow_service.py`, `calendar_service.py` e `appointment_queue_service.py`.
- Quando o comportamento envolve UI da plataforma, normalmente o fluxo cruza `app/platform/assets/app.js`, `app/api/platform_routes.py` e `app/api/admin_routes.py`.

---

## Automation Studio

### `app/automations/` — domínio puro, sem I/O

| Arquivo | Responsabilidade |
|---|---|
| `domain.py` | grafo tipado, estados, forma canônica, `effect_key`, `enrollment_key` |
| `catalog.py` | catálogo de nós v1; fonte única do validador, do motor e do inspector |
| `validation.py` | validação autoritativa: ciclos, alcance, alças, capacidades, segredos, conflito com o legado |
| `semantics.py` | `evaluate_node` — a única interpretação das regras |
| `simulation.py` | percurso sem efeito, usando a mesma semântica |
| `events.py` | eventos canônicos, redigidos por allowlist |
| `effects.py` | adapters de efeito externo, delegando aos serviços existentes |
| `engine.py` | motor durável: um passo por job, com lease e ledger |
| `exceptions.py` | erros do domínio, separando permanente de retryable |

### Serviços e persistência

| Arquivo | Responsabilidade |
|---|---|
| `app/services/automation_workflow_service.py` | control plane: rascunho, validação, simulação, publicação, estado |
| `app/services/automation_event_service.py` | emissão e dispatch do outbox |
| `app/services/automation_execution_service.py` | inspeção, retry, cancelamento, reconciliador |
| `app/services/automation_approval_service.py` | portões humanos |
| `app/services/automation_trigger_service.py` | construção dos eventos a partir dos fatos do runtime |
| `app/storage/automation_repository.py` | persistência tenant-scoped e claim concorrente |
| `app/storage/migrations_automation.py` | migrations 048–053 |
| `app/storage/migrations_hardening.py` | migrations 045–047 |
| `app/api/automation_routes.py` | 20 rotas tenant-scoped |
| `app/tools/automation_worker.py` | worker: reconcilia, despacha, executa |

### `frontend/automation-studio/`

React 18 + TypeScript strict + `@xyflow/react`, build Vite para
`app/platform/automations`. Ver `docs/automation_studio.md` §13.

### Testes de automação

| Arquivo | Cobre |
|---|---|
| `tests/test_automation_domain.py` | catálogo, forma canônica, validação, segredos |
| `tests/test_automation_conformance.py` | simulação e runtime escolhem o mesmo caminho |
| `tests/test_automation_engine.py` | idempotência, concorrência, lease, retry, pausa, version pinning, portões |
| `tests/test_automation_api.py` | isolamento por tenant, RBAC, 409, publicação |
| `tests/test_automation_e2e.py` | ciclo completo e integração com o orquestrador |
| `tests/test_automation_studio_bundle.py` | o frontend não carrega segredo nem executa fluxo |
| `tests/test_phase_zero_blockers.py` | os seis bloqueadores de autorização |
| `tests/test_checked_modules.py` | nenhum módulo sensível fora do mypy/Bandit do CI |
