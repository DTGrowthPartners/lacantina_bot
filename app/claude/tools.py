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
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from app.integrations import cantina_api
from app.eventos import clave_orden_evento, extraer_eventos
from app.event_media import leer_descripcion_evento
from app.logging_setup import log
from app.nombres import limpiar_nombre_reserva, validar_nombre_reserva


_POLITICA_HORARIO_COVER = (
    "REGLA OBLIGATORIA: en días de evento el cover aplica desde la hora de inicio "
    "del evento. Entrar antes NO exime del cover si la persona se queda durante "
    "el evento; solo no paga cover quien se retira antes de que empiece. "
    "Acláralo siempre al cliente "
    "cuando menciones el cover."
)


def _formatear_hora_cover(raw: object) -> str | None:
    hora = str(raw or "").strip()
    if not hora:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            dt = datetime.strptime(hora, fmt)
            sufijo = "a. m." if dt.hour < 12 else "p. m."
            hora12 = dt.hour % 12 or 12
            return f"{hora12}:{dt.minute:02d} {sufijo}"
        except ValueError:
            pass
    return hora


def _politica_horario_cover_desde(candidatos: list[dict]) -> str:
    for item in candidatos:
        tiene_cover = (
            item.get("tiene_cover") is True
            or item.get("cover_estado") in {"pendiente", "anticipado", "en_entrada"}
            or bool(item.get("monto_cover"))
        )
        if not tiene_cover:
            continue
        hora = _formatear_hora_cover(item.get("hora_inicio") or item.get("hora"))
        if hora:
            return (
                f"REGLA OBLIGATORIA: en este evento el cover aplica desde las {hora}, "
                "hora de inicio del evento. Entrar antes NO exime del cover si la "
                "persona se queda durante el evento; solo no paga cover quien se "
                "retira antes de esa hora. "
                "Acláralo siempre al cliente cuando menciones el cover."
            )
    return _POLITICA_HORARIO_COVER


def _anotar_politica_horario_cover(res: dict) -> dict:
    """Inyecta la política cuando una respuesta de la API contiene cover."""
    if not isinstance(res, dict):
        return res

    candidatos = [res]
    for clave in ("data", "evento", "reserva"):
        valor = res.get(clave)
        if isinstance(valor, dict):
            candidatos.append(valor)
    data = res.get("data")
    if isinstance(data, dict) and isinstance(data.get("evento"), dict):
        candidatos.append(data["evento"])
    if isinstance(data, dict) and isinstance(data.get("eventos"), list):
        candidatos.extend(v for v in data["eventos"] if isinstance(v, dict))
    for clave in ("reservas", "eventos"):
        valores = res.get(clave)
        if isinstance(valores, list):
            candidatos.extend(v for v in valores if isinstance(v, dict))

    aplica = bool(res.get("cover")) or any(
        item.get("tiene_cover") is True
        or item.get("cover_estado") in {"pendiente", "anticipado", "en_entrada"}
        or bool(item.get("monto_cover"))
        for item in candidatos
    )
    if aplica:
        res["politica_horario_cover"] = _politica_horario_cover_desde(candidatos)
    return res


def _es_bloqueo_casa_llena(res: dict | None) -> bool:
    if not isinstance(res, dict):
        return False
    if res.get("casa_llena") or res.get("bloqueo_casa_llena"):
        return True
    texto = " ".join(
        str(res.get(k) or "")
        for k in ("error", "mensaje", "detail", "motivo", "razon")
    ).casefold()
    return any(
        marca in texto
        for marca in (
            "casa llena",
            "reservas cerradas",
            "cierre de reservas",
            "cierre activo",
            "cerrado por aforo",
        )
    )


def _respuesta_cliente_casa_llena(fecha: str | None = None) -> str:
    fecha_txt = f" para esa fecha ({fecha})" if fecha else " para esa fecha"
    return (
        "Gracias por querer reservar con nosotros. "
        f"Por ahora ya estamos en casa llena{fecha_txt} y no podemos recibir "
        "más reservas. Lo sentimos mucho; será un gusto recibirte en una próxima ocasión."
    )


def _normalizar_casa_llena_cliente(res: dict, fecha: str | None = None) -> dict:
    if not _es_bloqueo_casa_llena(res):
        return res
    normalizado = dict(res)
    normalizado.update({
        "ok": False,
        "casa_llena": True,
        "bloqueo_casa_llena": True,
        "hay_disponibilidad": False,
        "total_disponibles": 0,
        "mesas_disponibles": [],
        "combos": [],
        "combo_sugerido": None,
        "salas_privadas": {"disponibles": []},
        "respuesta_cliente": _respuesta_cliente_casa_llena(fecha),
        "instruccion": (
            "Responde al cliente con `respuesta_cliente`. No menciones backend, "
            "panel, reapertura, equipo ni opciones internas. No ofrezcas mesas, "
            "combos ni salas para esta fecha."
        ),
    })
    return normalizado


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


