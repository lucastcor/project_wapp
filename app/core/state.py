from typing import Any

from app.storage.repository import RuntimeRepository

_repository = RuntimeRepository()


def get_runtime_state() -> dict[str, Any]:
    return _repository.get_state()


def update_runtime_state(message: str) -> None:
    _repository.update_message(message)