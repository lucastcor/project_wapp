# Contratos comuns — alvo de implementação

Este documento define comportamento desejado, não afirma que ele já existe. P01 decide nomes/schemas concretos e registra-os aqui antes de agentes implementarem consumidores. Reutilizar estruturas existentes quando equivalentes; não criar uma segunda identidade, fila ou sistema de configuração.

## Fontes de autoridade

- Identidade e autorização: usuários/memberships/sessões e `company_id` canônico existente. O modelo nunca escolhe tenant por argumento vindo do cliente.
- Contexto empresarial: versão publicada pelo serviço existente; rascunhos não afetam produção. Defaults legados têm migração explícita, sem fallback silencioso para outra empresa.
- Fatos transacionais: banco e estado de sincronização da operação. Texto da IA, resumo e grafo derivado não comprovam reserva ou cancelamento.
- Agenda externa: integração configurada com autoridade e estratégia de reconciliação documentadas. Em caso de incerteza, não inventar disponibilidade/sucesso.
- Conteúdo importado: dado não confiável, com fonte, versão e tenant. Pode gerar candidatos, nunca autorização operacional automática.

## Domínio mínimo

Empresa; Cliente/Contato; Serviço; Recurso reservável; Agendamento; Política; Consentimento; Operação; Proposta de confirmação. Unidade/profissional podem se ligar ao recurso; não exigir um sistema completo de RH/múltiplas filiais para o piloto.

Cada agenda do piloto tem capacidade um por recurso. Agendamentos ativos não se sobrepõem no mesmo `(empresa, recurso)`. Usar intervalos `[início, fim)` e UTC para persistência, timezone IANA para apresentação e regras locais. O mesmo telefone pode existir em empresas diferentes sem compartilhar memória, consentimento ou reservas. Registros legados precisam de recurso default atribuído deterministicamente e detecção prévia de conflitos.

## Política efetiva e precedência

Restrições de canal/segurança e integridade sempre se aplicam. Depois: política publicada da empresa > preset da vertical > default documentado. Texto de estilo não substitui permissão. Campo de configuração só pode ser apresentado como funcional se um consumidor executável e um teste comprovarem seu efeito.

Contrato semântico a concretizar em P01:

| Grupo | Requisitos |
| --- | --- |
| Conversa | saudação habilitada/texto, orientação para ambiguidade, tratamento fora do horário, aviso de encaminhamento |
| Autonomia | intenções autorizadas, limiar de confiança, ações com humano obrigatório, posse humana explícita |
| Agenda | serviços/duração, recurso, antecedência, horários/folgas; permissões e prazo de cancelamento/remarcação |
| Confirmação | operação, alvo, parâmetros, versão, validade, estado e evidência da aceitação |
| Canal | consentimento por finalidade, última entrada, template/idioma/categoria, opt-out e estado de entrega |

## Interpretação e confirmação

Intenções de controle: confirmar / recusar / alterar / perguntar / incerto. Negativa e pergunta não autorizam escrita. Semântica deve considerar contexto, não apenas substring ou palavra isolada. LLM pode sugerir intenção/argumentos; serviço valida. Mudança de horário/serviço/booking invalida a proposta anterior e exige confirmação da nova.

Confirmação deve estar vinculada a uma proposta ainda válida, ao mesmo contato/empresa e à mesma versão de operação/alvo. Confirmação explícita na mensagem inicial só pode executar se alvo e parâmetros forem inequívocos e a política permitir; default do piloto pede uma proposta antes de ação destrutiva. Reentrega da mesma aceitação não executa outra operação.

Saudação sem fluxo ativo recebe resposta configurada. Durante fluxo ativo, saudação não reinicia nem apaga estado. “Cancelar” durante coleta de nova reserva significa desistir daquela coleta; não cancelar automaticamente outra reserva existente. Se a intenção não for inequívoca, esclarecer.

## Persistência, efeitos e recuperação

Estado conceitual de operação: proposta → confirmada pelo cliente → execução/sincronização → concluída, recusada, falha recuperável ou revisão. Mapear para o schema final; não presumir que esses nomes já existem.

