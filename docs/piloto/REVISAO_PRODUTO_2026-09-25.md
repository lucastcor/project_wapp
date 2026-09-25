# Revisão de produto — 25/09/2026

Objetivo declarado pelo dono do projeto: **um bot de atendimento e agendamento pelo WhatsApp pronto para vender por assinatura a pequenas empresas**, que a própria empresa configure pela interface, com conexão de WhatsApp, agenda e Instagram o mais simples possível, rodando inicialmente num PC ou em hospedagem barata, mas com caminho para escalar.

Esta revisão responde a três perguntas: a aplicação é coerente com esse objetivo? Está funcional? Como se compara ao mercado? Ela também registra o que foi corrigido nesta sessão e o que falta, em ordem de prioridade.

## 1. Veredito em poucas linhas

- **O motor é sólido e está acima da média do mercado em segurança operacional**: confirmação explícita antes de agendar, idempotência, concorrência de agenda protegida no banco, fila durável, revisão humana, isolamento entre empresas, auditoria, backup. Isso é raro em concorrentes de R$ 100–300/mês.
- **O produto, do ponto de vista de quem compra, ainda não existe**: o dono de um salão não consegue, sozinho, sair do cadastro para um bot atendendo no WhatsApp. Faltam os três "conectores" que o mercado inteiro oferece em minutos: WhatsApp (Embedded Signup), Google Agenda (OAuth) e Instagram. Hoje as três conexões dependem de um desenvolvedor editando variáveis de ambiente e chamando a API com token de plataforma.
- **A interface fala a língua de quem construiu, não de quem compra**: "Control Tower", "Readiness", "Contact ID", "runtime", "pipeline", "break-glass". Antes desta sessão o wizard de configuração devolvia HTTP 403 para o dono da conta e a tela de Billing dava HTTP 500 numa instalação limpa. Ambos corrigidos (seção 5).
- **O preço cadastrado (R$ 447 / 697 / 1.197) está 3 a 5 vezes acima do que o segmento paga** em produtos equivalentes (R$ 69–297). Vender nesse patamar exige entrega de implantação assistida, o que é um serviço, não SaaS.
- **Conclusão**: o projeto está a algumas semanas de trabalho focado de um piloto vendável para 1–2 clientes, desde que o esforço vá para conexões e onboarding, e não para novas capacidades de IA.

## 2. O que foi verificado nesta sessão

- Leitura de `ESTADO.md`, `P08.md`, `README.md`, runbook e código das rotas, serviços, providers e do painel (`app.js`, 7.5 mil linhas de JavaScript sem framework).
- Aplicação executada localmente (Python 3.13, SQLite, providers `mock`) e percorrida com navegador automatizado na jornada de um dono de negócio que nunca viu o produto: criar conta, painel inicial, wizard, simulador, política, canal, agenda, billing. Capturas de tela foram analisadas uma a uma.
- Dois agentes de apoio: um auditou as integrações no código (WhatsApp, Google, Instagram, onboarding, simulador, LLM, billing, deploy) com referências a arquivo e linha; outro pesquisou o mercado (produtos, preços, padrões de onboarding, regras Meta/Google 2026, hospedagem). O acesso direto aos sites oficiais foi bloqueado pelo proxy da sessão; os dados de mercado vêm de resumos de busca de 2025–2026 e itens não confirmados estão marcados.
- Suíte de testes, Ruff, mypy e verificação de diff após as correções (resultado na seção 5).

## 3. Coerência com o objetivo: o que existe versus o que o comprador espera

