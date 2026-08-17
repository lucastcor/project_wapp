# Conhecimento, recuperação e ciclo de fine-tuning

Como a plataforma responde com o que a empresa realmente sabe, e como um
modelo ajustado sai de conversas reais até a produção — sem que nenhuma das
duas coisas atravesse a fronteira de um tenant.

> **Estado:** recuperação ligada por padrão (`KNOWLEDGE_RETRIEVAL_ENABLED`);
> busca vetorial e avaliação com modelo real desligadas até haver provider
> configurado.

---

## 1. Por que existem dois caminhos, e não um

| | Recuperação (RAG) | Fine-tuning |
|---|---|---|
| **Responde a** | "o que é verdade nesta empresa?" | "como esta empresa fala?" |
| **Muda em** | segundos (publicar documento) | dias (treinar e promover) |
| **Erro típico** | responder com fato errado | responder com tom errado |
| **Fonte** | `knowledge_documents` | `message_examples` |

Confundir os dois é a origem do pior defeito possível num agente comercial:
tentar ensinar **preço** ao modelo por fine-tuning. O preço muda; o modelo
treinado não. Fato mutável vem do RAG, sempre; o fine-tuning carrega estilo.

---

## 2. Recuperação tenant-safe

### O filtro vem antes da pontuação

`company_id` entra na query, não num descarte posterior em memória. Filtrar
depois significa que o ranking já viu conteúdo de outra empresa — e um bug de
ordenação vira vazamento.

### Funciona sem embeddings

A busca lexical é determinística e roda igual em SQLite e PostgreSQL. Vetorial
é extensão (`ENABLE_VECTOR_SEARCH`), não pré-requisito: exigir um provider
externo para responder com a base da própria empresa acoplaria o atendimento a
um serviço que pode estar fora do ar.

Pesos por tipo de documento — regra de negócio e política de desconto valem
mais que FAQ solta — porque a pergunta do cliente raramente cita o termo exato
da regra que a responde.

### Conteúdo recuperado é entrada não confiável

Um documento é escrito por alguém, e alguém pode escrever
*"ignore as instruções anteriores"*. `knowledge_retrieval_service`
neutraliza padrões de injeção **antes** do texto entrar no prompt. A base de
conhecimento é um vetor de ataque de dentro do tenant.

### Ausência de fonte é um sinal, não silêncio

`has_relevant_source` chega ao contrato de resposta. Abaixo de
`KNOWLEDGE_MIN_SCORE` nenhuma fonte entra, e a política da empresa decide
entre pedir esclarecimento e encaminhar para um humano — em vez de deixar o
modelo preencher a lacuna. Sem esse sinal, "não sei" vira invenção plausível.

### Citações

Cada trecho entra no prompt como `[fonte:<chunk_id>]`, e o rastro fica em
`messages.retrieval_json`. Dá para perguntar, depois do fato, **de onde** uma
resposta específica saiu.

---

## 3. Versionamento de documento

```
knowledge_documents          o documento (identidade estável)
  ├─ current_published_version_id   o que a produção usa
  └─ working_version_id             o que está em edição
knowledge_document_versions  cada versão, com metadados próprios
knowledge_chunks             recorte por versão
```

Publicada e em edição são colunas **separadas** (migration 055). Uma nova
versão em rascunho não tira do ar a que está atendendo: quem edita não derruba
produção por engano, e rollback é apontar `current_published_version_id` para
a versão anterior.

Metadados (título, tipo, prioridade, validade) pertencem à **versão**
(migration 059), não ao documento — senão corrigir um título mudaria
retroativamente o que uma resposta antiga citou.

---

## 4. Proteção de PII

Detectores declarativos com validação real: CPF e CNPJ por dígito
verificador, cartão por Luhn. A versão anterior trocava o caractere `@` e
procurava a palavra "cpf" sem detectar o valor — encontrava a menção e
deixava passar o dado.

Três invariantes:

1. **o valor original nunca é persistido** em staging, candidato ou dataset;
2. **o relatório conta ocorrências por tipo**, nunca guarda o valor;
3. **pseudônimo é irreversível**. `PII_PSEUDONYMIZATION_KEY` é obrigatória em
   produção: sem chave, um pseudônimo derivado só do valor é recuperável por
   enumeração — basta calcular o hash de todos os CPFs possíveis. Isso não é
   pseudonimização, é ofuscação.

---

## 5. Do histórico ao dataset

```
importar conversa  →  candidato  →  revisão humana  →  exemplo  →  dataset
```

| Etapa | Tabela | Garantia |
|---|---|---|
| Importação | `conversation_import_batches`, `imported_conversations`, `imported_messages` | sessões separadas por `IMPORT_SESSION_GAP_MINUTES`; PII removida na entrada |
| Candidato | `training_candidates` | deduplicado; `promotion_state` explícito |
| Aprovação | `message_examples` | idempotente por `source_candidate_id` (índice único) |
| Dataset | `dataset_versions`, `dataset_version_items` | congelado, com split sem vazamento |

### Sessão, e não arquivo

