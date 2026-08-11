## Arquitetura

O Project WAPP e um backend de atendimento conversacional orientado a operacao comercial. A aplicacao foi organizada para separar claramente entrada HTTP, orquestracao, regras de negocio, integracoes com LLM/providers e persistencia.

### Objetivo do sistema

O produto atende quatro frentes principais:

- receber mensagens de clientes e decidir se a IA pode responder;
- escalar casos sensiveis para responsavel humano;
- persistir contexto operacional para continuidade do atendimento;
- preparar dados e operacao para evolucao via fine-tuning.

### Camadas

#### 1. Entrada HTTP

Arquivos principais:

- `app/main.py`
- `app/api/routes.py`
- `app/api/admin_routes.py`
- `app/api/platform_routes.py`
- `app/api/schemas.py`

Responsabilidades:

- inicializar a aplicacao FastAPI;
- expor endpoints publicos de entrada de mensagem e webhook;
- expor endpoints administrativos para operacao, treinamento e outbound;
- servir a interface dedicada em `/platform` com assets estaticos;
- autenticar a plataforma por sessao web com cookie HTTP-only;
- validar payloads e traduzir erros de dominio em respostas HTTP.

#### 2. Orquestracao

Arquivo principal:

- `app/core/orchestrator.py`

Responsabilidades:

- resolver a empresa ativa para a conversa;
- salvar/atualizar contato;
- classificar a mensagem;
- decidir se existe resposta automatica;
- consultar contexto da empresa;
- criar handoff quando necessario;
- atualizar memoria e estado do pipeline;
- persistir historico da interacao.

Fluxo simplificado:

1. Recebe a mensagem do contato.
2. Resolve `company_id` e `company_slug`.
3. Recupera historico e eventual clarificacao pendente.
4. Classifica a mensagem.
5. Decide entre resposta automatica, review ou handoff.
6. Gera resposta, quando permitido.
7. Persiste mensagem, memoria e pipeline.
8. Cria `pending_question` quando a IA nao deve decidir sozinha.

#### 3. Servicos de negocio

Diretorio:

- `app/services/`

Servicos principais:

- `classification_service.py`: classificacao por heuristica ou OpenAI.
- `decision_service.py`: define auto reply, prioridade e acao.
- `reply_services.py`: gera resposta comercial com contexto, exemplos e conhecimento.
- `memory_service.py`: resume a conversa para continuidade.
- `pipeline_service.py`: infere o estagio comercial do contato.
- `handoff_service.py`: decide escalonamento humano e normaliza resolucoes.
- `knowledge_service.py`: transforma resolucoes aprovadas em conhecimento reutilizavel.
- `outbound_dispatch_service.py`: envia mensagens outbound por provider configurado.
- `finetuning_service.py`: cria, consulta e persiste jobs de fine-tuning.
- `training_export_service.py`: cura, deduplica, divide e exporta datasets.
- `training_import_service.py`: importa CSV/JSON de base historica ou CRM, atualiza contatos e cria exemplos curados.
- `calendar_service.py`: resolve integracao de agenda, perfis de disponibilidade, bookings e sync com calendario externo.
- `appointment_queue_service.py`: gerencia fila de prioridade, ofertas de remanejamento e follow-ups.
- `conversation_workflow_service.py`: conduz fluxos conversacionais de agenda, incluindo agendamento do cliente, cancelamento, reagendamento e comandos internos do operador.

#### 4. Providers e integracoes

Diretorio:

- `app/llm/providers/`
- `app/services/whatsapp_provider.py`

Integracoes atuais:

- OpenAI para classificacao, memoria, inferencia de pipeline, reescrita customer-facing e resposta comercial;
- provider mock para operacao sem dependencia externa;
- provider real de WhatsApp preparado para dispatch.

#### 5. Persistencia

Arquivos principais:

- `app/storage/db.py`
- `app/storage/repository.py`

Persistencia atual:

- Postgres suportado no runtime por `DATABASE_URL`;
- SQLite mantido como fallback para testes e desenvolvimento rapido;
- validacao dedicada de alvo Postgres via `POSTGRES_DATABASE_URL`;
- schema criado em bootstrap via `init_db()`;
- acesso concentrado em `RuntimeRepository`;
- migracao de dados legados via `app/tools/migrate_sqlite_to_postgres.py`.

