from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class WebhookIn(BaseModel):
    text: str
def reply_text(text:str) -> str:
    t= text.strip().lower()
    if not t:
        return "Me diga sua dúvida."
    return f"Você disse: {text}"
@router.get("/health")
def health() -> dict:
    return {"status": "ok"}
@router.get("/echo/{text}")
def echo(text: str) -> dict:
    return {"You send:": text}
@router.post("/webhook")
def webhook(payload: WebhookIn) -> dict:
    ans= reply_text(payload.text)
    return { "ok": True, "received": payload.text, "answer": ans }