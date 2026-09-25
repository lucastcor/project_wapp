# Decisões, premissas e pendências

Este registro é curto e cumulativo. ESTADO contém apenas checkpoint; evidências ficam por tarefa. Diferencie decisão de produto aprovada, premissa reversível e dependência externa.

| ID | Tipo | Decisão / motivo | Quando revisar |
| --- | --- | --- | --- |
| D01 | Objetivo do usuário | produto funcional para testes com pequenos clientes; fechar o fluxo central | mudança explícita de objetivo |
| D02 | Arquitetura proposta | preservar FastAPI/PostgreSQL/Redis/workers e providers; evolução incremental | limitação demonstrada |
| D03 | Premissa do piloto | uma empresa primeiro; capacidade um por recurso; serviços com agenda e supervisão | configuração do primeiro cliente |
| D04 | Escopo técnico | políticas/contratos de domínio agora; GraphRAG avaliado após baseline | P10 com comparação mensurada |
| D05 | Proposta de integração | Google Calendar inicial; agenda interna só se adotada explicitamente; mock não equivale a agenda externa | informação do cliente piloto |
| D06 | Escopo de canal | texto e templates utility; tipos não suportados recebem orientação; botão exige suporte de entrada | demanda mensurada de áudio/outros canais |
| D07 | Premissa comercial | cobrança manual pode atender piloto com entitlement válido; sem expansão de Stripe agora | necessidade de cobrança automática |
| D08 | Segurança operacional | sem ações indevidas/duplicação nos cenários críticos; sem liberar G3 com P0/P1 pertinente aberto | não dispensável por prazo |
| D09 | Coordenação | um escritor por arquivo compartilhado; suíte SQLite padrão serializada | isolamento implementado e validado |
| D10 | Histórico | relatório de 13/09 orienta; código prevalece como evidência; checkpoint de agosto não guia a frente | nova evidência registrada |
| D11 | Execução (13/09) | base = checkout compartilhado sobre `1bc3a45` + alterações locais (árvore `2f35963`); sem commit/worktree porque worktree de HEAD perderia a frente Qwen e commit não foi autorizado. Agentes escrevem só arquivos reservados; coordenador integra `db.py`/rotas/config | usuário autorizar commit/branch |
| D13 | Conversa (13/09) | aviso de encaminhamento ligado por padrão (silêncio só por política explícita); saudação pura sem fluxo ativo recebe texto configurado sem chamar modelo e sem pendência; intenção fora de `auto_reply_allowed_for` e intenção de `handoff_required_for` viram pendência real; spam segue silencioso por restrição fixa | feedback do piloto |
| D14 | Interface agenda (13/09) | `BookingConflictError` (vaga ocupada, erro de negócio) e `BookingPolicyError(outcome, reason)` em `calendar_service`; `actor` customer/automation só executa `allowed`; operator supera `requires_human`, ninguém supera `denied`; API admin age como operator | revisão em P06/P07 |
| D15 | Canal (13/09) | erro de envio WhatsApp classificado por código oficial: `transient` (429, 5xx, 4, 130429, 131000, 131016, 131056, 80007…) e `configuration` repetem com backoff; `permanent` (131047, 132xxx, 190, 131026…) e `uncertain` (timeout de resposta, conexão caída após abrir, 200 sem id) vão direto a dead letter sem retry. Diagnóstico `kind:code:título`, sem `error_data`. Entrada: `interactive`/`button` viram texto com `reply_id`; `reaction`/`system` ignorados; demais tipos `unsupported` com orientação configurável | P06 cria estado de revisão explícito para `uncertain` em vez de dead letter |
| D16 | Agenda (14/09) | capacidade garantida pelo banco: exclusão GiST (`btree_gist`) no PostgreSQL e triggers no SQLite, sobre `(empresa, recurso, intervalo ∪ retenção de remarcação)` para status `confirmed`/`pending`; reserva `pending` antes do efeito externo; sobreposição legada bloqueia a migration 070 sem alterar dados (relatório para o operador) em vez de reatribuir silenciosamente | P04 reconcilia `pending` órfão; revisar se piloto tiver dados legados |
| D17 | Execução (14/09) | limite de sessão da API interrompeu agentes paralelos duas vezes: executar com no máximo um agente por vez e retomar o mesmo agente quando possível | limite de uso normalizado |
| D18 | Fila de encaixe (14/09) | oferta só existe com capacidade `appointment_queue` publicada (presets services/clinic/salon ligam; general/retail/education não); falha de oferta após cancelamento é adiada e recuperada pelo worker de lembretes; aceite exige confirmação inequívoca e trata vaga ocupada como erro de negócio | pedido de empresa piloto |
| D19 | Confirmação (14/09) | toque em botão/lista nunca autoriza ação de agenda nem aceite de encaixe (não carrega a proposta a que responde); pede "sim" digitado. Envio pelo worker de entrada: bloqueio/recusa permanente conclui o job com dead letter visível, transitório volta à fila, só incerto vai para revisão | botões de confirmação com contexto vinculado à proposta |
| D20 | Agenda externa (14/09) | evento externo com ID estável derivado de empresa+booking (`ap<empresa>b<booking>`, base32hex válido no Google; UID no CalDAV); recusa explícita do provider libera a vaga, resposta perdida mantém `pending` e a operação em revisão; worker reconcilia `pending` antigo pela chave (existe → confirma; ausente com certeza → libera; incerto → mantém) | agenda real do piloto (P08) |
| D24 | Cobrança e dados no piloto (14/09, P07) | piloto roda com `ENABLE_BILLING_ENFORCEMENT=false` (cobrança manual); empresa piloto não recebe teste de 7 dias; segurança nunca depende do estado da assinatura. Retenção automática e exportação/exclusão por contato não existem: documentadas como lacuna com procedimento manual no runbook, sem prometer ao cliente | ferramenta de exportação/exclusão por contato e expurgo por retenção antes de ampliar o piloto |
| D25 | Diagnóstico de integrações (14/09, revisão P07) | o painel lê o binding WhatsApp canônico usado pelo runtime. “Configurado” significa que a configuração mínima existe; “conectado” exige teste explícito, auditado, somente leitura e sem efeito no cliente: leitura do número na Meta ou consulta de disponibilidade no calendário. Respostas e erros são sanitizados; segredos e IDs completos não saem pela API | alteração dos providers ou necessidade de diagnóstico mais profundo |
| D26 | Estado visual da agenda (14/09, revisão P07) | apenas reservas `confirmed`/`pending` ocupam a grade mensal. Canceladas continuam no histórico e informam que o horário foi liberado. Datas `YYYY-MM-DD` são datas civis locais e não instantes UTC. Erro por calendário dentro de um `freeBusy` HTTP 200 é falha de integração, nunca agenda vazia | adoção de timezone por filial/recurso ou novos estados de reserva |
| D23 | Limite de autenticação (14/09, smoke P07) | o limite estrito de `/platform/auth/*` (`RATE_LIMIT_AUTH_REQUESTS`, 10/min por IP) vale só para o que recebe credencial ou muda sessão (POST/PUT/PATCH/DELETE: login, cadastro, convite, redefinição, logout). Leitura de sessão e capacidades (GET/HEAD/OPTIONS) usa escopo `platform_auth_read` com o orçamento administrativo. O painel tenta ler a sessão de novo uma vez em falha de rede/servidor e só abre o login diante de 401 real; sem resposta confiável avisa que o servidor não respondeu | limite por usuário em vez de por IP, se o piloto tiver muitos operadores no mesmo IP |
| D22 | Posse humana (14/09, revisão P06) | com humano na conversa nada automático fala com o cliente: resposta gerada antes da tomada não é gravada, e o dispatch transforma envio de origem automática (`whatsapp_auto_reply`, `reminder_customer`, `appointment_queue_*`, `automation:*`) em dead letter `blocked:human_owned`; texto do operador sai. Exceção: confirmação de SAIR/VOLTAR (`whatsapp_channel_reply`). Ramo humano não chama modelo nem dispara automação. Resolver revisão retira a resposta pendente vinculada (`blocked:resolved_manually`) e recusa enquanto o envio está em andamento | aviso automático ao assumir/devolver, se o piloto pedir |
| D21 | Recuperação (14/09, revisão P04) | job de envio com falha transitória espera (`inbound_jobs.available_at`, 073) o backoff do outbound em vez de voltar à fila na hora; após `INBOUND_DISPATCH_MAX_ATTEMPTS` (12) ou outbound preso em `processing` além do TTL → revisão. Oferta de encaixe não retém capacidade: só é enviada com vaga livre e sem oferta aberta sobreposta, e o texto não promete reserva; aceite passa pela guarda do banco. Agenda externa sem resposta ≠ recusa: `ExternalCalendarUncertainError` mantém reserva/hold e vai para revisão. Evento Google só é "nosso" com a marca privada `wappBookingKey` | retenção real de vaga na oferta se o piloto pedir |
| D12 | Contexto (13/09) | fonte de runtime = versão publicada (`company_context_versions`). Legado `company_contexts` da própria empresa vale só como compatibilidade quando não há versão; nunca contexto global em produção. Escritas legadas (PUT admin, onboarding) passam a publicar versão | remoção do legado |