Tabelas principais:

- `companies`
- `contacts`
- `messages`
- `pending_questions`
- `knowledge_entries`
- `message_examples`
- `outbound_messages`
- `finetuning_jobs`
- `audit_events`
- `schema_migrations`
- `calendar_integrations`
- `appointment_bookings`

### Contexto empresarial

Arquivos principais:

- `app/data/company_context.json`
- `app/data/company_contexts/<slug>.json`

O `company_context` funciona como contrato de negocio para cada empresa. Ele concentra:

- identidade e tom de marca;
- canais e horario de atendimento;
- politicas comerciais;
- catalogo;
- regras de negocio;
- diretrizes de LLM;
- politicas de agenda em `scheduling_policy.profiles`;
- contatos internos autorizados a operar agenda em `internal_operation.operator_contact_ids`.

Quando um contexto por empresa nao existe, o sistema faz fallback para o contexto global.

### Multiempresa

O sistema ja suporta escopo por empresa em:

- contatos;
- mensagens;
- pending questions;
- knowledge entries;
- outbound messages;
- message examples;
- jobs de fine-tuning.

O escopo pode ser resolvido por `company_id`, `company_slug` ou empresa padrao ativa.

### Operacao administrativa

Existe um painel Streamlit em `app/admin_app.py` para operacao minima, com foco em:

- readiness e onboarding operacional;
- cadastro de empresas;
- consulta de contatos;
- edicao estruturada de perfis de agenda e operacao interna no company context;
- edicao de `customer_profile` e observacoes de contato;
- consulta de knowledge base;
- curadoria de message examples;
- importacao inicial de base historica/CRM para curadoria de treino;
- atendimento de pending questions;
- dispatch outbound;
- acompanhamento de dataset e fine-tuning;
- leitura de auditoria e migracoes.

Agora existe tambem uma interface dedicada servida pelo proprio FastAPI em `/platform`, com foco em:

- `Control Tower` para readiness, onboarding e observabilidade;
- `Bot Studio` para simular cliente ou operador dentro do runtime real;
- `Flow Studio` para editar playbooks e mensagens aprovadas;
- `Agenda Console` para controlar bookings e fila;
- `Training Ops` para importar base historica e acompanhar fine-tuning.

### Observabilidade

O backend agora possui observabilidade nativa em tres frentes:

- logs estruturados em JSON;
- correlacao de requisicoes via `request_id`;
- resumo operacional por endpoint administrativo.

Componentes principais:

- `app/core/logging.py`: configuracao, formatter JSON, contexto por requisicao e utilitarios;
- middleware em `app/main.py`: gera/propaga `X-Request-ID` e registra inicio/fim de request;
- eventos instrumentados em rotas, orquestrador, outbound e fine-tuning.

Caracteristicas:

- cada resposta HTTP devolve `X-Request-ID`;
- erros nao tratados retornam `request_id` para facilitar investigacao;
- logs de negocio evitam registrar corpo completo de mensagem e mascaram identificadores sensiveis.

### Hardening Operacional

O backend agora inclui controles adicionais para operacao mais segura:

- autenticacao administrativa por token configuravel;
- RBAC administrativo por papel;
- autenticacao de sessao para a plataforma dedicada;
- trilha de auditoria persistida em banco;
- metricas exportaveis para integracao com coletores externos.

Elementos principais:

- `ENABLE_ADMIN_AUTH` e `ADMIN_API_TOKEN` controlam a protecao da superficie `/admin`;
- `ADMIN_VIEWER_TOKEN`, `ADMIN_OPERATOR_TOKEN` e `ADMIN_SUPERADMIN_TOKEN` permitem separar privilegios por papel;
- `PLATFORM_SESSION_SECRET` assina a sessao web da plataforma dedicada;
- `X-Admin-Actor` identifica o operador logico em acoes administrativas;
- tabela `audit_events` registra acoes criticas com `actor_id`, `request_id`, status e metadata;
- endpoint `GET /admin/observability/metrics` entrega metricas em formato texto para scraping.

### Migracoes

O banco agora possui migracoes formais versionadas, registradas em `schema_migrations`.

Objetivos:

- tornar evolucao do schema auditavel;
- reduzir risco de drift entre ambientes;
- permitir inspecao do estado de banco em runtime.

Estado atual:

