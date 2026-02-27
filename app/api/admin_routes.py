from fastapi import APIRouter

admin_router = APIRouter(prefix="/admin", tags=["admin"])

@admin_router.get("/ping")
def ping():
    return {"admin": "pong"}