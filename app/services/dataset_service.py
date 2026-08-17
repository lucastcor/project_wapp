"""Ciclo de vida do dataset de fine-tuning.

Correções em relação ao pipeline anterior:
- split agrupado por conversa/identidade anonimizada, não por linha, para que
  mensagens do mesmo contato não caiam em treino e validação ao mesmo tempo;
- deduplicação exata **e** de near-duplicates;
- holdout separado de validação;
- versões congeláveis com hash de conteúdo, o que torna o dataset reproduzível
  e auditável;
- itens sempre passam por scrub de PII antes de entrar na versão.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.taxonomy import (
    EXCLUDED_TRAINING_SCENARIOS,
    normalize_with_fallback,
)
from app.core.tenancy import require_company_id
from app.services.company_config_service import get_company_config
from app.services.pii_service import POLICY_STANDARD, anonymize_text, pseudonymize_identifier
from app.storage import dataset_repository
from app.storage.repository import RuntimeRepository

logger = get_logger(__name__)
repository = RuntimeRepository()

MIN_QUALITY_SCORE = 8.0
NEAR_DUPLICATE_SHINGLE_SIZE = 4


class DatasetError(Exception):
    pass


@dataclass(frozen=True)
class DatasetItem:
    split: str
    group_key: str
    source_type: str
    source_id: int | None
    scenario_type: str
    customer_profile: str | None
    profile_name: str
    context: list[dict[str, str]]
    customer_message: str
    approved_reply: str
    style_notes: str | None
    quality_score: float
    content_hash: str
    near_duplicate_key: str


def _deterministic_rank(group_key: str, seed: int) -> str:
    """Posição estável de um grupo no embaralhamento, dada uma seed."""
    return hashlib.blake2b(
        f"{seed}:{group_key}".encode(),
        digest_size=16,
    ).hexdigest()


def _fold(value: str | None) -> str:
    text = str(value or "").strip().lower()
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9\s]", " ", stripped)


def content_hash(customer_message: str, approved_reply: str) -> str:
    payload = f"{_fold(customer_message)}||{_fold(approved_reply)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


SIMHASH_BITS = 64


def _shingles(customer_message: str, approved_reply: str) -> set[str]:
    """Shingles de palavras do par completo."""
    tokens = [
        token
        for token in f"{_fold(customer_message)} {_fold(approved_reply)}".split()
        if token
    ]
    if not tokens:
        return set()
    if len(tokens) <= NEAR_DUPLICATE_SHINGLE_SIZE:
        return {" ".join(tokens)}
    return {
        " ".join(tokens[index : index + NEAR_DUPLICATE_SHINGLE_SIZE])
        for index in range(len(tokens) - NEAR_DUPLICATE_SHINGLE_SIZE + 1)
    }


def simhash(customer_message: str, approved_reply: str) -> int:
    """SimHash de 64 bits sobre os shingles do par.

    Substitui o min-hash de permutação única usado antes: igualdade exata de
    uma assinatura de 1 permutação só detecta o caso em que o *mesmo* shingle
    é o mínimo nos dois textos, o que é frágil nos dois sentidos — deixa passar
    duplicata real e agrupa textos distintos por acidente.

    Com SimHash a proximidade vira distância de Hamming, comparável contra um
    limiar configurável (`NEAR_DUPLICATE_MAX_DISTANCE`).
    """
    shingle_set = _shingles(customer_message, approved_reply)
    if not shingle_set:
        return 0

    weights = [0] * SIMHASH_BITS
    for shingle in shingle_set:
        digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(SIMHASH_BITS):
            weights[bit] += 1 if (value >> bit) & 1 else -1

    fingerprint = 0
    for bit in range(SIMHASH_BITS):
        if weights[bit] > 0:
            fingerprint |= 1 << bit
    return fingerprint


def hamming_distance(left: int, right: int) -> int:
    return int(left ^ right).bit_count()


def jaccard_similarity(
    left: tuple[str, str],
    right: tuple[str, str],
) -> float:
    """Similaridade exata de shingles, usada para confirmar um candidato."""
    left_set = _shingles(*left)
    right_set = _shingles(*right)
    if not left_set or not right_set:
        return 0.0
    intersection = len(left_set & right_set)
    union = len(left_set | right_set)
    return intersection / union if union else 0.0


def near_duplicate_key(customer_message: str, approved_reply: str) -> str:
    """Assinatura persistida do par, em hexadecimal de 64 bits."""
    return f"{simhash(customer_message, approved_reply):016x}"


def _excluded_scenarios(company_id: int) -> set[str]:
    config = get_company_config(company_id)
    extra = config.policy("extra_excluded_training_scenarios", []) or []
    return set(EXCLUDED_TRAINING_SCENARIOS) | {str(item).strip().lower() for item in extra}


def collect_dataset_candidates(*, company_id: int) -> list[DatasetItem]:
    """Reúne exemplos aprovados da empresa, já normalizados e sem PII."""
    resolved_company_id = require_company_id(company_id, context="dataset collection")
    config = get_company_config(resolved_company_id)
    pii_policy = str(config.policy("pii_policy", POLICY_STANDARD))
    excluded = _excluded_scenarios(resolved_company_id)

    examples = repository.get_exportable_message_examples(company_id=resolved_company_id)

    items: list[DatasetItem] = []
    for example in examples:
        scenario_type, _ = normalize_with_fallback("scenario_type", example.get("scenario_type"))
        if scenario_type in excluded:
            continue

        quality_score = float(example.get("quality_score") or 0)
        if quality_score < MIN_QUALITY_SCORE:
            continue

        customer_message, customer_report = anonymize_text(
            example.get("customer_message"), policy=pii_policy
        )
        approved_reply, reply_report = anonymize_text(
            example.get("approved_reply"), policy=pii_policy
        )
        customer_message = customer_message.strip()
        approved_reply = approved_reply.strip()
        if not customer_message or not approved_reply:
            continue

        profile_name, _ = normalize_with_fallback("profile_name", example.get("profile_name"))
        customer_profile = None
        if example.get("customer_profile"):
            customer_profile, _ = normalize_with_fallback(
                "customer_profile", example.get("customer_profile")
            )

        # Agrupamento pela identidade da conversa/contato de origem. Cair no
        # próprio exemplo só acontece para conteúdo cadastrado manualmente,
        # que por definição não compartilha conversa com outro item.
        group_key = str(example.get("source_group_key") or "").strip()
        if not group_key:
            group_key = pseudonymize_identifier(
                f"message_example:{example['id']}",
                company_id=resolved_company_id,
            )

        # O contexto multi-turno acompanha o par e também passa por scrub.
        raw_context = example.get("conversation_context_json")
        conversation_context: list[dict[str, str]] = []
        if raw_context:
            try:
                parsed_context = json.loads(str(raw_context))
            except (TypeError, ValueError):
                parsed_context = []
            for turn in parsed_context if isinstance(parsed_context, list) else []:
                if not isinstance(turn, dict):
                    continue
                turn_content, _ = anonymize_text(turn.get("content"), policy=pii_policy)
                turn_content = turn_content.strip()
                if not turn_content:
                    continue
                conversation_context.append(
                    {"role": str(turn.get("role") or "customer"), "content": turn_content}
                )

        items.append(
            DatasetItem(
                split="",
                group_key=group_key,
                source_type="message_example",
                source_id=int(example["id"]),
                scenario_type=scenario_type,
                customer_profile=customer_profile,
                profile_name=profile_name,
                context=conversation_context,
                customer_message=customer_message,
                approved_reply=approved_reply,
                style_notes=example.get("style_notes"),
                quality_score=quality_score,
                content_hash=content_hash(customer_message, approved_reply),
                near_duplicate_key=near_duplicate_key(customer_message, approved_reply),
            )
        )

        if customer_report.has_pii or reply_report.has_pii:
            log_event(
                logger,
                logging.INFO,
                "dataset.item.pii_scrubbed",
                company_id=resolved_company_id,
                message_example_id=int(example["id"]),
                replacements=(customer_report.counts + reply_report.counts).total(),
            )

    return items


NEAR_DUPLICATE_MIN_JACCARD = 0.6


def _is_near_duplicate(
    item: DatasetItem,
    other: DatasetItem,
    *,
    max_distance: int,
) -> bool:
    """SimHash filtra candidatos; Jaccard confirma.

    A confirmação evita descartar pares semanticamente distintos que só
    compartilham saudação e estrutura.
    """
    if hamming_distance(int(item.near_duplicate_key, 16), int(other.near_duplicate_key, 16)) > max_distance:
        return False
    similarity = jaccard_similarity(
        (item.customer_message, item.approved_reply),
        (other.customer_message, other.approved_reply),
    )
    return similarity >= NEAR_DUPLICATE_MIN_JACCARD


def deduplicate(
    items: list[DatasetItem],
    *,
    max_distance: int | None = None,
) -> tuple[list[DatasetItem], dict[str, Any]]:
    """Remove duplicata exata e near-duplicate, nessa ordem.

    A deduplicação acontece **antes** do split: assim nenhum near-duplicate
    sobrevive para cruzar train/validation/holdout.
    """
    threshold = (
        max_distance if max_distance is not None else settings.near_duplicate_max_distance
    )
    seen_exact: set[str] = set()
    unique: list[DatasetItem] = []
    exact_removed = 0
    near_removed = 0
    removal_reasons: list[dict[str, Any]] = []

    for item in items:
        if item.content_hash in seen_exact:
            exact_removed += 1
            removal_reasons.append(
                {"source_id": item.source_id, "reason": "exact_duplicate"}
            )
            continue

        duplicate_of = next(
            (
                kept
                for kept in unique
                if _is_near_duplicate(item, kept, max_distance=threshold)
            ),
            None,
        )
        if duplicate_of is not None:
            near_removed += 1
            removal_reasons.append(
                {
                    "source_id": item.source_id,
                    "reason": "near_duplicate",
                    "duplicate_of_source_id": duplicate_of.source_id,
                    "hamming_distance": hamming_distance(
                        int(item.near_duplicate_key, 16),
                        int(duplicate_of.near_duplicate_key, 16),
                    ),
                }
            )
            continue

        seen_exact.add(item.content_hash)
        unique.append(item)

    return unique, {
        "exact_duplicates_removed": exact_removed,
        "near_duplicates_removed": near_removed,
        "near_duplicate_max_distance": threshold,
        "removal_reasons": removal_reasons[:50],
    }


def split_by_group(
    items: list[DatasetItem],
    *,
    seed: int,
    validation_ratio: float,
    holdout_ratio: float,
) -> list[DatasetItem]:
    """Split agrupado e reproduzível.

    A unidade de split é o grupo (conversa/identidade anonimizada), nunca a
    linha: assim nenhuma mensagem de uma conversa aparece em dois conjuntos.
    """
    if not items:
        return []

    groups: dict[str, list[DatasetItem]] = defaultdict(list)
    for item in items:
        groups[item.group_key].append(item)

    # Ordenação determinística por hash da chave + seed. Substitui
    # `random.shuffle`, que além de ser um PRNG não-criptográfico não tem
    # ordem garantida entre versões do Python — o split precisa ser
    # reproduzível anos depois, inclusive em outro interpretador.
    group_keys = sorted(groups.keys(), key=lambda key: _deterministic_rank(key, seed))

    total_groups = len(group_keys)
    holdout_size = int(total_groups * holdout_ratio)
    validation_size = int(total_groups * validation_ratio)

    # Com poucos grupos, treino tem prioridade: um dataset minúsculo já é
    # barrado pelo readiness, e vazar grupo é pior que não ter validação.
    if total_groups >= 3:
        holdout_size = max(1, holdout_size) if holdout_ratio > 0 else 0
        validation_size = max(1, validation_size) if validation_ratio > 0 else 0
    if holdout_size + validation_size >= total_groups:
        holdout_size = 0
        validation_size = 1 if total_groups > 1 else 0

    holdout_keys = set(group_keys[:holdout_size])
    validation_keys = set(group_keys[holdout_size : holdout_size + validation_size])

    assigned: list[DatasetItem] = []
    for group_key in group_keys:
        if group_key in holdout_keys:
            split = dataset_repository.SPLIT_HOLDOUT
        elif group_key in validation_keys:
            split = dataset_repository.SPLIT_VALIDATION
        else:
            split = dataset_repository.SPLIT_TRAIN
        assigned.extend(
                DatasetItem(
                    split=split,
                    group_key=item.group_key,
                    source_type=item.source_type,
                    source_id=item.source_id,
                    scenario_type=item.scenario_type,
                    customer_profile=item.customer_profile,
                    profile_name=item.profile_name,
                    context=item.context,
                    customer_message=item.customer_message,
                    approved_reply=item.approved_reply,
                    style_notes=item.style_notes,
                    quality_score=item.quality_score,
                    content_hash=item.content_hash,
                    near_duplicate_key=item.near_duplicate_key,
                )
            for item in groups[group_key]
        )
    return assigned


def build_stats(items: list[DatasetItem], dedup_stats: dict[str, int]) -> dict[str, Any]:
    split_counter = Counter(item.split for item in items)
    scenario_counter = Counter(item.scenario_type for item in items)
    profile_counter = Counter(item.customer_profile or "unknown" for item in items)
    style_profile_counter = Counter(item.profile_name for item in items)
    quality_scores = [item.quality_score for item in items]

    return {
        "total_items": len(items),
        "distinct_groups": len({item.group_key for item in items}),
        "split_distribution": dict(sorted(split_counter.items())),
        "scenario_distribution": dict(sorted(scenario_counter.items())),
        "customer_profile_distribution": dict(sorted(profile_counter.items())),
        "style_profile_distribution": dict(sorted(style_profile_counter.items())),
        "average_quality_score": (
            round(sum(quality_scores) / len(quality_scores), 4) if quality_scores else 0.0
        ),
        **dedup_stats,
    }


def compute_dataset_hash(items: list[DatasetItem]) -> str:
    payload = json.dumps(
        [
            {
                "split": item.split,
                "group_key": item.group_key,
                "content_hash": item.content_hash,
                "scenario_type": item.scenario_type,
                "context_turns": len(item.context),
            }
            for item in sorted(items, key=lambda entry: (entry.split, entry.content_hash))
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_dataset_version(
    *,
    company_id: int,
    name: str | None = None,
    seed: int = 42,
    validation_ratio: float = 0.2,
    holdout_ratio: float = 0.1,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Materializa uma nova versão de dataset em estado `draft`."""
    resolved_company_id = require_company_id(company_id, context="dataset build")

    candidates = collect_dataset_candidates(company_id=resolved_company_id)
    deduplicated, dedup_stats = deduplicate(candidates)
    assigned = split_by_group(
        deduplicated,
        seed=seed,
        validation_ratio=validation_ratio,
        holdout_ratio=holdout_ratio,
    )

    dataset_version_id = dataset_repository.create_version(
        company_id=resolved_company_id,
        name=name,
        split_seed=seed,
        validation_ratio=validation_ratio,
        holdout_ratio=holdout_ratio,
        created_by=created_by,
    )
    dataset_repository.replace_items(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
        items=[
            {
                "split": item.split,
                "group_key": item.group_key,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "scenario_type": item.scenario_type,
                "customer_profile": item.customer_profile,
                "profile_name": item.profile_name,
                "context": item.context,
                "customer_message": item.customer_message,
                "approved_reply": item.approved_reply,
                "style_notes": item.style_notes,
                "quality_score": item.quality_score,
                "content_hash": item.content_hash,
                "near_duplicate_key": item.near_duplicate_key,
            }
            for item in assigned
        ],
    )

    stats = build_stats(assigned, dedup_stats)
    dataset_repository.update_version_metadata(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
        content_hash=compute_dataset_hash(assigned),
        stats=stats,
    )

    log_event(
        logger,
        logging.INFO,
        "dataset.version.built",
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
        total_items=stats["total_items"],
        distinct_groups=stats["distinct_groups"],
    )
    return dataset_repository.get_version(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    ) or {}


