# Implantação barata com caminho para escalar

Atualizado em 25/09/2026. Este guia cobre o cenário do piloto: um servidor pequeno atendendo de 1 a 5 empresas, com HTTPS válido (obrigatório para o webhook da Meta e para o retorno do Google), backup diário e um caminho claro para crescer sem trocar de arquitetura.

## 1. Onde rodar

| Opção | Custo aproximado | Quando usar |
| --- | --- | --- |
| VPS pequeno (Hetzner CX23/CAX11, Contabo VPS 10) | €5–6/mês | **Recomendado desde o primeiro cliente pagante.** 2 vCPU/4–8 GB bastam para API, workers, PostgreSQL e Redis desta stack. |
| PC de casa + Cloudflare Tunnel | R$ 0 | Só para homologar com o primeiro cliente. A energia e a internet da sua casa viram o SLA do cliente; o túnel resolve HTTPS e CGNAT, mas não resolve uptime. |
| Railway / Fly.io / Render | US$ 5–20/mês | Fácil, mas o plano gratuito dorme (webhook da Meta dá timeout) e Postgres/Redis gerenciados custam à parte. |

Qwen local via Ollama precisa de GPU e não cabe num VPS de €5. Para vender, use IA por API: uma conversa de agendamento custa centavos, e duas empresas com 500 conversas/mês ficam abaixo de US$ 10/mês. Mantenha o Ollama como opção "os dados não saem do servidor" para quem pagar por isso.

## 2. Passo a passo no VPS (Ubuntu 24.04)

```bash
# 1. Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# 2. Código
git clone <seu-repositório> wapp && cd wapp

# 3. Configuração mínima
cp deploy/env.minimal.example .env.production
nano .env.production   # preencha segredos, domínio, chaves de API

# 4. DNS: crie um registro A de app.suaempresa.com.br para o IP do servidor
#    e libere as portas 80 e 443 no firewall (ufw allow 80,443/tcp).

# 5. Migrations como passo de release, depois a stack com Caddy (TLS automático)
docker compose --env-file .env.production run --rm migrate
docker compose --env-file .env.production -f compose.yaml -f deploy/compose.caddy.yaml up -d --build

# 6. Verificação
curl -s https://app.suaempresa.com.br/health/ready
docker compose --env-file .env.production exec api python -m app.tools.preflight_check --require-ready
```

O Caddy obtém o certificado sozinho. Com `APP_PORT=127.0.0.1:8000`, `POSTGRES_PORT=127.0.0.1:5432` e `ADMIN_PORT=127.0.0.1:8501` (já no exemplo), só o Caddy fica exposto à internet.

Atualização de versão: `git pull`, `run --rm migrate`, `up -d --build`. Rollback: `git checkout <tag anterior>` e repetir; as migrations são aditivas e não precisam ser desfeitas para voltar uma versão de código.

### Alternativa: PC de casa com Cloudflare Tunnel

1. Crie um túnel em Zero Trust → Networks → Tunnels, instale o `cloudflared` como serviço e aponte o hostname público para `http://localhost:8000`.
2. Rode a stack sem o Caddy (`docker compose --env-file .env.production up -d`) com `TRUST_PROXY_HEADERS=true` e `PLATFORM_PUBLIC_URL=https://<hostname do túnel>`.
3. O PC precisa ficar ligado 24 h; configure reinício automático do Docker e do `cloudflared`.

## 3. Backup e restauração

```bash
# diário, via cron do host
docker compose --env-file .env.production exec -T api \
  python -m app.tools.export_runtime_backup --output /app/backups/wapp-$(date +%F).json
```

Copie a pasta `backups/` para fora do servidor (Backblaze B2, Cloudflare R2 ou um `rclone` para o seu Drive). O backup lógico remove credenciais de terceiros por segurança: depois de um restore em servidor novo, cada empresa reconecta o WhatsApp e a agenda pelo painel. Guarde `CREDENTIALS_ENCRYPTION_KEY` e `PII_PSEUDONYMIZATION_KEY` no seu gerenciador de senhas: sem elas, tokens e pseudônimos do banco ficam ilegíveis. Detalhes e restauração em `docs/production_runbook.md`.

