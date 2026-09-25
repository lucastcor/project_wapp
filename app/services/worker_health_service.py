"""Persisted liveness is written only by executing workers, never by probes."""
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.storage.sql_support import execute_rowcount, fetch_one


def _iso(moment: datetime) -> str:
    # Largura fixa: `expires_at` é comparado como texto em SQL.
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def record_heartbeat(component: str, instance_id: str) -> None:
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=max(5, settings.worker_heartbeat_ttl_seconds))
    execute_rowcount("""
        INSERT INTO worker_heartbeats(component, instance_id, updated_at, expires_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(component, instance_id) DO UPDATE SET updated_at = excluded.updated_at, expires_at = excluded.expires_at
    """, (component, instance_id, _iso(now), _iso(expires)))


def worker_is_alive(component: str) -> bool:
    try:
        row = fetch_one("SELECT 1 AS alive FROM worker_heartbeats WHERE component = ? AND expires_at > ? LIMIT 1",
                        (component, _iso(datetime.now(UTC))))
        return row is not None
    except Exception:
        return False


def remove_heartbeat(component: str, instance_id: str) -> None:
    execute_rowcount("DELETE FROM worker_heartbeats WHERE component = ? AND instance_id = ?", (component, instance_id))
