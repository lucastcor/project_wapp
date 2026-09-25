# Project WAPP

> **Frente ativa — piloto com pequenos clientes:** comece por [`docs/piloto/INICIO.md`](docs/piloto/INICIO.md). O plano contém checkpoint, tarefas, coordenação de agentes e critérios de liberação. Para Claude Code, [`CLAUDE.md`](CLAUDE.md) direciona a leitura inicial.

Plataforma multi-tenant de comércio conversacional: atendimento com IA, operação comercial, handoff para humano, outbound multicanal, automação visual de fluxos, recuperação sobre a base de conhecimento da empresa (RAG) e ciclo completo de fine-tuning.

> **Chegando agora no projeto?** Comece por [`docs/estado_do_projeto.md`](docs/estado_do_projeto.md) — o que existe de fato, o que está verificado com evidência, e o que ainda depende de credencial externa.

## Testar com Qwen local

O ambiente deste computador usa Ollama com `qwen3.5:9b-q4_K_M`. Inicie API e workers com
`.venv/bin/python -m app.tools.local_dev --port 8010` e abra `http://127.0.0.1:8010/platform`.
Veja [configuração, fila durável e critérios para piloto](docs/local_qwen.md).
Para homologar conversa, agendamento e cancelamento, siga o [teste funcional local](docs/local_functional_test.md).
Para medir carga com contatos sintéticos, siga o [teste local de capacidade](docs/local_capacity_test.md).

## Visão geral
O Project WAPP é um backend em FastAPI pensado para evoluir de um chatbot simples para uma plataforma de atendimento orientada a negócio. O sistema já suporta classificação de mensagens, geração de resposta com OpenAI ou Ollama, memória por contato, pipeline comercial, handoff para responsável humano, knowledge base incremental, outbound persistido e pipeline de fine-tuning.

A arquitetura foi organizada para manter separação clara entre:
- API e rotas administrativas
- orquestração de mensagens
- serviços de negócio
- providers de LLM
- camada de persistência
- testes automatizados

## Principais capacidades atuais
- Recebimento e processamento de mensagens de clientes
- Classificação de intenção e decisão de ação
- Geração de respostas comerciais com OpenAI ou Qwen local via Ollama
- Memória por contato com resumo conversacional
- Pipeline comercial por estágio
- Handoff para responsável humano quando a IA não deve decidir sozinha
- Transformação da resposta do responsável em mensagem final para o cliente
- Knowledge base incremental e company context
- Cadastro de exemplos aprovados de mensagem
- Curadoria e exportação de dataset para fine-tuning
- Histórico e observabilidade de jobs de fine-tuning
- Outbound persistido com dispatch desacoplado do canal
- Provider real de WhatsApp preparado
- Agenda operacional com bookings persistidos e integrações preparadas
- Fila de remanejamento prioritária para recuperar vagas abertas após cancelamento
- Cancelamento e reagendamento conversacional natural pelo proprio cliente
- Operacao interna por conversa natural para agendar cliente por nome ou telefone, com confirmacao e desambiguacao
- Importador inicial de base historica/CRM para atualizar `customer_profile` e promover exemplos para curadoria de treino
- Frontend dedicado em `/platform` com analytics, simulador, flow studio,
  agenda, contatos, training, equipe, billing e onboarding
- Autenticacao propria de sessao para a plataforma dedicada com cookie HTTP-only
- Identidade SaaS com usuários, memberships por empresa, convite, recuperação,
  sessão revogável e proteção CSRF
- Stripe Checkout, Customer Portal, webhook reconciliado e enforcement de uso
- Redis para rate limiting e eventos realtime compartilhados entre réplicas
- Canvas visual persistido de pipelines conversacionais no `company_context`
- Timeline/calendario visual de agenda com layout semanal baseado em endpoint proprio do backend
- Testes automatizados para fluxos críticos

### Automação visual (Automation Studio)
- Editor de fluxos em canvas com catálogo de 25 nós (gatilhos, condições, ações,
  aprovação humana, espera, terminais)
- Motor de execução durável com outbox transacional, retry com backoff,
  dead-letter e idempotência por efeito externo
- Gatilhos reais: mensagem processada, handoff criado, agendamento criado,
  campanha enviada e agenda (`trigger.schedule`) por janela horária/diária/semanal
- Portões humanos: execução pausa aguardando aprovação e retoma no ponto exato
- Version pinning: uma execução em andamento continua no grafo com que começou
- Histórico de execução passo a passo, com redação de dados sensíveis
- Publicação e ativação bloqueadas quando o plano não cobre a capacidade do fluxo

### Conhecimento e recuperação (RAG)
- Documentos versionados, com versão publicada e versão em edição separadas
- Chunking por versão, busca lexical determinística e busca vetorial opcional
- Filtro de tenant dentro da query, nunca em descarte posterior
- Neutralização de injeção de prompt no conteúdo recuperado
- Citações rastreáveis por resposta (`messages.retrieval_json`)
- Sinal explícito de ausência de fonte relevante, em vez de silêncio

### Proteção de PII
- Detecção com validação real: CPF e CNPJ por dígito verificador, cartão por Luhn
- Valor original nunca persistido em staging, candidato ou dataset
- Pseudonimização irreversível com chave obrigatória em produção

