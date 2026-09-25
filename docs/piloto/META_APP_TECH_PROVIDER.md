# Criar o app da Meta como Tech Provider — passo a passo

Atualizado em 25/09/2026. Este é o roteiro para a **sua empresa** (a que vende o SaaS) criar um único app na Meta e ser reconhecida como **Tech Provider**, para que cada cliente conecte o próprio número do WhatsApp pelo botão "Conectar WhatsApp" do painel. Faz-se uma vez; depois nenhuma variável de ambiente muda por cliente.

Como foi produzido: a documentação oficial da Meta (developers.facebook.com) não pôde ser lida diretamente do ambiente em que este guia foi escrito, então ele consolida a documentação oficial via resumos de busca, guias de parceiros (Twilio, Infobip, 360dialog, Bird, Telnyx, Chatwoot) e relatos brasileiros de 2025–2026, com verificação cruzada. Rótulos de tela estão em inglês como aparecem nas fontes; a interface em português pode variar. Itens marcados **[não confirmado]** devem ser conferidos na tela. Antes de gravar vídeos ou preencher formulários, abra a página oficial "Become a Tech Provider" (developers.facebook.com → Documentation → Business Messaging → WhatsApp → Solution Providers), que é a fonte de autoridade.

## Resumo em uma tela

| # | O que fazer | Onde | O que sai daqui |
| --- | --- | --- | --- |
| 1 | Conta de desenvolvedor Meta + 2FA | developers.facebook.com | acesso ao App Dashboard |
| 2 | Portfólio empresarial da sua empresa (CNPJ) | business.facebook.com | dono do app; será verificado |
| 3 | Criar o app (tipo Business, caso de uso WhatsApp) | App Dashboard → Create App | `META_APP_ID` |
| 4 | Copiar App Secret; preencher política de privacidade, termos, exclusão de dados, ícone, domínios | App settings → Basic | `WHATSAPP_APP_SECRET` |
| 5 | Verificar a sua empresa | Business Suite → Security Center | portfólio "Verified" |
| 6 | Iniciar o onboarding de Tech Provider (independente) | Use cases → Customize → Tech Provider onboarding | status de Tech Provider |
| 7 | Criar a configuração do Embedded Signup e liberar o domínio do painel | Facebook Login for Business → Configurations / Settings | `META_EMBEDDED_SIGNUP_CONFIG_ID` |
| 8 | Configurar o webhook do app e assinar os campos | WhatsApp → Configuration → Webhook | `WHATSAPP_WEBHOOK_VERIFY_TOKEN` (você inventa) |
| 9 | Testar em modo Development (papéis do app, número de teste, sandbox) | App roles; WhatsApp → API Setup / Quickstart | fluxo validado no seu painel |
| 10 | App Review (Advanced Access nas duas permissões do WhatsApp) | App Review → Permissions and Features | clientes reais podem conectar |
| 11 | Access Verification e modo Live | App settings → Basic; chave Development/Live | onboarding público, limite 200 clientes/7 dias |
| 12 | Data Use Checkup anual | alerta no App Dashboard | app continua ativo |

Tempo realista do zero até clientes externos: **3 a 6 semanas**, dominado pela verificação da empresa e pelo App Review. Comece pelos passos 1–5 hoje; o resto anda em paralelo.

## O que é o Tech Provider e por que ele, e não um BSP

- **Tech Provider** é o nível de parceria da Meta para quem fornece software que conecta clientes à WhatsApp Business Platform. É **gratuito**. Você não tem linha de crédito com a Meta: cada cliente cadastra a própria forma de pagamento e paga a Meta diretamente pelo uso da API; você cobra só a assinatura do seu software. Desde 30/06/2025 todo fornecedor de software que oferece WhatsApp precisa estar inscrito como Tech Provider.
- **Solution Partner (BSP)** tem linha de crédito, pode faturar o cliente em nome da Meta e embute margem de 5–20%. Exige contrato e volume; não é o caso agora.
- Você **não precisa** de número nem de WABA de produção próprios: cada cliente é dono da sua WABA e do seu número. Para demonstração servem o número de teste do app (até 5 destinatários) e a conta sandbox.
- **Tech Partner** é um nível acima, só depois de ser Tech Provider; irrelevante para o piloto.

