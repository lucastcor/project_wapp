# Comece aqui — produto funcional para pequenos clientes

Plano de execução criado em 13/09/2026 para uma nova sessão do Claude Code. Pode ser executado com o modelo escolhido pelo usuário; não depende de comandos, versões ou capacidades exclusivas de um modelo.

## Objetivo e definição de pronto

Entregar atendimento WhatsApp por empresa que acolhe o cliente, informa conforme fontes publicadas, agenda, remarca e cancela com confirmação segura, envia lembretes permitidos e oferece operação humana quando necessário. O primeiro piloto é supervisionado, com uma empresa e agenda de capacidade um por recurso explicitamente configurado; ampliar a poucos clientes depois da homologação.

O produto só está pronto para piloto quando tarefas P00–P08 e o gate G3 forem aceitos, com canais reais e evidências. Código concluído com integrações mockadas é G1/G2, não produção aprovada. P09 acompanha o piloto. P10 mantém a evolução de grafos prevista, sem bloquear o primeiro cliente.

## Leitura progressiva

1. Leia [ESTADO.md](ESTADO.md) e depois este arquivo se precisar do mapa completo: objetivo, checkpoint e próxima tarefa.
2. Leia [CONTRATOS.md](CONTRATOS.md) e [ORQUESTRACAO.md](ORQUESTRACAO.md) uma vez no início da frente; depois apenas se revisados.
3. Abra somente o pacote que contém a tarefa elegível: [01-CONVERSA.md](01-CONVERSA.md), [02-AGENDA.md](02-AGENDA.md), [03-CANAL-E-OPERACAO.md](03-CANAL-E-OPERACAO.md) ou [04-PILOTO-E-EVOLUCAO.md](04-PILOTO-E-EVOLUCAO.md).
4. Consulte [VALIDACAO.md](VALIDACAO.md) para o gate aplicável. Leia os arquivos de código e testes indicados na tarefa, expandindo a busca conforme necessário.
5. Use [DECISOES.md](DECISOES.md) para decisões e dúvidas duráveis. O [diagnóstico completo](../../outputs/revisao-producao-e-plano-2026-09-13.md) é referência sob demanda; não precisa ser relido em toda sessão.

## Primeiros passos executáveis

- Execute `git status --short` e `git rev-parse --short HEAD`; confira `CLAUDE.md` e instruções locais aplicáveis. Nunca descarte mudanças existentes.
- Confira ESTADO; P00 é a primeira tarefa se o checkpoint ainda estiver no estado inicial.
- Identifique divergências pontuais entre plano e checkout antes de editar. Não reinicie uma auditoria global já feita.
- Registre proprietário e arquivos de escrita da tarefa antes de delegar. Defina contratos compartilhados antes de paralelizar.
- Implemente, teste, revise, integre e atualize o checkpoint. Continue para a próxima tarefa elegível; não pare apenas ao apresentar um plano ou ao terminar um subagente.
- Se faltarem credenciais/infraestrutura, registre exatamente o que falta, conclua código e homologação isolada e avance em tarefas independentes. Não invente resultado de integração real.

## Sequência e dependências

| ID | Entrega | Depende de | Pacote |
| --- | --- | --- | --- |
| P00 | Base reproduzível, inventário e isolamento | — | 01 |
| P01 | Contratos de domínio, políticas e compatibilidade | P00 | 01 |
| P02 | Conversa e confirmação seguras | P01 | 01 |
| P03 | Reserva concorrente e recursos | P01 | 02 |
| P04 | Sincronização, idempotência e encaixes | P03 | 02 |
| P05 | WhatsApp completo para o piloto | P01 | 03 |
| P06 | Recuperação de falhas e controle humano | P02, P04, P05 | 03 |
| P07 | Configuração e operação utilizáveis pela empresa | P02, P04, P05, P06 | 03 |
| P08 | Release e homologação real | P07 | 04 |
| P09 | Piloto e validação comercial | P08 + G3 | 04 |
| P10 | Avaliar evolução semântica/GraphRAG | baseline de P09 | 04 |

P02, P03 e P05 podem executar em paralelo após P01 se os arquivos de escrita não colidirem. P06/P07 podem preparar desenho/testes com contratos congelados, mas sua aceitação depende das integrações completas.

## Escopo deliberado

Manter FastAPI, PostgreSQL, Redis, workers e providers existentes. Completar os fluxos positivos e de falha. Incluir configuração de conversa, regras de agenda, entrega WhatsApp, fila de revisão, operação humana, onboarding mínimo e homologação real. Não reescrever a plataforma nem priorizar expansão do Studio.

Uma agenda interna é válida somente se escolhida conscientemente como fonte oficial da empresa e identificada assim no produto; `mock` não deve simular sucesso externo em produção. Google Calendar é o caminho de integração inicial proposto. Não é necessário habilitar CalDAV e Google simultaneamente. Recursos múltiplos devem ser modelados corretamente, mas telas sofisticadas de escala ficam posteriores ao piloto.

Stripe automático pode esperar caso haja cobrança manual registrada e entitlement válido, sem desligar controles globais. Transcrição de áudio e mais canais ficam posteriores; o piloto deve responder claramente a conteúdo não suportado. Se botões forem usados, entrada interativa correspondente faz parte de P05.

## Fontes e cuidado com histórico

Diagnóstico de 13/09: backend 1.319 passaram/17 pulados; frontend 42; Ruff/Mypy/TypeScript passaram. São evidências históricas do checkout daquela revisão, não aceitação de mudanças futuras. A revisão não fez deploy remoto nem operações externas reais.

`docs/estado_do_projeto.md` preserva trechos históricos já superados; `outputs/correcoes-qwen-2026-09-08.md` registra evolução posterior. `.claude/RESUME.md` não é o checkpoint desta frente. Os PDFs acadêmicos e o artigo CIO estão sintetizados na seção 8 do diagnóstico; não reler os PDFs por padrão.

## Pedido para iniciar a sessão

> Leia `CLAUDE.md`, `docs/piloto/ESTADO.md` e a evidência P08 indicada nele. Continue P08/G2 a partir do checkpoint, sem reiniciar a auditoria ou repetir gates verdes sem mudança de código. Preserve toda a árvore local. Conclua primeiro os ensaios que independem de credenciais; registre bloqueios externos específicos e só aceite G2/G3 com as evidências exigidas em `VALIDACAO.md`. Use agentes apenas conforme `ORQUESTRACAO.md` e mantenha `ESTADO.md` atualizado. Meu objetivo é colocar um piloto supervisionado com um pequeno cliente em funcionamento, não produzir outro plano.
