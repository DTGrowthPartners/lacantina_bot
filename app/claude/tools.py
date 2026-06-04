"""Tools del flujo CLIENTE (público que escribe al bot de La Cantina).

Cubre: FAQ, disponibilidad, reservas (mesa simple / grupo / sala privada),
registro de comprobante de cover, escalación al equipo.

Toda la lógica de negocio (capacidades, reglas VIP, doble reserva) la fuerza
el backend (`cantina_api.py`). Estas tools son thin wrappers que mapean los
args de Claude → llamada HTTP → resultado para el modelo.

ctx incluye:
  - session: AsyncSession (DB del bot, NO la de mesas)
  - cliente_id: int
  - cliente_numero: str (E.164)
  - outbox: list — avisos al grupo del equipo, drenados post-commit
"""

from __future__ import annotations

from typing import Awaitable, Callable

from app.integrations import cantina_api
from app.logging_setup import log


TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "consultar_disponibilidad",
        "description": (
            "Consulta qué mesas están libres para una fecha (y opcionalmente N personas). "
            "Devuelve evento del día (si hay), mesas libres filtradas por capacidad+VIP, "
            "sugerencia de combo si el grupo es grande, salas privadas disponibles y "
            "URL del plano del salón. SIEMPRE úsala antes de ofrecer mesas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "personas": {"type": "integer", "description": "tamaño del grupo (opcional)"},
            },
            "required": ["fecha"],
        },
    },
    {
        "name": "consultar_evento",
        "description": (
            "Si el cliente pregunta por un evento/show, devuelve nombre, artista, cover "
            "por persona, link de pago o indica que no hay evento ese día."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"fecha": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["fecha"],
        },
    },
    {
        "name": "crear_reserva",
        "description": (
            "Reserva UNA mesa. Antes DEBES haber llamado consultar_disponibilidad. "
            "Si hay evento con cover, el backend lo aplica automáticamente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string"},
                "mesa_id": {"type": "integer"},
                "nombre_cliente": {"type": "string"},
                "telefono": {"type": "string", "description": "Opcional, default = chat actual"},
                "num_personas": {"type": "integer"},
                "notas": {"type": "string"},
            },
            "required": ["fecha", "mesa_id", "nombre_cliente", "num_personas"],
        },
    },
    {
        "name": "crear_reserva_grupo",
        "description": (
            "Reserva un GRUPO sobre varias mesas vecinas (cuando el grupo no cabe en "
            "una sola). Mesas deben ser contiguas — el backend valida. Las mesas se "
            "toman de `consultar_disponibilidad → combo_sugerido`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string"},
                "mesa_numeros": {
                    "type": "array", "items": {"type": "integer"},
                    "description": "Números de mesa (no IDs)",
                },
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
            "Reserva una SALA PRIVADA (1 o 2). 10p c/u, mínimo de consumo $1.000.000 "
            "(gastable, no es cobro extra), SIN cover. Ofrécela para privacidad o "
            "grupos exclusivos."
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
        "name": "consultar_reserva_cliente",
        "description": "Detalle de una reserva por id (cliente preguntando por la suya).",
        "input_schema": {
            "type": "object",
            "properties": {"reserva_id": {"type": "integer"}},
            "required": ["reserva_id"],
        },
    },
    {
        "name": "registrar_comprobante_cover",
        "description": (
            "Cuando el cliente envía comprobante de pago del cover, marca la reserva "
            "como 'anticipado' (pendiente verificación humana) y escala al equipo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reserva_id": {"type": "integer"},
                "comprobante_url": {"type": "string"},
            },
            "required": ["reserva_id", "comprobante_url"],
        },
    },
    {
        "name": "escalar_a_equipo",
        "description": (
            "Avisa al equipo cuando el caso está fuera de alcance: queja, evento privado, "
            "grupo corporativo, pago dudoso, pide hablar con humano, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tipo": {
                    "type": "string",
                    "enum": [
                        "pide_humano", "queja", "fuera_de_alcance",
                        "cover_no_coincide", "mesa_no_disponible", "evento_privado",
                        "otro",
                    ],
                },
                "mensaje": {"type": "string"},
            },
            "required": ["tipo", "mensaje"],
        },
    },
]


