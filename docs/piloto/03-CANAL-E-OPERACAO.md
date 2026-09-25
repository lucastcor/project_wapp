# Pacote 03 — WhatsApp e operação empresarial

## P05 — Canal adequado ao piloto

**Depende:** P01. **Ler:** `services/whatsapp_provider.py`, `outbound_dispatch_service.py`, `api/routes.py`, `storage/channel_repository.py`, `inbound_repository.py`, modelos de outbound/reminder; `tests/test_whatsapp_tenant_binding.py`, `test_ingress_security.py`, `test_routes.py`, `test_outbound_reliability.py`.

**Executar:**

1. Manter validação HMAC e resolução pelo número receptor; validar múltiplas mensagens e callbacks, sem escolher tenant por slug do cliente.
2. Registrar última mensagem do usuário e decidir entre texto livre/template aprovado conforme janela vigente. Conferir documentação oficial Meta antes de implementar contratos externos/versionamento.
3. Templates utility por empresa/idioma/finalidade e parâmetros validados; não reclassificar publicidade como utility. Suportar status de template ou checagem operacional antes de ativação.
4. Persistir consentimento por finalidade e opt-out; respeitar recusas tanto na fila quanto no dispatch efetivo. Última mensagem do cliente não deve virar consentimento geral para campanhas.
5. Processar callbacks duplicados/fora de ordem e correlacionar provider ID + empresa; distinguir aceito, entregue, lido e falhou sem regredir estado indevidamente. Preservar timestamps/eventos suficientes para auditoria.
6. Separar erro definitivo, rate limit/transitório e envio incerto. Retry seguro com backoff e reconciliação; não repetir cegamente quando pode ter havido aceitação.
7. Conteúdo não suportado recebe orientação controlada, sem ciclos/duplicatas. Implementar botões/interactive replies se usados em confirmação; transcrição de áudio fica posterior ao piloto.
8. Token compartilhado atual só serve a números que ele realmente autoriza. Validar acesso a cada número. Credenciais distintas por cliente exigem armazenamento seguro ou referência a secret store; não colocar token em contexto/prompt.

**Aceite:** W01–W07; payloads reais sanitizados/fixtures; callbacks inválidos não alteram estado; empresa sem binding não envia pelo número default de outra. Integração real e template aprovado ainda são gate P08/G3.

## P06 — Erros recuperáveis e atendimento humano

**Depende:** P02/P04/P05. **Ler:** `services/inbound_service.py`, `storage/inbound_repository.py`, `services/handoff_service.py`, `outbound_dispatch_service.py`, `worker_health_service.py`, `local_runtime_readiness.py`, `api/admin_routes.py` (inbound/handoff), `tools/inbound_worker.py`; testes durable inbound, worker readiness, handoff e RBAC.

**Executar:** erro de negócio vira resposta útil; falha transitória segura vira retry controlado; resultado incerto vira revisão com operação e ação recomendada. Preservar ordem por contato e evitar que geração lenta/lease expirado permita efeitos por um dono antigo. Definir lease renovável/fencing ou proteção equivalente e testar cancelamento de posse.

Adicionar estados operacionais explícitos para humano assumir/devolver conversa com trilha auditável. Persistir novas entradas durante posse humana; não disparar bot concorrente. Avisar cliente quando apropriado e evitar múltiplas pendências idênticas.

Expor backlog, idade da mensagem mais antiga, revisão, erro de provider e heartbeat. Alertas externos só após canal autorizado; visibilidade no painel e registro operacional não podem depender disso.

**Aceite:** F01–F06 e H01–H03; intervenção resolve o caso sem duplicar efeito e desbloqueia a conversa correta; teste de crash/lease; auditoria com ator/empresa/ação sem conteúdo sensível desnecessário. Estado de revisão deve ser explicável ao operador, não só nome de exceção.

## P07 — Configuração e painel utilizáveis

**Depende:** P02/P04/P05/P06. **Ler:** `app/platform/index.html`, `app/platform/assets/app.js`, `styles.css`, `api/platform_admin_routes.py`, `admin_routes.py`, schemas/permissões/escopo, `services/onboarding_service.py`, `system_readiness_service.py`; frontend guide somente partes relevantes.

O painel principal é HTML/JS servido pela API. `frontend/automation-studio` é o editor de automação separado; não construir as telas de operação no lugar errado nem reescrever tudo em React.

**Executar:**

- Onboarding curto: empresa/contexto publicado, serviços/durações, recurso/horários, política de conversa/cancelamento e operadores.
- Mostrar testes de canal/agenda e pendências por empresa com segredos mascarados; distinguir simulação, agenda interna e integração externa.
- Configurações de políticas com exemplos e validação; publicar/voltar versão com compatibilidade. Preview usa cenário sintético e sinaliza que não é envio real.
- Agenda e conversa mostram estados verdadeiros: reservado, sincronizando, cancelado, erro/revisão. Ações de operador passam pelos mesmos serviços do bot.
- Tela de revisão com contexto mínimo, filtros, motivo, evidência da operação, tomada humana e resolução segura; confirmação para ação destrutiva inequívoca.
- Entitlement do piloto explícito; cobrança manual não deve desativar segurança/billing global. Privacidade, retenção e exportação/exclusão operacional documentadas.

**Aceite:** operador autorizado configura a empresa, publica, atende, cancela/remarca e resolve falha pelo painel. Viewer não escreve; operador de outra empresa não lê. Validar navegador real, erro/vazio/carregamento e navegação básica em tela estreita; endpoints/mock isolados não bastam. Novas rotas devem declarar RBAC, escopo e CSRF. Não exibir capacidade ainda não implementada como disponível.
