import contextvars
import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings

# The default is None rather than {}: a mutable ContextVar default is shared by
# every context that never sets it, so an accidental in-place mutation would
# leak request data across requests.
_request_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "request_context",
    default=None,
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "app": settings.app_name,
            "environment": settings.environment,
        }

        request_context = get_request_context()
        if request_context:
            payload.update(request_context)

        event_name = getattr(record, "event", None)
        if event_name:
            payload["event"] = event_name

        event_data = getattr(record, "event_data", None)
        if isinstance(event_data, dict):
            payload.update(
                {
                    key: value
                    for key, value in event_data.items()
                    if value is not None
                }
            )

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    root_logger = logging.getLogger()
    desired_level = getattr(logging, settings.log_level, logging.INFO)
    # Bibliotecas HTTP registram a linha da requisição em DEBUG, com a query
    # string inteira — que, nas chamadas à Meta e ao Google, carrega token e
    # código de autorização. Elas nunca descem de INFO, mesmo com LOG_LEVEL=DEBUG.
    for noisy in ("urllib3", "httpx", "httpcore", "googleapiclient", "google_auth_httplib2"):
        logging.getLogger(noisy).setLevel(max(desired_level, logging.INFO))

    if root_logger.handlers:
        root_logger.setLevel(desired_level)
        for handler in root_logger.handlers:
            handler.setLevel(desired_level)
            if settings.log_json:
                handler.setFormatter(JsonFormatter())
        return

    handler = logging.StreamHandler()
    handler.setLevel(desired_level)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
            )
        )

    root_logger.setLevel(desired_level)
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def set_request_context(**kwargs: Any) -> None:
    sanitized = {
        key: value
        for key, value in kwargs.items()
        if value is not None
    }
    _request_context.set(sanitized)


def get_request_context() -> dict[str, Any]:
    return dict(_request_context.get() or {})


def clear_request_context() -> None:
    _request_context.set(None)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    logger.log(
        level,
        event,
        extra={
            "event": event,
            "event_data": fields,
        },
    )


def mask_identifier(value: str | None, visible_digits: int = 4) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if len(normalized) <= visible_digits:
        return normalized

    return f"***{normalized[-visible_digits:]}"