- as migracoes sao executadas no bootstrap da aplicacao;
- o historico aplicado pode ser consultado em `GET /admin/system/migrations`;
- indices operacionais essenciais ja fazem parte do ciclo de migracao;
- a sexta rodada adicionou indices focados em crescimento multiempresa.

### Readiness e onboarding

O backend agora expõe uma camada dedicada de leitura operacional para apoiar rollout em cliente real.

Elementos principais:

- `GET /health/ready`: probe simples para orquestracao;
- `GET /admin/system/profile`: perfil atual de implantacao;
- `GET /admin/system/readiness`: checks consolidados de runtime, seguranca, contexto e preparo para escala;
- `GET /admin/system/onboarding`: checklist por empresa ou no escopo global.

O servico `app/services/system_readiness_service.py` consolida:

- status do banco de runtime;
- alvo Postgres e probe opcional;
- autenticacao administrativa e RBAC;
- contexto global e contexto por empresa;
- prontidao de exemplos para fine-tuning;
- snapshot operacional por empresa.

### Containers e deploy local

O projeto agora inclui:

- `Dockerfile` para empacotar a API;
- `compose.yaml` com a imagem oficial `postgres` e a API conectada ao banco;
- healthcheck via `pg_isready` para subida mais segura em ambiente local e homologacao.

### Agenda e calendarios

O backend agora possui uma camada propria para agenda operacional.

Capacidades atuais:

- integracao ativa resolvida por empresa;
- ativacao por empresa via `company_context`;
- perfis de agendamento opcionais por empresa para separar janelas por tipo de atendimento;
- provider `mock` funcional para validacao ponta a ponta;
- bookings persistidos em banco;
- endpoint de timeline proprio para renderizacao visual semanal na plataforma;
- cancelamento com sync no provider externo quando existir `external_event_id`;
- reagendamento com update do evento externo quando a integracao estiver ativa;
- cancelamento e reagendamento conversacional natural do proprio cliente;
- operacao interna por conversa para agendar cliente por nome ou telefone com confirmacao/desambiguacao;
- lembretes internos persistidos e materializaveis em outbound apenas para uso de dono ou funcionario;
- lembretes de cliente gerados automaticamente a partir de regras de agendamento;
- fluxo conversacional com estado por contato em `conversation_flows`;
- fila de prioridade persistida em `appointment_queue_entries` e `appointment_queue_offers`;
- worker recorrente para materializacao e disparo de lembretes vencidos;
- preparacao para Google Calendar via API oficial;
- preparacao para CalDAV, cobrindo iCloud/Apple Calendar e outros servidores compativeis.

Superficie administrativa:

- `GET /admin/calendar/providers`
- `GET /admin/calendar/profile`
- `GET /admin/calendar/integrations`
- `POST /admin/calendar/integrations`
- `POST /admin/calendar/availability/query`
- `GET /admin/calendar/bookings`
- `POST /admin/calendar/bookings`
- `POST /admin/calendar/bookings/{booking_id}/reschedule`
- `POST /admin/calendar/bookings/{booking_id}/cancel`
- `GET /admin/calendar/queue`
- `POST /admin/calendar/queue`
- `GET /admin/calendar/queue-offers`
- `POST /admin/calendar/queue-offers/{offer_id}/respond`
- `GET /admin/reminders`
- `POST /admin/reminders`
- `POST /admin/reminders/dispatch-due`

Arquivos centrais desse fluxo:

- `app/core/company_context.py`: valida e persiste o contrato de contexto por empresa, incluindo `scheduling_policy.profiles`.
- `app/core/company_context.py`: valida tambem `conversation_pipelines`, com transicoes e posicoes do canvas.
- `app/services/calendar_service.py`: resolve o perfil aplicavel, calcula slots, valida janela, cria/cancela/reagenda booking e sincroniza provider externo.
- `app/services/conversation_workflow_service.py`: conduz o agendamento conversacional do cliente, o cancelamento/reagendamento natural e a operacao interna de agenda.
- `app/services/appointment_queue_service.py`: controla fila de prioridade, ofertas, espera, permanencia na fila e remanejamento.
- `app/services/training_import_service.py`: transforma CSV/JSON curado em atualizacao de contatos e `message_examples`.
- `app/services/platform_session_service.py`: emite e valida a sessao web da plataforma dedicada.
- `app/storage/db.py`: materializa colunas e tabelas de agenda, fila e lembretes no bootstrap/migracoes internas.
- `app/storage/repository.py`: faz leitura/escrita de bookings, fila, offers, reminders e integracoes.
- `app/api/admin_routes.py`: expõe operacao administrativa de disponibilidade, timeline, bookings, reagendamento, cancelamento, fila, contatos e importacao de treino.

