# Automation Studio

Primeira vertical de automações visuais do Project WAPP: multi-tenant, com
execução durável e efeitos idempotentes.

> **Estado:** habilitado por flag, desligado por padrão. Nenhuma empresa
> executa automação sem duas decisões explícitas — ligar o motor e ativar o
> fluxo.

---

## 1. Control plane versus data plane

A separação é a decisão estrutural do módulo. Os dois planos mudam por razões
diferentes, em ritmos diferentes, e misturá-los é o que faz um editor visual
virar um motor imprevisível.

| | Control plane | Data plane |
|---|---|---|
| **Quem muda** | uma pessoa editando | cada evento processado |
| **Ritmo** | raro | contínuo |
| **Tabelas** | `automation_workflows`, `automation_workflow_drafts`, `automation_workflow_versions`, `automation_deployments` | `automation_events`, `automation_outbox`, `automation_executions`, `automation_step_executions`, `automation_jobs`, `automation_effects`, `automation_approval_gates` |
| **Código** | `app/services/automation_workflow_service.py` | `app/automations/engine.py`, `app/services/automation_event_service.py`, `app/services/automation_execution_service.py` |
| **Falha significa** | não dá para publicar | um evento não foi processado *ainda* |

O domínio puro (`app/automations/`) não pertence a nenhum dos dois: ele
descreve o que um fluxo *significa*, sem I/O.

```
app/automations/          domínio puro, sem I/O
  domain.py               grafo, estados, forma canônica, chaves de efeito
  catalog.py              catálogo de nós v1 (fonte única)
  validation.py           validação autoritativa do grafo
  semantics.py            interpretação única das regras
  simulation.py           percurso sem efeito
  events.py               eventos canônicos, redigidos por allowlist
  effects.py              adapters de efeito externo
  engine.py               motor durável (um passo por job)
  exceptions.py
```

---

## 2. Modelo de eventos

Um evento é um **fato já persistido**, nunca um pedido.

### Eventos canônicos

| Tipo | Gatilho | Origem |
|---|---|---|
| `message.processed` | `trigger.messageProcessed` | orquestrador, no mesmo commit da mensagem |
| `handoff.created` | `trigger.handoffCreated` | serviço de handoff |
| `appointment.booked` | `trigger.appointmentBooked` | agenda |
| `appointment.canceled` | — | agenda |
| `reminder.due` | — | worker de lembretes |
| `manual.triggered` | `trigger.manual` | operador, via API |
| `schedule.triggered` | `trigger.schedule` | agendador |

### Redação por allowlist

`app/automations/events.py` mantém `ALLOWED_PAYLOAD_KEYS`. Uma chave fora
dela é **descartada na construção**. A escolha de allowlist em vez de
blocklist é deliberada: a blocklist erra sempre que alguém acrescenta um
campo novo.

O texto da mensagem e o da resposta nunca entram. Um fluxo decide por
classificação, confiança, estágio e sinalizadores — e `graph_json`, logs e a
API de execuções são visíveis para todo operador do tenant.

### Outbox transacional

`RuntimeRepository.save_message` aceita `automation_event_factory` e grava o
evento **no mesmo commit** da mensagem, dentro de um `SAVEPOINT`:

- não existe janela em que a mensagem está salva e o fato foi perdido;
- se a escrita do evento falhar, só ela é desfeita — o atendimento continua.

---

## 3. Garantias de entrega e limites da idempotência

| Camada | Chave | Garantia |
|---|---|---|
| Evento | `(company_id, event_key)` único | o mesmo fato entra uma vez |
| Inscrição | `(company_id, workflow_id, enrollment_key)` único | a reentrega não cria segunda execução |
| Job | claim atômico com lease | dois workers nunca processam o mesmo job |
| Efeito | `(company_id, effect_key)` único | um retry não repete uma ação já concluída |
| Portão | `(company_id, execution_id, node_id, occurrence)` único | um portão por passo lógico |

### Onde a idempotência termina — e por quê

O ledger de efeitos distingue três estados anteriores, e o terceiro é o
limite honesto do sistema:

- `succeeded` → **não repete**. A ação aconteceu.
- `failed` → **repete**. O adapter reportou falha, então por contrato a ação
  externa não se completou.
