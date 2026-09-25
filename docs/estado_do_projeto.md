# Estado do projeto

> **Frente de execução de 13/09/2026:** [piloto com pequenos clientes](piloto/INICIO.md), com [checkpoint atual](piloto/ESTADO.md). A revisão encontrou bloqueios conversacionais/transacionais apesar da suíte verde. O plano distingue correções a fazer, evidência local e homologação real. Os trechos abaixo permanecem como histórico e não substituem esse checkpoint.

> Atualização de 08/09/2026: integração Qwen/Ollama, fila durável do webhook, configuração local/produção e readiness dos workers foram implementados. Veja o [guia atualizado](local_qwen.md). Os registros abaixo preservam o estado e as evidências da análise anterior.

Onde o Project WAPP realmente está, o que sustenta cada afirmação, e o que
ainda não foi provado. Escrito para quem chega agora e precisa distinguir
"implementado" de "funcionando em produção" — a distância entre os dois é onde
moram as surpresas caras.

**Data:** 2026-09-05 · **Branch de origem:** `release-1/finalizacao` · **Alvo:** `main`

---

## 1. Leia isto antes de confiar em qualquer coisa

Três categorias, e elas não são intercambiáveis:

| Categoria | O que significa | Como conferir |
|---|---|---|
| **Provado** | existe teste automatizado que falha se quebrar | rode `pytest` |
| **Implementado** | o código está completo e testado com mock | leia o teste, veja o mock |
| **Não verificado** | nunca tocou o serviço real | nada aqui prova que funciona |

Nenhuma credencial externa foi exercitada em nenhum momento. Stripe, Meta
WhatsApp, OpenAI, Google Calendar e Vercel estão na terceira categoria. Isso
não é uma ressalva de rodapé: é a diferença entre um piloto que funciona e um
que descobre o problema com cliente na linha.

---

## 2. O que o produto faz

### Atendimento conversacional
Mensagem recebida → classificação de intenção → decisão de ação → resposta
gerada, com memória por contato, estágio de pipeline comercial e handoff para
humano quando a IA não deve decidir sozinha. A resposta do responsável volta
transformada em mensagem para o cliente.

O ponto de entrada é `app/core/orchestrator.py`. Ele é síncrono e dono do
atendimento: automação, billing e telemetria acontecem **depois**, e nenhum
deles pode derrubar a conversa.

### Automação visual (Automation Studio)
Editor em canvas com 25 nós, motor de execução durável e histórico passo a
passo. O que o torna confiável não é o editor, é o que está embaixo:

- **outbox transacional** — o evento entra no mesmo commit do fato que o
  originou. Não existe janela em que a mensagem foi salva e o gatilho se perdeu.
- **idempotência em quatro níveis** — `event_key` por empresa, `enrollment_key`
  por fluxo, `effect_key` por efeito externo, `window_key` por janela de agenda.
  Reentrega não duplica execução; reentrega de efeito não manda a mensagem duas
  vezes.
- **version pinning** — uma execução em andamento continua no grafo em que
  começou. Publicar uma versão nova não muda o comportamento de quem já está no
  meio do fluxo.
- **portões humanos** — a execução pausa aguardando aprovação e retoma no ponto
  exato, não do início.
- **retry com backoff e dead-letter** — falha transitória tenta de novo, falha
  persistente para de queimar recurso e fica visível.

Detalhes em [`automation_studio.md`](automation_studio.md).

### Conhecimento e recuperação (RAG)
Documentos versionados, com **versão publicada e versão em edição separadas**:
editar não derruba o que está atendendo. A recuperação filtra por empresa
dentro da query — nunca por descarte posterior, porque filtrar depois significa
que o ranking já viu conteúdo de outra empresa.

Conteúdo recuperado é tratado como **entrada não confiável**: alguém de dentro
do tenant pode escrever "ignore as instruções anteriores" num documento, e os
padrões de injeção são neutralizados antes do texto entrar no prompt.

Ausência de fonte relevante é um sinal explícito (`has_relevant_source`), não
silêncio — senão "não sei" vira invenção plausível.

### Proteção de PII
Detecção com validação real: CPF e CNPJ por dígito verificador, cartão por
Luhn. O valor original nunca é persistido em staging, candidato ou dataset, e a
pseudonimização é irreversível — com chave obrigatória em produção, porque um
pseudônimo derivado só do valor é recuperável por enumeração.

### Ciclo de fine-tuning
Importação de histórico → candidato → revisão humana → exemplo → dataset →
avaliação → promoção. As garantias que importam:

- **split sem vazamento**: turnos da mesma conversa nunca caem em lados opostos
  de treino e validação;
- **execução simulada nunca promove**: um teste de avaliação não pode liberar
  um modelo para produção;
- **o portão recusa regressão**, inclusive empate — trocar modelo tem custo e
  risco, e empate não paga nenhum dos dois;
