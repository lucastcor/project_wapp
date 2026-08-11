# Plano de fechamento para piloto

Produzido na Onda 0, a partir de quatro auditorias independentes e paralelas
(segurança/tenancy, runtime de automações, RAG/modelos, infraestrutura) mais a
investigação do líder contra um PostgreSQL real.

**Linha de base:** `docs/closure-baseline.md`. 646 testes Python passando em
SQLite, 40 no frontend. Nada disso provava os caminhos críticos.

## Como ler a severidade

| Nível | Significado |
|---|---|
| **P0** | Vazamento cross-tenant, perda/duplicação de trabalho, ou impede a stack de subir |
| **P1** | Estado impossível observável, elevação de privilégio, ou promessa não cumprida |
| **P2** | Defesa em profundidade, custo, ergonomia operacional |

## Estados

`CORRIGIDO` · `EM ANDAMENTO` · `PENDENTE` · `ACEITO` (com justificativa)

---

## L — Achados do líder (PostgreSQL real)

Só apareceram porque a suíte foi executada contra um PostgreSQL de verdade.
Nenhum deles é observável em SQLite.

### [L-01] P0 — Tradução de tipo quebrava todo o schema no PostgreSQL · **CORRIGIDO**

- **Causa raiz:** `schema_utils.create_table` fazia
  `columns_sql.replace("TIMESTAMP", "TIMESTAMPTZ")`, o que também reescrevia
  `CURRENT_TIMESTAMP` como `CURRENT_TIMESTAMPTZ`. O PostgreSQL responde
  `cannot use column reference in DEFAULT expression`.
- **Alcance:** 90 colunas com `DEFAULT CURRENT_TIMESTAMP`. `init_db()` falhava
  na primeira migration. **O produto nunca subiu no backend que declara usar
  em produção.**
- **Correção:** substituição por token com fronteira de palavra, em passe
  único (`translate_column_definition`). `\b` não casa dentro de
  `CURRENT_TIMESTAMP` porque `_` é caractere de palavra. `ensure_column` passa
  a usar a mesma tradução — antes, coluna adicionada depois nascia `TIMESTAMP`
  enquanto a criada de origem nascia `TIMESTAMPTZ`.
- **Testes:** `tests/test_schema_type_translation.py` (15).
- **Rollback:** reverter `app/storage/schema_utils.py`; sem migration.

### [L-02] P0 — Parâmetro opcional sem tipo derrubava 69 testes no PostgreSQL · **CORRIGIDO**

- **Causa raiz:** o padrão `WHERE (? IS NULL OR coluna = ?)`, usado 67 vezes
  para filtro opcional, não deixa o PostgreSQL inferir o tipo do parâmetro que
  só aparece em `IS NULL`: `could not determine data type of parameter $N`.
- **Alcance:** praticamente toda listagem filtrável do produto.
- **Correção:** cast central em `_normalize_postgres_query`, aplicado na
  tradução `?` → `%s`. Um cast em 67 pontos de chamada seria esquecido no
  próximo `SELECT`.
- **Testes:** `tests/test_postgres_query_normalization.py` (13).
- **Rollback:** reverter `app/storage/db.py`; sem migration.

### [L-03] P0 — A suíte não podia rodar contra PostgreSQL · **CORRIGIDO**

- **Causa raiz:** `tests/conftest.py` fixava `DATABASE_URL` em SQLite
  incondicionalmente.
- **Correção:** `WAPP_TEST_DATABASE_URL` aponta a suíte para um PostgreSQL
  descartável; `pytest.ini` declara os marcadores `integration`/`postgres`.
- **Resultado:** 72 falhas na primeira execução real → 7 após L-01 e L-02.

### [L-04] P1 — Divergência de tipo entre backends · **CORRIGIDO**

- **Evidência:** `job["run_at"] > datetime.now(UTC).isoformat()` em testes, e
  `run_at <= ?` com string ISO no claim. No SQLite tudo é texto; no PostgreSQL
  volta `datetime`, e a comparação levanta `TypeError`.
- **Alcance real:** maior do que parecia. `CompanyResponse` recusava o
  `created_at` do próprio banco (Pydantic esperava `str`); e comparar ISO como
  texto entre offsets diferentes está **errado** — `13:36-03:00` e
  `15:36+00:00` são o mesmo instante.
- **Correção:** normalização no cursor (`_normalize_postgres_row`): timestamp
  vira ISO **em UTC**, `date` vira ISO, `Decimal` vira `float`. Um lugar só,
  os dois backends com o mesmo contrato.

### [L-05] P0 — Timestamp inválido era gravado como texto · **CORRIGIDO**

- **Causa raiz:** `_normalize_timestamp` devolvia o **texto cru** quando
  nenhum formato casava, e esse texto ia para uma coluna `TIMESTAMP`.
- **Por que ninguém viu:** o SQLite não tem tipo de coluna, então aceitava. O
  PostgreSQL recusa com `invalid input syntax for type timestamp with time
  zone`.
- **Não é só incompatibilidade:** é corrupção de dado. Um CSV com vírgula não
  escapada desloca as colunas e o conteúdo da mensagem chega ao campo de data.
- **Correção:** valor não parseável vira `None`. A linha continua importável e
  a ausência fica explícita.
- **Efeito colateral revelador:** a fixture do e2e tinha 4 de 14 linhas com
  vírgula não escapada. O teste passava porque a corrupção mascarava o defeito
  da própria fixture. Corrigido com `csv.writer`.

---

## T — Segurança e tenancy

Todos verificados por **execução** contra o app real, não por leitura.

**Causa raiz única:** `require_company_scope` resolve e autoriza a empresa e
grava em `request.state`. Falta a ponte: em `app/api/admin_routes.py`, 48
handlers declaram `company_id: int | None = Query(default=None)` e apenas 8
consomem o escopo autorizado. Os outros 40 releem a query — e `company_id=None`
significa **todas as empresas** no SQL (`WHERE (? IS NULL OR company_id = ?)`).

**Omitir o parâmetro é o exploit.** Não é preciso forjar nada.

A suíte não via porque todo teste de isolamento envia
`params={"company_id": other_company}` — caminho que de fato dá 403. O caminho
da **omissão** nunca era exercido.

### [T-01] P0 — Leitura cross-tenant em 10 famílias · **CORRIGIDO**

`GET /admin/messages` sem `company_id`, com token do tenant A, devolvia
mensagens do tenant B. Idem contacts (nome, telefone, resumo, notas),
pending-questions, knowledge-entries, message-examples, outbound-messages,
audit-events (mapa de quem opera o outro tenant), reminders, calendar/bookings,
analytics/trend.

`read_contacts` agravava: buscava tudo e truncava com `contacts[:limit]` depois
do fetch — paginação pós-busca permite varrer a base inteira.

### [T-02] P0 — Escrita cross-tenant por recurso carregado por ID · **CORRIGIDO**

`PATCH /admin/contacts/{id}` com `company_id=None` buscava globalmente e
gravava com `company_id or contact.get("company_id")` — autorização invertida:
a empresa do registro encontrado virava a empresa da escrita. Verificado:
contato do tenant B alterado por principal do tenant A. Idem
`outbound-messages/{id}/mark-sent` e `pending-questions/{id}/answer`.

### [T-03] P0 — Escrita cross-tenant em rotas sem `company_id` · **CORRIGIDO (reaberto e refeito)**

