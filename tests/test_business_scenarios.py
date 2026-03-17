from app.core import orchestrator
from tests.test_data.conversation_scenarios import ALL_SCENARIOS


class FakeRepository:
    def __init__(self) -> None:
        self.contacts: dict[str, dict] = {}
        self.messages: list[dict] = []

    def save_contact(self, contact_id: str, contact_name: str | None) -> None:
        existing = self.contacts.get(contact_id, {})
        self.contacts[contact_id] = {
            "contact_id": contact_id,
            "contact_name": contact_name,
            "conversation_summary": existing.get("conversation_summary"),
            "last_classification": existing.get("last_classification"),
            "pipeline_stage": existing.get("pipeline_stage"),
            "next_action": existing.get("next_action"),
            "lead_temperature": existing.get("lead_temperature"),
            **existing,
        }

    def get_contact(self, contact_id: str) -> dict | None:
        contact = self.contacts.get(contact_id)
        if not contact:
            return None

        return {
            "contact_id": contact.get("contact_id"),
            "contact_name": contact.get("contact_name"),
            "conversation_summary": contact.get("conversation_summary"),
            "last_classification": contact.get("last_classification"),
            "pipeline_stage": contact.get("pipeline_stage"),
            "next_action": contact.get("next_action"),
            "lead_temperature": contact.get("lead_temperature"),
            **contact,
        }

    def save_message(self, **kwargs) -> None:
        self.messages.append(kwargs)

    def update_contact_memory(self, **kwargs) -> None:
        contact_id = kwargs["contact_id"]
        existing = self.contacts.get(contact_id, {})
        self.contacts[contact_id] = {
            **existing,
            **kwargs,
        }


class FakeConversationEngine:
    def classify_message(self, message: str) -> dict:
        normalized = message.lower()

        if any(term in normalized for term in ["pagamento", "nota fiscal", "financeiro"]):
            return {
                "classification": "financeiro",
                "confidence": 0.94,
                "reason": "Mensagem com conteúdo financeiro.",
            }

        if any(term in normalized for term in ["problema", "erro", "não consigo", "nao consigo", "acesso"]):
            return {
                "classification": "suporte",
                "confidence": 0.93,
                "reason": "Mensagem com sinal de suporte técnico.",
            }

        return {
            "classification": "lead_novo",
            "confidence": 0.91,
            "reason": "Mensagem com intenção comercial.",
        }

    def decide_message_action(self, classification: str, confidence: float) -> dict:
        if classification in {"financeiro", "fornecedor"}:
            return {
                "auto_reply": False,
                "action": "handoff_human",
                "priority": "high",
            }

        if classification == "suporte":
            return {
                "auto_reply": True,
                "action": "auto_reply",
                "priority": "medium",
            }

        return {
            "auto_reply": True,
            "action": "auto_reply",
            "priority": "high",
        }

    def generate_business_reply(self, **kwargs) -> dict:
        classification = kwargs["classification"]
        message = kwargs["message"]

        if classification == "suporte":
            return {
                "reply": "Recebi sua solicitação de suporte e vou te orientar nos próximos passos.",
                "source": "openai",
            }

        return {
            "reply": f"Resposta contextual para: {message}",
            "source": "openai",
        }

    def update_conversation_summary(self, **kwargs) -> str:
        previous_summary = kwargs.get("previous_summary") or ""
        latest_message = kwargs["latest_message"]
        latest_classification = kwargs["latest_classification"]

        return (
            f"{previous_summary} Última interação: {latest_message}. "
            f"Classificação: {latest_classification}."
        ).strip()[:800]

    def infer_pipeline_state(self, **kwargs) -> dict:
        latest_message = kwargs["latest_message"].lower()
        latest_classification = kwargs["latest_classification"]
        conversation_summary = (kwargs.get("conversation_summary") or "").lower()
        combined = f"{conversation_summary} {latest_message}".strip()
        if latest_classification == "financeiro" and any(
            term in combined for term in ["pagamento", "nota fiscal", "financeiro"]
        ):
            return {
                "pipeline_stage": "encerrado",
                "next_action": "Encaminhar para atendimento humano adequado.",
                "lead_temperature": "cold",
            }

        if latest_classification != "lead_novo":
            return {
                "pipeline_stage": "encerrado",
                "next_action": "Encaminhar para atendimento humano adequado.",
                "lead_temperature": "cold",
            } 

        if any(
            term in combined
            for term in [
                "quero",
                "contratar",
                "seguir",
                "confirmar",
                "comprar",
                "fechar",
                "próximo passo",
                "proximo passo",
                "reservar",
            ]
        ):
            return {
                "pipeline_stage": "aguardando_confirmacao",
                "next_action": "Conduzir fechamento e confirmar dados necessários.",
                "lead_temperature": "hot",
            }

        if any(
            term in combined
            for term in [
                "valor",
                "preço",
                "preco",
                "quanto",
                "agendar",
                "horário",
                "horario",
                "profissional",
                "disponibilidade",
                "locação",
                "locacao",
                "alugar",
                "venda",
                "plano",
                "mensal",
                "recorrente",
                "começar",
                "comecar",
                "melhor caminho",
            ]
        ):
            return {
                "pipeline_stage": "qualificando",
                "next_action": "Aprofundar contexto e alinhar detalhes do atendimento.",
                "lead_temperature": "warm",
            }

        return {
            "pipeline_stage": "novo_lead",
            "next_action": "Entender necessidade do contato.",
            "lead_temperature": "warm",
        }



