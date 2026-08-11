"""Dataset: split agrupado, near-duplicates, congelamento e reprodutibilidade."""

import pytest

from app.services.dataset_service import (
    DatasetError,
    DatasetItem,
    assert_version_is_mutable,
    build_dataset_version,
    check_split_leakage,
    collect_dataset_candidates,
    compute_dataset_hash,
    deduplicate,
    freeze_dataset_version,
    near_duplicate_key,
    rebuild_dataset_version_items,
    split_by_group,
)
from app.storage import dataset_repository
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


def _seed_examples(company_id: int, *, conversations: int = 6, per_conversation: int = 3) -> None:
    for conversation_index in range(conversations):
        for turn_index in range(per_conversation):
            repository.create_message_example(
                company_id=company_id,
                profile_name="default",
                scenario_type=["lead", "qualification", "closing"][turn_index % 3],
                customer_profile="new_customer",
                customer_message=f"Pergunta {conversation_index}-{turn_index} sobre o serviço",
                approved_reply=f"Resposta detalhada {conversation_index}-{turn_index} da equipe",
                quality_score=9.0,
                used_for_training=True,
                approval_status="approved",
                source_type="conversation_import",
                source_reference=f"training_candidate:conv-{conversation_index}",
                pii_scrubbed=True,
            )


def test_only_approved_examples_enter_the_dataset(company_factory) -> None:
    company_id = company_factory("ds-approval")

    repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="lead",
        customer_message="Aprovado",
        approved_reply="Resposta aprovada da equipe",
        quality_score=9.0,
        used_for_training=True,
        approval_status="approved",
    )
    repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="lead",
        customer_message="Pendente",
        approved_reply="Resposta ainda pendente de revisão",
        quality_score=9.0,
        used_for_training=True,
        approval_status="pending",
    )

    candidates = collect_dataset_candidates(company_id=company_id)
    assert [item.customer_message for item in candidates] == ["Aprovado"]


def test_excluded_scenarios_never_enter_the_dataset(company_factory) -> None:
    company_id = company_factory("ds-excluded")

    for scenario in ("financeiro", "suporte", "financial", "support"):
        repository.create_message_example(
            company_id=company_id,
            profile_name="default",
            scenario_type=scenario,
            customer_message=f"Mensagem de {scenario}",
            approved_reply=f"Resposta sobre {scenario} para o cliente",
            quality_score=9.5,
            used_for_training=True,
            approval_status="approved",
        )

    assert collect_dataset_candidates(company_id=company_id) == []


def test_split_keeps_a_whole_conversation_in_one_bucket(company_factory) -> None:
    company_id = company_factory("ds-grouping")
    _seed_examples(company_id, conversations=8, per_conversation=3)

    version = build_dataset_version(company_id=company_id, seed=7)
    items = dataset_repository.get_items(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
    )

    splits_by_group: dict[str, set[str]] = {}
    for item in items:
        splits_by_group.setdefault(str(item["group_key"]), set()).add(str(item["split"]))

    assert splits_by_group
    assert all(len(splits) == 1 for splits in splits_by_group.values())

    leakage = check_split_leakage(company_id=company_id, dataset_version_id=int(version["id"]))
    assert leakage["has_leakage"] is False


def test_split_produces_train_validation_and_holdout(company_factory) -> None:
    company_id = company_factory("ds-splits")
    _seed_examples(company_id, conversations=10, per_conversation=2)

    version = build_dataset_version(
        company_id=company_id,
        seed=11,
        validation_ratio=0.2,
        holdout_ratio=0.1,
    )
    distribution = version["stats"]["split_distribution"]

    assert distribution["train"] > 0
    assert distribution["validation"] > 0
    assert distribution["holdout"] > 0


def test_split_is_reproducible_for_the_same_seed(company_factory) -> None:
    company_id = company_factory("ds-reproducible")
    _seed_examples(company_id, conversations=6, per_conversation=2)

    first = build_dataset_version(company_id=company_id, seed=99)
    second = build_dataset_version(company_id=company_id, seed=99)
    third = build_dataset_version(company_id=company_id, seed=1234)

    assert first["content_hash"] == second["content_hash"]
    assert first["content_hash"] != third["content_hash"]