- `pending` → **não repete, e marca `indeterminate_previous_attempt`.** Uma
  tentativa anterior morreu entre reservar e registrar o resultado. Não há
  como saber se a ação externa ocorreu. Repetir arriscaria duplicar um envio;
  pular arrisca omitir. O sistema escolhe não duplicar e deixa o caso
  visível para inspeção humana.

Um efeito nesse estado aparece no detalhe da execução com o motivo
preenchido. Trate-o como pendência operacional, não como falha silenciosa.

---

## 4. Estados

### Deployment (control plane)

```
(sem versão) ──publicar──▶ paused ──ativar──▶ active
                             ▲                  │
                             └─────pausar───────┘
```

Publicar **sempre** cria pausado. Ativar é outra chamada, com autorização
própria.

### Execução (data plane)

```
queued ──▶ running ──┬──▶ waiting ──────────┐
                     ├──▶ awaiting_human ───┤
                     │                      ▼
                     └──────────────▶ succeeded | failed | canceled | dead_letter
```

`succeeded`, `failed`, `canceled` e `dead_letter` são terminais: nenhum job
novo nasce deles.

### Job

`queued → running → succeeded | failed | canceled | dead_letter`, com
`lease_owner`, `lease_expires_at`, `attempts`, `max_attempts` e `run_at`.

### Efeito

`pending → succeeded | failed` (ver §3).

### Portão humano

```
awaiting_human ──claim──▶ processing ──decide──▶ completed
       ▲                       │
       └──lease vencido────────┘
       └──▶ expired | canceled
```

---

## 5. Política de pausa

Pausar **bloqueia novas entradas** e **preserva o que já está em execução**.

- O dispatch só inscreve deployments `active`. Pausado não recebe evento novo.
- Uma execução em `waiting` continua com o job agendado e retoma quando o
  tempo vencer — com a versão original.
- Uma decisão humana tomada com o fluxo pausado é **registrada e auditada**,
  mas não avança. Reativar chama `resume_held_decisions`, que retoma cada
  execução pela alça da decisão já tomada.

Isso é diferente de cancelar. Cancelar é cooperativo: marca os jobs e fecha a
execução; o job em voo termina o passo e não reagenda.

---

## 6. Version pinning

Uma execução guarda `version_id` e o motor carrega **a versão da execução**,
nunca a do deployment corrente.

Consequência prática: republicar e ativar uma versão nova não muda o
comportamento de uma execução que já começou. Ela termina com as regras que
tinha quando entrou. É a única semântica que não surpreende quem está com um
follow-up de 24 horas em espera.

Versões publicadas são imutáveis: não há caminho de escrita para
`automation_workflow_versions` depois da criação. Publicar de novo cria uma
versão nova.

### Forma canônica

`canonical_form()` ordena nós e arestas e **descarta posição**. Mover um bloco
no canvas não muda `content_hash`, não gera versão nova e não aparece no diff.
O frontend usa a mesma regra (`behaviourSignature`), e
`tests/test_automation_domain.py` prova a equivalência.

---

## 7. Portões humanos

Máquina de estados no §4. As garantias:

- **claim atômico** — `WHERE status = 'awaiting_human'` decide o vencedor;
  o segundo operador recebe 409;
- **release idempotente** — decidir duas vezes devolve `already_decided` e não
  cria um segundo job de retomada;
- **pausa respeitada** — ver §5;
- **lease vencido volta para a fila** — pelo reconciliador;
- **auditoria com principal real** — `decided_by` vem da credencial, nunca do
  header `X-Admin-Actor`;
- **escopo por empresa** — a rota usa o escopo já autorizado.

---

## 8. Retenção e redação

| Dado | Retenção | Redação |
|---|---|---|
| `automation_events.payload_json` | `AUTOMATION_EXECUTION_RETENTION_DAYS` | allowlist na construção |
| `automation_executions.context_json` | idem | `redact_context` mascara `contact_id`, `conversation_id`, `message_id` na leitura |
| `automation_step_executions.detail_json` | idem | só decisões e metadados |
| `automation_effects.request_json` | idem | dados já redigidos do contexto |

Nenhuma tabela de automação guarda texto de conversa.

---

## 9. Rollback

