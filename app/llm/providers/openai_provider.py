import json
from typing import Any

from openai import OpenAI


from app.core.config import settings
from app.core.company_context import get_company_context


class OpenAIProvider:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not configured.")

        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model
        self.finetuned_model = settings.openai_finetuned_model

    def _get_reply_model(self) -> str:
        if settings.use_finetuned_model_for_replies and self.finetuned_model:
            return self.finetuned_model
        return self.model

    def generate_business_reply(
        self,
        classification: str,
        message: str,
        contact_name: str | None = None,
        conversation_summary: str | None = None,
        last_classification: str | None = None,
        pipeline_stage: str | None = None,
        relevant_knowledge: list[dict[str, Any]] | None = None,
        style_examples_text: str | None = None,
    ) -> str:
        first_name = contact_name or "cliente"
        summary = conversation_summary or "Sem histórico anterior."
        previous_class = last_classification or "Sem classificação anterior."
        current_pipeline_stage = pipeline_stage or "Sem estágio definido."
        company_context = get_company_context()
        knowledge_entries = relevant_knowledge or []
        style_examples = style_examples_text or "- Nenhum exemplo de estilo aprovado cadastrado"

        company_name = company_context.get("company_name", "a empresa")
        business_identity = company_context.get("business_identity", {})
        business_type = business_identity.get("business_type", "serviços")
        service_area = business_identity.get("service_area", "conforme disponibilidade")

        business_hours = company_context.get("business_hours", {})
        response_policy = company_context.get("response_policy", {})
        commercial_policy = company_context.get("commercial_policy", {})
        catalog = company_context.get("catalog", {})

        response_time = response_policy.get("response_time", "conforme horário comercial")
        budget_policy = commercial_policy.get("budget_policy", "valores dependem do escopo")
        services = catalog.get("items", [])
        rules = company_context.get("business_rules", [])

        services_text = "\n".join(
            [
                (
                    f"- {service.get('name', 'Item')}: {service.get('description', 'Sem descrição')} "
                    f"| Tipo: {service.get('type', 'não informado')} "
                    f"| Modos: {', '.join(service.get('transaction_modes', [])) or 'não informado'} "
                    f"| Faixa de preço: {service.get('price_range', 'Sob consulta')}"
                )
                for service in services
            ]
        ) or "- Nenhum item cadastrado"

        rules_text = "\n".join([f"- {rule}" for rule in rules]) or "- Não inventar informações"

        knowledge_text = "\n".join(
            [
                (
                    f"- Título: {entry.get('title', 'Sem título')} "
                    f"| Tipo: {entry.get('knowledge_type', 'general_faq')} "
                    f"| Conteúdo: {entry.get('content', '')}"
                )
                for entry in knowledge_entries
            ]
        ) or "- Nenhum conhecimento incremental relevante cadastrado"

        reply_model = self._get_reply_model()

        response = self.client.responses.create(
            model=reply_model,
            input=(
                "Você é um assistente de atendimento comercial e operacional no WhatsApp. "
                "Responda em português do Brasil, de forma educada, objetiva, profissional e humana. "
                "Não invente informações. Não prometa preços, prazos ou políticas que não estejam no contexto da empresa. "
                "Se faltar contexto, peça esclarecimentos de forma natural. "
                "Sempre foque em responder o que o cliente realmente quer saber."
                "Lembre de que vc esta conversando como se fosse uma pessoa da empresa, responda de maneira simpática, um vendedor consultivo."
                "Use um linguajar mais próximo do cliente, sem tanta formalidade, mas mantendo o profissionalismo."
                "E nao amplie muito a conversa para nao perder o foco do cliente, seja mais direto e objetivo possível, mas sem perder a simpatia."
                "Va perguntando aos poucos se precisar de mais informacoes, nunca tudo de uma vez so, para nao perder o cliente no meio do caminho."
                "Use o histórico resumido para manter continuidade e use o estágio do funil para ajustar o objetivo da resposta.\n\n"
                "Use os exemplos aprovados apenas como referência de tom, ritmo, estrutura e jeito de responder, sem copiar literalmente.\n\n"
                f"Empresa: {company_name}\n"
                f"Tipo de negócio: {business_type}\n"
                f"Modelos de negócio: {json.dumps(business_identity.get('business_model', []), ensure_ascii=False)}\n"
                f"Área de atendimento: {service_area}\n"
                f"Identidade da empresa: {business_identity.get('company_description', 'Sem descrição cadastrada')}\n"
                f"Tempo médio de resposta: {response_time}\n"
                f"Política comercial: {budget_policy}\n"
                f"Horário de funcionamento: {json.dumps(business_hours, ensure_ascii=False)}\n\n"
                f"Catálogo disponível:\n{services_text}\n\n"
                f"Regras de negócio:\n{rules_text}\n\n"
                f"Conhecimento incremental relevante:\n{knowledge_text}\n\n"
                f"Exemplos aprovados de estilo e resposta:\n{style_examples}\n\n"
                f"Nome do contato: {first_name}\n"
                f"Classificação atual: {classification}\n"
                f"Última classificação conhecida: {previous_class}\n"
                f"Estágio atual do funil: {current_pipeline_stage}\n"
                f"Resumo anterior da conversa: {summary}\n"
                f"Mensagem atual do contato: {message}\n\n"
                "Diretrizes por estágio:\n"
                "- novo_lead: entender a necessidade e acolher o contato.\n"
                "- qualificando: aprofundar contexto, escopo, formato, restrições e intenção de compra.\n"
                "- aguardando_confirmacao: conduzir para fechamento dos detalhes necessários.\n"
                "- proposta_em_preparacao: alinhar dados faltantes para proposta.\n"
                "- aguardando_resposta: retomar de forma leve e objetiva.\n"
                "- convertido: manter tom de continuidade e próximos passos.\n"
                "- encerrado: só retomar se a nova mensagem indicar nova demanda.\n\n"
                "Responda apenas com a mensagem final que seria enviada ao contato."
            ),
        )

        return response.output_text.strip()
    
    def generate_conversation_summary(
        self,
        previous_summary: str | None,
        latest_message: str,
        latest_classification: str,
        latest_reply: str | None = None,
    ) -> str:
        summary = previous_summary or "Sem resumo anterior."
        reply = latest_reply or "Nenhuma resposta automática foi enviada."

        response = self.client.responses.create(
            model=self.model,
            input=(
                "Você é um assistente que consolida memória de conversa de atendimento. "
                "Gere um resumo curto, objetivo e útil para continuidade do atendimento. "
                "Não repita literalmente toda a mensagem. "
                "Destaque interesse do contato, contexto relevante, objeções, preferências, estágio do atendimento e próximo passo quando existir. "
                "Escreva em português do Brasil. "
                "Mantenha no máximo 5 frases curtas e sem floreio.\n\n"
                f"Resumo anterior: {summary}\n"
                f"Mensagem mais recente do contato: {latest_message}\n"
                f"Classificação mais recente: {latest_classification}\n"
                f"Última resposta enviada: {reply}\n"
            ),
        )

        return response.output_text.strip()
    
    def infer_pipeline_state(
        self,
        conversation_summary: str | None,
        latest_message: str,
        latest_classification: str,
    ) -> dict[str, Any]:
        summary = conversation_summary or "Sem resumo anterior."

        response = self.client.responses.create(
            model=self.model,
            input=(
                "Você é um assistente que identifica o estágio comercial atual de um contato no funil de atendimento. "
                "Analise o resumo da conversa, a mensagem mais recente e a classificação atual. "
                "Escolha apenas um pipeline_stage válido, uma next_action curta e uma lead_temperature válida.\n\n"
                "pipeline_stage válidos: novo_lead, qualificando, aguardando_confirmacao, proposta_em_preparacao, aguardando_resposta, convertido, encerrado.\n"
                "lead_temperature válidos: cold, warm, hot.\n\n"
                "Regras:\n"
                "- Retorne apenas JSON válido.\n"
                "- next_action deve ser curta, objetiva e operacional.\n"
                "- Se a conversa ainda estiver exploratória, prefira novo_lead ou qualificando.\n"
                "- Se houver intenção clara de contratação, use aguardando_confirmacao.\n"
                "- Se o atendimento estiver fora de fluxo comercial, pode usar encerrado.\n\n"
                f"Resumo atual da conversa: {summary}\n"
                f"Mensagem mais recente: {latest_message}\n"
                f"Classificação mais recente: {latest_classification}\n"
            ),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "pipeline_state",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "pipeline_stage": {
                                "type": "string",
                                "enum": [
                                    "novo_lead",
                                    "qualificando",
                                    "aguardando_confirmacao",
                                    "proposta_em_preparacao",
                                    "aguardando_resposta",
                                    "convertido",
                                    "encerrado",
                                ],
                            },
                            "next_action": {
                                "type": "string",
                            },
                            "lead_temperature": {
                                "type": "string",
                                "enum": ["cold", "warm", "hot"],
                            },
                        },
                        "required": ["pipeline_stage", "next_action", "lead_temperature"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            },
        )

        content = response.output_text
        return json.loads(content)
    
    def classify_message(self, message: str) -> dict[str, Any]:
        response = self.client.responses.create(
            model=self.model,
            input=(
                "Classifique a mensagem do usuário em apenas uma categoria válida. "
                "Categorias permitidas: "
                "lead_novo, cliente_existente, suporte, financeiro, fornecedor, spam, indefinido.\n\n"
                "Regras:\n"
                "- Retorne apenas JSON válido.\n"
                "- confidence deve ser um número entre 0 e 1.\n"
                "- reason deve ser curta e objetiva.\n\n"
                f"Mensagem: {message}"
            ),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "message_classification",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "classification": {
                                "type": "string",
                                "enum": [
                                    "lead_novo",
                                    "cliente_existente",
                                    "suporte",
                                    "financeiro",
                                    "fornecedor",
                                    "spam",
                                    "indefinido",
                                ],
                            },
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "reason": {
                                "type": "string",
                            },
                        },
                        "required": ["classification", "confidence", "reason"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            },
        )

        content = response.output_text
        return json.loads(content)