# ── HANDLERS ────────────────────────────────────────────────────────────────


async def handler_consultar_disponibilidad(args: dict, ctx: dict) -> dict:
    fecha = args.get("fecha")
    if not fecha:
        return {"ok": False, "error": "fecha requerida (YYYY-MM-DD)"}
    return await cantina_api.disponibilidad(fecha, args.get("personas"))


async def handler_consultar_evento(args: dict, ctx: dict) -> dict:
    return await cantina_api.consultar_evento(args.get("fecha"))


async def handler_crear_reserva(args: dict, ctx: dict) -> dict:
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "mesa_id": args.get("mesa_id"),
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": args.get("telefono") or ctx.get("cliente_numero"),
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    res = await cantina_api.crear_reserva(payload)
    if res.get("ok"):
        outbox = ctx.get("outbox")
        if isinstance(outbox, list):
            outbox.append({
                "tipo": "reserva_nueva",
                "mensaje": (
                    f"🪑 Reserva nueva: {args.get('nombre_cliente')} · "
                    f"{args.get('num_personas')}p · {args.get('fecha')}"
                ),
            })
    return res


async def handler_crear_reserva_grupo(args: dict, ctx: dict) -> dict:
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "mesa_numeros": args.get("mesa_numeros") or [],
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": args.get("telefono") or ctx.get("cliente_numero"),
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    return await cantina_api.crear_reserva_grupo(payload)


async def handler_crear_reserva_sala(args: dict, ctx: dict) -> dict:
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "sala_id": args.get("sala_id"),
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": args.get("telefono") or ctx.get("cliente_numero"),
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    return await cantina_api.crear_reserva_sala(payload)


async def handler_consultar_reserva_cliente(args: dict, ctx: dict) -> dict:
    return await cantina_api.detalle_reserva(args.get("reserva_id"))


async def handler_registrar_comprobante_cover(args: dict, ctx: dict) -> dict:
    reserva_id = args.get("reserva_id")
    url = args.get("comprobante_url")
    if not (reserva_id and url):
        return {"ok": False, "error": "reserva_id y comprobante_url son requeridos"}
    res = await cantina_api.actualizar_reserva(reserva_id, {
        "cover_estado": "anticipado",
        "notas": f"Comprobante: {url}",
    })
    outbox = ctx.get("outbox")
    if isinstance(outbox, list):
        outbox.append({
            "tipo": "comprobante_cover",
            "mensaje": f"💸 Comprobante de cover — reserva #{reserva_id}\nVerificar: {url}",
            "media_url": url,
        })
    return res


async def handler_escalar_a_equipo(args: dict, ctx: dict) -> dict:
    outbox = ctx.get("outbox")
    if isinstance(outbox, list):
        outbox.append({
            "tipo": args.get("tipo", "otro"),
            "mensaje": args.get("mensaje") or "Escalación sin detalle",
            "cliente_id": ctx.get("cliente_id"),
            "cliente_numero": ctx.get("cliente_numero"),
        })
    log.info("tools.escalar_a_equipo",
             tipo=args.get("tipo"), cliente_id=ctx.get("cliente_id"))
    return {"ok": True, "escalado": True}


# ── DISPATCHER ──────────────────────────────────────────────────────────────

Handler = Callable[[dict, dict], Awaitable[dict]]

HANDLERS: dict[str, Handler] = {
    "consultar_disponibilidad": handler_consultar_disponibilidad,
    "consultar_evento": handler_consultar_evento,
    "crear_reserva": handler_crear_reserva,
    "crear_reserva_grupo": handler_crear_reserva_grupo,
    "crear_reserva_sala_privada": handler_crear_reserva_sala,
    "consultar_reserva_cliente": handler_consultar_reserva_cliente,
    "registrar_comprobante_cover": handler_registrar_comprobante_cover,
    "escalar_a_equipo": handler_escalar_a_equipo,
}


async def ejecutar_tool(name: str, args: dict, ctx: dict) -> dict:
    handler = HANDLERS.get(name)
    if not handler:
        return {"error": f"Tool desconocida: {name}"}
    try:
        return await handler(args, ctx)
    except Exception as e:
        log.exception("tools.exec.fail", tool=name, error=str(e))
        return {"error": str(e)[:200]}
