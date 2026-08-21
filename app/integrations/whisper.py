"""Transcripción de notas de voz: OpenAI (Whisper) con fallback local.

Cuando un cliente manda una nota de voz, descargamos el audio de whapi (requiere
auth) y lo enviamos a OpenAI para convertirlo a texto. Así el bot puede
"escuchar" la nota y responder como si el cliente hubiera escrito.

Si OpenAI falla (sin créditos, sin clave, error de red), reintentamos con
faster-whisper local en CPU. Es más lento pero no depende de saldo: en agosto
de 2026 la cuenta de OpenAI se quedó en cero y el bot pasó días sin entender
audios sin que nadie se enterara, porque todo falla silencioso.

Todo sigue fallando silencioso: si ninguna vía funciona devolvemos None y el
caller decide el fallback (pedir que escriban).
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import httpx

from app.config import get_settings
from app.logging_setup import log
from app.whapi.client import auth_headers

settings = get_settings()

_OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # límite de la API de OpenAI (25 MB)

_SCRIPT_LOCAL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "whisper_local.py",
)

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
    """Descarga el audio de whapi y lo transcribe (OpenAI → local).

    Devuelve el texto transcrito (str no vacío) o None si no se pudo por
    ninguna vía (feature apagada, descarga falla, audio muy grande, ambos
    transcriptores caídos).
    """
    if not settings.feature_transcribir_audio:
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

    # 2. OpenAI primero (rápido y más preciso), local como red de seguridad.
    texto = await _transcribir_openai(audio, ctype, ext)
    if texto:
        return texto

    if settings.whisper_local_fallback:
        texto = await _transcribir_local(audio, ext)
        if texto:
            log.info("whisper.local_ok", chars=len(texto))
            return texto

    return None


async def _transcribir_openai(audio: bytes, ctype: str, ext: str) -> str | None:
    """Manda el audio a la API de OpenAI. None si no hay clave o falla."""
    if not settings.openai_api_key:
        log.warning("whisper.sin_api_key")
        return None

    # `language` mejora precisión y latencia.
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            resp = await c.post(
                _OPENAI_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                data={"model": settings.whisper_model, "language": settings.whisper_idioma},
                files={"file": (f"audio.{ext}", audio, ctype)},
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


async def _transcribir_local(audio: bytes, ext: str) -> str | None:
    """Transcribe con faster-whisper en CPU vía `whisper-env`.

    Va por subproceso y no por import porque la librería vive en otro venv.
    Se corre en un archivo temporal que borramos siempre.
    """
    ruta = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
            f.write(audio)
            ruta = f.name

        proc = await asyncio.create_subprocess_exec(
            settings.whisper_local_python,
            _SCRIPT_LOCAL,
            ruta,
            settings.whisper_idioma,
            settings.whisper_local_modelo,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=settings.whisper_local_timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            log.warning("whisper.local_timeout", timeout=settings.whisper_local_timeout)
            return None

        if proc.returncode != 0:
            log.error("whisper.local_fail", rc=proc.returncode,
                      err=err.decode("utf-8", "replace")[:300])
            return None

        texto = out.decode("utf-8", "replace").strip()
        if not texto:
            log.warning("whisper.local_texto_vacio")
            return None
        return texto
    except Exception as e:
        log.exception("whisper.local_exc", error=str(e))
        return None
    finally:
        if ruta:
            try:
                os.unlink(ruta)
            except OSError:
                pass
