# Testes locais com Qwen e operação da fila

Atualizado em 20/09/2026. O modelo instalado e validado neste Mac é
`qwen3.5:9b-q4_K_M`, servido pelo Ollama. O ambiente local usa IA real,
SQLite e envio/agenda simulados. Não envia mensagens a clientes.

## Iniciar neste computador

Com o Ollama aberto, na raiz do projeto:

```bash
.venv/bin/python -m app.tools.local_dev --port 8010
```

Abra <http://127.0.0.1:8010/platform>. O comando aplica as migrations locais,
inicia API e workers de entrada, automação e lembretes habilitados no `.env`,
e encerra os filhos ao receber Ctrl+C. A API escuta apenas em localhost.
O login administrativo usa o token configurado no `.env`; não compartilhe esse arquivo.

O `.env` deste computador já foi ajustado, com segredos próprios e banco
`runtime/local.db`. Para outra instalação, use `.env.example` como referência,
preencha os segredos indicados e instale o modelo no Ollama antes de iniciar.
Não sobrescreva uma configuração existente sem preservar suas credenciais.

Configuração principal:

```dotenv
ENVIRONMENT=development
DEBUG=false
STRICT_RUNTIME_VALIDATION=false
DATABASE_URL=sqlite:///runtime/local.db
POSTGRES_DATABASE_URL=
REDIS_URL=
REQUIRE_SHARED_STATE=false
RUN_MIGRATIONS_ON_STARTUP=true
CLASSIFICATION_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3.5:9b-q4_K_M
LLM_TIMEOUT_SECONDS=30
OLLAMA_NUM_CTX=8192
OLLAMA_NUM_PREDICT=512
OLLAMA_MAX_CONCURRENCY=1
OUTBOUND_PROVIDER=mock
CALENDAR_PROVIDER=mock
INBOUND_WORKER_ENABLED=true
AUTOMATION_ENGINE_ENABLED=true
AUTOMATION_WORKER_ENABLED=true
ENABLE_FINETUNING_PIPELINE=false
```

A integração usa `/api/chat`, com `think=false`, saída estruturada validada e
limite de tempo. Não faz fallback automático para OpenAI. Classificação,
resposta, memória, pipeline, interpretação de agenda e reformulação de handoff
usam o provider selecionado. Fine-tuning continua sendo a integração OpenAI
existente; conectar o Qwen não implementa treinamento local.

O limite de concorrência do Ollama vale **por processo**. API, workers e outras
aplicações ainda podem disputar o mesmo modelo. Comece com um worker de entrada;
meça a fila e a latência antes de aumentar réplicas. Não diminua o lease da fila
abaixo do tempo de processamento completo de uma mensagem.

## Testar o fluxo

Para medir ACK, fila, latência completa e vazão com 1, 3 e 5 conversas,
use o [roteiro de capacidade](local_capacity_test.md). Ele gera somente dados
sintéticos, exige outbound mock e grava relatórios JSON comparáveis.
Para avaliar respostas, agenda, confirmação e cancelamento no Bot Studio, siga
primeiro a [homologação funcional local](local_functional_test.md).

Cadastre a empresa, revise e publique uma versão do contexto operacional,
com catálogo, regras comerciais e encaminhamento humano. Apenas salvar um
rascunho ou o contexto legado não publica essa versão; em desenvolvimento,
empresas sem versão publicada podem usar o template global de demonstração. Use primeiro o simulador com conversas sintéticas.
Para exercitar também a fila, envie um evento normalizado ao endpoint abaixo,
com `ENABLE_NORMALIZED_WHATSAPP_INGRESS=true` e o segredo interno do `.env`:

```http
POST /webhooks/whatsapp?company_id=ID_DA_EMPRESA
X-Internal-Ingress-Secret: SEGREDO_INTERNO
Content-Type: application/json

{"event":"message.received","from_number":"cliente-sintetico","text":"Preciso da segunda via do boleto.","provider_message_id":"teste-unico-001"}
```

Use um `provider_message_id` novo por mensagem; repetir o mesmo ID na mesma
empresa testa deduplicação. Empresa explícita e ID de mensagem são obrigatórios
na entrada normalizada. Eventos Meta continuam exigindo assinatura válida e
vínculo do número receptor à empresa. Todos os textos de todas as entradas e
alterações do lote são extraídos; eventos de status e mídias sem suporte não
são convertidos em mensagens de texto.