| O que um pequeno negócio espera | Estado no código | Avaliação |
| --- | --- | --- |
| Criar conta e começar sozinho | Cadastro self-service existe (`ENABLE_SELF_SERVICE_SIGNUP`), mas criava a empresa no template global e o wizard respondia 403 ao dono | Corrigido nesta sessão: o wizard agora configura e publica a própria empresa |
| Dizer ao bot o que a empresa faz, horários, serviços e preços | Wizard de 5 passos com presets por nicho (clínica, salão, escritório, academia, outro) e tela "Política" com mensagens e regras | Funciona; faltam editor de catálogo depois do wizard, "treinar com o site/PDF" e nichos como barbearia e restaurante |
| Testar o bot como cliente | Bot Studio usa o mesmo motor de produção | Funciona; formatação corrigida nesta sessão, jargão reduzido |
| Conectar o WhatsApp em minutos | Só Meta Cloud API com token, app secret e verify token **globais por instalação** em variáveis de ambiente; vínculo do número só por API com token de plataforma; nenhum formulário na tela Canal | **Lacuna principal**. Sem Embedded Signup, um cliente por instalação na prática |
| Conectar Google Agenda com um clique | Provider Google existe (refresh token), mas não há rota OAuth nem tela; token é colado à mão via API ou `.env` | **Lacuna principal** |
| Instagram no mesmo painel | Inexistente (apenas campo de texto no perfil) | Lacuna; quase todos os concorrentes brasileiros têm |
| Inbox ao vivo com "pausar o bot nesta conversa" | Tela Operação com assumir conversa, revisão e falhas de envio | Existe, mas sem visão de conversa em tempo real (lista de mensagens por contato) |
| Lembretes e redução de faltas | Lembretes, fila de encaixe, templates de utilidade | Existe e é diferencial; depende de template aprovado pela Meta cadastrado à mão |
| Preço previsível na faixa do segmento | R$ 447 / 697 / 1.197 com Stripe | Fora da faixa (seção 6) |
| Rodar barato para 1–2 clientes | Compose com PostgreSQL, Redis, 4 processos Python, Streamlit e build Node; `REQUIRE_SHARED_STATE=true`; sem guia de HTTPS/túnel | Pesado; funciona, mas exige desenvolvedor (seção 7) |

O motor de conversa, agenda e operação corresponde ao objetivo. A camada de produto (conexões, onboarding sem desenvolvedor, linguagem) é o que separa o repositório de algo vendável.

## 4. Front-end: a jornada de quem nunca teve um bot

Achados na ordem em que um dono de negócio os encontra. Os marcados com ✔ foram corrigidos nesta sessão.

1. ✔ **Painel inicial sem caminho.** Após criar a conta, a tela mostrava "Uma camada premium para operar o bot como produto", "Command Center", "Readiness" e quatro métricas zeradas. Agora há um cartão "Primeiros passos" (configurar, testar, conectar WhatsApp, conectar agenda) logo abaixo de um título em linguagem comum.
2. ✔ **Wizard bloqueado para o dono.** "Ativar empresa" chamava um endpoint reservado a credencial de plataforma e devolvia "This platform-level operation requires break-glass administrator credentials". Além disso o wizard criaria uma **segunda** empresa em vez de configurar a que o cadastro criou. Agora, com uma empresa selecionada, o wizard chama `POST /admin/onboarding/configure` (novo, restrito à empresa do usuário), pré-preenche o nome, renomeia a empresa, publica a versão do contexto e mostra "Salvar e publicar". O caminho antigo continua existindo apenas para operador de plataforma sem escopo.
3. ✔ **Simulador mal formatado.** O texto dos balões aparecia deslocado porque a indentação do template HTML era renderizada por `white-space: pre-wrap`. Cada resposta exibia quatro chips técnicos (classificação, ação, pipeline, fonte), o status vinha como enum cru (`SCHEDULE_OPTIONS_PRESENTED`), o botão dizia "Enviar para o runtime" e era obrigatório digitar um "Contact ID". Agora: balões alinhados, um rótulo humano por resposta ("Horários oferecidos", "Bot respondeu"), detalhes técnicos recolhidos num "Detalhes técnicos", botão "Enviar", telefone de teste gerado automaticamente, textos sem menção a rota ou runtime.
4. ✔ **Billing quebrado em instalação limpa.** `GET /admin/billing/plans` respondia 500 porque as variáveis `STRIPE_PRICE_*` vazias do `.env.example` chegavam como string vazia e violavam o índice único da tabela de planos. Agora vazio vira nulo; teste cobre o caso.
5. ✔ **Aviso de regex no cadastro.** O `pattern` do slug era inválido para o navegador (hífen sem escape) e gerava erro no console a cada abertura do formulário.
6. **Onde se insere o contexto da empresa não é óbvio.** Hoje está dividido entre Wizard (dados, horários, serviços), Política (mensagens e regras), Flow Studio (pipelines e mensagens aprovadas) e Training Ops. Para o comprador deveria haver uma única área "Meu negócio" com abas: Dados, Horários, Serviços e preços, Como o bot fala, Regras. O wizard agora cobre o essencial; o resto é reorganização de navegação.
7. **Navegação com 14 itens e nomes internos.** "Control Tower", "Flow Studio", "Automation Studio", "Contacts Desk", "Training Ops", "Billing". Para o piloto, 6 itens bastam: Início, Meu negócio, Testar o bot, Conversas, Agenda, Conexões (+ Plano e Equipe no rodapé). Fine-tuning, importação de CRM e automação visual são recursos avançados que devem ficar escondidos por padrão.
8. **Tela Canal WhatsApp não conecta nada.** Mostra status, um teste de conexão e um formulário para *declarar* templates aprovados na Meta. O texto "O vínculo é feito por um administrador com o identificador do número na Meta" é a admissão de que o cliente não consegue fazer isso.
9. **Agenda Console mistura operação e ferramenta de desenvolvedor.** Campos como "Scheduling profile", "Customer profile", "Contexto do pedido" e "Timezone" livre não fazem sentido para o dono; o calendário mensal em si é bom.
10. **Textos sem acentos** em vários presets e passos ("Clinica", "Salao", "integracao") passam impressão de descuido. Corrigidos os "próximos passos"; os rótulos dos presets ficam para a próxima rodada.
11. **Telefone.** O painel funciona em largura de celular, mas a jornada do wizard e o simulador não foram testados em toque nesta sessão.