- Reservar capacidade atomicamente antes de efeitos externos; prever retenção/expiração para operações pendentes.
- Usar chave de idempotência por empresa/operação e ID externo estável quando o provider suportar.
- Evitar manter transação/trava de banco aberta durante chamada de rede.
- Persistir eventos necessários no mesmo commit do fato; consumidores idempotentes.
- Sucesso externo com commit local perdido exige reconciliação, não novo evento cego.
- Falha de negócio conhecida deve gerar explicação/próximas opções. Resultado externo incerto exige revisão. Retry só quando seguro.
- Uma falha no encaixe ou analytics não pode apagar sucesso já confirmado de cancelamento.
- Não prometer exactly-once externo se o provider não o garante. Separar aceito/enviado, entregue, lido e falhou.

## Posse humana, UI e privacidade

Ao assumir uma conversa, o humano suspende respostas automáticas conforme política; a entrada continua persistida. Retomada explícita e auditada. Operador vê pendências, motivos e ações permitidas sem SQL manual ou alteração cega de status. Resoluções exigem RBAC/escopo/CSRF e não podem reexecutar uma ação incerta sem evidência.

Segredos nunca retornam em APIs/telas/logs de configuração; mostrar presença/status mascarado. Dados reais não entram em fixtures ou relatórios. Retenção, exclusão/exportação e acesso por empresa devem ser definidos para o piloto; preservar as proteções já existentes e não assumir conformidade regulatória apenas por haver pseudonimização.

## Contratos congelados em P01 (13/09/2026)

Alteração posterior exige registro aqui antes de integrar consumidores.

### Fonte de contexto em runtime (D12)

`company_config_service.get_company_config(company_id)` é a única leitura de runtime (orquestrador, workflow, agenda, lembretes, respostas). Ordem: versão `published` em `company_context_versions` → linha legada `company_contexts` **da própria empresa** (source `legacy_context`, compatibilidade sem escrita) → seed da própria empresa fora de produção (bootstrap existente) → erro em produção / template global marcado (`global_template`) só fora de produção. Rascunho nunca é lido. Cache local validado por `(id, content_hash)` da versão publicada a cada leitura; fontes não versionadas não são cacheadas. Escritas legadas (PUT `/admin/company-context?company_slug=`, onboarding) também criam e publicam versão. Empresa não resolvida: fora de produção mantém `ensure_default_company`; em produção falha explicitamente.

### Política efetiva (`app/services/company_policy_service.py`)

Precedência: restrições de segurança/canal fixas > `policies` publicadas da empresa > preset da vertical (`vertical_packs`) > `POLICY_DEFAULTS`. Seções dict são mescladas por chave (override parcial não apaga defaults). `resolve_effective_policy(config) -> EffectivePolicy`; `validate_policies(policies)` recusa chave desconhecida, tipo ou faixa inválidos com mensagem útil em criação de versão.

| Campo | Origem | Default | Consumidor executável |
| --- | --- | --- | --- |
| `autonomy.confidence_threshold` | `policies.handoff_confidence_threshold` | 0,6 (clínica 0,75) | `decide_message_action`, `should_create_pending_question` |
| `autonomy.handoff_on_low_confidence` | `policies.handoff_on_low_confidence` | true | handoff: false → esclarecimento sem pendência |
| `autonomy.auto_reply_intents` | `context.response_policy.auto_reply_allowed_for` | lead_novo, cliente_existente, suporte | decisão |
| `autonomy.handoff_intents` | `context.response_policy.handoff_required_for` | financeiro, fornecedor | decisão |
| `conversation.greeting_enabled/greeting_message` | `policies.conversation` | true / texto com nome da empresa | orquestrador: saudação pura sem fluxo ativo |
| `conversation.clarification_message` | `policies.conversation` | texto padrão | baixa confiança sem handoff |
| `conversation.handoff_notice_enabled/handoff_notice_message` | `policies.conversation` | true / texto padrão | orquestrador quando ação é handoff/revisão |
| `conversation.unsupported_content_message` | `policies.conversation` | texto padrão ("apenas mensagens de texto") | orquestrador para `content_type=unsupported` (W07), sem modelo, sem tocar fluxo, 1 vez/30 min por contato |
| `conversation.after_hours_mode` (`continue`/`notice_only`) + `after_hours_message` | `policies.conversation`; horários de `context.business_hours` | `continue` | orquestrador; `notice_only` não aciona bot fora do horário, avisa uma vez por contato a cada 12 h e cria uma pendência |
| `booking_rules.cancellation/reschedule` = `{allowed, min_notice_minutes, outside_window}` (`handoff`/`deny`) | `policies.booking_rules` | permitido, 0 min, `handoff` | `evaluate_booking_change()`; ligação em agenda/API/bot é aceite de P03/P04 (A08) |
| `confirmation.proposal_ttl_minutes` | `policies.confirmation` | 30 | P02 ao criar proposta |