`POST /admin/calendar/bookings/{id}/cancel`, `.../reschedule`,
`queue-offers/{id}/respond`, `analytics/bookings/{id}/outcome` e
`.../cancellation-reason` não aceitavam empresa alguma, então
`require_company_scope` não tinha o que autorizar. Verificado: booking do
tenant B cancelado e 3 lembretes dele cancelados junto. IDs são sequenciais
globais, portanto enumeráveis.

> **Este item foi marcado CORRIGIDO indevidamente na rodada anterior.** A
> conversão em massa de handlers só alcançou os que **já tinham** um parâmetro
> `company_id` na assinatura; estas cinco não tinham nenhum, então o regex
> nunca as tocou — e nenhum teste as cobria. O estado foi atualizado sem
> execução, exatamente o que `.claude/rules/testing-and-release.md` proíbe.

**Correção efetiva:**

1. as cinco rotas injetam `company_id: int = Depends(require_authorized_company_id)`
   e repassam ao serviço, que já filtrava dentro da query;
2. `TenantResourceNotFoundError` (nova, em `app/core/exceptions.py`) distingue
   "não está no seu escopo" de erro de validação, e um handler registrado no
   app inteiro a mapeia para **404** — nunca 400 (a requisição está bem
   formada) nem 403 (revelaria que o recurso existe);
3. a auditoria passa a registrar a empresa **autorizada**, não a do registro
   carregado — antes, uma exploração era auditada com a empresa da vítima e
   sumia do filtro de quem deveria investigá-la.

**Testes:** `tests/test_cross_tenant_resource_by_id.py` (26). Todos nasceram
vermelhos. Cobrem, por rota: omissão, `company_id` próprio, `company_id`
alheio, recurso inexistente indistinguível, viewer, operator legítimo,
membership revogada e efeitos derivados (lembretes do outro tenant).

**Verificação contra PostgreSQL real:** a exploração original responde agora
**404** e o booking de B permanece `confirmed`.

### [T-04] P0 — `company_id` de path nunca autorizado · **CORRIGIDO**

`GET /admin/onboarding/setup-status/{company_id}` — `require_company_scope` só
lê `request.query_params`. Verificado: nome, slug, `business_type` e postura de
configuração do tenant B devolvidos a um principal do tenant A. Oráculo de
enumeração de toda a base de clientes da plataforma. A mesma informação via
query (`/admin/system/onboarding?company_id=B`) já dava 403.

### [T-05] P1 — Superadmin de tenant cria empresa · **CORRIGIDO**

`POST /admin/companies` é `platform_global` (403 correto), mas
`POST /admin/onboarding/setup` estava sob `POLICY_TENANT` e chama
`repository.create_company`. Verificado: 200, empresa criada. Permitia poluir o
catálogo, consumir billing e ocupar o slug de um concorrente.

**Correção:** `("/admin/onboarding/setup", {"POST"}, POLICY_PLATFORM_GLOBAL)`.
Encontrado pelo teste estrutural novo, não por inspeção manual.

### [T-06] P1 — Papel da membership é gravado e nunca aplicado · **CORRIGIDO**

`platform_company_memberships.role` existe, é gravado por
`POST /admin/principals/{id}/memberships` e devolvido pela query — e
`_load_principal_scope` descartava o campo, mantendo só `company_id`. As duas
decisões de permissão (`build_require_admin_role` e `AdminPrincipal.has_role`)
usavam `global_role`. Verificado: rebaixar alguém para `viewer` em uma empresa
não tinha efeito nenhum.

### [T-07] P1 — `/admin/state` e observability entregam o agregado da plataforma · **CORRIGIDO**

`SELECT COUNT(*) FROM messages` sem cláusula de empresa, sob política
`unscoped`. Qualquer viewer de tenant lê o número de empresas na plataforma,
quantas estão ativas e o volume total — escala do negócio inferível por
subtração e monitorável ao longo do tempo.

### [T-08] P2 — Diagnóstico global para qualquer viewer · **CORRIGIDO**

`/admin/system/readiness` e `/admin/system/migrations` são `unscoped` e
devolvem postura operacional, guardas desligados e versão de schema.

### [T-09] P2 — Filtro de tenant em memória em `read_companies` · **CORRIGIDO**

Único sobrevivente da classe. Está correto hoje, mas depende de o handler
lembrar da compreensão de lista. `get_companies` deveria aceitar `company_ids`
como `get_finetuning_job_records` já faz.

### [T-10] P2 — `TenantScopeError` escapa como 500 · **CORRIGIDO**

`/admin/training-dataset/json` sem empresa → 500. Falha **fechado**, o que
salvou essa família de entrar no T-01, mas 500 é resposta errada para erro do
cliente e a diferença de status é sinal para o atacante. Deve ser 400.

### [T-11] P2 — Auditoria registra o `company_id` da query · **PENDENTE**

`_record_audit_event(..., company_id=company_id)` grava o valor cru, possivelmente
`None`, exatamente nas requisições que exploravam T-01/T-02 — o incidente não
aparece filtrado por tenant. `broadcast_event` tem o mesmo defeito, e evento com
`company_id=None` é descartado por todo assinante escopado.

---

## R — Runtime de automações

### [R1] P0 — Efeito indeterminado tratado como sucesso · **PENDENTE**

`reserve_effect` distingue honestamente `succeeded`/`failed`/`pending`, mas o
motor colapsa os três em `effect_reused=True`, grava o passo como `succeeded` e
**avança para o próximo nó**. Se o worker morreu entre a chamada externa e o
registro do resultado, ninguém sabe se a mensagem foi enviada — e o fluxo
continua como se tivesse sido. `grep indeterminate` no repositório: duas
ocorrências, ambas de escrita/log. Nenhuma rota, nenhum estado
`needs_reconciliation`, nada na UI.

**Correção:** estado `needs_reconciliation` na execução, o passo **não** vira
`succeeded`, nenhum job novo, e rota de reconciliação auditada exigindo papel
apropriado e escopo de empresa.

### [R2] P0 — Outbox sem recuperação de lease · **PENDENTE**

`recover_expired_leases` é `UPDATE automation_jobs` literal. Uma entrada de
outbox reservada por um worker que morreu fica `running` para sempre e nunca
volta a ser candidata (`claimable_status="pending"`). O evento existe, a
inscrição nunca acontece, nada aparece em dead-letter. Quebra a garantia
central do padrão outbox.

### [R3] P0 — Próximo job enfileirado antes de fechar o atual · **PENDENTE**

`execute_step` enfileira o job de N+1 e só depois `process_job` chama
`finish_job`. Morrer entre os dois faz o lease vencer, o job de N ser
reexecutado inteiro e **um segundo** job de N+1 nascer. Não há índice único que
impeça. Agravante: `append_step` faz `SELECT MAX(sequence)` + `INSERT` sem
atomicidade contra um índice **único** — a colisão vira `except Exception`
genérico e é reportada como falha transitória do adapter.

### [R4] P0 — Finalização por ID, sem fencing token · **PENDENTE**

`finish_job`, `finish_outbox_entry` e `release_gate` usam
`WHERE id = ? AND company_id = ?`, sem validar o dono do lease. Um worker cujo
lease venceu finaliza trabalho que outro já assumiu. A primitiva correta existe
no módulo (`_claim_one` e `claim_gate` fazem CAS) — só não é usada na
finalização.

### [R5] P0 — Cancelamento não é monotônico · **PENDENTE**

`cancel_requested` é lido uma única vez, do snapshot do job. Um cancelamento
que chegue depois dessa leitura não é visto, e `update_execution` não tem
guarda de estado — a execução cancelada volta para `running`, aplica o efeito
e enfileira um job novo, que nasce com `cancel_requested = 0` e nunca será
alcançado pelo cancelamento.

