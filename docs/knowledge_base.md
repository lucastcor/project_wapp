## Knowledge Base e Memoria Operacional

O Project WAPP trabalha com tres fontes complementares de contexto para melhorar respostas e reduzir decisoes arbitrarias da IA.

## 1. Company Context

Origem:

- `app/data/company_context.json`
- `app/data/company_contexts/<slug>.json`

Papel:

- funcionar como contrato principal de negocio da empresa;
- orientar tom, catalogo, regras, politicas e restricoes;
- reduzir inventividade do modelo em temas sensiveis.

Campos chave:

- `brand_tone`
- `enabled_modules`
- `business_identity`
- `contact_channels`
- `business_hours`
- `response_policy`
- `commercial_policy`
- `catalog`
- `customer_intake`
- `business_rules`
- `llm_guidelines`

Uso pratico:

- resposta comercial;
- handoff;
- reescrita customer-facing;
- fallback de templates;
- validacao operacional da empresa;
- checklist de onboarding por empresa em `GET /admin/system/onboarding`.
- regras de disponibilidade e agendamento para a camada de calendario.

## 2. Knowledge Entries

Persistencia:

- tabela `knowledge_entries`

Origem:

- resolucoes de `pending_questions` consideradas reutilizaveis.

Tipos atuais:

- `discount_policy`
- `business_rule`
- `product_info`
- `availability_rule`
- `schedule_rule`
- `rental_rule`
- `custom_case_guidance`
- `general_faq`

Quando uma resolucao pode virar conhecimento:

- quando `knowledge_action` nao e `none`;
- quando nao se trata apenas de pedir clarificacao ao cliente;
- quando a regra e global ou marcada como aplicavel globalmente.

Escopo:

- global por empresa, quando `applies_globally = true`;
- ou contextual por contato/caso.

Uso pratico:

- injetar conhecimento incremental relevante no prompt de resposta;
- reaproveitar decisoes humanas consistentes;
- diminuir repeticao de handoffs para perguntas parecidas.

## 3. Message Examples

Persistencia:

- tabela `message_examples`

Papel:

- guardar respostas aprovadas de alta qualidade;
- recuperar exemplos de estilo por perfil/cenario;
- compor dataset para treino.

Campos chave:

- `profile_name`
- `scenario_type`
- `customer_message`
- `approved_reply`
- `style_notes`
- `quality_score`
- `used_for_training`
- `active`

Uso pratico:

- guiar o tom da resposta no runtime;
- alimentar curadoria e exportacao de dataset.

## Curadoria para treinamento

Responsavel:

- `app/services/training_export_service.py`

Regras atuais de curadoria:

- score minimo de qualidade;
- exclusao de cenarios nao desejados;
- remocao de dados sensiveis simples;
- deduplicacao;
- limite por tipo de cenario;
- split treino/validacao.

Saidas disponiveis:

- JSON para revisao;
- JSONL para treino;
- bundle train/validation;
- report de prontidao para fine-tuning.

## Memoria conversacional

Persistencia:

- campos do contato em `contacts`

Campos principais:

- `conversation_summary`
- `last_classification`
- `pipeline_stage`
- `next_action`
- `lead_temperature`

Objetivo:

- preservar continuidade entre interacoes;
- evitar repeticao de perguntas;
- apoiar inferencia do estagio comercial.

## Fluxo recomendado de aprendizado operacional

1. A IA recebe a mensagem e tenta responder com base no `company_context`.
2. Quando faltar seguranca, o sistema cria `pending_question`.
3. O responsavel responde.
4. A resolucao pode gerar outbound para o cliente.
5. A resolucao relevante pode virar `knowledge_entry`.
6. Respostas aprovadas de alta qualidade podem virar `message_example`.
7. Exemplos curados passam a compor dataset para fine-tuning.

## Agenda operacional

O produto agora possui uma camada inicial de agenda integrada ao backend.

Ela usa:

- `calendar_integrations` para definir o provider ativo por empresa;
- `appointment_bookings` para persistir reservas;
- `conversation_flows` para conduzir a conversa de agendamento em mais de uma mensagem;
- `customer_reminders` para persistir lembretes internos e lembretes automáticos de booking;
- `calendar_service.py` para combinar disponibilidade do provider com conflitos locais.
- `reminder_worker.py` para processar lembretes vencidos sem acao manual.

Providers atuais:

- `mock` para validacao;
- `google_calendar` preparado para credenciais reais;
- `caldav` preparado para servidores compativeis, incluindo cenarios de iCloud/Apple Calendar.

## Governanca recomendada

- revisar `company_context` sempre que a operacao mudar;
- promover para conhecimento apenas respostas recorrentes e estaveis;
- manter exemplos de mensagem enxutos, naturais e realmente aprovados;
- evitar usar dados pessoais em exemplos de treino;
- revisar periodicamente cenarios subrepresentados antes de iniciar fine-tuning.
- usar o onboarding administrativo para verificar contexto valido, exemplos suficientes e backlog humano antes de ativar uma empresa.

## Lacunas atuais

- `promote_to_company_context` ainda nao realiza atualizacao automatica do JSON de contexto;
- nao existe fluxo de aprovacao com trilha de auditoria por usuario/actor;
- a deteccao de dados sensiveis ainda e simples e baseada em marcadores.