Regras de leitura (revisão P01): a linha legada só vale se `company_id` for da própria empresa (ou sem vínculo e slug idêntico à chave) — slugs que colidem na normalização nunca emprestam contexto. `business_hours` por dia aceita `HH:MM-HH:MM` (várias janelas) ou `fechado`; janela que cruza a meia-noite é dividida entre dias; valor ilegível é recusado na escrita e, em versão antiga, conta como aberto com log (nunca derruba nem silencia). Saudação exige abertura ("oi", "bom dia"...); "beleza"/"tudo certo" sozinhos não são saudação. Aviso de encaminhamento não se repete ao mesmo contato em 30 min; aviso de fora do horário, em 12 h. Readiness alerta empresa cuja linha legada foi editada depois da versão publicada.

Cancelamento é livre quando `allowed` e o início está a ≥ `min_notice_minutes`; booking já iniciado, regra desligada ou prazo vencido → `requires_human` (`outside_window=handoff`) ou `denied`. Multa/aprovação não são suportadas: modelar como `handoff`. Confirmação na mensagem inicial não executa ação destrutiva no piloto (fixo, não configurável).

### Operações e propostas (`booking_operations`, migration 069)

Uma linha por intenção transacional de agenda (`create`/`cancel`/`reschedule`), usada por conversa (P02), execução/sincronização (P03/P04) e revisão (P06). Estados: `proposed → confirmed → executing → completed`; `proposed → rejected | expired | superseded`; `executing → failed | review`; `review → completed | failed` (resolução auditada, P06). Transições são compare-and-set no banco.

- Vínculo: `company_id` + `contact_id` + `params_hash` (sha256 canônico de kind, booking alvo, recurso, parâmetros e `target_version`). No máximo uma proposta aberta por contato (índice único parcial); nova proposta substitui a anterior na mesma transação.
- Aceite: `accept_proposal` só confirma proposta aberta, não expirada, do mesmo contato/empresa e hash; retorna `accepted | replay | expired | superseded | mismatch | not_found | not_open`. Reentrega com a mesma `confirmation_ref` é `replay` e não executa de novo. `confirmation_ref` guarda ID do provider ou hash, nunca texto.
- Idempotência: `UNIQUE(company_id, idempotency_key)`; mesma chave com hash diferente é conflito.
- Parâmetros de horário em UTC ISO-8601 + timezone IANA para apresentação.
- Implementação (`app/storage/operation_repository.py`): FK composta `(booking_id, company_id) → appointment_bookings(id, company_id)` (índice da migration 035); `cancel`/`reschedule` exigem `booking_id`. Timestamps UTC sempre com microssegundos (ordem textual = ordem temporal); `now` sem timezone é recusado. Serialização: `BEGIN IMMEDIATE` no SQLite em `create_proposal`/`accept_proposal`; advisory lock por empresa+contato no PostgreSQL em `create_proposal` (o aceite depende do compare-and-set por linha). `confirmation_ref` é única por empresa (índice parcial): a mesma confirmação nunca confirma uma segunda operação e é reportada como `replay` da original. Mesma chave de idempotência com hash igual mas contato diferente é conflito. `error_code` restrito a `[A-Za-z0-9_.:-]{1,80}`. Linhas retornadas trazem `params`/`result` desserializados.

### Canal WhatsApp — alvo da migration 071 (P05)

Congelado para implementação após P03 liberar `db.py`/backup. Aplica-se só a envio pelo canal `whatsapp`; mock e demais canais não mudam.