def _nombre_reserva_sospechoso(valor: str | None) -> bool:
    return not validar_nombre_reserva(valor).es_nombre


def _nombre_reserva_basura(valor: str | None) -> bool:
    return bool(_normalizar_nombre(valor)) and not validar_nombre_reserva(valor).es_nombre


def _validar_nombre_reserva(args: dict, ctx: dict) -> dict | None:
    """Impide usar el pushname de WhatsApp o un nombre inferido por el modelo."""
    validacion = validar_nombre_reserva(ctx.get("nombre_reserva_confirmado"))
    if not validacion.es_nombre:
        log.warning(
            "tools.reserva.nombre_no_confirmado",
            cliente=ctx.get("cliente_numero"),
            razon=validacion.razon,
        )
        return {
            "ok": False,
            "falta_nombre_confirmado": True,
            "error": (
                "Antes de reservar debes preguntarle al cliente exactamente "
                "\"¿A nombre de quién hago la reserva?\" y esperar su respuesta. "
                "No uses el nombre del perfil de WhatsApp. No vuelvas a intentar "
                "crear la reserva en este turno."
            ),
        }
    args["nombre_cliente"] = validacion.nombre_limpio
    return None


def _reservas_en_respuesta(res: dict) -> list[dict]:
    if not isinstance(res, dict):
        return []
    reservas = res.get("reservas")
    if isinstance(reservas, list):
        return [r for r in reservas if isinstance(r, dict)]
    reserva = _extraer_reserva(res)
    return [reserva] if reserva else []


async def _autocorregir_nombre_reserva(
    tipo: str,
    args: dict,
    ctx: dict,
    res: dict,
) -> dict:
    """Corrige nombres basura antes de notificar al grupo del equipo."""
    if not (isinstance(res, dict) and res.get("ok")):
        return res

    nombre_correcto = limpiar_nombre_reserva(ctx.get("nombre_reserva_confirmado"))
    if not nombre_correcto:
        return res

    reservas = _reservas_en_respuesta(res)
    sospechosas = [
        reserva for reserva in reservas
        if _nombre_reserva_basura(reserva.get("nombre_cliente"))
    ]
    if not sospechosas and not _nombre_reserva_basura(args.get("nombre_cliente")):
        return res

    actualizador = (
        cantina_api.actualizar_reserva_sala
        if tipo == "sala"
        else cantina_api.actualizar_reserva
    )
    ids = [r.get("id") for r in (sospechosas or reservas) if r.get("id")]
    if not ids and res.get("id"):
        ids = [res.get("id")]

    corregidas = 0
    for reserva_id in ids:
        try:
            actualizado = await actualizador(reserva_id, {"nombre_cliente": nombre_correcto})
        except Exception as e:
            log.warning(
                "tools.reserva.nombre_autocorreccion_fail",
                reserva_id=reserva_id,
                error=str(e)[:160],
            )
            continue
        if isinstance(actualizado, dict) and actualizado.get("ok", True):
            corregidas += 1

    if not corregidas:
        return res

    args["nombre_cliente"] = nombre_correcto
    for reserva in reservas:
        if reserva.get("id") in ids or _nombre_reserva_basura(reserva.get("nombre_cliente")):
            reserva["nombre_cliente"] = nombre_correcto
    if isinstance(res.get("reserva"), dict):
        res["reserva"]["nombre_cliente"] = nombre_correcto
    if isinstance(res.get("data"), dict):
        res["data"]["nombre_cliente"] = nombre_correcto
    res["nombre_autocorregido"] = True
    res["nombre_cliente"] = nombre_correcto
    log.info(
        "tools.reserva.nombre_autocorregido",
        tipo=tipo,
        cliente=ctx.get("cliente_numero"),
        reservas=ids,
        nombre=nombre_correcto,
    )
    return res


def _clave_intento_reserva(tipo: str, payload: dict) -> tuple:
    def congelar(valor):
        if isinstance(valor, list):
            return tuple(valor)
        if isinstance(valor, dict):
            return tuple(sorted((k, congelar(v)) for k, v in valor.items()))
        return valor

    return tipo, tuple(sorted((k, congelar(v)) for k, v in payload.items()))


def _resultado_intento_previo(tipo: str, payload: dict, ctx: dict) -> dict | None:
    cache = ctx.setdefault("_intentos_reserva", {})
    anterior = cache.get(_clave_intento_reserva(tipo, payload))
    if anterior is None:
        return None
    log.warning(
        "tools.reserva.reintento_omitido",
        tipo=tipo,
        cliente=ctx.get("cliente_numero"),
    )
    resultado = dict(anterior)
    resultado["reintento_omitido"] = True
    resultado["instruccion"] = (
        "Este mismo intento ya se ejecutó en este turno. No vuelvas a llamar "
        "la tool; responde usando este resultado."
    )
    return resultado