## Passo a passo

### 1. Conta de desenvolvedor e portfólio empresarial

1. Entre em developers.facebook.com com o **seu perfil pessoal do Facebook** (real, com histórico; ele será o administrador de tudo) e conclua o registro como desenvolvedor: a Meta confirma telefone e e-mail e usa esses contatos para avisos importantes (política, Data Use Checkup). Ative a autenticação de dois fatores nesse perfil; o programa exige 2FA no Business Manager.
2. Em business.facebook.com, crie ou escolha o **portfólio empresarial** (Business Portfolio) da **sua** empresa, com a razão social exatamente como no cartão CNPJ. Para MEI, fontes brasileiras dizem que a Meta aceita o CCMEI **[não confirmado na fonte oficial]**. Tenha um site próprio e e-mail no mesmo domínio: facilita a verificação.
3. Esse portfólio será o dono do app e o que passará pela verificação. Não use o portfólio de um cliente.

### 2. Criar o app

1. App Dashboard (developers.facebook.com/apps) → **Create App**. Nome do app **sem** "WhatsApp", "WA", "Meta" ou "Facebook" (diretriz de marca; motivo de reprovação). Use o nome comercial do seu produto: é o que o cliente vê no popup.
2. Em **Use cases** há dois caminhos documentados: (a) "Connect with customers through WhatsApp" (documentação oficial de 2025–2026; já adiciona o produto WhatsApp e mostra o painel de Tech Provider) ou (b) "Other" → Type **Business** (guias de parceiros; exige adicionar o produto WhatsApp depois). Se um deles não aparecer, use o outro **[rótulos não confirmados na tela de 2026]**.
3. Selecione o portfólio empresarial do passo 1 no dropdown e conclua. O **App ID** aparece no topo do painel e em App settings → Basic: é o `META_APP_ID`.
4. Se veio pelo caminho (b): menu lateral → Add product → **WhatsApp** → Set up; aceite os termos da WhatsApp Business e do Cloud API hosting e vincule o portfólio. A Meta cria uma WABA de teste com um número de teste e um token temporário de 24 h (ver passo 9).

### 3. App settings → Basic

1. **App secret** → Show (pede a senha do Facebook) → copie para `WHATSAPP_APP_SECRET`. Ele valida a assinatura `X-Hub-Signature-256` dos webhooks e troca o código do Embedded Signup por token. Se vazar, use Reset e atualize o `.env` imediatamente (até lá todos os webhooks falham na assinatura).
2. Preencha **Privacy Policy URL** e **Terms of Service URL** (páginas públicas HTTPS no seu domínio, em português, dizendo claramente que o serviço recebe mensagens dos contatos das empresas clientes via WhatsApp e por quanto tempo guarda), **Data Deletion Instructions URL**, **App icon** (1024×1024), **Category** (Business and pages ou Messaging), **App domains** (o domínio do painel) e e-mail de contato. Política genérica, lenta ou em outro domínio é motivo recorrente de reprovação no App Review e no Data Use Checkup.

### 4. Verificar a sua empresa (Business Verification)

1. Business Suite → Settings → Business settings → **Security Center** → **Business verification** → Start verification. Também acessível pelo App Dashboard → Use cases → Customize (lápis) → Tech Provider onboarding → Start verification.
2. Informe razão social, CNPJ, endereço, telefone e site; anexe cartão CNPJ (PDF da Receita), contrato social ou CCMEI, comprovante de endereço em nome da empresa com até 3 meses; confirme por e-mail no domínio do site ou por verificação de domínio (registro DNS TXT). Nome, endereço e telefone têm de bater entre documento, CNPJ e site.
3. Prazo: de 24 h a algumas semanas; planeje 1–2 semanas. Não confunda com "Meta Verified" (selo pago): não é isso. Sem esta verificação o App Review de Tech Provider não conclui e o limite de onboarding fica em 10 clientes por 7 dias.

