"""Fencing do job de entrada antes de efeitos transacionais (P06, F03).

O worker processa uma mensagem sob uma lease (`inbound_jobs.claim_token` com
validade). Uma geração lenta pode passar da validade; a recuperação então
entrega a mensagem a outro worker. Sem fencing, o dono antigo ainda executaria
o cancelamento ou a reserva depois de ter perdido a posse.

Antes de qualquer efeito de agenda, o executor renova a própria lease por
compare-and-set: se não detém mais o job, nenhum efeito acontece. A renovação
também protege a execução em andamento contra a recuperação do job.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from app.storage import inbound_repository


class LeaseLostError(RuntimeError):
    """O worker perdeu a posse do job de entrada; nenhum efeito pode ocorrer."""


@dataclass(frozen=True)
class JobLease:
    job_id: int
    company_id: int
    claim_token: str


_current_lease: ContextVar[JobLease | None] = ContextVar("inbound_job_lease", default=None)


@contextmanager
def bind_job_lease(job: dict[str, Any]) -> Iterator[None]:
    token = _current_lease.set(
        JobLease(job_id=int(job["id"]), company_id=int(job["company_id"]), claim_token=str(job["claim_token"]))
    )
    try:
        yield
    finally:
        _current_lease.reset(token)


def assert_lease_before_effect() -> None:
    """Renova a lease do job corrente ou recusa o efeito.

    Fora do worker durável (API síncrona, simulador, testes) não há lease a
    proteger e a checagem não se aplica.
    """
    lease = _current_lease.get()
    if lease is None:
        return
    if not inbound_repository.renew_lease(
        job_id=lease.job_id,
        company_id=lease.company_id,
        claim_token=lease.claim_token,
    ):
        raise LeaseLostError("Inbound job lease was lost before a booking effect; another worker owns this message.")