### [R6] P0 — Gate expirado sem política · **PENDENTE**

`expire_overdue_gates` marca o gate `expired` e não toca a execução. Não há job
na fila. `decide_gate` recusa gate expirado. `resume_held_decisions` só olha
gates `completed`. A execução fica em `awaiting_human` para sempre, e
**invisível**: `list_pending_gates` filtra por `awaiting_human` no gate.

### [R7] P0 — `resume_held_decisions` retoma pelo gate errado · **PENDENTE**

Filtra por `execution.status == awaiting_human` sem verificar **qual** gate
está aberto. Num fluxo com dois portões, o gate A já decidido casa, e a
execução salta o gate B sem decisão — bypass de aprovação humana.
`DECISION_APPROVED` como default agrava: gate `completed` sem decisão gravada é
reprocessado como aprovado.

### [R8] P1 — `decide_gate` ignora `claimed_by` · **PENDENTE**

Reservar não protege nada além da UI. Qualquer operador decide o portão de
outro, sem takeover e sem que a auditoria distinga os dois casos.

### [R9] P1 — `get_open_gate_for_node` não filtra status · **PENDENTE**

Apesar do nome, não tem predicado de status. Um job replay do nó do portão
reabre um gate já decidido, e a execução volta para `awaiting_human` apontando
para um gate `completed` — invisível na fila.

### [R10] P1 — Estados terminais não são impostos · **PENDENTE**

`update_execution` é um `UPDATE` cego. Dez transições alcançáveis que a máquina
declarada não admite, incluindo `canceled → running` e
`succeeded → dead_letter`.

### [R11] P2 — Backoff não é full jitter e colapsa no teto · **PENDENTE**

O resultado vive em `[raw, raw*1.25]`, não em `[0, raw]`. O teto absoluto de
30s torna o jitter irrelevante a partir da 5ª tentativa. **No teto, o jitter é
eliminado por completo**: toda tentativa a partir da 10ª retorna exatamente
3600.0, sincronizando a frota inteira — a tempestade que o jitter deveria
evitar. RNG e relógio não são injetáveis; `backoff_seconds` não tem nenhum
teste.

### [R12] P2 — `trigger.schedule` sem produtor · **PENDENTE**

`emit_schedule_trigger` não tem nenhum chamador. O bloco aparece na UI, valida,
publica, ativa e reporta `active` — e nunca dispara. Falha silenciosa de
configuração.

---

## G — RAG, dados e governança de modelo

Documentação oficial consultada em **2026-08-06**
(`developers.openai.com/api/docs`, para onde `platform.openai.com/docs`
redireciona).

### [G-01] P0 — Base model de fine-tuning inválido e plataforma em wind-down · **PENDENTE**

Default do projeto: `gpt-4o-mini-2024-07-18`. A lista oficial de bases para SFT
hoje é `gpt-4.1-2025-04-14`, `gpt-4.1-mini-2025-04-14`,
`gpt-4.1-nano-2025-04-14`. **`gpt-4o-mini` não aparece.**

Marcos publicados: **7 mai 2026** organizações que nunca treinaram são
bloqueadas; **2 jul 2026** organizações sem inferência em modelo fine-tuned nos
últimos 60 dias são bloqueadas; **6 jan 2027** ninguém cria job novo. Inferência
sobrevive até o base model ser desligado.

Agravante de custo: `create_finetuning_job` faz dois `client.files.create`
**antes** do `jobs.create`, então cada tentativa faz upload que nunca vira
treino.

**Correção:** capability discovery por provider (available / deprecated /
unavailable_to_new_accounts / bases suportadas), validar o modelo **antes** do
upload, e manter dataset/avaliação/export funcionando sem depender de job.

### [G-02] P0 — `build_model_backed_executor` é inalcançável · **PENDENTE**

Nenhuma rota, nenhum CLI, nenhum worker o chama. A única rota de avaliação passa
`execution_mode=SIMULATION` fixo. Como o gate exige `model_backed` +
`executor_kind`, **nenhum run promovível pode existir** — o portão está soldado
fechado, e a documentação descreve um procedimento operacional que não existe.

### [G-03] P0 — Título de documento entra no prompt sem neutralização · **PENDENTE**

`neutralize_source_content` é aplicado ao corpo; o título é copiado cru e
injetado em `format_sources_for_prompt`. `detect_injection` na ingestão roda só
sobre o conteúdo. Um documento com corpo benigno e título
"Ignore as instruções anteriores…" sequestra a instrução.

### [G-04] P1 — `LIMIT 400` corta o corpus antes do scoring · **PENDENTE**

E o corte é por `priority DESC, document_id DESC`, não por relevância. Acima de
400 chunks, documentos de prioridade 0 e id baixo ficam invisíveis para
qualquer consulta, sem erro e sem log.

**Baseline medido** (400 chunks, 1536d): lexical 8,05 ms; `json.loads` dos
vetores 103,18 ms; cosine puro-Python 47,22 ms; total híbrido 159,40 ms —
contra ~1–3 s de uma chamada de resposta.

**Decisão:** HNSW/pgvector **não se justifica**. O gargalo medido é
serialização (65%), não índice. Critério para reabrir: corpus mediano por
empresa > 5.000 chunks **e** p95 de retrieval > 300 ms em produção.

### [G-05] P1 — Identidade do vetor não é verificada · **PENDENTE**

`_dimensions = 1536` fixo, independente de `EMBEDDINGS_MODEL`; `dimensions`
nunca é enviado. `embedding_model` é gravado por chunk e até selecionado na
query, mas nunca comparado com o provider corrente. Trocar 3-small → 3-large
faz `cosine_similarity` devolver 0.0 pelo `len` diferente — degradação
**silenciosa** para lexical. Com dimensões iguais e espaços diferentes, devolve
número plausível e sem sentido.

### [G-06] P1 — `ai.extractStructured` nunca extrai nada · **PENDENTE**

Copia chaves do contexto. As cinco opções do catálogo
(`nome`, `servico_desejado`, `data_preferida`, `orcamento`, `urgencia`) não
existem como chave de contexto em lugar nenhum — o payload canônico é redigido
por allowlist e não as inclui. Retorna `{}` em 100% das execuções.

### [G-07] P1 — `ai.classifyOrRoute` não classifica · **PENDENTE**

Repete `context["classification"]`. E `target_label` é string livre enquanto
`classification` vem de um enum fechado de 7 valores: qualquer rótulo fora do
enum roteia pelo ramo falso para sempre, sem aviso na validação.

### [G-08] P1 — `ai.composeMessage`: objetivo entra como fala do cliente · **PENDENTE**

`message=objective` — o slot da mensagem **do cliente**. Esse mesmo valor
dirige o RAG. O modelo é instruído a responder ao objetivo do operador como se
um cliente o tivesse escrito. `use_knowledge` não tem nenhum consumidor: o
toggle não controla nada e quem o desliga continua pagando retrieval.

### [G-09] P1 — Dedup é O(N²) real · **PENDENTE**

**Baseline medido** (10% duplicatas): N=1.000 → 0,163 s; N=2.000 → 0,654 s;
N=4.000 → 2,719 s. Quadrático exato, 0,33 µs/par. Extrapolando para o limite de
importação declarado (`IMPORT_MAX_MESSAGES_PER_BATCH=20000`): ~66 s. Para
50.000: ~7 min. `build_dataset_version` é síncrono, chamado por rota admin.