- Finalidade do envio (derivada de `outbound_messages.source` no momento do dispatch por `whatsapp_delivery_policy.purpose_for_source`; sem coluna própria): `reply` (`whatsapp_auto_reply`, `appointment_queue_waiting_ack`, `appointment_queue_keep_waiting`); `service_followup` (`owner_resolution`, `manual_booking_confirmation`); `reminders` (`reminder_customer`); `internal_notice` (`reminder_internal`, contato da equipe); `queue_offers` (`appointment_queue_offer`, `appointment_queue_waiting_followup`); `automation` (`automation:execution:*`). Origem desconhecida = `service_followup` (proativa, conservadora).
- Estado por contato: última entrada do cliente (atualizada ao persistir mensagem recebida, não ao processar), opt-out com data/origem. Consentimento por finalidade (`granted`/`revoked`) separado; última mensagem nunca vira consentimento geral.
- Decisão no dispatch efetivo (não só ao enfileirar), nesta ordem: (1) opt-out bloqueia toda finalidade exceto `reply` e `internal_notice`; (2) consentimento `revoked` da finalidade bloqueia; `marketing` exigiria `granted` e não é usado no piloto; (3) dentro de 24 h da última entrada → texto livre; fora → template da finalidade aprovado, ativo, categoria `utility`, idioma da empresa, com parâmetros validados em quantidade; sem template → não envia. Bloqueio vira dead letter com código (`opted_out`, `consent_revoked`, `outside_window_no_template`) visível ao operador e no readiness.
- Palavras de opt-out exatas ("SAIR", "PARAR", "STOP", "CANCELAR MENSAGENS") registram opt-out e respondem uma confirmação; não passam pelo classificador.
- Templates por empresa/finalidade/idioma com status (`approved`/`pending`/`rejected`/`paused`/`disabled`) e quantidade de parâmetros; nunca reclassificar publicidade como `utility`.
- Implementado em P07 (14/09): `GET /admin/whatsapp/templates` (viewer, com o idioma da política) e `PUT /admin/whatsapp/templates` (admin da empresa; registra template já aprovado na Meta, não submete); `GET /admin/whatsapp/contacts/{contato}/channel` (viewer) e `PUT /admin/whatsapp/contacts/{contato}/consents/{finalidade}` (operador, origem `operator_panel`, evidência opcional, auditoria com telefone mascarado). Parâmetros preenchidos hoje: lembrete 1 (texto), oferta de vaga 2 (serviço, data/hora). Template ausente para lembrete pendente ou oferta aberta aparece como alerta `whatsapp_template_missing` no resumo operacional da empresa (o readiness é global de plataforma).
- Callbacks de status: somente em payload assinado; empresa pelo `phone_number_id` do binding; correlação `(company_id, provider_message_id)`; evento persistido uma vez por `(empresa, provider id, status)`; estado de entrega monotônico `sent < delivered < read`; `failed` só substitui `sent`/vazio; evento sem mensagem conhecida é registrado como órfão sem alterar outra empresa. Erros do callback guardam código, não `error_data`.
- Envio `uncertain` não é repetido; revisão explícita chega em P06.

### Conexões por empresa — migration 074 (P08, 25/09/2026)

Cada empresa conecta o próprio número e a própria agenda pelo painel; segredos de terceiros nunca ficam em claro no banco.

- Meta: um app por instalação (`META_APP_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`); por empresa, no binding (`whatsapp_channel_bindings`): `waba_id`, `business_id`, `verified_name`, `quality_rating`, `platform_type`, `access_secret_encrypted`, `registration_pin_encrypted`, `credential_source` (`embedded_signup`, `embedded_signup_coexistence`, `manual`), `connected_at/by`, `last_verified_at`, `needs_reconnection`. O token global `WHATSAPP_API_TOKEN` continua como fallback quando o binding não tem token próprio. Webhook inalterado: assinatura com app secret global, empresa pelo `phone_number_id`.
- Fluxo: `GET /admin/whatsapp/embedded-signup/config` (sem empresa, sem segredo) → popup `FB.login` com `config_id`, `response_type=code`, `sessionInfoVersion=3` → `POST /admin/whatsapp/embedded-signup/complete?company_id=` (superadmin; troca do código com o app secret no servidor, descoberta de WABA/número via `debug_token` quando o popup não os informa, `subscribed_apps`, `register` com PIN de 6 dígitos gerado e cifrado, perfil do número, gravação cifrada; 409 se o número está ativo em outra empresa; 502 erro da Meta; 503 cofre/app ausentes) → `POST .../disconnect`. Código 190 da Meta marca `needs_reconnection`. Cifra: `app/core/credential_vault.py` (Fernet, `CREDENTIALS_ENCRYPTION_KEY`; em produção é obrigatória quando há app Meta ou cliente Google configurado — check `credential_vault` no readiness).
- Google: `GOOGLE_CALENDAR_CLIENT_ID/SECRET` por instalação; por empresa, `calendar_integrations.config_json.refresh_token` cifrado (`enc:v1:`), `external_calendar_id` (`primary` por padrão), `connection_source=oauth`, `connected_account_email`, `connected_at/by`. `POST /admin/calendar/google/authorize?company_id=` grava `oauth_states` (state aleatório, 15 min, uso único, ligado à empresa e ao usuário) e devolve a URL de consentimento (`access_type=offline`, `prompt=consent`); `GET /platform/integrations/google/callback` (cookie de sessão, sem CSRF) consome o state, exige que a sessão seja a que iniciou, troca o código, exige `refresh_token`, usa o calendário `primary` para o e-mail, atualiza a MESMA linha numa reconexão (preserva `integration_id`) e redireciona para `/platform?view=agenda&company_id=&google=connected|error&reason=`. `GET .../calendars`, `PATCH .../calendar`, `POST .../disconnect` (revoga no Google). Rows legadas com token em claro continuam válidas; `POST /admin/calendar/integrations` passa a cifrar `refresh_token`/`client_secret`/`password`.
- Backups: `oauth_states` é efêmera e fica fora do conteúdo; segredos cifrados dentro de `config_json` continuam removidos pelo sanitizador — depois de um restore as empresas reconectam pelo painel.

