# Evidências e gates de liberação

Critérios deste arquivo são alvos a provar. Nenhum está aprovado apenas por constar na documentação. Por tarefa, guardar resumo em `docs/piloto/evidencias/Pxx.md` (criar quando houver execução), com base/diff, comando, ambiente, contagem, cenários, falhas e limites. Logs grandes/sensíveis ficam fora do Git; referências não podem depender somente de um `/tmp` que desaparecerá.

## Gates

| Gate | Evidência exigida |
| --- | --- |
| G0 — Base | P00; árvore preservada, runtimes e bancos descartáveis; baseline e divergências identificadas |
| G1 — Código | P01–P07 implementados, regressões e integração local; revisão sem achado crítico aberto; configurações realmente aplicadas |
| G2 — Release em homologação | imagem construída; PostgreSQL/Redis; migrations/upgrade; segurança; UI real; concorrência; reinício/carga; backup/restore e rollback |
| G3 — Piloto habilitado | Meta/agenda reais nas capacidades habilitadas, números autorizados, template/status, operador/fallback, smoke completo e evidência no artefato final |
| G4 — Resultado do piloto | métricas de operação/custo, feedback, incidentes e decisão comercial de continuidade |

Passar G1 não implica G2/G3. O ambiente pode estar pronto tecnicamente e ainda faltar autorização ou participantes para smoke externo. Registrar esse bloqueio precisamente e continuar trabalho independente. Não aprovar piloto com P0/P1 de ação incorreta, isolamento ou integridade em aberto. Melhorias secundárias podem ficar desligadas e explicitadas no escopo de liberação; requisitos de P01–P08 não podem ser removidos silenciosamente.

## Matriz funcional mínima

| ID | Cenário | Resultado verificável |
| --- | --- | --- |
| C01 | “Oi, tudo bem?” sem fluxo | resposta conforme empresa; nenhuma pendência humana indevida |
| C02 | “Oi” durante escolha de horário | preserva estado, não reserva nem reinicia |
| C03 | ambiguidade e modelo indisponível | esclarecimento/fallback útil; erro visível e sem fato inventado |
| C04 | políticas distintas e fora do horário | empresa/preset/override aplicados; rascunho sem efeito |
| C05 | “não quero confirmar”, “isso cancela todos?”, “assim está bom?” | nenhum efeito de confirmação |
| C06 | “não, pode manter” após pedido de cancelar | reserva preservada e desistência encerrada |
| C07 | “sim, mas às 16h”, troca de serviço/alvo | invalida proposta anterior e pede confirmação da nova |
| A01 | agendar e confirmar proposta inequívoca | booking e efeito externo correspondem ao apresentado |
| A02 | dois bookings do contato | desambiguação; operação só no selecionado |
| A03 | aceite expirado/repetido | não executa proposta antiga nem duplica |
| A04 | cancelar durante coleta de nova reserva | encerra coleta; não altera reserva existente |
| A05 | dois contatos/rotas disputando recurso | exatamente uma reserva ativa no intervalo |
| A06 | sobreposição parcial, adjacência e recurso distinto | conflito apenas conforme recurso e intervalos |
| A07 | evento externo entra após oferta de remarcação | não confirma horário ocupado; reserva original tratada com segurança |
| A08 | regra de prazo/exceção/recurso alheio | recusa/encaminha com motivo; mesma regra na API e bot |
| A09 | timezone, virada de dia e antecedência | horário local e UTC consistentes; nenhum cálculo ingênuo |
| R01 | cancelar reserva com lembretes | evento/booking cancelados; lembretes não enviados |
| R02 | falha no envio de encaixe após cancelamento | cancelamento continua concluído; oferta recuperável |
| R03 | dois aceites, oferta expirada, recusa e espera | capacidade não duplicada; estado e expiração coerentes |
| W01 | webhook duplicado e lote | persistência única por mensagem/empresa; ordem por contato |
| W02 | token/binding ausente e tenant errado | falha explícita; sem saída pelo número de outra empresa |
| W03 | dentro/fora da janela Meta | texto/template corretos; ausência de template visível |
| W04 | template rejeitado, rate limit e erro permanente | tratamento distinto; sem retry infinito |
| W05 | callbacks repetidos/fora de ordem/falsificados | correlação correta, estado coerente e HMAC obrigatório |
| W06 | opt-out após mensagem entrar na fila | mensagem proativa barrada no dispatch; consentimento segregado |
| W07 | áudio/imagem/botão | orientação para tipo não suportado; botão usado tem handler funcional |
| F01 | queda antes/depois da chamada externa | reconcilia sem duplicar nem afirmar sucesso falso |
| F02 | perda de commit após sucesso externo | recupera operação existente pelo identificador seguro |
| F03 | worker reinicia/lease expira | dono antigo não produz efeito indevido; ordem preservada |
| F04 | vaga ocupada/erro de negócio | cliente recebe opção/explicação; conversa não trava sem necessidade |
| F05 | erro incerto e resolução pelo operador | revisão auditada; nenhuma reexecução cega |
| F06 | backlog e worker morto | alerta/estado operacional visível e discriminado |
| H01 | humano assume e cliente continua escrevendo | entrada persiste, bot suspenso conforme contrato |
| H02 | humano devolve atendimento | retomada explícita, contexto preservado |
| H03 | viewer/outro tenant tenta resolver | acesso negado no backend, sem depender da UI |

