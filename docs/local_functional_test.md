# Homologação funcional local

Este roteiro verifica o que um pequeno cliente perceberá: identidade da empresa,
respostas factuais, continuidade da conversa, confirmação segura, criação,
remarcação e cancelamento. Ele complementa o teste de carga em
[`local_capacity_test.md`](local_capacity_test.md); o Bot Studio usa o runtime
conversacional real, mas não passa pela fila durável do webhook.

## 1. Preparar uma empresa sintética publicada

Inicie o Ollama e, na raiz do projeto:

```bash
.venv/bin/python -m app.tools.local_dev --port 8010
```

Abra <http://127.0.0.1:8010/platform>. Entre com a conta local ou, em
**Acesso administrativo de emergência**, com o token do `.env`. Não copie o
token para comandos, logs ou documentação.

Em **Configuração** (o assistente de cinco passos), configure a empresa criada no cadastro ou, com o token administrativo em escopo global, crie uma empresa exclusiva para o teste. Exemplo:

- nome: `Clínica Piloto Local`;
- tipo: `Clínica / Consultório`;
- timezone: `America/Sao_Paulo`;
- segunda a sexta: `08:00-18:00`;
- serviço: `Consulta inicial`, 30 minutos, preço `150`;
- antecedência mínima: `0` para facilitar o ensaio.

O onboarding publica a primeira versão do contexto. Selecione essa empresa no
topo do painel. Não faça o ensaio na empresa `default` sem versão publicada:
nesse caso o desenvolvimento usa o template global e a resposta não representa
o cliente configurado.

Mantenha `OUTBOUND_PROVIDER=mock` e `CALENDAR_PROVIDER=mock`. Assim o teste
persiste todos os efeitos locais sem enviar WhatsApp nem gravar numa agenda real.

## 2. Conversa principal em Testar o bot

Abra **Testar o bot**, crie uma sessão (o telefone de teste é gerado sozinho se ficar vazio) e use a mesma sessão até o cancelamento. Escolha uma
data útil futura dentro do horário publicado.

| Passo | Mensagem | Resultado obrigatório |
| --- | --- | --- |
| 1 | `Oi, tudo bem?` | cita a empresa selecionada; `Ação: greeting`; `Fonte: policy_greeting`; não abre agenda nem handoff |
| 2 | `Quais serviços vocês oferecem e quanto custa a consulta inicial?` | responde pelo catálogo, incluindo `R$ 150,00`; não inicia coleta de data |
| 3 | `Quero agendar uma Consulta inicial no dia DD/MM/AAAA às 10h. Meu nome é Ana Teste.` | apresenta exatamente serviço, data e hora; `Ação: schedule_confirmation_requested`; ainda não existe booking confirmado |
| 4 | `Assim está bom?` | pede confirmação explícita; não cria booking |
| 5 | `Sim, pode confirmar` | `Ação: schedule_confirmed`; cria exatamente um booking no horário apresentado |
| 6 | repita `Sim, pode confirmar` | não cria um segundo booking |
| 7 | `Quero remarcar meu horário para DD/MM/AAAA às 15h` | apresenta a mudança e pede confirmação; preserva o horário anterior até o aceite |
| 8 | `Não, pode manter` | `Ação: booking_change_kept`; booking original continua ativo |
| 9 | `Quero cancelar meu agendamento` | `Ação: booking_cancel_confirmation_requested`; booking continua ativo |
| 10 | `Isso cancela todos?` | explica o alvo; não cancela nada |
| 11 | `Sim, pode cancelar` | `Ação: booking_cancelled`; o mesmo booking passa a `cancelled` |
| 12 | repita `Sim, pode cancelar` | não produz novo efeito nem reativa o booking |

Depois dos passos 5, 8, 10 e 11, confira a tela **Agenda**. O estado visual
deve coincidir com a conversa. O cancelamento deve liberar o horário e sair da
ocupação do calendário.

Use uma segunda sessão para testar desconhecimento:

```text
Vocês fazem um procedimento que não está no catálogo? Qual o preço?
```

O bot deve pedir esclarecimento ou encaminhar para a equipe. Reprove se inventar
serviço, preço, política, disponibilidade ou confirmação.

## 3. Persistência e efeitos

Com o servidor parado ou em outro terminal, descubra o ID da empresa e inspecione
somente os contatos sintéticos:

```bash
sqlite3 -header -column runtime/local.db \
  "SELECT id, slug, active FROM companies ORDER BY id;"

sqlite3 -header -column runtime/local.db \
  "SELECT id, contact_id, status, starts_at, ends_at, provider
   FROM appointment_bookings
   WHERE company_id = ID_DA_EMPRESA
     AND contact_id LIKE 'cliente-homologacao-%'
   ORDER BY id;"

sqlite3 -header -column runtime/local.db \
  "SELECT kind, status, booking_id, confirmation_ref
   FROM booking_operations
   WHERE company_id = ID_DA_EMPRESA
     AND contact_id LIKE 'cliente-homologacao-%'
   ORDER BY id;"
```

O fluxo principal deve terminar com um booking `cancelled`, uma operação
`create/completed` e uma `cancel/completed`. Frases ambíguas e confirmações
repetidas não podem acrescentar operações concluídas.

## 4. Fila e desempenho

Depois da qualidade funcional, execute o runner da fila com a mesma empresa:

```bash
.venv/bin/python -m app.tools.capacity_test \
  --company-id ID_DA_EMPRESA \
  --scenario greeting \
  --conversations 2 \
  --messages-per-conversation 2 \
  --output outputs/capacity/greeting-smoke.json

.venv/bin/python -m app.tools.capacity_test \
  --company-id ID_DA_EMPRESA \
  --scenario common \
  --conversations 1 \
  --messages-per-conversation 3 \
  --drain-timeout-seconds 180 \
  --output outputs/capacity/common-smoke.json
```

O runner exige outbound mock, usa contatos sintéticos e verifica ACK, conclusão,
jobs ausentes, presos ou enviados para revisão. Para decidir capacidade de
produção, execute depois a matriz 1/3/5 em PostgreSQL e Redis descrita no roteiro
de carga.

## 5. Critério de aprovação para o primeiro piloto

Aprovar somente quando:

1. todos os efeitos de agenda acima estiverem corretos e idempotentes;
2. nenhuma resposta inventar fatos e pelo menos 90% da amostra de perguntas da
   empresa estiver correta, útil e coerente com o catálogo publicado;
3. nenhuma conversa ficar presa; dúvidas não cobertas forem esclarecidas ou
   encaminhadas para humano;
4. ACK p95 ficar abaixo de 1 segundo e a latência comum medida for aceita para o
   hardware do piloto;
5. não houver job ausente, inacabado ou em `review` sem procedimento operacional;
6. um operador real conseguir acompanhar agenda, assumir/devolver conversa e
   corrigir exceções.

Registre por cenário: mensagem, resposta, ação, fonte, efeito esperado, efeito
observado, latência e aprovação. Cinco exemplos fáceis não bastam; use ao menos
20 perguntas reais anonimizadas do nicho, incluindo linguagem incompleta,
erros de digitação, negação, mudança de ideia e pedidos fora do escopo.