## Dependências externas conhecidas — não são bloqueio para iniciar código

- Empresa/nicho piloto, catálogo, regras comerciais, horários, recursos, timezone e operador responsável.
- Ambiente persistente e domínio HTTPS; PostgreSQL/Redis; capacidade/endpoint de inferência acessível.
- Credenciais Meta, acesso ao número, binding, webhook e templates aprovados; consentimento/contatos autorizados para smoke.
- Credenciais/acesso à agenda de teste e decisão de fonte de autoridade.
- Política de retenção e comunicação de privacidade adequadas ao piloto; restrições adicionais se o nicho exigir.

Não antecipar perguntas sobre tudo: concluir desenho/código independente; pedir apenas dados necessários ao próximo teste ou integração. Não usar nomes/segredos hipotéticos como configuração real.

## Formato de nova decisão

`Dxx | contexto concreto | alternativas avaliadas | escolha e razão | contrato/arquivos afetados | compatibilidade | evidência | condição de revisão`.

Registrar: schema final de operações, mecanismo de exclusão, tratamento de edições externas, credenciais por número, mecanismo de posse humana e gates alterados. Não criar um ADR para cada ajuste trivial.

## Revisão de coerência do plano — 13/09/2026

- Entradas de código/teste, CI, plataforma principal e Studio conferidos.
- Dependências sem ciclo: P01 libera P02/P03/P05; P03 libera P04; P06 integra; P07 expõe; P08 homologa.
- Contratos-alvo distinguem implementação existente de trabalho futuro; estado inicial não marca correções como feitas.
- Cada achado crítico da revisão tem tarefa e cenário de validação; alterações de schema exigem compatibilidade/backup.
- Orquestração considera mudanças não commitadas, arquivos comuns e colisão real do banco SQLite.
- GraphRAG, GNN e treinamento não entram inadvertidamente como pré-requisitos do primeiro cliente.
- O plano permite progresso sem credenciais, mas não chama mock de produção validada.
- Mudanças desta sessão limitadas à documentação; aplicação não corrigida nem publicada.
- Verificação final: links locais dos 11 arquivos de orientação válidos; nomes de testes explicitamente citados encontrados; 35 IDs de cenário sem referência inexistente; `git diff --check` limpo. Os 30 testes existentes de precisão documental passaram. Esses testes conferem tabelas históricas de configuração e não substituem a revisão manual dos novos contratos, tarefas e dependências.