def _guardar_resultado_reserva(tipo: str, payload: dict, ctx: dict, res: dict) -> dict:
    ctx.setdefault("_intentos_reserva", {})[
        _clave_intento_reserva(tipo, payload)
    ] = dict(res)
    log.info(
        "tools.reserva.resultado",
        tipo=tipo,
        ok=bool(res.get("ok")),
        error=str(res.get("error") or "")[:160],
    )
    return res


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


def _formatear_alerta_cambio_mesa(anterior: dict, nueva: dict, telefono: str) -> str:
    return "\n".join([
        "🔄 *Reserva modificada — cambio de mesa*",
        "",
        f"👤 *A nombre de:* {nueva.get('nombre_cliente') or anterior.get('nombre_cliente')}",
        f"📱 *Teléfono:* {telefono}",
        f"📅 *Fecha:* {nueva.get('fecha') or anterior.get('fecha')}",
        f"👥 *Personas:* {nueva.get('num_personas') or anterior.get('num_personas')}",
        f"↩️ *Mesa anterior:* {anterior.get('mesa_numero')}",
        f"🪑 *Mesa nueva:* {nueva.get('mesa_numero')}",
        f"🧾 *ID anterior:* {anterior.get('id')} · cancelada",
        f"🧾 *ID actual:* {nueva.get('id')}",
        f"✅ *Estado:* {nueva.get('estado') or 'confirmada'}",
    ])


def _formatear_alerta_cambio_personas(anterior: dict, nueva: dict, telefono: str) -> str:
    return "\n".join([
        "🔄 *Reserva modificada — cantidad de personas*",
        "",
        f"👤 *A nombre de:* {nueva.get('nombre_cliente') or anterior.get('nombre_cliente')}",
        f"📱 *Teléfono:* {telefono}",
        f"📅 *Fecha:* {nueva.get('fecha') or anterior.get('fecha')}",
        f"🪑 *Mesa:* {nueva.get('mesa_numero') or anterior.get('mesa_numero')}",
        f"↩️ *Personas antes:* {anterior.get('num_personas')}",
        f"👥 *Personas ahora:* {nueva.get('num_personas')}",
        f"🧾 *ID:* {nueva.get('id') or anterior.get('id')}",
        f"✅ *Estado:* {nueva.get('estado') or anterior.get('estado') or 'confirmada'}",
    ])


def _normalizar_texto_contexto(valor: str | None) -> str:
    texto = (valor or "").casefold()
    return texto.translate(str.maketrans("áéíóúüñ", "aeiouun"))


def _textos_contexto_cliente(ctx: dict) -> list[str]:
    textos: list[str] = []
    actual = (ctx.get("mensaje_actual_cliente") or "").strip()
    if actual:
        textos.append(actual)
    historial = ctx.get("historial_cliente_reciente")
    if isinstance(historial, list):
        for item in reversed(historial):
            if isinstance(item, dict):
                contenido = (item.get("contenido") or "").strip()
            else:
                contenido = str(item or "").strip()
            if contenido and contenido not in textos:
                textos.append(contenido)
    return textos


def _fechas_en_texto(texto: str) -> list[str]:
    normalizado = _normalizar_texto_contexto(texto)
    fechas: list[str] = []
    for match in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", normalizado):
        fechas.append(f"{match.group(1)}-{match.group(2)}-{match.group(3)}")
    for match in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?\b", normalizado):
        dia = int(match.group(1))
        mes = int(match.group(2))
        year = int(match.group(3) or datetime.now(ZoneInfo("America/Bogota")).year)
        try:
            fechas.append(datetime(year, mes, dia).date().isoformat())
        except ValueError:
            pass
    hoy = datetime.now(ZoneInfo("America/Bogota")).date()
    if re.search(r"\bhoy\b", normalizado):
        fechas.append(hoy.isoformat())
    if re.search(r"\bmanana\b", normalizado):
        fechas.append((hoy + timedelta(days=1)).isoformat())
    return list(dict.fromkeys(fechas))


def _mesas_en_texto(texto: str) -> set[int]:
    normalizado = _normalizar_texto_contexto(texto)
    mesas: set[int] = set()
    for match in re.finditer(r"\bmesa(?:s)?\s*#?\s*(\d{1,3})\b", normalizado):
        mesas.add(int(match.group(1)))
    return mesas


def _inferir_reserva_desde_contexto(reservas: list[dict], ctx: dict) -> dict | None:
    """Escoge una reserva solo si el chat reciente apunta a una unica candidata."""
    if len(reservas) <= 1:
        return reservas[0] if reservas else None

    for texto in _textos_contexto_cliente(ctx):
        ids = {
            int(match.group(1))
            for match in re.finditer(
                r"\b(?:reserva\s*)?(?:id|#)\s*[:#-]?\s*(\d+)\b",
                _normalizar_texto_contexto(texto),
            )
        }
        if ids:
            candidatas = [r for r in reservas if r.get("id") in ids]
            if len(candidatas) == 1:
                return candidatas[0]

        fechas = set(_fechas_en_texto(texto))
        mesas = _mesas_en_texto(texto)
        if fechas and mesas:
            candidatas = [
                r for r in reservas
                if r.get("fecha") in fechas and r.get("mesa_numero") in mesas
            ]
            if len(candidatas) == 1:
                return candidatas[0]
        if fechas:
            candidatas = [r for r in reservas if r.get("fecha") in fechas]
            if len(candidatas) == 1:
                return candidatas[0]
        if mesas:
            candidatas = [r for r in reservas if r.get("mesa_numero") in mesas]
            if len(candidatas) == 1:
                return candidatas[0]
    return None


