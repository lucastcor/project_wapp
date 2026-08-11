# Plataforma SaaS multiempresa

Este documento descreve os subsistemas introduzidos para operar o Project WAPP
como plataforma multiempresa com isolamento forte, recuperação factual
governada e pipeline de fine-tuning auditável.

## Princípios aplicados

1. **Fato mutável não vive no modelo.** Catálogo, preço, horário, política e
   disponibilidade vêm de `company_context` publicado, da camada RAG ou de
   serviços determinísticos.
2. **Ação com efeito externo não é simulada.** Reserva, cancelamento e envio
   continuam em serviços verificáveis. A avaliação reprova resposta que afirma
   ter executado uma ação sem que a ferramenta tenha rodado.
3. **Fine-tuning aprende comportamento, não fato.** Cenários `financial` e
   `support` — e os aliases em português — nunca entram no dataset.
4. **`business_type` dá preset, não branch.** O comportamento efetivo vem de
   `capabilities` e `policies` resolvidas por empresa.
5. **`company_id` é obrigatório para dado privado.** O filtro entra na query,
   antes de qualquer ranking.
6. **Conteúdo importado é não confiável** até anonimização e revisão humana.

---

## 1. Resolução de tenant e canais

| Componente | Papel |
| --- | --- |
| `app/core/tenancy.py` | `require_company_id` — invariante de escopo |
| `app/storage/channel_repository.py` | `company_channel_bindings` |
| `app/services/tenant_resolution_service.py` | resolução confiável |

Fluxo do webhook:

1. o payload do Meta traz `value.metadata.phone_number_id`;
2. `resolve_channel_tenant` busca o binding ativo desse canal;
3. havendo binding, a empresa vem dele — `company_id`/`company_slug` enviados
   pelo chamador são ignorados;
4. sem binding, em produção a requisição é recusada com `403`.

Fora de produção, e apenas com `ALLOW_UNTRUSTED_TENANT_HINTS=true`, as dicas do
chamador são aceitas e a decisão é registrada em log com
`tenant.resolution.untrusted_hint_used`.

### Payload normalizado do webhook

O payload interno normalizado não carrega assinatura do Meta. Ele é:

- **bloqueado** quando `ENVIRONMENT=production`, `STRICT_RUNTIME_VALIDATION=true`
  ou `ALLOW_NORMALIZED_WHATSAPP_WEBHOOK=false`;
- **autenticado por HMAC** sempre que `WHATSAPP_APP_SECRET` estiver definido.

---

## 2. Company context versionado

`company_context_versions` passa a ser a fonte operacional. Os JSONs em
`app/data/company_contexts/` viram **seeds de bootstrap**.

- estados: `draft` → `published` → `archived`;
- cada versão guarda `content_hash`, autor, nota de mudança e timestamps;
- escrita **nunca** sobrescreve: publicar a versão N arquiva a anterior, o que
  dá rollback por republicação;
- leitura por `get_company_config`, com cache local de 30s e invalidação
  explícita na publicação (o TTL curto cobre as outras réplicas).

Sem contexto publicado:

| Situação | Comportamento |
| --- | --- |
| existe seed JSON com o slug da empresa | bootstrap automático e idempotente |
| não existe seed, ambiente não-produção | template global, marcado `source="global_template"` |
| não existe seed, produção | `CompanyConfigError` — responder com identidade genérica é pior que falhar |

### Capabilities e policies

`app/core/vertical_packs.py` traduz `business_type` em presets. Overrides
explícitos da empresa vencem o preset; chaves desconhecidas são ignoradas.

Capacidades: `scheduling`, `reminders`, `appointment_queue`, `outbound`,
`internal_operation`, `knowledge_rag`, `style_examples`, `conversation_import`,
`finetuning`.

Políticas: `pii_policy`, `handoff_on_low_confidence`,
`handoff_confidence_threshold`, `require_source_for_factual_claims`,
`allow_price_quotes`, `max_knowledge_chunks`, `training_data_retention_days`,
`extra_excluded_training_scenarios`.

A vertical `clinic` aplica `pii_policy=healthcare_strict`, bloqueia cotação de
preço por mensagem e exclui `scheduling` do treino.

---

## 3. Taxonomia

`app/core/taxonomy.py` é a fonte única de `classification`, `scenario_type`,
`customer_profile` e `profile_name`.

