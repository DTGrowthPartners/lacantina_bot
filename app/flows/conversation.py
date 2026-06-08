"""
Flujo CLIENTE de La Cantina Plus.

Quien escribe es público (cualquier número no clasificado como equipo/interno):
alguien que pregunta por horarios/ubicación, quiere reservar mesa, pregunta por
un show/cover o manda el comprobante del cover. El bot resuelve con las tools de
`app.claude.tools` (disponibilidad → reserva → comprobante → escalar).

Pipeline:
  1. Construye historial reciente
  2. Clasifica intent (Haiku)
  3. Llama a Claude (tool use loop) con las tools de cliente
  4. Humaniza (typing + delay anti-detección) y envía vía whapi
  5. Persiste todo

Devuelve el `outbox` (avisos al grupo del equipo: reserva nueva, comprobante,
escalación) para drenar DESPUÉS del commit (lo hace _procesar_async en main.py).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.claude.client import conversar
from app.claude.intent import clasificar
from app.config import get_settings
from app.db.models import Cliente, Conversacion
from app.db.repos import bot_activo, guardar_conversacion, ultimos_mensajes
from app.logging_setup import log
from app.utils.humanizer import (
    dentro_horario,
    proxima_hora_apertura,
    puede_enviar,
    sleep_humano,
)
from app.whapi.client import (
    auth_headers,
    enviar_documento_bytes,
    enviar_imagen_bytes,
    enviar_paused,
    enviar_texto,
    enviar_typing,
    enviar_video_bytes,
    set_token as set_whapi_token,
)
from app.whapi.parser import MensajeWhapi
from app.identidades import Identidad, principal as _identidad_principal

settings = get_settings()

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


_VIDEO_COMO_LLEGAR = Path(settings.data_dir) / "media" / "como-llegar.mp4"
_CARTA_PDF = Path(settings.data_dir) / "media" / "Menu_La_Cantina.pdf"
_FLYERS_DIR = Path(settings.data_dir) / "media" / "flyers"


async def _enviar_flyer_evento(session: AsyncSession, cliente_id: int, cliente_numero: str, fecha: str) -> None:
    """Envía el flyer del evento de una fecha (si existe), tras el texto."""
    p = next((c for ext in (".jpg", ".jpeg", ".png", ".webp")
              if (c := _FLYERS_DIR / f"{fecha}{ext}").exists()), None)
    if not p:
        return
    try:
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        await enviar_imagen_bytes(
            cliente_numero, p.read_bytes(), mime=mime,
            caption="🎟️ ¡Te esperamos en el evento! 🎶",
        )
        await guardar_conversacion(
            session, cliente_id=cliente_id, direccion="outbound", tipo="imagen",
            contenido="[flyer del evento]", metadata={"media": "flyer_evento", "fecha": fecha},
        )
        log.info("flow.flyer_evento.enviado", cliente=cliente_numero, fecha=fecha)
    except Exception as e:
        log.warning("flow.flyer_evento.fail", error=str(e))


async def _enviar_carta_pdf(session: AsyncSession, cliente_id: int, cliente_numero: str) -> None:
    """Envía la carta/menú en PDF al cliente (tras el texto)."""
    if not _CARTA_PDF.exists():
        log.warning("flow.carta_pdf.no_existe", path=str(_CARTA_PDF))
        return
    try:
        await enviar_documento_bytes(
            cliente_numero, _CARTA_PDF.read_bytes(), mime="application/pdf",
            filename="Menu_La_Cantina.pdf", caption="🍾 Nuestra carta 🎶",
        )
        await guardar_conversacion(
            session, cliente_id=cliente_id, direccion="outbound", tipo="pdf",
            contenido="[carta: Menu_La_Cantina.pdf]", metadata={"media": "carta_pdf"},
        )
        log.info("flow.carta_pdf.enviado", cliente=cliente_numero)
    except Exception as e:
        log.warning("flow.carta_pdf.fail", error=str(e))


async def _enviar_video_como_llegar(session: AsyncSession, cliente_id: int, cliente_numero: str) -> None:
    """Envía el video de cómo llegar al cliente (tras el texto de la dirección)."""
    if not _VIDEO_COMO_LLEGAR.exists():
        log.warning("flow.video_como_llegar.no_existe", path=str(_VIDEO_COMO_LLEGAR))
        return
    try:
        data = _VIDEO_COMO_LLEGAR.read_bytes()
        await enviar_video_bytes(
            cliente_numero, data, mime="video/mp4",
            caption="🎥 Así llegas a La Cantina Plus 🎶", filename="como-llegar.mp4",
        )
        await guardar_conversacion(
            session, cliente_id=cliente_id, direccion="outbound", tipo="video",
            contenido="[video: cómo llegar]", metadata={"media": "como_llegar"},
        )
        log.info("flow.video_como_llegar.enviado", cliente=cliente_numero)
    except Exception as e:
        log.warning("flow.video_como_llegar.fail", error=str(e))


def _bloque_fecha_actual() -> str:
    """Bloque con la fecha/hora actual de Colombia para que el bot convierta
    'hoy/mañana/el viernes' a YYYY-MM-DD sin inventar (incluido el año)."""
    ahora = datetime.now(ZoneInfo(settings.tz or "America/Bogota"))
    hoy = ahora.strftime("%Y-%m-%d")
    manana = (ahora + timedelta(days=1)).strftime("%Y-%m-%d")
    legible = f"{_DIAS[ahora.weekday()]} {ahora.day} de {_MESES[ahora.month - 1]} de {ahora.year}"
    return (
        "## FECHA Y HORA ACTUAL (Colombia / Cartagena) — ÚSALA, NO la inventes\n"
        f"- Hoy es **{legible}**. Hora actual: {ahora.strftime('%H:%M')}.\n"
        f"- Para las tools (formato YYYY-MM-DD): hoy = **{hoy}**, mañana = **{manana}**.\n"
        f"- Calcula 'el viernes', 'este sábado', etc. a partir de hoy. NUNCA uses un año distinto de {ahora.year}."
    )


async def procesar_mensaje_inbound(
    *,
    session: AsyncSession,
    cliente_id: int,
    cliente_numero: str,
    msg: MensajeWhapi,
    identidad: Identidad | None = None,
) -> list[dict]:
    """Procesa un inbound de un cliente (ya persistido por el webhook) y responde."""
    ident = identidad or _identidad_principal()
    set_whapi_token(ident.token)   # asegurar el token correcto en todo I/O whapi de esta tarea
    # Bot desactivado globalmente → NO enviar nada (ni respuestas ni errores).
    if not await bot_activo(session):
        log.info("flow.bot_inactivo", cliente=cliente_numero)
        return []
    contenido_usuario = msg.texto or ""
    es_audio = msg.tipo == "audio"

    # Nota de voz: La Cantina no tiene transcripción configurada. En vez de
    # ignorarla, le pedimos amablemente al cliente que escriba el mensaje.
    if not contenido_usuario.strip() and es_audio and msg.media_url:
        contenido_usuario = (
            "[El cliente envió una nota de voz, pero no puedo escucharla. "
            "Pídele amablemente que te escriba el mensaje por texto.]"
        )

    if not contenido_usuario.strip():
        if msg.tipo == "imagen" and msg.media_url:
            contenido_usuario = "[El cliente envió una imagen sin texto.]"
        else:
            log.info("flow.inbound_sin_texto", cliente=cliente_numero, tipo=msg.tipo)
            return []

    # Mensaje citado (reply) → inyectar contexto del mensaje citado
    if msg.quoted_message_id:
        quoted_preview = msg.quoted_content or ""
        quoted_db = (await session.execute(
            select(Conversacion).where(
                Conversacion.whapi_message_id == msg.quoted_message_id
            ).limit(1)
        )).scalar_one_or_none()
        if quoted_db and quoted_db.contenido:
            quoted_preview = quoted_db.contenido
        if quoted_preview:
            contenido_usuario = (
                f"[El cliente respondió/citó este mensaje anterior tuyo:\n"
                f"\"{quoted_preview[:500]}\"]\n\n"
                f"Su respuesta: {contenido_usuario}"
            )

    # 1. Historial (hasta 30 msgs / 48h)
    historial_db = await ultimos_mensajes(session, cliente_id, n=30, horas_max=48)
    ahora_utc = datetime.now(timezone.utc)
    umbral_gap = ahora_utc - timedelta(hours=12)
    historial_claude: list[dict] = []
    separador = False
    for h in historial_db[:-1]:
        ts = h.timestamp
        if ts and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if not separador and ts and ts >= umbral_gap and historial_claude:
            historial_claude.append({
                "role": "user",
                "content": "[— Nota interna: pasaron varias horas. Retoma con coherencia usando lo que ya sabes del cliente (nombre, lo conversado, su reserva si tiene). —]",
            })
            separador = True
        if h.direccion == "inbound" and h.contenido:
            historial_claude.append({"role": "user", "content": h.contenido})
        elif h.direccion in ("outbound", "humano") and h.contenido:
            historial_claude.append({"role": "assistant", "content": h.contenido})

    # 2. Intent
    contexto_intent = [h.contenido or "" for h in historial_db[-3:] if h.contenido]
    intent = await clasificar(contenido_usuario, contexto_reciente=contexto_intent)
    log.info("flow.intent", cliente=cliente_numero, intent=intent)
    if intent == "spam":
        log.info("flow.spam_ignorado", cliente=cliente_numero)
        return []

    # 3. Imagen entrante → multimodal (comprobante de cover, captura, etc.)
    imagen_b64: str | None = None
    imagen_mime: str | None = None
    if msg.tipo == "imagen" and msg.media_url:
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(msg.media_url, headers=auth_headers())
                if r.status_code < 400 and len(r.content) <= 5 * 1024 * 1024:
                    imagen_b64 = base64.b64encode(r.content).decode("ascii")
                    imagen_mime = msg.media_mime or "image/jpeg"
        except Exception as e:
            log.warning("flow.imagen.fail_download", error=str(e))

    # 4. Contexto dinámico del cliente + tool use loop
    ctx = {
        "session": session,
        "cliente_id": cliente_id,
        "cliente_numero": cliente_numero,
        "intent": intent,
        "outbox": [],
    }
    extra_system = await _construir_contexto_cliente(session, cliente_id, cliente_numero)

    respuesta = await conversar(
        historial=historial_claude,
        mensaje_usuario=contenido_usuario,
        ctx=ctx,
        imagen_base64=imagen_b64,
        imagen_mime=imagen_mime,
        extra_system=extra_system,
        persona_file=ident.persona_prompt_file,
    )

    texto_final = (respuesta.texto or "").strip()
    if not texto_final:
        log.warning("flow.respuesta_vacia", cliente=cliente_numero)
        return ctx.get("outbox", [])

    # Race-condition guard: si mientras procesábamos llegó OTRO mensaje del
    # mismo cliente, abortar este flow (no enviar) — el siguiente flow va a
    # procesar todo junto con contexto completo. Esto evita que el bot
    # responda 3 veces a un cliente que mandó 3 mensajes seguidos.
    from sqlalchemy import text as _sa_text
    msg_actual_id = msg.id
    nuevo_inbound = (await session.execute(_sa_text("""
        SELECT 1 FROM conversaciones
        WHERE cliente_id = :cid AND direccion = 'inbound'
          AND whapi_message_id IS NOT NULL
          AND whapi_message_id != :curr
          AND timestamp > (SELECT timestamp FROM conversaciones WHERE whapi_message_id = :curr LIMIT 1)
        LIMIT 1
    """), {"cid": cliente_id, "curr": msg_actual_id})).first()
    if nuevo_inbound:
        log.info(
            "flow.abortado_mensaje_mas_nuevo",
            cliente=cliente_numero, msg_id=msg_actual_id,
        )
        return ctx.get("outbox", [])

    # 5. Humanización (anti-detección) — clave en un número con tráfico público
    if settings.feature_humanizacion:
        # Modo 24/7: el bot responde a cualquier hora (La Cantina opera de noche).
        # Configurable vía FEATURE_RESPONDER_24_7. Si está en false, respeta el
        # horario configurado y deja los mensajes pendientes para la apertura.
        if not settings.feature_responder_24_7 and not dentro_horario():
            apertura = proxima_hora_apertura()
            log.info("flow.fuera_de_horario", cliente=cliente_numero, proxima=apertura.isoformat())
            await guardar_conversacion(
                session, cliente_id=cliente_id, direccion="outbound", tipo="texto",
                contenido=texto_final, intent=intent, modelo=respuesta.modelo,
                tokens_input=respuesta.tokens_input, tokens_output=respuesta.tokens_output,
                cache_read_tokens=respuesta.cache_read, cache_create_tokens=respuesta.cache_write,
                metadata={"no_enviado": True, "razon": "fuera_de_horario", "programado_para": apertura.isoformat()},
            )
            return ctx.get("outbox", [])

        ok, enviados, limite = await puede_enviar(session)
        if not ok:
            log.warning("flow.rate_limit", cliente=cliente_numero, enviados=enviados, limite=limite)
            await guardar_conversacion(
                session, cliente_id=cliente_id, direccion="outbound", tipo="texto",
                contenido=texto_final, intent=intent, modelo=respuesta.modelo,
                metadata={"no_enviado": True, "razon": "rate_limit"},
            )
            return ctx.get("outbox", [])

        if settings.humanization_typing_indicator:
            await enviar_typing(cliente_numero)
        await sleep_humano(texto_final)

    # 6. Enviar
    try:
        await enviar_texto(cliente_numero, texto_final)
        if settings.feature_humanizacion and settings.humanization_typing_indicator:
            await enviar_paused(cliente_numero)
    except Exception as e:
        log.exception("flow.enviar_whapi_fail", error=str(e))
        return ctx.get("outbox", [])

    # 6.5 Adjuntos pedidos por el bot (tras el texto): video / carta PDF / flyer del evento.
    if ctx.get("enviar_video_como_llegar"):
        await _enviar_video_como_llegar(session, cliente_id, cliente_numero)
    if ctx.get("enviar_carta_pdf"):
        await _enviar_carta_pdf(session, cliente_id, cliente_numero)
    if ctx.get("flyer_evento_fecha"):
        await _enviar_flyer_evento(session, cliente_id, cliente_numero, ctx["flyer_evento_fecha"])

    # 7. Persistir outbound
    await guardar_conversacion(
        session, cliente_id=cliente_id, direccion="outbound", tipo="texto",
        contenido=texto_final, intent=intent,
        tokens_input=respuesta.tokens_input, tokens_output=respuesta.tokens_output,
        cache_read_tokens=respuesta.cache_read, cache_create_tokens=respuesta.cache_write,
        modelo=respuesta.modelo,
        metadata={"tools_usadas": respuesta.tools_usadas, "costo_usd": str(respuesta.costo_usd)},
    )
    log.info(
        "flow.respondido", cliente=cliente_numero,
        tokens_in=respuesta.tokens_input, tokens_out=respuesta.tokens_output,
        cache_read=respuesta.cache_read, costo_usd=str(respuesta.costo_usd),
        tools=respuesta.tools_usadas,
    )
    return ctx.get("outbox", [])


async def _construir_contexto_cliente(
    session: AsyncSession, cliente_id: int, cliente_numero: str
) -> str:
    """Bloque dinámico (no cacheado) con lo que ya sabemos del cliente, para que
    el bot no vuelva a preguntar lo mismo y personalice a clientes recurrentes.

    Incluye: nombre, tags de seguimiento (actuales + disponibles), y memoria
    evolutiva (preferencias aprendidas, ej. "Pedro siempre pide la mesa 12").

    NOTA: las reservas activas del cliente NO se cargan aquí porque el backend
    de mesas no expone (todavía) un endpoint de "reservas por teléfono". Si el
    cliente pregunta por su reserva, el bot usa la tool `consultar_reserva_cliente`
    con el id. Cuando exista el endpoint, añadir aquí la query.
    """
    # ── FECHA/HORA ACTUAL — crítico: sin esto el bot inventa la fecha al
    #    convertir "hoy/mañana/el viernes" a YYYY-MM-DD para las tools. ──────
    lineas: list[str] = [_bloque_fecha_actual(), ""]
    lineas.append("## LO QUE YA SÉ DEL CLIENTE (úsalo, NO vuelvas a preguntarlo)")
    lineas.append(f"- Número: {cliente_numero}")

    cliente = (await session.execute(
        select(Cliente).where(Cliente.id == cliente_id)
    )).scalar_one_or_none()
    if cliente and cliente.nombre:
        lineas.append(f"- Nombre: {cliente.nombre}")

    # Tags actuales aplicados al cliente + lista de tags disponibles para
    # aplicar_tag_seguimiento (si la tool existe en el flujo equipo).
    from sqlalchemy import text as _sa_text
    tag_rows = (await session.execute(_sa_text("""
        SELECT t.nombre,
               (ct.cliente_id IS NOT NULL) AS asignado,
               t.descripcion
          FROM tags t
          LEFT JOIN cliente_tags ct
            ON ct.tag_id = t.id AND ct.cliente_id = :cid
         ORDER BY t.orden ASC, t.nombre ASC
    """), {"cid": cliente_id})).all()
    asignados = [r.nombre for r in tag_rows if r.asignado]
    if asignados:
        lineas.append(f"- Tags actuales: {', '.join(asignados)}")

    # Memoria evolutiva: preferencias aprendidas de este cliente + reglas generales.
    from app import memoria as mem
    memorias = await mem.cargar_relevantes(session, contacto_id=cliente_id)
    bloque_mem = mem.formatear_para_prompt(memorias)

    if len(lineas) <= 2 and not bloque_mem:
        return ""  # casi sin datos útiles
    lineas.append("")
    lineas.append("Si el cliente ya dio un dato arriba o en el historial, NO se lo vuelvas a preguntar.")
    texto = "\n".join(lineas)
    if bloque_mem:
        texto += "\n\n" + bloque_mem
    return texto