async def _cliente_ya_reservo(fecha: str | None, telefono: str | None) -> list | None:
    """Mesas que el cliente (por teléfono) YA tiene reservadas esa fecha, o None.

    Se usa para evitar repetir la misma mesa por accidente. El negocio sí permite
    que el mismo cliente tenga otra reserva para la misma fecha si es una mesa
    distinta.
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
            "Si el cliente pregunta por eventos/shows de una FECHA concreta, devuelve "
            "todos los eventos registrados para ese día, diferenciándolos por hora_inicio "
            "cuando exista; incluye artista, cover por persona y link de pago si aplican. "
            "Úsalo también cuando pregunte por horario, servicio o si puede "
            "llegar temprano en una fecha con posible partido/evento, porque `hora_inicio` "
            "puede indicar apertura especial antes del horario normal. Si hay flyer para "
            "la fecha, este se le envía al cliente."
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
        "name": "cambiar_mesa_reserva_cliente",
        "description": (
            "Cambia una reserva SIMPLE activa del propio cliente a otra mesa. "
            "Es la ÚNICA tool permitida para cambios de mesa: NUNCA combines "
            "cancelar_reserva_cliente + crear_reserva. Conserva automáticamente "
            "nombre, teléfono y personas; no vuelvas a preguntarlos. Es idempotente: "
            "si ya quedó en la mesa solicitada no crea ni notifica otra reserva."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "mesa_nueva": {"type": "integer", "description": "Número de la mesa destino"},
                "reserva_id": {
                    "type": "integer",
                    "description": "Opcional; no se lo pidas al cliente.",
                },
            },
            "required": ["fecha", "mesa_nueva"],
        },
    },
    {
        "name": "actualizar_personas_reserva_cliente",
        "description": (
            "Actualiza la cantidad de personas de una reserva SIMPLE activa del "
            "propio cliente. Úsala cuando el cliente pida cambiar de 2 a 4 "
            "personas, sumar/quitar personas o ajustar el número de asistentes. "
            "Si el cliente ya dio la nueva cantidad, NO pidas validación extra: "
            "ejecútala y confirma. No cambia mesa ni valida capacidad."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "num_personas": {
                    "type": "integer",
                    "description": "Nueva cantidad de personas.",
                },
                "fecha": {
                    "type": "string",
                    "description": "YYYY-MM-DD opcional; úsala si el cliente la indicó.",
                },
                "reserva_id": {
                    "type": "integer",
                    "description": "Opcional; no se lo pidas al cliente.",
                },
            },
            "required": ["num_personas"],
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
            "La imagen/PDF actual se toma automáticamente del contexto; NO inventes ni "
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
        "name": "cancelar_reserva_cliente",
        "description": (
            "Cancela una reserva activa del propio cliente. Úsala cuando responda "
            "'Cancelar' a un recordatorio o pida cancelar. Si tiene varias reservas "
            "y no indicó fecha, la tool pedirá aclarar únicamente la fecha. NUNCA la "
            "uses para cambiar de mesa; usa cambiar_mesa_reserva_cliente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "YYYY-MM-DD opcional"},
                "reserva_id": {
                    "type": "integer",
                    "description": "Opcional; nunca se lo pidas si no lo dio.",
                },
            },
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
            "Úsalo cuando pregunte por precios, productos, bebidas, tragos, licores, "
            "comida, la carta o el menú. NUNCA respondas precios ni listes productos: "
            "invítalo a consultar el link. Llámalo una sola vez por conversación "
            "salvo que lo vuelva a pedir."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "enviar_plano_espacio",
        "description": (
            "Envía al cliente el plano público del salón. NUNCA muestra las mesas "
            "ocupadas; opcionalmente destaca en verde una mesa recomendada. Úsalo cuando "
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
                "mesa_recomendada": {
                    "type": "integer",
                    "description": (
                        "Mesa libre recomendada, solo si ya consultaste disponibilidad. "
                        "No inventes una mesa."
                    ),
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
            "que publicaron', el flyer de la promo, etc. NO la uses si pide 'carta', "
            "'menú', precios, bebidas o comida: en esos casos usa `enviar_carta`. Si NO hay un estado vigente "
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
        if res.get("casa_llena"):
            res = _normalizar_casa_llena_cliente(res, fecha)
            res["nota_bot"] = res["instruccion"]
            return res
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
        # Si el cliente ya tiene reserva para esta fecha, señalarlo claramente.
        # Puede pedir una mesa adicional; solo hay que evitar repetir la misma mesa.
        ya = await _cliente_ya_reservo(fecha, ctx.get("cliente_numero"))
        if ya:
            res["reserva_propia"] = ya
            res["nota_reserva_propia"] = (
                f"ATENCIÓN: este cliente YA tiene reserva confirmada para {fecha} "
                f"en mesa(s) {ya}. Si está pidiendo OTRA mesa para la misma fecha, "
                "puedes reservarla siempre que sea una mesa distinta y esté libre. "
                "Si falta información, pregunta para cuántas personas va esa segunda "
                "mesa y si queda al mismo nombre o a nombre de otra persona. No escales "
                "al equipo solo por ser una segunda reserva del mismo teléfono."
            )
    return _anotar_politica_horario_cover(res)


async def handler_consultar_evento(args: dict, ctx: dict) -> dict:
    fecha = args.get("fecha")
    res = await cantina_api.consultar_evento(fecha)
    if isinstance(res, dict) and res.get("ok") and fecha:
        eventos = extraer_eventos(res)
        if eventos:
            eventos.sort(key=clave_orden_evento)
            for evento in eventos:
                try:
                    descripcion = leer_descripcion_evento(evento)
                    if descripcion:
                        evento["descripcion"] = descripcion
                except Exception:
                    pass
            res["eventos"] = eventos
            res["total_eventos"] = len(eventos)
            if len(eventos) > 1:
                res["nota_bot"] = (
                    "Hay varios eventos registrados para esta fecha. Menciona "
                    "todos, diferenciándolos por hora_inicio si está disponible."
                )
            ctx["flyer_eventos"] = eventos
        else:
            # Compatibilidad: si el backend no mandó lista, intenta flyer legacy por fecha.
            ctx["flyer_evento_fecha"] = fecha
    return _anotar_politica_horario_cover(res)


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
         "hora_inicio": e.get("hora_inicio"), "hora_fin": e.get("hora_fin"),
         "tiene_cover": e.get("tiene_cover"), "valor_cover": e.get("valor_cover"),
         "link_pago": e.get("link_pago")}
        for e in (evs or []) if isinstance(e, dict) and str(e.get("fecha") or "") >= hoy
    ]
    proximos.sort(key=clave_orden_evento)
    return _anotar_politica_horario_cover({
        "ok": True,
        "eventos": proximos[:10],
        "proximos_eventos": proximos[:10],
    })


async def handler_crear_reserva(args: dict, ctx: dict) -> dict:
    error_nombre = _validar_nombre_reserva(args, ctx)
    if error_nombre:
        return error_nombre
    tel = args.get("telefono") or ctx.get("cliente_numero")
    ya = await _cliente_ya_reservo(args.get("fecha"), tel)
    mesa_solicitada = args.get("mesa_id")
    mesas_ya = {str(m) for m in (ya or []) if m is not None}
    if ya and mesa_solicitada is not None and str(mesa_solicitada) in mesas_ya:
        return {"ok": False, "ya_reservado": True, "mesas": ya,
                "error": f"Este cliente YA tiene una reserva para {args.get('fecha')} "
                         f"en la mesa {mesa_solicitada}. NO crees otra reserva igual: "
                         "confírmale esa reserva existente. Si necesita una mesa adicional, "
                         "pregunta cuál mesa distinta quiere y cuántas personas van."}
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "mesa_id": args.get("mesa_id"),
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": tel,
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    previo = _resultado_intento_previo("simple", payload, ctx)
    if previo is not None:
        return previo
    res = await cantina_api.crear_reserva(payload)
    res = _normalizar_casa_llena_cliente(res, args.get("fecha"))
    res = _anotar_politica_horario_cover(res)
    res = await _autocorregir_nombre_reserva("simple", args, ctx, res)
    res = _guardar_resultado_reserva("simple", payload, ctx, res)
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
    mesas_solicitadas = [m for m in (args.get("mesa_numeros") or []) if m is not None]
    mesas_ya = {str(m) for m in (ya or []) if m is not None}
    repetidas = [m for m in mesas_solicitadas if str(m) in mesas_ya]
    if repetidas:
        return {"ok": False, "ya_reservado": True, "mesas": ya,
                "error": f"Este cliente YA tiene una reserva para {args.get('fecha')} "
                         f"en mesa(s) {repetidas}. NO crees otra reserva con las mismas "
                         "mesas: confírmale la reserva existente o pide mesas distintas "
                         "para la reserva adicional."}
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "mesa_numeros": args.get("mesa_numeros") or [],
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": tel,
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    previo = _resultado_intento_previo("grupo", payload, ctx)
    if previo is not None:
        return previo
    res = await cantina_api.crear_reserva_grupo(payload)
    res = _normalizar_casa_llena_cliente(res, args.get("fecha"))
    res = _anotar_politica_horario_cover(res)
    res = await _autocorregir_nombre_reserva("grupo", args, ctx, res)
    res = _guardar_resultado_reserva("grupo", payload, ctx, res)
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
        args["notas"] = (args.get("notas") or "") or f"Reserva adicional; ya tenía mesa(s) {ya}."
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "sala_id": args.get("sala_id"),
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": tel,
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    previo = _resultado_intento_previo("sala", payload, ctx)
    if previo is not None:
        return previo
    res = await cantina_api.crear_reserva_sala(payload)
    res = _normalizar_casa_llena_cliente(res, args.get("fecha"))
    res = await _autocorregir_nombre_reserva("sala", args, ctx, res)
    res = _guardar_resultado_reserva("sala", payload, ctx, res)
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


async def handler_cambiar_mesa_reserva_cliente(args: dict, ctx: dict) -> dict:
    """Cambia mesa sin dejar al cliente sin reserva y tolera reintentos."""
    telefono = ctx.get("cliente_numero")
    fecha = args.get("fecha")
    mesa_nueva = args.get("mesa_nueva")
    consulta = await cantina_api.reservas_cliente(telefono)
    if not (isinstance(consulta, dict) and consulta.get("ok", True)):
        return consulta

    reservas = [
        reserva for reserva in (consulta.get("reservas") or [])
        if reserva.get("fecha") == fecha
    ]
    if args.get("reserva_id"):
        reservas = [r for r in reservas if r.get("id") == args["reserva_id"]]
    if not reservas:
        return {
            "ok": False,
            "error": "No encontré una reserva activa tuya para esa fecha.",
        }
    if len(reservas) > 1:
        return {
            "ok": False,
            "requiere_aclaracion": True,
            "reservas": reservas,
            "error": (
                "Hay varias reservas activas para esa fecha. No cambies ni canceles "
                "ninguna: pregunta cuál mesa actual quiere mover."
            ),
        }

    anterior = reservas[0]
    if anterior.get("tipo_reserva") != "mesa" or anterior.get("grupo_id"):
        return {
            "ok": False,
            "requiere_equipo": True,
            "error": "Los cambios de grupos o salas debe hacerlos el equipo.",
        }
    if anterior.get("mesa_numero") == mesa_nueva:
        return {
            "ok": True,
            "modificada": False,
            "ya_estaba_en_mesa": True,
            "reserva": anterior,
            "instruccion": (
                f"La reserva ya está en la mesa {mesa_nueva}. Confírmalo sin crear "
                "otra reserva ni avisar al equipo nuevamente."
            ),
        }

    payload = {
        "fecha": anterior.get("fecha"),
        "mesa_id": mesa_nueva,
        "nombre_cliente": anterior.get("nombre_cliente"),
        "telefono": telefono,
        "num_personas": anterior.get("num_personas"),
    }
    if anterior.get("notas"):
        payload["notas"] = anterior["notas"]

    # Crear primero: si la mesa destino ya no está libre, la reserva original
    # permanece intacta. Solo la cancelamos después de asegurar la nueva.
    creada = await cantina_api.crear_reserva(payload)
    creada = _anotar_politica_horario_cover(creada)
    if not (isinstance(creada, dict) and creada.get("ok")):
        resultado = dict(creada) if isinstance(creada, dict) else {"ok": False}
        resultado["reserva_original_conservada"] = True
        resultado["instruccion"] = (
            "No se pudo cambiar la mesa. La reserva original sigue activa; "
            "díselo al cliente y no intentes cancelar ni crear por separado."
        )
        return resultado

    nueva = _extraer_reserva(creada)
    cancelada = await cantina_api.cancelar_reserva(anterior["id"])
    if not (isinstance(cancelada, dict) and cancelada.get("ok")):
        rollback = await cantina_api.cancelar_reserva(nueva.get("id"))
        if not (isinstance(rollback, dict) and rollback.get("ok")):
            outbox = ctx.get("outbox")
            if isinstance(outbox, list):
                outbox.append({
                    "clase": "escalacion",
                    "tipo": "error_sistema",
                    "cliente_numero": telefono,
                    "mensaje": (
                        "⚠️ *Revisar cambio de mesa manualmente*\n"
                        f"Cliente: {telefono}\n"
                        f"Reserva anterior: #{anterior.get('id')} mesa {anterior.get('mesa_numero')}\n"
                        f"Reserva nueva: #{nueva.get('id')} mesa {nueva.get('mesa_numero')}\n"
                        "Falló la cancelación y también la reversión automática."
                    ),
                })
        return {
            "ok": False,
            "cambio_revertido": bool(isinstance(rollback, dict) and rollback.get("ok")),
            "error": "No se pudo completar el cambio de mesa.",
            "instruccion": "No confirmes el cambio; indica que se está verificando.",
        }

    outbox = ctx.get("outbox")
    if isinstance(outbox, list):
        outbox.append({
            "tipo": "reserva_modificada",
            "mensaje": _formatear_alerta_cambio_mesa(anterior, nueva, telefono),
            "cliente_numero": telefono,
        })
    log.info(
        "tools.reserva.mesa_cambiada",
        cliente=telefono,
        reserva_anterior=anterior.get("id"),
        reserva_nueva=nueva.get("id"),
        mesa_anterior=anterior.get("mesa_numero"),
        mesa_nueva=nueva.get("mesa_numero"),
    )
    return {
        "ok": True,
        "modificada": True,
        "reserva_anterior": {
            "id": anterior.get("id"),
            "mesa_numero": anterior.get("mesa_numero"),
            "estado": "cancelada",
        },
        "reserva": nueva,
        "instruccion": (
            f"Confirma que la reserva fue MODIFICADA de la mesa "
            f"{anterior.get('mesa_numero')} a la mesa {nueva.get('mesa_numero')}. "
            "No la presentes como una reserva adicional."
        ),
    }


async def handler_actualizar_personas_reserva_cliente(args: dict, ctx: dict) -> dict:
    """Actualiza asistentes de una reserva propia sin pedir confirmación extra."""
    telefono = ctx.get("cliente_numero")
    nuevo_numero = args.get("num_personas")
    try:
        nuevo_numero = int(nuevo_numero)
    except (TypeError, ValueError):
        nuevo_numero = None
    if not nuevo_numero or nuevo_numero < 1:
        return {"ok": False, "error": "Indica una cantidad válida de personas."}

    consulta = await cantina_api.reservas_cliente(telefono)
    if not (isinstance(consulta, dict) and consulta.get("ok", True)):
        return consulta

    reservas = [
        reserva for reserva in (consulta.get("reservas") or [])
        if reserva.get("estado") != "cancelada"
    ]
    if args.get("reserva_id"):
        reservas = [r for r in reservas if r.get("id") == args["reserva_id"]]
    if args.get("fecha"):
        reservas = [r for r in reservas if r.get("fecha") == args["fecha"]]

    if not reservas:
        return {
            "ok": False,
            "error": "No encontré una reserva activa tuya con esos datos.",
        }
    if len(reservas) > 1:
        inferida = _inferir_reserva_desde_contexto(reservas, ctx)
        if inferida:
            reservas = [inferida]
    if len(reservas) > 1:
        return {
            "ok": False,
            "requiere_aclaracion": True,
            "reservas": reservas,
            "error": (
                "Hay varias reservas activas y el historial reciente no deja claro "
                "cuál actualizar. Pregunta únicamente cuál fecha o mesa quiere tocar."
            ),
        }

    anterior = reservas[0]
    if anterior.get("tipo_reserva") != "mesa" or anterior.get("grupo_id"):
        return {
            "ok": False,
            "requiere_equipo": True,
            "error": "Los cambios de personas en grupos o salas debe hacerlos el equipo.",
        }
    if anterior.get("num_personas") == nuevo_numero:
        return {
            "ok": True,
            "modificada": False,
            "ya_estaba_actualizada": True,
            "reserva": anterior,
            "instruccion": (
                f"La reserva ya está registrada para {nuevo_numero} personas. "
                "Confírmalo sin pedir validación extra."
            ),
        }

    actualizado = await cantina_api.actualizar_reserva(
        anterior["id"],
        {"num_personas": nuevo_numero},
    )
    if not (isinstance(actualizado, dict) and actualizado.get("ok", True)):
        return actualizado

    nueva = _extraer_reserva(actualizado)
    if not isinstance(nueva, dict) or not nueva.get("id"):
        nueva = dict(anterior)
    nueva["num_personas"] = nuevo_numero
    outbox = ctx.get("outbox")
    if isinstance(outbox, list):
        outbox.append({
            "tipo": "reserva_modificada",
            "mensaje": _formatear_alerta_cambio_personas(anterior, nueva, telefono),
            "cliente_numero": telefono,
        })
    log.info(
        "tools.reserva.personas_actualizadas",
        cliente=telefono,
        reserva_id=anterior.get("id"),
        antes=anterior.get("num_personas"),
        ahora=nuevo_numero,
    )
    return {
        "ok": True,
        "modificada": True,
        "reserva_anterior": anterior,
        "reserva": nueva,
        "instruccion": (
            f"Confirma que la reserva #{anterior.get('id')} quedó actualizada "
            f"a {nuevo_numero} personas. No pidas validación adicional."
        ),
    }


async def handler_registrar_comprobante_cover(args: dict, ctx: dict) -> dict:
    reserva_id = args.get("reserva_id")
    url = args.get("comprobante_url") or ctx.get("incoming_media_url")
    if not (reserva_id and url):
        return {
            "ok": False,
            "error": "reserva_id y una imagen/PDF de comprobante son requeridos",
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


async def handler_cancelar_reserva_cliente(args: dict, ctx: dict) -> dict:
    if ctx.get("intent") == "modificar_reserva":
        return {
            "ok": False,
            "usar_cambio_mesa": True,
            "error": (
                "No canceles una reserva para cambiarla de mesa. Usa "
                "cambiar_mesa_reserva_cliente; conserva nombre, personas y teléfono."
            ),
        }
    consulta = await cantina_api.reservas_cliente(ctx.get("cliente_numero"))
    if not (isinstance(consulta, dict) and consulta.get("ok", True)):
        return consulta
    reservas = consulta.get("reservas") or []
    if args.get("reserva_id"):
        reservas = [r for r in reservas if r.get("id") == args["reserva_id"]]
    if args.get("fecha"):
        reservas = [r for r in reservas if r.get("fecha") == args["fecha"]]
    if not reservas:
        return {"ok": False, "error": "No encontré una reserva activa tuya con esos datos."}
    if len(reservas) > 1:
        return {
            "ok": False,
            "requiere_fecha": True,
            "fechas": sorted({r.get("fecha") for r in reservas if r.get("fecha")}),
            "error": "Hay varias reservas. Pregunta únicamente cuál fecha desea cancelar.",
        }

    reserva = reservas[0]
    tipo = reserva.get("tipo_reserva")
    if tipo == "sala":
        res = await cantina_api.cancelar_reserva_sala(reserva["id"])
    elif reserva.get("grupo_id"):
        res = await cantina_api.cancelar_grupo(reserva["grupo_id"])
    else:
        res = await cantina_api.cancelar_reserva(reserva["id"])
    if isinstance(res, dict) and res.get("ok"):
        res["instruccion"] = (
            f"Confirma que la reserva del {reserva.get('fecha')} fue cancelada."
        )
    return res


async def handler_enviar_como_llegar(args: dict, ctx: dict) -> dict:
    """Marca que hay que enviar el video de cómo llegar. El envío real lo hace el
    flow DESPUÉS del mensaje de texto (para que llegue dirección y luego video)."""
    ctx["enviar_video_como_llegar"] = True
    log.info("tools.enviar_como_llegar", cliente=ctx.get("cliente_numero"))
    return {"ok": True, "nota": "El video de cómo llegar se enviará junto con tu respuesta."}


async def handler_enviar_carta(args: dict, ctx: dict) -> dict:
    """Marca que hay que enviar el link del menú digital tras el texto."""
    ctx["enviar_carta_link"] = True
    log.info("tools.enviar_carta", cliente=ctx.get("cliente_numero"))
    return {
        "ok": True,
        "nota": (
            "El link del menú se enviará junto con tu respuesta. No escribas precios "
            "ni productos; solo invita al cliente a abrirlo."
        ),
    }


async def handler_enviar_plano_espacio(args: dict, ctx: dict) -> dict:
    """Marca que hay que enviar el plano público del salón."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    fecha = args.get("fecha") or datetime.now(ZoneInfo("America/Bogota")).date().isoformat()
    ctx["enviar_plano_espacio"] = True
    ctx["plano_fecha"] = fecha
    mesa = args.get("mesa_recomendada")
    ctx["plano_mesa_recomendada"] = int(mesa) if str(mesa or "").isdigit() else None
    log.info("tools.enviar_plano_espacio", cliente=ctx.get("cliente_numero"), fecha=fecha)
    return {
        "ok": True,
        "fecha": fecha,
        "nota": "El plano público se enviará sin revelar mesas ocupadas. Si se indicó "
                "una recomendación válida, aparecerá destacada en verde.",
    }


