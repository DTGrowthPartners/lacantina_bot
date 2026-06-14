"""Helper centralizado para notificar al equipo de La Cantina vía grupo de WhatsApp.

Cuando ocurre algo que necesita atención humana (comprobante de cover,
escalación, reserva nueva, etc.), en lugar de mandar mensajes individuales,
los publicamos en el grupo del equipo para que el primero que esté
disponible atienda.

Configurable vía `settings.equipo_cantina_group_id`. Si está vacío, cae al
fallback de mandar a `superiores_para()` individuales.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.logging_setup import log
from app.whapi.client import (
    auth_headers,
    enviar_botones,
    enviar_imagen_bytes,
    enviar_texto,
)

settings = get_settings()

# Límite de descarga para reenvío de comprobantes al grupo (whapi sirve la
# media tras auth; replicamos el mismo tope que usamos al leer imágenes del cliente).
_MAX_MEDIA_BYTES = 16 * 1024 * 1024


async def notificar_equipo(
    texto: str,
    media_url: str | None = None,
    *,
    media_bytes: bytes | None = None,
    media_mime: str | None = None,
) -> bool:
    """Envía un mensaje al grupo del equipo de La Cantina.

    Si `media_url` viene dado (p. ej. el comprobante de pago que mandó un
    cliente), descarga la imagen y la reenvía al grupo con `texto` como
    caption. Así el equipo ve el comprobante directo en el grupo de reservas,
    sin tener que ir al chat del cliente.

    Devuelve True si se envió correctamente. False si falló o no hay grupo
    configurado (en cuyo caso el caller puede caer al fallback).

    Es seguro llamarlo desde cualquier flujo — atrapa errores y los loguea
    sin propagar (no queremos que un fallo de notif rompa el flujo principal).
    """
    if not settings.equipo_cantina_group_id:
        log.warning("notif_equipo.sin_grupo_configurado")
        return False
    grupo = settings.equipo_cantina_group_id
    try:
        if media_bytes:
            await enviar_imagen_bytes(
                grupo,
                media_bytes,
                mime=media_mime or "image/jpeg",
                caption=texto,
            )
            return True
        if media_url:
            imagen = await _descargar_media(media_url)
            if imagen is not None:
                data, mime = imagen
                await enviar_imagen_bytes(grupo, data, mime=mime, caption=texto)
                return True
            # Si la descarga falló, no perdemos el aviso: mandamos el texto
            # (que ya incluye el link "Verificar: <url>") como fallback.
            log.warning("notif_equipo.media_fallback_texto", media_url=media_url[:120])
        await enviar_texto(grupo, texto)
        return True
    except Exception as e:
        log.exception("notif_equipo.fail", error=str(e))
        return False


async def _descargar_media(media_url: str) -> tuple[bytes, str] | None:
    """Descarga bytes de una media de whapi (requiere header de auth).

    Devuelve (bytes, mime) o None si falla / excede el tope de tamaño.
    """
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(media_url, headers=auth_headers())
        if r.status_code >= 400:
            log.warning("notif_equipo.media_download_http", status=r.status_code)
            return None
        if len(r.content) > _MAX_MEDIA_BYTES:
            log.warning("notif_equipo.media_too_big", size=len(r.content))
            return None
        mime = r.headers.get("content-type") or "image/jpeg"
        return r.content, mime
    except Exception as e:
        log.warning("notif_equipo.media_download_fail", error=str(e))
        return None


async def notificar_equipo_con_botones(
    body: str,
    botones: list[tuple[str, str]],
    *,
    header: str | None = None,
    footer: str | None = None,
) -> dict | None:
    """Envía una alerta al grupo con botones quick-reply (1-3 botones).

    Devuelve la respuesta de whapi (incluye message.id para guardar metadata
    de la alerta), o None si falla / no hay grupo configurado.
    """
    if not settings.equipo_cantina_group_id:
        log.warning("notif_equipo.sin_grupo_configurado")
        return None
    try:
        return await enviar_botones(
            settings.equipo_cantina_group_id,
            body=body, botones=botones, header=header, footer=footer,
        )
    except Exception as e:
        log.exception("notif_equipo_botones.fail", error=str(e))
        return None


async def notificar_equipo_con_fallback(texto: str) -> int:
    """Notifica al grupo. Si falla o no hay grupo, cae a notificar a los
    superiores individuales. Devuelve el número de destinos notificados.
    """
    if await notificar_equipo(texto):
        return 1
    # Fallback individuos
    from app.equipo.directorio import superiores_para
    enviados = 0
    for sup in superiores_para("clientes"):
        try:
            await enviar_texto(sup.numero_whatsapp, texto)
            enviados += 1
        except Exception as e:
            log.warning("notif_equipo.fallback_fail", numero=sup.numero_whatsapp, error=str(e))
    return enviados