## 5. Alterações feitas nesta sessão e verificação

Arquivos: `app/api/admin_routes.py`, `app/services/onboarding_service.py`, `app/services/billing_service.py`, `app/storage/repository.py`, `app/platform/assets/app.js`, `app/platform/assets/styles.css`, `app/platform/index.html`, `tests/test_onboarding_configure.py`, `tests/test_billing_plans_without_stripe_prices.py`.

- Novo endpoint `POST /admin/onboarding/configure` (política de escopo `tenant`, exige `company_id` autorizado; sessão de identidade owner/admin ou token de plataforma). Não cria empresa, não muda slug. Testes: dono configura e publica a própria empresa; não consegue configurar empresa alheia (403); sem escopo é 400; `/onboarding/setup` continua 403 para sessão de identidade; tipo de negócio inválido é 400.
- `repository.update_company_profile` (nome e tipo de negócio).
- `seed_plans` normaliza preços Stripe vazios para nulo; teste de serviço e de endpoint.
- Painel: cartão "Primeiros passos", títulos em linguagem comum, wizard em modo "minha empresa", simulador reformatado, rótulos humanos de ação, pattern do slug corrigido, versão dos assets atualizada para invalidar cache.
- Verificação: suíte completa, Ruff, mypy e `git diff --check` (resultado registrado em `evidencias/P08.md`). A jornada completa (cadastro → wizard → publicação → simulador → política com "Versão 1 · publicada" → billing) foi repetida no navegador sem erro de API.

O que **não** foi alterado: nenhuma integração real, nenhuma mudança de segurança além da rota nova (que reaproveita o escopo central), nenhuma mudança de preço ou plano.

## 6. Comparação com o mercado

Resumo do levantamento (set/2026, fontes no final). Preços são plano de entrada mensal e podem variar.

| Produto | Público | Entrada | WhatsApp | Agenda | Instagram | Como facilita o começo |
| --- | --- | --- | --- | --- | --- | --- |
| Umbler Talk (BR) | PME e autônomo | R$ 69 + créditos de IA | Cloud API oficial | Google Agenda nativo | Sim | Wizard; bot treinado com PDF e FAQ |
| Chatvolt (BR) | PME | R$ 97 (500 conversas) | Cloud API / QR (não verificado) | via ferramentas | Sim | "Agente em 5 minutos"; treina com URL/PDF; teste sem cartão |
| Zaia (BR) | Autônomos e PME | ~R$ 150–249 | Cloud API **ou QR** | via integração | Sim | "Funcionário de IA" com personalidade + site + PDF |
| GPT Maker (BR) | Autônomos e PME | créditos por interação | Cloud API ou QR | via API | Sim | Escolha do modo de conexão no painel |
| Nexloo (BR) | Clínicas e serviços | R$ 497 (implantação inclusa) | Cloud API | não verificado | Sim | A equipe deles configura |
| Trinks + IA (BR, beleza) | Salões e barbearias | R$ 89–249 + créditos | via parceiros | Agenda própria | Sim | A agenda já existe; a IA "encaixa" |
| Wati (global) | PME | US$ 49–59 | Embedded Signup | via Zapier | Sim | Signup embutido em minutos |
| Respond.io (global) | PME | US$ 79 (IA a partir de US$ 159) | Embedded Signup + Coexistence | via Zapier | Sim | Mantém o app do celular funcionando |
| Chatwoot (open source) | Inbox | grátis self-host | Cloud API / 360dialog | não | Sim | Inbox humano pronto; IA opcional |
| Meta Business Agent | PME sem TI | app grátis; IA cobrada por token | nativo no app | agenda do app | via Business Suite | Nada a instalar |

Padrões que aparecem em praticamente todos os líderes e que o comprador vai procurar:

1. Conectar o WhatsApp em menos de 5 minutos por **Embedded Signup** (popup da Meta) ou **Coexistence** (escanear QR no app WhatsApp Business e continuar usando o celular). A conexão por QR não oficial (Baileys/Evolution/Z-API) ainda é vendida por alguns como atalho, mas há relatos de banimento em massa em 2026 e líderes globais a abandonaram.
2. Templates por nicho e "treinar o bot com o site, PDF ou FAQ".
3. Persona e regras em linguagem natural, chat de teste ao lado da configuração.
4. Inbox ao vivo com transbordo humano e "pausar IA nesta conversa".
5. Google Agenda nativo ou agenda própria; lembretes com botão de confirmação como argumento de ROI.
6. Instagram DM no mesmo painel.
7. Checklist de "ir ao ar": número, nome de exibição, forma de pagamento Meta, verificação do negócio.
8. Teste grátis de 7 dias sem cartão; preço fixo entre R$ 97 e R$ 497; acima de R$ 600 é mercado médio (Zenvia, Botmaker, Poli).
9. Ouvir áudio (clientes brasileiros mandam áudio). O projeto hoje responde "só entendo texto".

**Onde este projeto está melhor que a média**: confirmação explícita e ambiguidade sem efeito, concorrência de agenda no banco, fila de encaixe após cancelamento, revisão humana, versionamento e rollback de configuração, isolamento entre empresas com política deny-by-default, auditoria, LGPD (pseudonimização de PII), backup/restore ensaiado. Concorrentes de entrada raramente têm isso, e é o argumento certo para clínicas.

**Onde está pior**: conexões, onboarding sem desenvolvedor, Instagram, áudio, treinar com site/PDF, inbox em tempo real, preço.

Fatos da plataforma Meta relevantes para a decisão (2026): cobrança por mensagem, não por conversa; utilidade ~R$ 0,04–0,05, marketing ~R$ 0,31–0,38, serviço grátis na janela de 24 h com **1.000 mensagens grátis por número por mês a partir de 1/out/2026** (valor após o limite não confirmado); Embedded Signup exige cadastro como **Tech Provider** (gratuito, com App Review) e a versão 2 é desligada em **15/out/2026**, portanto implementar direto a v4; sem verificação do negócio o limite é 250 conversas iniciadas pela empresa por dia. Instagram Messaging API via "Instagram Login" dispensa Página do Facebook, mas exige App Review. Google Calendar usa escopo sensível: app em modo "Testing" atende até 100 usuários de teste com refresh token que expira em 7 dias; produção exige verificação (semanas). Para 1–2 clientes o modo Testing serve, com re-login semanal.

## 7. Custo e hospedagem para 1–2 clientes, com caminho para escalar

- **Webhook da Meta exige HTTPS público válido.** Rodar no PC de casa é viável com Cloudflare Tunnel (grátis, sem abrir porta, resolve CGNAT), mas o PC precisa ficar ligado 24 h e a energia/ISP viram o SLA do cliente. Aceitável para homologação com o primeiro cliente, não para cobrar mensalidade de dois.
- **Recomendação**: um VPS pequeno (Hetzner CX23 ≈ €5,5/mês ou Contabo ≈ €5,5/mês) com Coolify ou o `compose.yaml` atual, Caddy/Traefik para TLS automático e backup diário para objeto (R2/B2). Isso atende dezenas de empresas pequenas antes de precisar de mais.
- **LLM**: uma conversa de agendamento (6–10 turnos) custa cerca de US$ 0,003–0,01 em modelos baratos de API; 2 clientes × 500 conversas/mês fica abaixo de US$ 10/mês. Qwen local via Ollama só compensa por privacidade ou acima de milhares de requisições/dia; num VPS de €5 não há GPU, e a pergunta comum levou 13,7 s no Mac local com 4 inferências sequenciais. Para vender, use API (OpenAI já suportado; vale adicionar Anthropic/Gemini como provider) e reduza para 1–2 chamadas por mensagem. Manter Ollama apenas como opção "dados não saem do servidor".
- **Simplificar a pilha para o primeiro cliente**: PostgreSQL e Redis no mesmo VPS já estão no compose; retirar Streamlit (`admin`) da imagem padrão; documentar um `.env` mínimo de 15 variáveis, não 100.

## 8. Roadmap priorizado

Ordem pensada para gerar receita o quanto antes sem reescrever nada.

**P0 — primeiro cliente pagante com implantação assistida (2–4 semanas)**

