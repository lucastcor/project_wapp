# Production Runbook

## Objetivo
Este runbook consolida o minimo operacional para colocar o Project WAPP em producao com menos improviso: bootstrap, validacao, backup, limpeza segura e resposta inicial a incidente.

## Go-live
1. Ligar o Docker daemon ou garantir que a infraestrutura alvo esteja acessivel.
2. Preencher `.env.production` com segredos reais e valores nao-placeholder.
3. Garantir `DATABASE_URL` e `POSTGRES_DATABASE_URL` apontando para Postgres no host, e `COMPOSE_DATABASE_URL` e `COMPOSE_POSTGRES_DATABASE_URL` apontando para `postgres:5432` dentro do Docker Compose.
4. Habilitar `ENABLE_ADMIN_AUTH=true`, `ENABLE_RATE_LIMITING=true` e `REMINDER_WORKER_ENABLED=true`.
4-rl. Rate limiting de autenticação (D23): o limite estrito `RATE_LIMIT_AUTH_REQUESTS` (padrão 10/min por IP) conta só requisições que recebem credencial ou mudam sessão (login, cadastro, convite, redefinição de senha, logout). Ler a sessão e as capacidades, feito a cada carga do painel, usa o escopo `platform_auth_read` com `RATE_LIMIT_ADMIN_REQUESTS`. Com vários operadores atrás do mesmo IP (rede da clínica), dimensione `RATE_LIMIT_ADMIN_REQUESTS` pelo número de pessoas; com proxy reverso, `TRUST_PROXY_HEADERS=true` para contar o IP real de cada cliente.
4a. Definir `PII_PSEUDONYMIZATION_KEY`. Sem ela a aplicacao recusa subir em producao: um pseudonimo derivado so do valor e recuperavel por enumeracao, o que nao e pseudonimizacao.
4b. Habilitar `INBOUND_WORKER_ENABLED=true` e o serviço `inbound-worker`. Para Qwen, configurar `CLASSIFICATION_PROVIDER=ollama`, modelo instalado e URL acessível aos containers (ver [guia local](local_qwen.md)).
4c. Decidir sobre automacao. Sao dois interruptores, nao um: `AUTOMATION_ENGINE_ENABLED` habilita publicar e ativar fluxos; `AUTOMATION_WORKER_ENABLED` faz o servico `automation-worker` puxar trabalho da fila. Ambos nascem `false`.
5. Configurar `OUTBOUND_PROVIDER=whatsapp`, `WHATSAPP_API_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN` e `WHATSAPP_APP_SECRET`.
6. Subir a stack endurecida:
   ```bash
   docker compose --env-file .env.production up --build -d
   ```
7. Rodar preflight com exigencia de escala de dentro da API:
   ```bash
   docker compose --env-file .env.production exec api \
     python -m app.tools.preflight_check --require-scale-ready
   ```
8. Validar `/health/ready` e login admin. No painel, conferir `GET /admin/whatsapp/channel-status` e executar os testes explícitos de conexão: `POST /admin/whatsapp/test-connection` lê o número na Meta sem enviar mensagem; `POST /admin/calendar/test-connection` consulta disponibilidade sem criar evento. “Configurado” não substitui “conectado”.
8a. Validar recebimento real do webhook, saída para um contato autorizado e criação/remarcação/cancelamento em uma agenda de teste. As respostas dos probes são sanitizadas; consultar `audit_events` para a trilha de execução.
9. Conferir o Automation Studio em `/platform/automations`. Se responder **503** com instrucao de build, o passo de build do frontend nao rodou — a imagem Docker o executa no estagio `studio`, e a Vercel via `buildCommand`.

## Migrations sao passo de release

`RUN_MIGRATIONS_ON_STARTUP=false` em producao. O servico `migrate` do compose roda ate o fim e sai; a API e os workers so sobem depois. Cold start aplicando migration faz o deploy depender de qual instancia ganhou a corrida.

```bash
docker compose --env-file .env.production run --rm migrate
```

Conferir o estado antes de migrar:

```sql
SELECT version FROM schema_migrations ORDER BY version;
```