**Decisão:** banding/multi-index **não se justifica** em N ≤ 20k. O prefiltro
SimHash já funciona — o Jaccard disparou 29 vezes em 7,9 milhões de pares
(0,0004%), então o custo não está na confirmação e sim no laço bruto. Cachear o
`int(hex)` dá 3,4× na primitiva (~66 s → ~49 s). Critério para adotar banding:
mediana de exemplos aprovados por empresa > 30.000 **e** p95 de
`build_dataset_version` > 60 s.

### [G-10] P2 — Rota de promotion-gate não informa a proveniência que ela exige · **PENDENTE**

`read_promotion_gate` não passa `model_ref`, `dataset_version_id` nem
`finetuning_job_id`, e o gate responde com as três razões de "alvo não
especificado". O endpoint devolve `passed:false` em 100% das chamadas, e o
operador não distingue "o modelo regrediu" de "a rota não passa parâmetro".

### [G-11] P2 — Embeddings pagos com `ENABLE_VECTOR_SEARCH=false` · **PENDENTE**

A flag guarda a leitura e não a escrita. Cada documento ingerido gera chamada
faturada cujo vetor nunca é lido.

### [G-12] P2 — Acoplamento vendor-specific · **PENDENTE**

`from openai import ...` no topo de `finetuning_service`; `OpenAIProvider`
importado concretamente em 6 serviços. O padrão correto já existe no repositório
(`EmbeddingProvider(Protocol)` + factory). Dado o G-01, trocar de provider
deixou de ser hipótese e virou requisito com prazo.

---

## INF — Infraestrutura

### [INF-01] P0 — Redis: pacote ausente, serviço ausente, host referenciado · **PENDENTE**

`redis` não está em `requirements.txt`; não existe serviço `redis` no compose;
o `automation-worker` aponta para `redis://redis:6379/0` por default. O fallback
**não** é silencioso — levanta `SharedStateError` —, mas ninguém captura, e
`evaluate_rate_limit` roda fora do `try/except` do middleware. Primeira
requisição a `/admin/*`, `/messages/incoming` ou `/webhooks/whatsapp` → 500.
Pior: `broadcast_event` é chamado **depois** do commit, então a mensagem é
processada, a resposta é 500, o WhatsApp reentrega e o efeito duplica.

### [INF-02] P0 — `PLATFORM_SESSION_SECRET` não chega a nenhum serviço · **PENDENTE**

Não está no `environment:` de nenhum serviço e não há `env_file:`. O fallback
usa `ADMIN_SUPERADMIN_TOKEN` (a chave HMAC vira credencial bearer viva) ou, na
ausência dele, a string pública `"project-wapp-platform-dev-secret"` —
**qualquer um forja um cookie de superadmin**. E o readiness marca isso como
blocker em produção, então `validate_runtime_environment("api")` derruba o
container: crash loop, e o serviço `admin` nunca inicia.

### [INF-03] P0 — O serviço `api` não recebe nenhuma variável `AUTOMATION_*` · **PENDENTE**

Ligar `AUTOMATION_ENGINE_ENABLED=true` liga só o worker. Toda rota
`/admin/automations/*` continua 503 e o outbox nunca é alimentado. O worker roda
um loop sobre uma fila que nunca recebe nada.

### [INF-04] P0 — `validate_runtime_environment` derruba os workers no boot · **PENDENTE**

Roda o readiness **inteiro** em todo processo, mas o compose dá env diferente
para cada um. Medido: o `automation-worker` é bloqueado por `admin_auth`,
`rate_limiting` e `reminder_worker` — ou seja, por não ser um worker de
lembretes. `RuntimeError` → exit ≠ 0 → `restart: unless-stopped` → crash loop
permanente nos três serviços. Um `docker compose up` não sobe a stack.

### [INF-05] P0 — Readiness declara Redis `pass` sem verificar nada · **PENDENTE**

`shared_state_is_process_local()` só compara a **string** de configuração.
Escrever `redis` basta para `scale_ready`, `/health/ready` 200 e container
`healthy`. E `/health` não está no escopo de rate limit, então os healthchecks
são os únicos endpoints que continuam funcionando enquanto todo tráfego real dá
500. `tests/test_readiness_platform_checks.py::test_redis_shared_state_passes`
cimenta o comportamento errado.

### [INF-06] P0 — Zero cobertura contra PostgreSQL real · **CORRIGIDO (parcial)**

O ramo `FOR UPDATE SKIP LOCKED` nunca era executado. Corrigido o acesso
(L-03) e os dois defeitos que ele revelou (L-01, L-02). **Falta** o CI com
service container e os testes multi-processo.

### [INF-07] P0 — `automation-worker` sai com código 0 e reinicia para sempre · **PENDENTE**

Com o motor desligado (default), `main()` retorna imediatamente e
`restart: unless-stopped` reinicia independentemente do código de saída.
`init_db()` roda **antes** de qualquer checagem, então cada reinício e cada
healthcheck (30s) executa a bateria de migrations completa contra o Postgres
compartilhado. O `return` precede o `if args.validate_only`, então o healthcheck
aprova o container justamente quando ele não faz nada.

### [INF-08] P1 — Migrations sem lock, executadas por 4+ processos em paralelo · **PENDENTE**

Nenhum `pg_advisory_lock`. `--workers 2` mais dois workers mais o healthcheck
executam DDL simultânea. `CREATE INDEX IF NOT EXISTS` concorrente no Postgres
produz `tuple concurrently updated` e deadlock — falha intermitente só sob carga
de deploy.

### [INF-09] P1 — `.dockerignore` não exclui `node_modules` · **PENDENTE**

`COPY frontend/automation-studio/ ./` despeja os `node_modules` do macOS por
cima do `npm ci` da linha anterior. Binários `darwin-arm64` numa imagem linux.

### [INF-10] P1 — `requirements.txt` sem versão fixada · **PENDENTE**

Nenhum `==`. Duas imagens do mesmo commit contêm dependências diferentes, e o
`pip-audit` do CI resolve versões que não são as da imagem — o "sem
vulnerabilidades" não fala sobre o artefato entregue.

### [INF-11] P1 — Gate de lint decorativo; Docker só em push para main · **PENDENTE**

Medido: `--select F821,F822,F823,E9` (o do CI) → **0 achados**. Ruff completo →
**245**, sendo **163 em arquivos desta rodada** e 82 legados. Um PR que quebre o
Dockerfile é mergeado verde e só falha depois, em main. O smoke usa
`ENVIRONMENT=test`, que desliga o production guard — INF-02 e INF-04 são
invisíveis para o CI **por construção**.

### [INF-12] P1 — Teste do bundle é pulado no CI · **PENDENTE**

O job `backend` não constrói o bundle, então a varredura de credenciais no
artefato é skipada; o job `frontend` constrói mas não roda pytest. A varredura
**nunca executa**. E o `COPY --from=studio` só acerta o caminho por clamping da
raiz: mudar o `WORKDIR` quebra o build silenciosamente.

### Notas adicionais

- `ALLOW_UNTRUSTED_TENANT_HINTS` tem default **inseguro** (`true`) e o compose
  só o desliga no serviço `api`.
- `PII_PSEUDONYMIZATION_KEY` ausente nos dois workers → pseudônimos divergentes
  entre processos.
- Sem graceful shutdown: nenhum handler de `SIGTERM`, nenhum `stop_grace_period`.
  Um job com lease de 60 s é morto no meio e só volta à fila quando o lease vence.

---