- **borda HTTP**: `normalize_or_raise` rejeita valor desconhecido;
- **importação**: `normalize_with_fallback` cai no default documentado e
  reporta o fallback.

O filtro de exclusão de treino passou a operar sobre valores canônicos, então
`financeiro`, `suporte`, `cobranca` e `boleto` são barrados junto com
`financial` e `support`.

`scenario_type_for` deriva o cenário da classificação e só deixa o estágio do
funil sobrepor quando a classificação é comercial — um chamado de suporte não
vira `closing` por causa do estágio.

---

## 4. Knowledge e RAG

```
knowledge_documents → knowledge_document_versions → knowledge_chunks
```

- ingestão idempotente por `external_ref`: mesmo conteúdo não gera versão nova;
- chunking por parágrafo com sobreposição curta;
- `search_text` inclui o título, porque o cliente costuma usar o termo do
  título e não a flexão do corpo;
- busca lexical determinística com match exato + prefixo de 5 caracteres, que
  aproxima flexões do português sem stemmer nem embeddings;
- **filtro por `company_id` na query**, antes do ranking, mais uma verificação
  defensiva no loop de pontuação;
- só entra no runtime documento `approved` e dentro de `valid_from`/`valid_until`.

### Extensão vetorial

`app/llm/embeddings.py` define o contrato `EmbeddingProvider`:

| Provider | Uso |
| --- | --- |
| `none` (padrão) | apenas lexical, zero chamada externa |
| `hashing` | determinístico, exercita o caminho vetorial em teste |
| `openai` | embeddings reais, opt-in por `EMBEDDINGS_PROVIDER=openai` |

Com `ENABLE_VECTOR_SEARCH=true` e um provider ativo, o score vira híbrido
(50% lexical, 50% semântico). Falha do provider externo cai para lexical e
registra `knowledge.retrieval.embedding_fallback` — nunca derruba a resposta.

### Proteção contra injeção

Fonte recuperada é **dado**, nunca instrução:

- `detect_injection` sinaliza o chunk;
- `neutralize_source_content` remove os padrões de instrução e prefixos de
  papel (`system:`) antes de o texto entrar no prompt;
- o prompt declara explicitamente que `[fonte:ID]` é dado.

O conteúdo factual do documento é preservado — só a tentativa de instrução é
removida.

### Ausência de fonte

Sem fonte relevante e com `require_source_for_factual_claims=true`, o prompt
instrui a pedir esclarecimento ou encaminhar para humano, em vez de afirmar.

### Observabilidade

Cada resposta grava `messages.retrieval_json` com `chunk_id`, `document_id`,
scores e `injection_flagged` — **ids e scores, nunca o conteúdo**.

---

## 5. Estilo por perfil

`select_style_examples` tem fallback explícito e previsível:

1. `customer_profile` + `scenario_type`
2. `customer_profile`
3. `profile_name` + `scenario_type`
4. `scenario_type`
5. qualquer exemplo ativo da empresa

O `customer_profile` do contato agora chega ao runtime pelo orquestrador.

`active` (few-shot) e `used_for_training` + `approval_status='approved'`
(fine-tuning) são dimensões independentes.

---

## 6. Importação governada de conversas

```
parse → anonimização → segmentação por sessão → staging
      → candidatos multi-turno → revisão humana → promoção
```

Tabelas: `conversation_import_batches`, `imported_conversations`,
`imported_messages`, `training_candidates`.

- formatos: `csv`, `json` (plano ou aninhado) e `whatsapp_txt`;
- o parser TXT trata multilinha, ruído de sistema e AM/PM; sem
  `agent_authors`, o segundo autor distinto é tratado como atendente — heurística
  documentada e sobrescrevível;
- linha inválida vira `ParseIssue`, não derruba o lote;
- **o staging já guarda texto anonimizado**: conteúdo bruto nunca é persistido;
- o identificador do contato vira `anon_<hash>` com salt por tenant, então a
  mesma pessoa em duas empresas não colide;
- sessões são separadas por `IMPORT_SESSION_GAP_MINUTES`;
- idempotência por `(company_id, batch_key)` e por `candidate_hash`;
  reimportar substitui o staging em vez de duplicar;
- `dry_run` executa o pipeline inteiro sem escrever nada.

Nenhum candidato vira treino sem `POST /admin/training-candidates/{id}/review`
com `approved=true`. A promoção grava `source_reference` apontando para o
candidato de origem.

