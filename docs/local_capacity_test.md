# Teste local de capacidade

Este roteiro mede a capacidade do caminho realmente usado pelo WhatsApp:

`HTTP ACK → fila durável → worker → classificação/LLM → regra de negócio → outbound mock`.

Ele usa contatos, textos e IDs sintéticos. O runner se recusa a iniciar se
`OUTBOUND_PROVIDER` não for `mock`. A
agenda também deve permanecer em `mock`. O resultado mede a máquina e a
configuração testadas; não é uma promessa de capacidade de produção.

## O que será medido

- ACK do webhook: p50, p95, p99, máximo e requisições por segundo.
- Fila completa: tempo entre `created_at` e `updated_at` de cada job.
- Vazão: jobs concluídos por minuto.
- Integridade: jobs ausentes, parados ou enviados para revisão.
- Concorrência real por conversa: mensagens do mesmo contato mantêm ordem;
  contatos diferentes entram em paralelo.

Metas iniciais do piloto: ACK p95 abaixo de 1 segundo e processamento comum
p95 abaixo de 10 segundos, sem job ausente, preso ou em revisão. Se o modelo
local não sustentar 10 segundos, registre o valor obtido e limite o volume do
piloto; não esconda a fila aumentando o timeout.

## Preparação segura

Na configuração usada tanto pela aplicação quanto pelo runner:

```dotenv
ENVIRONMENT=development
ENABLE_NORMALIZED_WHATSAPP_INGRESS=true
INTERNAL_INGRESS_SECRET=<segredo-local-nao-placeholder>
CLASSIFICATION_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3.5:9b-q4_K_M
OLLAMA_MAX_CONCURRENCY=1
INBOUND_WORKER_ENABLED=true
OUTBOUND_PROVIDER=mock
CALENDAR_PROVIDER=mock
```

Não use token, número ou contato real. Confirme que o Ollama está ativo e que
o modelo aparece em `ollama list`. A empresa escolhida precisa estar ativa e
ter uma versão de contexto publicada; caso contrário o teste mede fallback de
desenvolvimento, não o comportamento configurado para o cliente.

## Teste rápido com a instalação local

No primeiro terminal:

```bash
.venv/bin/python -m app.tools.local_dev --port 8010
```

Confirme `http://127.0.0.1:8010/health/ready`. Descubra o ID da empresa local:

```bash
sqlite3 runtime/local.db 'SELECT id, slug, active FROM companies ORDER BY id;'
```

O runner precisa carregar o mesmo `DATABASE_URL` e o mesmo
`INTERNAL_INGRESS_SECRET` da aplicação. No segundo terminal, aqueça o modelo:

```bash
.venv/bin/python -m app.tools.capacity_test \
  --company-id 1 \
  --base-url http://127.0.0.1:8010 \
  --scenario common \
  --conversations 1 \
  --messages-per-conversation 2 \
  --drain-timeout-seconds 180 \
  --output outputs/capacity/warmup.json
```

Troque `1` pelo ID real. O aquecimento não entra na comparação.

## Matriz recomendada

Execute três rodadas de cada nível. Comece uma nova rodada somente depois de a
anterior drenar por completo.

### 1. Baseline sem LLM

Valida API, persistência, fila, worker e outbound mock. Saudação pura usa a
política publicada e evita a inferência:

```bash
.venv/bin/python -m app.tools.capacity_test \
  --company-id 1 --scenario greeting \
  --conversations 5 --messages-per-conversation 10 \
  --output outputs/capacity/greeting-5x10.json
```

### 2. Uma conversa usando o modelo

```bash
.venv/bin/python -m app.tools.capacity_test \
  --company-id 1 --scenario common \
  --conversations 1 --messages-per-conversation 5 \
  --drain-timeout-seconds 300 \
  --output outputs/capacity/common-1x5.json
```

### 3. Três conversas concorrentes

```bash
.venv/bin/python -m app.tools.capacity_test \
  --company-id 1 --scenario common \
  --conversations 3 --messages-per-conversation 5 \
  --drain-timeout-seconds 600 \
  --output outputs/capacity/common-3x5.json
```

### 4. Cinco conversas concorrentes

```bash
.venv/bin/python -m app.tools.capacity_test \
  --company-id 1 --scenario common \
  --conversations 5 --messages-per-conversation 5 \
  --drain-timeout-seconds 900 \
  --output outputs/capacity/common-5x5.json
```

`mixed` alterna saudação e perguntas comuns. `--think-time-ms 2000` adiciona
dois segundos entre mensagens de um mesmo contato e aproxima melhor uma troca
humana. Uma rajada sem think time mede o limite; ela não representa tráfego
normal de uma pequena empresa.

