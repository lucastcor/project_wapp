from types import SimpleNamespace

from app.tools import reminder_worker


def test_validate_only_closes_database_pool(monkeypatch) -> None:
    parser = SimpleNamespace(
        parse_args=lambda: SimpleNamespace(validate_only=True, run_once=False)
    )
    pool_close_calls: list[bool] = []

    monkeypatch.setattr(reminder_worker, "_build_arg_parser", lambda: parser)
    monkeypatch.setattr(reminder_worker, "init_db", lambda: None)
    monkeypatch.setattr(
        reminder_worker,
        "validate_runtime_environment",
        lambda _component: None,
    )
    monkeypatch.setattr(
        reminder_worker,
        "close_postgres_pool",
        lambda: pool_close_calls.append(True),
    )

    reminder_worker.main()

    assert pool_close_calls == [True]