### Importador legado

`POST /admin/training-import` continua funcionando para pares curados, agora
com taxonomia normalizada, PII anonimizada e `approval_status='pending'` —
nada é liberado para treino automaticamente.

---

## 7. PII

`app/services/pii_service.py` detecta **por valor**, não por menção:

e-mail, telefone, CPF (com dígito verificador), CNPJ (com dígito verificador),
CEP, cartão (Luhn), RG, chave Pix e endereço. A política `healthcare_strict`
acrescenta nome completo, data de nascimento e identificador de saúde.

O relatório informa **quantas** substituições de cada tipo ocorreram, nunca o
valor original.

---

## 8. Ciclo de vida do dataset

`dataset_versions` + `dataset_version_items`.

Estados: `draft` → `reviewing` → `approved` → `frozen` → `superseded`.

- **split agrupado**: a unidade é o grupo (conversa/identidade anonimizada),
  nunca a linha — nenhuma mensagem de uma conversa cai em dois conjuntos;
- `train` / `validation` / `holdout` separados;
- reprodutível por seed: mesma seed produz o mesmo `content_hash`;
- deduplicação exata (hash de conteúdo) **e** near-duplicate (min-hash sobre
  shingles de 4 palavras);
- versão congelada é imutável: `rebuild` e novo `freeze` são recusados;
- congelar uma versão nova marca a anterior como `superseded`;
- `check_split_leakage` reporta grupos e near-duplicates que cruzaram splits.

---

## 9. Avaliação

`evaluation_cases`, `evaluation_runs`, `evaluation_results`.

Dimensões: `factuality`, `forbidden_content`, `source_adherence`, `handoff`,
`action_integrity`, `safety`, `tone`.

- o gerador de resposta é **injetado**; a suíte padrão e a rota administrativa
  usam o gerador offline determinístico, sem chamada externa;
- `compare_runs` compara base, base+RAG e fine-tuned contra o baseline;
- `get_promotion_gate` aplica o critério configurável
  (`FINETUNING_MIN_EVAL_PASS_RATE`) e barra regressão contra o baseline.

---

## 10. Fine-tuning e modelos por empresa

### Readiness bloqueante

`evaluate_finetuning_readiness` verifica: existência da versão, capability
habilitada, volume mínimo, presença de validation, cobertura de cenário,
qualidade, ausência de PII residual, integridade do split, dataset congelado e
gate de avaliação.

`create_finetuning_job` **recusa** criar o job — sem upload de arquivo — quando
há qualquer falha bloqueante, e exige uma `dataset_version` congelada.

### Registro de modelo

`company_models` com estados `candidate` → `shadow` → `active` → `retired`.

- **um** modelo ativo por empresa; ativar aposenta o anterior;
- ativação exige `evaluation_run_id` vinculado;
- `rollback` reativa o modelo anterior ou cai no modelo base;
- `resolve_reply_model` resolve na ordem: modelo ativo da empresa →
  `OPENAI_FINETUNED_MODEL` global (compatibilidade) → modelo base.

Um fine-tune de outra empresa nunca é alcançável.

---

## 11. Contrato de prompt

`app/core/reply_contract.py` é a fonte única do formato. Tanto
`build_finetuning_record` quanto `OpenAIProvider.generate_business_reply`
derivam dele, então o modelo treinado recebe em produção a mesma estrutura de
campos, na mesma ordem, que viu no treino. Campo ausente vira
`não informado` em vez de sumir.

---

## 12. Estado compartilhado

`SHARED_STATE_BACKEND` controla rate limit e barramento SSE:

| Valor | Escopo |
| --- | --- |
| `memory` (padrão) | local ao processo — **só desenvolvimento e testes** |
| `redis` | compartilhado entre réplicas |

O readiness marca `memory` como blocker em produção.

Validação real do adapter Redis:

```bash
SHARED_STATE_BACKEND=redis REDIS_URL=redis://127.0.0.1:6379/0 \
  python3 -m app.tools.preflight_check
```

---

## 13. SSE

`GET /platform/events` exige sessão da plataforma e escopo de empresa.

- viewer/operator precisam de `company_id` ou `company_slug`;
- superadmin sem empresa escolhida assina o escopo global;
- identificadores de contato são mascarados antes de entrar no stream.

---

## 14. RBAC