### 5. Onboarding como Tech Provider

1. App Dashboard → **Use cases** → Customize (ícone de lápis) no caso de uso WhatsApp → menu lateral **Tech Provider onboarding** (redação da página oficial atual). Em apps mais antigos o mesmo painel aparece em WhatsApp → Quickstart → "Scale your Business" → **Become a Tech Provider** → Start onboarding.
2. Aceite os **Tech Provider Terms** e escolha **Onboard without a partner** / "Independent Tech Provider" (você não usa BSP). O painel lista os passos restantes (verificação da empresa, App Review, Access Verification) com marcação verde ao concluir.
3. Não existe um "interruptor" de Tech Provider; o status vem de concluir esses passos.

### 6. Configuração do Embedded Signup (Facebook Login for Business)

1. Se o produto não estiver no menu: Add product → **Facebook Login for Business** → Set up (não confundir com o "Facebook Login" comum, para consumidores).
2. Facebook Login for Business → **Configurations** → **Create from template** → template "WhatsApp Embedded Signup Configuration With 60 Expiration Token" (nome como citado nas fontes; o "60" são 60 dias de validade do token) ou **Create configuration** manual: nome interno; Login variation **WhatsApp Embedded Signup** (se a tela só mostrar "Business", use-a **[não confirmado]**); Assets **WhatsApp accounts** (marque as sub-opções); Permissions **whatsapp_business_management** e **whatsapp_business_messaging**; **não** peça `business_management` (só BSP com linha de crédito precisa; pedir permissão desnecessária é motivo de reprovação).
3. **Validade do token.** O template padrão gera token de negócio que expira em 60 dias. O painel do project_wapp guarda o token por empresa e, quando a Meta devolve o código 190 (token inválido), marca "precisa reconectar" na tela WhatsApp, mas **não renova o token sozinho**. Se a tela de configuração oferecer a opção de token sem expiração, escolha-a **[existência da opção não confirmada]**; caso contrário, cada cliente precisará clicar em "Conectar WhatsApp" de novo a cada 60 dias até que a renovação automática seja implementada (pendência registrada ao final).
4. Copie o **Configuration ID** da lista de configurações → `META_EMBEDDED_SIGNUP_CONFIG_ID`. Não é o App ID.
5. Facebook Login for Business → **Settings** → Client OAuth settings: **Login with the JavaScript SDK** = Yes; **Allowed Domains for the JavaScript SDK** = domínio do painel (ex.: `https://app.suaempresa.com.br`); **Valid OAuth Redirect URIs** = a URL da página que abre o popup (ex.: `https://app.suaempresa.com.br/platform`). Repita o domínio em App settings → Basic → **App domains**. Só HTTPS, sem `localhost`, sem curinga. Esquecer um destes três lugares é a causa mais comum de "o popup abre e nada acontece".
6. **Versão v4 vs v2.** A Meta desliga o Embedded Signup v2 em outubro de 2026 (a página principal diz 15/10; a página de Coexistence diz 08/10 — trate 08/10/2026 como limite). Configurações criadas agora já nascem na v4, em que a configuração define produtos e permissões. O painel do project_wapp chama `FB.login` com `config_id`, `response_type: "code"` e `extras { setup: {}, featureType, sessionInfoVersion: "3" }`, que é o formato documentado hoje; fontes divergem sobre se `featureType` e `sessionInfoVersion` continuam sendo honrados na v4. **Teste no Embedded Signup Builder / Integration Helper (Use cases → Customize → WhatsApp) antes de 08/10/2026** e, se o evento de conclusão vier em formato diferente, ajuste o listener em `app/platform/assets/app.js` (pendência registrada ao final).