O comando retorna código 1 quando não cumpre as metas. O JSON continua sendo
gravado e contém `passed`, percentis, vazão, estados finais e falhas sanitizadas.

## Teste representativo de produção local

SQLite serve para validar o fluxo, mas a decisão de capacidade deve usar
PostgreSQL, Redis, a imagem Python 3.13 e o mesmo modelo pretendido para o
piloto. Use uma stack e um banco exclusivos; não aponte o teste para dados de
cliente.

Crie `.env.capacity.local` (o padrão `.gitignore` não o versiona) a partir de
`.env.production.example` e ajuste pelo menos:

```dotenv
ENVIRONMENT=development
STRICT_RUNTIME_VALIDATION=false
APP_PORT=8010
POSTGRES_DB=project_wapp_capacity
POSTGRES_USER=project_wapp
POSTGRES_PASSWORD=<senha-local>
POSTGRES_PORT=55432
DATABASE_URL=postgresql://project_wapp:<senha-local>@127.0.0.1:55432/project_wapp_capacity
POSTGRES_DATABASE_URL=postgresql://project_wapp:<senha-local>@127.0.0.1:55432/project_wapp_capacity
COMPOSE_DATABASE_URL=postgresql://project_wapp:<senha-local>@postgres:5432/project_wapp_capacity
COMPOSE_POSTGRES_DATABASE_URL=postgresql://project_wapp:<senha-local>@postgres:5432/project_wapp_capacity
COMPOSE_REDIS_URL=redis://redis:6379/15
REQUIRE_SHARED_STATE=true
ENABLE_NORMALIZED_WHATSAPP_INGRESS=true
INTERNAL_INGRESS_SECRET=<segredo-local-nao-placeholder>
CLASSIFICATION_PROVIDER=ollama
COMPOSE_OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3.5:9b-q4_K_M
OLLAMA_MAX_CONCURRENCY=1
INBOUND_WORKER_ENABLED=true
REMINDER_WORKER_ENABLED=false
AUTOMATION_ENGINE_ENABLED=false
AUTOMATION_WORKER_ENABLED=false
OUTBOUND_PROVIDER=mock
CALENDAR_PROVIDER=mock
ENABLE_BILLING_ENFORCEMENT=false
```

Com nenhuma outra stack deste projeto ativa, suba primeiro somente banco/cache
e aplique as migrations:

```bash
docker compose --env-file .env.capacity.local up -d postgres redis
docker compose --env-file .env.capacity.local run --build --rm migrate
```

Para usar uma empresa sintética já configurada no SQLite local, copie-a para o
banco descartável antes da carga. O migrador limpa o destino, mas não altera a
origem:

```bash
set -a
source .env.capacity.local
set +a
.venv/bin/python -m app.tools.migrate_sqlite_to_postgres \
  --sqlite-path runtime/local.db \
  --postgres-url "$DATABASE_URL"
docker compose --env-file .env.capacity.local up --build -d api inbound-worker
```

Carregue o mesmo arquivo no terminal do runner e repita a matriz:

```bash
set -a
source .env.capacity.local
set +a
.venv/bin/python -m app.tools.capacity_test \
  --company-id 1 --scenario common \
  --conversations 5 --messages-per-conversation 5 \
  --base-url http://127.0.0.1:8010 \
  --drain-timeout-seconds 900 \
  --output outputs/capacity/postgres-common-5x5.json
```

## Como decidir a capacidade

A capacidade local aprovada é o maior nível que, em três rodadas consecutivas:

1. mantém ACK p95 abaixo de 1 segundo;
2. mantém o p95 completo dentro da meta definida;
3. conclui todos os jobs, sem `review`, ausentes ou parados;
4. volta a backlog zero após a rajada;
5. não causa timeout do modelo, perda de heartbeat ou crescimento contínuo de
   CPU/memória.

Registre mediana e pior rodada, não apenas a melhor. Observe `ollama ps`, uso de
CPU/RAM e logs do `inbound-worker`. Se 3 ou 5 conversas falharem, mantenha um
worker e `OLLAMA_MAX_CONCURRENCY=1`, reduza o limite comercial do piloto e
meça antes de aumentar réplicas: vários processos ainda disputam o mesmo
modelo.

Este teste não valida qualidade das respostas, Meta ou Google. Depois da carga,
faça separadamente uma conversa manual completa de agendamento, remarcação e
cancelamento pelo [roteiro funcional local](local_functional_test.md). Integrações
reais pertencem ao smoke G3 com número e agenda de teste autorizados.