### Ciclo de fine-tuning
- Importação de histórico com separação por sessão, candidatos e revisão humana
- Deduplicação por distância de edição e split sem vazamento entre treino e validação
- Critérios de prontidão que recusam o treino antes de gastar job de provider
- Avaliação com casos versionados; execução simulada nunca habilita promoção
- Portão de promoção que recusa regressão, e registro de modelo por empresa com fallback

### Isolamento e operação
- Guarda de integridade de tenant no próprio banco em todas as tabelas com `company_id`
- Escopo de empresa deny-by-default por rota, com papéis owner/admin/operator/viewer
- Workers duráveis em processos separados da API (entrada WhatsApp, lembretes e automação)

## Stack principal
- Python 3.13+
- FastAPI
- Pydantic
- Uvicorn
- Postgres com psycopg
- Redis
- Stripe
- SQLite como fallback local/testes
- Pytest
- OpenAI Python SDK

## Arquitetura do projeto
```text
app/
  api/
    routes.py                 entrada pública (mensagens, webhooks)
    admin_routes.py           API administrativa
    platform_routes.py        sessão e páginas da plataforma
    automation_routes.py      fluxos, execuções, aprovações
    knowledge_routes.py       documentos, versões, chunks
    dataset_routes.py         datasets, avaliação, registro de modelo
    company_scope.py          escopo de empresa deny-by-default
    admin_security.py         autenticação e autorização admin
  automations/
    catalog.py                catálogo de nós e capacidades
    domain.py                 grafo, validação e forma canônica
    engine.py                 execução passo a passo
    events.py                 eventos canônicos e mapa de gatilhos
    entitlements.py           capacidade do fluxo x limite do plano
  core/
    orchestrator.py           orquestra a mensagem recebida
    company_context.py        contexto por empresa
    config.py                 Settings
  llm/providers/              OpenAI, Ollama e mocks
  services/                   regras de negócio (~40 serviços)
    automation_*_service.py   workflow, trigger, execução, scheduler
    knowledge_*_service.py    documento, chunking, recuperação
    dataset_service.py        curadoria, split, congelamento
    evaluation_service.py     casos, execuções e notas
    model_registry_service.py resolução do modelo em runtime
    pii_service.py            detecção e pseudonimização
  storage/
    db.py                     conexão, pool e registro de migrations
    repository.py             persistência do runtime de conversa
    sql_support.py            helpers de SQL seguros para os repositórios
    automation_repository.py  fluxos, execuções, outbox
    knowledge_repository.py   documentos e chunks
    migrations_*.py           068 migrations acumuladas
  tools/
    run_migrations.py         passo explícito de release
    inbound_worker.py         fila durável de entrada WhatsApp
    local_dev.py              API e workers para testes com Qwen
    reminder_worker.py        worker de lembretes
    automation_worker.py      worker de automação e scheduler
    preflight_check.py        checagem antes de publicar
  platform/                   plataforma web servida pela API
    index.html, assets/
    automations/              bundle compilado do Automation Studio
  main.py
frontend/
  automation-studio/          editor visual (Vite + TypeScript)
tests/                        ~65 arquivos de teste
docs/
compose.yaml                  postgres, redis, migrate, api, worker,
                              automation-worker, inbound-worker, admin
Dockerfile                    alvos api / admin, com estágio do Studio
vercel.json
```

## Fluxos já implementados

### 1. Processamento de mensagem de cliente
O sistema recebe uma mensagem, recupera contexto do contato, classifica a intenção, decide se pode responder automaticamente e atualiza memória e pipeline comercial.

### 2. Handoff para responsável humano
Quando a IA encontra baixa confiança, dúvida de regra comercial, pedido de desconto ou situação sensível ao negócio, ela cria uma `pending_question` para o responsável.

### 3. Resposta do responsável
O responsável pode responder uma `pending_question` informando:
- resposta final
- necessidade de clarificação com o cliente
- regra global
- ação de conhecimento

Essa resposta passa por normalização e pode ser reescrita para linguagem customer-facing com OpenAI.

### 4. Outbound message
Quando a resposta do responsável deve virar mensagem ao cliente, o sistema cria um `outbound_message` persistido. Esse outbound pode ser despachado por provider `mock` ou provider real de WhatsApp.

### 5. Fine-tuning
O projeto já possui:
- cadastro de exemplos aprovados
- curadoria de exemplos aptos para treino
- deduplicação
- split treino/validação
- exportação JSON e JSONL
- criação e acompanhamento de jobs de fine-tuning
- persistência local do histórico de jobs

