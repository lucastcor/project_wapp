# Diagnóstico do Project WAPP — 7 de setembro de 2026

Base analisada: branch `main`, commit `1bc3a45`. Análise de documentação, código, configuração local sem exposição de segredos, testes automatizados e amostra de inferência local. Não foram alterados código de aplicação, configuração, banco operacional ou integrações externas.

## Parecer

O produto está em **MVP avançado, na preparação para piloto supervisionado**. A base de engenharia está substancialmente mais madura do que a evidência disponível de operação real. É apropriado começar agora testes internos com a equipe de uma empresa, em ambiente separado e com dados fictícios. Atendimento autônomo a clientes exige fechar os problemas de entrada de mensagens, configuração e validação operacional descritos abaixo.

**Vale integrar o Qwen local para testes.** O modelo instalado respondeu a uma classificação sintética pela API nativa; o formato de chamada atualmente usado pelo projeto apresentou timeout. Recomenda-se um provider Ollama explícito, com limites de tempo e geração, avaliação própria e preservação das regras de negócio existentes.

## Ideia e foco do produto

O WAPP é uma plataforma de atendimento e operação comercial por WhatsApp para múltiplas empresas. Seu valor está em combinar contexto empresarial, classificação, memória do contato, resposta orientada por conhecimento, encaminhamento humano, pipeline comercial e agenda. A automação visual amplia a operação com gatilhos, espera, aprovações e execução persistida.

Há três superfícies: plataforma principal em HTML/JavaScript, Automation Studio em React/TypeScript e painel Streamlit de apoio. O backend usa FastAPI e uma camada SQL compatível com SQLite/PostgreSQL, além de Redis e workers separados. Também existem identidade SaaS, permissões, billing e ferramentas de backup.

Minha recomendação de foco para o primeiro piloto é uma empresa de serviços, um número de atendimento e um fluxo bem delimitado: acolher, qualificar, consultar informações aprovadas e encaminhar para a equipe. Agenda deve entrar após validação da integração escolhida. Billing automático, fine-tuning e automações complexas podem ser etapas posteriores.

## Evidências obtidas nesta análise

| Verificação | Resultado observado |
| --- | --- |
| Backend, suíte completa em SQLite | 1.286 passaram; 17 pulados; 3 avisos de depreciação |
| Ruff, configuração completa do repositório | Aprovado |
| Mypy com os argumentos utilizados no CI | 140 arquivos sem erros |
| Automation Studio: testes | 42 passaram |
| Automation Studio: build com TypeScript e Vite | Aprovado |
| Automation Studio: ESLint | Aprovado |
| Testes do bundle após sua compilação | 7 passaram |
| Evento sintético com duas mensagens de WhatsApp | Extrator retornou somente a primeira |
| Inferência local com Qwen | Uma chamada nativa concluída e dois timeouts no formato atual do projeto |

A suíte foi executada sem herdar credenciais e sem apontar para banco operacional ou Redis compartilhado. Parte dos 17 testes pulados dependia do bundle, compilado depois e verificado separadamente; os demais incluem integrações que exigem infraestrutura específica. Não somar os sete testes do bundle à suíte como se todos fossem casos novos.

O ambiente Python local é 3.14.3; `.python-version`, Docker e CI apontam para 3.13. O sucesso local é útil, mas convém padronizar essa diferença para reproduzir o ambiente de entrega.

Não foram reexecutados nesta análise: PostgreSQL/Redis reais, build Docker, deploy, auditoria atual de dependências, jornada visual completa em navegador ou integrações reais Meta/agenda/Stripe. O documento `docs/estado_do_projeto.md` registra resultados anteriores; eles são evidência histórica, não validações realizadas aqui.

## Pontos fortes confirmados na estrutura e nos testes

- Separação entre intenção, decisão operacional, geração de resposta, memória e encaminhamento humano.
- Identidade canônica, papéis e escopo de empresa, acompanhados de testes de isolamento e integridade no banco.
- Persistência de mensagens, encaminhamentos e outbound; mecanismos de idempotência, retries e recuperação em subsistemas operacionais.
- Automação com versões, execução persistida, fila e aprovações humanas.
- Conhecimento por empresa, recuperação de fontes e sinalização de ausência de fonte. Há proteções contra padrões de injeção; isso não comprova imunidade a prompt injection.
- Pipeline de curadoria, avaliação e promoção de modelos que distingue simulação de execução real.
- Ferramentas de backup/restauração, migrações, observabilidade, readiness e CI.

Esses pontos sustentam a classificação de MVP avançado. Testes com providers simulados verificam o software sob condições controladas; não medem a qualidade de conversação de um LLM real nem a entrega efetiva de WhatsApp.

## Problemas e condições para piloto

### 1. Mensagens adicionais no mesmo evento podem ser descartadas — corrigir antes de clientes reais

`app/api/routes.py:91`, `_extract_meta_whatsapp_message`, percorre os arrays de entradas, mudanças e mensagens, mas retorna no primeiro texto válido. O handler chama o extrator uma única vez e processa um único resultado.