### 7. Webhook do app

1. App Dashboard → WhatsApp → **Configuration** → Webhook → **Edit**: Callback URL `https://app.suaempresa.com.br/webhooks/whatsapp`; Verify token = uma string longa aleatória que você inventa (`openssl rand -hex 32`) e coloca em `WHATSAPP_WEBHOOK_VERIFY_TOKEN`. Não use `+` no caminho ou na query da URL.
2. O servidor precisa estar no ar, com certificado válido, **antes** de clicar **Verify and save**: a Meta faz um GET com `hub.mode`, `hub.verify_token` e `hub.challenge`, e o project_wapp responde o challenge em texto puro (`app/api/routes.py`, rota `GET /webhooks/whatsapp`).
3. **Manage** (Webhook fields): assine **messages** e **account_update** (obrigatório: traz `PARTNER_ADDED` quando um cliente conclui o Embedded Signup e `PARTNER_REMOVED` quando desconecta). Recomendado: `message_template_status_update`, `phone_number_quality_update`, `phone_number_name_update`, `account_review_update`. Para Coexistence a Meta lista também `history`, `smb_app_state_sync` e `smb_message_echoes` (o project_wapp ainda não processa esses três; sem eles a conexão funciona, só não sincroniza histórico do app).
4. Assinar campos no app **não** liga o app à WABA de cada cliente. Isso é feito pelo backend a cada conexão (`POST /{waba_id}/subscribed_apps`), já implementado em `app/services/whatsapp_signup_service.py`. O botão **Test** ao lado de `messages` envia um payload sintético e não prova que mensagens reais chegarão.
5. Todo POST chega com `X-Hub-Signature-256` = HMAC-SHA256 do corpo bruto com o App Secret; o project_wapp valida e responde 200 rápido, enfileirando o processamento. Entregas sem 200 são reenviadas com backoff por até 7 dias; o processamento é idempotente por id de mensagem.

### 8. Preencher o `.env` e subir

```dotenv
META_APP_ID=<App ID>
WHATSAPP_APP_SECRET=<App secret>
META_EMBEDDED_SIGNUP_CONFIG_ID=<Configuration ID>
WHATSAPP_WEBHOOK_VERIFY_TOKEN=<string que você inventou>
META_GRAPH_API_VERSION=v25.0
CREDENTIALS_ENCRYPTION_KEY=<chave Fernet gerada>
# deixe vazios no modelo por empresa:
WHATSAPP_API_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
```

Versão da Graph API: v26.0 é a mais recente (29/07/2026) e v25.0 (18/02/2026) vale até 2028; o padrão `v23.0` do projeto ainda é suportado. Use `v25.0` e rode `pytest tests/test_whatsapp_embedded_signup.py` depois de trocar. Com essas variáveis, a tela **WhatsApp** do painel passa a mostrar os botões "Conectar WhatsApp" e "Já uso o app WhatsApp Business no celular"; sem elas mostra o que falta.

### 9. Testar antes do App Review (modo Development)