### 6. Agenda, lembretes e disponibilidade
O projeto agora possui:
- integrações de calendário persistidas por empresa
- agenda e lembretes ativados por contexto da empresa
- fluxo conversacional natural para agendamento com o cliente
- fluxo conversacional natural para cancelamento e reagendamento pelo proprio cliente
- fluxo interno para dono/atendente pedir agendamento de um cliente por linguagem natural
- consulta de disponibilidade com provider `mock`
- reservas persistidas em banco com resumo útil para a equipe
- perfis de agendamento por empresa para segmentar janela por tipo de atendimento, cliente ou regra operacional
- lembretes internos criados por dono ou funcionário em fluxo operacional separado
- lembretes do cliente gerados automaticamente a partir do agendamento e das regras da empresa
- fila de prioridade para remanejamento quando uma vaga é liberada
- ofertas de vaga persistidas para contato ordenado de clientes em espera ou com interesse em antecipação
- preparação para Google Calendar e CalDAV/iCloud
- sync real de cancelamento e reagendamento no provider externo quando a integração estiver ativa
- worker recorrente para processar lembretes vencidos
- follow-up automático de ofertas de remanejamento quando o cliente pede tempo para verificar
- painel admin para disponibilidade, integrações e bookings

## Banco de dados
O runtime agora suporta SQLite e Postgres, com foco operacional em Postgres. As tabelas atuais são:
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
- `appointment_queue_entries`
- `appointment_queue_offers`
- `conversation_flows`
- `customer_reminders`
- `saas_users`
- `company_memberships`
- `identity_invitations`
- `user_sessions`
- `password_reset_tokens`
- `worker_leases`
- `billing_plans`
- `billing_subscriptions`
- `billing_usage_records`
- `billing_webhook_events`

O projeto também já possui:
- configuração explícita via `DATABASE_URL`;
- `POSTGRES_DATABASE_URL` para probe/validação dedicada;
- índices operacionais adicionais para crescimento multiempresa;
- inspeção administrativa de migrações, readiness e onboarding.

### Perfis de agendamento por empresa
O agendamento agora suporta uma camada de segmentação opcional em `company_context.scheduling_policy.profiles`.

Cada perfil pode definir, por empresa:
- `key` e `label`
- `allowed_weekdays`
- `day_windows` por dia da semana
- `allowed_windows` como janela fallback
- `slot_increment_minutes`
- `default_duration_minutes`
- `minimum_notice_minutes`
- `service_names`
- `customer_profiles`
- `match_keywords`

Exemplo:

```json
{
  "scheduling_policy": {
    "default_profile_key": "novos",
    "profiles": [
      {
        "key": "novos",
        "label": "Clientes novos",
        "allowed_weekdays": ["monday", "wednesday"],
        "day_windows": {
          "monday": "09:00-12:00",
          "wednesday": "14:00-18:00"
        },
        "service_names": ["Consultoria inicial"],
        "customer_profiles": ["new_customer"],
        "minimum_notice_minutes": 180,
        "default_duration_minutes": 60
      },
      {
        "key": "retorno",
        "label": "Retorno / retirada",
        "allowed_windows": "13:00-18:00",
        "service_names": ["Retirada", "Prova", "Retorno"],
        "customer_profiles": ["returning_customer"]
      }
    ]
  }
}
```

Se a empresa nao tiver agenda, basta manter `enabled_modules.scheduling=false`. Se tiver agenda sem segregacao, pode usar apenas `business_hours` e omitir `profiles`.

### Operacao interna por conversa
O `company_context` agora tambem pode registrar quem sao os contatos internos autorizados a operar agenda por WhatsApp natural:

```json
{
  "internal_operation": {
    "operator_contact_ids": ["5511999990000", "5511999990001"]
  }
}
```

Quando um desses contatos manda algo como "marca a Maria para amanha as 10h", o backend:
- interpreta o comando com LLM;
- busca contatos por nome ou telefone;
- pede confirmacao do contato quando existir apenas uma correspondencia;
- exige desambiguacao quando houver varios clientes possiveis;
- consulta a disponibilidade respeitando `scheduling_policy.profiles`;
- pede confirmacao final antes de criar o booking.

### Canvas de fluxo no company context
O `Flow Studio` persiste o desenho da conversa diretamente em `company_context.conversation_pipelines`.

Cada etapa pode registrar:
- `key`
- `label`
- `objective`
- `trigger_terms`
- `success_criteria`
- `handoff_when`
- `preferred_cta`
- `next_keys`
- `canvas_position`

Esse contrato e validado no backend, incluindo unicidade de `key` e referencias validas em `next_keys`.

## Configuração por ambiente
O projeto depende de variáveis de ambiente. Não suba segredos reais para o repositório.

### Qual arquivo usar
- `.env.example`: modelo versionado. Serve apenas como referência. Nunca coloque chaves reais nele.
- `.env`: arquivo para desenvolvimento local quando voce roda comandos diretamente no host, por exemplo `uvicorn app.main:app --reload`. O backend carrega `.env` automaticamente via `load_dotenv()`.
- `.env.production`: arquivo local para homologacao/producao com Docker Compose. Ele nao e versionado e deve receber as chaves reais quando voce subir a stack com `docker compose --env-file .env.production ...`.

Regra pratica:
- se voce vai rodar o app manualmente no host, preencha `.env`;
- se voce vai rodar a stack pelo `compose.yaml`, preencha `.env.production`;
- se voce so vai usar Docker, pode deixar `.env` vazio ou nem criar esse arquivo;
- `.env.example` nunca deve ter segredo real.