Reprodução: payload sintético válido, com `object=whatsapp_business_account` e dois itens em `messages`; saída contém apenas `synthetic-1`, sem `synthetic-2`. Isso é uma falha reproduzida do extrator, não uma observação de tráfego real da Meta.

A correção deve normalizar e persistir todos os itens suportados, associar cada remetente ao contato correto e aplicar idempotência individual. O teste de regressão deve incluir múltiplas mensagens e múltiplas mudanças/entradas.

### 2. Processamento do LLM bloqueia o webhook — prioridade alta para Qwen e concorrência

`app/api/routes.py:432` declara um handler assíncrono, mas chama diretamente o orquestrador síncrono em `:523`, seguido do envio. O cliente de IA também é síncrono. Portanto, o trabalho ocorre antes da resposta HTTP e pode bloquear o event loop desse processo.

Uma conversa pode envolver classificação, geração, resumo e inferência de pipeline em sequência (`app/core/orchestrator.py`). Com inferência lenta, isso amplia o tempo total e o risco de novas tentativas do canal. Ter fila para automação não significa ter fila de entrada para esse atendimento.

Recomendação: validar, persistir e enfileirar o evento; responder ao webhook; processar em worker com ordem por conversa, idempotência, limites de tempo e encaminhamento seguro em falha. Apenas deslocar para uma tarefa em memória não oferece durabilidade.

### 3. Arquivos locais não representam uma configuração pronta de produção

Inspeção feita sem registrar valores de segredos:

- `.env` declara produção e providers reais, mas faltam valores utilizáveis de IA, WhatsApp, sessão e pseudonimização nas verificações efetuadas.
- `.env.production` declara `ENVIRONMENT=development`, `DEBUG=true`, validação estrita desligada e providers `mock` para IA, WhatsApp e agenda.
- Os interruptores de automação estão ausentes nesses arquivos; seus padrões são desligados.

São constatações dos arquivos locais. Não houve consulta a um deploy existente nem comprovação de que algum processo esteja usando esses valores.

Preparar um ambiente explícito de teste/piloto e conferir suas variáveis efetivas é pré-requisito. Não confiar no nome do arquivo para determinar o modo de execução.

### 4. Readiness não cobre toda a operação nova

`app/services/system_readiness_service.py` não verifica a saúde do worker de automação nem a chave de pseudonimização. A proteção de PII recusa a chave ausente em produção quando a função correspondente é utilizada, em `app/services/pii_service.py:232`; não foi encontrada chamada equivalente no startup que sustente a afirmação de bloqueio antecipado presente no runbook.

Adicionar verificações explícitas de configuração e heartbeat dos componentes habilitados. Saúde HTTP e banco acessível não demonstram que as filas estejam sendo consumidas.

### 5. Escopo real do canal precisa ser delimitado

O extrator atual aceita texto e ignora tipos não suportados. O provider outbound monta payload `type=text`; não há envio de template nesse adapter. Confirmar áudio, imagens, templates e status de entrega antes de prometer esses recursos ao piloto. Se a operação exigir essas capacidades, elas passam a ser requisitos da etapa.

Validar com a conta real: recebimento, resposta, falha de envio, reentrega, reconexão e retomada humana. Agenda e lembretes precisam de validação equivalente se fizerem parte do piloto.

### 6. Qualidade de atendimento ainda precisa de evidência

Não há nesta auditoria avaliação de conversas reais suficiente para afirmar taxa de acerto, segurança de respostas ou capacidade de atendimento simultâneo. A confiança numérica emitida pelo modelo não deve ser tratada como probabilidade calibrada.

Os arquivos centrais ainda são grandes: repositório SQL com cerca de 5.900 linhas, JavaScript principal com cerca de 5.900 e rotas administrativas com cerca de 2.400. Isso aumenta o custo de manutenção, mas não é motivo para adiar o piloto com uma reescrita geral.

## Qwen: ambiente e amostra real

Equipamento confirmado: **Apple M4 Pro, 24 GiB de RAM**. Ollama **0.33.3**. Modelo instalado: **`qwen3.5:9b-q4_K_M`**, aproximadamente **6,6 GB** em disco. O tamanho do arquivo não equivale ao consumo total de RAM durante inferência.

Também existe `nomic-embed-text:latest`, que pode ser avaliado depois para recuperação semântica; sua presença não significa que esteja integrado ao RAG.

Foram feitas somente chamadas locais com dados fictícios:

| Caso | Configuração | Resultado |
| --- | --- | --- |
| Classificação com método atual do projeto | SDK apontado ao Ollama, Responses API, prompt/schema atuais, timeout explícito de 45 s | `APITimeoutError`, 45,49 s |
| Classificação de solicitação de boleto | API nativa `/api/chat`, `think=false`, JSON Schema, temperatura 0, contexto 4.096, limite 128 tokens | JSON válido, categoria `financeiro`, 2,28 s, cerca de 32,48 tokens/s na geração |
| Resposta sem fonte e sem permissão para preço | Método atual de resposta, SDK apontado ao Ollama, timeout explícito de 45 s | `APITimeoutError`, 45,01 s |

