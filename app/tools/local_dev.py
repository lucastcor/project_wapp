"""Start the local API and workers together, bound to localhost only."""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from app.core.config import is_production_like, settings
from app.storage.db import close_postgres_pool, init_db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if is_production_like() or settings.outbound_provider != "mock":
        raise SystemExit("local_dev requires development with OUTBOUND_PROVIDER=mock.")
    if settings.classification_provider != "ollama":
        raise SystemExit("Set CLASSIFICATION_PROVIDER=ollama for the local Qwen environment.")
    if not settings.inbound_worker_enabled:
        raise SystemExit("Set INBOUND_WORKER_ENABLED=true.")
    if settings.database_url.startswith("sqlite:///"):
        Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    init_db()
    close_postgres_pool()
    commands = [[sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port)],
                [sys.executable, "-m", "app.tools.inbound_worker"]]
    if settings.automation_engine_enabled and settings.automation_worker_enabled:
        commands.append([sys.executable, "-m", "app.tools.automation_worker"])
    if settings.reminder_worker_enabled:
        commands.append([sys.executable, "-m", "app.tools.reminder_worker"])
    processes: list[subprocess.Popen] = []
    child_environment = dict(os.environ, PLATFORM_PUBLIC_URL=f"http://127.0.0.1:{args.port}",
                             ADMIN_APP_API_BASE_URL=f"http://127.0.0.1:{args.port}")
    def stop(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        for command in commands:
            # Fixed interpreter/modules; only a parsed integer port is variable.
            # Keep each spawn recorded immediately so partial startup is cleaned up.
            processes.append(subprocess.Popen(command, env=child_environment))  # noqa: S603, PERF401
        print(f"WAPP local: http://127.0.0.1:{args.port}/platform", flush=True)
        while True:
            for process in processes:
                if process.poll() is not None:
                    raise SystemExit(f"A local component exited ({process.returncode}); stopping the stack.")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
