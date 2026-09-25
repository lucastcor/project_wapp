"""booking_operations (069): propostas, aceite e execução por compare-and-set.

Os testes de concorrência usam threads com conexões separadas e rodam nos dois
backends: no PostgreSQL exercitam locks de linha e o advisory lock por contato;
no SQLite exercitam a serialização por BEGIN IMMEDIATE.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.tenancy import TenantScopeError
from app.storage import operation_repository as ops
from app.storage.db import db_transaction
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()
PARAMS = {"starts_at": "2026-10-01T13:00:00+00:00", "timezone": "America/Sao_Paulo", "service": "Consulta"}


def _contact() -> str:
    return f"test-contact-{uuid4().hex[:12]}"


def _propose(company_id: int, contact_id: str, **overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "company_id": company_id,
        "contact_id": contact_id,
        "kind": "create",
        "params": dict(PARAMS),
        "ttl_minutes": 15,
    }
    arguments.update(overrides)
    return ops.create_proposal(**arguments)


def _accept(proposal: dict[str, Any], **overrides: Any) -> ops.AcceptResult:
    arguments: dict[str, Any] = {
        "company_id": proposal["company_id"],
        "contact_id": proposal["contact_id"],
        "operation_id": proposal["id"],
        "params_hash": proposal["params_hash"],
        "confirmation_ref": "wamid-confirm-1",
    }
    arguments.update(overrides)
    return ops.accept_proposal(**arguments)


def _executing(company_id: int) -> int:
    proposal = _propose(company_id, _contact())
    # Referência única: uma confirmação só é consumida uma vez por empresa.
    assert _accept(proposal, confirmation_ref=f"wamid-{uuid4().hex}").outcome == "accepted"
    assert ops.claim_for_execution(company_id=company_id, operation_id=proposal["id"]) is not None
    return int(proposal["id"])


def _booking(company_id: int, contact_id: str) -> int:
    return repository.create_appointment_booking(
        provider="mock",
        title="Consulta",
        starts_at="2026-10-01T13:00:00+00:00",
        ends_at="2026-10-01T14:00:00+00:00",
        timezone="America/Sao_Paulo",
        company_id=company_id,
        contact_id=contact_id,
    )


def _status(company_id: int, operation_id: int) -> str:
    operation = ops.get_operation(company_id=company_id, operation_id=operation_id)
    assert operation is not None
    return str(operation["status"])


def _is_integrity_error(error: BaseException) -> bool:
    if isinstance(error, sqlite3.IntegrityError):
        return True
    return str(getattr(error, "sqlstate", "") or "").startswith("23")


def _raw_insert(company_id: int, contact_id: str, status: str) -> None:
    stamp = datetime.now(UTC).isoformat(timespec="microseconds")
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO booking_operations (
                company_id, contact_id, kind, status, params_json, params_hash,
                idempotency_key, expires_at, created_at, updated_at
            ) VALUES (?, ?, 'create', ?, '{}', 'raw', ?, ?, ?, ?)
            """,
            (company_id, contact_id, status, uuid4().hex, stamp, stamp, stamp),
        )


# ── criação, hash e validação ─────────────────────────────────────────


def test_create_proposal_persists_an_open_proposal(company_factory) -> None:
    company_id = company_factory("ops")
    contact_id = _contact()
    now = datetime.now(UTC)

    operation = _propose(company_id, contact_id, now=now, resource_key="default", target_version="v1", flow_id=7)

    assert operation["status"] == "proposed"
    assert (operation["company_id"], operation["contact_id"], operation["kind"]) == (company_id, contact_id, "create")
    assert operation["params"] == PARAMS
    assert operation["result"] is None
    assert operation["attempt_count"] == 0
    assert operation["source"] == "conversation"
    assert operation["flow_id"] == 7
    assert operation["created_at"] == now.isoformat(timespec="microseconds")
    assert operation["expires_at"] == (now + timedelta(minutes=15)).isoformat(timespec="microseconds")
    assert len(operation["idempotency_key"]) == 32
    assert operation["params_hash"] == ops.compute_params_hash(
        kind="create", booking_id=None, resource_key="default", params=PARAMS, target_version="v1",
    )
    assert ops.get_operation(company_id=company_id, operation_id=operation["id"]) == operation
    assert ops.get_open_proposal(company_id=company_id, contact_id=contact_id, now=now) == operation