## Correções da revisão independente

Uma revisão independente conferiu o relatório da rodada anterior contra o
código e encontrou duas divergências. Ambas eram minhas.

### 1. T-03 estava marcado CORRIGIDO sem ter sido corrigido

O exploit continuava reproduzível: `POST /admin/calendar/bookings/{id}/cancel`
com token do tenant A e id do tenant B respondia **200**, e o booking de B
terminava `cancelled`.

Causa do erro de relato: a conversão em massa de handlers usou uma
substituição sobre `company_id: int | None = Query(default=None)`. As cinco
rotas do T-03 **não têm parâmetro de empresa nenhum** — nunca foram tocadas. E
`tests/test_cross_tenant_omission.py`, que eu apresentei como cobertura de
T-01 a T-04, não tem um único teste para elas.

Marcar o item como corrigido sem executar nada é exatamente o que
`.claude/rules/testing-and-release.md` proíbe: *"`NÃO EXECUTADO` é uma resposta
legítima e honesta. 'Passou' sem ter rodado, não."*

**O que mudou no processo, além do código:** o teste estrutural
`tests/test_tenant_scope_consumption.py` passa a verificar que cada handler
mutável tenant-scoped **consome** o escopo — não apenas que existe uma política
declarada para o caminho. O teste anterior respondia "esta rota deveria ser
escopada?"; o novo responde "o handler realmente usa o escopo?". Foi ele que
encontrou o T-05 e os 8 handlers de billing.

### 2. As contagens do relatório estavam erradas

Relatei 24 pendências e 7 P0. A contagem programática sobre o próprio arquivo
dava **41 pendências e 16 P0**. Eu contei apenas os achados que havia citado no
texto do relatório, não os do plano.

Contagem correta, recontada a cada atualização:

| Momento | Achados | Corrigidos | Pendentes | P0 pendentes |
|---|---:|---:|---:|---:|
| Fim da rodada anterior (real) | 51 | 10 | 41 | 16 |
| T-03 reaberto | 51 | 9 | 42 | 17 |
| **Agora** | **52** | **12** | **40** | **16** |

O total subiu para 52 porque o T-05 ganhou entrada própria ao ser corrigido.

Comando da recontagem, para não depender de leitura manual:

```bash
python - <<'EOF'
import re, pathlib
from collections import Counter
h = re.findall(r"^### \[([^\]]+)\] (P\d) — (.+?) · \*\*([^*]+)\*\*",
               pathlib.Path("docs/closure-plan.md").read_text(), re.M)
pend = [x for x in h if x[3].startswith("PENDENTE")]
print("total:", len(h), "| pendentes:", len(pend),
      "| P0 pendentes:", sum(1 for x in pend if x[1] == "P0"))
EOF
```

---

## Ondas

| Onda | Conteúdo | Estado |
|---|---|---|
| 0 | Baseline, agentes, regras, auditoria, plano | **CONCLUÍDA** |
| 1 | Escopo tenant, path/body, RBAC por membership | **EM ANDAMENTO** — T-01..T-06 corrigidos e verificados nos dois backends; aguardando revisão independente antes de fechar |
| 1a | Backend PostgreSQL viável (L-01..L-05) | **CONCLUÍDA** |
| 1b-gate | Cobertura real dos gates (mypy/Bandit) | **CONCLUÍDA** — 66 → 74 módulos |
| 1b | T-05, T-07..T-11 | PENDENTE |
| 2 | Efeito indeterminado, outbox, fencing, cancelamento, gates, retry | PENDENTE |
| 3 | Produtores de evento, scheduler, nós de IA reais, lembretes | PENDENTE |
| 4 | Compose, Redis, CI com PostgreSQL, multiworker, observabilidade | PENDENTE |
| 5 | RAG (decidido: sem HNSW), dedup (decidido: sem banding), avaliação real | PENDENTE |
| 6 | Frontend, documentação, revisão final | PENDENTE |

**Decisões de algoritmo já tomadas, com benchmark:** HNSW/pgvector **rejeitado**
(G-04); banding para dedup **rejeitado** (G-09). Ambos com critério de aceite
publicado para reabrir.

---

## Cobertura dos gates: de 66 para 74 módulos

`checked_modules.txt` não incluía os módulos mais críticos do repositório:
`app/main.py`, `app/api/admin_routes.py`, `app/storage/db.py`,
`app/storage/repository.py`, `app/core/exceptions.py` e os três serviços de
calendário/fila/analytics.

Foi **em `admin_routes.py` que o vazamento cross-tenant viveu** — e o gate não
tinha nada a dizer sobre ele. Um módulo fora do mypy/Bandit é um módulo onde a
ferramenta é silêncio, não aprovação.

Ao incluí-los, apareceram seis defeitos reais, todos corrigidos **na causa**,
sem `# noqa`, `# type: ignore` nem `# nosec`:

| Ferramenta | Achado | Correção |
|---|---|---|
| mypy | `calendar_service.py:164` — `windows` redeclarada no mesmo escopo | anotação duplicada removida |
| mypy | `appointment_queue_service.py:149` — assinatura promete `tuple[int,int,int,str,int]`, corpo devolve 3 elementos | assinatura corrigida para o comportamento real e testado |
| Bandit B106/B105 | `token_uri="https://oauth2.googleapis.com/token"` lido como credencial | extraído para `GOOGLE_OAUTH_EXCHANGE_ENDPOINT` — o nome passa a descrever o endpoint, não o que trafega nele |
| Bandit B110 ×2 | `except Exception: pass` nas estratégias de busca CalDAV | a exceção passa a ser registrada em DEBUG; cair para a próxima estratégia continua correto, engolir o motivo não era |
| Bandit B110 | `db.py::close` engolia falha de rollback antes de devolver a conexão | passa a registrar WARNING; o `putconn` vai para `finally`, porque a conexão precisa voltar de qualquer forma |

**Resultado:** mypy limpo em 74 módulos, Bandit com **0 achados** em 30.355
linhas e **0 `#nosec`**.


---

## Rodada de reabertura da Onda 1 — achados da revisão independente

A revisão independente executou os ataques em vez de ler o código, e o que ela
encontrou não foi "o T-03 voltou": foi que **o guarda construído para impedir a
reincidência não a impedia**, e que outros dois defeitos exploráveis estavam
abertos ao lado.

### [C-01] P1 — o teste estrutural aceitava o T-03 verbatim · **CORRIGIDO**

**Causa raiz.** `_consumes_scope` devolvia `True` assim que existisse um default
`Depends(require_authorized_company_id)`. Nunca verificava se o valor chegava ao
serviço. O contraexemplo é a rota original do T-03, sem uma vírgula alterada.

Mais cinco escapes provados no mesmo detector: `company_id` reatribuído a partir
da query crua; `company_id` derivado do registro carregado; handler `async`
(`AsyncFunctionDef` nunca era visitado); `@router.post(path=...)`; router com
nome que não termina em "router".

**Correção.** O arquivo foi reescrito em torno de uma tese mais honesta: uma AST
não prova dataflow, e o teste não afirma mais que prova. Ele decide três
proposições **negativas** — nunca lido, reatribuído, auditoria vinda do registro
— que quando disparam são sempre defeito. A prova de segurança é comportamental
e mora em `test_cross_tenant_resource_by_id.py`. Sete controles negativos, um
por escape demonstrado, mais cinco controles positivos.

### [A-02] P1 — duas das cinco rotas auditavam com `company_id` NULL · **CORRIGIDO**

