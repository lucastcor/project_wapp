## Especificacao de API

Esta documentacao descreve os endpoints atualmente expostos pelo backend.

Base local padrao:

- `http://127.0.0.1:8000`

Formato padrao:

- requests e responses em JSON;
- codificacao UTF-8;
- rotas publicas sem autenticacao nesta fase;
- rotas administrativas sob prefixo `/admin`.

## Endpoints publicos

### `GET /platform`

Uso:

- abrir a interface dedicada do produto, servida pelo proprio FastAPI.

Observacao:

- a shell SPA carrega assets em `/platform/assets/...` e consome os endpoints publicos e administrativos existentes.

### `POST /platform/auth/session`

Uso:

- trocar credencial administrativa por sessao web da plataforma;
- definir cookie HTTP-only para a interface dedicada.

Request:

```json
{
  "admin_token": "viewer-ou-operator-ou-superadmin-token",
  "admin_actor": "platform-admin"
}
```

Response:

```json
{
  "authenticated": true,
  "role": "operator",
  "actor": "platform-admin",
  "auth_mode": "session",
  "expires_at": 1770000000
}
```

Observacoes:

- quando `ENABLE_ADMIN_AUTH=false`, o endpoint permite sessao local de desenvolvimento sem token;
- em producao, o recomendado e definir `PLATFORM_SESSION_SECRET`.

### `GET /platform/auth/session`

Uso:

- inspecionar a sessao web atual da plataforma.

### `DELETE /platform/auth/session`

Uso:

- encerrar a sessao atual da plataforma e limpar o cookie.

### `GET /health`

Uso:

- healthcheck simples da aplicacao.

Response:

```json
{
  "status": "ok"
}
```

### `GET /health/ready`

Uso:

- readiness probe simples para deploy e orquestracao.

Response:

```json
{
  "status": "ok",
  "database_ok": true
}
```

Observacao:

- o probe reflete o banco configurado em `DATABASE_URL`, seja SQLite ou Postgres.

### `POST /messages/incoming`

Uso:

- entrada generica de mensagem recebida do cliente.

Query params opcionais:

- `company_id`
- `company_slug`

Headers opcionais:

- `X-Company-Id`
- `X-Company-Slug`

Request:

```json
{
  "contact_id": "5511999999999",
  "contact_name": "Cliente Teste",
  "message": "Quero entender melhor como funciona o servico."
}
```

Response:

```json
{
  "contact_id": "5511999999999",
  "classification": "lead_novo",
  "classification_reason": "Interesse comercial claro.",
  "classification_confidence": 0.91,
  "reply": "Ola, posso te ajudar com isso.",
  "source": "openai",
  "auto_reply": true,
  "action": "auto_reply",
  "priority": "high",
  "pipeline_stage": "qualificando",
  "next_action": "Entender escopo.",
  "lead_temperature": "warm",
  "handoff_required": false,
  "pending_question_id": null,
  "question_type": null
}
```

Observacoes operacionais:

- quando `enabled_modules.scheduling=true`, a mesma entrada publica tambem pode conduzir fluxo natural de agendamento, cancelamento e reagendamento;
- quando o remetente estiver listado em `internal_operation.operator_contact_ids`, a conversa pode operar agendamento interno de cliente por nome ou telefone, com confirmacao e desambiguacao;
- os fluxos de agenda respeitam `scheduling_policy.profiles` quando a empresa segmenta janelas por perfil de cliente, tipo de atendimento ou regra operacional.

Erros esperados:

- `400` quando `message` fica vazia apos `strip()`;
- `422` para payloads invalidos no schema.

### `POST /webhooks/whatsapp`

Uso:

- entrada especifica para webhook de WhatsApp.
- aceita payload normalizado interno e payload bruto de mensagem de texto do Meta.
- quando `auto_reply=true`, a resposta tambem e materializada em `outbound_messages` e despachada pelo provider configurado.
- para payload bruto do Meta, o header `X-Hub-Signature-256` precisa conferir com `WHATSAPP_APP_SECRET`.

Query params opcionais:

- `company_id`
- `company_slug`

Headers opcionais:

- `X-Company-Id`
- `X-Company-Slug`

Request normalizado:

```json
{
  "event": "message.received",
  "from_number": "5511999999999",
  "from_name": "Cliente Teste",
  "text": "Oi, queria saber mais sobre o servico."
}
```

