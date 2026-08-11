import json
import re
from dataclasses import dataclass
from typing import Any

_SENSITIVE_KEY_NAMES = {
    "access_token",
    "api_key",
    "api_token",
    "app_secret",
    "authorization",
    "client_secret",
    "credentials",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}
_JSON_IDENTIFIER_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class SanitizationResult:
    value: Any
    removed_fields: int = 0


def _normalize_sensitive_key(key: object) -> str:
    normalized = _JSON_IDENTIFIER_PATTERN.sub("_", str(key).strip().lower()).strip("_")
    return normalized


def is_sensitive_key(key: object) -> bool:
    """Whether a JSON key names a credential rather than a public identifier."""
    normalized = _normalize_sensitive_key(key)
    if normalized in _SENSITIVE_KEY_NAMES:
        return True
    return any(normalized.endswith(f"_{name}") for name in _SENSITIVE_KEY_NAMES)


def sanitize_sensitive_value(value: Any) -> SanitizationResult:
    """Remove credential-bearing keys recursively without inspecting user text."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        removed_fields = 0
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if is_sensitive_key(key):
                removed_fields += 1
                continue
            nested = sanitize_sensitive_value(raw_value)
            sanitized[key] = nested.value
            removed_fields += nested.removed_fields
        return SanitizationResult(sanitized, removed_fields)

    if isinstance(value, (list, tuple)):
        sanitized_items: list[Any] = []
        removed_fields = 0
        for item in value:
            nested = sanitize_sensitive_value(item)
            sanitized_items.append(nested.value)
            removed_fields += nested.removed_fields
        return SanitizationResult(sanitized_items, removed_fields)

    return SanitizationResult(value, 0)


def sanitize_runtime_row(
    table_name: str, row: dict[str, Any]
) -> tuple[dict[str, Any], int, bool]:
    """Sanitize JSON columns and fail closed for malformed credential config.

    Calendar integrations whose persisted credentials were removed are disabled
    and marked for reconfiguration. Other JSON columns are sanitized when they
    contain valid JSON; malformed non-credential JSON is preserved because it may
    be legacy application data rather than configuration.
    """
    sanitized_row = dict(row)
    total_removed = 0
    integration_disabled = False

    if table_name == "outbound_messages":
        if sanitized_row.get("status") == "processing":
            sanitized_row["status"] = "retry"
        sanitized_row["claim_token"] = None
        sanitized_row["claimed_at"] = None
    elif table_name == "customer_reminders":
        if sanitized_row.get("status") == "processing":
            sanitized_row["status"] = "pending"
        sanitized_row["claim_token"] = None
        sanitized_row["claimed_at"] = None
    elif (
        table_name == "billing_webhook_events"
        and sanitized_row.get("status") == "processing"
    ):
        sanitized_row["status"] = "failed"
        sanitized_row["last_error"] = "Restored from an interrupted processing claim."

    for column_name, raw_value in list(sanitized_row.items()):
        if not column_name.endswith("_json") or raw_value in (None, ""):
            continue
        if not isinstance(raw_value, str):
            nested = sanitize_sensitive_value(raw_value)
            sanitized_row[column_name] = nested.value
            total_removed += nested.removed_fields
            continue

        try:
            decoded = json.loads(raw_value)
        except (TypeError, ValueError) as exc:
            if table_name == "calendar_integrations" and column_name == "config_json":
                raise ValueError(
                    "calendar_integrations.config_json contains invalid JSON; "
                    "refusing to export or migrate potentially sensitive data."
                ) from exc
            continue

        nested = sanitize_sensitive_value(decoded)
        if nested.removed_fields:
            if table_name == "calendar_integrations" and column_name == "config_json":
                config = (
                    nested.value
                    if isinstance(nested.value, dict)
                    else {"configuration": nested.value}
                )
                config["_credentials_redacted"] = True
                nested = SanitizationResult(config, nested.removed_fields)
                sanitized_row["active"] = 0
                integration_disabled = True
            sanitized_row[column_name] = json.dumps(
                nested.value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            total_removed += nested.removed_fields

    return sanitized_row, total_removed, integration_disabled
