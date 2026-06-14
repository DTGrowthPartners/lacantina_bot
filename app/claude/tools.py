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

import re
from typing import Awaitable, Callable

from app.integrations import cantina_api
from app.logging_setup import log


def _mismo_telefono(a: str | None, b: str | None) -> bool:
    """True si dos teléfonos son el mismo número (compara solo dígitos, últimos 10).
    Se usa para verificar que una reserva pertenece al cliente que pregunta."""
    da = re.sub(r"\D", "", a or "")
    db = re.sub(r"\D", "", b or "")
    return bool(da) and bool(db) and da[-10:] == db[-10:]


def _extraer_reserva(res: dict) -> dict:
    """Saca el dict de la reserva de una respuesta del backend (varias formas)."""
    if not isinstance(res, dict):
        return {}
    for k in ("reserva", "data"):
        if isinstance(res.get(k), dict):
            return res[k]
    return res


def _normalizar_nombre(valor: str | None) -> str:
    return re.sub(r"\s+", " ", (valor or "")).strip()


def _validar_nombre_reserva(args: dict, ctx: dict) -> dict | None:
    """Impide usar el pushname de WhatsApp o un nombre inferido por el modelo."""
    confirmado = _normalizar_nombre(ctx.get("nombre_reserva_confirmado"))
    if not confirmado:
        return {
            "ok": False,
            "falta_nombre_confirmado": True,
            "error": (
                "Antes de reservar debes preguntarle al cliente exactamente "
                "\"¿A nombre de quién hago la reserva?\" y esperar su respuesta. "
                "No uses el nombre del perfil de WhatsApp."
            ),
        }
    args["nombre_cliente"] = confirmado
    return None


def _formatear_cop(valor) -> str:
    try:
        return f"${int(float(valor)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(valor or "No informado")


def _formatear_alerta_reserva(tipo: str, args: dict, res: dict, telefono: str) -> str:
    reserva = _extraer_reserva(res)
    if isinstance(res.get("reservas"), list) and res["reservas"]:
        primera = res["reservas"][0]
        if isinstance(primera, dict):
            reserva = primera

    titulos = {
        "simple": "🪑 *Nueva reserva de mesa*",
        "grupo": "🪑 *Nueva reserva de grupo*",
        "sala": "🥂 *Nueva reserva de sala privada*",
    }
    mesas = (
        args.get("mesa_numeros")
        or res.get("mesas")
        or reserva.get("mesa_numero")
        or args.get("mesa_id")
    )
    sala = reserva.get("sala_nombre") or args.get("sala_id")
    identificador = (
        res.get("grupo_id")
        or reserva.get("grupo_id")
        or reserva.get("id")
        or res.get("id")
    )
    estado = reserva.get("estado") or res.get("estado") or "confirmada"
    zona = reserva.get("mesa_zona") or reserva.get("zona") or res.get("zona")
    cover_estado = reserva.get("cover_estado") or res.get("cover_estado")
    monto_cover = reserva.get("monto_cover") or res.get("monto_cover")

    lineas = [
        titulos[tipo],
        "",
        f"👤 *A nombre de:* {args.get('nombre_cliente')}",
        f"📱 *Teléfono:* {telefono}",
        f"📅 *Fecha:* {args.get('fecha')}",
        f"👥 *Personas:* {args.get('num_personas')}",
    ]
    if tipo == "sala":
        lineas.append(f"🥂 *Sala:* {sala}")
    else:
        etiqueta = "Mesas" if isinstance(mesas, list) else "Mesa"
        valor_mesas = ", ".join(str(m) for m in mesas) if isinstance(mesas, list) else mesas
        lineas.append(f"🪑 *{etiqueta}:* {valor_mesas}")
    if zona:
        lineas.append(f"📍 *Zona:* {zona}")
    if identificador is not None:
        lineas.append(f"🔖 *ID:* {identificador}")
    lineas.append(f"✅ *Estado:* {estado}")
    if tipo == "sala" or not (cover_estado or monto_cover):
        lineas.append("🎟️ *Cover:* No aplica")
    else:
        detalle_cover = str(cover_estado or "pendiente")
        if monto_cover:
            detalle_cover += f" · {_formatear_cop(monto_cover)}"
        lineas.append(f"🎟️ *Cover:* {detalle_cover}")
    if args.get("notas"):
        lineas.append(f"📝 *Notas:* {args['notas']}")
    return "\n".join(str(linea) for linea in lineas)