**Causa raiz.** `record_booking_outcome_endpoint` e
`record_cancellation_reason_endpoint` chamavam `_record_audit_event` sem
`company_id`. O evento ficava com empresa nula e sumia do filtro de quem
precisava investigá-lo.

O teste que deveria pegar isso passava **por vacuidade**: ele comparava o valor
apenas dentro de um laço sobre os keywords, então um handler sem o argumento
nunca chegava à asserção. E parametrizava 3 das 5 rotas.

**Correção.** As duas rotas passaram a auditar com a empresa autorizada. O teste
exige **presença e valor**, cobre as cinco, e um teste companheiro prova que a
lista parametrizada corresponde aos handlers reais das cinco rotas.

### [B-01 / T-07] P1 — agregados da plataforma legíveis por qualquer viewer · **CORRIGIDO**

Exploração executada: viewer com membership em UMA empresa lia
`project_wapp_total{entity="companies"} 4` e o volume de mensagens, contatos e
perguntas pendentes da plataforma inteira. Não vaza registro; vaza **agregado**,
que entrega o tamanho da carteira e a operação dos concorrentes por diferença.

### [F-01] P1 — `has_role()` decidia com o papel global · **CORRIGIDO**

**Causa raiz.** `principal.has_role("superadmin")` sem `company_id` cai em
`role_in_company(None)`, que devolve `global_role`. Um superadmin global
rebaixado a `viewer` numa empresa continuava recebendo o `external_id` do
provider — o phone_number_id da Meta, o mesmo campo que a rota mascara por ser
sensível. O `POST` da mesma rota negava corretamente; só a decisão de revelar
consultava o papel errado.

**Correção.** `has_role("superadmin", company_id=scope_company_id)`. Regressão
com os três casos: rebaixado (mascarado), viewer comum (mascarado), superadmin
legítimo (revelado — sem este controle positivo, mascarar tudo passaria).

### [A2-a] P1 — `checked_modules.txt` estava falsamente protegido · **CORRIGIDO**

Dava para remover os **oito módulos mais críticos do repositório** — incluindo
`app/api/admin_routes.py`, onde o vazamento viveu — e os cinco testes seguiam
verdes: a lista continuava com mais de 60 entradas e nenhum daqueles nomes
casava com os prefixos. Um limite numérico não protege um módulo; protege um
tamanho.

**Correção.** Conjunto `CRITICAL_MODULES` nomeado (23 módulos), cobertura por
pacote inteiro (`app/api`, `app/storage`, `app/automations`) e um **teste de
mutação por módulo**: remove cada crítico, um por vez, e exige falha.

### [A2-b] P2 — o comando do gate só rodava em bash · **CORRIGIDO**

`MODULES=$(...)` sem aspas depende de word splitting. No zsh do macOS a lista
inteira vira UM nome de arquivo: `mypy: error: Cannot read file '...': File name
too long`. O gate publicado não rodava na máquina de quem devia rodá-lo.
Corrigido com `xargs -0` no CI e no release-verifier, com teste que proíbe o
retorno da forma antiga.

### [L-06] P1 — o SQLite não aplicava integridade referencial · **CORRIGIDO**

Descoberto quando uma fixture nova passou no SQLite e falhou no PostgreSQL com
`violates foreign key constraint`. `PRAGMA foreign_keys` é desligado por padrão
**e por conexão**: a suíte inteira estava cega para violação de chave
estrangeira, que é exatamente a classe de defeito que só aparece em produção.

Ligar parecia custar zero: os 830 testes passaram na primeira tentativa com a
PRAGMA ativa. **A revisão adversarial mostrou que o custo era uma negação de
serviço** — ver I-01. A PRAGMA foi revertida e a ordem correta ficou registrada
no próprio `db.py`: `try/finally` primeiro, PRAGMA depois.

Um teste em `tests/test_tenant_scope_boundaries.py` afirma que a FK está
desligada e **falha quando isso mudar**, para que ligá-la de novo exija olhar
para o I-01 primeiro.

### [D-01] P2 — o gate de lint eram quatro regras · **CORRIGIDO**

Não havia `ruff.toml`. O CI passava `--select F821,F822,F823,E9` na linha de
comando: nome indefinido e erro de sintaxe, o que um compilador já faz. Com a
seleção padrão do Ruff o mesmo código acusava **260 achados**, incluindo **58
diretivas `noqa` que não suprimiam mais nada** e continuavam ali sugerindo que
algo tinha sido analisado e dispensado.

`ruff.toml` versionado, 22 famílias selecionadas, oito códigos fora com o motivo
escrito ao lado de cada um, e teste que proíbe o CI de voltar a escolher regras
na linha de comando. Defeitos reais corrigidos na causa no caminho: `ContextVar`
com default mutável compartilhado pelo processo, `zip()` sem `strict`, dois
`except Exception: pass` engolindo falha de provider, `assert` em produção
montando URL do WhatsApp, binding morto no orchestrator.

**Nenhuma supressão foi adicionada.** O repositório tem 0 `# noqa` novos, 0
`# type: ignore` novos e 0 `# nosec`.

### Pendências abertas nesta área

- **[E-01] P2** — `appointment_queue_service` grava a recusa da oferta e só
  depois valida o booking liberado: um 404 é devolvido sobre mutação já
  comitada. Precisa de dado corrompido para disparar hoje.
- **[B-02] P2** — `conversation_workflow_service:947,998` chama
  `release_booking_slot` e `reschedule_calendar_booking` sem `company_id`. Não
  explorável hoje (a lista de origem é escopada), mas é o mesmo "filtro tardio"
  que o T-03 removeu das rotas admin.
- **[B-03] P2** — `contacts.contact_name` é reescrito sem filtro de empresa.
  Inobservável hoje: todo caminho de leitura usa `contact_company_profiles`.
- **[D-02] P2** — `BLE001`: 52 `except Exception` nus aguardando revisão caso a
  caso. Fora da seleção com motivo escrito, não silenciados.
- **[D-03] P2** — `E501`: 92 linhas acima de 120 colunas. Pertence a um
  formatador; adotar `ruff format` reformataria arquivos que o usuário está
  editando agora.


---

## Segunda revisão adversarial — o que ela quebrou

Dois dos seis achados foram **introduzidos por mim nesta rodada**. Ficam
registrados com essa origem porque é o dado mais útil do relatório.

### [I-01] P0 — escrita que falha esgota o pool do PostgreSQL · **CORRIGIDO**

*Introduzido nesta rodada.* Ligar `PRAGMA foreign_keys = ON` transformou inserts
que antes passavam em silêncio em exceção. E 96 funções de `repository.py` abrem
conexão **sem `try/finally`**: quando `cursor.execute` estoura, `close()` nunca
é alcançado.

No SQLite o coletor de lixo às vezes salva. No PostgreSQL não existe salvação —
`putconn` só acontece dentro de `close()`.

Medido contra PostgreSQL real, `pool max_size=10`:

```
tentativa  1: ForeignKeyViolation   pool_available=1
...
tentativa 10: ForeignKeyViolation   pool_size=10 pool_available=0
tentativa 11: PoolTimeout em 30.00s          requests_waiting=1
leitura seguinte: PoolTimeout — couldn't get a connection after 30.00 sec
```

Dez escritas inválidas, disparáveis por **qualquer operator autenticado de um
único tenant**, derrubam a plataforma para todos os tenants até o processo
reiniciar.