### Exemplo de `.env.example`
```env
ENVIRONMENT=production
DEBUG=false
STRICT_RUNTIME_VALIDATION=true
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
OPENAI_FINETUNING_BASE_MODEL=gpt-4o-mini-2024-07-18
OPENAI_FINETUNING_SUFFIX=project-wapp-style
ENABLE_FINETUNING_PIPELINE=false
OPENAI_FINETUNED_MODEL=
USE_FINETUNED_MODEL_FOR_REPLIES=false

DATABASE_URL=postgresql://project_wapp:project_wapp@127.0.0.1:5432/project_wapp
POSTGRES_DATABASE_URL=postgresql://project_wapp:project_wapp@127.0.0.1:5432/project_wapp
COMPOSE_DATABASE_URL=postgresql://project_wapp:project_wapp@postgres:5432/project_wapp
COMPOSE_POSTGRES_DATABASE_URL=postgresql://project_wapp:project_wapp@postgres:5432/project_wapp
DATABASE_POOL_SIZE=10
DATABASE_STATEMENT_TIMEOUT_MS=5000
RUN_MIGRATIONS_ON_STARTUP=false
REDIS_URL=redis://127.0.0.1:6379/0
COMPOSE_REDIS_URL=redis://redis:6379/0
REQUIRE_SHARED_STATE=true
ENABLE_ADMIN_AUTH=true
ADMIN_API_TOKEN=change-me-superadmin-token
ADMIN_VIEWER_TOKEN=change-me-viewer-token
ADMIN_OPERATOR_TOKEN=change-me-operator-token
ADMIN_SUPERADMIN_TOKEN=change-me-superadmin-token
ADMIN_ACTOR=streamlit-admin
ADMIN_APP_API_BASE_URL=http://127.0.0.1:8000
AUDIT_RETENTION_DAYS=90
ENABLE_SELF_SERVICE_SIGNUP=false
IDENTITY_EXPOSE_ACTION_TOKENS=false
PLATFORM_PUBLIC_URL=https://app.seu-dominio.com
TRANSACTIONAL_EMAIL_PROVIDER=resend
TRANSACTIONAL_EMAIL_FROM=
RESEND_API_KEY=
ENABLE_BILLING_ENFORCEMENT=false
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PUBLISHABLE_KEY=
REMINDER_WORKER_ENABLED=true
REMINDER_WORKER_POLL_SECONDS=60
REMINDER_WORKER_DISPATCH_IMMEDIATELY=true
REMINDER_WORKER_CHANNEL=
REMINDER_WORKER_AUDIENCES=customer,internal
APPOINTMENT_QUEUE_AUTO_DISPATCH=true
APPOINTMENT_QUEUE_RESPONSE_WINDOW_MINUTES=30
APPOINTMENT_QUEUE_WAIT_FOLLOWUP_MINUTES=30

CLASSIFICATION_PROVIDER=openai
OUTBOUND_PROVIDER=whatsapp
OUTBOUND_DEFAULT_CHANNEL=whatsapp
CALENDAR_PROVIDER=mock
CALENDAR_TIMEZONE=America/Sao_Paulo
CALENDAR_DEFAULT_WORKDAY_START=09:00
CALENDAR_DEFAULT_WORKDAY_END=18:00
CALENDAR_SLOT_INCREMENT_MINUTES=30
GOOGLE_CALENDAR_CLIENT_ID=
GOOGLE_CALENDAR_CLIENT_SECRET=
GOOGLE_CALENDAR_REFRESH_TOKEN=
GOOGLE_CALENDAR_ID=
CALDAV_URL=
CALDAV_USERNAME=
CALDAV_PASSWORD=
CALDAV_CALENDAR_NAME=

WHATSAPP_API_BASE_URL=
WHATSAPP_API_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_WEBHOOK_VERIFY_TOKEN=change-me-whatsapp-verify-token
WHATSAPP_APP_SECRET=change-me-whatsapp-app-secret
POSTGRES_DB=project_wapp
POSTGRES_USER=project_wapp
POSTGRES_PASSWORD=project_wapp
POSTGRES_PORT=5432
APP_PORT=8000
ADMIN_PORT=8501
```

### Arquivo local de producao
Existe um arquivo local pronto em `.env.production` com placeholders operacionais.
Ele nao e versionado por Git e serve como ponto de partida para deploy/homologacao.

### Onde colocar as chaves corretas
Para o fluxo mais profissional do projeto hoje, coloque as chaves reais em `.env.production`.

Sobre banco:
- `DATABASE_URL` e `POSTGRES_DATABASE_URL`: use quando voce rodar scripts ou a API diretamente no host.
- `COMPOSE_DATABASE_URL` e `COMPOSE_POSTGRES_DATABASE_URL`: use quando a stack subir pelo `docker compose`, porque dentro do container o host certo do banco e `postgres`, nao `127.0.0.1`.

As variaveis internas que voce mesmo controla sao:
- `POSTGRES_PASSWORD`
- `ADMIN_API_TOKEN`
- `ADMIN_VIEWER_TOKEN`
- `ADMIN_OPERATOR_TOKEN`
- `ADMIN_SUPERADMIN_TOKEN`
- `PLATFORM_SESSION_SECRET`

