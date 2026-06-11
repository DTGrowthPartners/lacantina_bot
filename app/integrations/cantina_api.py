"""Cliente HTTP del backend de mesas de La Cantina (cantina-mesas-backend).

Base: https://cantina-api.dtgrowthpartners.com
Auth: header `x-api-key: <CANTINA_API_KEY>` en TODAS las llamadas (excepto /api/health).

Endpoints documentados en `docs/API_BACKEND.md` o en
`/Users/.../botplatform/cantina-bot-info.md` (fuente).

Devuelve siempre `{ok: bool, data?: ..., error?: str, status?: int}` y nunca
levanta hacia el caller — el handler de tools convierte eso a un mensaje útil
para Claude.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.logging_setup import log

settings = get_settings()

_TIMEOUT = 20


def _headers() -> dict[str, str]:
    return {
        "x-api-key": settings.cantina_api_key,
        "Content-Type": "application/json",
    }


def _configurado() -> bool:
    return bool(settings.cantina_api_key and settings.cantina_api_base_url)


async def _request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
) -> dict[str, Any]:
    if not _configurado():
        return {"ok": False, "error": "Cantina API no configurada (falta CANTINA_API_KEY)."}
    url = f"{settings.cantina_api_base_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.request(method, url, headers=_headers(), params=params, json=json)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        if r.status_code >= 400:
            log.warning("cantina_api.http_error",
                        method=method, path=path, status=r.status_code,
                        body=str(data)[:300])
            return {
                "ok": False,
                "status": r.status_code,
                "error": (data.get("error") if isinstance(data, dict) else None)
                         or f"Cantina API {method} {path} → {r.status_code}",
            }
        # La API ya devuelve {"ok": true, ...} en éxito — preservar.
        if isinstance(data, dict) and "ok" in data:
            return data
        return {"ok": True, "data": data}
    except Exception as e:
        log.exception("cantina_api.request.fail", method=method, path=path, error=str(e))
        return {"ok": False, "error": str(e)[:200]}


# ── Salud ───────────────────────────────────────────────────────────────────

async def healthcheck() -> dict[str, Any]:
    """GET /api/health (sin auth). Para verificar que el backend está vivo."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{settings.cantina_api_base_url.rstrip('/')}/api/health")
        return {"ok": r.status_code == 200, "status": r.status_code, "data": r.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Disponibilidad (la query central del flow cliente) ─────────────────────

async def disponibilidad(fecha: str, personas: int | None = None) -> dict[str, Any]:
    """GET /api/disponibilidad?fecha=YYYY-MM-DD&personas=N

    Devuelve TODO el contexto del día:
      - evento (None o {nombre, artista, tiene_cover, valor_cover, link_pago})
      - plano_url (foto del diagrama estático del salón)
      - ocupacion: {ocupadas:[...], libres:[...]}  ← mapa REAL sin filtrar
      - mesas_disponibles: [...] + total_disponibles  ← FILTRADO por capacidad + VIP
      - requiere_combinar (bool) + combo_sugerido + combos
      - nota_vip (str | None)  ← solo en día de evento si la VIP queda restringida
      - salas_privadas: {disponibles:[...], nota}
    """
    params: dict[str, Any] = {"fecha": fecha}
    if personas is not None:
        params["personas"] = personas
    return await _request("GET", "/api/disponibilidad", params=params)


async def combos(fecha: str, personas: int) -> dict[str, Any]:
    """GET /api/combos?fecha=YYYY-MM-DD&personas=N — sugerencias de mesas vecinas."""
    return await _request("GET", "/api/combos", params={"fecha": fecha, "personas": personas})


# ── Reservas de mesa ───────────────────────────────────────────────────────

async def crear_reserva(payload: dict) -> dict[str, Any]:
    """POST /api/reservas — reserva una mesa simple.

    payload: {fecha, mesa_id, nombre_cliente, telefono, num_personas, notas?}
    Auto-aplica cover si la fecha tiene evento.
    409 si la mesa ya está reservada esa fecha.
    """
    return await _request("POST", "/api/reservas", json=payload)


async def crear_reserva_grupo(payload: dict) -> dict[str, Any]:
    """POST /api/reservas/grupo — grupo grande sobre varias mesas vecinas.

    payload: {fecha, mesa_numeros:[..], nombre_cliente, telefono, num_personas, notas?}
    Valida contigüidad. Cancelar una mesa cancela todo el grupo.
    """
    return await _request("POST", "/api/reservas/grupo", json=payload)


async def listar_reservas(fecha: str, incluir_canceladas: bool = False) -> dict[str, Any]:
    params: dict[str, Any] = {"fecha": fecha}
    if incluir_canceladas:
        params["incluir_canceladas"] = "true"
    return await _request("GET", "/api/reservas", params=params)


async def detalle_reserva(reserva_id: int | str) -> dict[str, Any]:
    return await _request("GET", f"/api/reservas/{reserva_id}")


async def actualizar_reserva(reserva_id: int | str, cambios: dict) -> dict[str, Any]:
    """PATCH /api/reservas/:id

    `cambios` puede incluir: cover_estado
    (no_aplica/pendiente/anticipado/en_entrada/invitado),
    estado (confirmada/cancelada), notas, num_personas, nombre_cliente,
    telefono, monto_cover. Nota: cambiar num_personas NO reasigna/valida mesa.
    """
    return await _request("PATCH", f"/api/reservas/{reserva_id}", json=cambios)


async def cancelar_reserva(reserva_id: int | str) -> dict[str, Any]:
    """DELETE /api/reservas/:id — soft-delete + libera la mesa."""
    return await _request("DELETE", f"/api/reservas/{reserva_id}")


async def cancelar_grupo(grupo_id: int | str) -> dict[str, Any]:
    """DELETE /api/reservas/grupo/:grupoId — libera todas las mesas del grupo."""
    return await _request("DELETE", f"/api/reservas/grupo/{grupo_id}")


# ── Salas privadas VIP ─────────────────────────────────────────────────────

async def listar_salas() -> dict[str, Any]:
    return await _request("GET", "/api/salas")


async def disponibilidad_salas(fecha: str) -> dict[str, Any]:
    return await _request("GET", "/api/salas/disponibilidad", params={"fecha": fecha})


async def crear_reserva_sala(payload: dict) -> dict[str, Any]:
    """POST /api/reservas-salas

    payload: {fecha, sala_id, nombre_cliente, telefono, num_personas, notas?}
    Mínimo de consumo $1.000.000 (gastable, no es cobro extra). Sin cover.
    """
    return await _request("POST", "/api/reservas-salas", json=payload)


async def listar_reservas_salas(fecha: str) -> dict[str, Any]:
    return await _request("GET", "/api/reservas-salas", params={"fecha": fecha})


async def actualizar_reserva_sala(reserva_id: int | str, cambios: dict) -> dict[str, Any]:
    return await _request("PATCH", f"/api/reservas-salas/{reserva_id}", json=cambios)


async def cancelar_reserva_sala(reserva_id: int | str) -> dict[str, Any]:
    return await _request("DELETE", f"/api/reservas-salas/{reserva_id}")


# ── Eventos / Covers ───────────────────────────────────────────────────────

async def consultar_evento(fecha: str) -> dict[str, Any]:
    """GET /api/eventos?fecha=YYYY-MM-DD — evento del día (o lista si no se pasa fecha)."""
    return await _request("GET", "/api/eventos", params={"fecha": fecha})


async def listar_eventos() -> dict[str, Any]:
    """GET /api/eventos (sin fecha) — todos los eventos registrados."""
    return await _request("GET", "/api/eventos")


async def crear_evento(payload: dict) -> dict[str, Any]:
    """POST /api/eventos — crea/actualiza evento del día.

    payload: {fecha, nombre, artista?, tiene_cover, valor_cover?, link_pago?}
    """
    return await _request("POST", "/api/eventos", json=payload)


async def borrar_evento(fecha: str) -> dict[str, Any]:
    """DELETE /api/eventos/:fecha — apaga modo evento (reservas se conservan)."""
    return await _request("DELETE", f"/api/eventos/{fecha}")


# ── Mesas (admin) ──────────────────────────────────────────────────────────

async def listar_mesas() -> dict[str, Any]:
    return await _request("GET", "/api/mesas")


async def layout() -> dict[str, Any]:
    """GET /api/layout — canvas, zonas, fijos, coords de las mesas (para dibujar)."""
    return await _request("GET", "/api/layout")


async def plano() -> dict[str, Any]:
    """GET /api/plano — URL de la imagen del plano (foto estática)."""
    return await _request("GET", "/api/plano")


# ── Resumen del día (vista equipo interno) ────────────────────────────────

async def resumen_dia(fecha: str) -> dict[str, Any]:
    """GET /api/resumen?fecha=YYYY-MM-DD — para el equipo. No usar con clientes."""
    return await _request("GET", "/api/resumen", params={"fecha": fecha})
