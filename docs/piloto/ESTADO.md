# Checkpoint compacto — fonte de continuidade

Atualizado: 25/09/2026. Status: **G0 e G1 aceitos; P08/G2 em execução; revisão de produto registrada**.

## Leia primeiro

Uma nova sessão deve ler, nesta ordem:

1. `CLAUDE.md` — contrato curto da sessão e regras de execução.
2. Este arquivo — estado atual, evidência e próxima ação.
3. `docs/piloto/evidencias/P07.md` — comportamento funcional entregue e revisão independente.
4. `docs/piloto/evidencias/P08.md` — gate de release já executado e pendências externas/operacionais.
5. Só então os documentos de domínio ligados à tarefa: `01-CONVERSA.md`, `02-AGENDA.md`, `03-CANAL-E-OPERACAO.md`, `04-PILOTO-E-EVOLUCAO.md`, `CONTRATOS.md` e `VALIDACAO.md`.

Não releia todos os logs nem reexecute a suíte inteira antes de verificar se houve alteração posterior. O código é a fonte mais atual; documentação registra intenção, decisões e evidência.

## Base e árvore

- Frente consolidada na branch `codex/piloto-atendimento-agenda`, baseada na `main` `1bc3a45`, para revisão por PR. O usuário autorizou commit e abertura do PR em 24/09/2026, substituindo a restrição histórica D11 para esta entrega.
- O pacote reúne a implementação de Claude e as correções/validações posteriores de Codex. Verifique `git status` e o estado do PR antes de continuar; preserve quaisquer alterações locais adicionais.
- Integração serial: um escritor por arquivo compartilhado. Antes de editar, confira `git status --short` e o diff do arquivo.
- Alterações da revisão final Codex concentram-se em `calendar_service.py`, `whatsapp_provider.py`, `admin_routes.py`, `admin_permissions.py`, `app.js`, `index.html`, `repository.py`, `tests/test_integration_connection_checks.py` e esta documentação.

## Estado por tarefa

| Tarefa | Estado | Evidência essencial |
| --- | --- | --- |
| P00 | DONE | base preservada e divergências mapeadas em [P00](evidencias/P00.md); G0 aceito |
| P01 | DONE | política efetiva, contexto publicado canônico e operações duráveis; [P01](evidencias/P01.md) |
| P02 | DONE | confirmação inequívoca e controle de conversa; [P02](evidencias/P02.md) |
| P03 | DONE | capacidade e concorrência protegidas no banco; [P03](evidencias/P03.md) |
| P04 | DONE (G1) | idempotência/reconciliação de agenda e fila; [P04](evidencias/P04.md) |
| P05 | DONE (G1) | entrega WhatsApp, janela/template/consentimento e estados monotônicos; [P05](evidencias/P05.md) |
| P06 | DONE (G1) | posse humana, fencing, revisão e resumo operacional; [P06](evidencias/P06.md) |
| P07 | DONE (G1) | painel utilizável, política, canal, agenda e onboarding; revisão independente e smoke completos em [P07](evidencias/P07.md) |
| P08 | IN_PROGRESS | build e gate local aprovados; ambiente persistente, ensaios operacionais e integrações reais pendentes em [P08](evidencias/P08.md) |
| P09 | TODO | depende de P08 + G3 + empresa piloto |
| P10 | POST-PILOT | ontologias/GraphRAG/GNN somente após baseline e comparação mensurada |

## Revisão final de P07

Foram encontrados e corrigidos seis problemas com impacto de produção:

- painel de Canal consultava tabela diferente do binding WhatsApp usado pelo runtime;
- presença de credenciais era confundida com conexão real;
- Google `freeBusy` HTTP 200 com erro por calendário era interpretado como agenda vazia;
- data civil `YYYY-MM-DD` podia abrir o mês anterior;
- cancelamento ainda aparecia e contava na ocupação do mês;
- timestamps operacionais com largura variável podiam ser ordenados incorretamente no SQLite.

O painel agora usa a fonte canônica, mascara identificadores e oferece probes somente leitura e auditados. O teste Meta lê os metadados do número sem enviar mensagem; o teste de agenda consulta disponibilidade sem criar, alterar ou excluir evento.

## Último gate integrado

| Verificação | Resultado em 14/09/2026 |
| --- | --- |
| Python 3.13 + SQLite | 1.863 passaram; 18 pulados |
| Python 3.13 + PostgreSQL 17 + Redis | 1.879 passaram; 2 pulados |
| Ruff / mypy / JS syntax / diff | limpos; mypy verificou 163 arquivos |
| Automation Studio | typecheck/lint/build limpos; 42 testes passaram; 199 módulos no build |
| Segurança | `pip-audit`: nenhuma vulnerabilidade conhecida; Bandit: nenhum achado |
| Docker | API e admin construídos; API saudável, Python 3.13.15, usuário `app`; `/health/ready` HTTP 200, `pilot_ready=true`, zero blockers |
| Backup/restore PostgreSQL | 67 tabelas; contagens iguais e conteúdo restaurável idêntico em banco novo; CLI corrigido para fechar o pool sem atraso/aviso |
| Fluxo sintético | configurar → publicar → saudar → iniciar workflow → criar → remarcar → cancelar → resolver revisão, sem replay |
| Navegador | mês/fuso corretos, cancelamento fora da ocupação, histórico preservado, status canônico mascarado, probes claros, console limpo |

### Delta local de 20/09/2026

- Adicionado runner de carga do caminho durável e roteiro funcional no Bot
  Studio. Smoke de saudação 2 × 2: ACK p95 14,34 ms, E2E p95 1,07 s, 4/4
  concluídos. Pergunta comum única: 13,68 s, acima do alvo inicial de 10 s.
