from fastapi import FastAPI
from app.api.routes import router
from app.api.admin_routes import admin_router

app = FastAPI(title="Chatbot API", version="1.0")
app.include_router(router)
app.include_router(admin_router)