O release atual vai até `073_inbound_retry_schedule`. A 068 adiciona fila persistente de entrada e heartbeats; a 069, propostas/operações de agenda; a 070, recurso reservável e a guarda de banco contra agendamentos sobrepostos; a 071, janela de atendimento, opt-out, consentimento, templates e status de entrega do WhatsApp; a 072, posse humana da conversa; e a 073, espera persistente para retry da entrada.

Depois da 071, lembrete e oferta de encaixe fora da janela de 24 h so saem com template `utility` aprovado cadastrado para a empresa, finalidade e idioma (`whatsapp_templates`). Sem template, a mensagem vai para dead letter com `blocked:outside_window_no_template` — cadastrar os templates aprovados na Meta antes de ligar lembretes em producao. A migration 067 falha **de proposito** se encontrar linha orfa — uma linha apontando para empresa inexistente — nomeando a tabela. Resolver as orfas antes de repetir.

A 070 exige a extensao `btree_gist` no PostgreSQL (vem nas imagens oficiais; e "trusted" desde o PostgreSQL 13, entao basta o usuario da migration ter CREATE no banco, ou um superusuario rodar `CREATE EXTENSION btree_gist` antes). Ela tambem falha **de proposito**, antes de qualquer alteracao, se existirem agendamentos ativos sobrepostos na mesma empresa. Listar os pares no banco ainda nao migrado e cancelar/remarcar um de cada par pela API administrativa antes de repetir:

```bash
docker compose --env-file .env.production run --rm migrate python -m app.storage.migrations_booking_capacity
```

## Workers sao processos separados

| Servico | Comando | Por que separado |
| --- | --- | --- |
| `automation-worker` | `python -m app.tools.automation_worker` | num runtime serverless ele nao existiria entre requisicoes, e um fluxo em `delay.wait` nunca acordaria |
| `inbound-worker` | `python -m app.tools.inbound_worker` | confirma o webhook após persistência e executa IA/envio fora da requisição |
| `worker` (lembretes) | `python -m app.tools.reminder_worker` | mesma razao |

Os claims são atômicos. Na entrada, mensagens da mesma empresa e contato são serializadas; falhas incertas bloqueiam aquela conversa para revisão. Dimensione réplicas conforme a capacidade do modelo.

Validar sem processar nada:

```bash
docker compose exec automation-worker python -m app.tools.automation_worker --validate-only
docker compose exec automation-worker python -m app.tools.automation_worker --healthcheck
docker compose exec inbound-worker python -m app.tools.inbound_worker --healthcheck
```

`--run-once` executa trabalho; não é uma validação sem efeitos. `--healthcheck` exige heartbeat real recente, enquanto `--validate-only` verifica configuração.

Entradas em revisão aparecem em `GET /admin/inbound/jobs?company_id=ID`; siga o [procedimento de reconciliação](local_qwen.md) antes de concluir manualmente um job.

Fila parada, dead-letter e execucoes travadas aparecem em `GET /admin/automations/dead-letter` (escopado por empresa).

## Backup logico
Gera um snapshot JSON versão 4 do runtime atual, incluindo `inbound_jobs`; heartbeats são efêmeros. O comando funciona tanto em SQLite quanto em Postgres:

```bash
python3 -m app.tools.export_runtime_backup
```

Saida customizada:

```bash
python3 -m app.tools.export_runtime_backup --output backups/pre-go-live.json
```

No `compose.yaml`, a API e o worker montam o volume `backups_data` em `/app/backups`.

## Limpeza operacional segura
O fluxo recomendado antes do go-live e sempre:
1. rodar dry-run;
2. revisar candidatos;
3. gerar backup;
4. executar a limpeza.

Dry-run:

```bash
python3 -m app.tools.cleanup_operational_data --stale-after-days 30
```

Execucao com backup automatico:

```bash
python3 -m app.tools.cleanup_operational_data --execute --stale-after-days 30
```

O processo hoje aplica:
- desativacao de empresas placeholder ainda ativas;
- arquivamento de `pending_questions` antigas em aberto;
- cancelamento de `outbound_messages` antigas ainda pendentes.
- arquivamento de jobs de fine-tuning falhados e antigos no historico local.

