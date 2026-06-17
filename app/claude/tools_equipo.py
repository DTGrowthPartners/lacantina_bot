"""Tools del flujo EQUIPO (Fabio, Edgardo, staff) — operación interna.

Se invocan desde el grupo WhatsApp del equipo. Cubren:
- Resumen del día (reservas, covers, recaudo)
- Buscar / editar / cancelar reservas
- Marcar covers (pagado anticipado / en entrada / pendiente / invitado)
- Crear o editar eventos del día

Todas las tools golpean el backend de mesas (`cantina_api.py`).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import text as sa_text

from app.config import get_settings
from app.difusiones import SegmentoDifusion, ejecutar_difusion_background, preparar_difusion
from app.integrations import cantina_api
from app.logging_setup import log

_settings = get_settings()
_FLYERS = Path(_settings.data_dir) / "media" / "flyers"
_DIFUSIONES_MEDIA = Path(_settings.data_dir) / "media" / "difusiones"
_PLANO = Path(get_settings().data_dir) / "media" / "plano-espacio.png"
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
        "name": "marcar_casa_llena",
        "description": (
            "Cierra nuevas reservas para una fecha. Úsala cuando el equipo diga "
            "'casa llena', 'estamos llenos', 'no acepten más reservas' o equivalente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "YYYY-MM-DD; si no se indica, usa hoy.",
                },
                "motivo": {"type": "string"},
            },
        },
    },
    {
        "name": "reabrir_reservas",
        "description": (
            "Vuelve a permitir reservas para una fecha cerrada por casa llena."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "YYYY-MM-DD; si no se indica, usa hoy.",
                },
            },
        },
    },
    {
        "name": "enviar_plano_espacio",
        "description": (
            "Envía el plano del salón con las mesas ya reservadas marcadas en rojo "
            "para la fecha indicada. Úsalo cuando pregunten por la distribución, "
            "cómo están las mesas, el mapa/plano o la disponibilidad visual. "
            "SÍ existe la foto — NUNCA digas que no la tienes. "
            "Acompáñala con una breve descripción de las 3 zonas (Cantina, VIP, Rumbero)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "YYYY-MM-DD de la noche a mostrar. Si no se mencionó, usa hoy.",
                },
            },
        },
    },
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
            "anticipado/en_entrada/pendiente/invitado, salas. Los invitados no "
            "cuentan en el recaudo esperado (el backend los rastrea aparte: "
            "invitado, n_invitado, personas_invitadas). Para uso interno."
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
            "Edita una reserva. Cambios admitidos: cover_estado, estado, notas, "
            "num_personas, nombre_cliente, telefono. Para cancelar prefiere "
            "`cancelar_reserva`. OJO: cambiar num_personas NO reasigna ni valida "
            "la mesa — si el grupo ya no cabe en su(s) mesa(s), avísale al equipo "
            "que quizá toque mover la reserva."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reserva_id": {"type": "integer"},
                "cover_estado": {
                    "type": "string",
                    "enum": ["no_aplica", "pendiente", "anticipado", "en_entrada", "invitado"],
                },
                "estado": {"type": "string", "enum": ["confirmada", "cancelada"]},
                "notas": {"type": "string"},
                "num_personas": {"type": "integer", "description": "Nueva cantidad de personas (≥1)"},
                "nombre_cliente": {"type": "string", "description": "Corregir el nombre del cliente"},
                "telefono": {"type": "string", "description": "Corregir el teléfono, +57..."},
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
        "name": "marcar_cover_invitado",
        "description": (
            "Marca la reserva como INVITADO: el dueño invitó a ese grupo y NO paga "
            "cover. No cuenta en el recaudo esperado. ÚSALO SOLO cuando el dueño/jefe "
            "lo indique explícitamente (ej. 'la mesa de Juan va como invitada', 'a "
            "esos no les cobres cover'). Nunca lo asumas por tu cuenta: si dudas, "
            "deja el cover como estaba."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reserva_id": {"type": "integer"}},
            "required": ["reserva_id"],
        },
    },
    {
        "name": "eventos_del_mes",
        "description": (
            "Lista los eventos/covers registrados en el backend para un mes completo. "
            "Úsala cuando el equipo pregunte 'qué eventos hay este mes', "
            "'eventos de junio', 'agenda de eventos del mes' o similar. "
            "No respondas con Instagram ni digas que no tienes herramienta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mes": {
                    "type": "string",
                    "description": "Mes en formato YYYY-MM. Si no se menciona, usa el mes actual.",
                },
            },
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
    {
        "name": "crear_difusion_evento",
        "description": (
            "Crea una difusión masiva de evento/promo para la base de contactos. "
            "Úsala cuando el equipo mande una imagen/flyer y diga 'difunde esto', "
            "'haz difusión del evento', 'mándalo a la base', 'difunde esta imagen'. "
            "Si hay imagen adjunta, la guarda y la envía como flyer con caption. "
            "Por defecto usa el tag 'Base difusión contacts.vcf' y envía lentamente "
            "para reducir riesgo de bloqueo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mensaje": {
                    "type": "string",
                    "description": (
                        "Mensaje/caption final que recibirá la audiencia. Puede usar "
                        "{primer_nombre}. Debe ser alusivo al evento y sonar humano."
                    ),
                },
                "nombre": {
                    "type": "string",
                    "description": "Nombre interno de la campaña.",
                },
                "tag_nombre": {
                    "type": "string",
                    "description": "Tag de audiencia. Default: Base difusión contacts.vcf",
                },
                "etiqueta": {
                    "type": "string",
                    "enum": ["todos", "cliente", "prospecto", "sin_clasificar"],
                    "description": "Filtro adicional por etiqueta. Default: todos.",
                },
                "delay_min_s": {
                    "type": "number",
                    "description": "Delay mínimo entre contactos. Default seguro: 45.",
                },
                "delay_max_s": {
                    "type": "number",
                    "description": "Delay máximo entre contactos. Default seguro: 90.",
                },
                "iniciar_envio": {
                    "type": "boolean",
                    "description": "True para empezar a enviar de una vez. Default true.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "True prepara/recorre sin enviar WhatsApp real.",
                },
            },
            "required": ["mensaje"],
        },
    },
    {
        "name": "publicar_estado",
        "description": (
            "Publica la IMAGEN o VIDEO ADJUNTO como estado de WhatsApp de La Cantina "
            "Y lo guarda como 'estado vigente' para que el bot pueda reenviárselo a "
            "los clientes que lo pidan. Úsalo cuando el equipo mande una imagen o un "
            "video diciendo 'publica esto como estado', 'sube este estado', 'pon esta "
            "promo', etc. Requiere que el equipo haya adjuntado una imagen o video en "
            "el mensaje. Si incluyen un texto para acompañar la promo, pásalo en `caption`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "caption": {"type": "string", "description": "Texto opcional para acompañar el estado/promo"},
            },
        },
    },
    {
        "name": "enviar_estado_actual",
        "description": (
            "Envía al chat la IMAGEN o VIDEO del estado/promo vigente de La Cantina "
            "(lo último que el equipo publicó). Úsalo cuando alguien pida 'el estado', "
            "'la promo', 'la foto que subieron', 'el flyer', etc. "
            "Si no hay estado vigente, la tool te avisa y lo dices con amabilidad."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "borrar_ultimo_estado",
        "description": (
            "Borra el último estado real publicado por La Cantina en WhatsApp. "
            "Úsalo cuando el equipo diga 'borra el último estado', 'elimina el "
            "estado reciente', 'quita el estado que subiste' o similar. Ignora "
            "registros vacíos, acciones de borrado pendientes y estados ya revocados."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "reenviar_comprobante_cliente",
        "description": (
            "Reenvía a ESTE chat (el grupo del equipo) la última IMAGEN que envió "
            "un cliente — típicamente su comprobante de pago de cover. Úsalo cuando "
            "el equipo diga 'reenvía el comprobante de +57...', 'pásame la imagen "
            "que mandó X' o similar. SÍ tienes esta herramienta: recuperas la imagen "
            "guardada del cliente y la mandas al grupo; NUNCA digas que toca hacerlo "
            "manualmente. Necesitas el teléfono del cliente en formato +57...; si no "
            "lo tienes, búscalo con consultar_reservas_del_dia."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "telefono": {"type": "string", "description": "Número del cliente, +57..."},
            },
            "required": ["telefono"],
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
    campos = ("cover_estado", "estado", "notas", "num_personas", "nombre_cliente", "telefono")
    cambios = {k: args[k] for k in campos if args.get(k) is not None}
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


async def handler_marcar_cover_invitado(args: dict, ctx: dict) -> dict:
    return await cantina_api.actualizar_reserva(
        args.get("reserva_id"), {"cover_estado": "invitado"},
    )


def _mes_actual_bogota() -> str:
    return datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m")


def _normalizar_mes(raw: str | None) -> tuple[str | None, str | None]:
    value = (raw or "").strip()
    if not value:
        return _mes_actual_bogota(), None
    # Permite que el modelo mande YYYY-MM-DD; se usa el mes de esa fecha.
    if len(value) >= 7 and value[4] == "-":
        month = value[:7]
        try:
            datetime.strptime(month, "%Y-%m")
            return month, None
        except ValueError:
            pass
    return None, "mes debe venir en formato YYYY-MM"


def _extraer_eventos(resp: dict) -> list[dict]:
    if not isinstance(resp, dict) or not resp.get("ok", True):
        return []
    data = resp.get("data", resp)
    if isinstance(data, dict):
        eventos = data.get("eventos")
        if isinstance(eventos, list):
            return [e for e in eventos if isinstance(e, dict)]
        evento = data.get("evento")
        if isinstance(evento, dict):
            return [evento]
        if data.get("fecha") or data.get("nombre"):
            return [data]
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    return []


async def handler_eventos_del_mes(args: dict, ctx: dict) -> dict:
    mes, error = _normalizar_mes(args.get("mes"))
    if error:
        return {"ok": False, "error": error}
    res = await cantina_api.listar_eventos()
    if not isinstance(res, dict) or not res.get("ok", False):
        return res
    eventos = [
        e for e in _extraer_eventos(res)
        if str(e.get("fecha") or "").startswith(mes or "")
    ]
    eventos.sort(key=lambda e: str(e.get("fecha") or ""))
    return {
        "ok": True,
        "mes": mes,
        "total": len(eventos),
        "eventos": eventos,
        "nota": (
            "Responde por WhatsApp con una lista corta por fecha. "
            "Si total=0, di que no hay eventos registrados en backend para ese mes."
        ),
    }


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


async def handler_crear_difusion_evento(args: dict, ctx: dict) -> dict:
    """Crea y opcionalmente inicia una difusión desde el chat del equipo."""
    if (ctx.get("rol") or "").lower() == "cliente":
        return {"ok": False, "error": "Un cliente whitelisted no puede crear difusiones."}
    session = ctx.get("session")
    if session is None:
        return {"ok": False, "error": "sin sesión de BD"}

    mensaje = (args.get("mensaje") or "").strip()
    if not mensaje:
        return {"ok": False, "error": "mensaje requerido"}

    imagen_bytes = ctx.get("imagen_bytes")
    imagen_mime = ctx.get("imagen_mime") or "image/jpeg"
    if not imagen_bytes:
        return {
            "ok": False,
            "error": (
                "Para difundir un flyer necesito que adjuntes la imagen en el mismo mensaje. "
                "Si quieres solo texto, créala desde /admin/difusiones."
            ),
        }

    tag_nombre = (args.get("tag_nombre") or "Base difusión contacts.vcf").strip()
    tag_row = (await session.execute(sa_text(
        "SELECT id FROM tags WHERE lower(nombre)=lower(:nombre) LIMIT 1"
    ), {"nombre": tag_nombre})).first()
    if not tag_row:
        return {"ok": False, "error": f"No existe el tag de audiencia '{tag_nombre}'."}

    from app.difusiones import normalizar_etiqueta_difusion
    from decimal import Decimal

    etiqueta = normalizar_etiqueta_difusion(args.get("etiqueta") or "todos")
    nombre = (args.get("nombre") or "Difusión evento desde WhatsApp").strip()[:140]
    delay_min = Decimal(str(args.get("delay_min_s") or 45))
    delay_max = Decimal(str(args.get("delay_max_s") or 90))
    dry_run = bool(args.get("dry_run") or False)
    iniciar_envio = bool(args.get("iniciar_envio", True))

    difusion_id = await preparar_difusion(
        session,
        nombre=nombre,
        mensaje=mensaje,
        media_url=None,
        segmento=SegmentoDifusion(
            etiqueta=etiqueta,
            tag_id=int(tag_row[0]),
            incluir_sin_chat=True,
        ),
        delay_min_s=delay_min,
        delay_max_s=delay_max,
        dry_run=dry_run,
        creado_por=ctx.get("miembro_nombre") or "whatsapp",
    )

    ext = _EXT_POR_MIME.get((imagen_mime or "").lower(), ".jpg")
    _DIFUSIONES_MEDIA.mkdir(parents=True, exist_ok=True)
    media_path = _DIFUSIONES_MEDIA / f"difusion-{difusion_id}{ext}"
    media_path.write_bytes(imagen_bytes)

    await session.execute(sa_text(
        """
        UPDATE difusiones
           SET metadata = metadata || CAST(:metadata AS jsonb),
               updated_at = now()
         WHERE id=:id
        """
    ), {
        "id": difusion_id,
        "metadata": json.dumps({
            "media_path": str(media_path),
            "media_mime": imagen_mime,
            "creada_desde": "whatsapp_equipo",
            "tag_nombre": tag_nombre,
        }),
    })
    await session.commit()

    total = (await session.execute(sa_text(
        "SELECT total_destinatarios FROM difusiones WHERE id=:id"
    ), {"id": difusion_id})).scalar_one()
    if iniciar_envio:
        asyncio.create_task(ejecutar_difusion_background(difusion_id))

    log.info(
        "tools_equipo.crear_difusion_evento",
        difusion_id=difusion_id,
        total=total,
        iniciar_envio=iniciar_envio,
        dry_run=dry_run,
        tag=tag_nombre,
    )
    return {
        "ok": True,
        "difusion_id": difusion_id,
        "destinatarios": int(total or 0),
        "tag": tag_nombre,
        "delay": f"{delay_min}-{delay_max}s",
        "iniciada": iniciar_envio,
        "dry_run": dry_run,
        "nota": (
            "Difusión preparada"
            + (" y arrancada en segundo plano." if iniciar_envio else ".")
            + " Responde con una confirmación corta y aclara que irá lenta."
        ),
    }


async def handler_publicar_estado(args: dict, ctx: dict) -> dict:
    """Publica la imagen/video adjunto como estado de WhatsApp y lo guarda como vigente."""
    imagen_bytes = ctx.get("imagen_bytes")
    video_bytes = ctx.get("video_bytes")
    if not imagen_bytes and not video_bytes:
        return {"ok": False, "error": "No recibí ninguna imagen ni video. Adjunta el "
                                      "contenido del estado en el mismo mensaje."}
    caption = (args.get("caption") or "").strip() or None
    from app import promo_estado
    from app.whapi.client import publicar_story_imagen_bytes, publicar_story_video_bytes
    # 1. Publicar como estado de WhatsApp (video tiene prioridad si llegan ambos).
    try:
        if video_bytes:
            data, mime = video_bytes, (ctx.get("video_mime") or "video/mp4")
            await publicar_story_video_bytes(data, caption=caption, mime=mime)
        else:
            data, mime = imagen_bytes, (ctx.get("imagen_mime") or "image/jpeg")
            await publicar_story_imagen_bytes(data, caption=caption, mime=mime)
    except Exception as e:
        log.warning("tools_equipo.publicar_estado.story_fail", error=str(e))
        return {"ok": False, "error": f"no pude publicar el estado: {str(e)[:160]}"}
    # 2. Guardar como estado vigente (para reenviar a clientes).
    try:
        promo_estado.guardar_estado(data, mime, caption)
    except Exception as e:
        log.warning("tools_equipo.publicar_estado.guardar_fail", error=str(e))
        return {"ok": True, "nota": "Estado publicado, pero no pude guardarlo para "
                                    "reenviar a clientes. Avísale al admin."}
    tipo = "video" if video_bytes else "imagen"
    log.info("tools_equipo.publicar_estado", por=ctx.get("miembro_nombre"), tipo=tipo)
    return {"ok": True, "nota": f"Estado ({tipo}) publicado en WhatsApp y guardado. Ahora el "
                                "bot puede reenviárselo a los clientes que pregunten por la promo/estado."}


async def handler_enviar_estado_actual(args: dict, ctx: dict) -> dict:
    """Marca que hay que enviar la imagen/video del estado vigente al chat del equipo."""
    from app import promo_estado
    if promo_estado.cargar_estado() is None:
        return {"ok": False, "sin_estado": True,
                "nota": "No hay un estado/promo vigente guardado. Avísale que por ahora no hay una promo publicada."}
    ctx["enviar_estado_actual"] = True
    return {"ok": True, "nota": "La imagen del estado/promo se enviará junto con tu respuesta."}


async def handler_borrar_ultimo_estado(args: dict, ctx: dict) -> dict:
    """Borra el último estado propio con contenido real (imagen/video/texto)."""
    from app.whapi.client import eliminar_mensaje, listar_stories

    stories = await listar_stories(count=100)
    items = stories.get("stories") or stories.get("messages") or stories.get("data") or []
    candidatos: list[dict] = []

    for story in items:
        if not isinstance(story, dict) or not story.get("from_me"):
            continue
        if story.get("subtype") == "revoke" or story.get("action"):
            continue
        media_kind = next(
            (kind for kind in ("image", "video", "audio", "text") if isinstance(story.get(kind), dict)),
            None,
        )
        if not media_kind:
            continue
        candidatos.append(story)

    if not candidatos:
        return {
            "ok": False,
            "sin_estado": True,
            "nota": "No encontré estados activos con imagen, video o texto para borrar.",
        }

    ultimo = max(candidatos, key=lambda s: int(s.get("timestamp") or 0))
    message_id = ultimo.get("id")
    if not message_id:
        return {"ok": False, "error": "Encontré un estado, pero no trae id para borrarlo."}

    await eliminar_mensaje(message_id)
    log.info(
        "tools_equipo.borrar_ultimo_estado",
        por=ctx.get("miembro_nombre"),
        message_id=message_id,
        timestamp=ultimo.get("timestamp"),
    )
    return {
        "ok": True,
        "message_id": message_id,
        "nota": (
            "Último estado enviado a borrar. WhatsApp/Whapi puede tardar unos minutos "
            "en reflejarlo; si aparece como pendiente, es sincronización del canal."
        ),
    }


async def handler_reenviar_comprobante_cliente(args: dict, ctx: dict) -> dict:
    """Recupera la última imagen que mandó un cliente (su comprobante) y la
    reenvía al chat actual (el grupo del equipo)."""
    from app.db.repos import ultima_imagen_inbound
    from app.whapi.parser import normalizar_numero
    from app.whapi.client import auth_headers, enviar_imagen_bytes

    tel = normalizar_numero((args.get("telefono") or "").strip())
    if not tel:
        return {"ok": False, "error": "telefono requerido en formato +57..."}
    destino = ctx.get("destino_envio")
    if not destino:
        return {"ok": False, "error": "no hay chat destino para reenviar la imagen"}
    session = ctx.get("session")
    if session is None:
        return {"ok": False, "error": "sin sesión de BD"}

    conv = await ultima_imagen_inbound(session, tel)
    if not conv or not conv.media_url:
        return {"ok": False, "error": (
            f"No encuentro ninguna imagen reciente enviada por {tel}. "
            "Verifica el número o pídele al cliente que reenvíe el comprobante."
        )}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(conv.media_url, headers=auth_headers())
        if r.status_code >= 400 or not r.content:
            log.warning("tools_equipo.reenviar_comprobante.download_http",
                        status=r.status_code, tel=tel)
            return {"ok": False, "error": "no pude descargar la imagen del cliente (link expirado)"}
        mime = r.headers.get("content-type") or "image/jpeg"
        await enviar_imagen_bytes(
            destino, r.content, mime=mime,
            caption=f"📎 Comprobante de {tel} (reenviado al grupo)",
        )
    except Exception as e:
        log.warning("tools_equipo.reenviar_comprobante.fail", tel=tel, error=str(e))
        return {"ok": False, "error": f"no se pudo reenviar la imagen: {str(e)[:160]}"}
    log.info("tools_equipo.reenviar_comprobante_cliente", tel=tel, destino=destino)
    return {"ok": True, "nota": f"Comprobante de {tel} reenviado a este grupo. "
                                "Confírmalo brevemente en tu texto."}


async def handler_enviar_plano_espacio(args: dict, ctx: dict) -> dict:
    """Manda el plano del salón con mesas reservadas marcadas en rojo."""
    if ctx.get("_plano_enviado"):
        return {"ok": True, "nota": "El plano ya se envió en este turno. NO lo mandes otra vez."}
    destino = ctx.get("destino_envio")
    if not destino:
        return {"ok": False, "error": "no hay chat destino para enviar la imagen"}
    if not _PLANO.exists():
        log.warning("tools_equipo.enviar_plano.no_existe", path=str(_PLANO))
        return {"ok": False, "error": "no encuentro la foto del plano en el servidor"}

    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.whapi.client import enviar_imagen_bytes
    from app.integrations import cantina_api
    from app.utils.plano import generar_plano_con_reservas

    fecha = args.get("fecha") or datetime.now(ZoneInfo("America/Bogota")).date().isoformat()

    # Obtener mesas ocupadas para la fecha
    mesas_reservadas: list[int] = []
    try:
        disp = await cantina_api.disponibilidad(fecha)
        ocupadas = (disp.get("ocupacion") or {}).get("ocupadas") or []
        mesas_reservadas = [int(m) for m in ocupadas if str(m).isdigit()]
    except Exception as e:
        log.warning("tools_equipo.enviar_plano.disponibilidad_fail", error=str(e))

    png_bytes = generar_plano_con_reservas(mesas_reservadas)
    if png_bytes is None:
        png_bytes = _PLANO.read_bytes()

    try:
        cap = f"🗺️ Plano del salón — {fecha} (🔴 = ya reservada)"
        await enviar_imagen_bytes(destino, png_bytes, mime="image/png",
                                  filename="plano-espacio.png", caption=cap)
    except Exception as e:
        log.warning("tools_equipo.enviar_plano.fail", destino=destino, error=str(e))
        return {"ok": False, "error": f"no se pudo enviar la foto: {str(e)[:160]}"}

    ctx["_plano_enviado"] = True
    log.info("tools_equipo.enviar_plano_espacio", destino=destino, fecha=fecha,
             reservadas=len(mesas_reservadas))
    return {
        "ok": True,
        "fecha": fecha,
        "mesas_marcadas": len(mesas_reservadas),
        "nota": "Plano enviado con mesas reservadas en rojo. Describe las 3 zonas brevemente.",
    }


async def handler_marcar_casa_llena(args: dict, ctx: dict) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    fecha = args.get("fecha") or datetime.now(
        ZoneInfo("America/Bogota")
    ).date().isoformat()
    return await cantina_api.marcar_casa_llena(
        fecha,
        args.get("motivo") or "Casa llena",
        ctx.get("miembro_nombre"),
    )


async def handler_reabrir_reservas(args: dict, ctx: dict) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    fecha = args.get("fecha") or datetime.now(
        ZoneInfo("America/Bogota")
    ).date().isoformat()
    return await cantina_api.reabrir_reservas(fecha)


# ── DISPATCHER ──────────────────────────────────────────────────────────────

Handler = Callable[[dict, dict], Awaitable[dict]]

HANDLERS_EQUIPO: dict[str, Handler] = {
    "marcar_casa_llena": handler_marcar_casa_llena,
    "reabrir_reservas": handler_reabrir_reservas,
    "enviar_plano_espacio": handler_enviar_plano_espacio,
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
    "marcar_cover_invitado": handler_marcar_cover_invitado,
    "eventos_del_mes": handler_eventos_del_mes,
    "crear_evento": handler_crear_evento,
    "guardar_flyer_evento": handler_guardar_flyer_evento,
    "borrar_evento": handler_borrar_evento,
    "avisar_cliente": handler_avisar_cliente,
    "crear_difusion_evento": handler_crear_difusion_evento,
    "reenviar_comprobante_cliente": handler_reenviar_comprobante_cliente,
    "publicar_estado": handler_publicar_estado,
    "enviar_estado_actual": handler_enviar_estado_actual,
    "borrar_ultimo_estado": handler_borrar_ultimo_estado,
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
