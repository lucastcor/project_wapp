"""Conexão da agenda Google da empresa por OAuth (início do fluxo e gestão).

O callback do Google é uma navegação de topo do navegador e vive em
`platform_routes.py` (cookie de sessão, sem cabeçalho CSRF). Este router
concentra o que o painel chama por `fetch`: iniciar a autorização, listar
calendários, escolher o calendário e desconectar.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.admin_routes import require_admin_access

google_calendar_router = APIRouter(
    prefix="/admin/calendar/google",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)