def test_params_hash_is_a_canonical_sha256() -> None:
    base: dict[str, Any] = {"kind": "reschedule", "booking_id": 12, "resource_key": "default", "target_version": "etag-1"}
    first = ops.compute_params_hash(params={"b": 1, "a": "ção"}, **base)
    reordered = ops.compute_params_hash(params={"a": "ção", "b": 1}, **base)
    material = json.dumps(
        {"booking_id": 12, "kind": "reschedule", "params": {"a": "ção", "b": 1},
         "resource_key": "default", "target_version": "etag-1"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )

    assert first == reordered == hashlib.sha256(material.encode("utf-8")).hexdigest()
    assert ops.compute_params_hash(params={"a": "ção", "b": 1}, **{**base, "target_version": "etag-2"}) != first
    assert ops.compute_params_hash(params={"a": "ção", "b": 1}, **{**base, "booking_id": 13}) != first
    assert ops.compute_params_hash(params={"a": "ção", "b": 1}, **{**base, "resource_key": "sala-2"}) != first


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "delete"},
        {"source": "webhook"},
        {"ttl_minutes": 0},
        {"ttl_minutes": 1441},
        {"ttl_minutes": True},
        {"kind": "cancel"},
        {"params": ["not", "a", "dict"]},
        {"contact_id": "   "},
        {"now": datetime(2026, 9, 13, 12, 0)},  # noqa: DTZ001 - naive on purpose: must be rejected
        {"idempotency_key": ""},
        {"booking_id": 0},
    ],
    ids=["kind", "source", "ttl-zero", "ttl-too-long", "ttl-bool", "cancel-without-booking",
         "params-not-dict", "blank-contact", "naive-now", "blank-idempotency-key", "booking-zero"],
)
def test_create_proposal_rejects_invalid_input(company_factory, overrides) -> None:
    company_id = company_factory("ops")
    arguments = {"contact_id": _contact(), **overrides}

    with pytest.raises(ValueError):
        _propose(company_id, **arguments)

    assert ops.list_operations(company_id=company_id) == []


def test_repository_requires_a_tenant() -> None:
    with pytest.raises(TenantScopeError):
        _propose(None, _contact())  # type: ignore[arg-type]
    with pytest.raises(TenantScopeError):
        ops.list_operations(company_id=0)
    with pytest.raises(TenantScopeError):
        ops.get_operation(company_id=None, operation_id=1)  # type: ignore[arg-type]


def test_new_proposal_supersedes_only_that_contacts_open_proposal(company_factory) -> None:
    company_id = company_factory("ops")
    other_company = company_factory("ops")
    contact_id = _contact()
    neighbour = _contact()
    first = _propose(company_id, contact_id)
    neighbour_open = _propose(company_id, neighbour)
    other_tenant_open = _propose(other_company, contact_id)

    second = _propose(company_id, contact_id, params={**PARAMS, "service": "Retorno"})

    assert _status(company_id, first["id"]) == "superseded"
    assert second["status"] == "proposed"
    assert ops.get_open_proposal(company_id=company_id, contact_id=contact_id)["id"] == second["id"]
    assert ops.get_open_proposal(company_id=company_id, contact_id=neighbour)["id"] == neighbour_open["id"]
    assert ops.get_open_proposal(company_id=other_company, contact_id=contact_id)["id"] == other_tenant_open["id"]


def test_database_allows_at_most_one_open_proposal_per_contact(company_factory) -> None:
    company_id = company_factory("ops")
    contact_id = _contact()
    _propose(company_id, contact_id)

    with pytest.raises(Exception) as exc_info:
        _raw_insert(company_id, contact_id, "proposed")
    assert _is_integrity_error(exc_info.value)

    # Estados fechados não contam para o índice parcial.
    _raw_insert(company_id, contact_id, "rejected")
    _raw_insert(company_id, _contact(), "proposed")
    assert len(ops.list_operations(company_id=company_id, contact_id=contact_id, statuses=["proposed"])) == 1


def test_idempotency_key_returns_the_same_operation_or_conflicts(company_factory) -> None:
    company_id = company_factory("ops")
    contact_id = _contact()
    first = _propose(company_id, contact_id, idempotency_key="turn-1")

    assert _propose(company_id, contact_id, idempotency_key="turn-1") == first
    assert len(ops.list_operations(company_id=company_id)) == 1

    with pytest.raises(ops.OperationConflictError):
        _propose(company_id, contact_id, idempotency_key="turn-1", params={**PARAMS, "service": "Outro"})
    with pytest.raises(ops.OperationConflictError):
        _propose(company_id, _contact(), idempotency_key="turn-1")
    assert _status(company_id, first["id"]) == "proposed"
    assert len(ops.list_operations(company_id=company_id)) == 1

    other_company = company_factory("ops")
    assert _propose(other_company, contact_id, idempotency_key="turn-1")["id"] != first["id"]


