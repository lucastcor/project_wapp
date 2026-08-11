# Plataforma Frontend: o que existe e como conversa com o backend

## Objetivo
Este documento explica a interface dedicada em `/platform`, o papel de cada área do frontend e como cada ação chega ao backend.

O foco é manutenção: se algo quebrar na interface, este arquivo deve indicar onde começar a investigar.

## Arquivos principais da plataforma

| Arquivo | Papel |
| --- | --- |
| `app/main.py` | Sobe o FastAPI, registra os routers e publica os assets em `/platform/assets`. |
| `app/api/platform_routes.py` | Entrega o shell HTML da plataforma e faz login/logout/leitura da sessão web. |
| `app/api/admin_security.py` | Aceita a sessão da plataforma por cookie e libera acesso aos endpoints administrativos. |
| `app/services/platform_session_service.py` | Emite, valida, assina e remove o cookie HTTP-only da sessão web. |
| `app/platform/index.html` | Casca HTML da plataforma; carrega fontes, CSS e o `app.js`. |
| `app/platform/assets/styles.css` | Identidade visual, layout, canvas, timeline e componentes da interface. |
| `app/platform/assets/app.js` | Aplicação SPA da plataforma: estado, renderização, eventos e chamadas para a API. |
| `app/api/admin_routes.py` | Backend da maior parte das telas operacionais. |
| `app/api/routes.py` | Backend do `Bot Studio` e do webhook/entrada conversacional. |

## Como a plataforma sobe

1. `app/main.py` registra `platform_router` e faz `mount("/platform/assets", ...)`.
2. `GET /platform` em `app/api/platform_routes.py` devolve `app/platform/index.html`.
3. O navegador carrega `styles.css` e `app.js`.
4. O `bootstrap()` do `app.js` monta a aplicação dentro de `#platform-root`.
5. A interface passa a operar como SPA: troca de telas no cliente, com dados puxados por `fetch`.

## Como a autenticação funciona

### Fluxo de login

1. A plataforma abre e tenta `GET /platform/auth/session`.
2. Se ainda não houver sessão válida, a UI mostra o login.
3. O usuário informa `admin_token` e `admin_actor`.
4. O frontend chama `POST /platform/auth/session`.
5. `app/services/platform_session_service.py` valida o token administrativo e emite um cookie assinado.
6. O cookie fica no navegador como `HTTP-only`, então o JavaScript não guarda a credencial em texto puro.

### Como a sessão é reaproveitada

- O `apiRequest()` do `app.js` usa `credentials: "same-origin"`.
- Isso faz o navegador enviar automaticamente o cookie da sessão para as chamadas seguintes.
- `app/api/admin_security.py` tenta autenticar primeiro pela sessão web; se não conseguir, ainda aceita token via header.

### Fluxo de logout

1. O frontend chama `DELETE /platform/auth/session`.
2. O backend remove o cookie.
3. A UI volta para o estado deslogado.

## Como o `app.js` está organizado

O `app/platform/assets/app.js` é a aplicação principal. Ele está dividido em cinco blocos práticos:

| Bloco | Funções principais | Papel |
| --- | --- | --- |
| Estado/configuração | `loadStoredConfig`, `persistConfig`, `state` | Guarda escopo de empresa, actor, sessão e dados carregados. |
| Camada HTTP | `apiRequest` | Normaliza chamadas REST, query params, erros e envio do cookie da sessão. |
| Carregadores de dados | `loadDashboardData`, `loadStudioData`, `loadFlowData`, `loadAgendaData`, `loadContactsData`, `loadTrainingData` | Puxa dados da API por tela. |
| Renderização | `renderDashboardView`, `renderStudioView`, `renderFlowsView`, `renderAgendaView`, `renderContactsView`, `renderTrainingView` | Monta a interface HTML de cada módulo. |
| Eventos | `bindStudioEvents`, `bindFlowEvents`, `bindAgendaEvents`, `bindContactsEvents`, `bindTrainingEvents` | Liga botões/formulários às chamadas do backend. |

## Como cada área da interface funciona

### 1. Control Tower

**O que faz**

- Mostra leitura rápida de readiness, perfil do sistema, onboarding e observabilidade.
- Serve como tela de situação geral da operação.

**Chamadas para o backend**