O redirecionamento foi feito em script temporário; nenhuma integração foi instalada no aplicativo. Os 45 segundos são o limite escolhido para a amostra, não uma configuração existente de timeout no projeto. As diferenças entre prompts, transporte, raciocínio e limites impedem atribuir os timeouts a uma causa única.

Uma classificação bem-sucedida prova viabilidade básica de inferência local; não constitui benchmark de precisão ou teste de capacidade. É evidência suficiente para recomendar um experimento controlado, não para aprovar o modelo para atender clientes sozinho.

### Integração recomendada

1. Definir contrato comum de provider e factory para `mock`, `openai` e `ollama`; manter os mocks na suíte determinística.
2. Configurar URL, modelo, tempo limite, tamanho de contexto, limite de saída e concorrência. Usar raciocínio desligado inicialmente nas tarefas simples e medir o efeito.
3. Adaptar os contratos de classificação, resposta, memória, pipeline e interpretação de agenda. Hoje vários serviços instanciam `OpenAIProvider` diretamente e só seguem o caminho de IA quando `CLASSIFICATION_PROVIDER=openai`.
4. Validar JSON, enums e limites numéricos na aplicação; registrar provider, modelo, duração e uso de fallback. `CLASSIFICATION_PROVIDER=ollama` sozinho atualmente cai em heurísticas/templates.
5. Adaptar seleção/registro e executor de avaliação: `app/services/evaluation_executor.py:113` só implementa executor real para `openai`.
6. Preservar os controles de promoção. O pipeline de fine-tuning OpenAI não treina Qwen automaticamente.
7. Avaliar primeiro contexto, fontes e exemplos aprovados; decidir sobre fine-tuning após medir erros recorrentes.

A documentação oficial do Ollama confirma compatibilidade parcial com endpoints OpenAI e suporte a JSON estruturado. Compatibilidade de transporte não elimina diferenças de contrato e comportamento:

- https://docs.ollama.com/api/openai-compatibility
- https://docs.ollama.com/capabilities/structured-outputs
- https://ollama.com/library/qwen3.5:9b

Para desenvolvimento, o Mac é um ambiente inicial plausível e já demonstrou inferência. Para atendimento contínuo, disponibilidade, suspensão, reinícios e concorrência desse equipamento precisam ser considerados. O modelo ideal de produção deve ser escolhido depois de comparar qualidade e latência no mesmo conjunto de casos.

## Caminho até uma empresa real

| Etapa | Parecer atual | Condição de avanço |
| --- | --- | --- |
| Demonstração e testes internos com equipe da empresa | Pode começar agora em ambiente isolado | Dados fictícios, canal de teste e limites explícitos |
| Piloto com clientes e revisão humana | Próxima etapa, condicionada | Corrigir entrada, configurar runtime, validar canal e disponibilizar operador |
| Atendimento autônomo restrito | Ainda não demonstrado | Qualidade, recuperação de falhas e latência medidas no piloto |
| Operação de várias empresas com disponibilidade prometida | Ainda não demonstrada | Carga, isolamento operacional, deploy, monitoramento e recuperação comprovados |

Plano sugerido:

1. Escolher uma empresa, um processo e critérios de sucesso; organizar informações aprovadas e responsáveis por exceções.
2. Corrigir webhook em lote e retirar inferência do caminho síncrono de entrada; ajustar configuração e readiness.
3. Integrar Qwen para avaliação isolada. Usar aproximadamente 100–200 cenários revisados: intenção, ausência de fonte, desconto, financeiro, reclamação, agenda e tentativa de desviar regras. Esse número é uma proposta inicial, não um padrão certificado.
4. Rodar PostgreSQL/Redis e a imagem da entrega; ensaiar backup/restauração, reinício do worker e falha do modelo/canal.
5. Validar um número de teste real e só então iniciar um piloto pequeno com aprovação humana antes do envio. A existência de handoff não comprova que toda resposta gerada passe por aprovação; esse modo precisa ser verificado ou implementado.
6. Medir proporção de respostas aceitas sem edição, fatos sem fonte, encaminhamentos corretos, falhas/duplicações/perdas e latências p50/p95 de ponta a ponta. Definir o tempo aceitável com a empresa.
7. Liberar respostas automáticas apenas nos assuntos que tenham evidência suficiente; manter retomada humana e interrupção rápida do bot.

Critérios propostos para o conjunto de aceite: todos os casos críticos aprovados, nenhum preço/compromisso inventado nesses casos, nenhum vazamento entre empresas e nenhuma perda/duplicação nos ensaios de reentrega. Passar um conjunto finito de testes não garante ausência de falhas futuras.

O investimento prioritário agora é provar um atendimento completo e confiável em escopo pequeno. A fundação existente justifica avançar para esse trabalho; a integração Qwen é uma ferramenta útil para a validação, não um substituto dela.