# ── transições ────────────────────────────────────────────────────────


def test_confirmed_operation_executes_once_and_completes(company_factory) -> None:
    company_id = company_factory("ops")
    contact_id = _contact()
    booking_id = _booking(company_id, contact_id)
    proposal = _propose(company_id, contact_id)

    accepted = _accept(proposal)
    assert accepted.outcome == "accepted"
    assert accepted.operation is not None
    assert accepted.operation["status"] == "confirmed"
    assert accepted.operation["confirmation_ref"] == "wamid-confirm-1"
    assert accepted.operation["confirmed_at"]
    assert ops.get_open_proposal(company_id=company_id, contact_id=contact_id) is None

    claimed = ops.claim_for_execution(company_id=company_id, operation_id=proposal["id"])
    assert claimed is not None
    assert (claimed["status"], claimed["attempt_count"]) == ("executing", 1)
    assert claimed["started_at"]
    assert ops.claim_for_execution(company_id=company_id, operation_id=proposal["id"]) is None

    assert ops.complete_operation(
        company_id=company_id, operation_id=proposal["id"], result={"event": "ok"},
        booking_id=booking_id, external_ref="evt-1",
    ) is True
    done = ops.get_operation(company_id=company_id, operation_id=proposal["id"])
    assert done is not None
    assert done["status"] == "completed"
    assert done["result"] == {"event": "ok"}
    assert (done["booking_id"], done["external_ref"], done["attempt_count"]) == (booking_id, "evt-1", 1)
    assert done["completed_at"]

    assert ops.complete_operation(company_id=company_id, operation_id=proposal["id"], result={"event": "again"}) is False
    assert ops.get_operation(company_id=company_id, operation_id=proposal["id"]) == done


def test_completion_keeps_target_booking_unless_overridden(company_factory) -> None:
    company_id = company_factory("ops")
    contact_id = _contact()
    booking_id = _booking(company_id, contact_id)
    cancel = _propose(company_id, contact_id, kind="cancel", booking_id=booking_id)
    _accept(cancel)
    ops.claim_for_execution(company_id=company_id, operation_id=cancel["id"])

    assert ops.complete_operation(company_id=company_id, operation_id=cancel["id"], result={}) is True

    done = ops.get_operation(company_id=company_id, operation_id=cancel["id"])
    assert done is not None
    assert (done["booking_id"], done["external_ref"], done["result"]) == (booking_id, None, {})


def test_invalid_transitions_leave_the_row_untouched(company_factory) -> None:
    company_id = company_factory("ops")
    other_company = company_factory("ops")
    proposal = _propose(company_id, _contact())
    op_id = proposal["id"]
    snapshot = ops.get_operation(company_id=company_id, operation_id=op_id)

    assert ops.claim_for_execution(company_id=company_id, operation_id=op_id) is None
    assert ops.complete_operation(company_id=company_id, operation_id=op_id, result={}) is False
    assert ops.fail_operation(company_id=company_id, operation_id=op_id, error_code="provider_timeout") is False
    assert ops.mark_review(company_id=company_id, operation_id=op_id, error_code="uncertain") is False
    assert ops.resolve_review(company_id=company_id, operation_id=op_id, outcome="completed") is False
    assert ops.get_operation(company_id=company_id, operation_id=op_id) == snapshot

    assert _accept(proposal).outcome == "accepted"
    confirmed = ops.get_operation(company_id=company_id, operation_id=op_id)
    assert ops.reject_proposal(company_id=company_id, contact_id=proposal["contact_id"], operation_id=op_id) is False
    assert ops.complete_operation(company_id=company_id, operation_id=op_id, result={}) is False
    assert ops.fail_operation(company_id=company_id, operation_id=op_id, error_code="x") is False
    assert ops.mark_review(company_id=company_id, operation_id=op_id, error_code="x") is False
    assert ops.resolve_review(company_id=company_id, operation_id=op_id, outcome="failed") is False
    # Outra empresa não enxerga nem transiciona a operação.
    assert ops.claim_for_execution(company_id=other_company, operation_id=op_id) is None
    assert ops.get_operation(company_id=other_company, operation_id=op_id) is None
    assert ops.get_operation(company_id=company_id, operation_id=op_id) == confirmed