def test_near_duplicates_are_removed_before_splitting(company_factory) -> None:
    company_id = company_factory("ds-neardup")

    base_message = "Bom dia, gostaria de saber o valor do pacote completo"
    variations = [
        base_message,
        f"{base_message}!",
        "bom dia, GOSTARIA de saber o valor do pacote completo...",
    ]
    for index, message in enumerate(variations):
        repository.create_message_example(
            company_id=company_id,
            profile_name="default",
            scenario_type="lead",
            customer_message=message,
            approved_reply="Claro, o pacote completo inclui todos os serviços listados",
            quality_score=9.0,
            used_for_training=True,
            approval_status="approved",
            source_reference=f"training_candidate:conv-{index}",
        )

    candidates = collect_dataset_candidates(company_id=company_id)
    assert len(candidates) == 3

    unique, stats = deduplicate(candidates)
    assert len(unique) == 1
    assert stats["exact_duplicates_removed"] + stats["near_duplicates_removed"] == 2

    version = build_dataset_version(company_id=company_id, seed=3)
    leakage = check_split_leakage(company_id=company_id, dataset_version_id=int(version["id"]))
    assert leakage["leaked_near_duplicate_count"] == 0


def test_near_duplicate_key_is_stable_across_cosmetic_changes() -> None:
    first = near_duplicate_key("Quanto custa o serviço?", "Custa 100 reais.")
    second = near_duplicate_key("quanto custa o SERVICO", "custa 100 reais")

    assert first == second
    assert first != near_duplicate_key("Vocês entregam?", "Sim, entregamos.")


def test_frozen_dataset_version_is_immutable(company_factory) -> None:
    company_id = company_factory("ds-frozen")
    _seed_examples(company_id, conversations=4, per_conversation=2)

    version = build_dataset_version(company_id=company_id)
    frozen = freeze_dataset_version(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
        frozen_by="operador",
    )

    assert frozen["status"] == dataset_repository.STATUS_FROZEN
    assert frozen["content_hash"] == version["content_hash"]

    with pytest.raises(DatasetError, match="immutable"):
        assert_version_is_mutable(
            company_id=company_id,
            dataset_version_id=int(version["id"]),
        )

    with pytest.raises(DatasetError, match="immutable"):
        rebuild_dataset_version_items(
            company_id=company_id,
            dataset_version_id=int(version["id"]),
        )

    # Congelar de novo é idempotente e não altera o conteúdo.
    again = freeze_dataset_version(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
        frozen_by="operador",
    )
    assert again["content_hash"] == frozen["content_hash"]


def test_freezing_a_new_version_supersedes_the_previous_one(company_factory) -> None:
    company_id = company_factory("ds-supersede")
    _seed_examples(company_id, conversations=4, per_conversation=2)

    first = build_dataset_version(company_id=company_id)
    freeze_dataset_version(
        company_id=company_id,
        dataset_version_id=int(first["id"]),
        frozen_by="operador",
    )
    second = build_dataset_version(company_id=company_id)
    freeze_dataset_version(
        company_id=company_id,
        dataset_version_id=int(second["id"]),
        frozen_by="operador",
    )

    reloaded_first = dataset_repository.get_version(
        company_id=company_id,
        dataset_version_id=int(first["id"]),
    )
    assert reloaded_first["status"] == dataset_repository.STATUS_SUPERSEDED
    latest = dataset_repository.get_latest_frozen_version(company_id=company_id)
    assert int(latest["id"]) == int(second["id"])


def test_dataset_versions_are_scoped_by_company(company_factory) -> None:
    company_a = company_factory("ds-tenant-a")
    company_b = company_factory("ds-tenant-b")
    _seed_examples(company_a, conversations=3, per_conversation=2)

    version = build_dataset_version(company_id=company_a)

    assert dataset_repository.get_version(
        company_id=company_b,
        dataset_version_id=int(version["id"]),
    ) is None
    assert dataset_repository.get_items(
        company_id=company_b,
        dataset_version_id=int(version["id"]),
    ) == []


def test_split_by_group_never_splits_a_single_group() -> None:
    # Grupos sintéticos: dois itens por grupo, cinco grupos.
    items = [
        DatasetItem(
            split="",
            group_key=f"group-{index // 2}",
            source_type="message_example",
            source_id=index,
            scenario_type="lead",
            customer_profile="new_customer",
            profile_name="default",
            context=[],
            customer_message=f"mensagem {index}",
            approved_reply=f"resposta {index}",
            style_notes=None,
            quality_score=9.0,
            content_hash=f"hash-{index}",
            near_duplicate_key=f"near-{index}",
        )
        for index in range(10)
    ]

    assigned = split_by_group(items, seed=5, validation_ratio=0.2, holdout_ratio=0.2)

    by_group: dict[str, set[str]] = {}
    for item in assigned:
        by_group.setdefault(item.group_key, set()).add(item.split)
    assert all(len(splits) == 1 for splits in by_group.values())
    assert compute_dataset_hash(assigned) == compute_dataset_hash(
        split_by_group(items, seed=5, validation_ratio=0.2, holdout_ratio=0.2)
    )
