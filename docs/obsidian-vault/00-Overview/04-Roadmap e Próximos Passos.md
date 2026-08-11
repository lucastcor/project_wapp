---
title: Roadmap e Próximos Passos
type: roadmap
tags: [roadmap, planejamento]
updated: 2026-07-26
---

# Roadmap e Próximos Passos

> Reflete o estado declarado em `README.md`, `docs/architecture.md` e o que efetivamente está pronto/falta no código. Use como direção, não como compromisso.

## Concluído — Releases 1A–1A.2.1 (2026-07-26)

- **1A**: segurança de ingress, isolamento de tenant no realtime, RBAC explícito e
  company context durável.
- **1A.1**: correção de PostgreSQL (I-016 e I-017), garantias de ciclo de vida de
  conexão, vínculo canal→empresa do WhatsApp e CI com PostgreSQL.
- **1A.2/1A.2.1**: conflitos race-safe, portabilidade completa e sanitizada,
  restore transacional, readiness autoritativa e Docker Compose validado com
  PostgreSQL, API, worker e painel saudáveis.

Detalhe em [[00-Overview/03-Estado Atual do Produto]].

## Implementado — Release 1B (fundação SaaS)

- Dependências de API/admin/dev separadas, fixadas com hashes e Python 3.13.
- Vercel stateless, migração one-shot e Docker multi-stage com API, admin e
  worker separados.
- Identidade real, memberships, convites, recuperação, sessão opaca, CSRF e
  papéis `owner/admin/operator/viewer`.
- Gestão de equipe na plataforma: convite, listagem, mudança de papel/estado e
  proteção do último owner.
- Integridade de tenant no banco e guards cross-tenant.
- Claims, leases, retries e dead-letter em outbound e lembretes.
- Redis compartilhado para rate limit e realtime.
- Resend preparado para emails transacionais.

O critério técnico local foi atendido. O critério operacional externo
(rollback e restore drill no banco gerenciado escolhido) pertence à ativação
de staging.

## Implementado — Release 1C (monetização)

- Produtos/preços Stripe mapeados por IDs configuráveis.
- Checkout e Customer Portal para o owner.
- Webhook assinado, ledger, hash do payload, deduplicação e proteção contra
  eventos fora de ordem.
- Reconciliação de customer/subscription/invoice/payment/trial/cancelamento.
- Grace period e enforcement nos fluxos reais.
- Testes de assinatura, replay, falha de pagamento, quotas e refund de reserva.

O código está concluído; falta executar o sandbox contra uma conta Stripe real
e configurar webhook/preços no ambiente de staging.

## Próxima fase — Release 1D: ativação de staging e piloto

Ordem recomendada:

1. **Infra gerenciada**: criar PostgreSQL com pooling, Redis, Vercel para a API
   e um serviço durável/scheduler para o worker.
2. **Release pipeline**: executar `python -m app.tools.run_migrations` antes de
   promover a versão, verificar `/health/ready` e documentar rollback.
3. **Stripe sandbox real**: cadastrar os seis prices, webhook
   `/webhooks/stripe`, testar cartão aprovado/recusado, replay, portal,
   cancelamento e grace period.
4. **Email e domínio**: validar domínio no Resend, configurar remetente e testar
   convite/reset fora do modo que expõe tokens.
5. **Canal real**: conectar WhatsApp Cloud API por tenant, templates,
   assinatura Meta e métricas de entrega/retry.
6. **Agenda real**: Google Calendar ou CalDAV por tenant, com credenciais
   isoladas e teste de conflito/cancelamento.
7. **Operação**: Sentry/APM, alertas, backup automático, restore drill,
   rotação de segredos e runbooks.
8. **Privacidade**: política LGPD, retenção, export/delete de titular e
   sanitização avançada antes de fine-tuning com dados reais.
9. **E2E de aceite**:
   cadastro→checkout→webhook→onboarding→WhatsApp→handoff→agenda→lembrete.
10. **Piloto controlado**: poucos tenants, feature flags, SLOs, suporte e
    checklist go/no-go.

### Critério de saída da 1D

- Readiness sem blockers em staging e produção.
- Stripe sandbox e WhatsApp/agenda reais exercitados de ponta a ponta.
- Restore comprovado, alertas acionáveis e rollback ensaiado.
- Nenhum segredo real no repositório e nenhum action token exposto.
- Piloto mede entrega, conversão, handoff, booking e churn/uso por plano.

## Diferenciação após o piloto

1. Inbox compartilhada com atribuição, SLA e presença de operadores.
2. Self-service de agenda, multi-staff e roteamento por especialidade.
3. Mídia/áudio/documentos e templates WhatsApp.
4. Avaliação automática, A/B testing e métricas de impacto do bot.
5. Instagram e outros canais.
6. Fine-tuning somente após governança de PII e dataset.
7. Refatoração gradual de `repository.py` e `admin_routes.py` por domínio.

## Origem do roadmap

Este roadmap consolida três fontes:

- "Próximos passos naturais" do `README.md` raiz.
- "Limites atuais conhecidos" e "Próximos passos recomendados" do `docs/architecture.md`.
- Auditoria de código feita ao construir este vault em 2026-04-28.

## Próximas leituras

- [[80-Improvements/01-Limites Conhecidos]]
- [[80-Improvements/05-Backlog de Produto]]
- [[60-Operations/06-Runbook Produção]]
