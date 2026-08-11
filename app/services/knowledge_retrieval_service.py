"""Recuperação de conhecimento tenant-safe (RAG).

Contrato:
- o filtro por `company_id` acontece na query, antes de qualquer pontuação;
- a mensagem atual do cliente é o que dirige a recuperação;
- funciona sem embeddings (busca lexical determinística em SQLite e Postgres)
  e estende para busca vetorial quando um provider está configurado;
- conteúdo recuperado é dado não confiável: é neutralizado contra injeção de
  instrução antes de entrar no prompt;
- ausência de fonte relevante é sinalizada para que a resposta peça
  esclarecimento ou faça handoff, em vez de inventar.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.tenancy import require_company_id
from app.llm.embeddings import (
    EmbeddingError,
    cosine_similarity,
    get_embedding_provider,
)
from app.storage import knowledge_repository

logger = get_logger(__name__)

CHUNK_TARGET_CHARS = 700
CHUNK_OVERLAP_CHARS = 120

# Peso por tipo de documento: regra e política valem mais que FAQ solta.
DOCUMENT_TYPE_WEIGHTS: dict[str, float] = {
    "business_rule": 1.25,
    "discount_policy": 1.2,
    "availability_rule": 1.15,
    "schedule_rule": 1.15,
    "price": 1.15,
    "product_info": 1.1,
    "procedure": 1.05,
    "general_faq": 1.0,
}

_STOPWORDS = frozenset(
    {
        "a", "as", "o", "os", "um", "uma", "de", "do", "da", "dos", "das", "em", "no", "na",
        "nos", "nas", "por", "para", "com", "sem", "que", "e", "ou", "se", "ao", "aos", "à",
        "às", "meu", "minha", "seu", "sua", "eu", "voce", "vocês", "voces", "tem", "ter",
        "qual", "quais", "como", "quando", "onde", "isso", "esse", "essa", "este", "esta",
        "the", "of", "to", "and", "is", "in", "for", "on",
    }
)

# Padrões que caracterizam tentativa de injeção de instrução em fonte externa.
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(as\s+|all\s+|todas\s+as\s+)?(previous|anteriores?|instru)", re.IGNORECASE),
    re.compile(r"desconsidere\s+(as\s+)?instru", re.IGNORECASE),
    re.compile(r"esque(ç|c)a\s+(as\s+)?(instru|regras)", re.IGNORECASE),
    re.compile(r"^\s*(system|assistant|user)\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"\bvoc(ê|e)\s+agora\s+(é|e)\b", re.IGNORECASE),
    re.compile(r"\bnew\s+instructions?\b", re.IGNORECASE),
    re.compile(r"\bnovas?\s+instru(ç|c)(õ|o)es\b", re.IGNORECASE),
    re.compile(r"\breveal\s+(the\s+)?(system\s+)?prompt\b", re.IGNORECASE),
    re.compile(r"<\s*/?\s*(system|instructions?)\s*>", re.IGNORECASE),
]


class KnowledgeRetrievalError(Exception):
    pass


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    document_id: int
    document_version_id: int
    document_title: str
    document_type: str
    content: str
    score: float
    lexical_score: float
    semantic_score: float
    injection_flagged: bool


@dataclass(frozen=True)
class RetrievalResult:
    company_id: int
    query: str
    chunks: list[RetrievedChunk]
    mode: str
    candidates_considered: int
    has_relevant_source: bool

    def as_trace(self) -> dict[str, Any]:
        """Rastro para observabilidade: ids e scores, nunca o conteúdo."""
        return {
            "mode": self.mode,
            "candidates_considered": self.candidates_considered,
            "has_relevant_source": self.has_relevant_source,
            "sources": [
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "document_version_id": chunk.document_version_id,
                    "document_type": chunk.document_type,
                    "score": round(chunk.score, 4),
                    "lexical_score": round(chunk.lexical_score, 4),
                    "semantic_score": round(chunk.semantic_score, 4),
                    "injection_flagged": chunk.injection_flagged,
                }
                for chunk in self.chunks
            ],
        }


# --- normalização e tokenização ----------------------------------------------


def fold_text(value: str | None) -> str:
    text = str(value or "").lower()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def tokenize(value: str | None) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", fold_text(value))
        if len(token) > 2 and token not in _STOPWORDS
    ]


def build_search_text(content: str, *, title: str | None = None) -> str:
    """Texto normalizado usado pela busca lexical em ambos os bancos.

    O título entra no índice porque costuma carregar o termo que o cliente
    usa ("horário", "cancelamento") mesmo quando o corpo usa outra flexão.
    """
    return " ".join(tokenize(f"{title or ''} {content}"))


# Prefixo curto aproxima flexões do português ("atendimento"/"atendemos")
# sem exigir um stemmer completo nem embeddings.
_PREFIX_LENGTH = 5


def _prefixes(tokens: list[str]) -> set[str]:
    return {token[:_PREFIX_LENGTH] for token in tokens if len(token) >= _PREFIX_LENGTH}


# --- chunking ------------------------------------------------------------------


def chunk_document(content: str, *, target_chars: int = CHUNK_TARGET_CHARS) -> list[str]:
    """Divide por parágrafo, agrupando até o alvo, com sobreposição curta."""
    normalized = str(content or "").strip()
    if not normalized:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    if not paragraphs:
        paragraphs = [normalized]

    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if not buffer:
            buffer = paragraph
        elif len(buffer) + len(paragraph) + 2 <= target_chars:
            buffer = f"{buffer}\n\n{paragraph}"
        else:
            chunks.append(buffer)
            overlap = buffer[-CHUNK_OVERLAP_CHARS:] if len(buffer) > CHUNK_OVERLAP_CHARS else ""
            buffer = f"{overlap}\n\n{paragraph}".strip() if overlap else paragraph

    if buffer:
        chunks.append(buffer)

    # Parágrafo único e muito longo ainda precisa ser fatiado.
    expanded: list[str] = []
    for chunk in chunks:
        if len(chunk) <= target_chars * 2:
            expanded.append(chunk)
            continue
        start = 0
        while start < len(chunk):
            expanded.append(chunk[start : start + target_chars])
            start += target_chars - CHUNK_OVERLAP_CHARS
    return expanded


def build_chunk_records(content: str, *, title: str | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    provider = get_embedding_provider()
    pieces = chunk_document(content)

    embeddings: list[list[float]] = []
    if provider.is_semantic or provider.model != "none":
        try:
            embeddings = provider.embed(pieces)
        except EmbeddingError:
            embeddings = []
        except Exception as exc:  # provider externo indisponível não pode quebrar ingestão
            log_event(
                logger,
                logging.WARNING,
                "knowledge.embedding.unavailable",
                error_type=type(exc).__name__,
            )
            embeddings = []

    for index, piece in enumerate(pieces):
        embedding = embeddings[index] if index < len(embeddings) else None
        records.append(
            {
                "content": piece,
                "search_text": build_search_text(piece, title=title),
                "token_estimate": max(1, len(piece) // 4),
                "embedding_model": provider.model if embedding else None,
                "embedding": embedding or None,
            }
        )
    return records


# --- defesa contra injeção ------------------------------------------------------


def detect_injection(content: str) -> bool:
    return any(pattern.search(content or "") for pattern in _INJECTION_PATTERNS)


def neutralize_source_content(content: str) -> str:
    """Converte a fonte em dado citável, não em instrução executável."""
    sanitized = str(content or "")
    for pattern in _INJECTION_PATTERNS:
        sanitized = pattern.sub("[conteudo removido: instrucao nao confiavel]", sanitized)
    # Evita que a fonte simule delimitadores ou papéis do prompt.
    sanitized = re.sub(r"^\s*(system|assistant|user)\s*:", "-", sanitized, flags=re.IGNORECASE | re.MULTILINE)
    sanitized = sanitized.replace("```", "'''")
    return sanitized.strip()


# --- scoring --------------------------------------------------------------------


def _lexical_score(query_tokens: list[str], search_text: str) -> float:
    if not query_tokens:
        return 0.0
    chunk_tokens = search_text.split()
    if not chunk_tokens:
        return 0.0

    chunk_token_set = set(chunk_tokens)
    chunk_prefixes = _prefixes(chunk_tokens)
    unique_query_tokens = set(query_tokens)

    exact_matches = {token for token in unique_query_tokens if token in chunk_token_set}
    prefix_matches = {
        token
        for token in unique_query_tokens - exact_matches
        if len(token) >= _PREFIX_LENGTH and token[:_PREFIX_LENGTH] in chunk_prefixes
    }
    if not exact_matches and not prefix_matches:
        return 0.0

    # Match aproximado vale menos que match exato.
    coverage = (len(exact_matches) + 0.6 * len(prefix_matches)) / len(unique_query_tokens)
    # Frequência com saturação logarítmica evita que repetição domine o score.
    frequency = sum(chunk_tokens.count(token) for token in exact_matches)
    density = math.log1p(frequency) / math.log1p(frequency + len(chunk_tokens) / 10 + 1)
    return round(min(1.0, coverage) * 0.75 + density * 0.25, 6)


def _validity_boost(valid_until: Any) -> float:
    if not valid_until:
        return 1.0
    return 1.05


def _priority_boost(priority: Any) -> float:
    try:
        numeric = int(priority or 0)
    except (TypeError, ValueError):
        numeric = 0
    return 1.0 + min(max(numeric, 0), 10) * 0.02


def retrieve_knowledge(
    *,
    company_id: int,
    query: str,
    limit: int | None = None,
    min_score: float | None = None,
    document_types: list[str] | None = None,
) -> RetrievalResult:
    """Recupera os chunks mais relevantes do tenant para a mensagem atual."""
    resolved_company_id = require_company_id(company_id, context="knowledge retrieval")
    max_chunks = limit if limit is not None else settings.knowledge_max_chunks
    threshold = min_score if min_score is not None else settings.knowledge_min_score

    if not settings.knowledge_retrieval_enabled:
        return RetrievalResult(
            company_id=resolved_company_id,
            query=query,
            chunks=[],
            mode="disabled",
            candidates_considered=0,
            has_relevant_source=False,
        )

    now_iso = datetime.now(UTC).isoformat()
    candidates = knowledge_repository.get_retrievable_chunks(
        company_id=resolved_company_id,
        now_iso=now_iso,
        document_types=document_types,
    )
    if not candidates:
        return RetrievalResult(
            company_id=resolved_company_id,
            query=query,
            chunks=[],
            mode="lexical",
            candidates_considered=0,
            has_relevant_source=False,
        )

    query_tokens = tokenize(query)
    query_embedding: list[float] = []
    mode = "lexical"

    provider = get_embedding_provider()
    if settings.enable_vector_search and provider.model != "none":
        try:
            embedded = provider.embed([query])
            query_embedding = embedded[0] if embedded else []
            if query_embedding:
                mode = "hybrid"
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "knowledge.retrieval.embedding_fallback",
                company_id=resolved_company_id,
                error_type=type(exc).__name__,
            )
            query_embedding = []

    scored: list[RetrievedChunk] = []
    for candidate in candidates:
        # Defesa em profundidade: a query já filtra, mas um chunk de outro
        # tenant nunca pode ser pontuado nem devolvido.
        if int(candidate["company_id"]) != resolved_company_id:
            continue

        lexical = _lexical_score(query_tokens, str(candidate.get("search_text") or ""))
        semantic = 0.0
        if query_embedding and candidate.get("embedding_json"):
            import json

            try:
                stored_vector = json.loads(candidate["embedding_json"])
            except (TypeError, ValueError):
                stored_vector = []
            semantic = cosine_similarity(query_embedding, stored_vector)

        base_score = max(lexical, semantic) if semantic else lexical
        if semantic and lexical:
            base_score = lexical * 0.5 + semantic * 0.5

        if base_score <= 0:
            continue

        weighted = (
            base_score
            * DOCUMENT_TYPE_WEIGHTS.get(str(candidate.get("document_type") or ""), 1.0)
            * _priority_boost(candidate.get("priority"))
            * _validity_boost(candidate.get("valid_until"))
        )
        if weighted < threshold:
            continue

        content = str(candidate.get("content") or "")
        scored.append(
            RetrievedChunk(
                chunk_id=int(candidate["chunk_id"]),
                document_id=int(candidate["document_id"]),
                document_version_id=int(candidate["document_version_id"]),
                document_title=str(candidate.get("document_title") or ""),
                document_type=str(candidate.get("document_type") or "general_faq"),
                content=neutralize_source_content(content),
                score=round(weighted, 6),
                lexical_score=lexical,
                semantic_score=round(semantic, 6),
                injection_flagged=detect_injection(content),
            )
        )

    scored.sort(key=lambda chunk: chunk.score, reverse=True)
    selected = scored[: max(1, max_chunks)] if scored else []

    result = RetrievalResult(
        company_id=resolved_company_id,
        query=query,
        chunks=selected,
        mode=mode,
        candidates_considered=len(candidates),
        has_relevant_source=bool(selected),
    )

    log_event(
        logger,
        logging.INFO,
        "knowledge.retrieval.completed",
        company_id=resolved_company_id,
        retrieval_mode=mode,
        candidates_considered=len(candidates),
        selected_count=len(selected),
        source_ids=[chunk.chunk_id for chunk in selected],
        injection_flagged_count=sum(1 for chunk in selected if chunk.injection_flagged),
    )
    return result


def format_sources_for_prompt(result: RetrievalResult) -> str:
    """Renderiza as fontes como dados citáveis, delimitados e rotulados."""
    if not result.chunks:
        return (
            "- Nenhuma fonte relevante encontrada para esta pergunta. "
            "Não afirme fato específico; peça esclarecimento ou encaminhe para um humano."
        )

    lines: list[str] = []
    for chunk in result.chunks:
        lines.append(
            f"[fonte:{chunk.chunk_id}] Título: {chunk.document_title} "
            f"| Tipo: {chunk.document_type}\n"
            f"  Conteúdo: {chunk.content}"
        )
    return "\n".join(lines)