| Endpoint | Backend |
| --- | --- |
| `GET /admin/observability/summary` | `app/api/admin_routes.py` -> `RuntimeRepository.get_observability_summary()` |
| `GET /admin/system/readiness` | `app/api/admin_routes.py` -> `app/services/system_readiness_service.py` |
| `GET /admin/system/profile` | `app/api/admin_routes.py` -> `app/services/system_readiness_service.py` |
| `GET /admin/system/onboarding` | `app/api/admin_routes.py` -> `app/services/system_readiness_service.py` |

**Persistência**

- Não grava dados.
- É uma tela de leitura operacional.

### 2. Bot Studio

**O que faz**

- Permite testar o runtime conversacional antes de publicar.
- Simula mensagens de cliente e de operador.
- Ajuda a validar atendimento, cancelamento, reagendamento e fluxos naturais sem depender do WhatsApp real.

**Chamadas para o backend**

| Endpoint | Backend |
| --- | --- |
| `POST /messages/incoming` | `app/api/routes.py` -> `app/core/orchestrator.py` -> serviços de negócio |

**Cadeia real do backend**

`routes.py` recebe a mensagem -> `core/orchestrator.py` decide o fluxo -> serviços como `classification_service`, `reply_services`, `conversation_workflow_service`, `memory_service`, `pipeline_service`, `handoff_service` e `appointment_queue_service` entram conforme o caso -> `repository.py` persiste efeitos.

**Persistência**

- Pode criar/atualizar contatos, mensagens, bookings, pending questions, outbounds e memória, dependendo do cenário testado.

### 3. Flow Studio

**O que faz**

- Edita os playbooks conversacionais da empresa.
- Desenha nós, conexões e posições no canvas.
- Permite criar respostas aprovadas para treino/estilo.

**Chamadas para o backend**

| Endpoint | Backend |
| --- | --- |
| `GET /admin/company-context` | `app/api/admin_routes.py` -> `app/core/company_context.py` |
| `PUT /admin/company-context` | `app/api/admin_routes.py` -> `app/core/company_context.py` |
| `GET /admin/message-examples` | `app/api/admin_routes.py` -> `RuntimeRepository` |
| `POST /admin/message-examples` | `app/api/admin_routes.py` -> `RuntimeRepository` |

**Como o canvas é salvo**

- O desenho fica em `company_context.conversation_pipelines`.
- Cada nó pode carregar `key`, `label`, `objective`, `trigger_terms`, `next_keys`, `preferred_cta`, `handoff_when`, `success_criteria` e `canvas_position`.
- O backend valida esse contrato em `app/core/company_context.py`.

**Persistência**

- Vai para os JSONs de contexto da empresa em `app/data/company_contexts/`.
- Respostas aprovadas vão para `message_examples` no banco.

### 4. Agenda Console

**O que faz**

- Mostra perfil da agenda, bookings, fila, ofertas e timeline semanal.
- Permite consultar disponibilidade, marcar, reagendar e cancelar manualmente.
- Dá visibilidade operacional para agenda e remanejamento.

**Chamadas para o backend**

| Endpoint | Backend |
| --- | --- |
| `GET /admin/calendar/profile` | `app/api/admin_routes.py` -> `app/services/calendar_service.py` |
| `GET /admin/calendar/bookings` | `app/api/admin_routes.py` -> `app/services/calendar_service.py` |
| `GET /admin/calendar/queue` | `app/api/admin_routes.py` -> `app/services/appointment_queue_service.py` |
| `GET /admin/calendar/queue-offers` | `app/api/admin_routes.py` -> `app/services/appointment_queue_service.py` |
| `GET /admin/calendar/timeline` | `app/api/admin_routes.py` -> `app/services/calendar_service.py` |
| `POST /admin/calendar/availability/query` | `app/api/admin_routes.py` -> `app/services/calendar_service.py` |
| `POST /admin/calendar/bookings` | `app/api/admin_routes.py` -> `app/services/calendar_service.py` |
| `POST /admin/calendar/bookings/{booking_id}/reschedule` | `app/api/admin_routes.py` -> `app/services/calendar_service.py` |
| `POST /admin/calendar/bookings/{booking_id}/cancel` | `app/api/admin_routes.py` -> `app/services/calendar_service.py` + `app/services/appointment_queue_service.py` |

**Como a timeline é montada**

- A UI consome `GET /admin/calendar/timeline`.
- O backend calcula sobreposição e colunas em `get_calendar_timeline()` e `_layout_timeline_segments()` dentro de `app/services/calendar_service.py`.
- O CSS posiciona visualmente os segmentos em grade semanal.

**Persistência**