- Smoke real com Qwen e empresa sintética publicada confirmou proposta,
  confirmação inequívoca, booking único, ambiguidade sem efeito e cancelamento.
- Corrigidos: duas inferências inúteis em saudação, polling entre jobs com
  backlog, pergunta de catálogo confundida com reserva e preço fixo ignorado no
  prompt. A pergunta de catálogo passou a citar R$ 150,00 em execução real.
- Suíte SQLite após as correções: 1.870 passaram, 18 pulados; Ruff, mypy (164
  arquivos) e `git diff --check` limpos. Execução em Python 3.14.3; o gate de
  release em Python 3.13/PostgreSQL continua sendo o registrado em 14/09.

### Delta local de 25/09/2026 — revisão de produto

- Revisão completa do produto contra o objetivo comercial (bot vendável por
  assinatura a pequenas empresas) e contra o mercado, com jornada do dono de
  negócio percorrida no navegador: [REVISAO_PRODUTO_2026-09-25.md](REVISAO_PRODUTO_2026-09-25.md).
  Contém veredito, comparação com concorrentes, roadmap P0/P1/P2, custo de
  hospedagem e decisões pendentes do dono do produto.
- Corrigidos: o wizard devolvia 403 ao dono da conta self-service e criaria uma
  segunda empresa (novo `POST /admin/onboarding/configure`, tenant-scoped);
  Billing respondia 500 com `STRIPE_PRICE_*` vazios; simulador com texto
  deslocado e jargão; painel inicial sem caminho (cartão "Primeiros passos");
  pattern do slug inválido no cadastro. Evidência em [P08](evidencias/P08.md).
- Entregues em seguida (mesmo dia): conexão do WhatsApp por Embedded Signup
  e da agenda Google por OAuth, ambas por empresa e pelo painel, com
  credenciais cifradas (migration 074; contrato em `CONTRATOS.md`; evidência
  em [P08](evidencias/P08.md)); guia de implantação barata com Caddy e
  checklists Meta/Google em [IMPLANTACAO.md](IMPLANTACAO.md). Ainda abertas:
  Instagram, áudio, inbox ao vivo, navegação simplificada, editor de serviços
  fora do wizard e preço. Integrações reais continuam pendentes de credenciais
  (G3).

Warnings conhecidos: três avisos de depreciação de TestClient/cookies. Não são falhas funcionais, mas devem ser tratados quando a pilha Starlette/httpx for atualizada.

## Gates

- **G0 — aceito.** Base, runtimes, bancos descartáveis, baseline e divergências estão registrados.
- **G1 — aceito.** P01–P07 entregues, integrados e sem achado crítico aberto.
- **G2 — em execução.** Imagens, banco/cache reais locais, Python alvo, UI, SAST, auditoria e round-trip local de backup/restore passaram. Faltam upgrade/migrations, carga, reinício, backup/restore e rollback ensaiados no ambiente persistente.
- **G3 — bloqueado por dependências externas.** Faltam Meta e Google reais, HTTPS, número/template aprovados e smoke externo.
- **G4 — pendente.** Exige métricas e feedback do piloto.

## Próxima ação concreta

Concluir P08 nesta ordem:

1. escolher/provisionar homologação persistente e registry;
2. executar migrations/upgrade e subir o compose com configuração de produção;
3. executar três rodadas da matriz 1/3/5 em PostgreSQL/Redis, ensaiar reinício de workers, backup/restore e rollback, registrando tempos e hashes;
4. configurar Meta e Google com segredos no cofre e executar a matriz real de `VALIDACAO.md`;
5. realizar smoke assistido com o operador do primeiro cliente e só então aceitar G2/G3.

Não chamar configuração mock de produção homologada. Se ainda não houver credenciais, avance tudo que não depende delas e registre `BLOCKED_EXTERNAL` apenas no item específico.

## Dependências e limites aceitos

- Necessários para G3: domínio HTTPS persistente; credenciais Meta (token, `phone_number_id`, app secret e verify token), número autorizado e templates utility aprovados; agenda Google de teste; contato consentido.
- Definir com o cliente: nicho, catálogo, regras, horários, recursos, timezone, operador responsável e protocolo de incidente.
- D24 continua válido apenas no piloto supervisionado: cobrança manual com enforcement desligado; exportação/exclusão por contato e expurgo automático de retenção ainda não existem. Não prometer essas funções ao cliente.
- A aplicação tem viabilidade para um piloto assistido de agenda por WhatsApp. Escala comercial ainda exige medir ativação, taxa de agendamento concluído, intervenção humana, falhas de entrega/sincronização, custo por conversa e retenção.

## Recursos locais ao encerrar esta revisão

- PostgreSQL 17: `127.0.0.1:55432`, usuário `wapp_test`; banco final `wapp_test_codex_final_20260914`.
- Redis: `127.0.0.1:56390`, DB 14 usada no gate.
- Venv descartável Python 3.13: `/private/tmp/project-wapp-py313`.
- Imagens locais: `project-wapp-review-api:20260914` e `project-wapp-review-admin:20260914`.
- Contêiner de smoke encerrado. Dados e usuários usados no smoke são sintéticos.

## Protocolo de atualização

Use TODO / IN_PROGRESS / REVIEW / DONE / BLOCKED_EXTERNAL / POST-PILOT. DONE exige evidência do critério. Atualize primeiro a evidência da tarefa e depois este checkpoint. Mantenha aqui apenas estado, conclusão, risco e próximo passo; não cole logs extensos.