As variaveis externas, que precisam vir do provedor real, sao:
- `OPENAI_API_KEY`
- `GOOGLE_CALENDAR_CLIENT_ID` e `GOOGLE_CALENDAR_CLIENT_SECRET` (projeto Google Cloud da instalação; cada empresa conecta a própria agenda pelo painel)
- `META_APP_ID`, `META_EMBEDDED_SIGNUP_CONFIG_ID`, `WHATSAPP_APP_SECRET` e `WHATSAPP_WEBHOOK_VERIFY_TOKEN` (app da Meta da instalação; cada empresa conecta o próprio número pelo painel)
- `CREDENTIALS_ENCRYPTION_KEY` (chave Fernet gerada por você; cifra os tokens por empresa)
- `WHATSAPP_API_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` e `GOOGLE_CALENDAR_REFRESH_TOKEN` só numa instalação antiga de número/agenda únicos
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `RESEND_API_KEY`

Roteiro de implantação em servidor barato, com HTTPS e os checklists da Meta e do Google: [`docs/piloto/IMPLANTACAO.md`](docs/piloto/IMPLANTACAO.md).

## Como rodar localmente
### 1. Criar e ativar ambiente virtual
```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Instalar dependências
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

`requirements.txt` contém somente o runtime da API/worker;
`requirements-admin.txt` adiciona Streamlit; `requirements-dev.txt` contém os
gates de desenvolvimento. Todos são fixados com hashes.

### 3. Escolher o modo de execucao
Voce tem dois caminhos:

- modo desenvolvimento simples: usa `.env` e sobe API direto no host;
- modo producao/homologacao: usa `.env.production` e sobe tudo com Docker Compose.

### 4. Desenvolvimento simples com `.env`
Crie `.env` com base em `.env.example` e rode:
```bash
uvicorn app.main:app --reload
```

### 5. Subir stack completa com Docker e `.env.production`
Esse e o fluxo recomendado para validacao seria, homologacao e producao.

#### 5.1. Antes de subir
- garanta que o Docker Desktop ou o Docker daemon esteja ligado;
- revise `.env.production` e troque todo valor `change-me-*` por valor real;
- mantenha `DATABASE_URL`, `POSTGRES_DATABASE_URL` e `POSTGRES_PASSWORD` coerentes entre si no host;
- mantenha `COMPOSE_DATABASE_URL` e `COMPOSE_POSTGRES_DATABASE_URL` apontando para `@postgres:5432` dentro do Docker Compose.

#### 5.2. Subir apenas o Postgres
Para local/homologacao, o caminho mais simples e suportado e usar o Postgres do proprio `compose.yaml`:
```bash
docker compose --env-file .env.production up -d postgres
```

Observacao:
- para ambiente local e homologacao, sim, o mais pratico e subir o Postgres pelo Docker;
- em producao real, voce tambem pode apontar `DATABASE_URL` para um Postgres externo/gerenciado e nem subir o servico `postgres` do compose.
- se a stack roda via `docker compose`, nao use `127.0.0.1` nas URLs internas do container; use `COMPOSE_DATABASE_URL` e `COMPOSE_POSTGRES_DATABASE_URL`.

#### 5.3. Migrar dados antigos do SQLite, se existir
Se voce ja tiver dados em `project_wapp.db`, exporte as variaveis de `.env.production` no shell e rode a migracao:
```bash
set -a
source .env.production
set +a

python3 -m app.tools.migrate_sqlite_to_postgres \
  --sqlite-path project_wapp.db \
  --postgres-url "$POSTGRES_DATABASE_URL"
```

#### 5.4. Subir a stack inteira
```bash
docker compose --env-file .env.production up --build -d
```

Para um runtime operacional de verdade, o `compose.yaml` sobe:
- `postgres`
- `redis`
- `migrate` (one-shot)
- `api`
- `worker`
- `admin`

Se faltar credencial obrigatoria, a API e o worker falham cedo em vez de subir em modo parcialmente configurado.

#### 5.5. Validar se os containers subiram
```bash
docker compose --env-file .env.production ps
```

#### 5.6. Rodar preflight do jeito mais seguro
O jeito mais limpo e rodar o preflight dentro do container da API, porque ele ja recebe as variaveis de `.env.production`:
```bash
docker compose --env-file .env.production exec api \
  python -m app.tools.preflight_check
```

Para exigir readiness de escala:
```bash
docker compose --env-file .env.production exec api \
  python -m app.tools.preflight_check --require-scale-ready
```

Se voce preferir rodar o preflight no host, ai sim exporte `.env.production` antes. Nesse caso o comando usa `DATABASE_URL` e `POSTGRES_DATABASE_URL` do host, nao as variaveis `COMPOSE_*`:
```bash
set -a
source .env.production
set +a
python3 -m app.tools.preflight_check --require-scale-ready
```

#### 5.7. Servicos esperados

- API em `http://127.0.0.1:8000`
- Admin em `http://127.0.0.1:8501`
- Worker recorrente de lembretes em container separado

### 6. Acessar documentação interativa
Abra no navegador:
```text
http://127.0.0.1:8000/docs
```

### 6.1. Readiness probe
Para orquestração e deploy:
```text
http://127.0.0.1:8000/health/ready
```

### 7. Rodar o painel administrativo
```bash
streamlit run app/admin_app.py
```

### 7.1. Abrir a plataforma dedicada
Com a API no ar, abra:

```text
http://127.0.0.1:8000/platform
```

A interface dedicada tem seis itens principais e um grupo "Mais":
- `Início` com primeiros passos, readiness e visão geral;
- `Configuração` com o assistente de cinco passos e o editor de serviços, preços, tom e regras do bot;
- `Testar o bot` para conversar como cliente ou operador antes de ir ao ar;
- `Atendimento` com revisões, conversas assumidas pela equipe e falhas de envio;
- `Agenda` com calendário, reservas, fila de encaixe e conexão do Google Agenda por OAuth;
- `WhatsApp` com a conexão do número por Embedded Signup da Meta e os templates aprovados;
- em "Mais": Análises, Fluxos de conversa, Automações, Mensagens e regras, Contatos, Treinamento, Equipe e Plano.

A plataforma agora autentica por sessao propria. O token administrativo deixa de ser usado como credencial permanente no navegador e passa a servir apenas para emissao de cookie HTTP-only da sessao web.

O painel Streamlit continua útil como contingência e apoio interno, mas a superfície dedicada em `/platform` passa a ser a camada principal de operação premium.

### 7.2. Abrir o Automation Studio

O editor visual é uma aplicação separada, servida pela própria API em
`/platform/automations` — mesma origem, mesma sessão, sem CORS e sem um segundo
domínio para autenticar.

```text
http://127.0.0.1:8000/platform/automations
```

Para desenvolver o editor com hot reload:

```bash
cd frontend/automation-studio
npm ci
npm run dev
```

O bundle versionado em `app/platform/automations` é gerado por `npm run build`.
A API serve o que estiver ali; se a pasta estiver vazia, a rota responde uma
página de instrução em vez de quebrar.

### 7.3. Rodar os workers

Os workers são **processos separados da API**, e não threads dentro dela. Uma
requisição HTTP pode ser interrompida a qualquer momento; trabalho durável não
pode depender disso. Em serverless a API sequer continua viva entre requisições.

```bash
python -m app.tools.reminder_worker      # lembretes de agenda
python -m app.tools.automation_worker    # automação + gatilho de agenda
```

São dois processos porque falham por motivos diferentes e escalam de forma
diferente: uma tempestade de automação não pode atrasar o lembrete de um
agendamento. O `compose.yaml` sobe os dois como serviços distintos.

Um motor de automação ligado **sem** o worker no ar aceita gatilhos, enfileira
execuções e não processa nenhuma. Hoje isso não aparece no readiness — está na
lista de próximos passos.

### 7.4. Aplicar migrations

Migrations são um **passo explícito de release**, nunca algo que acontece no
start da aplicação:

```bash
python -m app.tools.run_migrations
```

Rodar migration no cold start significa que N instâncias competem pelo mesmo
schema ao mesmo tempo, e que um deploy da Vercel altera o banco sem ninguém ter
decidido isso. Detalhes em [`docs/production_runbook.md`](docs/production_runbook.md).

### 8. Migrar dados do SQLite atual para Postgres
Se você já tiver dados em `project_wapp.db`, pode migrar assim:
```bash
python3 -m app.tools.migrate_sqlite_to_postgres \
  --sqlite-path project_wapp.db \
  --postgres-url postgresql://project_wapp:project_wapp@127.0.0.1:5432/project_wapp
```

### 9. Rodar preflight antes de publicar
```bash
python3 -m app.tools.preflight_check
```

Se o comando retornar `blocked`, ainda há item crítico a resolver antes do deploy.

### 9.1. Backup lógico antes do go-live
```bash
python3 -m app.tools.export_runtime_backup
```

### 9.2. Limpeza operacional segura
Dry-run:
```bash
python3 -m app.tools.cleanup_operational_data --stale-after-days 30
```

Execução real com backup automático:
```bash
python3 -m app.tools.cleanup_operational_data --execute --stale-after-days 30
```

O runbook completo de produção, backup, limpeza e incidente está em [docs/production_runbook.md](/Users/lucasteixeira/Documents/project_wapp/docs/production_runbook.md).

### 9.3. Ativacao do webhook do Meta
O endpoint de verificacao e recepcao e:

- `GET /webhooks/whatsapp` para handshake
- `POST /webhooks/whatsapp` para mensagens

Passos recomendados:

1. Definir `WHATSAPP_WEBHOOK_VERIFY_TOKEN` e `WHATSAPP_APP_SECRET` no `.env.production`.
2. Publicar a API em uma URL HTTPS publica.
3. No painel/app do Meta, configurar a callback URL apontando para `https://SEU-DOMINIO/webhooks/whatsapp`.
4. Informar no Meta o mesmo valor usado em `WHATSAPP_WEBHOOK_VERIFY_TOKEN`.
5. Assinar ao menos o campo de mensagens do WhatsApp.
6. Validar o `GET /webhooks/whatsapp` no handshake.
7. Confirmar que o Meta envia `X-Hub-Signature-256` e que o app secret configurado e o mesmo do app no Meta.
8. Enviar uma mensagem real de teste e confirmar que o `POST /webhooks/whatsapp` cria e despacha o auto-reply outbound.

O webhook agora aceita tanto payload normalizado interno quanto payload bruto de mensagem de texto do Meta. Eventos sem mensagem de texto, como status de entrega, sao reconhecidos e ignorados com `200`.

