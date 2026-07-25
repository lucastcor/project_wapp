---
title: Glossário
type: reference
tags: [glossário, vocabulário]
updated: 2026-04-28
---

# Glossário

Vocabulário recorrente do projeto. Ordenado alfabeticamente.

| Termo | Significado |
|-------|-------------|
| **Auto-reply** | Decisão de responder automaticamente sem handoff humano. Definida em `app/services/decision_service.py`. |
| **Booking** | Reserva persistida em `appointment_bookings`. Pode ter `external_event_id` quando sincronizada com calendário externo. |
| **Bot Studio** | Tela da plataforma web `/platform` que simula cliente/operador batendo em `/messages/incoming`. |
| **Canvas (Flow Studio)** | Desenho visual de pipeline conversacional persistido em `company_context.conversation_pipelines`. |
| **Classification** | Saída de `classify_message()` — `lead_novo`, `cliente_existente`, `suporte`, `indefinido`. |
| **Company Context** | Contrato de negócio JSON por empresa em `app/data/company_context.json` ou `app/data/company_contexts/<slug>.json`. Ver [[40-Data/05-Company Context]]. |
| **Company scope** | Resolução do tenant ativo via `company_id` (int), `company_slug` (string normalizada) ou empresa default. |
| **Customer profile** | Tag livre por contato (ex.: `new_customer`, `returning_customer`) usada por `scheduling_policy.profiles` e curadoria. |
| **Dispatch** | Ato de enviar `outbound_message` por provider. Implementado em `app/services/outbound_dispatch_service.py`. |
| **External event ID** | ID retornado pelo provider externo de calendário ao criar/atualizar evento. Permite cancelamento sincronizado. |
| **Fallback** | Resposta determinística quando OpenAI não está configurada ou falha. Implementada em `_fallback_business_reply()`. |
| **Fine-tuning** | Treino especializado de modelo OpenAI a partir de `message_examples` curados. Pipeline em `training_export_service.py` + `finetuning_service.py`. |
| **Flow Studio** | Editor visual de pipelines conversacionais. Persiste em `company_context`. |
| **Handoff** | Decisão de escalar para responsável humano quando IA não deve decidir sozinha. Cria `pending_question`. |
| **Idempotência (webhook)** | Garantia de não processar duas vezes o mesmo `provider_message_id`. Tabela `inbound_message_receipts`. |
| **Knowledge entry** | Conhecimento incremental aprendido a partir de respostas humanas. Tabela `knowledge_entries`. |
| **MOC** | Map of Content — nota índice (este vault tem várias). |
| **Multi-tenant** | Suporte a múltiplas empresas no mesmo runtime. Cada tabela operacional aceita `company_id`. |
| **Outbound message** | Mensagem persistida pendente de envio em `outbound_messages` com status `pending|sent|failed|canceled`. |
| **Pending question** | Pergunta em aberto para responsável humano gerada por handoff. Tabela `pending_questions`. |
| **Pipeline (comercial)** | Estágio do funil inferido por `pipeline_service.py` — lead, qualificando, proposta, fechado, etc. |
| **Pipeline (conversacional)** | Playbook visual desenhado no Flow Studio. |
| **Plataforma** | A SPA dedicada servida em `/platform`. Diferente do painel Streamlit. |
| **Pre-flight** | Validação operacional antes de subir a stack — `python -m app.tools.preflight_check`. |
| **Profile (scheduling)** | Perfil de agendamento por empresa que segmenta janelas/perfis de cliente/serviço. |
| **Provider** | Camada que abstrai integração externa (OpenAI, WhatsApp Cloud API, Google Calendar, CalDAV, mock). |
| **Pre-flight** | CLI que valida readiness operacional antes do go-live. |
| **Pending question** | Pergunta em aberto para responsável humano resultante de handoff. |
| **Queue (appointment)** | Fila de prioridade para remanejamento quando uma vaga é liberada. Tabelas `appointment_queue_entries` + `appointment_queue_offers`. |
| **RBAC** | Role-Based Access Control com 3 papéis: viewer, operator, superadmin. |
| **Readiness** | Conjunto de checks consolidados em `system_readiness_service.py` — `pilot_ready`, `scale_ready`, `overall_status`. |
| **Reminder** | Lembrete persistido em `customer_reminders` com `audience` `customer` ou `internal`. |
| **Reply source** | Origem da resposta: `openai`, `openai_finetuned`, `template`, `owner_clarification`, `workflow`, `none`. |
| **Resolution type** | Tipo de resposta humana ao handoff: `final_answer`, `needs_customer_clarification`, `case_specific_exception`, `global_rule`. |
| **Scheduling policy** | Bloco do `company_context` com janelas, slots e perfis de agendamento. |
| **Session web** | Sessão de plataforma web emitida via cookie HTTP-only `project_wapp_platform_session`. |
| **Slot increment** | Granularidade em minutos para sugerir horários (default 30, configurável). |
| **Strict runtime validation** | Modo onde blockers de readiness impedem subida da API/worker. |
| **Tenant** | Empresa registrada em `companies`, identificada por `slug`. |
| **Workflow conversacional** | Fluxo conduzido por `conversation_workflow_service.py` que mantém estado em `conversation_flows`. |
