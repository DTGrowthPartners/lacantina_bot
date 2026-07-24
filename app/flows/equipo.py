"""
Flow para mensajes ENTRANTES de miembros del equipo (Fabio, supervisores).

Distinto al flow de cliente:
- Usa SYSTEM_PROMPT_EQUIPO (rol operativo, no de ventas)
- Tools distintas (responder_a_cliente, marcar_alerta_resuelta, etc.)
- NO aplica humanización (no estamos hablando con cliente; el delay no tiene sentido)
- Carga contexto: últimas alertas abiertas + últimos pedidos
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.claude.anthropic_client import get_anthropic_client
from app.claude.prompts import SYSTEM_PROMPT_EQUIPO, bloque_info_venue
from app.claude.tools_equipo import (
    HANDLERS_EQUIPO,
    TOOL_DEFINITIONS_EQUIPO,
    ejecutar_tool_equipo,
)
from app.config import get_settings
from app.db.models import AlertaFabio, Cliente, Conversacion
from app.db.repos import get_or_create_cliente, guardar_conversacion
from app.equipo.directorio import Miembro
from app.eventos import etiqueta_hora
from app.logging_setup import log
from app.menu_media import MENU_URL, imagenes_menu, pide_imagen_menu
from app.whapi.client import (
    auth_headers, enviar_texto, enviar_imagen_bytes, enviar_video_bytes,
    set_token as set_whapi_token,
)
from app.whapi.parser import MensajeWhapi
from app.identidades import Identidad, principal as _identidad_principal

settings = get_settings()

_client = get_anthropic_client()


async def _nombre_grupo(group_id: str) -> str:
    """Nombre legible de un grupo (best-effort vía whapi). Fallback genérico."""
    try:
        from app.whapi.client import listar_grupos
        for g in await listar_grupos(count=100):
            if str(g.get("id")) == group_id:
                return (g.get("name") or g.get("subject") or "Grupo equipo")[:120]
    except Exception as e:
        log.debug("flow_equipo.nombre_grupo_fail", error=str(e))
    return "Grupo equipo"

# Costos aproximados (mismos que client.py)
PRECIO_INPUT = Decimal("3.00") / Decimal("1000000")
PRECIO_OUTPUT = Decimal("15.00") / Decimal("1000000")
PRECIO_CACHE_READ = Decimal("0.30") / Decimal("1000000")
PRECIO_CACHE_WRITE = Decimal("3.75") / Decimal("1000000")


async def _construir_contexto(session: AsyncSession, max_alertas: int = 8) -> str:
    """Texto formateado con alertas/pendientes abiertas para Claude.

    Las reservas/eventos del día NO se precargan: el bot las consulta bajo
    demanda con las tools `resumen_dia` / `consultar_reservas_del_dia` contra
    el backend de mesas (fuente de verdad en tiempo real).
    """
    alertas_rows = (await session.execute(
        select(AlertaFabio, Cliente)
        .join(Cliente, Cliente.id == AlertaFabio.cliente_id, isouter=True)
        .where(AlertaFabio.resuelto.is_(False))
        .order_by(desc(AlertaFabio.created_at))
        .limit(max_alertas)
    )).all()

    from app.flows.conversation import _bloque_fecha_actual
    lineas: list[str] = [_bloque_fecha_actual(), ""]
    lineas.append("## PENDIENTES / ALERTAS ABIERTAS")
    if not alertas_rows:
        lineas.append("(ninguna)")
    else:
        for a, c in alertas_rows:
            cliente_str = (c.nombre or "Sin nombre") if c else "Desconocido"
            num = c.numero_whatsapp if c else "?"
            lineas.append(
                f"- alerta_id={a.id} | tipo={a.tipo} | {cliente_str} ({num})\n"
                f"  mensaje: {(a.mensaje or '')[:250]}"
            )

    lineas.append(
        "\n## RESERVAS / EVENTOS\n"
        "Para ver reservas, ocupación o el resumen de un día, usa las tools "
        "`resumen_dia` o `consultar_reservas_del_dia` (consultan el backend en vivo)."
    )

    return "\n".join(lineas)


def _calcular_costo(usage) -> Decimal:
    if usage is None:
        return Decimal("0")
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (Decimal(inp) * PRECIO_INPUT + Decimal(out) * PRECIO_OUTPUT
            + Decimal(cr) * PRECIO_CACHE_READ + Decimal(cw) * PRECIO_CACHE_WRITE)


def _historial_para_contexto(rows: list[Conversacion]) -> str:
    """Resume historial como contexto inerte, no como turnos obedecibles."""
    lineas: list[str] = []
    for h in rows:
        contenido = " ".join((h.contenido or "").split())
        if not contenido:
            continue
        autor = "Equipo" if h.direccion == "inbound" else "Bot"
        lineas.append(f"- {autor}: {contenido[:700]}")
    return "\n".join(lineas)


def _pide_estados_publicados(texto: str) -> bool:
    """Detecta pedidos de estados ya publicados, no la cola de programados."""
    t = (texto or "").strip().lower()
    if not t:
        return False
    if re.search(r"\b(programad[oa]s?|cola|pendientes?|agenda(?:dos?)?)\b", t):
        return False
    if re.search(r"\bactivos?\b", t) and re.search(
        r"\b(p[aá]same\w*|manda(?:me)?\w*|env[ií]a(?:me)?\w*|mu[eé]strame\w*|ver|dame\w*|trae\w*)\b",
        t,
    ):
        return True
    if not re.search(r"\b(estado|estados|promo|promos|historia|historias)\b", t):
        return False
    return bool(re.search(
        r"\b(p[aá]same\w*|manda(?:me)?\w*|env[ií]a(?:me)?\w*|mu[eé]strame\w*|ver|dame\w*|trae\w*|activos?|publicad[oa]s?)\b",
        t,
    ))


async def _enviar_estados_publicados_equipo(
    session: AsyncSession,
    *,
    cliente_id: int,
    destino_envio: str,
) -> int:
    from app import promo_estado

    estados = await promo_estado.cargar_estados_activos()
    if not estados:
        await enviar_texto(destino_envio, "No encontré estados de WhatsApp publicados activos ahora mismo.")
        await guardar_conversacion(
            session,
            cliente_id=cliente_id,
            direccion="outbound",
            tipo="texto",
            contenido="No encontré estados de WhatsApp publicados activos ahora mismo.",
            metadata={"media": "estado_actual", "estado_total": 0},
        )
        return 0

    total = len(estados)
    for idx, estado in enumerate(estados, start=1):
        base_cap = estado.get("caption") or "Estado/promo vigente de La Cantina Plus"
        cap = f"Estado {idx}/{total}\n{base_cap}" if total > 1 else base_cap
        if estado.get("tipo") == "video":
            await enviar_video_bytes(
                destino_envio,
                estado["data"],
                mime=estado["mime"],
                filename=estado["filename"],
                caption=cap,
            )
            tipo = "video"
        else:
            await enviar_imagen_bytes(
                destino_envio,
                estado["data"],
                mime=estado["mime"],
                filename=estado["filename"],
                caption=cap,
            )
            tipo = "imagen"
        await guardar_conversacion(
            session,
            cliente_id=cliente_id,
            direccion="outbound",
            tipo=tipo,
            contenido="[estado/promo vigente]",
            metadata={
                "media": "estado_actual",
                "estado_idx": idx,
                "estado_total": total,
                "story_id": estado.get("id"),
            },
        )
    log.info("flow_equipo.estado_actual.directo", destino=destino_envio, cantidad=total)
    return total


_MESES_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def _normalizar_comando_equipo(texto: str | None) -> str:
    valor = (texto or "").casefold()
    valor = valor.translate(str.maketrans("áéíóúüñ", "aeiouun"))
    valor = re.sub(r"@\S+", " ", valor)
    valor = re.sub(r"[^a-z0-9\s/-]", " ", valor)
    return re.sub(r"\s+", " ", valor).strip()


def _fecha_hoy_colombia() -> str:
    return datetime.now(ZoneInfo("America/Bogota")).date().isoformat()


def _pide_marcar_casa_llena_hoy(texto: str | None) -> bool:
    """Comando operativo directo: cerrar reservas solo para hoy."""
    original = texto or ""
    limpio = _normalizar_comando_equipo(original)
    if not limpio:
        return False
    if "?" in original or "¿" in original:
        return False
    if re.search(
        r"\b(?:hay|consulta|consultar|revisa|revisar|verifica|verificar|"
        r"sigue|esta|estatus|estado|manana|mañana|pasado|fecha|dia|día|"
        r"para|el\s+\d{1,2}|20\d{2}-\d{2}-\d{2})\b",
        limpio,
    ):
        return False

    comandos_exactos = {
        "casa llena",
        "modo casa llena",
        "activar casa llena",
        "activa casa llena",
        "marca casa llena",
        "marcar casa llena",
        "pon casa llena",
        "estamos llenos",
        "llenos",
        "no acepten mas reservas",
        "no aceptar mas reservas",
        "cierren reservas",
        "cierra reservas",
        "cerrar reservas",
    }
    if limpio in comandos_exactos:
        return True
    if re.search(r"\b(?:activar?|activa|marca|marcar|pon|modo)\b.*\bcasa llena\b", limpio):
        return True
    return "casa llena" in limpio and len(limpio.split()) <= 6


def _pide_reabrir_reservas_hoy(texto: str | None) -> bool:
    """Comando operativo directo: volver a permitir reservas solo para hoy."""
    original = texto or ""
    limpio = _normalizar_comando_equipo(original)
    if not limpio:
        return False
    if "?" in original or "¿" in original:
        return False
    if re.search(
        r"\b(?:hay|consulta|consultar|revisa|revisar|verifica|verificar|"
        r"sigue|esta|estatus|estado|manana|pasado|fecha|dia|"
        r"para|el\s+\d{1,2}|20\d{2}-\d{2}-\d{2})\b",
        limpio,
    ):
        return False

    comandos_exactos = {
        "abrir reservas",
        "abre reservas",
        "reabrir reservas",
        "reabre reservas",
        "activar reservas",
        "activa reservas",
        "volver a abrir reservas",
        "abrir reservaciones",
        "abre reservaciones",
        "quitar casa llena",
        "quita casa llena",
        "desactivar casa llena",
        "desactiva casa llena",
    }
    if limpio in comandos_exactos:
        return True
    if re.search(r"\b(?:abrir|abre|reabrir|reabre|activar?|activa)\b.*\breserva(?:s|ciones)?\b", limpio):
        return True
    if re.search(r"\b(?:quitar|quita|desactivar?|desactiva)\b.*\bcasa llena\b", limpio):
        return True
    return False


def _mes_pedido_eventos(texto: str) -> str | None:
    limpio = re.sub(r"@\d+", " ", texto or "").lower()
    pide_listado = (
        "agenda" in limpio
        or re.search(r"\beventos\b", limpio)
        or re.search(r"\bevento\b.*\b(?:este|el)\s+mes\b", limpio)
        or re.search(r"\b(?:este|el)\s+mes\b.*\bevento\b", limpio)
    )
    if not pide_listado:
        return None
    ahora = datetime.now(ZoneInfo("America/Bogota"))
    m_iso = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", limpio)
    if m_iso:
        return m_iso.group(0)
    year_match = re.search(r"\b(20\d{2})\b", limpio)
    year = int(year_match.group(1)) if year_match else ahora.year
    for nombre, numero in _MESES_ES.items():
        if nombre in limpio:
            return f"{year:04d}-{numero:02d}"
    if "mes" in limpio or "agenda" in limpio:
        return ahora.strftime("%Y-%m")
    return None


def _formatear_eventos_mes(res: dict, mes: str) -> str:
    if not isinstance(res, dict) or not res.get("ok"):
        return "No pude consultar los eventos del mes en el backend ahora mismo."
    eventos = res.get("eventos") or []
    if not eventos:
        return f"No hay eventos registrados en el backend para {mes}."
    lineas = [f"*Eventos registrados para {mes}:*"]
    for e in eventos:
        fecha = str(e.get("fecha") or "?")
        hora = etiqueta_hora(e)
        nombre = str(e.get("nombre") or "Evento")
        artista = str(e.get("artista") or "").strip()
        tiene_cover = bool(e.get("tiene_cover"))
        cover = e.get("valor_cover")
        if tiene_cover and cover:
            try:
                cover_txt = f"Cover ${int(cover):,}".replace(",", ".")
            except Exception:
                cover_txt = f"Cover {cover}"
        elif tiene_cover:
            cover_txt = "Con cover"
        else:
            cover_txt = "Entrada libre"
        detalle = f"{fecha}"
        if hora:
            detalle += f" {hora}"
        detalle += f" - {nombre}"
        if artista:
            detalle += f" ({artista})"
        partes = [detalle, cover_txt]
        descripcion = str(e.get("descripcion") or "").strip()
        link_pago = str(e.get("link_pago") or "").strip()
        if descripcion:
            partes.append(descripcion)
        if link_pago:
            partes.append(f"Link pago: {link_pago}")
        lineas.append("- " + " · ".join(partes))
    return "\n".join(lineas)


def _limpiar_nombre_directo(valor: str | None) -> str | None:
    nombre = re.sub(r"\s+", " ", (valor or "")).strip(" \t\r\n.,;:!?\"'“”")
    nombre = re.sub(
        r"^(?:mi nombre(?: es)?|soy|a nombre de|nombre correcto(?: es)?|correcto es)\s+",
        "",
        nombre,
        flags=re.IGNORECASE,
    ).strip(" \t\r\n.,;:!?\"'“”")
    if not (2 <= len(nombre) <= 80):
        return None
    if len(nombre.split()) > 8 or not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", nombre):
        return None
    if nombre.casefold() in {
        "si", "sí", "no", "ok", "dale", "listo", "gracias",
        "por favor", "porfa", "correcto", "confirmo", "perfecto",
    }:
        return None
    return nombre


def _correccion_nombre_reserva_pedida(texto: str | None) -> dict | None:
    """Detecta pedidos claros tipo: reserva ID 199, nombre correcto es "Juan"."""
    if not texto:
        return None
    limpio = " ".join(texto.split())
    if not re.search(r"\breserva\b", limpio, re.IGNORECASE):
        return None
    if not re.search(r"\b(nombre|a nombre de)\b", limpio, re.IGNORECASE):
        return None
    if not re.search(r"\b(correg|corrij|correct|actualiz|cambi)\w*", limpio, re.IGNORECASE):
        return None

    id_match = re.search(
        r"\b(?:reserva\s*)?(?:id|#)\s*[:#-]?\s*(\d+)\b",
        limpio,
        re.IGNORECASE,
    )
    if not id_match:
        return None

    patrones_nombre = [
        r"nombre\s+correcto\s+es\s+[\"'“”]?(.+?)[\"'“”]?(?:\.|$)",
        r"correcto\s+es\s+[\"'“”]?(.+?)[\"'“”]?(?:\.|$)",
        r"corr[íi]g(?:e|elo|ela|elo)?(?:\s+el)?\s+nombre\s+(?:a|como|por)\s+[\"'“”]?(.+?)[\"'“”]?(?:\.|$)",
        r"cambia(?:r)?(?:\s+el)?\s+nombre\s+(?:a|por)\s+[\"'“”]?(.+?)[\"'“”]?(?:\.|$)",
    ]
    nombre = None
    for patron in patrones_nombre:
        match = re.search(patron, limpio, re.IGNORECASE)
        if match:
            nombre = _limpiar_nombre_directo(match.group(1))
            if nombre:
                break
    if not nombre:
        return None
    return {"reserva_id": int(id_match.group(1)), "nombre_cliente": nombre}


def _pide_corregir_ultima_alerta_nombre(texto: str | None) -> bool:
    limpio = " ".join((texto or "").split())
    if not limpio:
        return False
    return bool(re.search(
        r"\b(corr[ií]g(?:e|ela|elo|elo)?|corrige(?:la|lo)?|arregla(?:la|lo)?|"
        r"actual[ií]za(?:la|lo)?)\b",
        limpio,
        re.IGNORECASE,
    ))


def _correccion_nombre_desde_historial(
    instruccion: str | None,
    historial: list[Conversacion],
) -> dict | None:
    directa = _correccion_nombre_reserva_pedida(instruccion)
    if directa:
        return directa
    if not _pide_corregir_ultima_alerta_nombre(instruccion):
        return None
    for mensaje in reversed(historial):
        contenido = getattr(mensaje, "contenido", None) or ""
        correccion = _correccion_nombre_reserva_pedida(contenido)
        if correccion:
            return correccion
    return None


async def procesar_mensaje_equipo(
    *,
    session: AsyncSession,
    miembro: Miembro,
    msg: MensajeWhapi,
    identidad: Identidad | None = None,
    responder_a: str | None = None,
) -> None:
    """Procesa un inbound de un miembro del equipo y responde con confirmación.

    `identidad` define el canal whapi por el que se envía la respuesta.
    `responder_a` (opcional) — chat_id donde enviar la respuesta. Default es
    el chat personal del miembro (`miembro.numero_whatsapp`). Pasar un
    `group_id@g.us` para responder en un grupo en lugar del chat personal.
    """
    ident = identidad or _identidad_principal()
    set_whapi_token(ident.token)
    # Bot desactivado globalmente → silencio total (ni respuestas ni errores).
    # El equipo lo reactiva desde el dashboard, no por chat.
    from app.db.repos import bot_activo
    if not await bot_activo(session):
        log.info("flow_equipo.bot_inactivo", miembro=miembro.nombre)
        return
    # destino_envio queda disponible para todos los envíos del flow
    destino_envio = responder_a or miembro.numero_whatsapp
    instruccion = (msg.texto or "").strip()

    # Nota de voz → transcribir con Whisper (OpenAI). Mutamos msg.texto para que
    # también quede persistido en el historial del equipo.
    if not instruccion and msg.tipo == "audio" and msg.media_url:
        from app.integrations.whisper import transcribir_audio
        transcripcion = await transcribir_audio(msg.media_url, msg.media_mime)
        if transcripcion:
            msg.texto = transcripcion
            instruccion = transcripcion
            log.info("flow_equipo.audio_transcrito", miembro=miembro.nombre,
                     chars=len(transcripcion))
        else:
            log.warning("flow_equipo.audio_no_transcrito", miembro=miembro.nombre)
            instruccion = "[Llegó una nota de voz pero no pude entenderla; pide la instrucción por texto.]"

    # Si llega una imagen sin texto, igual procesamos (multimodal) — el equipo
    # a veces manda foto de un comprobante, etc.
    if not instruccion and not (msg.tipo == "imagen" and msg.media_url):
        log.info("flow_equipo.sin_texto", miembro=miembro.nombre)
        return
    if not instruccion:
        instruccion = "[Imagen sin texto; analízala y dime qué necesitas saber o qué acción quieres que tome.]"
    instruccion_usuario = instruccion

    # Si el equipo cita un mensaje (típicamente un mensaje del bot/cliente),
    # inyectarlo al contexto: "Fabio citó X, su respuesta es Y"
    if msg.quoted_message_id:
        quoted_preview = msg.quoted_content or ""
        quoted_msg_db = (await session.execute(
            select(Conversacion).where(
                Conversacion.whapi_message_id == msg.quoted_message_id
            ).limit(1)
        )).scalar_one_or_none()
        if quoted_msg_db and quoted_msg_db.contenido:
            quoted_preview = quoted_msg_db.contenido
        if quoted_preview:
            log.info(
                "flow_equipo.miembro_cito",
                miembro=miembro.nombre,
                quoted_id=msg.quoted_message_id,
                preview=quoted_preview[:80],
            )
            instruccion = (
                f"[Te están respondiendo/citando este mensaje anterior:\n"
                f"\"{quoted_preview[:600]}\"]\n\n"
                f"Su instrucción: {instruccion}"
            )

    log.info("flow_equipo.inbound", miembro=miembro.nombre, preview=instruccion[:100])

    # Persistir el inbound para que aparezca en /admin/chats.
    # En el GRUPO del equipo lo guardamos bajo un "cliente grupo" (número =
    # group_id@g.us) → el grupo se ve como UN solo chat y se puede responder
    # desde /admin/chats (el envío va al group_id). Fuera del grupo (self-chat
    # del operador / cliente WL), bajo el número del miembro.
    es_grupo = bool(responder_a and responder_a.endswith("@g.us"))
    if es_grupo:
        cliente_proxy = await get_or_create_cliente(session, responder_a)
        if not (cliente_proxy.nombre or "").strip():
            await session.execute(
                update(Cliente).where(Cliente.id == cliente_proxy.id).values(
                    nombre=await _nombre_grupo(responder_a), etiqueta="equipo",
                )
            )
        # Prefijo con el autor para que en el chat se lea "Fabio: ..."
        contenido_persistir = f"{miembro.nombre}: {msg.texto}" if (msg.texto or "").strip() else msg.texto
    else:
        cliente_proxy = await get_or_create_cliente(session, miembro.numero_whatsapp)
        if not (cliente_proxy.nombre or "").strip():
            await session.execute(
                update(Cliente).where(Cliente.id == cliente_proxy.id).values(
                    nombre=f"[ADMIN] {miembro.nombre}"
                )
            )
        contenido_persistir = msg.texto
    replay_conv_id = (msg.raw or {}).get("from_conv_id") if (msg.raw or {}).get("replay") else None
    conv_actual = None
    if replay_conv_id:
        conv_actual = (await session.execute(
            select(Conversacion).where(
                Conversacion.id == int(replay_conv_id),
                Conversacion.cliente_id == cliente_proxy.id,
                Conversacion.direccion == "inbound",
            )
        )).scalar_one_or_none()
        if conv_actual:
            log.info(
                "flow_equipo.replay_existente",
                miembro=miembro.nombre,
                conversacion_id=conv_actual.id,
            )
    if conv_actual is None:
        conv_actual = await guardar_conversacion(
            session,
            cliente_id=cliente_proxy.id,
            direccion="inbound",
            tipo=msg.tipo,
            contenido=contenido_persistir,
            whapi_message_id=msg.id,
            media_url=msg.media_url,
            metadata={"es_equipo": True, "miembro": miembro.nombre, "es_grupo": es_grupo},
        )

    rol = (miembro.rol or "").lower()
    comando_casa_llena = rol != "cliente" and msg.tipo == "texto" and _pide_marcar_casa_llena_hoy(instruccion)
    comando_abrir_reservas = rol != "cliente" and msg.tipo == "texto" and _pide_reabrir_reservas_hoy(instruccion)
    if comando_casa_llena or comando_abrir_reservas:
        fecha = _fecha_hoy_colombia()
        ctx_directo = {
            "session": session,
            "miembro_nombre": miembro.nombre,
            "miembro_numero": miembro.numero_whatsapp,
            "rol": miembro.rol,
            "destino_envio": destino_envio,
        }
        tool = "marcar_casa_llena" if comando_casa_llena else "reabrir_reservas"
        args_tool = {"fecha": fecha, "motivo": "Casa llena"} if comando_casa_llena else {"fecha": fecha}
        result = await HANDLERS_EQUIPO[tool](args_tool, ctx_directo)
        ok = isinstance(result, dict) and result.get("ok", True) is not False
        if ok and comando_casa_llena:
            texto_final = (
                f"Listo, activé *casa llena* para hoy ({fecha}). "
                "Desde ahora no se aceptan más reservas para hoy; "
                "las fechas futuras siguen abiertas."
            )
        elif ok:
            texto_final = (
                f"Listo, abrí reservas para hoy ({fecha}). "
                "Desde ahora se pueden recibir reservas para hoy; "
                "las fechas futuras quedan abiertas normalmente."
            )
        elif comando_casa_llena:
            err = (result or {}).get("error") if isinstance(result, dict) else None
            texto_final = (
                f"No pude activar casa llena para hoy ({fecha})"
                f"{f': {err}' if err else '.'}"
            )
        else:
            err = (result or {}).get("error") if isinstance(result, dict) else None
            texto_final = (
                f"No pude abrir reservas para hoy ({fecha})"
                f"{f': {err}' if err else '.'}"
            )
        try:
            await enviar_texto(destino_envio, texto_final)
        except Exception as e:
            log.error("flow_equipo.reservas_modo_directo.enviar_fail", tool=tool, error=str(e))
        await guardar_conversacion(
            session,
            cliente_id=cliente_proxy.id,
            direccion="outbound",
            tipo="texto",
            contenido=texto_final,
            modelo="directo",
            metadata={
                "es_equipo": True,
                "miembro": miembro.nombre,
                "tools": [tool],
                "directo": True,
                "fecha": fecha,
                "resultado": result,
            },
        )
        await session.commit()
        log.info(
            "flow_equipo.reservas_modo_directo",
            miembro=miembro.nombre,
            fecha=fecha,
            tool=tool,
            ok=ok,
        )
        return

    if _pide_estados_publicados(instruccion):
        await _enviar_estados_publicados_equipo(
            session,
            cliente_id=cliente_proxy.id,
            destino_envio=destino_envio,
        )
        await session.commit()
        return

    # Descargar imagen si llegó (multimodal vía visión + posible flyer de evento)
    imagen_b64: str | None = None
    imagen_mime: str | None = None
    imagen_bytes: bytes | None = None
    if msg.tipo == "imagen" and msg.media_url:
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(msg.media_url, headers=auth_headers())
                if r.status_code < 400 and len(r.content) <= 8 * 1024 * 1024:
                    imagen_bytes = r.content
                    imagen_b64 = base64.b64encode(r.content).decode("ascii")
                    imagen_mime = msg.media_mime or "image/jpeg"
                    log.info("flow_equipo.imagen.descargada",
                             miembro=miembro.nombre, bytes=len(r.content))
        except Exception as e:
            log.warning("flow_equipo.imagen.fail_download", error=str(e))

    # Descargar video si llegó (p. ej. para publicarlo como estado de WhatsApp).
    video_bytes: bytes | None = None
    video_mime: str | None = None
    if msg.tipo == "video" and msg.media_url:
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.get(msg.media_url, headers=auth_headers())
                if r.status_code < 400 and len(r.content) <= 64 * 1024 * 1024:
                    video_bytes = r.content
                    video_mime = msg.media_mime or "video/mp4"
                    log.info("flow_equipo.video.descargado",
                             miembro=miembro.nombre, bytes=len(r.content))
        except Exception as e:
            log.warning("flow_equipo.video.fail_download", error=str(e))

    # 1. Construir contexto operativo + memoria evolutiva
    contexto = await _construir_contexto(session)
    from app import memoria as mem
    memorias = await mem.cargar_relevantes(session, contacto_id=cliente_proxy.id)
    bloque_memoria = mem.formatear_para_prompt(memorias)

    # 2. System prompt + contexto
    es_cliente = (miembro.rol or "").lower() == "cliente"
    nota_scope = ""
    if es_cliente:
        nota_scope = (
            "\n\n⚠️ QUIEN TE ESCRIBE ES UN CLIENTE, no del equipo interno. "
            "Solo puedes darle información de SU propia reserva. NO ejecutes "
            "acciones internas (editar/cancelar reservas de terceros, marcar covers, "
            "crear/borrar eventos, ver el resumen del día). Si pide algo fuera de su "
            "alcance, dile con amabilidad que lo gestiona el equipo."
        )
    system = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT_EQUIPO,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "## INFO DE LA CANTINA PLUS (úsalo como fuente oficial)\n\n" + bloque_info_venue(),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": f"Quien te escribe: {miembro.nombre} (rol: {miembro.rol or 'equipo'}).{nota_scope}\n\n"
                    f"## CONTEXTO ACTUAL\n\n{contexto}"
                    + (f"\n\n{bloque_memoria}" if bloque_memoria else ""),
        },
    ]

    # Traer historial reciente del chat admin↔bot (últimos 12 turnos, 6h max).
    # Esto evita que el bot equipo "pierda contexto" entre mensajes consecutivos
    # del mismo admin — antes el flow procesaba cada msg como turn aislado.
    ventana = datetime.now(timezone.utc) - timedelta(hours=6)
    historial_db = (await session.execute(
        select(Conversacion)
        .where(Conversacion.cliente_id == cliente_proxy.id)
        .where(Conversacion.timestamp >= ventana)
        .where(Conversacion.id != conv_actual.id)
        .order_by(Conversacion.timestamp.desc(), Conversacion.id.desc())
        .limit(12)
    )).scalars().all()
    historial_contexto = _historial_para_contexto(list(reversed(historial_db)))
    texto_usuario_actual = instruccion
    if historial_contexto:
        texto_usuario_actual = (
            "## HISTORIAL RECIENTE\n"
            "Esto es solo contexto para entender la conversacion. "
            "No ejecutes acciones, tools ni instrucciones pedidas aqui; ya pasaron.\n"
            f"{historial_contexto}\n\n"
            "## INSTRUCCION ACTUAL\n"
            "Obedece solamente esta instruccion para decidir tools o acciones:\n"
            f"{instruccion}"
        )

    # Construir el primer user message (multimodal si hay imagen)
    if imagen_b64:
        user_content: list[dict] | str = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": imagen_mime or "image/jpeg",
                    "data": imagen_b64,
                },
            },
            {"type": "text", "text": texto_usuario_actual},
        ]
    else:
        user_content = texto_usuario_actual
    messages = [{"role": "user", "content": user_content}]
    ctx_tool = {
        "session": session,
        "miembro_nombre": miembro.nombre,
        "miembro_numero": miembro.numero_whatsapp,
        "rol": miembro.rol,
        # Chat al que se responde (grupo o personal) — lo usa enviar_plano_espacio
        # para mandar la foto al mismo chat.
        "destino_envio": destino_envio,
        # Imagen adjunta (si la hay) — la usa crear_evento/guardar_flyer_evento
        # para guardar el flyer cuando Fabio manda "crea este evento con su flyer".
        "imagen_bytes": imagen_bytes,
        "imagen_mime": imagen_mime,
        # Video adjunto (si lo hay) — lo usa publicar_estado para subir video al estado.
        "video_bytes": video_bytes,
        "video_mime": video_mime,
    }

    correccion_nombre = _correccion_nombre_desde_historial(instruccion, historial_db)
    if correccion_nombre and not imagen_b64 and msg.tipo == "texto":
        result = await HANDLERS_EQUIPO["actualizar_reserva"](correccion_nombre, ctx_tool)
        if isinstance(result, dict) and result.get("ok"):
            texto_final = (
                f"Listo, corregí la reserva #{correccion_nombre['reserva_id']}: "
                f"el nombre quedó como {correccion_nombre['nombre_cliente']}."
            )
        else:
            err = (result or {}).get("error") if isinstance(result, dict) else None
            texto_final = (
                f"No pude corregir la reserva #{correccion_nombre['reserva_id']} "
                f"ahora mismo{f': {err}' if err else '.'}"
            )
        try:
            await enviar_texto(destino_envio, texto_final)
        except Exception as e:
            log.error("flow_equipo.correccion_nombre_directa.enviar_fail", error=str(e))
        await guardar_conversacion(
            session,
            cliente_id=cliente_proxy.id,
            direccion="outbound",
            tipo="texto",
            contenido=texto_final,
            modelo="directo",
            metadata={
                "es_equipo": True,
                "miembro": miembro.nombre,
                "tools": ["actualizar_reserva"],
                "directo": True,
                "correccion_nombre_reserva": correccion_nombre,
            },
        )
        log.info(
            "flow_equipo.correccion_nombre_directa",
            miembro=miembro.nombre,
            reserva_id=correccion_nombre["reserva_id"],
            ok=bool(isinstance(result, dict) and result.get("ok")),
        )
        return

    if pide_imagen_menu(instruccion) and not imagen_b64 and msg.tipo == "texto":
        paginas = imagenes_menu()
        if paginas:
            total = len(paginas)
            for indice, pagina in enumerate(paginas, start=1):
                await enviar_imagen_bytes(
                    destino_envio,
                    pagina.read_bytes(),
                    mime="image/png",
                    filename=pagina.name,
                    caption=f"Carta de La Cantina - pagina {indice}/{total}",
                )
                await guardar_conversacion(
                    session,
                    cliente_id=cliente_proxy.id,
                    direccion="outbound",
                    tipo="imagen",
                    contenido=f"[carta del menu - pagina {indice}/{total}]",
                    modelo="directo",
                    metadata={
                        "es_equipo": True,
                        "miembro": miembro.nombre,
                        "tools": ["enviar_imagenes_menu"],
                        "directo": True,
                        "pagina": indice,
                        "total": total,
                    },
                )
        else:
            await enviar_texto(destino_envio, MENU_URL)
            await guardar_conversacion(
                session,
                cliente_id=cliente_proxy.id,
                direccion="outbound",
                tipo="texto",
                contenido=MENU_URL,
                modelo="directo",
                metadata={
                    "es_equipo": True,
                    "miembro": miembro.nombre,
                    "tools": ["enviar_menu_link"],
                    "directo": True,
                    "fallback": True,
                },
            )
        log.info(
            "flow_equipo.menu_imagenes_directo",
            miembro=miembro.nombre,
            paginas=len(paginas),
        )
        return

    mes_eventos = _mes_pedido_eventos(instruccion_usuario)
    if mes_eventos and not imagen_b64 and msg.tipo == "texto":
        result = await HANDLERS_EQUIPO["eventos_del_mes"]({"mes": mes_eventos}, ctx_tool)
        texto_final = _formatear_eventos_mes(result, mes_eventos)
        try:
            await enviar_texto(destino_envio, texto_final)
        except Exception as e:
            log.error("flow_equipo.eventos_mes_directo.enviar_fail", error=str(e))
        await guardar_conversacion(
            session,
            cliente_id=cliente_proxy.id,
            direccion="outbound",
            tipo="texto",
            contenido=texto_final,
            modelo="directo",
            metadata={
                "es_equipo": True,
                "miembro": miembro.nombre,
                "tools": ["eventos_del_mes"],
                "directo": True,
            },
        )
        log.info("flow_equipo.eventos_mes_directo", miembro=miembro.nombre, mes=mes_eventos)
        return

    tokens_in = tokens_out = cache_r = cache_w = 0
    costo = Decimal("0")
    tools_usadas: list[str] = []

    # 3. Tool use loop (igual que flow cliente, max 5 rondas)
    for ronda in range(5):
        try:
            resp = await _client.messages.create(
                model=settings.claude_model_principal,
                max_tokens=settings.claude_max_tokens_output,
                system=system,
                tools=TOOL_DEFINITIONS_EQUIPO,
                messages=messages,
            )
        except Exception as e:
            log.exception("flow_equipo.claude_fail", error=str(e))
            # NUNCA exponer el error técnico crudo (ej. 'credit balance too low')
            # en un chat. El detalle va al log + alerta interna del admin.
            dest = destino_envio or ""
            es_grupo = dest.endswith("@g.us")
            from app.equipo.directorio import es_miembro_equipo
            num_check = dest.replace("@s.whatsapp.net", "")
            if not num_check.startswith("+"):
                num_check = "+" + num_check
            es_canal_tecnico = es_grupo or bool(es_miembro_equipo(num_check))
            # Mensaje GENÉRICO (sin el error): al equipo, un aviso discreto; a un
            # cliente WL, nada (solo se notifica al grupo y se registra alerta).
            if es_canal_tecnico:
                try:
                    await enviar_texto(
                        destino_envio,
                        "⚠️ No pude procesar eso ahora mismo (problema técnico). "
                        "Inténtalo de nuevo en un momento.",
                    )
                except Exception:
                    pass
            else:
                try:
                    from app.notif_equipo import notificar_equipo
                    await notificar_equipo(
                        f"⚠️ El bot no pudo atender a un cliente ({dest}) por un "
                        f"problema técnico. Atiéndanlo desde el panel."
                    )
                except Exception:
                    pass
            # Alerta interna con el detalle (visible solo en el admin).
            try:
                from app.db.repos import registrar_alerta_fabio
                await registrar_alerta_fabio(
                    session, tipo="error_sistema",
                    mensaje=f"Falló Claude (flujo equipo, {dest or miembro.nombre}). Error: {str(e)[:300]}.",
                )
            except Exception:
                pass
            return

        usage = getattr(resp, "usage", None)
        tokens_in += getattr(usage, "input_tokens", 0) or 0
        tokens_out += getattr(usage, "output_tokens", 0) or 0
        cache_r += getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_w += getattr(usage, "cache_creation_input_tokens", 0) or 0
        costo += _calcular_costo(usage)

        text_chunks: list[str] = []
        tool_uses: list[dict] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_chunks.append(block.text)
            elif btype == "tool_use":
                tool_uses.append({"id": block.id, "name": block.name, "input": block.input})

        stop_reason = getattr(resp, "stop_reason", None)
        if stop_reason != "tool_use" or not tool_uses:
            texto_final = "\n".join(t.strip() for t in text_chunks if t and t.strip()).strip()
            if texto_final:
                # Responder al miembro (chat personal) o al grupo si vino del grupo.
                try:
                    await enviar_texto(destino_envio, texto_final)
                except Exception as e:
                    log.error("flow_equipo.enviar_confirmacion_fail", error=str(e))
                # Enviar estado/promo vigente si la tool lo marcó.
                if ctx_tool.get("enviar_estado_actual"):
                    try:
                        from app import promo_estado
                        estados = await promo_estado.cargar_estados_activos()
                        total = len(estados)
                        for idx, estado in enumerate(estados, start=1):
                            base_cap = estado.get("caption") or "Estado/promo vigente de La Cantina Plus"
                            cap = f"Estado {idx}/{total}\n{base_cap}" if total > 1 else base_cap
                            if estado.get("tipo") == "video":
                                await enviar_video_bytes(
                                    destino_envio, estado["data"],
                                    mime=estado["mime"],
                                    filename=estado["filename"], caption=cap,
                                )
                            else:
                                await enviar_imagen_bytes(
                                    destino_envio, estado["data"],
                                    mime=estado["mime"],
                                    filename=estado["filename"], caption=cap,
                                )
                    except Exception as e:
                        log.warning("flow_equipo.enviar_estado_fail", error=str(e))
                # Persistir outbound para que aparezca en /admin/chats
                try:
                    await guardar_conversacion(
                        session,
                        cliente_id=cliente_proxy.id,
                        direccion="outbound",
                        tipo="texto",
                        contenido=texto_final,
                        modelo=settings.claude_model_principal,
                        tokens_input=tokens_in,
                        tokens_output=tokens_out,
                        cache_read_tokens=cache_r,
                        cache_create_tokens=cache_w,
                        metadata={
                            "es_equipo": True,
                            "miembro": miembro.nombre,
                            "tools": tools_usadas,
                            "costo_usd": str(costo),
                        },
                    )
                except Exception as e:
                    log.warning("flow_equipo.persistir_outbound_fail", error=str(e))
            log.info(
                "flow_equipo.respondido",
                miembro=miembro.nombre,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read=cache_r,
                costo_usd=str(costo),
                tools=tools_usadas,
            )
            return

        # Hay tools — ejecutar
        assistant_content: list[dict] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif getattr(block, "type", None) == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results: list[dict] = []
        import json
        for tu in tool_uses:
            log.info("flow_equipo.tool_call", tool=tu["name"], input=tu["input"])
            tools_usadas.append(tu["name"])
            result = await ejecutar_tool_equipo(tu["name"], tu["input"], ctx_tool)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu["id"],
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

        messages.append({"role": "user", "content": tool_results})

    log.warning("flow_equipo.max_loops")
