# Pacote 01 — base, políticas e decisões

## P00 — Estabelecer base reproduzível

**Entrada:** INICIO/ESTADO; `git status`; `.github/workflows/ci.yml`; `tests/conftest.py`; `Dockerfile`; relatório somente seção de evidência.

**Executar:** inventariar alterações existentes e separar propriedade; verificar providers, runtimes e disponibilidade de bancos sem expor segredos; definir base de integração incluindo arquivos novos já usados pelo projeto. Corrigir estratégia de isolamento de testes se necessário antes de delegação com pytest concorrente. Guardar apenas resumo e referência de diff/commit.

**Aceite:** checkout/base identificado, nenhum trabalho prévio perdido, bancos descartáveis definidos, baseline de testes registrado ou falhas classificadas como pré-existentes com reprodução. Atualizar ESTADO. Não confundir PostgreSQL configurado com conexão realmente validada.

## P01 — Contratos e configuração empresarial efetiva

**Depende:** P00. **Ler código:** `core/vertical_packs.py`, `core/company_context.py`, `core/taxonomy.py`, `services/company_config_service.py`, `services/onboarding_service.py`, `storage/company_config_repository.py`; consumidores de decisão/handoff/agenda. **Testes:** `test_company_context.py`, `test_company_context_persistence.py`, `test_company_scope_policy.py`, `test_operational_rules.py`.

**Executar:** concretizar CONTRATOS em tipos/schemas pequenos; definir precedência e defaults de migração; integrar políticas já declaradas (`handoff_confidence_threshold`, `handoff_on_low_confidence`, resposta por intenção) aos consumidores em vez de duplicar campos. Definir recurso default e contrato de operação/proposta em conjunto com P03. Publicação precisa invalidar/atualizar cache conforme mecanismo existente. Configurações inválidas falham na edição/publicação com mensagem útil.

**Entregáveis:** nomes e semântica dos contratos finalizados; exemplos sintéticos de duas empresas com políticas diferentes; migração/compatibilidade para contexto antigo; mapa de consumidores. Definir no contrato quando exceção requer humano e quando cancelamento é livre. Não implementar multas/cobrança sem necessidade do piloto: regra não suportada deve bloquear autonomia e encaminhar.

**Aceite:** empresa A e B respondem conforme respectivas políticas; override prevalece sobre preset; legado tem comportamento documentado; rascunho não altera produção; contexto ausente não toma identidade de outra empresa; nenhum campo novo anunciado sem consumidor. Congelar interfaces para P02/P03/P05 e registrar alteração posterior antes de integrar.

## P02 — Conversa, confirmação e mudança de intenção

**Depende:** P01. **Ler código:** `core/orchestrator.py`, `services/decision_service.py`, `classification_service.py`, `handoff_service.py`, `conversation_workflow_service.py`, `reply_services.py`, `memory_service.py`, `pipeline_service.py`; extração estruturada dos providers somente quando necessária. **Testes:** `test_conversation_workflow_service.py`, `test_orchestrator_no_auto_reply.py`, `test_orchestrator_flow.py`, `test_reply_service.py`, `test_business_scenarios.py`.

**Executar:**

1. Escrever regressão da confirmação incorreta antes da correção; avaliar cancelamento, remarcação, nova reserva e operação interna, que compartilham interpretação.
2. Separar interpretação do controle de estado e do efeito transacional. Negativa, pergunta e alteração não podem cair no mesmo caminho de confirmação.
3. Vincular aceitação à proposta ativa; conferir validade/versão/alvo/tenant e rejeitar replay de ação. Integração final com P04 obrigatória.
4. Permitir desistência (“não, pode manter”), mudança de assunto/horário e esclarecimento sem perder reserva existente.
5. Saudação configurável, ambiguidade útil e aviso de handoff; manter silêncio intencional para spam e políticas explícitas, não para falha silenciosa.
6. Aplicar horário comercial e autonomia a partir da configuração publicada. Frases do workflow usam apresentação configurável sem alterar fatos ou permissões.
7. Não deixar atualização de resumo/pipeline atrasar ou invalidar efeito já concluído; qualquer desacoplamento deve manter consistência e ordem por contato. Medir chamadas antes de otimizar.

**Aceite mínimo:** C01–C07 e A01–A04 de VALIDACAO; negativas não chamam efeitos; “sim, mas às 16h” pede nova confirmação; saudação não cria handoff injustificado; não há confirmação factual antes de resultado durável. Manter o comportamento legítimo dos testes anteriores; atualizar teste de silêncio somente com requisito explícito, não apagar cobertura.

**Fronteira:** alterações de schema/operação coordenadas com P03/P04; não refazer mecanismo de identidade nem workflow engine. P02 pode terminar a parte isolada antes de P04, mas o gate integrado exige persistência real e duplicação testadas.