def freeze_dataset_version(
    *,
    company_id: int,
    dataset_version_id: int,
    frozen_by: str | None,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Congela a versão: a partir daqui ela é imutável."""
    resolved_company_id = require_company_id(company_id, context="dataset freeze")
    version = dataset_repository.get_version(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )
    if not version:
        raise DatasetError(
            f"Dataset version {dataset_version_id} not found for company_id={resolved_company_id}."
        )
    if version["status"] == dataset_repository.STATUS_FROZEN:
        return version
    if version["status"] not in dataset_repository.MUTABLE_STATUSES:
        raise DatasetError(
            f"Dataset version {dataset_version_id} is {version['status']} and cannot be frozen."
        )

    dataset_repository.update_version_metadata(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
        status=dataset_repository.STATUS_FROZEN,
        frozen_by=frozen_by,
        readiness=readiness,
    )
    dataset_repository.supersede_other_frozen_versions(
        company_id=resolved_company_id,
        keep_version_id=dataset_version_id,
    )

    log_event(
        logger,
        logging.INFO,
        "dataset.version.frozen",
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )
    return dataset_repository.get_version(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    ) or {}


def assert_version_is_mutable(*, company_id: int, dataset_version_id: int) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="dataset mutation guard")
    version = dataset_repository.get_version(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )
    if not version:
        raise DatasetError(
            f"Dataset version {dataset_version_id} not found for company_id={resolved_company_id}."
        )
    if version["status"] not in dataset_repository.MUTABLE_STATUSES:
        raise DatasetError(
            f"Dataset version {dataset_version_id} is {version['status']} and is immutable."
        )
    return version


def rebuild_dataset_version_items(
    *,
    company_id: int,
    dataset_version_id: int,
) -> dict[str, Any]:
    """Recalcula os itens de uma versão ainda mutável."""
    resolved_company_id = require_company_id(company_id, context="dataset rebuild")
    version = assert_version_is_mutable(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )

    candidates = collect_dataset_candidates(company_id=resolved_company_id)
    deduplicated, dedup_stats = deduplicate(candidates)
    assigned = split_by_group(
        deduplicated,
        seed=int(version["split_seed"]),
        validation_ratio=float(version["validation_ratio"]),
        holdout_ratio=float(version["holdout_ratio"]),
    )
    dataset_repository.replace_items(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
        items=[
            {
                "split": item.split,
                "group_key": item.group_key,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "scenario_type": item.scenario_type,
                "customer_profile": item.customer_profile,
                "profile_name": item.profile_name,
                "context": item.context,
                "customer_message": item.customer_message,
                "approved_reply": item.approved_reply,
                "style_notes": item.style_notes,
                "quality_score": item.quality_score,
                "content_hash": item.content_hash,
                "near_duplicate_key": item.near_duplicate_key,
            }
            for item in assigned
        ],
    )
    stats = build_stats(assigned, dedup_stats)
    dataset_repository.update_version_metadata(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
        content_hash=compute_dataset_hash(assigned),
        stats=stats,
    )
    return dataset_repository.get_version(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    ) or {}


def get_dataset_version(*, company_id: int, dataset_version_id: int) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="dataset read")
    version = dataset_repository.get_version(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )
    if not version:
        raise DatasetError(
            f"Dataset version {dataset_version_id} not found for company_id={resolved_company_id}."
        )
    return version


def list_dataset_versions(*, company_id: int) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="dataset listing")
    return dataset_repository.list_versions(company_id=resolved_company_id)


def check_split_leakage(*, company_id: int, dataset_version_id: int) -> dict[str, Any]:
    """Verifica se algum grupo ou near-duplicate cruzou splits."""
    resolved_company_id = require_company_id(company_id, context="dataset leakage check")
    items = dataset_repository.get_items(
        company_id=resolved_company_id,
        dataset_version_id=dataset_version_id,
    )

    splits_by_group: dict[str, set[str]] = defaultdict(set)
    for item in items:
        splits_by_group[str(item["group_key"])].add(str(item["split"]))

    leaked_groups = sorted(key for key, splits in splits_by_group.items() if len(splits) > 1)

    # Near-duplicate entre splits diferentes: comparação por distância de
    # Hamming, com confirmação por Jaccard, exatamente como na deduplicação.
    threshold = settings.near_duplicate_max_distance
    leaked_pairs: list[dict[str, Any]] = []
    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_split[str(item["split"])].append(item)

    split_names = sorted(by_split)
    for left_index, left_split in enumerate(split_names):
        for right_split in split_names[left_index + 1 :]:
            for left_item in by_split[left_split]:
                left_key = str(left_item.get("near_duplicate_key") or "")
                if not left_key:
                    continue
                for right_item in by_split[right_split]:
                    right_key = str(right_item.get("near_duplicate_key") or "")
                    if not right_key:
                        continue
                    try:
                        distance = hamming_distance(int(left_key, 16), int(right_key, 16))
                    except ValueError:
                        continue
                    if distance > threshold:
                        continue
                    similarity = jaccard_similarity(
                        (str(left_item["customer_message"]), str(left_item["approved_reply"])),
                        (str(right_item["customer_message"]), str(right_item["approved_reply"])),
                    )
                    if similarity < NEAR_DUPLICATE_MIN_JACCARD:
                        continue
                    leaked_pairs.append(
                        {
                            "left_split": left_split,
                            "right_split": right_split,
                            "left_source_id": left_item.get("source_id"),
                            "right_source_id": right_item.get("source_id"),
                            "hamming_distance": distance,
                            "jaccard": round(similarity, 4),
                        }
                    )

    return {
        "company_id": resolved_company_id,
        "dataset_version_id": dataset_version_id,
        "leaked_group_count": len(leaked_groups),
        "leaked_near_duplicate_count": len(leaked_pairs),
        "has_leakage": bool(leaked_groups or leaked_pairs),
        "leaked_groups": leaked_groups[:20],
        "leaked_near_duplicate_pairs": leaked_pairs[:20],
        "near_duplicate_max_distance": threshold,
    }
