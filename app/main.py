from fastapi import FastAPI

from app.api.admin_routes import admin_router
from app.api.routes import router
from app.core.config import settings
from app.storage.db import init_db

init_db()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="API base para orquestração simples de chatbot.",
)

app.include_router(router)
app.include_router(admin_router)


@app.get("/health", tags=["health"])
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}