### Decisoes arquiteturais atuais

- Repositorio unico para simplificar a camada de persistencia nesta fase do produto.
- Fallbacks locais quando OpenAI nao esta configurado.
- Handoff explicito para casos de baixa confianca ou regra de negocio sensivel.
- Dataset de fine-tuning derivado apenas de exemplos aprovados e curados.
- Fato mutavel resolvido por company context publicado, RAG ou servico
  deterministico; o modelo nao e fonte de verdade factual.
- Isolamento por empresa aplicado dentro das queries, nao como filtragem
  posterior em memoria.

A arquitetura multiempresa completa (resolucao de tenant, company context
versionado, taxonomia canonica, RAG tenant-safe, importacao governada, ciclo
de dataset, avaliacao e registro de modelo por empresa) esta em
[multitenant_platform.md](multitenant_platform.md).

### Automation Studio

O modulo de automacoes visuais introduz uma separacao propria entre **control
plane** (workflow, rascunho, versao imutavel, deployment) e **data plane**
(evento, outbox, execucao, step, job, efeito, portao humano). O dominio puro
vive em `app/automations/` e nao faz I/O: ele define o grafo, o catalogo de
nos, a validacao autoritativa e a **semantica unica** que simulacao e motor
compartilham.

Tres garantias sustentam o modulo:

1. **Outbox transacional.** O evento `message.processed` e gravado no mesmo
   commit da mensagem, dentro de um SAVEPOINT: nao ha janela em que a mensagem
   existe e o fato foi perdido, e uma falha na automacao nao derruba o
   atendimento.
2. **Version pinning.** Uma execucao carrega `version_id` e o motor carrega a
   versao *da execucao*, nunca a do deployment corrente. Republicar nao muda o
   comportamento de quem ja esta em espera.
3. **Ledger de efeitos.** Todo efeito externo reserva uma linha antes da
   chamada; um retry que recalcule a mesma chave encontra o efeito concluido e
   nao repete a acao.

O detalhamento esta em [automation_studio.md](automation_studio.md).

### Limites atuais conhecidos

- As migracoes continuam sendo gerenciadas internamente, sem Alembic; isso funciona para o produto atual, mas uma futura etapa de governanca pode migrar o ciclo de schema para uma ferramenta dedicada.
- O RBAC atual e baseado em token estatico por papel; para ambientes maiores, o ideal e evoluir para identidade centralizada.
- A promocao automatica de conhecimento para `company_context` ainda nao esta implementada como fluxo completo.
- A busca vetorial existe como contrato e adapter, mas o caminho padrao e
  lexical; embeddings reais e pgvector continuam opt-in.
- O backend compartilhado de rate limit e SSE tem adapter Redis implementado,
  porem nao exercitado pela suite padrao, que roda offline.
- Ainda nao ha tracing distribuido nem exportacao direta para stacks externas de observabilidade.
- O provider de WhatsApp esta preparado, mas ainda precisa endurecimento operacional para uso produtivo.
- O Automation Studio nao executa codigo arbitrario, loops, fan-out nem HTTP
  generico; `trigger.schedule` valida mas ainda nao tem agendador emitindo o
  evento. Ver a secao 12 de [automation_studio.md](automation_studio.md).
- O claim concorrente de jobs foi exercitado em SQLite pela suite; o caminho
  `FOR UPDATE SKIP LOCKED` do Postgres esta implementado mas ainda nao foi
  validado contra um Postgres real.

### Proximos passos recomendados

1. Consolidar a suite de testes como gate obrigatorio.
2. Evoluir autenticacao baseada em token para identidade centralizada.
3. Adicionar politicas de retencao/arquivamento mais avancadas para auditoria.
4. Evoluir o ciclo de migracoes para governanca mais forte, se necessario.
5. Expandir tracing e exportacao para stack externa de monitoramento.
6. Fechar o ciclo de provider real e operacao multiempresa em ambiente controlado.