Adicionar cenários descobertos durante implementação. Mocks são úteis para injeção de falhas, mas A05/A06 exigem banco real concorrente; W03/W05 e A01/R01 exigem evidência adicional de integração real para G3.

## Comandos locais seguros e gates técnicos

Na raiz, uma suíte SQLite por vez. O comando abaixo remove destinos herdados para não usar PostgreSQL/Redis do operador:

```bash
env -u TEST_DATABASE_URL -u TEST_POSTGRES_MIGRATION_URL REDIS_URL='' TEST_REDIS_URL='' .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check .
.venv/bin/mypy app
git diff --check
```

Conferir `tests/conftest.py` antes da execução se ele mudou. Para desenvolvimento, usar a mesma isolação e passar somente arquivos de testes da tarefa. Não rodar comandos que inicializam app apontando para `.env.production` por acidente.

No diretório `frontend/automation-studio`, quando houver mudança nessa superfície ou no gate de release:

```bash
npm ci --no-audit --no-fund
npm run typecheck
npm run lint
npm test
npm run build
```

Os 42 testes do Studio não cobrem o painel principal em `app/platform/assets`; telas alteradas também precisam de smoke de navegador e testes de API/RBAC.

Para PostgreSQL/Redis, replicar a configuração de serviços descartáveis da CI, definir `TEST_DATABASE_URL`, `TEST_REDIS_URL` e, quando necessário, `TEST_POSTGRES_MIGRATION_URL` exclusivamente para esses destinos, e executar os gates correspondentes de `.github/workflows/ci.yml`. A suíte de migração cria/remove bases: conferir destino antes de rodar. Não copiar URL operacional para facilitar teste.

G2 também exige Python 3.13 da imagem (ou versão alvo alterada de modo justificado), build API/admin/Studio conforme CI, lint/typecheck/SAST/auditoria de dependências e testes de importação/empacotamento. Se um gate depender da rede e não puder rodar, registrar pendência; não afirmar ausência de vulnerabilidades com resultado antigo.

## Carga e observabilidade

Medir caminho completo e etapas: ACK do webhook, espera em fila, inferência, serviços e dispatch. Alvos iniciais propostos: ACK p95 < 1 s e resposta comum p95 < 10 s no ambiente do piloto; calibrar com o usuário conforme hardware/volume e registrar qualquer alteração. Não apresentar esses números como desempenho já medido.

Ensaiar 1, 3 e 5 conversas concorrentes, incluindo mensagens sucessivas do mesmo contato, durante janela definida com amostra suficiente para informar p95. Registrar tokens/chamadas, espera, tempo total, backlog e consumo; não inferir capacidade por um único smoke. Limite de inferência deve considerar todos os processos, não só semáforo local.

## Evidência mínima por tarefa

```text
Pxx — título; data; base/commit/diff; executor e revisor
Critérios: IDs e resultado de cada um; o que é mock vs real
Mudanças: arquivos e comportamento observável
Validação: comandos, ambiente/versão, contagem e falhas
Migration/compatibilidade/rollback: quando aplicável
Riscos/pêndencias: incluindo bloqueios externos
Decisão: aceitar / corrigir / bloqueado; próxima ação
```

Não marcar caso como aprovado porque existe um teste com nome parecido. Inspecionar assertions e dependências substituídas; confirmar que a evidência prova o contrato-alvo.