## Documentação complementar

| Documento | Quando ler |
| --- | --- |
| [`docs/estado_do_projeto.md`](docs/estado_do_projeto.md) | **primeiro**: o que existe, o que está provado, o que falta |
| [`docs/codebase_map.md`](docs/codebase_map.md) | em qual arquivo mexer para mudar um comportamento |
| [`docs/automation_studio.md`](docs/automation_studio.md) | motor de automação, eventos, idempotência, catálogo de nós |
| [`docs/rag_e_finetuning.md`](docs/rag_e_finetuning.md) | conhecimento, recuperação, PII e ciclo de modelo |
| [`docs/production_runbook.md`](docs/production_runbook.md) | go-live, migrations, workers, backup, incidente |
| [`docs/architecture.md`](docs/architecture.md) | visão de arquitetura de origem |
| [`docs/api_spec.md`](docs/api_spec.md) | contrato das rotas |
| [`docs/platform_frontend_guide.md`](docs/platform_frontend_guide.md) | plataforma web em `/platform` |
| [`docs/migration_compatibility_map.md`](docs/migration_compatibility_map.md) | como as migrations do branch foram reconciliadas |
| [`docs/knowledge_base.md`](docs/knowledge_base.md) | base de conhecimento por empresa |
| `.env.example` | configuração compartilhável |

## Observabilidade
- logs estruturados em JSON por padrão
- correlação por `X-Request-ID` nas respostas HTTP
- resumo operacional em `GET /admin/observability/summary`
- métricas exportáveis em `GET /admin/observability/metrics`
- configuração via `LOG_LEVEL` e `LOG_JSON`

## Hardening Operacional
- autenticação administrativa configurável por `ENABLE_ADMIN_AUTH` e `ADMIN_API_TOKEN`
- trilha de auditoria persistida para ações administrativas críticas
- suporte a identificação de operador via `X-Admin-Actor`

## Maturidade Operacional
- RBAC administrativo com papéis `viewer`, `operator` e `superadmin`
- migrações formais versionadas em `schema_migrations`
- limpeza de auditoria por retenção configurável via `AUDIT_RETENTION_DAYS`
- inspeção de migrações em `GET /admin/system/migrations`

## Dados, regras e fine-tuning
- respostas usam `company_context`, regras de negocio, knowledge base incremental, memoria do contato, estagio de pipeline e exemplos aprovados de estilo
- o dataset de fine-tuning atual nasce de `message_examples` aprovados, nao de conversas brutas automaticamente
- `knowledge_entries` alimentam o prompt de runtime, mas nao entram automaticamente no dataset de fine-tuning
- historico bruto de conversas deve passar por curadoria antes de virar `message_examples`
- o exportador filtra exemplos por qualidade minima, remove duplicados e barra alguns marcadores sensiveis simples como `cpf`, `cnpj`, `senha`, `token`, `boleto` e `fatura`
- a redacao atual de sensiveis e basica; para operacao com dados reais, o ideal e adicionar uma etapa dedicada de sanitizacao/anonimizacao antes de usar conversas historicas em treino

### Como receber dados para treino
Se voce fornecer uma base historica, o caminho correto e:

1. importar as conversas para um staging separado;
2. segmentar pares ou trechos uteis de atendimento;
3. remover ou anonimizar dados sensiveis;
4. classificar por empresa, perfil e tipo de cenario;
5. promover apenas exemplos aprovados para `message_examples`;
6. revisar o report em `GET /admin/training-dataset/review`;
7. so depois exportar `train/validation` e iniciar o fine-tuning.

O projeto agora possui uma camada inicial de importacao em `POST /admin/training-import`, com suporte a CSV ou JSON para:
- atualizar contatos com `customer_profile`;
- criar `message_examples` a partir de pares curados de pergunta/resposta;
- rodar em `dry_run` para revisar o preview antes de persistir;
- promover automaticamente exemplos elegiveis para treino quando fizer sentido.

Esse importador ja resolve a entrada inicial por CRM/planilha/export simples, mas ainda nao substitui um ETL dedicado para bases complexas, anexos, multi-turno longo ou exports legados muito sujos.

## Escalabilidade e Onboarding
- perfil de implantação em `GET /admin/system/profile`
- readiness operacional em `GET /admin/system/readiness`
- onboarding por empresa ou escopo global em `GET /admin/system/onboarding`
- suporte real a Postgres via `DATABASE_URL`
- stack local com `compose.yaml` e imagem oficial do Postgres
- painel Streamlit com visão de readiness, auditoria, migrações, contatos, knowledge base e message examples

## Rotas administrativas importantes
### Dataset e fine-tuning
- `GET /admin/training-dataset/json`
- `GET /admin/training-dataset/jsonl`
- `GET /admin/training-dataset/split`
- `GET /admin/training-dataset/review`
- `GET /admin/finetuning-dataset/train-jsonl`
- `GET /admin/finetuning-dataset/validation-jsonl`
- `GET /admin/finetuning-dataset/bundle`
- `POST /admin/finetuning/jobs`
- `GET /admin/finetuning/jobs`
- `GET /admin/finetuning/jobs/{job_id}`
- `GET /admin/finetuning/jobs-db`
- `GET /admin/finetuning/jobs-db/{job_id}`

