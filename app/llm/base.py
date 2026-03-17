from app.llm.providers.mock_provider import MockLLMProvider


def generate_reply(message: str) -> str:
    provider = MockLLMProvider()
    return provider.generate(message)