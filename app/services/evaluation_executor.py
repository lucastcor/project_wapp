"""Executor oficial de avaliação e a atestação que ele produz.

O problema que este módulo resolve: `execution_mode = model_backed` era uma
afirmação do chamador. Qualquer função injetada podia produzir respostas
determinísticas e o run resultante ficava `promotion_eligible`, liberando a
promoção de um modelo que nunca foi executado.

Agora o modo é uma **consequência** de quem executou. Só um executor que
carrega `ExecutionAttestation` pode rodar em `model_backed`, e só uma
atestação `promotable` conta para promoção:

- `build_model_backed_executor` resolve provider e `model_ref` explicitamente,
  chama o modelo configurado e falha fechada quando a chamada real está
  desabilitada;
- `build_fake_executor` existe para testes e nunca é `promotable` em ambiente
  produtivo.

A rota administrativa de avaliação continua sempre em `simulation`: nenhuma
requisição HTTP consegue registrar uma prova de modelo.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from app.core.config import is_production_like, settings
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

EXECUTOR_KIND_OPENAI = "openai_chat"
EXECUTOR_KIND_FAKE = "fake_test_adapter"


class EvaluationExecutorError(Exception):
    """Executor não pôde ser construído ou não pode ser usado aqui."""


@dataclass(frozen=True)
class ExecutionAttestation:
    """O que se sabe, de forma verificável, sobre a execução da avaliação.

    `promotable` é a única porta para promoção. Um adapter de teste pode
    existir e ser útil, mas nunca marca `promotable` fora de desenvolvimento.
    """

    executor_kind: str
    provider: str
    model_ref: str
    prompt_contract_version: str
    retrieval_config_version: str
    promotable: bool
    detail: dict[str, Any] | None = None

    def subject_fingerprint(self) -> str:
        """Impressão do sujeito avaliado, estável e sem segredo.

        Entra no run para que dois resultados só sejam comparáveis quando
        avaliaram exatamente o mesmo sujeito sob o mesmo contrato.
        """
        payload = json.dumps(
            {
                "executor_kind": self.executor_kind,
                "provider": self.provider,
                "model_ref": self.model_ref,
                "prompt_contract_version": self.prompt_contract_version,
                "retrieval_config_version": self.retrieval_config_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["subject_fingerprint"] = self.subject_fingerprint()
        return record


class AttestedExecutor(Protocol):
    """Executor cuja procedência é declarada e verificável."""

    attestation: ExecutionAttestation

    def __call__(self, request: Any) -> Any: ...


def get_attestation(executor: Any) -> ExecutionAttestation | None:
    """Atestação do executor, se ele tiver uma.

    Um `Callable` comum devolve `None` — e é isso que impede que ele seja
    aceito em `model_backed`.
    """
    attestation = getattr(executor, "attestation", None)
    return attestation if isinstance(attestation, ExecutionAttestation) else None


@dataclass
class _AttestedExecutor:
    """Empacota uma função de geração com sua atestação."""

    attestation: ExecutionAttestation
    _generate: Any

    def __call__(self, request: Any) -> Any:
        return self._generate(request)


def build_model_backed_executor(
    *,
    company_id: int,
    model_ref: str,
    provider: str | None = None,
) -> _AttestedExecutor:
    """Executor que chama o modelo realmente configurado para a empresa.

    Falha fechada em três situações, todas antes de qualquer chamada:
    `model_ref` vazio, avaliação real desabilitada e provider sem credencial.
    """
    from app.services.evaluation_service import (
        PROMPT_CONTRACT_VERSION,
        EvaluationResponse,
        retrieval_config_version,
    )

    resolved_model_ref = str(model_ref or "").strip()
    if not resolved_model_ref:
        raise EvaluationExecutorError(
            "A model-backed executor requires an explicit model_ref identifying the subject."
        )

    if not settings.enable_real_model_evaluation:
        raise EvaluationExecutorError(
            "Real model evaluation is disabled. "
            "Set ENABLE_REAL_MODEL_EVALUATION=true to evaluate against a real model."
        )

    resolved_provider = (provider or settings.classification_provider or "").strip().lower()
    if resolved_provider != "openai":
        raise EvaluationExecutorError(
            f"No model-backed executor is implemented for provider '{resolved_provider}'."
        )

    from app.llm.providers.openai_provider import OpenAIProvider

    llm_provider = OpenAIProvider()

    def generate(request: Any) -> Any:
        case = request.case
        source_text = "\n".join(
            str(source.get("content") or "") for source in request.sources
        )
        reply = llm_provider.generate_business_reply(
            classification=str(case.get("scenario_type") or "general"),
            message=str(case.get("customer_message") or ""),
            scenario_type=str(case.get("scenario_type") or "general"),
            customer_profile=str(case.get("customer_profile") or "unknown"),
            profile_name=str(case.get("profile_name") or "default"),
            conversation_context=[],
            knowledge_sources_text=source_text or None,
            has_relevant_source=bool(request.has_relevant_source),
            # O modelo avaliado é o sujeito da atestação: nunca o default.
            model=resolved_model_ref,
        )
        return EvaluationResponse(reply=str(reply or ""))

    attestation = ExecutionAttestation(
        executor_kind=EXECUTOR_KIND_OPENAI,
        provider=resolved_provider,
        model_ref=resolved_model_ref,
        prompt_contract_version=PROMPT_CONTRACT_VERSION,
        retrieval_config_version=retrieval_config_version(),
        promotable=True,
        detail={"company_id": int(company_id)},
    )

    log_event(
        logger,
        logging.INFO,
        "evaluation.executor.built",
        company_id=int(company_id),
        executor_kind=attestation.executor_kind,
        provider=attestation.provider,
        model_ref=resolved_model_ref,
    )
    return _AttestedExecutor(attestation=attestation, _generate=generate)


def build_fake_executor(
    *,
    model_ref: str,
    generate: Any,
    provider: str = "test",
) -> _AttestedExecutor:
    """Adapter de teste, atestado e explicitamente não promovível em produção.

    Existe para que a suíte exercite o caminho `model_backed` inteiro sem
    chamada externa. `promotable` acompanha o ambiente: fora de produção a
    cadeia de promoção pode ser testada de ponta a ponta; em produção a
    atestação nasce não promovível e `run_evaluation` recusa a execução.
    """
    from app.services.evaluation_service import (
        PROMPT_CONTRACT_VERSION,
        retrieval_config_version,
    )

    attestation = ExecutionAttestation(
        executor_kind=EXECUTOR_KIND_FAKE,
        provider=provider,
        model_ref=str(model_ref or "").strip(),
        prompt_contract_version=PROMPT_CONTRACT_VERSION,
        retrieval_config_version=retrieval_config_version(),
        promotable=not is_production_like(),
        detail={"note": "deterministic test adapter; performs no external call"},
    )
    return _AttestedExecutor(attestation=attestation, _generate=generate)
