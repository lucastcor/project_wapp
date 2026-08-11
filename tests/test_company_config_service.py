"""Company context versionado como fonte operacional."""

import pytest

from app.core.company_context import get_seed_company_context
from app.services import company_config_service
from app.services.company_config_service import (
    CompanyConfigError,
    bootstrap_company_config,
    get_company_config,
    invalidate_company_config,
    list_company_config_versions,
    publish_company_config,
    save_company_config,
)
from app.storage import company_config_repository


@pytest.fixture(autouse=True)
def _clear_config_cache():
    invalidate_company_config()
    yield
    invalidate_company_config()


def test_saving_creates_a_new_version_without_overwriting_history(company_factory) -> None:
    company_id = company_factory("cfg-history")
    base_context = get_seed_company_context(None)

    first = save_company_config(
        company_id=company_id,
        context={**base_context, "company_name": "Versão 1"},
        author="autor-1",
        change_note="primeira",
    )
    second = save_company_config(
        company_id=company_id,
        context={**base_context, "company_name": "Versão 2"},
        author="autor-2",
        change_note="segunda",
    )

    assert first["version"] == 1
    assert second["version"] == 2

    history = list_company_config_versions(company_id=company_id)
    assert [entry["version"] for entry in history] == [2, 1]
    assert history[0]["status"] == "published"
    assert history[1]["status"] == "archived"
    assert history[0]["author"] == "autor-2"

    config = get_company_config(company_id, use_cache=False)
    assert config.context["company_name"] == "Versão 2"
    assert config.source == "database"


def test_draft_version_is_not_served_until_published(company_factory) -> None:
    company_id = company_factory("cfg-draft")
    base_context = get_seed_company_context(None)

    save_company_config(
        company_id=company_id,
        context={**base_context, "company_name": "Publicada"},
        publish=True,
    )
    draft = save_company_config(
        company_id=company_id,
        context={**base_context, "company_name": "Rascunho"},
        publish=False,
    )

    invalidate_company_config(company_id)
    assert get_company_config(company_id, use_cache=False).context["company_name"] == "Publicada"

    publish_company_config(company_id=company_id, version_id=int(draft["id"]), author="op")
    invalidate_company_config(company_id)
    assert get_company_config(company_id, use_cache=False).context["company_name"] == "Rascunho"


def test_content_hash_changes_with_the_content(company_factory) -> None:
    company_id = company_factory("cfg-hash")
    base_context = get_seed_company_context(None)

    first = save_company_config(company_id=company_id, context=base_context)
    second = save_company_config(
        company_id=company_id,
        context={**base_context, "company_name": "Outro nome"},
    )

    assert first["content_hash"] != second["content_hash"]


def test_invalid_context_is_rejected(company_factory) -> None:
    company_id = company_factory("cfg-invalid")

    with pytest.raises(CompanyConfigError, match="missing required keys"):
        save_company_config(company_id=company_id, context={"company_name": "Só o nome"})


def test_capabilities_come_from_the_vertical_pack_with_overrides(company_factory) -> None:
    company_id = company_factory("cfg-capabilities", business_type="clinic")
    base_context = get_seed_company_context(None)

    save_company_config(
        company_id=company_id,
        context=base_context,
        capabilities={"finetuning": True},
        policies={"allow_price_quotes": True},
    )
    invalidate_company_config(company_id)
    config = get_company_config(company_id, use_cache=False)

    # Preset da vertical de saúde.
    assert config.vertical_pack == "clinic"
    assert config.has_capability("scheduling") is True
    assert config.policy("pii_policy") == "healthcare_strict"
    # Override explícito da empresa vence o preset.
    assert config.has_capability("finetuning") is True
    assert config.policy("allow_price_quotes") is True


def test_unknown_capability_keys_are_ignored(company_factory) -> None:
    company_id = company_factory("cfg-unknown-capability")

    save_company_config(
        company_id=company_id,
        context=get_seed_company_context(None),
        capabilities={"capacidade_inexistente": True},
    )
    invalidate_company_config(company_id)
    config = get_company_config(company_id, use_cache=False)

    assert "capacidade_inexistente" not in config.capabilities


def test_bootstrap_requires_a_seed_for_the_company_slug(company_factory) -> None:
    company_id = company_factory("cfg-no-seed")

    with pytest.raises(CompanyConfigError, match="No company context seed"):
        bootstrap_company_config(company_id=company_id)


def test_bootstrap_is_idempotent_when_a_version_exists(company_factory) -> None:
    company_id = company_factory("cfg-bootstrap")
    save_company_config(company_id=company_id, context=get_seed_company_context(None))

    config = bootstrap_company_config(company_id=company_id)

    assert config.version == 1
    assert len(list_company_config_versions(company_id=company_id)) == 1


def test_versions_are_scoped_by_company(company_factory) -> None:
    company_a = company_factory("cfg-tenant-a")
    company_b = company_factory("cfg-tenant-b")

    stored = save_company_config(company_id=company_a, context=get_seed_company_context(None))

    assert company_config_repository.get_context_version(
        company_id=company_b,
        version_id=int(stored["id"]),
    ) is None
    assert company_config_repository.get_published_context(company_id=company_b) is None


def test_cache_is_invalidated_after_publishing(company_factory) -> None:
    company_id = company_factory("cfg-cache")
    base_context = get_seed_company_context(None)

    save_company_config(company_id=company_id, context={**base_context, "company_name": "Antes"})
    assert get_company_config(company_id).context["company_name"] == "Antes"

    save_company_config(company_id=company_id, context={**base_context, "company_name": "Depois"})
    # Sem invalidação explícita a leitura seguinte já reflete a nova versão.
    assert get_company_config(company_id).context["company_name"] == "Depois"


def test_production_never_falls_back_to_the_global_template(monkeypatch, company_factory) -> None:
    company_id = company_factory("cfg-production")
    monkeypatch.setattr(company_config_service, "is_production_like", lambda: True)

    with pytest.raises(CompanyConfigError, match="Refusing to answer with a generic identity"):
        get_company_config(company_id, use_cache=False)