**Mitigação aplicada:** a PRAGMA foi revertida, o que devolve o sistema ao
comportamento anterior. **A causa continua aberta:** o vazamento de conexão
existia antes e continua existindo — qualquer exceção dentro daquelas 96 funções
vaza. A FK apenas tornou o caminho trivialmente alcançável.

**Correção real, ainda não feita:** `try/finally` ou o contexto `transaction()`
nas 96 funções, e só então religar a PRAGMA.

### [E-01] P1 — 500 sobre mutação já comitada · **CORRIGIDO** *(elevado de P2)*

Dois gatilhos, ambos executados. `appointment_queue_service:799` faz
`int(offer["released_booking_id"])` depois de já ter comitado a recusa: a oferta
vai de `pending` para `declined` e a entrada de `active` para
`awaiting_confirmation`, e então a resposta é **500**. O segundo gatilho é
alcançável **pela API pública**, sem tocar no repositório: enfileirar um contato
que nunca passou pela ingestão e responder `waiting` estoura em
`create_outbound_message`.

O operador vê "erro interno", acredita que nada aconteceu, e repete — e cada 500
desses queima uma conexão do pool (I-01).

### [A-01] P2 — o detector novo ainda aceita 8 escapes · **PENDENTE**

*Introduzido nesta rodada.* A reescrita fechou os 6 escapes da primeira revisão
e abriu outros. O mais grave é estrutural: `_resolves_scope_in_body` só verifica
que o **nome** aparece como chamada, então para os 36 handlers que resolvem o
escopo no corpo a proposição é **tautológica** — uma linha solta
`require_authorized_company_id(request)` satisfaz o guarda. `_rebound_names` não
alcança `for`, `with ... as` nem `except ... as`, ao contrário do que a docstring
afirma. E a varredura avalia 60 das 150 rotas: as 80 GET nunca são checadas.

Nenhum handler real explora isso hoje (verificado por AST: 0 de 36 descartam o
resultado). Mas é a terceira vez que um guarda deste arquivo promete mais do que
entrega, e a lição de método é essa.

**Correção:** exigir que o resultado da chamada de escopo seja **ligado a um
nome** e repassado; estender `_rebound_names` a `for`/`with`/`except as`;
incluir GET na varredura; os 8 casos como controles negativos.

### [G-01] P2 — o handler de `TenantScopeError` esconde defeito do servidor · **PENDENTE**

*Introduzido nesta rodada.* `TenantScopeError` vinda de uma **dependência** é
erro do cliente (400). Vinda do **repositório**, no meio de uma requisição já
escopada, é defeito do servidor — e agora responde 400, culpa o cliente, não
emite `http.request.failed` e não dispara alerta.

**Correção:** distinguir a origem. 400 quando a exceção nasce antes do handler;
500 com `request_id` quando nasce depois.

### [D-01] P2 — a migração T-07/T-08 quebrou o dashboard · **PENDENTE**

*Introduzido nesta rodada.* Não é falha de segurança — é a correção certa sem o
consumidor ajustado. `app/platform/assets/app.js:575` usa `Promise.all`, então o
403 em `/admin/observability/summary` rejeita o conjunto inteiro e a tela toda
falha, **inclusive a parte tenant-scoped que o superadmin do tenant tem direito
de ver**. Mesma coisa em `app/ui/pages/system_page.py`.

**Correção:** degradar por bloco, não abortar.

### [B-03] P2 — `contacts.contact_name` atravessa a fronteira · **PENDENTE**

A revisão elevou a evidência: além do `PATCH /admin/contacts/{telefone}`, a
escrita é alcançável pela **ingestão pública** — com a credencial de A, um
`POST /messages/incoming` com o telefone de um cliente de B grava o nome
escolhido por A na linha global. `contact_company_profiles` fica intacto e a
API admin lê de lá, o que contém o raio; mas A consegue criar linha em
`contacts` para qualquer telefone.

### O que a revisão auditou e considera fechado

- **As 5 rotas do T-03: 56 ataques, nenhum passou.** Incluindo `company_id`
  duplicado nas duas ordens, dígitos árabe-índicos (`٢`), `company_id` no body,
  header `X-Company-Id`, e `company_id`/`company_slug` divergentes. Estado
  persistido conferido por SQL cru após cada requisição.
- **F-01 fechado**, inclusive `include_all_companies=true` com rebaixado, e
  multi-membership pedindo bindings da empresa onde é viewer.
- **T-09 fechado**: escopo no SQL, `[]` → `[]`, `read_company` singular com
  corpos idênticos para alheio e inexistente.
- **Rotas `unscoped` fechadas**: as 5 restantes são catálogo estático, exigem
  credencial e não contêm marcador de tenant.
- **Leitura cross-tenant por id**: 360 requisições (18 famílias × 10 ids × com e
  sem `company_id`) com dados marcados — zero vazamentos, zero 500.


---

## Onda 1 — rodada do I-01

### I-01 fechado, com prova em PostgreSQL real

**Causa raiz.** 99 das 107 aquisições de conexão fechavam **apenas no caminho
feliz**: `connection.close()` como última linha. Qualquer coisa que lançasse
antes dela — `cursor()`, `execute()`, `fetchall()`, `commit()`, conversão de
linha — pulava o close. No SQLite o coletor de lixo às vezes salvava; no pool
do PostgreSQL, nunca, porque `putconn` só acontece dentro de `close()`.

**Correção.** Três gerenciadores em `app/storage/db.py`, fonte única do
contrato:

| Gerenciador | Garante |
|---|---|
| `transaction()` | commit no sucesso, rollback no erro, devolução em `finally` |
| `read_connection()` | devolução em `finally`, **sem** commit |
| `connection_scope()` | devolução em `finally`, commit sob controle do chamador |

`except BaseException` e não `Exception`: um `KeyboardInterrupt` no meio de uma
escrita também precisa devolver a conexão, com a transação desfeita.
`PostgresConnectionWrapper.close()` ficou idempotente — devolver duas vezes é
**pior** que vazar, porque corrompe a contabilidade do pool em vez de só
desperdiçar um recurso. A revisão confirmou: sem a guarda, o segundo `putconn`
levanta `ValueError` dentro de um `finally` e **mascara a exceção original**.

**Inventário.** 99 aquisições inseguras → **0**. As 3 restantes estão dentro
dos próprios gerenciadores, com `try/finally`.

**Prova (PostgreSQL real, `max_size=2`).** Experimento controlado: reintroduzi
o padrão vazante e reexecutei.

```
padrão antigo:  tentativa 2 — nenhuma conexão disponível {'size': 2, 'available': 0}
                psycopg_pool.PoolTimeout: couldn't get a connection after 30.00 sec
com a correção: 5 passed
```

A revisão independente mediu o mesmo por outro caminho: `aberturas=20
fechamentos=20` em 20 escritas inválidas, e nenhuma
`current transaction is aborted` em 6 ciclos falha→leitura.

### L-06 fechado — FK ligada nos dois backends

`PRAGMA foreign_keys = ON` voltou, agora que o vazamento está fechado. O teste
temporário que exigia `enabled == 0` foi substituído por um que exige
`enabled == 1`, mais INSERT órfão, UPDATE órfão, e o teste que amarra os dois
achados: **a violação de FK não pode levar a conexão junto**.

### E-01 fechado — validar antes da primeira escrita

O ramo `declined` comitava a recusa da oferta e a mudança da entrada e **só
então** convertia `released_booking_id` e procurava o booking. Agora as
pré-condições são verificadas antes da primeira escrita.