EXPECTED_ALLOWED_CLASSIFICATIONS = {
    "suporte_tecnico_basico": {"suporte"},
    "assunto_financeiro": {"financeiro", "lead_novo"},
}


EXPECTED_ALLOWED_PIPELINES = {
    "lead_servico_inicial": {"qualificando", "aguardando_confirmacao"},
    "servico_com_agendamento": {"qualificando", "aguardando_confirmacao"},
    "produto_digital_venda": {"qualificando", "aguardando_confirmacao"},
    "produto_com_duvida_comercial": {"qualificando", "aguardando_confirmacao"},
    "locacao_com_prova": {"qualificando", "aguardando_confirmacao"},
    "item_com_aluguel_ou_venda": {"qualificando", "aguardando_confirmacao"},
    "agendamento_com_escolha_profissional": {"qualificando", "aguardando_confirmacao"},
    "agendamento_sem_profissional_definido": {"qualificando", "aguardando_confirmacao"},
    "empresa_hibrida_servico_produto": {"qualificando", "aguardando_confirmacao"},
    "empresa_hibrida_com_recorrencia": {"qualificando", "aguardando_confirmacao"},
    "suporte_tecnico_basico": {"encerrado"},
    "assunto_financeiro": {"encerrado"},
}


EXPECTED_AUTO_REPLY = {
    "assunto_financeiro": False,
}


def test_business_conversation_scenarios(monkeypatch) -> None:
    fake_repository = FakeRepository()
    fake_engine = FakeConversationEngine()

    monkeypatch.setattr(orchestrator, "repository", fake_repository)
    # monkeypatch.setattr(orchestrator, "classify_message", fake_engine.classify_message)  # REMOVE this line
    monkeypatch.setattr(orchestrator, "decide_message_action", fake_engine.decide_message_action)
    monkeypatch.setattr(orchestrator, "generate_business_reply", fake_engine.generate_business_reply)
    monkeypatch.setattr(orchestrator, "update_conversation_summary", fake_engine.update_conversation_summary)
    monkeypatch.setattr(orchestrator, "infer_pipeline_state", fake_engine.infer_pipeline_state)

    def classify_with_context(message: str) -> dict:
        normalized = message.lower()
        if any(
            term in normalized
            for term in [
                "nota fiscal",
                "financeiro",
                "pagamento pendente",
                "boleto vencido",
                "fatura",
                "reembolso",
            ]
        ):
            return {
                "classification": "financeiro",
                "confidence": 0.94,
                "reason": "Mensagem com conteúdo financeiro.",
            }

        if any(term in normalized for term in ["problema", "erro", "não consigo", "nao consigo", "acesso"]):
            return {
                "classification": "suporte",
                "confidence": 0.93,
                "reason": "Mensagem com sinal de suporte técnico.",
            }

        current_contact = fake_repository.get_contact(contact_id)
        if current_contact:
            summary = (current_contact.get("conversation_summary") or "").lower()
            if any(
                term in summary
                for term in [
                    "nota fiscal",
                    "financeiro",
                    "pagamento pendente",
                    "boleto vencido",
                    "fatura",
                    "reembolso",
                ]
            ):
                return {
                    "classification": "financeiro",
                    "confidence": 0.90,
                    "reason": "Contexto acumulado indica assunto financeiro.",
                }

        return {
            "classification": "lead_novo",
            "confidence": 0.91,
            "reason": "Mensagem com intenção comercial.",
        }

    for scenario in ALL_SCENARIOS:
        scenario_name = scenario["name"]
        contact_id = scenario["contact_id"]
        contact_name = scenario["contact_name"]
        monkeypatch.setattr(orchestrator, "classify_message", classify_with_context)

        last_result = None
        for message in scenario["messages"]:
            last_result = orchestrator.process_incoming_message(
                contact_id=contact_id,
                contact_name=contact_name,
                message=message,
            )

        assert last_result is not None
        assert last_result["contact_id"] == contact_id
        assert "classification" in last_result
        assert "reply" in last_result
        assert "pipeline_stage" in last_result
        assert "lead_temperature" in last_result
        assert fake_repository.get_contact(contact_id) is not None

        expected_classifications = EXPECTED_ALLOWED_CLASSIFICATIONS.get(scenario_name)
        if expected_classifications:
            assert last_result["classification"] in expected_classifications, (
                f"Classificação inesperada para {scenario_name}: {last_result['classification']} "
                f"(esperado um de {sorted(expected_classifications)})"
            )

        expected_pipelines = EXPECTED_ALLOWED_PIPELINES.get(scenario_name)
        if expected_pipelines:
            assert last_result["pipeline_stage"] in expected_pipelines, (
                f"Pipeline inesperado para {scenario_name}: {last_result['pipeline_stage']} "
                f"(esperado um de {sorted(expected_pipelines)})"
            )

        expected_auto_reply = EXPECTED_AUTO_REPLY.get(scenario_name)
        if expected_auto_reply is not None:
            assert last_result["auto_reply"] is expected_auto_reply

        if last_result["auto_reply"]:
            assert last_result["reply"] != ""
            assert last_result["source"] == "openai"
        else:
            assert last_result["reply"] == ""
            assert last_result["source"] == "none"

    assert len(fake_repository.messages) == sum(len(s["messages"]) for s in ALL_SCENARIOS)