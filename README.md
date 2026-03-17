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
- Testes automatizados para fluxos críticos

## Stack principal
- Python 3.14
- FastAPI
- Pydantic
- Uvicorn
- SQLite
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

## Banco de dados
Atualmente o projeto usa SQLite e já possui tabelas para:
- `contacts`
- `messages`
- `pending_questions`
- `knowledge_entries`
- `message_examples`
- `outbound_messages`
- `finetuning_jobs`

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

CLASSIFICATION_PROVIDER=mock

OUTBOUND_PROVIDER=mock
OUTBOUND_DEFAULT_CHANNEL=whatsapp

WHATSAPP_API_BASE_URL=
WHATSAPP_API_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
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

### 5. Acessar documentação interativa
Abra no navegador:
```text
http://127.0.0.1:8000/docs
```

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
- observabilidade mínima para operação e evolução

## Próximos passos naturais
- integrar envio real em produção com provider de WhatsApp
- adicionar provider futuro para Instagram
- concluir um job de fine-tuning com sucesso e plugar o modelo no runtime
- construir interface administrativa além do `/docs`
- ampliar observabilidade operacional e métricas

## Boas práticas
- nunca versionar `.env` com segredos reais
- usar `.env.example` para configuração compartilhável
- revogar qualquer chave exposta acidentalmente
- manter testes atualizados a cada novo fluxo crítico