### Recuperação e posse humana — alvo P06 (migration 072)

Desenho congelado para quando P02/P04/P05 estiverem integrados.

- Posse da conversa por `(company_id, contact_id)`: `bot` (default, sem linha) ou `human`, com `owner_user_id`, motivo, `taken_at`, `released_at` e versão para compare-and-set. Memória por empresa já é separada (`contact_company_profiles`); `contacts` é só identidade do número.
- Durante posse `human`: a entrada é persistida e publicada em tempo real; não roda workflow, classificador pago nem resposta automática; no máximo uma pendência aberta "cliente escreveu durante atendimento humano" por conversa. Tomar e devolver são ações explícitas de operador (RBAC operator, escopo de empresa, CSRF, auditoria com ator/empresa/ação, sem texto do cliente). No piloto a posse não é tomada automaticamente.
- Falha no job de entrada classificada: erro de negócio conhecido (vaga ocupada, política) → resposta útil e job concluído; transitório seguro (indisponibilidade antes de qualquer efeito) → nova tentativa com espera e limite; efeito incerto (operação em `executing`/`review`, envio `uncertain`) → revisão com código de motivo, operação vinculada e ação recomendada. Ordem por contato preservada.
- Resolução de revisão: `handled_manually` (conclui sem reexecutar) ou `requeue` somente quando a operação vinculada comprova que nenhum efeito ocorreu. Nunca editar tabelas à mão; nunca reexecutar ação incerta sem evidência.
- Fencing: antes de qualquer efeito transacional, o executor confirma que ainda detém a lease do job (token + validade); dono antigo não produz efeito nem salva resultado.
- Visibilidade por empresa: backlog, idade da entrada mais antiga, revisões abertas, dead letters por código, heartbeat do worker — painel/API e readiness, sem depender de alerta externo.
- Após revisão (14/09): com humano na conversa, resposta gerada antes da tomada não é gravada para envio; envio automático (resposta do bot, lembrete, fila de encaixe, automação) vira dead letter `blocked:human_owned` no dispatch; texto do operador sai; nenhuma automação é disparada e nenhum modelo é chamado para a mensagem. SAIR/VOLTAR continuam valendo e confirmam (origem `whatsapp_channel_reply`). Mensagens recebidas durante a posse não são retomadas pelo bot na devolução. Resolver revisão retira a resposta pendente vinculada (`blocked:resolved_manually`, fora do alerta de dead letter) e recusa (409) enquanto o envio está em andamento.
- Implementado (14/09): tabela `conversation_ownerships`; `GET /admin/conversations/ownership` (viewer), `POST /admin/conversations/{contact_id}/take|release` (operator; `force` só superadmin; exige usuário de sessão), `GET /admin/operations/summary` (viewer); fencing `execution_lease` renovando `inbound_jobs.lease_expires_at` antes do aceite/efeito.

### Recurso default (definição para P03)

Toda empresa com agenda tem recurso `default` de capacidade 1; bookings legados recebem esse recurso deterministicamente. `resource_key` em operações é texto até P03 materializar a tabela de recursos (migration 070 reservada para P03; 071 reservada para P05).

## Evolução semântica

Formalizar entidades/relações e políticas agora. Manter consultas estruturadas para estado vivo e RAG existente para documentos. GraphRAG entra como experimento posterior com proveniência, vigência e isolamento. Banco de grafo dedicado, GNN, multiagentes em runtime e fine-tuning não são pré-requisitos do piloto.
