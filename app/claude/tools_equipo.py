"""Tools del flujo EQUIPO (Fabio, Edgardo, staff) — operación interna.

Se invocan desde el grupo WhatsApp del equipo. Cubren:
- Resumen del día (reservas, covers, recaudo)
- Buscar / editar / cancelar reservas
- Marcar covers (pagado anticipado / en entrada / pendiente)
- Crear o editar eventos del día

Todas las tools golpean el backend de mesas (`cantina_api.py`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable

from app.config import get_settings
from app.integrations import cantina_api
from app.logging_setup import log

_FLYERS = Path(get_settings().data_dir) / "media" / "flyers"
_EXT_POR_MIME = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _guardar_flyer(fecha: str, imagen_bytes: bytes | None, mime: str | None) -> bool:
    """Guarda la imagen como flyer del evento de una fecha. True si guardó."""
    if not (fecha and imagen_bytes):
        return False
    ext = _EXT_POR_MIME.get((mime or "").lower(), ".jpg")
    _FLYERS.mkdir(parents=True, exist_ok=True)
    for e in (".jpg", ".jpeg", ".png", ".webp"):  # un flyer por fecha
        old = _FLYERS / f"{fecha}{e}"
        if old.exists():
            try:
                old.unlink()
            except Exception:
                pass
    (_FLYERS / f"{fecha}{ext}").write_bytes(imagen_bytes)
    return True


def _guardar_descripcion(fecha: str, texto: str | None) -> None:
    if not (fecha and (texto or "").strip()):
        return
    _FLYERS.mkdir(parents=True, exist_ok=True)
    (_FLYERS / f"{fecha}.txt").write_text(texto.strip(), encoding="utf-8")


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
        "name": "crear_reserva",
        "description": (
            "Crea una reserva de UNA mesa (el equipo reserva a nombre de un "
            "cliente). Si la fecha tiene evento con cover, el backend lo aplica. "
            "Usa consultar_reservas_del_dia/resumen_dia antes si dudas de la mesa."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "mesa_id": {"type": "integer", "description": "Número de la mesa (1-42)"},
                "nombre_cliente": {"type": "string"},
                "telefono": {"type": "string"},
                "num_personas": {"type": "integer"},
                "notas": {"type": "string"},
            },
            "required": ["fecha", "mesa_id", "nombre_cliente", "num_personas"],
        },
    },
    {
        "name": "crear_reserva_grupo",
        "description": (
            "Reserva un GRUPO grande sobre varias mesas vecinas contiguas (el "
            "backend valida contigüidad)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string"},
                "mesa_numeros": {"type": "array", "items": {"type": "integer"}},
                "nombre_cliente": {"type": "string"},
                "telefono": {"type": "string"},
                "num_personas": {"type": "integer"},
                "notas": {"type": "string"},
            },
            "required": ["fecha", "mesa_numeros", "nombre_cliente", "num_personas"],
        },
    },
    {
        "name": "crear_reserva_sala_privada",
        "description": (
            "Reserva una SALA PRIVADA (1 o 2). 10p, mínimo de consumo $1.000.000 "
            "(gastable), sin cover."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string"},
                "sala_id": {"type": "integer", "description": "1 o 2"},
                "nombre_cliente": {"type": "string"},
                "telefono": {"type": "string"},
                "num_personas": {"type": "integer"},
                "notas": {"type": "string"},
            },
            "required": ["fecha", "sala_id", "nombre_cliente", "num_personas"],
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
            "Crea/actualiza el evento de un día (artista, cover, link de pago, "
            "descripción). Solo el equipo. **Si el equipo adjuntó una imagen en el "
            "mismo mensaje, se guarda como FLYER del evento automáticamente** y el "
            "bot se la enviará a los clientes que pregunten por ese día."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "nombre": {"type": "string"},
                "artista": {"type": "string"},
                "tiene_cover": {"type": "boolean"},
                "valor_cover": {"type": "integer", "description": "COP por persona"},
                "link_pago": {"type": "string", "description": "Link de la pasarela de pago"},
                "descripcion": {"type": "string", "description": "Descripción corta del evento"},
            },
            "required": ["fecha", "nombre"],
        },
    },
    {
        "name": "guardar_flyer_evento",
        "description": (
            "Guarda la imagen ADJUNTA en el mensaje como flyer del evento de una "
            "fecha (para agregar/cambiar el flyer de un evento ya creado). Requiere "
            "que el equipo haya mandado una imagen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"fecha": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["fecha"],
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
    {
        "name": "avisar_cliente",
        "description": (
            "Envía un mensaje de WhatsApp a un CLIENTE (no al grupo del equipo). "
            "Úsalo cuando el equipo te diga 'dile a X que...' o para notificar a un "
            "cliente (cambio de mesa, confirmación, etc.). Necesitas el teléfono del "
            "cliente en formato +57...; si no lo tienes, búscalo con "
            "`consultar_reservas_del_dia` o pídeselo al equipo. Redacta el mensaje en "
            "primera persona como La Cantina (cálido y claro)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "telefono": {"type": "string", "description": "Número del cliente, +57..."},
                "mensaje": {"type": "string", "description": "Texto a enviarle al cliente"},
            },
            "required": ["telefono", "mensaje"],
        },
    },
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


async def handler_crear_reserva(args: dict, ctx: dict) -> dict:
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "mesa_id": args.get("mesa_id"),
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": args.get("telefono"),
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    return await cantina_api.crear_reserva(payload)


async def handler_crear_reserva_grupo(args: dict, ctx: dict) -> dict:
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "mesa_numeros": args.get("mesa_numeros") or [],
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": args.get("telefono"),
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    return await cantina_api.crear_reserva_grupo(payload)


async def handler_crear_reserva_sala(args: dict, ctx: dict) -> dict:
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "sala_id": args.get("sala_id"),
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": args.get("telefono"),
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    return await cantina_api.crear_reserva_sala(payload)


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
    fecha = args.get("fecha")
    descripcion = args.get("descripcion")
    # El backend de eventos no guarda descripción ni flyer → van aparte (local).
    payload = {k: v for k, v in args.items()
               if v is not None and k not in ("descripcion",)}
    res = await cantina_api.crear_evento(payload)
    if isinstance(res, dict) and res.get("ok"):
        guardo_flyer = _guardar_flyer(fecha, ctx.get("imagen_bytes"), ctx.get("imagen_mime"))
        _guardar_descripcion(fecha, descripcion)
        res["flyer_guardado"] = guardo_flyer
        if not guardo_flyer:
            res["nota_flyer"] = ("Evento creado. No adjuntaste imagen, así que NO hay flyer. "
                                 "Si quieres flyer, mándame la imagen y usa guardar_flyer_evento.")
        else:
            res["nota_flyer"] = "Evento creado CON flyer. El bot se lo enviará a quien pregunte por ese día."
        log.info("tools_equipo.crear_evento", fecha=fecha, flyer=guardo_flyer)
    return res


async def handler_guardar_flyer_evento(args: dict, ctx: dict) -> dict:
    fecha = args.get("fecha")
    if not ctx.get("imagen_bytes"):
        return {"ok": False, "error": "No recibí ninguna imagen. Adjunta el flyer en el mismo mensaje."}
    ok = _guardar_flyer(fecha, ctx.get("imagen_bytes"), ctx.get("imagen_mime"))
    log.info("tools_equipo.guardar_flyer_evento", fecha=fecha, ok=ok)
    return {"ok": ok, "fecha": fecha} if ok else {"ok": False, "error": "no se pudo guardar el flyer"}


async def handler_borrar_evento(args: dict, ctx: dict) -> dict:
    return await cantina_api.borrar_evento(args.get("fecha"))


async def handler_avisar_cliente(args: dict, ctx: dict) -> dict:
    """Envía un WhatsApp a un cliente y lo deja registrado en su chat."""
    tel = (args.get("telefono") or "").strip()
    mensaje = (args.get("mensaje") or "").strip()
    if not tel or not mensaje:
        return {"ok": False, "error": "telefono y mensaje son requeridos"}
    numero = tel if tel.startswith("+") else "+" + tel.lstrip("+")
    from app.whapi.client import enviar_texto
    try:
        await enviar_texto(numero, mensaje)
    except Exception as e:
        log.warning("tools_equipo.avisar_cliente.fail", tel=numero, error=str(e))
        return {"ok": False, "error": f"no se pudo enviar: {str(e)[:160]}"}
    # Registrar el outbound en el chat del cliente (aparece en /admin/chats).
    session = ctx.get("session")
    if session is not None:
        try:
            from app.db.repos import get_or_create_cliente, guardar_conversacion
            cli = await get_or_create_cliente(session, numero)
            await guardar_conversacion(
                session, cliente_id=cli.id, direccion="outbound", tipo="texto",
                contenido=mensaje,
                metadata={"enviado_por_equipo": ctx.get("miembro_nombre")},
            )
        except Exception as e:
            log.debug("tools_equipo.avisar_cliente.persist_fail", error=str(e))
    log.info("tools_equipo.avisar_cliente", tel=numero, por=ctx.get("miembro_nombre"))
    return {"ok": True, "enviado_a": numero}


# ── DISPATCHER ──────────────────────────────────────────────────────────────

Handler = Callable[[dict, dict], Awaitable[dict]]

HANDLERS_EQUIPO: dict[str, Handler] = {
    "consultar_reservas_del_dia": handler_consultar_reservas_del_dia,
    "resumen_dia": handler_resumen_dia,
    "consultar_reserva": handler_consultar_reserva,
    "crear_reserva": handler_crear_reserva,
    "crear_reserva_grupo": handler_crear_reserva_grupo,
    "crear_reserva_sala_privada": handler_crear_reserva_sala,
    "actualizar_reserva": handler_actualizar_reserva,
    "cancelar_reserva": handler_cancelar_reserva,
    "marcar_cover_pagado": handler_marcar_cover_pagado,
    "marcar_cover_en_entrada": handler_marcar_cover_en_entrada,
    "crear_evento": handler_crear_evento,
    "guardar_flyer_evento": handler_guardar_flyer_evento,
    "borrar_evento": handler_borrar_evento,
    "avisar_cliente": handler_avisar_cliente,
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