- **prontidão bloqueia antes do job**: o provider cobra mesmo quando o
  resultado sai pior que o modelo base.

Detalhes em [`rag_e_finetuning.md`](rag_e_finetuning.md).

### Multi-tenancy
Identidade canônica: `saas_users` + `company_memberships` + sessões opacas, com
papéis owner/admin/operator/viewer. Escopo de empresa **deny-by-default** por
rota: uma rota nova sem política declarada é recusada, não liberada.

Desde a migration 067, o isolamento também é garantido pelo **banco**: as 26
tabelas com `company_id` recusam referência cruzada por constraint no
PostgreSQL e por trigger no SQLite. Um bug de aplicação passa a esbarrar no
banco antes de virar vazamento.

---

## 3. Como isso foi construído

Doze commits, cada um com uma decisão que vale conhecer.

| Commit | Decisão |
|---|---|
| `ba4b999` | baseline da fundação SaaS (Releases 1A–1C) |
| `99657f1` | congelamento da fundação e linha de base de segurança |
| `73c394a` | migrations do branch reconciliadas sobre o schema canônico |
| `46ba2a6` | escopo de empresa deny-by-default sobre a identidade canônica |
| `c79875c` | backend de automação sobre identidade e billing existentes |
| `aee701b` | RAG, proteção de PII e ciclo de fine-tuning |
| `64ad0ff` | superfície HTTP de conhecimento, dataset, avaliação e modelos |
| `69b554b` | Automation Studio, sessão same-origin e empacotamento |
| `201c638` | guarda de tenant no nível do banco |
| `5e5350e` | documentação que não consegue divergir em silêncio |
| `50960a9` | ligação dos gatilhos que a paleta já anunciava |
| `f24bb93` | Automation Studio alcançável pela plataforma |

### Três decisões que explicam o resto

**Identidade humana continua sendo uma só.** O branch de origem trazia um
segundo sistema de identidade (`platform_principals`, `platform_access_tokens`).
Adotá-lo criaria duas fontes de verdade sobre quem é uma pessoa e o que ela
pode fazer — o tipo de duplicação em que uma revogação funciona num lado e não
no outro. Credencial de máquina foi modelada como service account consumindo a
**mesma** autorização e os mesmos memberships.

**Migrations não rodam no cold start.** Em serverless isso significa N
instâncias competindo pelo mesmo schema, e um deploy alterando o banco sem
ninguém ter decidido. Migration é passo explícito de release.

**Workers não vivem dentro da API.** Requisição HTTP pode ser cortada a
qualquer momento, e na Vercel a API nem continua viva entre requisições.
Trabalho durável precisa de processo durável — dois deles, porque automação e
lembrete falham por motivos diferentes e não podem competir pela mesma fila.

---

## 4. Defeitos reais encontrados e corrigidos

Não foram introduzidos por este trabalho — já estavam lá, e apareceram porque
alguém foi olhar.

**Vazamento de conhecimento entre empresas.** Em
`get_relevant_knowledge_entries`, o `company_id` era opcional e "ausente"
significava *sem filtro*. Pior: a condição de contato ficava fora do predicado
de empresa, então `applies_globally = 1 OR contact_id = ?` escapava do filtro
de tenant. Uma empresa podia receber conhecimento de outra. Hoje o `company_id`
é obrigatório e envolve o predicado inteiro.

**26 tabelas sem guarda no banco.** Todas tinham `company_id` e nenhuma tinha
constraint impedindo referência cruzada. O isolamento dependia inteiramente de
o código nunca errar. Migration 067 fechou isso.

**Gatilhos anunciados que nunca disparavam.** Cinco dos sete emissores
canônicos não tinham chamador, e três deles estavam na paleta — dava para desenhar, publicar e ativar um fluxo que nunca
rodava, sem nenhuma forma de descobrir isso a não ser esperando. Hoje existe um
teste que falha se a paleta anunciar um gatilho sem evento canônico.

**CSRF ausente no cliente do Studio.** Toda escrita do editor teria recebido
403 na primeira tentativa real.

**`SQLITE_ID_TABLES` incompleto.** Faltavam tabelas no registro que decide
quando anexar `RETURNING id`. O sintoma só apareceria em PostgreSQL, gravando
id nulo — verde em SQLite, quebrado em produção.

**Reset de rate limit pela metade.** `reset_rate_limit_state()` limpava a
memória e deixava os contadores no Redis. Com `REDIS_URL` configurada — e o
`.env.example` já a configura — a suíte falhava de forma intermitente em quem
apenas copiou o arquivo de exemplo. Hoje são duas funções com nomes honestos:
`reset_rate_limit_state()` descarta o estado local (e por isso serve para
simular uma réplica nova), `clear_rate_limit_counters()` limpa também o
compartilhado.

---

## 5. Evidência

Verificado em 2026-09-05, no commit de merge para `main`:

| Verificação | Resultado |
|---|---|
| `pytest` (SQLite, padrão) | **1286 passaram, 17 pulados** |
| `pytest` (PostgreSQL 16 + Redis) | **1302 passaram, 1 pulado** |
| `ruff check .` | limpo |
| `mypy app` | 140 arquivos, sem erros |
| `bandit -ll -r app -x app/storage` | nenhum achado de severidade média ou alta |
| `pip-audit -r requirements.txt` | nenhuma vulnerabilidade conhecida |
| frontend: `npm test` | 42 testes |
| frontend: `npm run build` | build real do Vite |
| flake do rate limit | 3 execuções consecutivas limpas na configuração que falhava |

Os pulados são condicionais, não falhas silenciadas: exigem PostgreSQL, Redis
ou bundle compilado. Na configuração completa sobra um — uma pragma de SQLite
que não existe no PostgreSQL.

### Testes que valem conhecer

Alguns testes existem para impedir uma classe inteira de erro, e vale saber que
estão lá antes de brigar com eles:

- **guarda estrutural de SQL** (`test_dynamic_sql_guard.py`) — prova por AST
  que toda expressão SQL interpolada deriva só de literais e constantes. Foi
  escrito depois de o padrão `(? IS NULL OR coluna = ?)` reaparecer seis vezes
  num código portado, causando `IndeterminateDatatype` só no PostgreSQL.
- **precisão da documentação** (`test_documentation_accuracy.py`) — lê as
  tabelas de configuração dos próprios documentos e compara com `Settings`, nos
  dois sentidos. Documentação erra em silêncio; este teste faz o erro aparecer.
- **monotonicidade de papéis** — o hierarquia de permissão não pode ter
  exceção pontual. Uma rota de escrita liberada para `viewer` quebra a suíte.

---

## 6. O que ainda não foi verificado

Nada aqui está quebrado que se saiba. O ponto é que **não se sabe**.

| Integração | Estado | O que falta |
|---|---|---|
| Stripe | implementado, mock testado | sandbox real, seis Price IDs |
| Meta WhatsApp | implementado, mock testado | token real, webhook de entrada |
| OpenAI | implementado, mock testado | chave real; nenhum job de fine-tuning concluído |
| Google Calendar / CalDAV | implementado, mock testado | credencial real |
| Vercel | empacotado | nenhum deploy executado |
| Imagem Docker | `Dockerfile` e `compose.yaml` versionados | **nunca construída** — não havia daemon no ambiente |

`compose config` valida a composição, e o `Dockerfile` tem estágios separados
para API, admin e o bundle do Studio. Mas validar sintaxe não é construir: a
primeira build vai encontrar o que só a build encontra.

---

## 7. Riscos aceitos e pendências abertas

**Criação de empresa é `unscoped`.** Em `/admin/onboarding/setup`, um
superadmin de tenant pode criar empresa e tomar o slug de um concorrente. Está
registrado como risco aceito, não como descuido: fechar isso é decisão de
produto sobre quem pode provisionar tenant, não um ajuste técnico.

**Readiness não cobre os subsistemas novos.** As 33 checagens incluem o worker
de lembretes, mas nenhuma detecta motor de automação ligado sem worker no ar,
nem `PII_PSEUDONYMIZATION_KEY` ausente. O primeiro aceita gatilhos e não
processa nada; o segundo só aparece quando o worker recusa subir. Ambos
deveriam falhar no readiness, antes e não depois.

**Janela de agenda não recupera atraso.** Um worker parado por mais de uma
janela dispara a corrente e segue, sem recuperar as perdidas. É deliberado —
executar às 9h um lote de "bom dia" represado de três dias é pior do que não
executar — mas é um comportamento que surpreende quem não sabe.

**Dois emissores de gatilho seguem sem chamador**
(`emit_appointment_canceled`, `emit_reminder_due`), porque não existe nó
correspondente na paleta. Não são gatilhos anunciados e mortos: são código
pronto esperando o nó.

---

## 8. Por onde começar a trabalhar

1. `README.md` — como rodar, como testar, como subir
2. [`codebase_map.md`](codebase_map.md) — em qual arquivo mexer para mudar o quê
3. o documento do subsistema que você vai tocar
   ([automação](automation_studio.md), [conhecimento e modelo](rag_e_finetuning.md))
4. [`production_runbook.md`](production_runbook.md) — antes de qualquer coisa
   que toque produção

Convenções que economizam tempo:

- **nunca** escreva um filtro SQL na forma `(? IS NULL OR coluna = ?)` — use
  `optional_equals()`. O guarda estrutural rejeita, e o motivo é um erro que só
  aparece em PostgreSQL;
- rota nova precisa de política de escopo declarada, senão é recusada;
- migration nova precisa entrar em `MIGRATIONS` e, se a tabela tiver id
  autoincremental, também em `SQLITE_ID_TABLES`;
- documento que cita valor de configuração é verificado por teste — atualize
  os dois juntos.