Silêncio maior que `IMPORT_SESSION_GAP_MINUTES` (6 h por padrão) encerra a
conversa. É o que separa dois atendimentos ao mesmo contato em vez de
fundi-los num diálogo que nunca aconteceu — e um diálogo inventado ensina o
modelo a responder a um contexto que não existe.

### Linhagem

Um exemplo aprovado guarda lote, conversa e candidato de origem, além de
`approved_by` e `approved_at`. Sem isso, descobrir que uma importação estava
errada não dá nenhuma forma de achar o que ela contaminou no dataset.

### Deduplicação

Quase-duplicatas são detectadas por distância de edição
(`NEAR_DUPLICATE_MAX_DISTANCE`). Exemplo repetido não é sinal novo — é o mesmo
sinal com peso dobrado, e desbalanceia o treino em silêncio.

### Split sem vazamento

O agrupamento é por `source_group_key`: turnos da mesma conversa nunca caem em
lados opostos de treino e validação. Sem isso o modelo é avaliado contra algo
que já viu, e a métrica fica boa exatamente quando não deveria.

---

## 6. Prontidão: recusar treino que não pode dar certo

`finetuning_readiness_service` bloqueia antes de gastar um job de provider:

| Piso | Padrão | Por quê |
|---|---|---|
| `FINETUNING_MIN_EXAMPLES` | 50 | abaixo disso o ajuste é ruído |
| `FINETUNING_MIN_SCENARIOS` | 3 | um só cenário produz um modelo que só sabe uma conversa |
| `FINETUNING_MIN_EXAMPLES_PER_SCENARIO` | 5 | cenário com um exemplo desbalanceia em vez de ensinar |
| `FINETUNING_MIN_EVAL_PASS_RATE` | 0.7 | promoção exige avaliação aprovada |

Mais: vazamento entre splits e PII residual bloqueiam. O custo do job é pago
mesmo quando o resultado é pior que o modelo base.

---

## 7. Avaliação e promoção

```
evaluation_cases   → o que perguntar
evaluation_runs    → uma execução, com proveniência
evaluation_results → resposta e nota por caso
```

### Simulada nunca promove

`execution_mode` distingue execução **simulada** de **real**, e
`promotion_eligible` só é verdadeiro para a real (migration 054). Sem essa
separação, uma execução simulada podia ser registrada como `finetuned` e
liberar promoção — o portão existiria e não guardaria nada.
`ENABLE_REAL_MODEL_EVALUATION` controla se a chamada ao modelo acontece.

### Portão de promoção

Compara o candidato com a baseline e recusa quando há regressão — geral ou
numa dimensão crítica. Empatar não basta: trocar o modelo tem custo e risco,
e um empate não paga nenhum dos dois.

`ALLOW_PROMOTION_GATE_OVERRIDE` existe porque emergência acontece, e nasce
desligada porque promover às cegas é exatamente o que o portão previne.

---

## 8. Registro de modelo e fallback

`company_models` guarda os candidatos por empresa, com estado do job,
`failure_reason` e a execução de avaliação que autorizou a promoção.

Resolução em runtime, por `model_registry_service.resolve_reply_model`:

1. modelo ativo da empresa;
2. modelo fine-tuned global, se habilitado;
3. modelo base.

Rollback é ativar a versão anterior — o registro é a fonte de qual modelo
responde, e não uma variável de ambiente que exige deploy para mudar.

---

## 9. Configuração

| Variável | Padrão | O que faz |
|---|---|---|
| `KNOWLEDGE_RETRIEVAL_ENABLED` | `true` | liga a recuperação |
| `KNOWLEDGE_MAX_CHUNKS` | `6` | trechos por resposta |
| `KNOWLEDGE_MIN_SCORE` | `0.05` | piso de relevância |
| `EMBEDDINGS_PROVIDER` | `none` | provider de embeddings |
| `ENABLE_VECTOR_SEARCH` | `false` | busca vetorial além da lexical |
| `NEAR_DUPLICATE_MAX_DISTANCE` | `4` | limiar de quase-duplicata |
| `IMPORT_SESSION_GAP_MINUTES` | `360` | silêncio que encerra a sessão |
| `IMPORT_MAX_BYTES` | `5242880` | teto do arquivo importado |
| `FINETUNING_MIN_EXAMPLES` | `50` | piso de exemplos |
| `FINETUNING_MIN_EVAL_PASS_RATE` | `0.7` | aprovação mínima |
| `ENABLE_REAL_MODEL_EVALUATION` | `false` | avaliação chama o modelo |
| `ALLOW_PROMOTION_GATE_OVERRIDE` | `false` | promover sem passar no portão |
| `PII_PSEUDONYMIZATION_KEY` | — | **obrigatória em produção** |

---

## 10. O que não foi verificado com credencial real

Nenhum provider externo foi exercitado nesta implementação. Embeddings,
fine-tuning e avaliação com modelo real estão **completos em código e
testados com mocks**, mas nunca chamaram OpenAI. Antes de ligar em produção,
ver a lista de ativação no relatório da Release 1.
