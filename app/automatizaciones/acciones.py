"""Acciones de proactividad de La Cantina, disparadas por el scheduler
(tabla `tareas_programadas`, cron en BD).

Cada acción es async, recibe (session, params) y devuelve un dict con el
resultado. Para agregar una: definir `async def accion_X(...)` y registrarla en
ACCIONES_DISPONIBLES con su schema de parámetros.

Las acciones de mesas/eventos consultan el backend de La Cantina vía HTTP
(`cantina_api`) y publican al grupo del equipo (`notif_equipo`). Las "soft"
(texto compuesto) usan Claude vía `_redactar`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.eventos import etiqueta_hora, extraer_eventos
from app.integrations import cantina_api
from app.logging_setup import log
from app.notif_equipo import notificar_equipo
from app.whapi.client import enviar_texto

settings = get_settings()

REGLA_VALIDEZ_RESERVA = (
    "Importante: tu reserva tiene validez hasta las 11:00 p. m.; despues de esa "
    "hora la mesa queda sujeta a disponibilidad."
)

_COVER_PAGADO_ANTICIPADO = {
    "anticipado",
    "pagado",
    "pago",
    "transferido",
    "transferencia",
    "comprobante_verificado",
}


# ─── HELPERS ───────────────────────────────────────────────────────────────


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(settings.tz or "America/Bogota")
    except Exception:
        return timezone.utc


def _hoy(offset_dias: int = 0) -> str:
    """Fecha local (America/Bogota) YYYY-MM-DD, con offset opcional de días."""
    d = (datetime.now(timezone.utc).astimezone(_tz()) + timedelta(days=offset_dias)).date()
    return d.isoformat()


def _aplica_regla_validez_reserva(reserva: dict) -> bool:
    """La regla de las 11 no aplica si la reserva ya esta pagada por anticipado."""
    estado = str(reserva.get("cover_estado") or "").strip().lower()
    return estado not in _COVER_PAGADO_ANTICIPADO


async def _enviar_a_destino(destino_tipo: str, destino_id: str, mensaje: str) -> dict:
    """destino_tipo: 'numero' (contacto) o 'grupo' (chat_id @g.us)."""
    destino = destino_id
    if destino_tipo == "grupo" and not destino.endswith("@g.us"):
        destino = destino + "@g.us"
    try:
        await enviar_texto(destino, mensaje)
        return {"ok": True, "destino": destino, "chars": len(mensaje)}
    except Exception as e:
        log.warning("automatizacion.envio_fail", destino=destino, error=str(e))
        return {"ok": False, "destino": destino, "error": str(e)[:200]}


def _render_plantilla(plantilla: str, ctx: dict[str, Any]) -> str:
    out = plantilla
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


async def _redactar(system: str, instruccion: str, max_tokens: int = 400) -> str:
    """Compone un mensaje breve con Claude. Devuelve "" si falla (el caller decide fallback)."""
    try:
        from app.claude.anthropic_client import get_anthropic_client
        client = get_anthropic_client()
        resp = await client.messages.create(
            model=settings.claude_model_principal,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": instruccion}],
        )
        return "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    except Exception as e:
        log.warning("automatizacion.redactar_fail", error=str(e))
        return ""


def _payload(resp: dict) -> dict:
    """Extrae el dict de datos de una respuesta de cantina_api ({ok, data?} o plano)."""
    if not isinstance(resp, dict):
        return {}
    data = resp.get("data")
    if isinstance(data, dict):
        return data
    return {k: v for k, v in resp.items() if k != "ok"}


def _fmt_resumen(payload: dict) -> str:
    """Formatea un resumen del backend (escalares de primer nivel) en texto legible."""
    if not payload:
        return "(sin datos)"
    lineas = []
    for k, v in payload.items():
        if isinstance(v, (int, float, str, bool)):
            lineas.append(f"- {k}: {v}")
    return "\n".join(lineas) if lineas else "(sin datos escalares)"


def _formatear_cop(valor: Any) -> str:
    try:
        return f"${int(float(valor)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(valor or "")


def _reservas_lista(resp: dict) -> list[dict]:
    """Normaliza la respuesta de listar_reservas a una lista de dicts."""
    if not isinstance(resp, dict) or not resp.get("ok", True):
        return []
    data = resp.get("data", resp)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("reservas", "items", "data"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
    return []


# ═══════════════════════════════════════════════════════════════════════════
# ACCIONES — operación de La Cantina (mesas / eventos / covers)
# ═══════════════════════════════════════════════════════════════════════════


async def accion_resumen_dia(session: AsyncSession, params: dict) -> dict:
    """Resumen del día al grupo del equipo (típico 9 AM).

    params: fecha? (default hoy), destino_tipo/destino_id (default grupo equipo).
    """
    fecha = params.get("fecha") or _hoy()
    resp = await cantina_api.resumen_dia(fecha)
    if not (isinstance(resp, dict) and resp.get("ok", False)):
        return {"ok": False, "error": (resp or {}).get("error", "no pude leer el resumen"), "skip": True}

    cuerpo = _fmt_resumen(_payload(resp))
    mensaje = f"📋 *Resumen de La Cantina — {fecha}*\n\n{cuerpo}"
    enviado = await notificar_equipo(mensaje)
    return {"ok": enviado, "fecha": fecha, "preview": mensaje[:200]}


async def accion_cierre_dia(session: AsyncSession, params: dict) -> dict:
    """Resumen del día ANTERIOR al grupo del equipo (típico 3 AM).

    params: destino_tipo/destino_id (default grupo equipo).
    """
    fecha = params.get("fecha") or _hoy(offset_dias=-1)
    resp = await cantina_api.resumen_dia(fecha)
    if not (isinstance(resp, dict) and resp.get("ok", False)):
        return {"ok": False, "error": (resp or {}).get("error", "no pude leer el resumen"), "skip": True}
    cuerpo = _fmt_resumen(_payload(resp))
    mensaje = f"🌙 *Cierre del día — {fecha}*\n\n{cuerpo}"
    enviado = await notificar_equipo(mensaje)
    return {"ok": enviado, "fecha": fecha, "preview": mensaje[:200]}


async def accion_pre_evento(session: AsyncSession, params: dict) -> dict:
    """Aviso al equipo si hoy hay evento: detalle + conteo de reservas.

    Pensado para correr cada 15-30 min en la franja previa al evento. Si no hay
    evento hoy → skip silencioso.
    params: fecha? (default hoy).
    """
    fecha = params.get("fecha") or _hoy()
    ev = await cantina_api.consultar_evento(fecha)
    eventos = extraer_eventos(ev)
    if not (isinstance(ev, dict) and ev.get("ok", False) and eventos):
        return {"ok": True, "skip": True, "razon": "sin evento hoy"}

    reservas = _reservas_lista(await cantina_api.listar_reservas(fecha))
    n_reservas = len(reservas)
    lineas_eventos = []
    for evento in eventos:
        nombre_ev = evento.get("nombre") or evento.get("evento") or "Evento"
        hora = etiqueta_hora(evento)
        cover = evento.get("valor_cover")
        detalle = f"- {hora + ' · ' if hora else ''}{nombre_ev}"
        if cover:
            detalle += f" · Cover: ${cover} por persona"
        lineas_eventos.append(detalle)
    mensaje = (
        f"🎤 *Hoy hay evento{'s' if len(eventos) > 1 else ''}*\n"
        f"Fecha: {fecha}\n"
        + "\n".join(lineas_eventos)
        + "\n"
        + f"Reservas registradas: {n_reservas}\n\n"
        f"Preparen todo. Para el detalle usen el resumen del día."
    )
    enviado = await notificar_equipo(mensaje)
    return {"ok": enviado, "fecha": fecha, "reservas": n_reservas}


async def accion_recordatorio_cover(session: AsyncSession, params: dict) -> dict:
    """Si hay covers en estado 'pendiente' para una fecha con evento, recuerda
    al equipo que los verifiquen/cobren (típico 10 AM).

    params: fecha? (default hoy).
    """
    fecha = params.get("fecha") or _hoy()
    reservas = _reservas_lista(await cantina_api.listar_reservas(fecha))
    pendientes = [r for r in reservas if (r.get("cover_estado") == "pendiente")]
    if not pendientes:
        return {"ok": True, "skip": True, "razon": "sin covers pendientes"}

    lineas = []
    for r in pendientes[:15]:
        nombre = r.get("nombre_cliente") or r.get("nombre") or "?"
        mesa = r.get("mesa_numero") or r.get("mesa_id") or "?"
        rid = r.get("id") or "?"
        lineas.append(f"- #{rid} · {nombre} · mesa {mesa}")
    mensaje = (
        f"💸 *Covers PENDIENTES — {fecha}* ({len(pendientes)})\n\n"
        + "\n".join(lineas)
        + "\n\nVerifiquen el pago o márquenlos como 'en entrada' / 'anticipado'."
    )
    enviado = await notificar_equipo(mensaje)
    return {"ok": enviado, "fecha": fecha, "pendientes": len(pendientes)}


def _reservas_logicas(reservas: list[dict]) -> list[dict]:
    """Deduplica las filas de una reserva grupal para enviar un solo mensaje."""
    salida: list[dict] = []
    vistos: set[str] = set()
    for reserva in reservas:
        clave = str(reserva.get("grupo_id") or f"mesa:{reserva.get('id')}")
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append(reserva)
    return salida


async def _recordatorio_ya_enviado(
    session: AsyncSession, cliente_id: int, clave: str
) -> bool:
    row = (await session.execute(sa_text("""
        SELECT 1 FROM conversaciones
        WHERE cliente_id = :cid
          AND metadata->>'recordatorio_reserva' = :clave
        LIMIT 1
    """), {"cid": cliente_id, "clave": clave})).first()
    return bool(row)


async def accion_recordatorio_reservas_clientes(
    session: AsyncSession, params: dict
) -> dict:
    """Recuerda reservas a clientes y acepta respuestas Confirmar/Cancelar."""
    from app.db.repos import get_or_create_cliente, guardar_conversacion

    offset_dias = int(params.get("offset_dias", 0))
    tipo = str(params.get("tipo") or "3h")
    fecha = params.get("fecha") or _hoy(offset_dias)
    mesas = _reservas_logicas(
        _reservas_lista(await cantina_api.listar_reservas(fecha))
    )
    salas = _reservas_lista(await cantina_api.listar_reservas_salas(fecha))
    reservas = mesas + salas

    enviados = 0
    omitidos = 0
    for reserva in reservas:
        telefono = str(reserva.get("telefono") or "").strip()
        if not telefono:
            omitidos += 1
            continue
        numero = telefono if telefono.startswith("+") else "+" + telefono
        identidad = (
            reserva.get("grupo_id")
            or f"{reserva.get('tipo_reserva', 'reserva')}:{reserva.get('id')}"
        )
        clave = f"{tipo}:{fecha}:{identidad}"
        cliente = await get_or_create_cliente(session, numero)
        if await _recordatorio_ya_enviado(session, cliente.id, clave):
            omitidos += 1
            continue

        nombre = reserva.get("nombre_cliente") or ""
        mesas_reserva = reserva.get("grupo_mesas")
        if mesas_reserva:
            ubicacion = "mesas " + ", ".join(str(m) for m in mesas_reserva)
        elif reserva.get("mesa_numero"):
            ubicacion = f"mesa {reserva['mesa_numero']}"
        else:
            ubicacion = reserva.get("sala_nombre") or "tu espacio reservado"
        momento = "mañana" if tipo == "24h" else "hoy"
        nota_validez = (
            f"{REGLA_VALIDEZ_RESERVA}\n\n"
            if _aplica_regla_validez_reserva(reserva)
            else ""
        )
        mensaje = (
            f"Hola{f' {nombre}' if nombre else ''}. Te recordamos tu reserva "
            f"para {momento}, {fecha}, en {ubicacion} para "
            f"{reserva.get('num_personas', '?')} persona(s).\n\n"
            f"{nota_validez}"
            "Responde *Confirmar* para mantenerla o *Cancelar* si ya no puedes asistir."
        )
        await enviar_texto(numero, mensaje)
        await guardar_conversacion(
            session,
            cliente_id=cliente.id,
            direccion="outbound",
            tipo="texto",
            contenido=mensaje,
            metadata={
                "recordatorio_reserva": clave,
                "tipo_recordatorio": tipo,
                "fecha_reserva": fecha,
            },
        )
        await session.commit()
        enviados += 1

    return {
        "ok": True,
        "fecha": fecha,
        "tipo": tipo,
        "enviados": enviados,
        "omitidos": omitidos,
    }


async def accion_recordatorio_cover_clientes(
    session: AsyncSession, params: dict
) -> dict:
    """Recuerda directamente al cliente cuando su cover sigue pendiente."""
    from app.db.repos import get_or_create_cliente, guardar_conversacion

    fecha = params.get("fecha") or _hoy(int(params.get("offset_dias", 0)))
    reservas = _reservas_logicas(
        _reservas_lista(await cantina_api.listar_reservas(fecha))
    )
    pendientes = [r for r in reservas if r.get("cover_estado") == "pendiente"]
    enviados = 0
    for reserva in pendientes:
        telefono = str(reserva.get("telefono") or "").strip()
        if not telefono:
            continue
        numero = telefono if telefono.startswith("+") else "+" + telefono
        identidad = reserva.get("grupo_id") or reserva.get("id")
        clave = f"cover:{fecha}:{identidad}"
        cliente = await get_or_create_cliente(session, numero)
        if await _recordatorio_ya_enviado(session, cliente.id, clave):
            continue
        monto = reserva.get("monto_cover")
        detalle = f" por {_formatear_cop(monto)}" if monto else ""
        mensaje = (
            f"Hola. Tu reserva para el {fecha} tiene el cover pendiente{detalle}. "
            "Puedes enviarnos el comprobante por este chat para validarlo."
        )
        await enviar_texto(numero, mensaje)
        await guardar_conversacion(
            session,
            cliente_id=cliente.id,
            direccion="outbound",
            tipo="texto",
            contenido=mensaje,
            metadata={
                "recordatorio_reserva": clave,
                "tipo_recordatorio": "cover",
                "fecha_reserva": fecha,
            },
        )
        await session.commit()
        enviados += 1
    return {"ok": True, "fecha": fecha, "enviados": enviados}


async def accion_saturacion_check(session: AsyncSession, params: dict) -> dict:
    """Alerta si la ocupación del día supera un umbral (default 85%).

    params: fecha? (default hoy), umbral_pct? (default 85).
    """
    fecha = params.get("fecha") or _hoy()
    umbral = float(params.get("umbral_pct", 85))
    resp = await cantina_api.disponibilidad(fecha)
    if not (isinstance(resp, dict) and resp.get("ok", False)):
        return {"ok": False, "skip": True, "error": (resp or {}).get("error", "no pude leer disponibilidad")}
    payload = _payload(resp)
    ocup = payload.get("ocupacion") or {}
    ocupadas = len(ocup.get("ocupadas") or []) if isinstance(ocup, dict) else 0
    libres = len(ocup.get("libres") or []) if isinstance(ocup, dict) else 0
    total = ocupadas + libres
    if total <= 0:
        return {"ok": True, "skip": True, "razon": "sin datos de ocupación"}
    pct = ocupadas / total * 100
    if pct < umbral:
        return {"ok": True, "skip": True, "razon": f"ocupación {pct:.0f}% < umbral {umbral:.0f}%"}
    mensaje = (
        f"🔥 *Alta ocupación — {fecha}*\n"
        f"{ocupadas}/{total} mesas ocupadas ({pct:.0f}%).\n"
        f"Consideren sugerir salas privadas o combos a nuevas reservas."
    )
    enviado = await notificar_equipo(mensaje)
    return {"ok": enviado, "fecha": fecha, "ocupacion_pct": round(pct, 1)}


async def accion_liberar_pendientes_cover(session: AsyncSession, params: dict) -> dict:
    """Reporta al equipo las reservas con cover 'pendiente' para que decidan si
    liberar la mesa (no auto-cancela: la decisión es humana).

    params: fecha? (default hoy).
    """
    fecha = params.get("fecha") or _hoy()
    reservas = _reservas_lista(await cantina_api.listar_reservas(fecha))
    pendientes = [r for r in reservas if (r.get("cover_estado") == "pendiente")]
    if not pendientes:
        return {"ok": True, "skip": True, "razon": "sin pendientes de cover"}
    mensaje = (
        f"⏳ *{len(pendientes)} reserva(s) con cover pendiente — {fecha}*\n"
        f"Si no confirman pago pronto, evalúen liberar la mesa con `cancelar_reserva`."
    )
    enviado = await notificar_equipo(mensaje)
    return {"ok": enviado, "fecha": fecha, "pendientes": len(pendientes)}


# ═══════════════════════════════════════════════════════════════════════════
# ACCIONES GENÉRICAS (reusadas de la plataforma; no dependen del backend de mesas)
# ═══════════════════════════════════════════════════════════════════════════


async def accion_recordatorio_pendientes(session: AsyncSession, params: dict) -> dict:
    """Recordatorio de alertas/pendientes abiertas >Nh al grupo del equipo."""
    destino_tipo = params.get("destino_tipo", "grupo")
    destino_id = params.get("destino_id") or settings.equipo_cantina_group_id
    horas_min = int(params.get("horas_min", 2))
    max_alertas = int(params.get("max_alertas", 5))
    rows = (await session.execute(sa_text(
        """SELECT a.id, a.tipo, LEFT(a.mensaje, 120), a.created_at,
                  c.numero_whatsapp, COALESCE(c.nombre, '-')
           FROM alertas_fabio a LEFT JOIN clientes c ON c.id = a.cliente_id
           WHERE a.resuelto = false
             AND a.created_at <= NOW() - (:h || ' hours')::interval
           ORDER BY a.created_at ASC LIMIT :lim"""
    ), {"h": str(horas_min), "lim": max_alertas})).fetchall()
    if not rows:
        return {"ok": True, "skip": True, "razon": f"sin pendientes >{horas_min}h"}
    lineas = []
    for r in rows:
        edad = int((datetime.now(timezone.utc) - r[3].replace(tzinfo=timezone.utc)).total_seconds() / 3600) if r[3] else 0
        lineas.append(f"#{r[0]} {r[1]} · {r[5]} {r[4]} ({edad}h)\n  {r[2]}")
    mensaje = f"🔔 Pendientes abiertos (>{horas_min}h)\n\n" + "\n\n".join(lineas)
    if not destino_id:
        enviado = await notificar_equipo(mensaje)
        return {"ok": enviado, "pendientes": len(rows)}
    res = await _enviar_a_destino(destino_tipo, destino_id, mensaje)
    return {"ok": res.get("ok"), "pendientes": len(rows), "error": res.get("error")}


async def accion_mensaje_custom(session: AsyncSession, params: dict) -> dict:
    """Mensaje arbitrario (texto fijo o con query SQL). params: destino_tipo, destino_id, mensaje, query_sql?"""
    destino_tipo = params.get("destino_tipo", "numero")
    destino_id = params.get("destino_id")
    plantilla = params.get("mensaje") or ""
    query_sql = (params.get("query_sql") or "").strip()
    if not destino_id or not plantilla:
        return {"ok": False, "error": "falta destino_id o mensaje"}
    ctx = {
        "fecha": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "hora": datetime.now(timezone.utc).strftime("%H:%M"),
    }
    if query_sql:
        if not query_sql.lower().lstrip().startswith("select"):
            return {"ok": False, "error": "query_sql debe ser SELECT"}
        try:
            r = (await session.execute(sa_text(query_sql))).fetchall()
            ctx["resultado"] = (str(r[0][0]) if len(r) == 1 and len(r[0]) == 1
                                else "\n".join(" | ".join(str(c) for c in row) for row in r[:20]))
        except Exception as e:
            return {"ok": False, "error": f"query falló: {str(e)[:200]}"}
    mensaje = _render_plantilla(plantilla, ctx)
    res = await _enviar_a_destino(destino_tipo, destino_id, mensaje)
    return {"ok": res.get("ok"), "preview": mensaje[:200], "error": res.get("error")}


async def accion_enviar_pendientes_apertura(session: AsyncSession, params: dict) -> dict:
    """Envía los mensajes que el bot generó FUERA DE HORARIO y dejó en BD con
    `metadata.no_enviado=true`. Útil solo si FEATURE_RESPONDER_24_7=false.

    params: max_envios (default 50), delay_min_s / delay_max_s (default 8/22).
    """
    import asyncio
    import random

    max_envios = int(params.get("max_envios") or 50)
    dmin = float(params.get("delay_min_s") or 8.0)
    dmax = float(params.get("delay_max_s") or 22.0)

    rows = (await session.execute(sa_text("""
        SELECT c.id, c.contenido, cl.numero_whatsapp, cl.nombre
        FROM conversaciones c
        JOIN clientes cl ON cl.id = c.cliente_id
        WHERE c.direccion = 'outbound'
          AND c.metadata->>'no_enviado' = 'true'
          AND c.metadata->>'razon' IN ('fuera_de_horario', 'rate_limit')
          AND c.contenido IS NOT NULL
          AND trim(c.contenido) != ''
        ORDER BY c.timestamp ASC
        LIMIT :lim
    """), {"lim": max_envios})).fetchall()

    if not rows:
        log.info("acciones.pendientes_apertura.vacio")
        return {"ok": True, "enviados": 0, "razon": "sin pendientes"}

    enviados = 0
    fallos: list[str] = []
    for r in rows:
        conv_id, texto, numero, nombre = r[0], r[1], r[2], r[3]
        try:
            await enviar_texto(numero, texto)
            enviados += 1
            await session.execute(sa_text("""
                UPDATE conversaciones
                SET metadata = (metadata - 'no_enviado' - 'razon' - 'programado_para')
                               || jsonb_build_object('enviado_retroactivo', true, 'enviado_en', now()::text)
                WHERE id = :id
            """), {"id": conv_id})
            await session.commit()
            log.info("acciones.pendiente_enviado", conv_id=conv_id, numero=numero, chars=len(texto or ""))
        except Exception as e:
            fallos.append(f"{conv_id}: {str(e)[:80]}")
            log.warning("acciones.pendiente_fail", conv_id=conv_id, error=str(e))
        await asyncio.sleep(random.uniform(dmin, dmax))

    log.warning("acciones.pendientes_apertura.done", total=len(rows), enviados=enviados, fallos=len(fallos))
    return {"ok": True, "total": len(rows), "enviados": enviados, "fallos": fallos[:10]}


async def accion_sync_chats_whapi(session: AsyncSession, params: dict) -> dict:
    """Importa de whapi los mensajes recientes de chats individuales que NO
    están en BD (cubre webhooks perdidos por restart/timeout).

    params: max_chats (def 30), max_msgs_por_chat (def 15), horas_max (def 24).
    """
    import httpx
    from app.config import get_settings as _gs
    from app.whapi.client import _headers
    from app.whapi.parser import parsear_mensaje
    from app.db.repos import get_or_create_cliente, guardar_conversacion
    settings_local = _gs()

    max_chats = int(params.get("max_chats") or 30)
    max_msgs = int(params.get("max_msgs_por_chat") or 15)
    horas_max = int(params.get("horas_max") or 24)
    desde_ts = int((datetime.now(timezone.utc) - timedelta(hours=horas_max)).timestamp())

    base = settings_local.whapi_base_url.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{base}/chats?count={max_chats}", headers=_headers())
        if r.status_code >= 400:
            return {"ok": False, "error": f"chats list HTTP {r.status_code}"}
        chats = r.json().get("chats", [])
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

    importados = 0
    revisados = 0
    saltados_no_individual = 0
    for ch in chats:
        cid = ch.get("id") or ""
        if not cid.endswith("@s.whatsapp.net"):
            saltados_no_individual += 1
            continue
        revisados += 1
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(f"{base}/messages/list/{cid}?count={max_msgs}", headers=_headers())
            if r.status_code >= 400:
                continue
            msgs = r.json().get("messages", [])
        except Exception:
            continue

        for raw in msgs:
            ts = raw.get("timestamp") or 0
            if ts < desde_ts:
                continue
            msg_id = raw.get("id")
            if not msg_id:
                continue
            if raw.get("from_me") and (raw.get("source") or "").lower() == "api":
                continue
            existe = (await session.execute(sa_text(
                "SELECT 1 FROM conversaciones WHERE whapi_message_id = :id LIMIT 1"
            ), {"id": msg_id})).first()
            if existe:
                continue
            mensaje = parsear_mensaje(raw)
            if not mensaje or not (mensaje.texto or mensaje.media_url or mensaje.tipo == "audio"):
                continue
            if mensaje.tipo == "desconocido" and not (mensaje.texto or "").strip() and not mensaje.media_url:
                continue
            try:
                cliente = await get_or_create_cliente(session, mensaje.from_number, nombre=mensaje.from_name)
                direccion = "humano" if mensaje.is_from_human else ("outbound" if mensaje.is_from_bot else "inbound")
                if mensaje.texto:
                    dup = (await session.execute(sa_text("""
                        SELECT 1 FROM conversaciones
                        WHERE cliente_id = :cid AND direccion = :dir
                          AND ABS(EXTRACT(EPOCH FROM (timestamp - to_timestamp(:t)))) < 90
                          AND LEFT(COALESCE(contenido,''), 60) = LEFT(:c, 60)
                        LIMIT 1
                    """), {"cid": cliente.id, "dir": direccion, "t": ts, "c": mensaje.texto})).first()
                    if dup:
                        continue
                await guardar_conversacion(
                    session, cliente_id=cliente.id, direccion=direccion,
                    tipo=mensaje.tipo, contenido=mensaje.texto,
                    whapi_message_id=mensaje.id, media_url=mensaje.media_url,
                    metadata={"importado_via_sync": True, "raw_ts": ts},
                )
                await session.execute(sa_text(
                    "UPDATE conversaciones SET timestamp = to_timestamp(:t) WHERE whapi_message_id = :id"
                ), {"t": ts, "id": msg_id})
                await session.commit()
                importados += 1
            except Exception as e:
                log.warning("sync_chats.import_fail", msg_id=msg_id, error=str(e)[:100])
                await session.rollback()
                continue

    log.warning(
        "acciones.sync_chats_done",
        chats_revisados=revisados, importados=importados,
        saltados_no_individual=saltados_no_individual,
    )
    return {
        "ok": True, "chats_revisados": revisados,
        "mensajes_importados": importados,
        "saltados_no_individual": saltados_no_individual,
    }


# ─── REGISTRY ────────────────────────────────────────────────────────────────


from app.automatizaciones.heartbeat import accion_heartbeat as _accion_heartbeat


ACCIONES_DISPONIBLES: dict[str, dict[str, Any]] = {
    "heartbeat": {
        "handler": _accion_heartbeat,
        "descripcion": "El bot decide UNA acción útil sobre alertas/chats sin responder. Conservador por defecto.",
        "parametros": {"respetar_horario": "bool (default true)"},
    },
    "resumen_dia": {
        "handler": accion_resumen_dia,
        "descripcion": "Resumen del día (reservas/covers) al grupo del equipo. Típico 9 AM.",
        "parametros": {"fecha": "YYYY-MM-DD (default hoy)"},
    },
    "cierre_dia": {
        "handler": accion_cierre_dia,
        "descripcion": "Resumen del día ANTERIOR al grupo del equipo. Típico 3 AM.",
        "parametros": {"fecha": "YYYY-MM-DD (default ayer)"},
    },
    "pre_evento": {
        "handler": accion_pre_evento,
        "descripcion": "Si hoy hay evento, avisa al equipo con detalle + conteo de reservas.",
        "parametros": {"fecha": "YYYY-MM-DD (default hoy)"},
    },
    "recordatorio_cover": {
        "handler": accion_recordatorio_cover,
        "descripcion": "Recuerda al equipo los covers en estado 'pendiente' del día.",
        "parametros": {"fecha": "YYYY-MM-DD (default hoy)"},
    },
    "recordatorio_reservas_clientes": {
        "handler": accion_recordatorio_reservas_clientes,
        "descripcion": "Envía recordatorio 24h/3h al cliente con Confirmar o Cancelar.",
        "parametros": {"offset_dias": "int", "tipo": "24h|3h"},
    },
    "recordatorio_cover_clientes": {
        "handler": accion_recordatorio_cover_clientes,
        "descripcion": "Recuerda al cliente que su cover sigue pendiente.",
        "parametros": {"offset_dias": "int (default 0)"},
    },
    "saturacion_check": {
        "handler": accion_saturacion_check,
        "descripcion": "Alerta si la ocupación del día supera el umbral (default 85%).",
        "parametros": {"fecha": "YYYY-MM-DD (default hoy)", "umbral_pct": "int (default 85)"},
    },
    "liberar_pendientes_cover": {
        "handler": accion_liberar_pendientes_cover,
        "descripcion": "Reporta reservas con cover pendiente para que el equipo decida liberar la mesa.",
        "parametros": {"fecha": "YYYY-MM-DD (default hoy)"},
    },
    "recordatorio_pendientes": {
        "handler": accion_recordatorio_pendientes,
        "descripcion": "Recordatorio de alertas/pendientes abiertas >Nh al grupo del equipo.",
        "parametros": {"destino_id": "@g.us o +57... (default grupo equipo)", "horas_min": "int", "max_alertas": "int"},
    },
    "mensaje_custom": {
        "handler": accion_mensaje_custom,
        "descripcion": "Mensaje arbitrario, opcional con query SQL ({{resultado}}).",
        "parametros": {"destino_tipo": "numero|grupo", "destino_id": "...", "mensaje": "texto con {{fecha}} {{hora}} {{resultado}}", "query_sql": "SELECT opcional"},
    },
    "enviar_pendientes_apertura": {
        "handler": accion_enviar_pendientes_apertura,
        "descripcion": "Envía mensajes que el bot dejó pendientes fuera de horario (solo si no es 24/7).",
        "parametros": {"max_envios": "int (default 50)", "delay_min_s": "int", "delay_max_s": "int"},
    },
    "sync_chats_whapi": {
        "handler": accion_sync_chats_whapi,
        "descripcion": "Importa de whapi mensajes recientes que el webhook no entregó (restart, hipo).",
        "parametros": {"max_chats": "int (def 30)", "max_msgs_por_chat": "int (def 15)", "horas_max": "int (def 24)"},
    },
}


async def ejecutar_accion(nombre: str, session: AsyncSession, params: dict) -> dict:
    """Ejecuta una acción por nombre. Maneja errores y devuelve dict resultado."""
    accion = ACCIONES_DISPONIBLES.get(nombre)
    if not accion:
        return {"ok": False, "error": f"acción desconocida: {nombre}"}
    try:
        return await accion["handler"](session, params or {})
    except Exception as e:
        log.exception("automatizacion.accion.fail", accion=nombre, error=str(e))
        return {"ok": False, "error": str(e)[:300]}