- Enquanto o app está em **Development**, o popup do Embedded Signup só mostra as permissões para quem tem papel **Admin, Developer ou Tester** no app (App Dashboard → App roles → Roles; a pessoa precisa aceitar o convite em developers.facebook.com). Clientes reais só depois do passo 10. Para um piloto supervisionado com 1–2 clientes, adicionar o dono de cada cliente como Tester é um caminho válido **[teto de testers não confirmado]**.
- **Número de teste da Meta** (WhatsApp → API Setup): grátis, envia só para até 5 números que você cadastra (cada um confirma por OTP; não dá para remover depois), token temporário de 24 h. Serve para gravar os vídeos do App Review usando as variáveis legadas `WHATSAPP_API_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` numa instalação de teste, não para clientes.
- **Conta sandbox** (WhatsApp → Quickstart → Testing Integrations → **Claim sandbox account** **[rótulo não confirmado por segunda fonte]**): simula um cliente concluindo o Embedded Signup e devolve `waba_id`, `phone_number_id` e o `code` reais; vale 30 dias; o número não envia nem recebe mensagens. Útil para validar o `POST /admin/whatsapp/embedded-signup/complete` de ponta a ponta.
- **Número real seu**: um chip pré-pago que nunca teve WhatsApp, num segundo portfólio, é o teste mais fiel: conecta pelo painel, recebe e responde mensagens.
- Local: use um túnel HTTPS (`cloudflared tunnel --url http://localhost:8010` ou ngrok) e refaça "Verify and save" toda vez que a URL mudar; nunca use túnel com cliente real.
- Confira nos logs a chegada do `account_update` com `PARTNER_ADDED` após uma conexão.

### 10. App Review (Advanced Access)

1. App Dashboard → **App Review** → **Permissions and Features** → **Request Advanced Access** em `whatsapp_business_messaging` e `whatsapp_business_management` (no dashboard por casos de uso o mesmo pedido aparece em Use cases → Customize → Permissions). Só essas duas.
2. Para cada permissão, uma **descrição escrita** (você é Tech Provider; cada cliente conecta o próprio número pelo Embedded Signup; o software atende e agenda em nome do cliente) e um **screencast**: vídeo 1 — uma mensagem criada e enviada pelo painel e **recebida no WhatsApp** do destinatário na mesma gravação; vídeo 2 — o painel criando ou registrando um template. Recomendação brasileira: mostrar também o Embedded Signup completo (login, escolha do número, volta ao painel com "Testar conexão" verde) e uma mensagem recebida. Grave no domínio de produção com HTTPS, sem `localhost`, sem valores "change-me".
3. A verificação da empresa (passo 4) precisa estar concluída; o diálogo "App verification details" bloqueia a submissão sem ela.
4. Prazo: a Meta fala em ~24 h; relatos de 2025–2026 variam de 2–7 dias úteis a até 20 dias. Cada reprovação reinicia o ciclo; motivos comuns: vídeo sem a mensagem chegando, permissão desnecessária, política de privacidade genérica, app inacessível ao revisor.

### 11. Access Verification, modo Live e limites

- **Access Verification**: questionário que confirma que o app pertence à sua empresa verificada e que você atua como Tech Provider (fornece software, não cobra pela API). Fica em App settings → Basic (cartão "Verifications") ou no painel Quickstart → Onboarding **[caminho diverge entre fontes]**; decisão em ~5 dias. Com verificação da empresa + App Review + Access Verification, o limite de onboarding sobe automaticamente de **10 para 200 novos clientes por janela de 7 dias**. Para o piloto, 10 basta.
- **Live**: chave Development/Live no topo do App Dashboard, só depois do App Review; exige Privacy Policy URL. Em Live, qualquer dono de negócio conclui o Embedded Signup, mas apenas permissões com Advanced Access funcionam para quem não tem papel no app.
- **Data Use Checkup**: certificação anual por um administrador do app; a Meta avisa 60 dias antes; perder o prazo desativa o app e derruba o atendimento de todos os clientes de uma vez. Ponha no calendário.

## O que cada cliente faz do lado dele