O novo contrato HTTP confirma persistência, não conclusão da IA:

```json
{"accepted":true,"processed":false,"payload_mode":"normalized","queued_count":1,"jobs":[{"job_id":1,"status":"pending","duplicate":false}]}
```

Clientes que dependiam da resposta síncrona do webhook precisam ser adaptados.
`queued_count` conta somente novos registros. O lote é persistido numa transação;
a confirmação só ocorre depois do commit. O worker processa e salva o resultado
junto do outbound, e uma etapa seguinte realiza o envio.

## Saúde e revisão operacional

- `GET /health/ready`: testa banco, configuração, modelo instalado no Ollama,
  chave de pseudonimização e heartbeat dos workers necessários. A chave é
  obrigatória em produção. O teste do Ollama verifica disponibilidade e tag,
  mas não substitui uma avaliação real de qualidade ou latência.
- `python -m app.tools.inbound_worker --healthcheck` e
  `python -m app.tools.automation_worker --healthcheck`: exigem heartbeat recente
  do componente no banco compartilhado. `--validate-only` não cria heartbeat.
- `GET /admin/inbound/jobs?company_id=ID`: lista estados e erros da fila com
  autenticação e escopo de empresa.

Estados: `pending` → `processing` → `ready` → `dispatching` → `completed`.
Mensagens sem resposta automática podem ir de `processing` para `completed`.
Mensagens ainda não iniciadas sobrevivem a reinícios. Um resultado já salvo
pode seguir para envio sem gerar outra resposta. Claims abandonados e falhas
com resultado incerto vão para `review`, preservando evidência e bloqueando
somente as próximas mensagens daquela conversa.

Antes de resolver uma revisão, o operador deve verificar histórico, agenda e
outbound, reconciliar eventual envio incerto e realizar o atendimento necessário.
Depois pode chamar, com papel operator ou superior:

```http
POST /admin/inbound/jobs/ID_DO_JOB/resolve?company_id=ID_DA_EMPRESA
```

Essa ação é auditada, conclui o job e libera a conversa. **Não reexecuta** a IA,
não reenvia a mensagem e não desfaz efeitos já produzidos. Não há garantia de
entrega exatamente uma vez diante de falha de rede após aceitação pelo canal.
A revisão está disponível na API administrativa; não há tela nova para a fila.

## Atualizar uma instalação existente

Faça backup e aplique `python -m app.tools.run_migrations` antes de iniciar a
nova API e os workers. A migration `068_durable_inbound_queue` cria fila e
heartbeats; recibos antigos concluídos continuam impedindo reprocessamento.
Recibos antigos com resultado incerto são encaminhados à revisão quando reaparecem.
O backup lógico agora usa versão 4 e inclui a fila; heartbeats são efêmeros.
Use as ferramentas de backup/restore da mesma versão do aplicativo.

No Docker Compose, `inbound-worker` é um serviço próprio. A configuração de
modelo e segurança é compartilhada com os demais workers. Para o Ollama no
Mac anfitrião, `COMPOSE_OLLAMA_BASE_URL` usa por padrão
`http://host.docker.internal:11434`; em outro servidor ajuste o endereço alcançável
pelos containers. Em produção as migrations são um passo separado do release.

## Próximo passo para uma empresa real

O ambiente está preparado para testes locais e ensaio supervisionado. O sinal
`pilot_ready` em desenvolvimento não certifica prontidão para clientes reais.
Antes de ativar atendimento externo:

1. Configure `.env.production` a partir de `.env.production.example`, com Meta
   real, Postgres, Redis, domínio HTTPS e contexto correto da empresa. Valide
   o vínculo do canal, credenciais e isolamento com contas de teste autorizadas.
2. Rode preflight e readiness em produção com todos os workers vivos. Valide
   backup/restauração e o procedimento de revisão de falhas.
3. Avalie um conjunto representativo de conversas aprovadas pela empresa:
   classificação, respostas fundamentadas, agenda, handoff, recuperação após
   reinício e concorrência. Registre acertos e latências; um smoke test não
   determina a qualidade geral do Qwen.
4. Comece com uma empresa, volume limitado e responsável humano disponível.
   Amplie apenas depois de observar a operação e corrigir erros recorrentes.

As credenciais externas reais ainda precisam ser fornecidas. O runtime de
produção deve continuar bloqueado enquanto essas condições não forem atendidas.
