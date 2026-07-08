"""API externa `/api/v1/*` — para que una plataforma de administración controle y
monitoree ESTE bot.

Auth: header `Authorization: Bearer <CONTROL_API_KEY>` en TODOS los endpoints.
Si `control_api_key` no está configurada, la API responde 503 (deshabilitada).

Endpoints:
  GET  /api/v1/health   → vivo + estado del bot + conexión de WhatsApp
  GET  /api/v1/estado   → estado del bot (activo / pausado + detalle)
  POST /api/v1/estado   → activar/desactivar el bot  (body: {"activo": bool, "razon"?: str})
  GET  /api/v1/stats    → métricas (reservas del día, mensajes, chats, pendientes)
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AlertaFabio, Conversacion
from app.db.session import get_session
from app.eventos import extraer_eventos, resumen_eventos
from app.integrations import cantina_api
from app.logging_setup import log
from app.webhooks_salientes import emitir_evento
from app.whapi import client as whapi

router = APIRouter(prefix="/api/v1", tags=["api-externa"])
TZ_COLOMBIA = ZoneInfo("America/Bogota")


# ── Auth ────────────────────────────────────────────────────────────────────
def _verificar_token(authorization: str | None) -> None:
    """Valida el Bearer contra control_api_key (comparación tiempo-constante)."""
    s = get_settings()
    esperado = (s.control_api_key or "").strip()
    if not esperado:
        raise HTTPException(503, "API externa no configurada (falta CONTROL_API_KEY)")
    token = ""
    if authorization:
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else authorization.strip()
    if not (token and hmac.compare_digest(token, esperado)):
        raise HTTPException(401, "token inválido")


async def auth(authorization: str | None = Header(default=None)) -> None:
    _verificar_token(authorization)


# ── Helpers ─────────────────────────────────────────────────────────────────
async def _leer_estado(session: AsyncSession) -> dict:
    try:
        row = (await session.execute(sa_text(
            "SELECT activo, pausado_por, pausado_en, razon FROM bot_estado WHERE id=1"
        ))).first()
    except Exception as e:
        log.warning("api_externa.estado.fail", error=str(e)[:160])
        return {"activo": True, "pausado_por": None, "pausado_en": None, "razon": None}
    if not row:
        return {"activo": True, "pausado_por": None, "pausado_en": None, "razon": None}
    return {
        "activo": bool(row[0]),
        "pausado_por": row[1],
        "pausado_en": row[2].isoformat() if row[2] else None,
        "razon": row[3],
    }


async def _aplicar_estado(session: AsyncSession, activo: bool, razon: str | None, quien: str) -> None:
    if activo:
        await session.execute(sa_text(
            "UPDATE bot_estado SET activo=true, pausado_por=null, pausado_en=null, "
            "razon=null, actualizado_en=now() WHERE id=1"
        ))
    else:
        await session.execute(sa_text(
            "UPDATE bot_estado SET activo=false, pausado_por=:por, pausado_en=now(), "
            "razon=:razon, actualizado_en=now() WHERE id=1"
        ), {"por": quien, "razon": razon or "Pausado vía API externa"})
    await session.commit()


# ── Endpoints ───────────────────────────────────────────────────────────────
@router.get("/health")
async def health(_: None = Depends(auth), session: AsyncSession = Depends(get_session)):
    """Vivo + estado del bot + conexión de WhatsApp. Para monitoreo de la plataforma."""
    s = get_settings()
    estado = await _leer_estado(session)
    wa = await whapi.estado_cuenta()
    return {
        "ok": True,
        "bot_id": s.bot_id,
        "vivo": True,
        "bot_activo": estado["activo"],
        "whatsapp": wa,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/estado")
async def get_estado(_: None = Depends(auth), session: AsyncSession = Depends(get_session)):
    """Estado actual del bot (activo / pausado + detalle)."""
    s = get_settings()
    estado = await _leer_estado(session)
    return {"ok": True, "bot_id": s.bot_id, **estado}


class CambioEstado(BaseModel):
    activo: bool
    razon: str | None = None


@router.post("/estado")
async def set_estado(
    payload: CambioEstado,
    _: None = Depends(auth),
    session: AsyncSession = Depends(get_session),
):
    """Activa o desactiva el bot. Body: `{"activo": true|false, "razon"?: "..."}`.

    Desactivar = silencio total (igual que el botón del dashboard). Emite el
    webhook `bot.estado_cambiado`.
    """
    s = get_settings()
    await _aplicar_estado(session, payload.activo, payload.razon, quien="api_externa")
    log.warning("api_externa.set_estado", activo=payload.activo, razon=payload.razon)
    await emitir_evento("bot.estado_cambiado", {
        "activo": payload.activo,
        "razon": payload.razon,
        "origen": "api_externa",
    })
    estado = await _leer_estado(session)
    return {"ok": True, "bot_id": s.bot_id, **estado}


@router.get("/stats")
async def stats(_: None = Depends(auth), session: AsyncSession = Depends(get_session)):
    """Métricas operativas: reservas del día, mensajes, chats activos, pendientes."""
    s = get_settings()
    ahora = datetime.now(timezone.utc)
    hoy_local = ahora.astimezone(TZ_COLOMBIA).replace(hour=0, minute=0, second=0, microsecond=0)
    hoy = hoy_local.astimezone(timezone.utc)
    fecha_hoy = hoy_local.date().isoformat()
    hace_7d = ahora - timedelta(days=7)

    inbound = (await session.execute(
        select(func.count()).select_from(Conversacion)
        .where(and_(Conversacion.timestamp >= hoy, Conversacion.direccion == "inbound"))
    )).scalar_one()
    outbound = (await session.execute(
        select(func.count()).select_from(Conversacion)
        .where(and_(Conversacion.timestamp >= hoy, Conversacion.direccion.in_(["outbound", "humano"])))
    )).scalar_one()
    chats_activos = (await session.execute(
        select(func.count(func.distinct(Conversacion.cliente_id)))
        .where(and_(Conversacion.timestamp >= hoy, Conversacion.direccion == "inbound"))
    )).scalar_one()
    clientes_7d = (await session.execute(
        select(func.count(func.distinct(Conversacion.cliente_id)))
        .where(Conversacion.timestamp >= hace_7d)
    )).scalar_one()
    pendientes = (await session.execute(
        select(func.count()).select_from(AlertaFabio).where(AlertaFabio.resuelto.is_(False))
    )).scalar_one()

    # Reservas del día (backend de mesas, best-effort)
    reservas: dict = {"ok": False, "error": "backend de mesas no disponible"}
    try:
        resumen = await cantina_api.resumen_dia(fecha_hoy)
        if isinstance(resumen, dict) and resumen.get("ok"):
            p = resumen.get("data") if isinstance(resumen.get("data"), dict) else resumen
            eventos = extraer_eventos(p)
            reservas_list = [r for r in (p.get("reservas") or []) if isinstance(r, dict)]
            salas_reservadas = [
                s for s in (p.get("salas") or [])
                if isinstance(s, dict) and isinstance(s.get("reserva"), dict)
            ]
            reservas = {
                "ok": True,
                "fecha": fecha_hoy,
                "total_reservas": (
                    p.get("total_reservas")
                    if p.get("total_reservas") is not None
                    else len(reservas_list) + len(salas_reservadas)
                ),
                "mesas_ocupadas": p.get("mesas_ocupadas"),
                "mesas_totales": p.get("mesas_totales"),
                "total_personas": p.get("total_personas"),
                "covers_pendientes": p.get("covers_pendientes"),
                "evento": resumen_eventos(eventos),
            }
    except Exception as e:
        reservas = {"ok": False, "error": str(e)[:160]}

    estado = await _leer_estado(session)
    return {
        "ok": True,
        "bot_id": s.bot_id,
        "ts": ahora.isoformat(),
        "bot_activo": estado["activo"],
        "reservas_hoy": reservas,
        "mensajes_hoy": {
            "recibidos": int(inbound),
            "enviados": int(outbound),
            "total": int(inbound) + int(outbound),
        },
        "chats_activos_hoy": int(chats_activos),
        "clientes_activos_7d": int(clientes_7d),
        "pendientes": int(pendientes),
    }