async def handler_enviar_estado_actual(args: dict, ctx: dict) -> dict:
    """Marca que hay que enviar la imagen del estado/promo vigente (tras el texto).
    Si no hay estado guardado, lo informa para que el bot avise al cliente."""
    from app import promo_estado
    if not await promo_estado.cargar_estados_activos(max_estados=1):
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
        item = {
            "clase": "escalacion",
            "tipo": args.get("tipo", "otro"),
            "mensaje": args.get("mensaje") or "Escalación sin detalle",
            "cliente_id": ctx.get("cliente_id"),
            "cliente_numero": ctx.get("cliente_numero"),
        }
        if ctx.get("incoming_media_tipo") == "audio" and ctx.get("incoming_media_url"):
            item["media_url"] = ctx.get("incoming_media_url")
            item["media_mime"] = ctx.get("incoming_media_mime") or "audio/ogg"
            item["mensaje"] = (
                f"{item['mensaje']}\n\n"
                "🎙️ Nota de voz adjunta para que el equipo la escuche."
            )
        outbox.append(item)
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
    "cambiar_mesa_reserva_cliente": handler_cambiar_mesa_reserva_cliente,
    "actualizar_personas_reserva_cliente": handler_actualizar_personas_reserva_cliente,
    "consultar_reserva_cliente": handler_consultar_reserva_cliente,
    "cancelar_reserva_cliente": handler_cancelar_reserva_cliente,
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