O caso "id inexistente" saiu da matriz de testes porque deixou de ser
construível: com a FK ligada, criar a oferta apontando para um booking que não
existe é recusado pelo banco. O que continua alcançável — booking de **outro
tenant** — virou teste.

### Achados novos da revisão de ciclo de vida

| ID | Sev | Estado |
|---|---|---|
| **D-01** aninhamento de conexão em `/health/ready`, **sem autenticação** | alta | **CORRIGIDO** |
| **D-03** cursor sobrevivia ao bloco, operando sobre conexão já devolvida | média | **CORRIGIDO** |
| **D-05** `close()` residual dentro do `with` (resíduo da minha conversão) | baixa | **CORRIGIDO** |
| **D-09** pool global criado sem lock: N threads → N pools, N−1 vazam para sempre | alta | **CORRIGIDO** |
| **D-10** E-01 fechado só para `released_booking_id`; `waiting` e `declined/ask` ainda comitavam | média-alta | **CORRIGIDO** |
| **D-11** o guarda estrutural aceitava 6 de 7 aquisições inseguras | média | **CORRIGIDO** |
| **D-13** `sqlite3.connect` fora de `finally` no migrador | baixa | **CORRIGIDO** |
| **D-02** `init_db()` sem advisory lock: dois processos colidem | alta | **PENDENTE** |
| **D-12** `ScopedCursor` tem 3 desvios; `db.transaction()` sem guarda | baixa | **PENDENTE** |
| **D-14** sessão pytest morta deixa banco órfão | baixa | **PENDENTE** |
| **D-04** `commit()` após erro engolido diverge SQLite/PostgreSQL | média | **PENDENTE** |
| **D-06** read-modify-write sem árbitro em 4 funções | baixa | **PENDENTE** |
| **D-07** `sqlite3.connect` sem `finally` no migrador CLI | baixa | **PENDENTE** |
| **D-08** readiness abre TCP fora do pool a cada sonda | info | **PENDENTE** |

**D-01** era o mais grave dos novos: `get_operational_hygiene_summary` mantinha
a conexão externa aberta enquanto `get_operational_snapshot` abria outra, uma
por empresa placeholder. `/health/ready` chega ali **sem autenticação**, então
algumas sondas concorrentes do load balancer esgotavam o pool. O snapshot passou
a ser buscado fora do bloco.

**D-03** era um use-after-return: o cursor de `sql_support.transaction()`
continuava funcionando depois do bloco, sobre uma conexão física já devolvida ao
pool — que outra requisição pode ter recebido. Isso é escrita de um tenant
dentro da transação de outro, sem nenhum erro visível. O cursor agora se
invalida na saída do bloco.

### Banco de teste por processo

`/tmp/project_wapp_test.db` era um caminho fixo, apagado na importação do
conftest. Duas sessões simultâneas usavam o mesmo arquivo e a segunda apagava o
banco da primeira no meio da execução — **146 falhas em massa nesta própria
rodada**, em testes sem relação entre si, que sumiam ao rodar o arquivo
sozinho. Agora o nome carrega o PID e `pytest_sessionfinish` remove só o banco
da própria sessão. Duas sessões paralelas: `69 passed` e `34 passed`, zero
bancos residuais.

### Pendências desta onda

- **D-02** (alta) — `init_db()` sem `pg_advisory_lock`. Dois processos subindo
  contra um banco novo colidem com `UniqueViolation` em `schema_migrations`.
  Medido pela revisão com barrier e duas threads.
- **G-01, D-01(dashboard), A-01, B-03, B-02, T-11, A6, A7, A8** — não alcançados
  nesta rodada.


---

## Segunda rodada da revisão de ciclo de vida

O revisor confirmou I-01, D-01, D-03, D-05 e L-06 fechados, com medição:
profundidade de aquisição **1** em toda a suíte nos dois backends
(`entradas=9419 saidas=9419 diferenca=0`), FKs idênticas entre SQLite e
PostgreSQL (14 de cada lado), e **0 regressões** na conversão em massa das 98
funções. E encontrou mais quatro coisas.

### [D-09] alta — o pool era criado sem lock · **CORRIGIDO**

`_get_postgres_pool()` fazia `if _POSTGRES_POOL is not None` sem lock. Os
handlers síncronos do FastAPI rodam num threadpool, então no cold start e a
cada rolling update as primeiras requisições concorrentes entravam juntas.
Medido: **8 threads → 8 pools distintos**; `close_postgres_pool()` alcançava
apenas o último e os outros sete seguravam backends até o processo morrer. Num
PostgreSQL gerenciado isso consome slots de `max_connections` de forma
permanente. Corrigido com double-checked locking, e `close_postgres_pool()`
passou a usar o mesmo lock.

### [D-10] média-alta — o E-01 estava fechado pela metade · **CORRIGIDO**

Eu tinha validado `released_booking_id` e declarado E-01 fechado. Os ramos
`waiting` e `declined/ask` continuavam comitando antes do envio de
acompanhamento, que falha quando o contato não existe —
`outbound_messages.contact_id` é chave estrangeira e `enqueue` não exige que o
contato exista. Medido nos dois backends:

```
antes:  offer.status='pending'  responded_at=nao  entry.status='active'
        ForeignKeyViolation em outbound_messages_contact_id_fkey
depois: offer.status='declined' responded_at=SIM  entry.status='awaiting_confirmation'
```

No ramo `declined` era pior que um 500: `offer_released_booking_slot()` nunca
rodava, então o horário ficava marcado como liberado e **nunca era
reofertado** — a fila travava sem ninguém perceber.

A validação do contato passou a acontecer antes de qualquer escrita, para os
três ramos.

### [D-11] média — o guarda que eu escrevi aceitava 6 de 7 escapes · **CORRIGIDO**

O detector exigia apenas que existisse *algum* `try/finally` na função. Um
`try/finally` não relacionado fazia passar uma função **sem `close()` nenhum**.
E `db.get_connection()` — chamada por atributo, a forma mais provável numa
regressão real — não era reconhecida como aquisição. Além disso, 12 módulos com
SQL estavam fora da lista escrita à mão.

Agora a verificação é **posicional** (a aquisição precisa estar dentro do `try`
ou do `with`, não apenas na mesma função), reconhece `db.get_connection`,
`sqlite3.connect` e `psycopg.connect`, e a lista de módulos é **descoberta**, não
escrita. A forma canônica do próprio gerenciador — adquirir e então
`try/finally` — é reconhecida explicitamente.

### [D-13] baixa — `sqlite3.connect` fora de `finally` no migrador · **CORRIGIDO**

Padrão I-01 verbatim, agora sob `closing()`.

### Pendências desta rodada

- **[D-02] alta** — `init_db()` sem `pg_advisory_lock`. Medido com barrier e 4
  threads sobre banco vazio: 3 estouram `UniqueViolation` em `pg_type`, 1
  termina. Não corrompe (tudo numa transação, o perdedor faz rollback inteiro),
  mas num rolling update os pods perdedores entram em CrashLoopBackOff até o
  vencedor terminar. **É o maior risco operacional restante.**
- **[D-12] baixa** — `ScopedCursor` tem três desvios (`cursor._cursor`, método
  capturado dentro do bloco, e a conexão de `db.transaction()` sem wrapper).
  Nenhum é explorado pelo código de hoje — o defeito é o guarda prometer o que
  não entrega.
- **[D-04] média** — `commit()` após erro engolido preserva no SQLite e descarta
  tudo no PostgreSQL.
- **[D-06] baixa** — read-modify-write sem árbitro em 4 funções.
- **[D-08], [D-14]** — info/cosmético.
