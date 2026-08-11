

MIN_QUALITY_SCORE = 8.0
MAX_EXAMPLES_PER_SCENARIO = 50
VALIDATION_RATIO = 0.2
DEDUP_NORMALIZE_CASE = True
EXCLUDED_SCENARIO_TYPES = {
    "financial",
    "support",
}

SENSITIVE_MARKERS = [
    "cpf",
    "cnpj",
    "cartão",
    "cartao",
    "senha",
    "token",
    "nota fiscal",
    "fatura",
    "boleto",
]

REDACTION_PATTERNS = [
    ("@", "[EMAIL_REDACTED]"),
]
