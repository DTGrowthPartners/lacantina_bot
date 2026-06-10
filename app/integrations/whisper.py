"""Transcripción de notas de voz vía la API de transcripción de OpenAI (Whisper).

Cuando un cliente manda una nota de voz, descargamos el audio de whapi (requiere
auth) y lo enviamos a OpenAI para convertirlo a texto. Así el bot puede
"escuchar" la nota y responder como si el cliente hubiera escrito.

Todo falla silencioso: si no hay API key, falla la descarga o la API responde
error, devolvemos None y el caller decide el fallback (pedir que escriban).
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.logging_setup import log
from app.whapi.client import auth_headers

settings = get_settings()

_OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # límite de la API de OpenAI (25 MB)

# Extensión por mime: OpenAI infiere el formato por el nombre del archivo.
# Las notas de voz de WhatsApp suelen ser ogg/opus.
_EXT_POR_MIME = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/oga": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "mp4",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/flac": "flac",
}


async def transcribir_audio(media_url: str, mime: str | None = None) -> str | None:
    """Descarga el audio de whapi y lo transcribe con OpenAI.

    Devuelve el texto transcrito (str no vacío) o None si no se pudo (sin clave,
    feature apagada, descarga falla, audio muy grande, error de API).
    """
    if not settings.feature_transcribir_audio:
        return None
    if not settings.openai_api_key:
        log.warning("whisper.sin_api_key")
        return None

    # 1. Descargar el audio desde whapi (necesita el header de auth del canal).
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(media_url, headers=auth_headers())
        if r.status_code >= 400 or not r.content:
            log.warning("whisper.download_fail", status=r.status_code)
            return None
        if len(r.content) > _MAX_AUDIO_BYTES:
            log.warning("whisper.audio_too_big", size=len(r.content))
            return None
        audio = r.content
        ctype = (mime or r.headers.get("content-type") or "audio/ogg")
        ctype = ctype.split(";")[0].strip().lower()
    except Exception as e:
        log.warning("whisper.download_exc", error=str(e))
        return None

    ext = _EXT_POR_MIME.get(ctype, "ogg")
    filename = f"audio.{ext}"

    # 2. Enviar a OpenAI (multipart). `language` mejora precisión y latencia.
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            resp = await c.post(
                _OPENAI_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                data={"model": settings.whisper_model, "language": settings.whisper_idioma},
                files={"file": (filename, audio, ctype)},
            )
        if resp.status_code >= 400:
            log.error("whisper.api_fail", status=resp.status_code, body=resp.text[:300])
            return None
        texto = (resp.json().get("text") or "").strip()
        if not texto:
            log.warning("whisper.texto_vacio")
            return None
        return texto
    except Exception as e:
        log.exception("whisper.api_exc", error=str(e))
        return None