### Outbound
- `GET /admin/outbound-messages`
- `GET /admin/outbound-messages/{outbound_message_id}`
- `POST /admin/outbound-messages/{outbound_message_id}/mark-sent`
- `POST /admin/outbound-messages/{outbound_message_id}/dispatch`
- `POST /admin/outbound-messages/dispatch-pending`

### Pending questions
- leitura e resposta das dúvidas abertas para o responsável

### Sistema e operação
- `GET /admin/system/profile`
- `GET /admin/system/readiness`
- `GET /admin/system/onboarding`
- `GET /admin/system/migrations`
- `GET /admin/knowledge-entries`
- `GET /admin/message-examples`
- `GET /admin/contacts`
- `PATCH /admin/contacts/{contact_id}`
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
- `POST /admin/training-import`

## Testes

A suíte roda sem nenhum serviço externo: o padrão é um SQLite descartável.

```bash
pytest
```

O mesmo conjunto roda contra PostgreSQL e Redis reais trocando duas variáveis.
Vale a pena antes de qualquer release: parte das garantias de concorrência e
integridade só existe no PostgreSQL.

```bash
TEST_DATABASE_URL=postgresql://user@host:5432/db \
TEST_REDIS_URL=redis://127.0.0.1:6379/9 \
pytest
```

Resultado verificado em 2026-09-05, no commit de merge para `main`:

| Configuração | Resultado |
| --- | --- |
| SQLite (padrão) | 1286 passaram, 17 pulados |
| PostgreSQL + Redis | 1302 passaram, 1 pulado |
| `ruff check .` | limpo |
| `mypy app` | 140 arquivos, sem erros |

Os testes pulados são condicionais, não falhas silenciadas: exigem PostgreSQL,
Redis ou um bundle compilado do Studio. Rodar na configuração completa reduz os
17 para 1 — o único restante é uma pragma específica de SQLite, que não existe
no PostgreSQL.

Frontend do Automation Studio:

```bash
cd frontend/automation-studio
npm ci
npm test          # 42 testes
npm run typecheck
npm run build     # gera app/platform/automations
```

### Escrevendo testes aqui

- `company_factory` cria empresas isoladas com slug único; não reuse ids fixos.
- `admin_principal_factory` devolve sessão e token CSRF do modelo canônico
  (usuário + membership), não um token por principal.
- Um teste que consome rate limit deve chamar `clear_rate_limit_counters()`;
  `reset_rate_limit_state()` só descarta o estado local e existe para simular
  uma réplica nova.

## Estado atual do produto

O detalhamento com evidência está em [`docs/estado_do_projeto.md`](docs/estado_do_projeto.md).
Resumo honesto:

**Provado localmente, com teste automatizado**
- atendimento conversacional, handoff e resposta do responsável
- identidade SaaS canônica, papéis por empresa e escopo deny-by-default por rota
- isolamento de tenant, agora também garantido pelo próprio banco
- automação visual ponta a ponta: publicar, ativar, disparar, aprovar, retomar
- recuperação sobre a base de conhecimento, com citação e proteção de injeção
- detecção e pseudonimização de PII
- curadoria de dataset, avaliação, portão de promoção e registro de modelo
- billing com quotas, entitlements por plano e reconciliação por webhook
- jobs idempotentes com claim, retry e dead-letter
- suíte completa contra PostgreSQL e Redis reais

**Completo em código, sem verificação externa**
Nenhuma credencial real foi exercitada. Estes caminhos têm teste com mock, e
nada além disso:
- Stripe (checkout, portal, webhook)
- Meta WhatsApp (envio e webhook de entrada)
- OpenAI (resposta, embeddings, fine-tuning, avaliação com modelo real)
- Google Calendar e CalDAV
- deploy na Vercel
- build da imagem Docker (o daemon não estava disponível no ambiente de trabalho)

Tratar qualquer um desses como funcionando em produção antes do primeiro teste
com credencial é otimismo, não evidência.

## Próximos passos naturais

Em ordem de dependência, do que destrava mais:

1. subir staging com PostgreSQL e Redis gerenciados, e o worker durável separado
2. rodar o sandbox do Stripe e registrar os seis Price IDs
3. construir a imagem Docker num ambiente com daemon e publicar no registry
4. ligar o provider real de WhatsApp e validar o webhook de entrada
5. concluir um job de fine-tuning real e promover o modelo pelo portão
6. conectar Google Calendar ou CalDAV com credencial real
7. cobrir com readiness o worker de automação e a chave de PII
   (hoje a ausência só aparece quando o worker recusa subir)
8. decidir a política de criação de empresa em `/admin/onboarding/setup`
   (hoje `unscoped`: um superadmin de tenant pode criar empresa e tomar o slug
   de um concorrente — está registrado como risco aceito, não como descuido)
9. ensaiar backup, restore, rollback e alertas antes do piloto

## Boas práticas
- nunca versionar `.env` com segredos reais
- usar `.env.example` para configuração compartilhável
- revogar qualquer chave exposta acidentalmente
- manter testes atualizados a cada novo fluxo crítico
