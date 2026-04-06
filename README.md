# Project WAPP

Backend de atendimento conversacional com foco em operação comercial, handoff para humano, outbound multicanal e preparação para fine-tuning.

## Visão geral
O Project WAPP é um backend em FastAPI pensado para evoluir de um chatbot simples para uma plataforma de atendimento orientada a negócio. O sistema já suporta classificação de mensagens, geração de resposta com OpenAI, memória por contato, pipeline comercial, handoff para responsável humano, knowledge base incremental, outbound persistido e pipeline de fine-tuning.

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
- Geração de respostas comerciais com OpenAI
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
- Testes automatizados para fluxos críticos

## Stack principal
- Python 3.13+
- FastAPI
- Pydantic
- Uvicorn
- Postgres com psycopg
- SQLite como fallback local/testes
- Pytest
- OpenAI Python SDK

## Arquitetura do projeto
```text
app/
  api/
    admin_routes.py
    routes.py
    schemas.py
  core/
    company_context.py
    config.py
    orchestrator.py
    training_config.py
  llm/
    providers/
      openai_provider.py
  services/
    classification_service.py
    decision_service.py
    finetuning_service.py
    handoff_service.py
    knowledge_service.py
    memory_service.py
    outbound_dispatch_service.py
    pipeline_service.py
    reply_services.py
    style_service.py
    training_export_service.py
    whatsapp_provider.py
  storage/
    db.py
    repository.py
  main.py
tests/
README.md
requirements.txt
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
- consulta de disponibilidade com provider `mock`
- reservas persistidas em banco com resumo útil para a equipe
- lembretes internos criados por dono ou funcionário em fluxo operacional separado
- lembretes do cliente gerados automaticamente a partir do agendamento e das regras da empresa
- preparação para Google Calendar e CalDAV/iCloud
- worker recorrente para processar lembretes vencidos
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
- `conversation_flows`
- `customer_reminders`

O projeto também já possui:
- configuração explícita via `DATABASE_URL`;
- `POSTGRES_DATABASE_URL` para probe/validação dedicada;
- índices operacionais adicionais para crescimento multiempresa;
- inspeção administrativa de migrações, readiness e onboarding.

## Configuração por ambiente
O projeto depende de variáveis de ambiente. Não suba segredos reais para o repositório.

### Exemplo de `.env`
```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
OPENAI_FINETUNING_BASE_MODEL=gpt-4o-mini-2024-07-18
OPENAI_FINETUNING_SUFFIX=project-wapp-style
ENABLE_FINETUNING_PIPELINE=false
OPENAI_FINETUNED_MODEL=
USE_FINETUNED_MODEL_FOR_REPLIES=false

DATABASE_URL=postgresql://project_wapp:project_wapp@127.0.0.1:5432/project_wapp
POSTGRES_DATABASE_URL=postgresql://project_wapp:project_wapp@127.0.0.1:5432/project_wapp
DATABASE_POOL_SIZE=10
DATABASE_STATEMENT_TIMEOUT_MS=5000
ENABLE_ADMIN_AUTH=false
ADMIN_API_TOKEN=
ADMIN_VIEWER_TOKEN=
ADMIN_OPERATOR_TOKEN=
ADMIN_SUPERADMIN_TOKEN=
ADMIN_ACTOR=streamlit-admin
ADMIN_APP_API_BASE_URL=http://127.0.0.1:8000
AUDIT_RETENTION_DAYS=90
REMINDER_WORKER_ENABLED=true
REMINDER_WORKER_POLL_SECONDS=60
REMINDER_WORKER_DISPATCH_IMMEDIATELY=true
REMINDER_WORKER_CHANNEL=
REMINDER_WORKER_AUDIENCES=customer,internal

CLASSIFICATION_PROVIDER=mock

OUTBOUND_PROVIDER=mock
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
POSTGRES_DB=project_wapp
POSTGRES_USER=project_wapp
POSTGRES_PASSWORD=project_wapp
POSTGRES_PORT=5432
APP_PORT=8000
ADMIN_PORT=8501
```

## Como rodar localmente
### 1. Criar e ativar ambiente virtual
```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Instalar dependências
```bash
pip install -r requirements.txt
```

### 3. Configurar variáveis de ambiente
Crie um arquivo `.env` com base no exemplo acima.

### 4. Subir a aplicação
```bash
uvicorn app.main:app --reload
```

### 4.1. Subir stack com Postgres
O caminho mais profissional para ambiente local e homologação é usar a imagem oficial do Postgres via Compose:
```bash
docker compose up -d postgres
```

Para subir API + Postgres:
```bash
docker compose up --build
```

Serviços esperados:

- API em `http://127.0.0.1:8000`
- Admin em `http://127.0.0.1:8501`
- Worker recorrente de lembretes em container separado

### 5. Acessar documentação interativa
Abra no navegador:
```text
http://127.0.0.1:8000/docs
```

### 5.1. Readiness probe
Para orquestração e deploy:
```text
http://127.0.0.1:8000/health/ready
```

### 6. Rodar o painel administrativo
```bash
streamlit run app/admin_app.py
```

### 7. Migrar dados do SQLite atual para Postgres
Se você já tiver dados em `project_wapp.db`, pode migrar assim:
```bash
python3 -m app.tools.migrate_sqlite_to_postgres \
  --sqlite-path project_wapp.db \
  --postgres-url postgresql://project_wapp:project_wapp@127.0.0.1:5432/project_wapp
```

### 8. Rodar preflight antes de publicar
```bash
python3 -m app.tools.preflight_check
```

Se o comando retornar `blocked`, ainda há item crítico a resolver antes do deploy.

## Documentação complementar
- `docs/architecture.md`
- `docs/api_spec.md`
- `docs/knowledge_base.md`
- `.env.example`

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
- `GET /admin/calendar/providers`
- `GET /admin/calendar/profile`
- `GET /admin/calendar/integrations`
- `POST /admin/calendar/integrations`
- `POST /admin/calendar/availability/query`
- `GET /admin/calendar/bookings`
- `POST /admin/calendar/bookings`
- `GET /admin/reminders`
- `POST /admin/reminders`
- `POST /admin/reminders/dispatch-due`

## Testes
Para rodar a suíte completa:
```bash
pytest
```

Para rodar testes específicos:
```bash
pytest tests/test_reply_service.py
pytest tests/test_training_export_service.py
pytest tests/test_finetuning_service.py
pytest tests/test_outbound_dispatch_service.py
```

## Estado atual do produto
O backend já está em um estágio avançado de base de produto. Os pontos mais importantes já implementados são:
- fluxo de atendimento orientado a negócio
- separação entre automação e decisão humana
- outbound multicanal com provider mock e provider real de WhatsApp preparado
- pipeline de fine-tuning com persistência de histórico
- observabilidade, auditoria e readiness para operação e evolução
- onboarding/admin UX mínimo porém funcional para multiempresa
- agenda operacional com conversa natural para o cliente e lembretes consistentes para operação

## Próximos passos naturais
- integrar envio real em produção com provider de WhatsApp
- adicionar provider futuro para Instagram
- concluir um job de fine-tuning com sucesso e plugar o modelo no runtime
- conectar credenciais reais de Google Calendar ou CalDAV/iCloud
- automatizar o disparo recorrente de lembretes vencidos
- endurecer identidade centralizada e RBAC com usuário real

## Boas práticas
- nunca versionar `.env` com segredos reais
- usar `.env.example` para configuração compartilhável
- revogar qualquer chave exposta acidentalmente
- manter testes atualizados a cada novo fluxo crítico
