"""Tools del flujo EQUIPO (Fabio, Edgardo, staff) — operación interna.

Se invocan desde el grupo WhatsApp del equipo. Cubren:
- Resumen del día (reservas, covers, recaudo)
- Buscar / editar / cancelar reservas
- Marcar covers (pagado anticipado / en entrada / pendiente)
- Crear o editar eventos del día
- Responder en nombre del bot a un cliente puntual

NOTA: en `bot_asistente` (María/DTGP) este archivo tiene MUCHO más (finanzas,
DT-OS, cuentas de cobro, Google Sheets). Aquí se reduce a lo de mesas/eventos.
Las "tools generales" reusables (responder_a_cliente, consultar_chat_cliente,
marcar_alerta_resuelta, etiquetar_contacto) se pueden COPIAR DESDE
`bot_asistente/app/claude/tools_equipo.py` sin cambios.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from app.integrations import cantina_api
from app.logging_setup import log


TOOL_DEFINITIONS_EQUIPO: list[dict] = [
    {
        "name": "consultar_reservas_del_dia",
        "description": "Lista las reservas de un día (resumen para el equipo).",
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "incluir_canceladas": {"type": "boolean"},
            },
            "required": ["fecha"],
        },
    },
    {
        "name": "resumen_dia",
        "description": (
            "Resumen ejecutivo del día: # reservas, ocupación, cover esperado vs "
            "anticipado/en_entrada/pendiente, salas. Para uso interno."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"fecha": {"type": "string"}},
            "required": ["fecha"],
        },
    },
    {
        "name": "consultar_reserva",
        "description": "Detalle de una reserva por id.",
        "input_schema": {
            "type": "object",
            "properties": {"reserva_id": {"type": "integer"}},
            "required": ["reserva_id"],
        },
    },
    {
        "name": "actualizar_reserva",
        "description": (
            "Edita una reserva. Cambios admitidos: cover_estado, estado, notas. "
            "Para cancelar prefiere `cancelar_reserva`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reserva_id": {"type": "integer"},
                "cover_estado": {
                    "type": "string",
                    "enum": ["no_aplica", "pendiente", "anticipado", "en_entrada"],
                },
                "estado": {"type": "string", "enum": ["confirmada", "cancelada"]},
                "notas": {"type": "string"},
            },
            "required": ["reserva_id"],
        },
    },
    {
        "name": "cancelar_reserva",
        "description": "Cancela una reserva (soft delete + libera la mesa).",
        "input_schema": {
            "type": "object",
            "properties": {"reserva_id": {"type": "integer"}},
            "required": ["reserva_id"],
        },
    },
    {
        "name": "marcar_cover_pagado",
        "description": (
            "Marca el cover de una reserva como pagado anticipado (después de "
            "verificar el comprobante)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reserva_id": {"type": "integer"}},
            "required": ["reserva_id"],
        },
    },
    {
        "name": "marcar_cover_en_entrada",
        "description": "Marca el cover como 'paga en la entrada' (sin pago anticipado).",
        "input_schema": {
            "type": "object",
            "properties": {"reserva_id": {"type": "integer"}},
            "required": ["reserva_id"],
        },
    },
    {
        "name": "crear_evento",
        "description": (
            "Crea/actualiza el evento de un día (artista, cover, link de pago). "
            "Solo el equipo lo usa, NO el flujo cliente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string"},
                "nombre": {"type": "string"},
                "artista": {"type": "string"},
                "tiene_cover": {"type": "boolean"},
                "valor_cover": {"type": "integer", "description": "COP por persona"},
                "link_pago": {"type": "string"},
            },
            "required": ["fecha", "nombre"],
        },
    },
    {
        "name": "borrar_evento",
        "description": "Apaga el 'modo evento' de un día. Las reservas se conservan.",
        "input_schema": {
            "type": "object",
            "properties": {"fecha": {"type": "string"}},
            "required": ["fecha"],
        },
    },
    # TODO: copiar de `bot_asistente/app/claude/tools_equipo.py` las tools
    # reusables genéricas: responder_a_cliente, consultar_chat_cliente,
    # consultar_chats_sin_responder, marcar_numero_interno,
    # marcar_alerta_resuelta, consultar_alertas_abiertas, etiquetar_contacto,
    # aprender_regla / olvidar_regla, programar_recordatorio, etc.
    # Adaptar el system prompt para el contexto de La Cantina (no DTGP).
]


# ── HANDLERS ────────────────────────────────────────────────────────────────


async def handler_consultar_reservas_del_dia(args: dict, ctx: dict) -> dict:
    return await cantina_api.listar_reservas(
        args.get("fecha"), bool(args.get("incluir_canceladas")),
    )


async def handler_resumen_dia(args: dict, ctx: dict) -> dict:
    return await cantina_api.resumen_dia(args.get("fecha"))


async def handler_consultar_reserva(args: dict, ctx: dict) -> dict:
    return await cantina_api.detalle_reserva(args.get("reserva_id"))


async def handler_actualizar_reserva(args: dict, ctx: dict) -> dict:
    cambios = {k: args[k] for k in ("cover_estado", "estado", "notas") if args.get(k) is not None}
    if not cambios:
        return {"ok": False, "error": "Sin cambios a aplicar"}
    return await cantina_api.actualizar_reserva(args.get("reserva_id"), cambios)


async def handler_cancelar_reserva(args: dict, ctx: dict) -> dict:
    return await cantina_api.cancelar_reserva(args.get("reserva_id"))


async def handler_marcar_cover_pagado(args: dict, ctx: dict) -> dict:
    return await cantina_api.actualizar_reserva(
        args.get("reserva_id"), {"cover_estado": "anticipado"},
    )


async def handler_marcar_cover_en_entrada(args: dict, ctx: dict) -> dict:
    return await cantina_api.actualizar_reserva(
        args.get("reserva_id"), {"cover_estado": "en_entrada"},
    )


async def handler_crear_evento(args: dict, ctx: dict) -> dict:
    payload = {k: v for k, v in args.items() if v is not None}
    return await cantina_api.crear_evento(payload)


async def handler_borrar_evento(args: dict, ctx: dict) -> dict:
    return await cantina_api.borrar_evento(args.get("fecha"))


# ── DISPATCHER ──────────────────────────────────────────────────────────────

Handler = Callable[[dict, dict], Awaitable[dict]]

HANDLERS_EQUIPO: dict[str, Handler] = {
    "consultar_reservas_del_dia": handler_consultar_reservas_del_dia,
    "resumen_dia": handler_resumen_dia,
    "consultar_reserva": handler_consultar_reserva,
    "actualizar_reserva": handler_actualizar_reserva,
    "cancelar_reserva": handler_cancelar_reserva,
    "marcar_cover_pagado": handler_marcar_cover_pagado,
    "marcar_cover_en_entrada": handler_marcar_cover_en_entrada,
    "crear_evento": handler_crear_evento,
    "borrar_evento": handler_borrar_evento,
    # TODO: añadir handlers de las tools genéricas que se copien de bot_asistente.
}


async def ejecutar_tool_equipo(name: str, args: dict, ctx: dict) -> dict:
    handler = HANDLERS_EQUIPO.get(name)
    if not handler:
        return {"error": f"Tool de equipo desconocida: {name}"}
    try:
        return await handler(args, ctx)
    except Exception as e:
        log.exception("tools_equipo.exec.fail", tool=name, error=str(e))
        return {"error": str(e)[:200]}
