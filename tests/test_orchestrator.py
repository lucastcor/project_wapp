from app.core.orchestrator import process_user_message


def test_process_user_message() -> None:
    result = process_user_message("teste")

    assert result["reply"] == "Resposta simulada para: teste"
    assert result["source"] == "mock-llm"