def test_failure_and_review_resolution(company_factory) -> None:
    company_id = company_factory("ops")

    failed_id = _executing(company_id)
    assert ops.fail_operation(company_id=company_id, operation_id=failed_id, error_code="calendar_conflict") is True
    failed = ops.get_operation(company_id=company_id, operation_id=failed_id)
    assert failed is not None
    assert (failed["status"], failed["last_error"]) == ("failed", "calendar_conflict")
    assert ops.fail_operation(company_id=company_id, operation_id=failed_id, error_code="again") is False

    review_id = _executing(company_id)
    assert ops.mark_review(company_id=company_id, operation_id=review_id, error_code="ProviderTimeout") is True
    assert _status(company_id, review_id) == "review"
    assert ops.resolve_review(
        company_id=company_id, operation_id=review_id, outcome="completed", result={"verified": True},
    ) is True
    resolved = ops.get_operation(company_id=company_id, operation_id=review_id)
    assert resolved is not None
    assert (resolved["status"], resolved["result"], resolved["last_error"]) == ("completed", {"verified": True}, "ProviderTimeout")
    assert resolved["completed_at"]
    assert ops.resolve_review(company_id=company_id, operation_id=review_id, outcome="failed") is False

    second_review = _executing(company_id)
    assert ops.mark_review(company_id=company_id, operation_id=second_review, error_code="uncertain") is True
    assert ops.resolve_review(
        company_id=company_id, operation_id=second_review, outcome="failed", error_code="not_created",
    ) is True
    second = ops.get_operation(company_id=company_id, operation_id=second_review)
    assert second is not None
    assert (second["status"], second["last_error"]) == ("failed", "not_created")

    with pytest.raises(ValueError):
        ops.mark_review(company_id=company_id, operation_id=second_review, error_code="cliente disse que não vem")
    with pytest.raises(ValueError):
        ops.resolve_review(company_id=company_id, operation_id=second_review, outcome="executing")


def test_reject_closes_only_the_contacts_open_proposal(company_factory) -> None:
    company_id = company_factory("ops")
    proposal = _propose(company_id, _contact())

    assert ops.reject_proposal(company_id=company_id, contact_id=_contact(), operation_id=proposal["id"]) is False
    assert ops.reject_proposal(company_id=company_id, contact_id=proposal["contact_id"], operation_id=proposal["id"]) is True
    assert ops.reject_proposal(company_id=company_id, contact_id=proposal["contact_id"], operation_id=proposal["id"]) is False
    assert _status(company_id, proposal["id"]) == "rejected"


# ── aceite ────────────────────────────────────────────────────────────


def test_accept_replay_never_confirms_twice(company_factory) -> None:
    company_id = company_factory("ops")
    proposal = _propose(company_id, _contact())
    first = _accept(proposal)
    assert first.outcome == "accepted"

    replay = _accept(proposal)
    assert replay.outcome == "replay"
    assert replay.operation == first.operation

    ops.claim_for_execution(company_id=company_id, operation_id=proposal["id"])
    after_claim = _accept(proposal)
    assert after_claim.outcome == "replay"
    assert after_claim.operation is not None
    assert (after_claim.operation["status"], after_claim.operation["attempt_count"]) == ("executing", 1)
    assert _accept(proposal, confirmation_ref="wamid-other").outcome == "not_open"


def test_redelivered_confirmation_never_confirms_a_later_operation(company_factory) -> None:
    company_id = company_factory("ops")
    contact_id = _contact()
    first = _propose(company_id, contact_id)
    assert _accept(first, confirmation_ref="wamid-once").outcome == "accepted"

    # Nova proposta do mesmo contato; a mesma mensagem "sim" é reentregue.
    second = _propose(company_id, contact_id, params={**PARAMS, "service": "Retorno"})
    redelivered = _accept(second, confirmation_ref="wamid-once")

    assert redelivered.outcome == "replay"
    assert redelivered.operation is not None
    assert redelivered.operation["id"] == first["id"]
    assert _status(company_id, second["id"]) == "proposed"

    # Outro contato na mesma empresa não consome nem revela a confirmação alheia.
    other = _propose(company_id, _contact())
    foreign = _accept(other, confirmation_ref="wamid-once")
    assert foreign.outcome == "not_open"
    assert foreign.operation is None
    assert _status(company_id, other["id"]) == "proposed"


