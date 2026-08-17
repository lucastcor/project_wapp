"""Detecção e anonimização de PII.

Substitui a redação anterior, que trocava apenas o caractere `@` e procurava
palavras como `cpf` sem detectar o valor em si.

Contrato:
- detectores são declarativos e extensíveis (nome/endereço entram aqui);
- o relatório informa **quantas** ocorrências de cada tipo foram substituídas,
  nunca o valor original;
- o valor original nunca é persistido em staging, candidato ou dataset.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from re import Pattern

from app.core.config import is_missing_or_placeholder, is_production_like, settings
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

POLICY_STANDARD = "standard"
POLICY_HEALTHCARE_STRICT = "healthcare_strict"


@dataclass(frozen=True)
class PIIDetector:
    name: str
    pattern: Pattern[str]
    placeholder: str
    # Validador opcional: reduz falso positivo em CPF/CNPJ/cartão.
    validator: Callable[[str], bool] | None = None
    policies: frozenset[str] = frozenset({POLICY_STANDARD, POLICY_HEALTHCARE_STRICT})


@dataclass
class PIIReport:
    counts: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def has_pii(self) -> bool:
        return self.total > 0

    def as_dict(self) -> dict[str, int]:
        return dict(sorted(self.counts.items()))


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _is_valid_cpf(value: str) -> bool:
    digits = _only_digits(value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for length in (9, 10):
        total = sum(int(digits[i]) * (length + 1 - i) for i in range(length))
        check = (total * 10) % 11
        check = 0 if check == 10 else check
        if check != int(digits[length]):
            return False
    return True


def _is_valid_cnpj(value: str) -> bool:
    digits = _only_digits(value)
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    for length, weights in (
        (12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
        (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
    ):
        total = sum(int(digits[i]) * weights[i] for i in range(length))
        check = total % 11
        check = 0 if check < 2 else 11 - check
        if check != int(digits[length]):
            return False
    return True


def _passes_luhn(value: str) -> bool:
    digits = [int(char) for char in _only_digits(value)]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        # Luhn: dobra os dígitos de posição alternada e soma os algarismos do
        # resultado. Um nome novo em vez de reatribuir `digit` — reatribuir a
        # variável do laço esconde qual valor está em uso na linha seguinte.
        doubled = digit * 2 if index % 2 == parity else digit
        checksum += doubled - 9 if doubled > 9 else doubled
    return checksum % 10 == 0


DETECTORS: tuple[PIIDetector, ...] = (
    PIIDetector(
        name="email",
        pattern=re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
        placeholder="[EMAIL]",
    ),
    # Identificadores de saúde vêm antes dos detectores numéricos genéricos
    # para que cartão/CNS não seja fatiado como telefone ou cartão de crédito.
    PIIDetector(
        name="health_id",
        pattern=re.compile(
            r"\b(?:cart(?:ã|a)o\s+(?:do\s+)?sus|cns|carteirinha)[\s:]*[\d.\-]{6,20}\b",
            re.IGNORECASE,
        ),
        placeholder="[ID_SAUDE]",
        policies=frozenset({POLICY_HEALTHCARE_STRICT}),
    ),
    PIIDetector(
        name="credit_card",
        pattern=re.compile(r"\b(?:\d[ \-]?){13,19}\b"),
        placeholder="[CARTAO]",
        validator=_passes_luhn,
    ),
    PIIDetector(
        name="cnpj",
        pattern=re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
        placeholder="[CNPJ]",
        validator=_is_valid_cnpj,
    ),
    PIIDetector(
        name="cpf",
        pattern=re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
        placeholder="[CPF]",
        validator=_is_valid_cpf,
    ),
    PIIDetector(
        name="phone",
        pattern=re.compile(
            r"(?:\+?55[\s\-]?)?(?:\(?\d{2}\)?[\s\-]?)?9?\d{4}[\s\-]?\d{4}\b"
        ),
        placeholder="[TELEFONE]",
    ),
    PIIDetector(
        name="cep",
        pattern=re.compile(r"\b\d{5}-?\d{3}\b"),
        placeholder="[CEP]",
    ),
    PIIDetector(
        name="rg",
        pattern=re.compile(r"\bRG[\s:]*[\d.\-]{7,12}\b", re.IGNORECASE),
        placeholder="[RG]",
    ),
    PIIDetector(
        name="pix_key",
        pattern=re.compile(r"\bchave\s+pix[\s:]*\S+", re.IGNORECASE),
        placeholder="[CHAVE_PIX]",
    ),
    PIIDetector(
        name="street_address",
        pattern=re.compile(
            r"\b(?:rua|avenida|av\.|alameda|travessa|rodovia)\s+[^,\n]{3,60}"
            r"(?:,\s*n?º?\s*\d{1,6})?",
            re.IGNORECASE,
        ),
        placeholder="[ENDERECO]",
    ),
    PIIDetector(
        name="birth_date",
        pattern=re.compile(r"\b(0?[1-9]|[12]\d|3[01])/(0?[1-9]|1[0-2])/(19|20)\d{2}\b"),
        placeholder="[DATA]",
        policies=frozenset({POLICY_HEALTHCARE_STRICT}),
    ),
    PIIDetector(
        name="full_name",
        # Conservador de propósito: só nomes precedidos de um marcador claro.
        # O marcador é case-insensitive; o nome capturado continua exigindo
        # inicial maiúscula para não engolir texto comum.
        pattern=re.compile(
            r"(?i:meu nome (?:é|e)|me chamo|sou (?:o|a)|paciente)\s+"
            r"([A-ZÀ-Ý][a-zà-ÿ]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ý][a-zà-ÿ]+){1,3})"
        ),
        placeholder="[NOME]",
        policies=frozenset({POLICY_HEALTHCARE_STRICT}),
    ),
)


def _detectors_for_policy(policy: str) -> tuple[PIIDetector, ...]:
    normalized = (policy or POLICY_STANDARD).strip().lower()
    if normalized not in {POLICY_STANDARD, POLICY_HEALTHCARE_STRICT}:
        normalized = POLICY_STANDARD
    return tuple(detector for detector in DETECTORS if normalized in detector.policies)


def anonymize_text(text: str | None, *, policy: str = POLICY_STANDARD) -> tuple[str, PIIReport]:
    """Substitui PII por marcadores e devolve a contagem por tipo."""
    sanitized = str(text or "")
    report = PIIReport()

    for detector in _detectors_for_policy(policy):

        def _replace(match: re.Match[str], detector: PIIDetector = detector) -> str:
            matched = match.group(0)
            if detector.validator is not None and not detector.validator(matched):
                return matched
            report.counts[detector.name] += 1
            if detector.name == "full_name":
                # Preserva o marcador ("meu nome é") e troca só o nome.
                return matched.replace(match.group(1), detector.placeholder)
            return detector.placeholder

        sanitized = detector.pattern.sub(_replace, sanitized)

    return sanitized, report


def contains_pii(text: str | None, *, policy: str = POLICY_STANDARD) -> bool:
    _, report = anonymize_text(text, policy=policy)
    return report.has_pii


class PseudonymizationKeyError(Exception):
    """Chave de pseudonimização ausente onde ela é obrigatória."""


def _resolve_pseudonymization_key() -> tuple[str, str]:
    """Segredo e versão da chave.

    Um salt previsível como `company:{id}` permite recuperar telefone e
    documento por enumeração: o espaço de busca é pequeno e o atacante conhece
    o salt. Por isso a derivação usa HMAC com segredo.
    """
    key = (settings.pii_pseudonymization_key or "").strip()
    version = (settings.pii_pseudonymization_key_version or "v1").strip() or "v1"

    if key and not is_missing_or_placeholder(key):
        return key, version

    if is_production_like():
        raise PseudonymizationKeyError(
            "PII_PSEUDONYMIZATION_KEY is required in production. "
            "Without it, pseudonyms are reversible by enumeration."
        )

    # Fora de produção o fallback existe para não travar desenvolvimento, mas
    # é sinalizado: os pseudônimos gerados assim não são seguros.
    log_event(
        logger,
        logging.WARNING,
        "pii.pseudonymization.insecure_fallback",
        key_version=version,
    )
    return "project-wapp-insecure-development-pseudonymization-key", f"{version}-insecure"


def pseudonymization_key_is_configured() -> bool:
    key = (settings.pii_pseudonymization_key or "").strip()
    return bool(key) and not is_missing_or_placeholder(key)


def pseudonymize_identifier(
    value: str | None,
    *,
    company_id: int | None = None,
    salt: str | None = None,
) -> str:
    """Identidade estável e não reversível para agrupar conversas.

    Estável dentro do tenant e diferente entre tenants. O prefixo carrega a
    versão da chave para permitir rotação planejada: girar a chave muda os
    pseudônimos, então agrupamentos antigos e novos não se misturam — o
    dataset precisa ser reconstruído após a rotação.
    """
    if salt is None and company_id is None:
        raise ValueError("pseudonymize_identifier requires company_id or salt.")

    key, key_version = _resolve_pseudonymization_key()
    scope = salt if salt is not None else f"company:{int(company_id or 0)}"
    normalized = str(value or "").strip().lower()
    digest = hmac.new(
        key.encode("utf-8"),
        f"{key_version}:{scope}:{normalized}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"anon_{key_version}_{digest[:24]}"


def merge_reports(reports: list[PIIReport]) -> PIIReport:
    merged = PIIReport()
    for report in reports:
        merged.counts.update(report.counts)
    return merged