## 4. Checklist Meta (WhatsApp) — feito uma vez pela sua empresa

1. Crie um app em developers.facebook.com do tipo **Business** e adicione o produto **WhatsApp**.
2. Cadastre-se como **Tech Provider** (gratuito) e crie uma configuração de **Facebook Login for Business** para o Embedded Signup; anote o **Configuration ID** (`META_EMBEDDED_SIGNUP_CONFIG_ID`). Use a versão atual do fluxo (v4): a v2 é desligada em outubro de 2026.
3. Em Configurações do app, copie o **App ID** (`META_APP_ID`) e o **App Secret** (`WHATSAPP_APP_SECRET`).
4. Em WhatsApp → Configuração → Webhooks, informe `https://app.suaempresa.com.br/webhooks/whatsapp` e o `WHATSAPP_WEBHOOK_VERIFY_TOKEN`; assine o campo `messages`.
5. Solicite **App Review** para `whatsapp_business_management` e `whatsapp_business_messaging`, e faça a **verificação do negócio** da sua empresa no Business Manager (sem ela o limite é 250 conversas iniciadas por dia por número).
6. Peça ao cliente um número que não esteja em outro WhatsApp (ou use a opção "Já uso o app WhatsApp Business no celular" para manter o app funcionando junto com a API).
7. No painel, o dono da empresa clica em **Conectar WhatsApp**, escolhe o número e conclui. O token fica cifrado no banco; nenhuma variável de ambiente precisa mudar por cliente.
8. Templates de lembrete (categoria *utility*) são aprovados na Meta e cadastrados na tela Canal; sem eles, lembretes fora da janela de 24 h não saem.

Custos Meta em 2026: mensagens de serviço dentro da janela de 24 h têm 1.000 grátis por número por mês; utilidade (lembretes) custa cerca de R$ 0,04–0,05; marketing cerca de R$ 0,31–0,38. Confirme os valores no site oficial antes de precificar.

## 5. Checklist Google (Agenda) — feito uma vez pela sua empresa

1. Crie um projeto em console.cloud.google.com e ative a **Google Calendar API**.
2. Configure a tela de consentimento OAuth (tipo externo), com política de privacidade e domínio verificado; adicione o escopo `https://www.googleapis.com/auth/calendar`.
3. Crie uma credencial **OAuth client ID** do tipo aplicativo Web com o redirect URI exato `https://app.suaempresa.com.br/platform/integrations/google/callback`; copie `GOOGLE_CALENDAR_CLIENT_ID` e `GOOGLE_CALENDAR_CLIENT_SECRET`.
4. Enquanto o app estiver em modo **Testing**, só usuários listados como testadores (até 100) conseguem conectar, e o token expira em 7 dias, obrigando a reconectar. Para clientes reais, envie o app para **verificação** (escopo sensível: leva semanas; comece cedo).
5. No painel, o dono da empresa clica em **Conectar Google Agenda**, escolhe a conta e o calendário. A agenda interna continua funcionando para quem não conectar.

## 6. Checklist de "ir ao ar" por cliente

- Empresa configurada e publicada (Configuração → cinco passos).
- Bot testado como cliente (Bot Studio) com pelo menos 20 perguntas típicas do nicho.
- WhatsApp conectado e "Testar conexão" verde; primeira mensagem real recebida e respondida.
- Agenda conectada (ou agenda interna aceita conscientemente) e um agendamento de teste criado, remarcado e cancelado.
- Templates de lembrete aprovados e cadastrados.
- Operador convidado, com a tela Operação apresentada (revisões, assumir conversa, falhas de envio).
- Contato de plantão e critério de pausa combinados com o cliente.

## 7. Quando crescer

- Até dezenas de empresas pequenas: o mesmo VPS, subindo para 4 vCPU/8 GB; aumente réplicas do `inbound-worker` conforme a latência do modelo.
- Depois: PostgreSQL e Redis gerenciados, duas réplicas da API atrás do Caddy ou de um balanceador, backups por snapshot. A separação API/workers/fila durável já existe; nada precisa ser reescrito.
