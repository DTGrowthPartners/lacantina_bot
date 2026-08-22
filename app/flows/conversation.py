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
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.claude.client import conversar
from app.claude.intent import clasificar
from app.config import get_settings
from app.db.models import Cliente, Conversacion
from app.db.repos import bot_activo, guardar_conversacion, ultimos_mensajes
from app.event_media import MIME, flyer_path_evento
from app.integrations import cantina_api
from app.logging_setup import log
from app.menu_media import MENU_URL, pide_menu
from app.nombres import limpiar_nombre_reserva
from app.utils.humanizer import (
    dentro_horario,
    proxima_hora_apertura,
    puede_enviar,
    sleep_humano,
)
from app.whapi.client import (
    auth_headers,
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
_PLANO_ESPACIO = Path(settings.data_dir) / "media" / "plano-espacio.png"
_INTENTS_ESCALACION_OBLIGATORIA = {"pide_humano", "queja"}
_CANCELACION_PENDIENTE_KEY = "cancelacion_reserva_pendiente"


def _texto_simple_confirmacion(valor: str | None) -> str:
    texto = (valor or "").casefold()
    texto = texto.translate(str.maketrans("áéíóúüñ", "aeiouun"))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return " ".join(texto.split())


def _respuesta_confirmacion_cancelacion(valor: str | None) -> str | None:
    texto = _texto_simple_confirmacion(valor)
    if not texto:
        return None
    if texto in {
        "si", "s", "si claro", "claro", "dale", "ok", "okay", "confirmo",
        "confirmar", "confirmala", "cancelala", "cancela", "cancelar",
        "si cancela", "si cancelar", "si cancelala", "si confirmo",
    }:
        return "si"
    if texto in {
        "no", "nop", "no gracias", "dejala", "dejela", "dejalo asi",
        "dejarla", "mantener", "mantenla", "no cancelar", "no la canceles",
        "no canceles", "mejor no",
    }:
        return "no"
    return None


def _cancelacion_pendiente_vigente(pendiente: dict | None) -> dict | None:
    if not isinstance(pendiente, dict):
        return None
    raw = pendiente.get("creada_en")
    try:
        creada = datetime.fromisoformat(str(raw))
    except Exception:
        return None
    if creada.tzinfo is None:
        creada = creada.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - creada > timedelta(minutes=30):
        return None
    if not pendiente.get("reserva_id"):
        return None
    return pendiente


async def _metadata_cliente(session: AsyncSession, cliente_id: int) -> dict:
    row = (await session.execute(
        select(Cliente.metadata_).where(Cliente.id == cliente_id)
    )).first()
    return dict((row[0] if row else None) or {})


async def _guardar_metadata_cliente(session: AsyncSession, cliente_id: int, metadata: dict) -> None:
    await session.execute(
        update(Cliente)
        .where(Cliente.id == cliente_id)
        .values(metadata_=metadata)
    )


async def _resolver_cancelacion_pendiente(
    *,
    session: AsyncSession,
    cliente_id: int,
    cliente_numero: str,
    contenido_usuario: str,
) -> bool:
    metadata = await _metadata_cliente(session, cliente_id)
    pendiente = _cancelacion_pendiente_vigente(metadata.get(_CANCELACION_PENDIENTE_KEY))
    if not pendiente:
        if _CANCELACION_PENDIENTE_KEY in metadata:
            metadata.pop(_CANCELACION_PENDIENTE_KEY, None)
            await _guardar_metadata_cliente(session, cliente_id, metadata)
        return False

    decision = _respuesta_confirmacion_cancelacion(contenido_usuario)
    reserva_id = pendiente.get("reserva_id")
    mesa = pendiente.get("mesa_numero")
    fecha = pendiente.get("fecha")
    if decision is None:
        texto = (
            "Antes de seguir, necesito confirmar esto: "
            f"¿estás seguro de que quieres cancelar tu reserva"
            f"{f' de la mesa {mesa}' if mesa else ''}"
            f"{f' para el {fecha}' if fecha else ''}? Responde *Sí* o *No*."
        )
        await enviar_texto(cliente_numero, texto)
        await guardar_conversacion(
            session, cliente_id=cliente_id, direccion="outbound", tipo="texto",
            contenido=texto, intent="cancelar_reserva",
            metadata={"cancelacion_reserva_pendiente": True},
        )
        log.info("flow.cancelacion_pendiente.repregunta", cliente=cliente_numero, reserva_id=reserva_id)
        return True

    metadata.pop(_CANCELACION_PENDIENTE_KEY, None)
    await _guardar_metadata_cliente(session, cliente_id, metadata)

    if decision == "no":
        texto = "Perfecto, mantengo tu reserva activa. No cancelé nada. ✅"
        await enviar_texto(cliente_numero, texto)
        await guardar_conversacion(
            session, cliente_id=cliente_id, direccion="outbound", tipo="texto",
            contenido=texto, intent="cancelar_reserva",
            metadata={"cancelacion_reserva_confirmada": False, "reserva_id": reserva_id},
        )
        log.info("flow.cancelacion_pendiente.rechazada", cliente=cliente_numero, reserva_id=reserva_id)
        return True

    tipo = pendiente.get("tipo_reserva")
    try:
        if tipo == "sala":
            res = await cantina_api.cancelar_reserva_sala(reserva_id)
        elif pendiente.get("grupo_id"):
            res = await cantina_api.cancelar_grupo(pendiente["grupo_id"])
        else:
            res = await cantina_api.cancelar_reserva(reserva_id)
    except Exception as e:
        res = {"ok": False, "error": str(e)}

    if isinstance(res, dict) and res.get("ok", True):
        texto = "Listo ✅ cancelé tu reserva."
        if fecha:
            texto += f" Fecha: {fecha}."
        if mesa:
            texto += f" Mesa: {mesa}."
    else:
        texto = (
            "Intenté cancelar tu reserva, pero hubo un problema técnico. "
            "Ya lo estoy revisando para que no quede en el aire."
        )
        log.warning(
            "flow.cancelacion_pendiente.fail",
            cliente=cliente_numero,
            reserva_id=reserva_id,
            error=(res or {}).get("error") if isinstance(res, dict) else str(res),
        )

    await enviar_texto(cliente_numero, texto)
    await guardar_conversacion(
        session, cliente_id=cliente_id, direccion="outbound", tipo="texto",
        contenido=texto, intent="cancelar_reserva",
        metadata={
            "cancelacion_reserva_confirmada": True,
            "reserva_id": reserva_id,
            "resultado": res,
        },
    )
    log.info("flow.cancelacion_pendiente.ejecutada", cliente=cliente_numero, reserva_id=reserva_id)
    return True


def _normalizar_texto_corto(texto: str | None) -> str:
    t = (texto or "").strip().lower()
    t = t.translate(str.maketrans("áéíóúüñ", "aeiouun"))
    t = re.sub(r"[^\w\s]+", " ", t)
    return " ".join(t.split())


def _es_cierre_simple_sin_equipo(texto: str | None) -> bool:
    """Mensajes amables/logisticos cortos no requieren alerta humana."""
    t = _normalizar_texto_corto(texto)
    if not t or len(t) > 80:
        return False

    cierres_exactos = {
        "gracias",
        "muchas gracias",
        "ok",
        "oka",
        "okay",
        "dale",
        "listo",
        "perfecto",
        "super",
        "bueno",
        "entiendo",
        "entendido",
        "confirmado",
        "de acuerdo",
        "esta bien",
        "vale",
        "genial",
    }
    if t in cierres_exactos:
        return True

    if re.fullmatch(
        r"(si\s+)?(?:ya\s+)?(?:vamos|voy|vengo|salimos|llegamos)(?:\s+para|\s+pa|\s+a)?\s+(?:alla|la cantina|el sitio)",
        t,
    ):
        return True
    if re.fullmatch(r"(?:si\s+)?(?:ya\s+)?(?:vamos|voy|vengo|salimos|llegamos).{0,35}(?:gracias|listo|dale)", t):
        return True
    if re.fullmatch(r"(?:gracias|listo|dale|ok|perfecto).{0,35}", t):
        return True

    return False


def _es_pedido_info_general_sin_equipo(texto: str | None) -> bool:
    """Leads normales pidiendo informacion no requieren alerta humana."""
    t = _normalizar_texto_corto(texto)
    if not t or len(t) > 120:
        return False
    if re.search(
        r"\b(?:mas|m[aá]s)\s+informacion\b|\binformacion\b|\binfo\b",
        t,
    ):
        return True
    if re.fullmatch(
        r"(?:hola|buenas|buenas noches|buenas tardes|buenos dias)?\s*"
        r"(?:quiero|quisiera|dame|me das|me regalas|necesito|puedes darme)\s+"
        r"(?:info|informacion|mas informacion).{0,30}",
        t,
    ):
        return True
    return False


def _es_pedido_asesor_generico_sin_equipo(texto: str | None) -> bool:
    """Pedir asesor sin explicar el motivo no amerita aviso al grupo."""
    t = _normalizar_texto_corto(texto)
    if not t or len(t) > 160:
        return False
    pide_asesor = re.search(
        r"\b(?:asesor|asesora|humano|persona|alguien|administrador|encargad[oa])\b",
        t,
    )
    if not pide_asesor:
        return False
    motivo_real = re.search(
        r"\b(?:queja|problema|error|reclamo|molest[oa]|comprobante|pago|"
        r"transferencia|reserva|reservacion|mesa|cover|devolucion|reembolso|"
        r"contratar|contratacion|evento privado|cumpleanos|cumpleanios|"
        r"corporativo|artista|show|presentarme|tocar|cantar)\b",
        t,
    )
    if motivo_real:
        return False
    return True


def _es_pregunta_vestimenta_sin_equipo(texto: str | None) -> bool:
    t = _normalizar_texto_corto(texto)
    if not t or len(t) > 220:
        return False
    return bool(re.search(
        r"\b(?:codigo\s+de\s+vestimenta|vestimenta|dress\s*code|bermudas?|"
        r"chanclas?|sandalias?|shorts?|pantaloneta)\b",
        t,
    ))


def _pide_plano_espacio(texto: str) -> bool:
    """Detecta pedidos explícitos del plano/mapa del salón."""
    t = (texto or "").lower()
    if not t.strip():
        return False
    if not re.search(r"\b(plano|mapa|distribuci[oó]n|croquis)\b", t):
        return False
    return bool(re.search(
        r"\b(env[ií]a(?:me)?|m[aá]nd(?:a|ame|as)?|p[aá]sa(?:me)?|muestra(?:me)?|ver|dame|quiero|puedo ver)\b",
        t,
    ))


def _normalizar_intent_por_reglas(
    intent: str,
    *,
    solicitud_menu: bool,
    solicitud_plano: bool = False,
    contenido_usuario: str,
    cliente_numero: str,
) -> str:
    """Reglas deterministas que tienen prioridad sobre el clasificador."""
    if solicitud_plano and intent == "pide_estado":
        log.warning(
            "flow.intent_estado_anulado_por_plano",
            cliente=cliente_numero,
            texto=contenido_usuario[:120],
        )
        return "otro"
    if solicitud_menu and intent == "pide_estado":
        log.warning(
            "flow.intent_estado_anulado_por_menu",
            cliente=cliente_numero,
            texto=contenido_usuario[:120],
        )
        return "otro"
    return intent



_RE_COMPROBANTE_EXPLICITO = re.compile(
    r"\b("
    r"comprobante|soporte|recibo|captura|pantallazo|transfer(?:encia)?|"
    r"nequi|daviplata|bancolombia|consignaci[oó]n|pag(?:o|ue|ué|ado|ada)|cover"
    r")\b",
    re.IGNORECASE,
)
_TIPOS_COMPROBANTE_MEDIA = {"imagen", "pdf", "documento"}


def _es_media_comprobante(msg: MensajeWhapi) -> bool:
    return bool(msg.media_url and msg.tipo in _TIPOS_COMPROBANTE_MEDIA)


def _texto_menciona_comprobante(texto: str | None) -> bool:
    return bool(_RE_COMPROBANTE_EXPLICITO.search(texto or ""))


def _ultimo_outbound_texto(historial_db: list) -> str:
    return next(
        (
            getattr(h, "contenido", "") or ""
            for h in reversed(historial_db)
            if getattr(h, "direccion", None) in ("outbound", "humano")
            and getattr(h, "contenido", None)
        ),
        "",
    )


def _esperaba_confirmacion_reserva(historial_db: list) -> bool:
    ultimo_outbound = _ultimo_outbound_texto(historial_db)
    if not ultimo_outbound:
        return False
    texto = ultimo_outbound.lower()
    if not re.search(r"\bconfirm(?:amos|ar|as|o|e|en)\b", texto):
        return False
    return bool(re.search(r"\bmesa|reserva|personas|a nombre de\b", texto))


def _esperaba_comprobante_pago(historial_db: list) -> bool:
    ultimo_outbound = _ultimo_outbound_texto(historial_db)
    if not ultimo_outbound:
        return False
    texto = ultimo_outbound.lower()
    pide_comprobante = re.search(
        r"\b(comprobante|soporte|recibo|pantallazo|captura)\b", texto
    )
    contexto_pago = re.search(
        r"\b(pago|pagues|pagar|cover|transferencia|nequi|daviplata|reserva)\b",
        texto,
    )
    return bool(pide_comprobante and contexto_pago)


def _contenido_real_cliente(msg: MensajeWhapi, contenido_usuario: str) -> str:
    texto = (msg.texto or "").strip()
    if texto:
        return texto
    contenido = (contenido_usuario or "").strip()
    if contenido.startswith("[El cliente envió "):
        return ""
    return contenido


def _debe_auto_encolar_comprobante(
    *,
    msg: MensajeWhapi,
    intent: str,
    contenido_usuario: str,
    historial_db: list,
) -> bool:
    if not _es_media_comprobante(msg):
        return False
    texto_real = _contenido_real_cliente(msg, contenido_usuario)
    if _texto_menciona_comprobante(texto_real):
        return True
    if _esperaba_confirmacion_reserva(historial_db) or _esperaba_comprobante_pago(historial_db):
        return True
    return False


def _asegurar_escalacion_humana(
    outbox: list[dict],
    *,
    intent: str,
    cliente_numero: str,
    mensaje_cliente: str,
    media_url: str | None = None,
    media_mime: str | None = None,
) -> bool:
    """Encola el aviso si Claude omitio la tool en un caso humano obligatorio."""
    if intent not in _INTENTS_ESCALACION_OBLIGATORIA:
        return False
    if _es_cierre_simple_sin_equipo(mensaje_cliente):
        return False
    if _es_pedido_info_general_sin_equipo(mensaje_cliente):
        return False
    if _es_pedido_asesor_generico_sin_equipo(mensaje_cliente):
        return False
    if _es_pregunta_vestimenta_sin_equipo(mensaje_cliente):
        return False
    if any(item.get("clase") == "escalacion" for item in outbox):
        return False

    mensaje = (mensaje_cliente or "").strip() or "[Mensaje sin texto]"
    item = {
        "clase": "escalacion",
        "tipo": intent,
        "mensaje": (
            "🙋 *Cliente necesita ayuda del equipo*\n"
            f"Cliente: {cliente_numero}\n"
            f"Consulta: {mensaje[:800]}"
        ),
        "cliente_numero": cliente_numero,
    }
    if media_url:
        item["media_url"] = media_url
        item["media_mime"] = media_mime
    outbox.append(item)
    return True


def _limpiar_nombre_reserva(valor: str | None) -> str | None:
    """Normaliza un nombre que el cliente dio expresamente para la reserva."""
    return limpiar_nombre_reserva(valor)


def _nombre_marcado_en_texto(texto: str | None) -> str | None:
    """Extrae un nombre acompañado de una indicación inequívoca de reserva."""
    patrones = (
        r"(?:^|\b)a nombre de\s+(.+)$",
        r"(?:^|\b)mi nombre es\s+(.+)$",
        r"(?:^|\b)la reserva (?:es|va|ser[ií]a|quedar[ií]a) "
        r"(?:a nombre de|para)\s+(.+)$",
    )
    for patron in patrones:
        coincidencia = re.search(patron, texto or "", flags=re.IGNORECASE)
        if coincidencia:
            return _limpiar_nombre_reserva(coincidencia.group(1))
    return None


def _pregunta_nombre_reserva(texto: str | None) -> bool:
    if re.search(r"a nombre de qui[eé]n aparto la mesa", texto or "", flags=re.IGNORECASE):
        return True
    return bool(re.search(
        r"a nombre de qui[eé]n|qu[eé] nombre (?:pongo|coloco)|"
        r"nombre para la reserva|c[oó]mo quieres que quede (?:el nombre|la reserva)|"
        r"confirma(?:s)? (?:exactamente )?c[oó]mo quieres que quede",
        texto or "",
        flags=re.IGNORECASE,
    ))


def _contenido_outbound_relevante_nombre(texto: str | None) -> bool:
    contenido = (texto or "").strip()
    if not contenido:
        return False
    if re.fullmatch(r"\[[^\]]{0,80}\]", contenido):
        return False
    return True


def _cierra_contexto_reserva(texto: str | None) -> bool:
    """Evita heredar el nombre de una reserva anterior ya terminada/escalada."""
    valor = texto or ""
    if not re.search(r"\breserva\b|\bmesa(?:s)?\b", valor, flags=re.IGNORECASE):
        return False
    return bool(re.search(
        r"confirmad[ao]|reservad[ao]|te confirmamos|le avis[eé] al equipo|"
        r"pas[eé] el dato al equipo|te contactar[aá]n para confirm",
        valor,
        flags=re.IGNORECASE,
    ))


def _nombre_reserva_explicito(mensaje_actual: str, historial_db: list) -> str | None:
    """Obtiene el nombre expresado dentro de la conversación de reserva activa."""
    texto = (mensaje_actual or "").strip()
    nombre_actual = _nombre_marcado_en_texto(texto)
    if nombre_actual:
        return nombre_actual

    anterior = next(
        (
            h for h in reversed(historial_db)
            if getattr(h, "direccion", None) in ("outbound", "humano")
            and getattr(h, "contenido", None)
            and _contenido_outbound_relevante_nombre(getattr(h, "contenido", None))
        ),
        None,
    )
    pregunta = getattr(anterior, "contenido", "") if anterior else ""
    if _pregunta_nombre_reserva(pregunta) and "\n" not in texto and len(texto.split()) <= 8:
        nombre = _limpiar_nombre_reserva(texto)
        if nombre:
            return nombre

    # El cliente puede dar el nombre y luego responder "Correcto" a una
    # confirmación. Recuperamos el último nombre explícito de ESTE trámite, no
    # del perfil de WhatsApp ni de una reserva anterior ya cerrada.
    historial = list(historial_db)
    for indice in range(len(historial) - 1, -1, -1):
        mensaje = historial[indice]
        direccion = getattr(mensaje, "direccion", None)
        contenido = getattr(mensaje, "contenido", None) or ""

        if direccion in ("outbound", "humano") and _cierra_contexto_reserva(contenido):
            break
        if direccion != "inbound":
            continue

        nombre = _nombre_marcado_en_texto(contenido)
        if nombre:
            return nombre

        previo = next(
            (
                historial[j]
                for j in range(indice - 1, -1, -1)
                if getattr(historial[j], "direccion", None) in ("outbound", "humano")
                and getattr(historial[j], "contenido", None)
                and _contenido_outbound_relevante_nombre(getattr(historial[j], "contenido", None))
            ),
            None,
        )
        if previo and _pregunta_nombre_reserva(getattr(previo, "contenido", "")):
            candidato = contenido.strip()
            if "\n" not in candidato and len(candidato.split()) <= 8:
                nombre = _limpiar_nombre_reserva(candidato)
                if nombre:
                    return nombre
    return None


async def _enviar_flyer_eventos(
    session: AsyncSession,
    cliente_id: int,
    cliente_numero: str,
    eventos: list[dict],
) -> None:
    """Envía flyers por evento/hora; cae al flyer legacy por fecha si aplica."""
    enviados: set[Path] = set()
    for evento in eventos:
        p = flyer_path_evento(evento)
        if not p or p in enviados:
            continue
        enviados.add(p)
        nombre = str(evento.get("nombre") or "evento")
        fecha = str(evento.get("fecha") or "")
        hora = str(evento.get("hora_inicio") or evento.get("hora") or "").strip()
        caption = "🎟️ "
        if hora:
            caption += f"{hora} · "
        caption += f"{nombre} 🎶"
        try:
            await enviar_imagen_bytes(
                cliente_numero,
                p.read_bytes(),
                mime=MIME.get(p.suffix.lower(), "image/jpeg"),
                caption=caption,
            )
            await guardar_conversacion(
                session,
                cliente_id=cliente_id,
                direccion="outbound",
                tipo="imagen",
                contenido="[flyer del evento]",
                metadata={
                    "media": "flyer_evento",
                    "fecha": fecha,
                    "hora_inicio": hora,
                    "evento": nombre,
                },
            )
            log.info("flow.flyer_evento.enviado", cliente=cliente_numero, fecha=fecha, hora=hora)
        except Exception as e:
            log.warning("flow.flyer_evento.fail", error=str(e))


async def _enviar_flyer_evento(session: AsyncSession, cliente_id: int, cliente_numero: str, fecha: str) -> None:
    """Compatibilidad: envía el flyer legacy de una fecha."""
    await _enviar_flyer_eventos(session, cliente_id, cliente_numero, [{"fecha": fecha, "nombre": "evento"}])


async def _enviar_link_menu(session: AsyncSession, cliente_id: int, cliente_numero: str) -> None:
    """Envía siempre el enlace canónico del menú digital."""
    try:
        await enviar_texto(cliente_numero, MENU_URL)
        await guardar_conversacion(
            session,
            cliente_id=cliente_id,
            direccion="outbound",
            tipo="texto",
            contenido=MENU_URL,
            metadata={"media": "menu_link"},
        )
        log.info("flow.menu_link.enviado", cliente=cliente_numero)
    except Exception as e:
        log.warning("flow.menu_link.fail", error=str(e))


async def _enviar_plano_espacio(
    session: AsyncSession,
    cliente_id: int,
    cliente_numero: str,
    fecha: str | None = None,
    mesa_recomendada: int | None = None,
) -> None:
    """Envía un plano para clientes sin revelar la ocupación del negocio."""
    if not _PLANO_ESPACIO.exists():
        log.warning("flow.plano_espacio.no_existe", path=str(_PLANO_ESPACIO))
        return
    try:
        from app.utils.plano import generar_plano_cliente
        from datetime import datetime
        from zoneinfo import ZoneInfo

        if not fecha:
            fecha = datetime.now(ZoneInfo("America/Bogota")).date().isoformat()

        png_bytes = generar_plano_cliente(mesa_recomendada)
        if png_bytes is None:
            png_bytes = _PLANO_ESPACIO.read_bytes()

        cap = f"🗺️ Plano público del salón — {fecha}. ¡Escoge tu mesa o zona favorita!"
        await enviar_imagen_bytes(
            cliente_numero, png_bytes, mime="image/png",
            filename="plano-espacio.png", caption=cap,
        )
        await guardar_conversacion(
            session, cliente_id=cliente_id, direccion="outbound", tipo="imagen",
            contenido="[plano del salón con reservas]", metadata={"media": "plano_espacio"},
        )
        log.info("flow.plano_espacio.enviado", cliente=cliente_numero, fecha=fecha)
    except Exception as e:
        log.warning("flow.plano_espacio.fail", error=str(e))


async def _enviar_estado_actual(session: AsyncSession, cliente_id: int, cliente_numero: str) -> None:
    """Envia al cliente los estados/promos vigentes (tras el texto)."""
    from app import promo_estado

    estados = await promo_estado.cargar_estados_activos()
    if not estados:
        log.info("flow.estado_actual.sin_estado", cliente=cliente_numero)
        return

    total = len(estados)
    enviados = 0
    try:
        for idx, estado in enumerate(estados, start=1):
            base_cap = estado.get("caption") or "Esto es lo ultimo de La Cantina Plus"
            cap = f"Estado {idx}/{total}\n{base_cap}" if total > 1 else base_cap
            if estado.get("tipo") == "video":
                await enviar_video_bytes(
                    cliente_numero, estado["data"], mime=estado["mime"],
                    filename=estado["filename"], caption=cap,
                )
                tipo = "video"
            else:
                await enviar_imagen_bytes(
                    cliente_numero, estado["data"], mime=estado["mime"],
                    filename=estado["filename"], caption=cap,
                )
                tipo = "imagen"
            enviados += 1
            await guardar_conversacion(
                session, cliente_id=cliente_id, direccion="outbound", tipo=tipo,
                contenido="[estado/promo vigente]", metadata={
                    "media": "estado_actual",
                    "estado_idx": idx,
                    "estado_total": total,
                    "story_id": estado.get("id"),
                },
            )
        log.info("flow.estado_actual.enviado", cliente=cliente_numero, cantidad=enviados)
    except Exception as e:
        log.warning("flow.estado_actual.fail", error=str(e))


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

    # Nota de voz: normalmente se transcribe con Whisper en el webhook (main.py)
    # y llega aquí ya como texto. Si la transcripción falló (sin clave, audio
    # corrupto, error de API), msg.texto queda vacío → pedimos que escriban.
    if not contenido_usuario.strip() and es_audio and msg.media_url:
        contenido_usuario = (
            "[El cliente envió una nota de voz, pero no pude entenderla. "
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

    if await _resolver_cancelacion_pendiente(
        session=session,
        cliente_id=cliente_id,
        cliente_numero=cliente_numero,
        contenido_usuario=contenido_usuario,
    ):
        return []

    solicitud_menu = pide_menu(msg.texto or contenido_usuario)
    solicitud_plano = _pide_plano_espacio(msg.texto or contenido_usuario)
    if solicitud_plano:
        await _enviar_plano_espacio(session, cliente_id, cliente_numero)
        return []

    # 1. Historial (hasta 30 msgs / 48h)
    historial_db = await ultimos_mensajes(session, cliente_id, n=30, horas_max=48)
    comprobante_contextual = _es_media_comprobante(msg) and (
        _esperaba_confirmacion_reserva(historial_db)
        or _esperaba_comprobante_pago(historial_db)
        or _texto_menciona_comprobante(_contenido_real_cliente(msg, contenido_usuario))
    )
    nombre_reserva_confirmado = _nombre_reserva_explicito(contenido_usuario, historial_db)
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

    intent = _normalizar_intent_por_reglas(
        intent,
        solicitud_menu=solicitud_menu,
        solicitud_plano=solicitud_plano,
        contenido_usuario=contenido_usuario,
        cliente_numero=cliente_numero,
    )
    if msg.tipo in {"pdf", "documento"} and comprobante_contextual:
        intent = "envia_comprobante_pago"
        log.info("flow.intent_pdf_comprobante", cliente=cliente_numero)


    if intent == "pide_estado":
        from app import promo_estado as _pe
        if _pe.cargar_estado() is not None:
            await _enviar_estado_actual(session, cliente_id, cliente_numero)
        else:
            await enviar_texto(
                cliente_numero,
                "Por ahora no tenemos una promo activa publicada. "
                "Cuando el equipo suba algo te lo hacemos saber. 🎶",
            )
        return []

    # 3. Imagen entrante → multimodal (comprobante de cover, captura, etc.)
    imagen_b64: str | None = None
    imagen_mime: str | None = None
    imagen_bytes: bytes | None = None
    if msg.tipo == "imagen" and msg.media_url:
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(msg.media_url, headers=auth_headers())
                if r.status_code < 400 and len(r.content) <= 5 * 1024 * 1024:
                    imagen_bytes = r.content
                    imagen_b64 = base64.b64encode(r.content).decode("ascii")
                    imagen_mime = msg.media_mime or "image/jpeg"
        except Exception as e:
            log.warning("flow.imagen.fail_download", error=str(e))

    # 4. Contexto dinámico del cliente + tool use loop
    outbox: list[dict] = []
    if _debe_auto_encolar_comprobante(
        msg=msg,
        intent=intent,
        contenido_usuario=contenido_usuario,
        historial_db=historial_db,
    ):
        outbox.append({
            "tipo": "comprobante_cover",
            "mensaje": (
                "💸 *Comprobante de pago recibido*\n"
                f"Cliente: {cliente_numero}\n\n"
                "Verifica la imagen. Para aprobar el pago y avisarle al cliente, "
                "menciona a Nicky e indica el cliente o la reserva."
            ),
            "media_url": msg.media_url,
            "media_bytes": imagen_bytes,
            "media_mime": imagen_mime or msg.media_mime or "image/jpeg",
            "cliente_numero": cliente_numero,
            "whapi_message_id": msg.id,
        })
        log.info(
            "flow.comprobante_auto_encolado",
            cliente=cliente_numero,
            msg_id=msg.id,
            bytes=len(imagen_bytes or b""),
        )
    ctx = {
        "session": session,
        "cliente_id": cliente_id,
        "cliente_numero": cliente_numero,
        "cliente_phone_hint": msg.phone_hint,
        "cliente_lid": msg.lid,
        "intent": intent,
        "outbox": outbox,
        "mensaje_actual_cliente": contenido_usuario,
        "historial_cliente_reciente": [
            {
                "direccion": getattr(h, "direccion", None),
                "contenido": getattr(h, "contenido", None),
            }
            for h in historial_db[-12:]
            if getattr(h, "contenido", None)
        ],
        "incoming_media_url": msg.media_url,
        "incoming_media_bytes": imagen_bytes,
        "incoming_media_mime": imagen_mime or msg.media_mime,
        "incoming_media_tipo": msg.tipo,
        "nombre_reserva_confirmado": nombre_reserva_confirmado,
        "enviar_carta_link": solicitud_menu,
    }
    extra_system = await _construir_contexto_cliente(session, cliente_id, cliente_numero)
    if solicitud_menu:
        extra_system += (
            "\n\n## SOLICITUD DE MENÚ DETECTADA\n"
            "Debes orientar al cliente al link que enviará el sistema. No escribas "
            "precios ni listes productos, aunque aparezcan en mensajes anteriores."
        )
    if intent == "consultar_reserva":
        from app.integrations import cantina_api

        consulta = await cantina_api.reservas_cliente(cliente_numero)
        reservas = consulta.get("reservas") if isinstance(consulta, dict) and consulta.get("ok") else None
        ctx["reservas_cliente_precargadas"] = reservas
        if reservas is not None:
            extra_system += (
                "\n\n## RESERVAS ACTIVAS DEL PROPIO CLIENTE\n"
                + json.dumps(reservas, ensure_ascii=False, default=str)
                + "\nResponde usando estos datos. Si hay una sola, confírmala directamente. "
                "Si hay varias, pregunta solo por la fecha. Si no hay ninguna, indícalo. "
                "NUNCA pidas ID, nombre ni teléfono."
            )

    respuesta = await conversar(
        historial=historial_claude,
        mensaje_usuario=contenido_usuario,
        ctx=ctx,
        imagen_base64=imagen_b64,
        imagen_mime=imagen_mime,
        extra_system=extra_system,
        persona_file=ident.persona_prompt_file,
    )

    if _asegurar_escalacion_humana(
        outbox,
        intent=intent,
        cliente_numero=cliente_numero,
        mensaje_cliente=contenido_usuario,
        media_url=msg.media_url if msg.tipo == "audio" else None,
        media_mime=msg.media_mime or ("audio/ogg" if msg.tipo == "audio" else None),
    ):
        log.warning(
            "flow.escalacion_humana_auto_encolada",
            cliente=cliente_numero,
            intent=intent,
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
        await sleep_humano(texto_final, intent=intent)

    # 6. Enviar
    try:
        await enviar_texto(cliente_numero, texto_final)
        if settings.feature_humanizacion and settings.humanization_typing_indicator:
            await enviar_paused(cliente_numero)
    except Exception as e:
        log.exception("flow.enviar_whapi_fail", error=str(e))
        return ctx.get("outbox", [])

    # 6.5 Envíos complementarios pedidos por el bot (tras el texto).
    if ctx.get("enviar_video_como_llegar"):
        await _enviar_video_como_llegar(session, cliente_id, cliente_numero)
    if ctx.get("enviar_carta_link"):
        ctx.pop("enviar_estado_actual", None)
        await _enviar_link_menu(session, cliente_id, cliente_numero)
    if ctx.get("enviar_plano_espacio"):
        await _enviar_plano_espacio(
            session,
            cliente_id,
            cliente_numero,
            ctx.get("plano_fecha"),
            ctx.get("plano_mesa_recomendada"),
        )
    if ctx.get("enviar_estado_actual"):
        await _enviar_estado_actual(session, cliente_id, cliente_numero)
    if ctx.get("flyer_eventos"):
        await _enviar_flyer_eventos(session, cliente_id, cliente_numero, ctx["flyer_eventos"])
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
        lineas.append(
            f"- Nombre visible del perfil de WhatsApp: {cliente.nombre} "
            "(solo referencia; NUNCA lo uses como nombre de una reserva)"
        )

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
    lineas.append(
        "EXCEPCION OBLIGATORIA: antes de crear cualquier reserva pregunta "
        "\"¿A nombre de quién hago la reserva?\". El nombre del perfil de WhatsApp "
        "NO cuenta como respuesta."
    )
    texto = "\n".join(lineas)
    if bloque_mem:
        texto += "\n\n" + bloque_mem
    return texto
