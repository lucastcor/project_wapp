# Pacote 02 — agenda confiável e recuperação de vagas

## P03 — Capacidade e concorrência

**Depende:** P01. **Ler:** `services/calendar_service.py`, `storage/repository.py` (bookings), registro/schema em `storage/db.py`, `storage/sql_support.py`, migrações e integridade de tenant. **Testes:** `test_calendar_service.py`, `test_postgres_compatibility.py`, `test_tenant_database_integrity.py`, `test_db_migration_concurrency.py`.

**Executar:**

1. Mapear recurso reservável a cada booking/integração existente. Detectar sobreposições legadas antes de habilitar constraint, sem apagar ou cancelar silenciosamente dados.
2. Escolher mecanismo PostgreSQL verificável de exclusão de sobreposição por empresa/recurso/intervalo ativo; constraint de exclusão é candidata, não requisito de copiar SQL sem validar tipos/migrations. Definir comportamento equivalente e limitações no SQLite de desenvolvimento.
3. Reserva de capacidade e operação devem ser transacionais, com idempotência e política para pendência expirada. Dois contatos, API e automação devem passar pela mesma guarda.
4. Não usar perfil de cliente como identidade de recurso. Capacidade um no piloto; intervalo contíguo permitido e recurso distinto independente.
5. Migrar campos/índices/constraints; registrar novas tabelas no isolamento e no backup. Não mexer em migrations já aplicadas para “facilitar”.

**Aceite:** testes com PostgreSQL real e conexões concorrentes: duas confirmações disputando vaga geram uma reserva ativa; horários parcialmente sobrepostos também conflitam; intervalos adjacentes e recursos distintos funcionam. Outra empresa não pode reservar/referenciar recurso alheio. Migração limpa e upgrade com dados legados testados. Indisponibilidade de PostgreSQL deixa o aceite pendente, mesmo com mocks verdes.

## P04 — Operações externas idempotentes e encaixes desacoplados

**Depende:** P03. **Ler:** `calendar_service.py` (create/cancel/reschedule e Google/CalDAV), `appointment_queue_service.py`, `reminder_service.py`, `automation_event_service.py`, `automation_trigger_service.py`, `storage/inbound_repository.py`, `services/inbound_service.py`; outbox existente antes de propor outra. **Testes:** `test_calendar_service.py`, `test_appointment_queue_service.py`, `test_reminder_reliability.py`, `test_durable_inbound.py`, `test_automation_e2e.py`.

**Executar:**

- Persistir operação e efeitos a executar com chave idempotente e correlação; aproveitar infraestrutura existente se seu contrato servir ao domínio. Não duplicar sistemas sem ADR.
- Google: ID estável de criação, recuperação por ID e tratamento de exclusão já realizada. Distinguir “não existe” legítimo de ausência de permissão/credencial. Não converter todo erro 404 em sucesso sem validar identidade e contexto.
- Criar/remarcar/cancelar suportam falha entre rede e commit; reconciliar estado real antes de retry. Garantir que o cliente receba resultado coerente quando a execução é assíncrona.
- Revalidar conflito externo na remarcação, excluindo o evento original corretamente. Se falhar, preservar reserva anterior ou registrar estado incerto para recuperação; não perder silenciosamente ambas.
- Alterar/cancelar lembretes de maneira consistente com a reserva; usar evento durável para passos derivados.
- Emitir fatos de domínio essenciais no commit relevante, incluindo o que já é anunciado nos fluxos. Não adicionar nós novos ao Studio apenas para consumir funções existentes.
- Cancelamento concluído não depende do envio de oferta de encaixe. Persistir oferta antes do dispatch e usar claim/expiração de capacidade para impedir duas promessas concorrentes.
- Fila: aceite, recusa, espera e expiração; mensagem tardia não aceita uma oferta já substituída. Desligar capacidade para empresas que não querem fila deve impedir efeitos, não só esconder UI.

**Aceite:** A05–A09, F01–F04 e R01–R03; injetar falha antes/depois de efeito externo e antes/depois do commit. Reprocessar operação com sucesso externo não cria duplicata. Cancelamento confirma sucesso mesmo com falha posterior de encaixe. Booking cancelado não gera lembrete. Evento/outbox é recuperado após restart. A integração Google real é validada em P08; mocks não substituem essa evidência.

**Regra de recuperação:** não reexecutar job em revisão apenas para fazê-lo sair da fila. Um estado “concluído” deve referenciar operação e evidência verificadas; se a operação não ocorreu, reagendar pelo mecanismo idempotente, não editar tabelas manualmente.
