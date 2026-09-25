"""Local capacity test for the durable inbound pipeline.

The runner sends synthetic normalized WhatsApp events, measures HTTP ACKs and
then reads only the jobs created by this run from the configured local database.
It never calls Meta and should be used with ``OUTBOUND_PROVIDER=mock``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import requests

from app.core.config import is_missing_or_placeholder, settings
from app.storage.db import close_postgres_pool, db_session

TERMINAL_STATUSES = frozenset({"completed", "review"})
SCENARIO_MESSAGES: dict[str, tuple[str, ...]] = {
    "greeting": ("Oi, tudo bem?",),
    "common": ("Quais são os horários de atendimento?",),
    "mixed": (
        "Oi, tudo bem?",
        "Quais serviços estão disponíveis?",
        "Como funciona o atendimento?",
        "Quais são os horários de atendimento?",
    ),
}


@dataclass(frozen=True)
class AckResult:
    provider_message_id: str
    ok: bool
    latency_seconds: float
    status_code: int | None
    error: str | None = None


def percentile(values: Sequence[float], percent: float) -> float | None:
    """Linear percentile without an optional statistics dependency."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def latency_summary(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "samples": len(values),
        "p50_ms": _milliseconds(percentile(values, 50)),
        "p95_ms": _milliseconds(percentile(values, 95)),
        "p99_ms": _milliseconds(percentile(values, 99)),
        "max_ms": _milliseconds(max(values) if values else None),
    }


def _milliseconds(value: float | None) -> float | None:
    return round(value * 1000, 2) if value is not None else None