Toda mutação administrativa exige papel explícito. As rotas de analytics,
onboarding e billing que dependiam apenas da autenticação de router passaram a
declarar `operator` ou `superadmin`.

Auditoria cobre knowledge, importação, revisão de candidato, dataset,
avaliação, modelo e vínculo de canal.

---

## 15. Endurecimento de identidade (rodada 3)

Seis defeitos confirmados no código antes desta rodada e corrigidos com
regressão dedicada em `tests/test_phase_zero_blockers.py`.

### 15.1 Escopo autorizado é a fonte única

`_resolve_company_scope` chamava `repository.resolve_company_id(None, None)`,
que cai em `get_default_company()`. Um principal com uma membership que
omitisse `company_id` operava na **primeira empresa ativa**, não na dele.

Agora o helper apenas lê `request.state.authorized_company_id`, preenchido por
`require_company_scope`. Nenhum handler resolve empresa por conta própria.
`request.state.authorized_company_ids` carrega o conjunto permitido para as
leituras amplas, e o filtro entra na query.

### 15.2 Deny-by-default por política declarada

A allowlist ampla por prefixo deixava `/admin/company-context`,
`/admin/audit-events` e `/admin/system/onboarding` fora da autorização, embora
todas carreguem dimensão de empresa.

`ROUTE_POLICIES` declara, por grupo de rotas, uma de três políticas:

| Política | Significado |
|---|---|
| `unscoped` | sem dimensão de empresa (catálogo estático, observabilidade) |
| `platform_global` | exige superadmin **global** (identidade, criação de empresa, worker) |
| `tenant` | exige empresa autorizada |

Caminho não declarado cai em `tenant` (o mais restritivo) e é registrado.
`test_every_mutating_admin_route_is_covered_by_a_scope_policy` enumera as
rotas mutáveis e falha se alguma ficar sem declaração.

### 15.3 Sessão morre com a credencial

O cookie carregava só `principal_id`. Se o principal fosse desativado ou
removido, `principal_from_session` **caía para principal legado** e a sessão
continuava valendo.

Agora a sessão carrega `token_id` e `auth_epoch` (migration 045). Quatro
eventos a invalidam imediatamente, todos com **401**:

- principal removido;
- principal desativado (`set_principal_active` move a época);
- credencial de origem revogada;
- `bump_auth_epoch` chamado.

`principal_from_session_claims` falha fechada em todos: não há fallback.

### 15.4 Proveniência completa no promotion gate

O gate só comparava quando os dois lados estavam preenchidos. Um run sem
`dataset_version_id` ou sem `finetuning_job_id` passava por "nada a comparar" —
exatamente o caso em que não se sabe o que foi avaliado.

Agora há duas exigências independentes:

1. o chamador declara modelo, dataset e job (`promotion_target_*_not_specified`);
2. o run tem os três preenchidos e idênticos (`evaluation_*_missing`,
   `evaluation_*_mismatch`), além de `executor_kind` presente.

### 15.5 Model-backed exige execução comprovada

`execution_mode = model_backed` era uma afirmação do chamador: qualquer
função injetada produzia um run `promotion_eligible`.

`app/services/evaluation_executor.py` introduz `ExecutionAttestation`, e o modo
passa a ser **consequência** de quem executou:

- `build_model_backed_executor` resolve provider e `model_ref`, chama o modelo
  configurado e falha fechada quando `ENABLE_REAL_MODEL_EVALUATION=false`;
- `build_fake_executor` existe para testes e nasce não promovível em ambiente
  produtivo — `run_evaluation` recusa a execução em vez de registrar prova
  falsa;
- um `Callable` sem atestação é recusado com mensagem explícita;
- a rota `/admin/evaluation/runs` continua **sempre** em `simulation`.

`executor_kind`, `provider` e `attestation_json` vão para o run (migration 047).

### 15.6 Metadados de knowledge pertencem à versão

`title`, `document_type`, `priority`, `valid_from` e `valid_until` viviam na
linha do documento. Editar um rascunho reescrevia o metadado da versão **que
estava no ar**: dava para renomear, repriorizar ou expirar a produção sem
publicar nada.

A migration 046 move esses campos para `knowledge_document_versions`. As
colunas do documento permanecem como **espelho da versão publicada** e só se
movem em `approve_document`. O retrieval lê conteúdo e metadados de
`v = knowledge_document_versions` via `d.current_published_version_id`.