| Situação | Ação |
|---|---|
| Versão nova com problema | pausar o fluxo; publicar a versão anterior de novo; ativar |
| Fluxo causando efeito indesejado | pausar (bloqueia entradas; não mata quem espera) |
| Execução travada | `POST /admin/automations/executions/{id}/cancel` |
| Job em dead-letter | corrigir a causa e `POST .../retry` — efeitos concluídos não repetem |
| Módulo inteiro | `AUTOMATION_ENGINE_ENABLED=false`; o orquestrador segue normal |

Desligar a flag não apaga dado: eventos e execuções ficam no banco e o
processamento simplesmente para.

---

## 10. Rollout gradual

1. `AUTOMATION_ENGINE_ENABLED=true` — eventos passam a ser gravados; nada é
   executado sem workflow ativo.
2. Criar o fluxo, validar e simular. A simulação não produz efeito.
3. Publicar (nasce pausado).
4. Ativar em **uma** empresa piloto.
5. Acompanhar execuções e dead-letter.
6. `AUTOMATION_WORKER_ENABLED=true` e subir o worker.
7. Expandir empresa a empresa.

---

## 11. Catálogo de nós v1

**Gatilhos:** `messageProcessed`, `handoffCreated`, `appointmentBooked`,
`schedule`, `manual`.

**Condições:** `classification`, `confidence`, `pipelineStage`,
`customerProfile`, `hasKnowledgeSource`, `handoffRequired`.

**IA:** `composeMessage`, `extractStructured`, `classifyOrRoute` — reutilizam
provider, RAG e contrato de resposta existentes. Produzem conteúdo; nunca
afirmam que uma ação externa ocorreu.

**Tempo e controle:** `delay.wait`, `control.if`, `control.switch`,
`control.humanApproval`, `terminal.done`, `terminal.error`,
`terminal.canceled`.

**Ações:** `createHandoff`, `createReminder`, `sendMessage`,
`updatePipelineStage` — todas passam por adapter server-side e pelo ledger.

### Capacidades exigidas

O catálogo declara `required_capability` por nó, e
`app/automations/entitlements.py` traduz cada uma num **limite do plano
contratado**. Não existe uma segunda tabela de features ao lado do billing:
ela divergiria no dia em que um plano mudasse.

| Nó | Capacidade | Limite no plano |
|---|---|---|
| `trigger.appointmentBooked` | `scheduling` | `bookings` |
| `action.createReminder` | `reminders` | `reminders_per_booking` |
| `action.sendMessage` | `outbound` | `outbound_campaigns` |

Um limite ausente ou `<= 0` significa "não faz parte do plano" — a mesma
convenção de `billing_service.check_limit`, de propósito: duas leituras
diferentes de "limite zero" é como um plano acaba permitindo por um caminho o
que nega pelo outro.

Automatizar não é caminho lateral para exceder o contrato: um nó que produz
efeito externo consome o mesmo direito que a funcionalidade equivalente fora
da automação.

**A checagem acontece ao publicar e ao ativar, não ao executar.** Verificar só
na execução põe o fluxo no ar, roda metade dos passos e falha no nó de envio —
deixando efeito parcial aplicado e uma execução em `failed` que ninguém pediu.
Na publicação, o mesmo erro é uma mensagem de formulário.

O **orçamento de execução** é o oposto e só pode ser medido em runtime: cada
inscrição consome `automation_executions`, uma métrica de uso degradada — ao
estourar, a plataforma para de inscrever novos fluxos em vez de derrubar o
produto.

Os nós de IA não exigem capacidade: o caminho de composição é o mesmo do
atendimento, que toda empresa já tem.

| Plano | `automation_workflows` | `automation_executions` |
|---|---|---|
| Essencial | 3 | 2.000 |
| Avançado | 15 | 15.000 |
| Premium | 60 | 60.000 |

---

## 12. Capacidades ainda não implementadas

Estão fora da v1 **por decisão**, não por falta de tempo:

| Fora | Por quê |
|---|---|
| Execução de código, shell, SQL livre, HTTP genérico, filesystem | cada um transforma o motor num executor remoto para quem editar um fluxo; o modelo de ameaça multi-tenant não suporta isso sem sandbox real |
| Loops e ciclos | sem eles a execução é uma linha e o custo por evento é previsível; o validador rejeita ciclos |
| Fan-out, split/join | mesma razão; uma saída, uma conexão |
| Community nodes | exigem revisão de supply chain |
| Agendador de `trigger.schedule` | o gatilho existe e valida, mas nada emite `schedule.triggered` automaticamente ainda |
| Avaliação de IA no `classifyOrRoute` | hoje usa a classificação já apurada pelo orquestrador |

`SINGLE_PASS_OCCURRENCE = 1` em `engine.py` é consequência direta da
ausência de loops: cada nó é visitado no máximo uma vez por execução, e a
ocorrência precisa ser **estável entre tentativas** para o ledger reconhecer
o efeito já aplicado. Quando loops existirem, esse valor passa a ser o índice
da iteração.

---

## 13. Frontend

`frontend/automation-studio/` — React 18 + TypeScript strict + `@xyflow/react`,
build Vite, servido pelo mesmo FastAPI em `/platform/automations`.

- **Mesma origem** — reutiliza o cookie HTTP-only de sessão. Sem CORS, sem
  token em `localStorage`, sem host configurável.
- **O navegador não executa fluxo.** Validar, simular, publicar e executar são
  sempre do servidor. `tests/test_automation_studio_bundle.py` prova a
  ausência de `eval`, de `new Function` e de qualquer formato de credencial no
  fonte e no bundle.
- **O inspector é gerado pelo catálogo do servidor.** Campo novo no backend
  aparece na UI sozinho, com o mesmo rótulo, opções e limites — é o que
  impede a UI de divergir da validação.

```bash
cd frontend/automation-studio
npm ci
npm run build     # tsc --noEmit && vite build → app/platform/automations
npm test
npm run lint
```

---

## 14. Configuração

| Variável | Padrão | O que faz |
|---|---|---|
| `AUTOMATION_ENGINE_ENABLED` | `false` | liga a gravação de eventos e a operação de fluxos |
| `AUTOMATION_WORKER_ENABLED` | `false` | permite o loop do worker **nesta máquina** |
| `AUTOMATION_MAX_STEPS_PER_EXECUTION` | `50` | teto de passos por execução |
| `AUTOMATION_MAX_ATTEMPTS` | `5` | tentativas antes do dead-letter |
| `AUTOMATION_JOB_LEASE_SECONDS` | `120` | validade do lease de job |
| `AUTOMATION_GATE_LEASE_SECONDS` | `300` | validade da reserva de um portão |
| `AUTOMATION_MAX_EVENT_BYTES` | `65536` | teto do payload de evento |
| `AUTOMATION_WORKER_POLL_SECONDS` | `5` | intervalo entre ciclos do worker |
| `AUTOMATION_WORKER_BATCH_SIZE` | `10` | jobs por ciclo |
| `AUTOMATION_RETRY_BASE_SECONDS` | `10` | base do backoff exponencial |
| `AUTOMATION_RETRY_MAX_SECONDS` | `900` | teto do backoff |

Motor e worker são **dois interruptores**, não um. Ligar o motor habilita
publicar e ativar fluxos; ligar o worker faz *esta máquina* puxar trabalho da
fila. Um deploy pode ter o motor ligado e nenhum worker (fluxos param na fila,
nada se perde), ou várias réplicas de worker dividindo a mesma fila.

O default de ambos é `false`. Automação começar a executar sozinha porque um
deploy trouxe as migrations é exatamente o que esse default previne.

---

## 15. Worker

```bash
python -m app.tools.automation_worker --run-once     # um ciclo
python -m app.tools.automation_worker --validate-only
python -m app.tools.automation_worker                # loop
```

Cada ciclo faz, nesta ordem: reconcilia (leases vencidos, portões
abandonados), despacha o outbox e executa jobs prontos. Reconciliar vem
primeiro para que o trabalho recuperado entre no mesmo ciclo.

O worker é global — varre a fila sem filtrar empresa — e o isolamento vem do
`company_id` que acompanha cada job. Várias réplicas podem rodar juntas: no
Postgres o claim usa `SELECT … FOR UPDATE SKIP LOCKED`; no SQLite, um
`UPDATE` guardado por `status`, com nova tentativa em caso de disputa.