def _parse_datetime(value: Any) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _fetch_jobs(company_id: int, provider_message_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = {}
    # SQLite defaults to 999 bound parameters. Leave space for company_id.
    for start in range(0, len(provider_message_ids), 400):
        chunk = list(provider_message_ids[start : start + 400])
        if not chunk:
            continue
        placeholders = ", ".join("?" for _ in chunk)
        with db_session() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                SELECT provider_message_id, status, last_error, created_at, updated_at
                FROM inbound_jobs
                WHERE company_id = ?
                  AND provider_message_id IN ({placeholders})
                """,  # nosec B608 -- placeholders only; values remain bound.
                (company_id, *chunk),
            )
            rows = cursor.fetchall()
        for row in rows:
            record = dict(row)
            jobs[str(record["provider_message_id"])] = record
    return jobs


def _preflight(base_url: str, company_id: int, request_timeout_seconds: float) -> None:
    try:
        health = requests.get(
            f"{base_url.rstrip('/')}/health",
            timeout=request_timeout_seconds,
        )
        readiness = requests.get(
            f"{base_url.rstrip('/')}/health/ready",
            timeout=request_timeout_seconds,
        )
    except requests.RequestException as exc:
        raise RuntimeError("The local API is not reachable.") from exc
    if health.status_code != 200:
        raise RuntimeError(f"The local API health check returned HTTP {health.status_code}.")
    if readiness.status_code != 200:
        raise RuntimeError(
            f"The local runtime is not ready (HTTP {readiness.status_code}); check API and worker logs."
        )
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT active FROM companies WHERE id = ?", (company_id,))
        company = cursor.fetchone()
    if company is None or not bool(company["active"]):
        raise RuntimeError("The selected company does not exist or is inactive in the configured database.")


def wait_for_jobs(
    company_id: int,
    provider_message_ids: Sequence[str],
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> tuple[dict[str, dict[str, Any]], float]:
    started = time.perf_counter()
    jobs: dict[str, dict[str, Any]] = {}
    while time.perf_counter() - started < timeout_seconds:
        jobs = _fetch_jobs(company_id, provider_message_ids)
        if len(jobs) == len(provider_message_ids) and all(
            str(job["status"]) in TERMINAL_STATUSES for job in jobs.values()
        ):
            break
        time.sleep(poll_seconds)
    return jobs, time.perf_counter() - started


def _send_one(
    session: requests.Session,
    *,
    endpoint: str,
    company_id: int,
    ingress_secret: str,
    contact_id: str,
    provider_message_id: str,
    text: str,
    request_timeout_seconds: float,
) -> AckResult:
    payload = {
        "event": "message.received",
        "from_number": contact_id,
        "text": text,
        "provider_message_id": provider_message_id,
        "received_at": datetime.now(UTC).isoformat(),
    }
    started = time.perf_counter()
    try:
        response = session.post(
            f"{endpoint}?{urlencode({'company_id': company_id})}",
            headers={"X-Internal-Ingress-Secret": ingress_secret},
            json=payload,
            timeout=request_timeout_seconds,
        )
        latency = time.perf_counter() - started
        if response.status_code != 200:
            return AckResult(
                provider_message_id,
                False,
                latency,
                response.status_code,
                f"http_{response.status_code}",
            )
        body = response.json()
        accepted = bool(body.get("accepted")) and int(body.get("queued_count") or 0) == 1
        return AckResult(
            provider_message_id,
            accepted,
            latency,
            response.status_code,
            None if accepted else "not_queued_or_duplicate",
        )
    except (requests.RequestException, ValueError) as exc:
        return AckResult(
            provider_message_id,
            False,
            time.perf_counter() - started,
            None,
            type(exc).__name__,
        )


def _send_conversation(
    *,
    base_url: str,
    company_id: int,
    ingress_secret: str,
    run_id: str,
    conversation_index: int,
    messages_per_conversation: int,
    scenario: str,
    request_timeout_seconds: float,
    think_time_seconds: float,
) -> list[AckResult]:
    contact_id = f"load-{run_id}-c{conversation_index:03d}"
    messages = SCENARIO_MESSAGES[scenario]
    results: list[AckResult] = []
    with requests.Session() as session:
        for message_index in range(messages_per_conversation):
            provider_message_id = (
                f"load-{run_id}-c{conversation_index:03d}-m{message_index + 1:03d}"
            )
            results.append(
                _send_one(
                    session,
                    endpoint=f"{base_url.rstrip('/')}/webhooks/whatsapp",
                    company_id=company_id,
                    ingress_secret=ingress_secret,
                    contact_id=contact_id,
                    provider_message_id=provider_message_id,
                    text=messages[message_index % len(messages)],
                    request_timeout_seconds=request_timeout_seconds,
                )
            )
            if think_time_seconds:
                time.sleep(think_time_seconds)
    return results


def run_capacity_test(args: argparse.Namespace) -> dict[str, Any]:
    ingress_secret = str(settings.internal_ingress_secret or "").strip()
    if is_missing_or_placeholder(ingress_secret):
        raise RuntimeError("Set a non-placeholder INTERNAL_INGRESS_SECRET for the local test.")
    if not settings.enable_normalized_whatsapp_ingress:
        raise RuntimeError("Set ENABLE_NORMALIZED_WHATSAPP_INGRESS=true for the local test.")
    if settings.outbound_provider != "mock":
        raise RuntimeError(
            "Capacity test refuses a real outbound provider. Set OUTBOUND_PROVIDER=mock."
        )

    _preflight(args.base_url, args.company_id, args.request_timeout_seconds)

    run_id = f"{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    started_at = datetime.now(UTC).isoformat()
    ack_started = time.perf_counter()
    ack_results: list[AckResult] = []
    with ThreadPoolExecutor(max_workers=args.conversations) as executor:
        futures = [
            executor.submit(
                _send_conversation,
                base_url=args.base_url,
                company_id=args.company_id,
                ingress_secret=ingress_secret,
                run_id=run_id,
                conversation_index=index + 1,
                messages_per_conversation=args.messages_per_conversation,
                scenario=args.scenario,
                request_timeout_seconds=args.request_timeout_seconds,
                think_time_seconds=args.think_time_ms / 1000,
            )
            for index in range(args.conversations)
        ]
        for future in as_completed(futures):
            ack_results.extend(future.result())
    ack_duration = time.perf_counter() - ack_started

    accepted_ids = [result.provider_message_id for result in ack_results if result.ok]
    jobs, drain_duration = wait_for_jobs(
        args.company_id,
        accepted_ids,
        timeout_seconds=args.drain_timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    status_counts = Counter(str(job["status"]) for job in jobs.values())
    terminal_latencies = [
        (_parse_datetime(job["updated_at"]) - _parse_datetime(job["created_at"])).total_seconds()
        for job in jobs.values()
        if str(job["status"]) in TERMINAL_STATUSES
    ]
    missing = len(accepted_ids) - len(jobs)
    unfinished = sum(status not in TERMINAL_STATUSES for status in status_counts.elements())
    ack_failures = [result for result in ack_results if not result.ok]
    review_count = status_counts.get("review", 0)
    completed_count = status_counts.get("completed", 0)
    ack_p95 = percentile([result.latency_seconds for result in ack_results], 95)
    e2e_p95 = percentile(terminal_latencies, 95)
    passed = bool(
        not ack_failures
        and not missing
        and not unfinished
        and not review_count
        and completed_count == len(accepted_ids)
        and ack_p95 is not None
        and ack_p95 <= args.ack_p95_target_seconds
        and e2e_p95 is not None
        and e2e_p95 <= args.e2e_p95_target_seconds
    )

    return {
        "run_id": run_id,
        "started_at": started_at,
        "configuration": {
            "base_url": args.base_url,
            "company_id": args.company_id,
            "scenario": args.scenario,
            "conversations": args.conversations,
            "messages_per_conversation": args.messages_per_conversation,
            "total_messages": len(ack_results),
            "database_backend": "postgres" if settings.database_url.startswith("postgres") else "sqlite",
            "classification_provider": settings.classification_provider,
            "outbound_provider": settings.outbound_provider,
        },
        "ack": {
            **latency_summary([result.latency_seconds for result in ack_results]),
            "accepted": len(accepted_ids),
            "failed": len(ack_failures),
            "requests_per_second": round(len(ack_results) / ack_duration, 2) if ack_duration else None,
            "failure_kinds": dict(Counter(result.error for result in ack_failures)),
        },
        "end_to_end": {
            **latency_summary(terminal_latencies),
            "completed": completed_count,
            "review": review_count,
            "unfinished": unfinished,
            "missing": missing,
            "status_counts": dict(sorted(status_counts.items())),
            "drain_seconds": round(drain_duration, 3),
            "completed_per_minute": (
                round(completed_count / drain_duration * 60, 2) if drain_duration else None
            ),
        },
        "targets": {
            "ack_p95_ms": round(args.ack_p95_target_seconds * 1000, 2),
            "end_to_end_p95_ms": round(args.e2e_p95_target_seconds * 1000, 2),
        },
        "passed": passed,
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=_positive_int, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--conversations", type=_positive_int, default=1)
    parser.add_argument("--messages-per-conversation", type=_positive_int, default=10)
    parser.add_argument("--scenario", choices=sorted(SCENARIO_MESSAGES), default="common")
    parser.add_argument("--think-time-ms", type=float, default=0)
    parser.add_argument("--request-timeout-seconds", type=float, default=10)
    parser.add_argument("--drain-timeout-seconds", type=float, default=180)
    parser.add_argument("--poll-seconds", type=float, default=0.2)
    parser.add_argument("--ack-p95-target-seconds", type=float, default=1)
    parser.add_argument("--e2e-p95-target-seconds", type=float, default=10)
    parser.add_argument("--output", help="Write the JSON result to this path.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.conversations * args.messages_per_conversation > 500:
        raise SystemExit("Limit one run to at most 500 synthetic messages.")
    try:
        result = run_capacity_test(args)
    except (RuntimeError, requests.RequestException) as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        close_postgres_pool()
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as output_file:
            output_file.write(rendered + "\n")
    if not result["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