1. Clica em **Conectar WhatsApp** no painel, entra com o Facebook que administra o negócio dele, cria ou escolhe o portfólio e a WABA, e escolhe o número: ou um número **limpo** (nunca teve WhatsApp; recebe SMS ou ligação; fixo funciona por voz; número atrás de URA não), ou **Já uso o app WhatsApp Business no celular** (Coexistence: mantém o app, lê um QR, precisa de app 2.24.17+, abrir o app pelo menos a cada ~14 dias; 20 mensagens/s; sem grupos, listas de transmissão e chamadas pela API).
2. Cadastra **forma de pagamento** no WhatsApp Manager da WABA dele (business.facebook.com → WhatsApp Manager → configurações de pagamento **[rótulo não confirmado]**). Desde 01/07/2026 contas novas no Brasil são faturadas em **reais**, por Facebook Serviços Online do Brasil, sem IOF. Sem forma de pagamento, templates fora da janela de 24 h falham.
3. Escolhe um **nome de exibição** que bata com o nome legal e o site (nada de nome de pessoa, termo genérico, slogan ou tudo em maiúsculas); aprovação em minutos a horas. Sem nome aprovado o número fica restrito.
4. **Limites**: portfólio novo inicia com 250 contatos únicos que a empresa pode iniciar por 24 h (respostas dentro da janela de 24 h não contam); a verificação do negócio do cliente sobe o limite (fontes divergem entre 1.000 e 2.000) e depois a Meta escala sozinha para 10 mil, 100 mil e ilimitado conforme qualidade e uso. Sem verificação, no máximo 2 números na WABA.
5. Não ativar o **Meta Business Agent** (IA da própria Meta, cobrada por token desde 01/08/2026) no mesmo número: resposta dupla e custo dobrado.

## Custos que o cliente paga à Meta (Brasil, setembro de 2026)

| Tipo | Valor por mensagem entregue | Observação |
| --- | --- | --- |
| Recebidas do cliente final | grátis | sempre |
| Serviço (resposta livre na janela de 24 h, inclui as respostas do bot) | grátis até 30/09/2026; a partir de 01/10/2026, 1.000 grátis por número por mês e depois ≈ R$ 0,035 | franquia por número, zera todo mês; um bot prolixo estoura rápido |
| Utilidade (lembretes) | ≈ R$ 0,035–0,05 **[fontes divergem]** | a partir de 01/10/2026 também é cobrada dentro da janela |
| Autenticação | ≈ R$ 0,035–0,19 **[fontes divergem]** | não usado pelo piloto |
| Marketing | R$ 0,3217 (US$ 0,0625) | preço fixo; só com consentimento registrado |
| Janela de 72 h após anúncio Click-to-WhatsApp | grátis, inclusive templates | ponto de entrada gratuito |

Confirme a tabela em reais na página oficial de preços antes de publicar valores para clientes. Sobre os termos de uso: desde 15/01/2026 a Meta restringe chatbots de IA de propósito geral na plataforma; o atendimento e agendamento do próprio negócio (caso do project_wapp) continua permitido. No Brasil houve decisão do CADE em janeiro de 2026 e recurso da Meta; a situação em setembro de 2026 não foi verificada.

## Pendências no código identificadas por este roteiro

Registradas para a próxima etapa; nenhuma impede o piloto com 1–2 clientes.

1. **Renovação do token de 60 dias**: se a configuração só permitir token com expiração, implementar a renovação automática antes do vencimento (hoje o painel só sinaliza "reconectar" ao receber o código 190).
2. **Compatibilidade com Embedded Signup v4**: testar no Integration Helper antes de 08/10/2026 se `featureType`/`sessionInfoVersion: "3"` continuam sendo honrados e se o evento `WA_EMBEDDED_SIGNUP` mantém o formato; ajustar `app.js` se necessário.
3. **Webhook `account_update`**: tratar `PARTNER_REMOVED` desativando o vínculo da empresa automaticamente (hoje o evento é recebido e ignorado).
4. **Coexistence**: processar `smb_message_echoes` (mensagens enviadas pelo app do celular) para não responder a uma conversa que o dono já respondeu.
5. **Checklist pós-conexão no painel**: lembrar o cliente de cadastrar forma de pagamento, aprovar o nome de exibição e verificar o negócio; mostrar o contador mensal de mensagens de serviço por número (franquia de 1.000).