## Migracao SQLite -> Postgres
Quando existir base em `project_wapp.db`, migrar antes do go-live:

```bash
set -a
source .env.production
set +a

python3 -m app.tools.migrate_sqlite_to_postgres \
  --sqlite-path project_wapp.db \
  --postgres-url "$POSTGRES_DATABASE_URL"
```

Se a migracao for executada de dentro do container da API, use `COMPOSE_POSTGRES_DATABASE_URL` ou deixe a URL explicita apontando para `postgres`.

## Politica minima de PII
- nao subir backups para repositorio Git;
- restringir acesso aos volumes de backup;
- tratar exportacoes e snapshots como dados sensiveis;
- evitar compartilhar `contact_id`, mensagens e contexto bruto fora da operacao;
- apagar snapshots temporarios depois da janela de suporte/incidente.

## Piloto supervisionado: cobrança, retenção e dados do cliente
Estado verificado no código em 14/09/2026 (D24). Não prometa ao cliente o que está marcado como lacuna.

Cobrança do piloto:
- Manter `ENABLE_BILLING_ENFORCEMENT=false`: cobrança manual fora do sistema. Com o enforcement desligado, falta de assinatura ou teste expirado não bloqueia mensagem nem agendamento.
- Não chamar `POST /admin/billing/trial` para empresa piloto: o teste dura 7 dias e, se o enforcement for ligado depois, o agendamento para de funcionar ao expirar.
- Se o enforcement for ligado, ative o plano por `POST /admin/billing/activate` (admin da empresa) com limite acima do volume esperado — essencial: 200 agendamentos e 3.000 mensagens por período; avançado: 800 e 10.000; plano superior: 3.000 e 30.000. A ativação manual grava um período de 30 dias sem Stripe; confira `GET /admin/billing/usage` antes do fim do período.
- Segurança não depende de cobrança: autenticação, RBAC, escopo por empresa, rate limiting e opt-out valem com qualquer estado de assinatura.

Retenção (lacuna):
- Não há expurgo automático. Mensagens, contatos, reservas, fila, lembretes e filas de entrada/saída ficam até remoção manual. O valor `training_data_retention_days` da política não é aplicado por nenhuma rotina hoje.
- `cleanup_operational_data` é higiene operacional (seção acima); não apaga dados pessoais.

Exportação e exclusão de dados de um cliente (lacuna):
- Não existe rota nem ferramenta por contato. `export_runtime_backup` exporta a base inteira e deve ser tratado como dado sensível.
- Pedido de titular durante o piloto: registrar data e canal do pedido; gerar backup lógico; a equipe técnica localiza o telefone, sempre filtrando por empresa, em `contacts`, `contact_company_profiles`, `messages`, `outbound_messages`, `inbound_jobs` (payload), `inbound_message_receipts`, `pending_questions`, `conversation_flows`, `customer_reminders`, `appointment_bookings`, `appointment_queue_entries`, `appointment_queue_offers`, `booking_operations`, `whatsapp_contact_states`, `contact_consents`, `whatsapp_delivery_events` e `conversation_ownerships`. `audit_events` guarda o telefone mascarado.
- Resolver antes de ampliar além do piloto supervisionado.

Consentimento e opt-out:
- SAIR e VOLTAR enviados pelo cliente valem sozinhos e bloqueiam avisos automáticos.
- Consentimento obtido fora do WhatsApp é registrado pelo operador na tela Canal WhatsApp, com evidência.

## Resposta inicial a incidente
1. Confirmar se o problema e de credencial, runtime, webhook ou backlog.
2. Rodar `python3 -m app.tools.preflight_check` e salvar o resultado.
3. Gerar backup logico antes de qualquer alteracao corretiva.
4. Verificar `audit_events`, `outbound_messages`, `pending_questions` e `inbound_jobs` (também `inbound_message_receipts` para eventos anteriores à migração).
5. Se houver dano operacional, congelar novas mudancas e priorizar restauracao do fluxo primario:
   - login admin;
   - recepcao de mensagem;
   - dispatch outbound;
   - worker de lembretes;
   - integracao de agenda.
6. Depois do incidente, registrar causa raiz e acao permanente.