- Bookings, integrações, fila e ofertas ficam no banco via `repository.py` / `db.py`.
- Se houver provider externo configurado, cancelamento e reagendamento também tentam sincronizar o evento real.

### 5. Contacts Desk

**O que faz**

- Lista contatos, busca por filtros e permite ajustar `customer_profile` e notas.
- É o ponto manual para saneamento operacional e preparação de contexto.

**Chamadas para o backend**

| Endpoint | Backend |
| --- | --- |
| `GET /admin/contacts` | `app/api/admin_routes.py` -> `RuntimeRepository` |
| `GET /admin/contacts/{contact_id}` | `app/api/admin_routes.py` -> `RuntimeRepository` |
| `PATCH /admin/contacts/{contact_id}` | `app/api/admin_routes.py` -> `RuntimeRepository` |

**Persistência**

- Atualiza a tabela `contacts`.

### 6. Training Ops

**O que faz**

- Importa base histórica/CRM.
- Mostra preview/review do dataset.
- Dispara job de fine-tuning.

**Chamadas para o backend**

| Endpoint | Backend |
| --- | --- |
| `GET /admin/training-dataset/review` | `app/api/admin_routes.py` -> `app/services/training_export_service.py` |
| `POST /admin/training-import` | `app/api/admin_routes.py` -> `app/services/training_import_service.py` |
| `GET /admin/finetuning/jobs` | `app/api/admin_routes.py` -> `app/services/finetuning_service.py` + `RuntimeRepository` |
| `POST /admin/finetuning/jobs` | `app/api/admin_routes.py` -> `app/services/finetuning_service.py` |

**Persistência**

- O import pode atualizar contatos e criar `message_examples`.
- Os jobs ficam registrados em `finetuning_jobs`.

## Como o frontend escolhe o escopo da empresa

- A plataforma carrega a lista em `GET /admin/companies`.
- O usuário escolhe uma empresa pelo painel.
- O `app.js` passa `company_id` e/ou `company_slug` via query params nas telas administrativas.
- O backend usa esse escopo para ler contexto, agenda, contatos e dataset da empresa certa.

## Onde os dados ficam

| Tipo de dado | Onde fica |
| --- | --- |
| Sessão web da plataforma | Cookie assinado emitido por `platform_session_service.py` |
| Seleções locais da UI | `localStorage` do navegador |
| Contexto e pipelines da empresa | JSON em `app/data/company_contexts/*.json` |
| Bookings, contatos, mensagens, fila, reminders, jobs | Banco via `app/storage/repository.py` e `app/storage/db.py` |

## Como depurar um problema de frontend

### Se a tela não carrega

- Verificar `GET /platform` e `GET /platform/assets/app.js`.
- Conferir se `app/main.py` registrou `platform_router` e o `mount` dos assets.

### Se o login falha

- Verificar `POST /platform/auth/session`.
- Conferir `ENABLE_ADMIN_AUTH`, tokens administrativos e `PLATFORM_SESSION_SECRET`.
- Validar se `app/api/admin_security.py` está aceitando o cookie.

### Se uma tela abre mas sem dados

- Conferir a função `load...Data()` correspondente em `app/platform/assets/app.js`.
- Inspecionar o endpoint correspondente em `app/api/admin_routes.py`.
- Confirmar se a empresa selecionada foi enviada via query string.

### Se uma ação salva mas não aparece no runtime

- Confirmar onde o dado persiste:
- `company_context` e `conversation_pipelines` ficam em JSON.
- agenda, contatos e treino ficam no banco.
- mensagens de teste entram pelo mesmo backend usado em produção.

## Resumo operacional

- A plataforma dedicada é um frontend SPA simples, sem framework, servido pelo próprio FastAPI.
- A autenticação principal usa sessão opaca e revogável de identidade, sem token
  persistido no JavaScript; o cookie assinado permanece como break-glass legado.
- A operação visual conversa com rotas administrativas e com as rotas próprias
  de identidade, equipe, sessão e eventos em `platform_routes.py`.
- O `Bot Studio` conversa com o mesmo runtime do bot real.
- O `Flow Studio` grava no `company_context`.
- O `Agenda Console` conversa com `calendar_service.py` e `appointment_queue_service.py`.
- O `Training Ops` conversa com importação, curadoria e fine-tuning.
- A tela `Equipe` gerencia memberships, convites e papéis por tenant.
- `Billing` usa Stripe Checkout/Portal e exibe consumo/limites reconciliados.