Response:

- para mensagem processada, retorna o contrato estrutural principal com metadados adicionais de dispatch.
- para eventos do Meta sem mensagem de texto, retorna `processed=false` com `reason=unsupported_or_non_message_event`.

Erros esperados:

- `400` quando `text` fica vazio apos `strip()`;
- `403` quando a assinatura do Meta nao confere;
- `503` quando `WHATSAPP_APP_SECRET` nao foi configurado para payload bruto do Meta;
- `422` para payloads invalidos no schema.

### `GET /webhooks/whatsapp`

Uso:

- handshake de verificacao do webhook do WhatsApp provider real.

Query params esperados:

- `hub.mode=subscribe`
- `hub.verify_token`
- `hub.challenge`

Response:

- `200 text/plain` com o valor de `hub.challenge` quando o token confere com `WHATSAPP_WEBHOOK_VERIFY_TOKEN`.

Erros esperados:

- `400` para payload de verificacao invalido;
- `403` quando o token nao confere;
- `503` quando `WHATSAPP_WEBHOOK_VERIFY_TOKEN` nao foi configurado.

## Endpoints administrativos

### Estado e observacao

- `GET /admin/state`
- `GET /admin/observability/summary`
- `GET /admin/observability/metrics`
- `GET /admin/audit-events`
- `POST /admin/audit-events/cleanup`
- `GET /admin/system/profile`
- `GET /admin/system/readiness`
- `GET /admin/system/onboarding`
- `GET /admin/calendar/providers`
- `GET /admin/calendar/profile`
- `GET /admin/calendar/timeline`
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
- `GET /admin/system/migrations`
- `GET /admin/messages`
- `GET /admin/contacts`
- `GET /admin/contacts/{contact_id}`
- `PATCH /admin/contacts/{contact_id}`
- `POST /admin/training-import`

Uso:

- acompanhamento operacional basico de contatos e mensagens processadas.

### `GET /admin/observability/summary`

Uso:

- obter um retrato consolidado da operacao para monitoramento e auditoria rapida.

Saidas principais:

- totais por entidade;
- distribuicao de pending questions por status;
- distribuicao de outbound por status;
- distribuicao de mensagens por classificacao, acao e origem de resposta;
- status das empresas.

### `GET /admin/observability/metrics`

Uso:

- exportar metricas operacionais em formato texto para scraping e integracao com monitoramento externo.

Saida:

- `text/plain; version=0.0.4`

### `GET /admin/audit-events`

Uso:

- consultar a trilha de auditoria persistida de operacoes administrativas.

Query params:

- `limit`
- `action`
- `resource_type`

### `POST /admin/audit-events/cleanup`

Uso:

- remover eventos de auditoria mais antigos que a janela de retencao configurada ou informada.

Query params opcionais:

- `older_than_days`

Permissao:

- `superadmin`

### `GET /admin/system/migrations`

Uso:

- listar migracoes aplicadas no banco com versao, nome e data de aplicacao.

### `GET /admin/system/profile`

Uso:

- retornar o perfil atual de implantacao com dados de app, banco, seguranca, AI, canais e observabilidade.

Saidas principais:

- runtime database ativo;
- alvo Postgres configurado ou nao;
- RBAC e autenticacao admin;
- providers e configuracoes criticas.

### `GET /admin/system/readiness`

Uso:

- consolidar checks de operacao para produto final, piloto e escala.

Saidas principais:

- `overall_status`
- `pilot_ready`
- `scale_ready`
- `checks`
- `blocking_items`
- `warning_items`

### `GET /admin/system/onboarding`

Uso:

- gerar checklist operacional por empresa ou no escopo global.

Query params opcionais:

- `company_id`
- `company_slug`

Saidas principais:

- escopo resolvido;
- status do contexto;
- snapshot operacional;
- readiness de fine-tuning;
- proximas acoes recomendadas.

### Agenda

### `GET /admin/calendar/providers`

Uso:

- listar providers suportados pela camada de agenda.

### `GET /admin/calendar/profile`

Uso:

- inspecionar o provider ativo, a origem da configuracao e o readiness da agenda.

Query params opcionais:

- `company_id`

### `GET /admin/calendar/integrations`

Uso:

- listar integracoes persistidas por empresa.

### `POST /admin/calendar/integrations`

Uso:

- criar uma integracao de agenda para uma empresa.

Permissao:

- `superadmin`

Request:

```json
{
  "provider": "mock",
  "external_calendar_id": null,
  "config": {},
  "active": true
}
```

### `POST /admin/calendar/availability/query`

Uso:

- consultar slots disponiveis no provider ativo, combinando restricoes externas e bookings locais.

Permissao:

- `operator`

Request:

```json
{
  "start_date": "2026-04-07",
  "end_date": "2026-04-10",
  "duration_minutes": 60,
  "limit": 12,
  "scheduling_profile_key": "novos",
  "customer_profile": "new_customer",
  "service_name": "Consultoria inicial",
  "request_context": "primeira consulta"
}
```

Observacao:

- `scheduling_profile_key`, `customer_profile`, `service_name` e `request_context` sao opcionais e ajudam o backend a escolher a janela correta quando a empresa segmenta a agenda por perfil.

### `GET /admin/calendar/bookings`

Uso:

- listar reservas persistidas da agenda.

### `GET /admin/calendar/timeline`

Uso:

- devolver a agenda em formato proprio para timeline/calendario visual da plataforma;
- incluir segmentos por dia, com layout de sobreposicao pronto para renderizacao.

Query params opcionais:

- `company_id`
- `start_date`
- `days` de `1` a `14`

### `POST /admin/calendar/bookings`

Uso:

- criar um booking no provider ativo e persistir o resultado localmente.

Permissao:

- `operator`

Request exemplo:

```json
{
  "contact_id": "5511999999999",
  "title": "Consultoria inicial | Cliente: Maria",
  "starts_at": "2026-04-08T10:00:00-03:00",
  "ends_at": "2026-04-08T11:00:00-03:00",
  "timezone": "America/Sao_Paulo",
  "description": "Primeira consulta",
  "scheduling_profile_key": "novos",
  "customer_profile": "new_customer"
}
```

### `POST /admin/calendar/bookings/{booking_id}/reschedule`

Uso:

- reagendar um booking existente;
- atualizar o evento no provider externo quando a integracao estiver ativa.

Permissao:

- `operator`

Request exemplo:

```json
{
  "starts_at": "2026-04-09T10:00:00-03:00",
  "ends_at": "2026-04-09T11:00:00-03:00",
  "timezone": "America/Sao_Paulo",
  "scheduling_profile_key": "retorno",
  "customer_profile": "returning_customer"
}
```

### `POST /admin/calendar/bookings/{booking_id}/cancel`

Uso:

- cancelar o booking local;
- cancelar o evento externo quando houver `external_event_id`;
- opcionalmente disparar a recuperacao da vaga pela fila de prioridade.

### `GET /admin/contacts`

Uso:

- listar contatos conhecidos pelo runtime;
- filtrar por `company_id`;
- pesquisar por nome ou telefone usando o parametro `q`.

### `PATCH /admin/contacts/{contact_id}`

Uso:

- atualizar metadados operacionais do contato, como `contact_name`, `customer_profile` e `notes`.

Permissao:

- `operator`

Request exemplo:

```json
{
  "contact_name": "Maria Silva",
  "customer_profile": "returning_customer",
  "notes": "Prefere horarios a tarde."
}
```

### `GET /admin/reminders`

Uso:

- listar lembretes persistidos, internos ou automáticos de booking para cliente.

### `POST /admin/reminders`

Uso:

- criar lembrete interno a partir de texto natural informado por dono ou funcionario.
- nao cria lembrete solicitado por cliente no fluxo conversacional externo.

Permissao:

- `operator`

### `POST /admin/reminders/dispatch-due`

Uso:

- transformar lembretes vencidos em outbound pendente ou enviado imediatamente.
- pode ser filtrado por audiencia (`internal` ou `customer`).
- lembretes `customer` sao derivados automaticamente de `appointment_bookings`, quando a politica da empresa estiver habilitada.
- em operacao produtiva, esse fluxo pode ser executado pelo worker recorrente em vez de disparo manual no painel.

Permissao:

- `operator`

### Empresas

- `POST /admin/companies`
- `GET /admin/companies`
- `GET /admin/companies/{company_id}`

`POST /admin/companies` request:

```json
{
  "slug": "empresa-teste",
  "name": "Empresa Teste",
  "business_type": "general",
  "active": true
}
```

### Company context

- `GET /admin/company-context`
- `PUT /admin/company-context`

Query param opcional:

- `company_slug`

`PUT /admin/company-context` request:

```json
{
  "data": {
    "company_name": "Empresa Teste"
  }
}
```

Observacao:

- o payload completo deve respeitar todas as chaves obrigatorias validadas em `app/core/company_context.py`.

### Pending questions

- `GET /admin/pending-questions`
- `GET /admin/pending-questions/{question_id}`
- `POST /admin/pending-questions/{question_id}/answer`

`POST /admin/pending-questions/{question_id}/answer` request:

```json
{
  "owner_answer": "Pode informar que ha analise caso a caso.",
  "resolution_type": "final_answer",
  "customer_followup_question": null,
  "applies_globally": true,
  "knowledge_action": "store_as_knowledge"
}
```

`resolution_type` validos:

- `final_answer`
- `needs_customer_clarification`
- `case_specific_exception`
- `global_rule`

`knowledge_action` validos:

- `none`
- `store_as_knowledge`
- `promote_to_company_context`

### Knowledge entries

- `GET /admin/knowledge-entries`
- `GET /admin/knowledge-entries/{knowledge_entry_id}`

Uso:

- consultar conhecimento incremental ativo ou historico cadastrado.

### Message examples

- `POST /admin/message-examples`
- `GET /admin/message-examples`
- `GET /admin/message-examples/{message_example_id}`

Uso:

- cadastrar respostas aprovadas para reutilizacao de estilo e para curadoria de treino.

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

Uso:

- revisar prontidao do dataset;
- exportar formatos de treino;
- criar e acompanhar jobs de fine-tuning;
- consultar historico persistido localmente.

### `POST /admin/training-import`

Uso:

- importar base historica ou export simples de CRM em CSV ou JSON;
- atualizar `customer_profile` dos contatos;
- criar `message_examples` a partir de pares curados;
- rodar em `dry_run` antes de persistir.

Permissao:

- `operator`

Request exemplo:

```json
{
  "format": "csv",
  "raw_text": "contact_id,contact_name,customer_profile,profile_name,scenario_type,customer_message,approved_reply,quality_score\n5511999999999,Maria Silva,returning_customer,retorno,reschedule,Quero remarcar,Claro,9\n",
  "dry_run": true,
  "update_contacts": true,
  "create_message_examples": true,
  "auto_promote_eligible_examples": false
}
```

Observacoes:

- o importador atual espera pares ja razoavelmente curados;
- dados sensiveis ainda devem passar por sanitizacao antes de seguirem para treino.

### Outbound

- `GET /admin/outbound-messages`
- `GET /admin/outbound-messages/{outbound_message_id}`
- `POST /admin/outbound-messages/{outbound_message_id}/mark-sent`
- `POST /admin/outbound-messages/{outbound_message_id}/dispatch`
- `POST /admin/outbound-messages/dispatch-pending`

Query param em rotas de dispatch:

- `channel` com valores validos `whatsapp`, `instagram` ou `generic`.

## Codigos de erro

- `400`: violacao de regra de negocio ou configuracao/provider invalido.
- `404`: recurso nao encontrado.
- `422`: erro de validacao do schema de entrada.
- `500`: erro inesperado nao tratado.

## Headers de observabilidade

- `X-Request-ID`: aceito na request e devolvido na response; quando ausente, o backend gera automaticamente um identificador.

## Headers de administracao

- `Authorization: Bearer <token>` ou `X-Admin-Token`: credenciais de acesso quando `ENABLE_ADMIN_AUTH=true`.
- `X-Admin-Actor`: identifica o operador para trilha de auditoria.

## RBAC administrativo

Papeis atuais:

- `viewer`: leitura de operacao, observabilidade, auditoria e migracoes;
- `operator`: acoes operacionais como responder pending questions, criar exemplos e fazer dispatch;
- `superadmin`: configuracao estrutural, company context, empresas e cleanup administrativo.

## Observacoes de evolucao

- O contrato administrativo ja suporta autenticacao, RBAC e auditoria basica.
- Para ambientes maiores, vale evoluir para identidade centralizada e controles mais granulares por recurso.