async def _cliente_ya_reservo(fecha: str | None, telefono: str | None) -> list | None:
    """Mesas que el cliente (por teléfono) YA tiene reservadas esa fecha, o None.

    Evita el bug de reservas duplicadas: si el bot vuelve a 'apartar' tras haber
    reservado, ve sus propias mesas como ocupadas y crea otra reserva en mesas
    distintas. Este guardia lo bloquea a nivel de tool.
    """
    if not (fecha and telefono):
        return None
    resp = await cantina_api.listar_reservas(fecha)
    if not isinstance(resp, dict):
        return None
    data = resp.get("data", resp)
    reservas = data.get("reservas") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    mesas = [
        r.get("mesa_numero") for r in (reservas or [])
        if isinstance(r, dict) and r.get("estado") != "cancelada"
        and _mismo_telefono(r.get("telefono"), telefono)
    ]
    return mesas or None


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
            "Si el cliente pregunta por el evento/show de una FECHA concreta, devuelve "
            "nombre, artista, cover por persona, link de pago o indica que no hay evento "
            "ese día. Si hay evento con flyer, este se le envía al cliente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"fecha": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["fecha"],
        },
    },
    {
        "name": "proximos_eventos",
        "description": (
            "Lista los PRÓXIMOS eventos/shows (de hoy en adelante). Úsalo cuando el "
            "cliente pregunte 'cuándo es el próximo evento', 'qué shows vienen', etc."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "crear_reserva",
        "description": (
            "Reserva UNA mesa. Antes DEBES haber llamado consultar_disponibilidad. "
            "Antes de usarla DEBES preguntar explícitamente a nombre de quién va la "
            "reserva y esperar la respuesta. NUNCA uses el nombre del perfil de WhatsApp. "
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
            "toman de `consultar_disponibilidad → combo_sugerido`. Antes de usarla "
            "DEBES preguntar a nombre de quién va la reserva y esperar la respuesta; "
            "NUNCA uses el nombre del perfil de WhatsApp."
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
            "grupos exclusivos. Antes de usarla DEBES preguntar a nombre de quién va "
            "la reserva y esperar la respuesta; NUNCA uses el nombre del perfil de WhatsApp."
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
        "description": (
            "Busca y confirma las reservas del propio cliente usando automáticamente "
            "el teléfono del chat. Úsala cuando pregunte si su reserva sigue activa, "
            "quiera confirmarla o pida sus datos, aunque hayan pasado días. NO le pidas "
            "el ID: reserva_id es opcional y solo se usa si el cliente ya lo dio."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reserva_id": {
                    "type": "integer",
                    "description": "Opcional; no se lo pidas al cliente.",
                },
            },
        },
    },
    {
        "name": "registrar_comprobante_cover",
        "description": (
            "Cuando el cliente envía comprobante de pago del cover, marca la reserva "
            "como 'anticipado' (pendiente verificación humana) y escala al equipo. "
            "La imagen actual se toma automáticamente del contexto; NO inventes ni "
            "pidas una URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reserva_id": {"type": "integer"},
            },
            "required": ["reserva_id"],
        },
    },
    {
        "name": "enviar_como_llegar",
        "description": (
            "Envía al cliente el VIDEO de cómo llegar a La Cantina. Úsalo cuando "
            "pregunte por la ubicación, dónde queda, dónde están ubicados, la "
            "dirección o cómo llegar. ADEMÁS dale la dirección por texto (con el "
            "punto de referencia: frente al Banco Popular). Llámalo una sola vez "
            "por conversación salvo que lo vuelva a pedir."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "enviar_carta",
        "description": (
            "Envía al cliente el LINK del menú digital (siempre actualizado). "
            "Úsalo cuando pregunte por bebidas, tragos, licores, comida, la carta "
            "o el menú. Acompáñalo con un mensaje breve (puedes mencionar 1-2 "
            "opciones o invitar a verlo). Llámalo una sola vez por conversación "
            "salvo que lo vuelva a pedir."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "enviar_plano_espacio",
        "description": (
            "Envía al cliente el plano del salón de La Cantina con las mesas ya "
            "reservadas marcadas en rojo para la fecha indicada. Úsalo cuando "
            "pregunte por el espacio, cómo es el lugar/salón, dónde está una mesa, "
            "la ubicación de las mesas, el mapa/plano o la distribución. "
            "(OJO: NO es lo mismo que 'cómo llegar'/dirección → para eso usa "
            "`enviar_como_llegar`.) JUNTO con la foto, en tu texto describe "
            "brevemente cada zona: *Cantina* (mesas 1–16, máximo 6p), *VIP* "
            "(mesas 17–25; 17, 18, 24 y 25 máximo 8p, las demás 6p) y "
            "*Rumbero* (mesas 26–42, máximo 6p), y menciona barra, tarima y "
            "baños. Cierra invitando al "
            "cliente a escoger una mesa o zona para reservar. Llámalo una sola "
            "vez por conversación salvo que lo vuelva a pedir."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "YYYY-MM-DD de la noche a consultar. "
                                   "Si no se mencionó fecha, usa hoy.",
                },
            },
        },
    },
    {
        "name": "enviar_estado_actual",
        "description": (
            "Envía al cliente la IMAGEN o VIDEO del estado/promo vigente de La Cantina "
            "(lo último que el equipo publicó en el estado de WhatsApp). Úsalo cuando el "
            "cliente pida 'el estado', 'la promo', 'la imagen/video que subieron', 'lo "
            "que publicaron', el flyer de la promo, etc. Si NO hay un estado vigente "
            "guardado, la tool te lo dirá: en ese caso avísale con amabilidad que por "
            "ahora no hay una promo publicada. Acompaña el envío con un texto breve."
        ),
        "input_schema": {"type": "object", "properties": {}},
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
    res = await cantina_api.disponibilidad(fecha, args.get("personas"))
    # El backend devuelve total_disponibles=0 cuando NINGUNA mesa SOLA cabe el
    # grupo (grupos grandes, 8+), pero igual hay disponibilidad combinando mesas
    # o en salas privadas. Sin esta señal el bot lee "0" y dice "no hay". 🚫
    if isinstance(res, dict) and res.get("ok", True):
        total = res.get("total_disponibles") or 0
        combos = res.get("combos") or []
        salas = (res.get("salas_privadas") or {}).get("disponibles") or []
        res["hay_disponibilidad"] = bool(total) or bool(combos) or bool(salas)
        if not total and (combos or salas):
            res["nota_bot"] = (
                "SÍ hay disponibilidad. El grupo no cabe en UNA sola mesa, así que "
                "ofrece COMBINAR mesas (usa `combo_sugerido`/`combos` con "
                "`crear_reserva_grupo`) o una SALA PRIVADA. NUNCA digas que no hay "
                "disponibilidad ni que está lleno."
            )
        # Si el cliente ya tiene reserva para esta fecha, señalarlo claramente para
        # que el bot no confunda su propia mesa ocupada con una mesa ajena. 🚫
        ya = await _cliente_ya_reservo(fecha, ctx.get("cliente_numero"))
        if ya:
            res["reserva_propia"] = ya
            res["nota_reserva_propia"] = (
                f"ATENCIÓN: este cliente YA tiene reserva confirmada para {fecha} "
                f"en mesa(s) {ya}. NO ofrezcas más mesas ni digas que están ocupadas: "
                "confírmale su reserva existente y pregunta si necesita algo más."
            )
    return res


async def handler_consultar_evento(args: dict, ctx: dict) -> dict:
    fecha = args.get("fecha")
    res = await cantina_api.consultar_evento(fecha)
    if isinstance(res, dict) and res.get("ok") and fecha:
        # Marca la fecha para que el flow envíe el flyer (si existe).
        ctx["flyer_evento_fecha"] = fecha
        # Descripción local del evento (el backend no la guarda).
        try:
            from pathlib import Path
            from app.config import get_settings
            d = Path(get_settings().data_dir) / "media" / "flyers" / f"{fecha}.txt"
            if d.exists():
                res["descripcion"] = d.read_text(encoding="utf-8")
        except Exception:
            pass
    return res


async def handler_proximos_eventos(args: dict, ctx: dict) -> dict:
    """Lista eventos de hoy en adelante (para 'cuándo es el próximo evento')."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    hoy = datetime.now(ZoneInfo("America/Bogota")).date().isoformat()
    res = await cantina_api.listar_eventos()
    if not (isinstance(res, dict) and res.get("ok", True)):
        return res
    data = res.get("data", res)
    evs = data.get("eventos") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    proximos = [
        {"fecha": e.get("fecha"), "nombre": e.get("nombre"), "artista": e.get("artista"),
         "tiene_cover": e.get("tiene_cover"), "valor_cover": e.get("valor_cover")}
        for e in (evs or []) if isinstance(e, dict) and str(e.get("fecha") or "") >= hoy
    ]
    proximos.sort(key=lambda e: str(e.get("fecha") or ""))
    return {"ok": True, "proximos_eventos": proximos[:10]}


async def handler_crear_reserva(args: dict, ctx: dict) -> dict:
    error_nombre = _validar_nombre_reserva(args, ctx)
    if error_nombre:
        return error_nombre
    tel = args.get("telefono") or ctx.get("cliente_numero")
    ya = await _cliente_ya_reservo(args.get("fecha"), tel)
    if ya:
        return {"ok": False, "ya_reservado": True, "mesas": ya,
                "error": f"Este cliente YA tiene una reserva para {args.get('fecha')} "
                         f"(mesa(s) {ya}). NO crees otra: confírmale ESA reserva. Si de "
                         f"verdad necesita una mesa adicional, escala al equipo."}
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "mesa_id": args.get("mesa_id"),
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": tel,
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    res = await cantina_api.crear_reserva(payload)
    if res.get("ok"):
        outbox = ctx.get("outbox")
        if isinstance(outbox, list):
            outbox.append({
                "tipo": "reserva_nueva",
                "mensaje": _formatear_alerta_reserva("simple", args, res, tel),
            })
    return res


async def handler_crear_reserva_grupo(args: dict, ctx: dict) -> dict:
    error_nombre = _validar_nombre_reserva(args, ctx)
    if error_nombre:
        return error_nombre
    tel = args.get("telefono") or ctx.get("cliente_numero")
    ya = await _cliente_ya_reservo(args.get("fecha"), tel)
    if ya:
        return {"ok": False, "ya_reservado": True, "mesas": ya,
                "error": f"Este cliente YA tiene una reserva para {args.get('fecha')} "
                         f"(mesa(s) {ya}). NO crees otra: confírmale ESA. Si necesita más "
                         f"mesas, escala al equipo."}
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "mesa_numeros": args.get("mesa_numeros") or [],
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": tel,
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    res = await cantina_api.crear_reserva_grupo(payload)
    if isinstance(res, dict) and res.get("ok"):
        outbox = ctx.get("outbox")
        if isinstance(outbox, list):
            outbox.append({
                "tipo": "reserva_nueva",
                "mensaje": _formatear_alerta_reserva("grupo", args, res, tel),
            })
    return res


async def handler_crear_reserva_sala(args: dict, ctx: dict) -> dict:
    error_nombre = _validar_nombre_reserva(args, ctx)
    if error_nombre:
        return error_nombre
    tel = args.get("telefono") or ctx.get("cliente_numero")
    ya = await _cliente_ya_reservo(args.get("fecha"), tel)
    if ya:
        return {"ok": False, "ya_reservado": True, "mesas": ya,
                "error": f"Este cliente YA tiene una reserva para {args.get('fecha')} "
                         f"(mesa(s) {ya}). NO crees otra: confírmale ESA. Si necesita más, "
                         f"escala al equipo."}
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "sala_id": args.get("sala_id"),
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": tel,
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    res = await cantina_api.crear_reserva_sala(payload)
    if isinstance(res, dict) and res.get("ok"):
        outbox = ctx.get("outbox")
        if isinstance(outbox, list):
            outbox.append({
                "tipo": "reserva_nueva",
                "mensaje": _formatear_alerta_reserva("sala", args, res, tel),
            })
    return res


async def handler_consultar_reserva_cliente(args: dict, ctx: dict) -> dict:
    """Busca por teléfono o consulta por ID, siempre limitada al propio cliente.

    PRIVACIDAD: sin esta verificación, un cliente podría pedir la reserva #N de
    otra persona y ver su nombre/teléfono. Si el teléfono de la reserva no
    coincide con el del chat, no devolvemos datos.
    """
    if not args.get("reserva_id"):
        precargadas = ctx.get("reservas_cliente_precargadas")
        if precargadas is None:
            res = await cantina_api.reservas_cliente(ctx.get("cliente_numero"))
            if not (isinstance(res, dict) and res.get("ok", True)):
                return res
            reservas = res.get("reservas") or []
        else:
            reservas = precargadas
        return {
            "ok": True,
            "reservas": reservas,
            "total": len(reservas),
            "instruccion": (
                "Si hay una sola reserva, confírmala directamente con fecha, nombre, "
                "personas y mesa(s) o sala. Si hay varias, pregunta únicamente por cuál "
                "fecha consulta. Si no hay ninguna, indícalo con amabilidad. NUNCA pidas ID."
            ),
        }
    res = await cantina_api.detalle_reserva(args.get("reserva_id"))
    if not (isinstance(res, dict) and res.get("ok", True)):
        return res
    reserva = _extraer_reserva(res)
    if not _mismo_telefono(reserva.get("telefono"), ctx.get("cliente_numero")):
        log.warning("tools.reserva_ajena_bloqueada",
                    reserva_id=args.get("reserva_id"), cliente=ctx.get("cliente_numero"))
        return {"ok": False, "ajena": True,
                "error": "Esa reserva no está a tu número. Por privacidad solo puedo darte "
                         "información de TU propia reserva."}
    return res


async def handler_registrar_comprobante_cover(args: dict, ctx: dict) -> dict:
    reserva_id = args.get("reserva_id")
    url = args.get("comprobante_url") or ctx.get("incoming_media_url")
    if not (reserva_id and url):
        return {
            "ok": False,
            "error": "reserva_id y una imagen de comprobante son requeridos",
        }
    # Verificar que la reserva sea del cliente antes de tocarla.
    det = await cantina_api.detalle_reserva(reserva_id)
    if isinstance(det, dict) and det.get("ok", True):
        reserva = _extraer_reserva(det)
        if reserva and not _mismo_telefono(reserva.get("telefono"), ctx.get("cliente_numero")):
            log.warning("tools.comprobante_reserva_ajena", reserva_id=reserva_id,
                        cliente=ctx.get("cliente_numero"))
            return {"ok": False, "error": "Esa reserva no está a tu número; no puedo "
                                          "registrar el comprobante. Verifica el número de reserva."}
    res = await cantina_api.actualizar_reserva(reserva_id, {
        "cover_estado": "anticipado",
        "notas": f"Comprobante: {url}",
    })
    outbox = ctx.get("outbox")
    if isinstance(outbox, list):
        item = next(
            (x for x in outbox if x.get("tipo") == "comprobante_cover"),
            None,
        )
        mensaje = (
            f"💸 *Comprobante de cover recibido*\n"
            f"Reserva: #{reserva_id}\n"
            f"Cliente: {ctx.get('cliente_numero') or '?'}\n\n"
            "Para aprobarlo, menciona a Nicky y dile: "
            f"“confirma el pago de la reserva #{reserva_id} y avísale al cliente”."
        )
        if item is None:
            outbox.append({
                "tipo": "comprobante_cover",
                "mensaje": mensaje,
                "media_url": url,
                "media_bytes": ctx.get("incoming_media_bytes"),
                "media_mime": ctx.get("incoming_media_mime"),
                "cliente_numero": ctx.get("cliente_numero"),
            })
        else:
            item.update({
                "mensaje": mensaje,
                "media_url": url,
                "reserva_id": reserva_id,
            })
    return res


async def handler_enviar_como_llegar(args: dict, ctx: dict) -> dict:
    """Marca que hay que enviar el video de cómo llegar. El envío real lo hace el
    flow DESPUÉS del mensaje de texto (para que llegue dirección y luego video)."""
    ctx["enviar_video_como_llegar"] = True
    log.info("tools.enviar_como_llegar", cliente=ctx.get("cliente_numero"))
    return {"ok": True, "nota": "El video de cómo llegar se enviará junto con tu respuesta."}


async def handler_enviar_carta(args: dict, ctx: dict) -> dict:
    """Marca que hay que enviar la carta/menú en PDF (tras el texto)."""
    ctx["enviar_carta_pdf"] = True
    log.info("tools.enviar_carta", cliente=ctx.get("cliente_numero"))
    return {"ok": True, "nota": "La carta en PDF se enviará junto con tu respuesta."}


async def handler_enviar_plano_espacio(args: dict, ctx: dict) -> dict:
    """Marca que hay que enviar el plano del salón con mesas reservadas (tras el texto)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    fecha = args.get("fecha") or datetime.now(ZoneInfo("America/Bogota")).date().isoformat()
    ctx["enviar_plano_espacio"] = True
    ctx["plano_fecha"] = fecha
    log.info("tools.enviar_plano_espacio", cliente=ctx.get("cliente_numero"), fecha=fecha)
    return {
        "ok": True,
        "fecha": fecha,
        "nota": "El plano del salón (con mesas reservadas marcadas en rojo) se enviará "
                "junto con tu respuesta. Describe cada zona e invita a escoger mesa.",
    }


async def handler_enviar_estado_actual(args: dict, ctx: dict) -> dict:
    """Marca que hay que enviar la imagen del estado/promo vigente (tras el texto).
    Si no hay estado guardado, lo informa para que el bot avise al cliente."""
    from app import promo_estado
    if promo_estado.cargar_estado() is None:
        return {"ok": False, "sin_estado": True,
                "nota": "No hay un estado/promo vigente guardado. Avísale al cliente "
                        "con amabilidad que por ahora no hay una promo publicada."}
    ctx["enviar_estado_actual"] = True
    log.info("tools.enviar_estado_actual", cliente=ctx.get("cliente_numero"))
    return {"ok": True, "nota": "La imagen del estado/promo se enviará junto con tu respuesta."}


async def handler_escalar_a_equipo(args: dict, ctx: dict) -> dict:
    # Dedup intra-turno: Claude a veces llama esta tool varias veces dentro del
    # mismo tool-loop (hasta 5 rondas) → reenviaba el MISMO aviso al grupo 5
    # veces. Con una sola escalación por turno basta.
    if ctx.get("_ya_escalo"):
        log.info("tools.escalar_a_equipo.dedup", cliente_id=ctx.get("cliente_id"))
        return {
            "ok": True,
            "escalado": True,
            "nota": "Ya se avisó al equipo en este turno. NO vuelvas a escalar; "
                    "solo responde al cliente con calma que el equipo lo verificará.",
        }
    ctx["_ya_escalo"] = True
    outbox = ctx.get("outbox")
    if isinstance(outbox, list):
        if ctx.get("intent") == "envia_comprobante_pago":
            comprobante = next(
                (x for x in outbox if x.get("tipo") == "comprobante_cover"),
                None,
            )
            if comprobante is not None:
                detalle = (args.get("mensaje") or "").strip()
                if detalle:
                    comprobante["mensaje"] = (
                        f"{comprobante['mensaje']}\n\nDetalle detectado:\n{detalle}"
                    )
                log.info(
                    "tools.escalar_a_equipo.comprobante_enriquecido",
                    cliente_id=ctx.get("cliente_id"),
                )
                return {"ok": True, "escalado": True, "comprobante_adjuntado": True}
        outbox.append({
            "clase": "escalacion",
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
    "proximos_eventos": handler_proximos_eventos,
    "crear_reserva": handler_crear_reserva,
    "crear_reserva_grupo": handler_crear_reserva_grupo,
    "crear_reserva_sala_privada": handler_crear_reserva_sala,
    "consultar_reserva_cliente": handler_consultar_reserva_cliente,
    "registrar_comprobante_cover": handler_registrar_comprobante_cover,
    "enviar_como_llegar": handler_enviar_como_llegar,
    "enviar_carta": handler_enviar_carta,
    "enviar_plano_espacio": handler_enviar_plano_espacio,
    "enviar_estado_actual": handler_enviar_estado_actual,
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
