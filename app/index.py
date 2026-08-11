"""Vercel FastAPI entrypoint.

Migrations are intentionally not run by serverless cold starts. Configure
``RUN_MIGRATIONS_ON_STARTUP=false`` and execute
``python -m app.tools.run_migrations`` once in the release workflow.
"""

from app.main import app

__all__ = ["app"]
