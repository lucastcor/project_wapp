class MockLLMProvider:
    def generate(self, message: str) -> str:
        normalized = message.strip()

        if not normalized:
            return "Não recebi nenhuma mensagem."

        return f"Resposta simulada para: {normalized}"