1. Cadastrar-se como Tech Provider na Meta, criar app e implementar **Embedded Signup v4** com Coexistence: botão "Conectar WhatsApp" na tela Canal, callback que troca o código por token por empresa, grava `phone_number_id`, `waba_id` e token cifrado por empresa, registra o webhook e o verify token por instalação. Substituir as credenciais globais por credenciais por empresa no provider (hoje `WHATSAPP_API_TOKEN` global).
2. **Google Agenda por OAuth**: rota de autorização e callback, refresh token cifrado por empresa, escolha do calendário na tela Agenda, botão "Conectar". Manter "agenda interna" como padrão quando não conectar.
3. Simplificar navegação para 6 itens e esconder recursos avançados atrás de "Avançado".
4. Área "Meu negócio" com editor de serviços e preços fora do wizard, e presets com acentos e nichos faltantes (barbearia, estética, pet, restaurante).
5. Implantação num VPS com HTTPS, `.env` mínimo, backup diário, e o roteiro de `VALIDACAO.md` com número real.
6. Homologar um cliente real com você operando junto (é o que Nexloo cobra R$ 497 para fazer).

**P1 — self-service e diferenciação (4–8 semanas)**

1. Inbox ao vivo por contato com "pausar o bot" e resposta manual na mesma tela.
2. "Treinar com o site/PDF": já existe base de conhecimento com RAG; falta o botão que importa uma URL e a apresentação disso no wizard.
3. Transcrição de áudio (Whisper via API) porque clientes mandam áudio.
4. Instagram DM via Instagram Login (mesmo motor, canal novo em `outbound_dispatch`).
5. Reduzir chamadas de LLM por mensagem e adicionar provider Anthropic/Gemini com escolha por empresa.
6. Checklist "ir ao ar" na tela inicial que lê o estado real (contexto publicado, número conectado, agenda conectada, templates aprovados).

**P2 — escala**

1. Planos alinhados ao mercado com trial de 7 dias sem cartão; billing self-service (hoje exige token de plataforma para iniciar trial).
2. Verificação Meta e Google em produção, templates criados pela API (hoje só declarados).
3. Métricas de ativação, agendamentos concluídos, intervenção humana e custo por conversa, já previstas em `04-PILOTO-E-EVOLUCAO.md`.

## 9. Decisões que só o dono do produto pode tomar

1. **Nicho de entrada.** Salão/estética ou clínica. Clínica valoriza segurança e revisão humana (ponto forte do projeto); salão valoriza preço e Instagram. Recomendo clínicas e consultórios pequenos.
2. **Preço.** Sugestão: R$ 147/mês (1 número, agenda, lembretes), R$ 297/mês (Instagram, equipe, relatórios), implantação assistida opcional de R$ 500–1.000. Os planos atuais devem ser revistos antes de qualquer venda.
3. **Modelo de IA.** API (recomendado para venda) ou Qwen local (privacidade, mais latência e servidor com GPU).
4. **Conexão oficial apenas.** Não oferecer QR não oficial mesmo que o cliente peça; o risco de banimento do número do cliente é seu risco de reputação.
5. **Hospedagem.** VPS desde o primeiro cliente pagante; PC de casa só para homologação.

## 10. Fontes consultadas (seleção)

Meta: developers.facebook.com/documentation/business-messaging/whatsapp/pricing · …/embedded-signup/overview/ · …/solution-providers/get-started-for-tech-providers · …/messaging-limits · docs.360dialog.com/partner/onboarding/whatsapp-coexistence · developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/ · respond.io/blog/whatsapp-pricing-change-2026 · ppc.land/metas-embedded-signup-v4-is-here-but-the-october-15-clock-is-ticking/
Google: developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification · support.google.com/cloud/answer/15549945
Produtos: help.umbler.com · expandenegocios.com.br/chatvolt/ · zaiadocs.gitbook.io · developer.gptmaker.ai/agents/agent-channels · nexloo.com/planos/ · negocios.trinks.com/planos/ · chatarmin.com/en/blog/wati-pricing · chatarmin.com/en/blog/respond-io-pricing · chatwoot.com/pricing/self-hosted-plans · blog.cubosuite.com.br/baileys-whatsapp-como-funciona-por-que-esta-sendo-bloqueado-e-o-que-usar/
Hospedagem e IA: howtohosting.guide/contabo-vs-hetzner/ · coolify.io/docs/integrations/cloudflare/tunnels/all-resource · markaicode.com/pricing/ollama-vs-openai-api-pricing/

Itens marcados "não verificado" no levantamento de apoio foram omitidos ou sinalizados aqui; preços e regras da Meta mudam com frequência e devem ser reconfirmados no site oficial antes de precificar.
