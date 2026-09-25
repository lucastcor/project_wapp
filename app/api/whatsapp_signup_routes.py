"""Conexão do número WhatsApp da empresa pelo Embedded Signup da Meta.

Router separado do `admin_routes` para que a feature evolua sem tocar no
arquivo compartilhado. Herda a autenticação administrativa e o escopo de
empresa por prefixo `/admin/whatsapp` (política tenant), exceto `/config`,
declarada como catálogo sem empresa em `company_scope.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.admin_routes import require_admin_access

whatsapp_signup_router = APIRouter(
    prefix="/admin/whatsapp/embedded-signup",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)