def test_database_rejects_a_confirmation_reused_by_another_operation(company_factory) -> None:
    company_id = company_factory("ops")
    first = _propose(company_id, _contact())
    assert _accept(first, confirmation_ref="wamid-unique").outcome == "accepted"
    second = _propose(company_id, _contact())

    with pytest.raises(Exception) as exc_info, db_transaction() as connection:
        connection.cursor().execute(
            "UPDATE booking_operations SET confirmation_ref = ? WHERE id = ? AND company_id = ?",
            ("wamid-unique", second["id"], company_id),
        )
    assert _is_integrity_error(exc_info.value)


def test_accept_after_expiry_marks_the_proposal_expired(company_factory) -> None:
    company_id = company_factory("ops")
    now = datetime.now(UTC)
    proposal = _propose(company_id, _contact(), now=now, ttl_minutes=5)

    # Exatamente no limite já está expirada.
    result = _accept(proposal, now=now + timedelta(minutes=5))
    assert result.outcome == "expired"
    assert result.operation is not None
    assert result.operation["status"] == "expired"
    assert _status(company_id, proposal["id"]) == "expired"
    assert _accept(proposal, now=now).outcome == "expired"


def test_accept_superseded_mismatch_and_not_open(company_factory) -> None:
    company_id = company_factory("ops")
    contact_id = _contact()
    first = _propose(company_id, contact_id)
    second = _propose(company_id, contact_id, params={**PARAMS, "service": "Retorno"})
    assert _accept(first).outcome == "superseded"

    mismatch = _accept(second, params_hash=first["params_hash"])
    assert mismatch.outcome == "mismatch"
    assert ops.get_operation(company_id=company_id, operation_id=second["id"]) == second
    assert _accept(second).outcome == "accepted"

    rejected = _propose(company_id, _contact())
    assert ops.reject_proposal(company_id=company_id, contact_id=rejected["contact_id"], operation_id=rejected["id"])
    assert _accept(rejected).outcome == "not_open"


def test_accept_does_not_reveal_other_tenants_or_contacts(company_factory) -> None:
    company_id = company_factory("ops")
    other_company = company_factory("ops")
    proposal = _propose(company_id, _contact())

    for result in (
        _accept(proposal, company_id=other_company),
        _accept(proposal, contact_id=_contact()),
        _accept(proposal, operation_id=2_000_000_000),
    ):
        assert (result.outcome, result.operation) == ("not_found", None)
    assert ops.get_operation(company_id=company_id, operation_id=proposal["id"]) == proposal


# ── expiração, listagem e integridade ─────────────────────────────────


def test_open_proposals_expire_lazily_and_in_bulk(company_factory) -> None:
    company_a = company_factory("ops")
    company_b = company_factory("ops")
    now = datetime.now(UTC)
    stale_a = _propose(company_a, _contact(), now=now - timedelta(minutes=30), ttl_minutes=10)
    stale_b = _propose(company_b, _contact(), now=now - timedelta(minutes=30), ttl_minutes=10)
    fresh_a = _propose(company_a, _contact(), now=now, ttl_minutes=10)
    lazy = _propose(company_a, _contact(), now=now - timedelta(minutes=20), ttl_minutes=10)

    assert ops.get_open_proposal(company_id=company_a, contact_id=lazy["contact_id"], now=now) is None
    assert _status(company_a, lazy["id"]) == "expired"

    assert ops.expire_open_proposals(now=now, company_id=company_a) == 1
    assert _status(company_a, stale_a["id"]) == "expired"
    assert _status(company_b, stale_b["id"]) == "proposed"
    # O banco é compartilhado entre testes: a varredura global pode achar mais.
    assert ops.expire_open_proposals(now=now) >= 1
    assert _status(company_b, stale_b["id"]) == "expired"
    assert _status(company_a, fresh_a["id"]) == "proposed"


