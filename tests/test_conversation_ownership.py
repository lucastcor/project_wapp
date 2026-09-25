"""P06 - H01 a H03: posse humana explícita da conversa (repositório, migration 072)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.storage import ownership_repository as ownership


def _contact() -> str:
    return f"5511{uuid4().int % 10**9:09d}"


def _user(admin_principal_factory, company_id: int) -> int:
    return int(admin_principal_factory(global_role="operator", company_ids=[company_id])["user_id"])


def test_conversation_is_bot_owned_until_a_human_takes_it(company_factory, admin_principal_factory) -> None:
    company_id = company_factory("ownership")
    operator = _user(admin_principal_factory, company_id)
    contact_id = _contact()

    assert ownership.is_human_owned(company_id=company_id, contact_id=contact_id) is False

    taken = ownership.take_ownership(company_id=company_id, contact_id=contact_id, user_id=operator, reason="cliente pediu humano")
    assert taken.outcome == "taken"
    assert taken.ownership["owner"] == "human"
    assert taken.ownership["owner_user_id"] == operator
    assert ownership.is_human_owned(company_id=company_id, contact_id=contact_id) is True

    again = ownership.take_ownership(company_id=company_id, contact_id=contact_id, user_id=operator)
    assert again.outcome == "already_yours"

    released = ownership.release_ownership(company_id=company_id, contact_id=contact_id, user_id=operator)
    assert released.outcome == "released"
    assert released.ownership["owner"] == "bot"
    assert released.ownership["version"] > taken.ownership["version"]
    assert ownership.is_human_owned(company_id=company_id, contact_id=contact_id) is False


def test_another_operator_cannot_take_or_release_without_authority(company_factory, admin_principal_factory) -> None:
    company_id = company_factory("ownership-other")
    first = _user(admin_principal_factory, company_id)
    second = _user(admin_principal_factory, company_id)
    contact_id = _contact()
    ownership.take_ownership(company_id=company_id, contact_id=contact_id, user_id=first)

    assert ownership.take_ownership(company_id=company_id, contact_id=contact_id, user_id=second).outcome == "owned_by_other"
    assert ownership.release_ownership(company_id=company_id, contact_id=contact_id, user_id=second).outcome == "owned_by_other"
    assert ownership.is_human_owned(company_id=company_id, contact_id=contact_id) is True

    forced = ownership.release_ownership(
        company_id=company_id, contact_id=contact_id, user_id=second, allow_other_owner=True
    )
    assert forced.outcome == "released"


def test_ownership_is_isolated_per_company(company_factory, admin_principal_factory) -> None:
    company_a = company_factory("ownership-a")
    company_b = company_factory("ownership-b")
    operator = _user(admin_principal_factory, company_a)
    contact_id = _contact()

    ownership.take_ownership(company_id=company_a, contact_id=contact_id, user_id=operator)

    assert ownership.is_human_owned(company_id=company_b, contact_id=contact_id) is False
    assert ownership.list_human_owned(company_id=company_b) == []
    assert [row["contact_id"] for row in ownership.list_human_owned(company_id=company_a)] == [contact_id]
    assert ownership.release_ownership(company_id=company_b, contact_id=contact_id, user_id=operator).outcome == "not_owned"


def test_concurrent_takes_leave_exactly_one_owner(company_factory, admin_principal_factory) -> None:
    company_id = company_factory("ownership-race")
    operators = [_user(admin_principal_factory, company_id) for _ in range(3)]
    for _round in range(5):
        contact_id = _contact()
        barrier = threading.Barrier(len(operators))

        def take(user_id: int, contact: str = contact_id, gate: threading.Barrier = barrier) -> str:
            gate.wait(timeout=10)
            return ownership.take_ownership(company_id=company_id, contact_id=contact, user_id=user_id).outcome

        with ThreadPoolExecutor(max_workers=len(operators)) as executor:
            outcomes = sorted(executor.map(take, operators))

        assert outcomes.count("taken") == 1
        assert outcomes.count("owned_by_other") == len(operators) - 1


def test_invalid_input_is_rejected(company_factory) -> None:
    company_id = company_factory("ownership-invalid")

    with pytest.raises(ValueError):
        ownership.take_ownership(company_id=company_id, contact_id=" ", user_id=1)
    with pytest.raises(ValueError):
        ownership.take_ownership(company_id=company_id, contact_id=_contact(), user_id=0)
