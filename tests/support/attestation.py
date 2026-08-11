"""Executor atestado para testes, sem nenhuma chamada externa.

Existe para que a suíte exercite o caminho `model_backed` inteiro — incluindo
o portão de promoção — sem falar com a OpenAI. A atestação declara
`executor_kind = fake_test_adapter`, então em ambiente produtivo
`run_evaluation` recusa a execução em vez de registrar uma prova falsa.
"""

from __future__ import annotations

from app.services.evaluation_executor import build_fake_executor
from app.services.evaluation_service import EvaluationResponse, build_offline_reply_generator

DEFAULT_TEST_REPLY = "Vou confirmar essa informação com a equipe e já te retorno."


def attested_test_executor(
    *,
    model_ref: str = "ft:test-model",
    default_reply: str | None = DEFAULT_TEST_REPLY,
    canned_replies: dict[str, str] | None = None,
):
    """Executor determinístico com atestação de adapter de teste."""
    generator = build_offline_reply_generator(
        canned_replies=canned_replies,
        default_reply=default_reply,
    )

    def generate(request) -> EvaluationResponse:
        return generator(request)

    return build_fake_executor(model_ref=model_ref, generate=generate)