def test_list_operations_is_tenant_scoped_and_validates_filters(company_factory) -> None:
    company_a = company_factory("ops")
    company_b = company_factory("ops")
    contact_id = _contact()
    a1 = _propose(company_a, contact_id)
    a2 = _propose(company_a, contact_id)
    a3 = _propose(company_a, _contact())
    b1 = _propose(company_b, contact_id)

    def ids(**filters: Any) -> list[int]:
        return [operation["id"] for operation in ops.list_operations(**filters)]

    assert ids(company_id=company_a) == [a3["id"], a2["id"], a1["id"]]
    assert ids(company_id=company_a, contact_id=contact_id) == [a2["id"], a1["id"]]
    assert ids(company_id=company_a, contact_id=contact_id, statuses=["superseded"]) == [a1["id"]]
    assert ids(company_id=company_a, statuses=["proposed", "superseded"], limit=2) == [a3["id"], a2["id"]]
    assert ops.list_operations(company_id=company_b, contact_id=contact_id) == [b1]
    assert ops.list_operations(company_id=company_a, statuses=[]) == []

    with pytest.raises(ValueError):
        ops.list_operations(company_id=company_a, statuses=["proposed", "deleted"])
    with pytest.raises(ValueError):
        ops.list_operations(company_id=company_a, statuses=["proposed') OR ('1'='1"])
    for bad_limit in (0, 501, True):
        with pytest.raises(ValueError):
            ops.list_operations(company_id=company_a, limit=bad_limit)


def test_database_rejects_booking_reference_from_another_tenant(company_factory) -> None:
    company_a = company_factory("ops")
    company_b = company_factory("ops")
    contact_id = _contact()
    own_booking = _booking(company_a, contact_id)
    foreign_booking = _booking(company_b, contact_id)
    open_before = _propose(company_a, contact_id)

    with pytest.raises(Exception) as cross_tenant:
        _propose(company_a, contact_id, kind="cancel", booking_id=foreign_booking)
    assert _is_integrity_error(cross_tenant.value)
    with pytest.raises(Exception) as missing:
        _propose(company_a, contact_id, kind="cancel", booking_id=2_000_000_000)
    assert _is_integrity_error(missing.value)
    # A transação voltou inteira: a proposta aberta não foi substituída.
    assert ops.get_open_proposal(company_id=company_a, contact_id=contact_id)["id"] == open_before["id"]

    cancel = _propose(company_a, contact_id, kind="cancel", booking_id=own_booking)
    assert _accept(cancel).outcome == "accepted"
    assert ops.claim_for_execution(company_id=company_a, operation_id=cancel["id"]) is not None
    with pytest.raises(Exception) as on_completion:
        ops.complete_operation(company_id=company_a, operation_id=cancel["id"], result={}, booking_id=foreign_booking)
    assert _is_integrity_error(on_completion.value)
    assert _status(company_a, cancel["id"]) == "executing"


# ── concorrência (conexões separadas) ─────────────────────────────────


def _race_accept(barrier: threading.Barrier, proposal: dict[str, Any], reference: str) -> str:
    barrier.wait(timeout=10)
    return _accept(proposal, confirmation_ref=reference).outcome


def _race_propose(barrier: threading.Barrier, company_id: int, contact_id: str, index: int) -> dict[str, Any]:
    barrier.wait(timeout=10)
    return _propose(company_id, contact_id, params={**PARAMS, "service": f"servico-{index}"})


@pytest.mark.parametrize(
    ("references", "expected"),
    [(("wamid-a", "wamid-b"), ["accepted", "not_open"]), (("wamid-same", "wamid-same"), ["accepted", "replay"])],
    ids=["different-confirmations", "redelivered-confirmation"],
)
def test_concurrent_accepts_confirm_exactly_once(company_factory, references, expected) -> None:
    company_id = company_factory("ops")
    for round_index in range(5):
        proposal = _propose(company_id, _contact())
        barrier = threading.Barrier(2)
        # Referências por rodada: uma confirmação só é consumida uma vez por empresa.
        round_references = [f"{reference}-{round_index}" for reference in references]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(_race_accept, barrier, proposal, reference) for reference in round_references]
            outcomes = sorted(future.result(timeout=30) for future in futures)
        assert outcomes == expected
        assert _status(company_id, proposal["id"]) == "confirmed"


def test_concurrent_proposals_leave_exactly_one_open(company_factory) -> None:
    company_id = company_factory("ops")
    for _round in range(5):
        contact_id = _contact()
        barrier = threading.Barrier(3)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(_race_propose, barrier, company_id, contact_id, index) for index in range(3)]
            created = [future.result(timeout=30) for future in futures]
        operations = ops.list_operations(company_id=company_id, contact_id=contact_id)
        assert {operation["id"] for operation in operations} == {operation["id"] for operation in created}
        assert sorted(operation["status"] for operation in operations) == ["proposed", "superseded", "superseded"]
