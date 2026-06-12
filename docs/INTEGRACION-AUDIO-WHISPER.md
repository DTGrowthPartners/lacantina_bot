# Integración: transcripción de notas de voz (Whisper / OpenAI)

Cómo se implementó que el bot "oiga" las notas de voz de WhatsApp y responda como
si el cliente hubiera escrito. Pensado para **replicarlo en otro bot**.

## Idea general

```
Cliente manda nota de voz por WhatsApp
        │
        ▼
Webhook recibe el mensaje  →  tipo = "audio",  media_url,  media_mime
        │
        ▼
¿tipo == audio y sin texto?  →  transcribir_audio(media_url, media_mime)
        │                              │
        │                              ├─ 1) descargar el audio del gateway (whapi) con auth
        │                              └─ 2) POST multipart a OpenAI /audio/transcriptions
        ▼
msg.texto = transcripción   (se MUTA el mensaje)
        │
        ▼
El resto del flujo trata el mensaje como texto normal
(se persiste en el historial, entra al contexto del bot, etc.)
```

Decisión clave: **mutar `msg.texto` con la transcripción** lo antes posible (en el
webhook, antes de procesar). Así todo lo demás —historial, panel admin, contexto
del modelo, reglas— funciona sin cambios, como si el usuario hubiera escrito.

Todo es **best-effort / falla silencioso**: si no hay API key, falla la descarga,
el audio es muy grande o la API responde error → devuelve `None` y el flujo cae a
un fallback (pedir que escriban). Nunca rompe el bot.

---

## 1) Configuración (`.env`)

```ini
OPENAI_API_KEY=sk-...                 # clave de OpenAI (requerida para transcribir)
WHISPER_MODEL=whisper-1               # whisper-1 | gpt-4o-mini-transcribe | gpt-4o-transcribe
WHISPER_IDIOMA=es                     # hint de idioma → más precisión y menos latencia
FEATURE_TRANSCRIBIR_AUDIO=true        # interruptor para apagar la feature sin tocar código
```

En `config.py` (Pydantic Settings):

```python
openai_api_key: str = Field(default="")
whisper_model: str = "whisper-1"
whisper_idioma: str = "es"
feature_transcribir_audio: bool = True
```

> Sin `OPENAI_API_KEY`, la transcripción se desactiva sola (devuelve `None`).

---

## 2) El módulo de transcripción (`app/integrations/whisper.py`)

Es independiente del bot: solo necesita (a) una URL del audio + cómo autenticar su
descarga, y (b) la clave de OpenAI. Reutilizable tal cual.

```python
"""Transcripción de notas de voz vía la API de OpenAI (Whisper)."""
from __future__ import annotations
import httpx
from app.config import get_settings
from app.logging_setup import log
from app.whapi.client import auth_headers      # ← cómo autenticar la descarga del audio

settings = get_settings()

_OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # límite de OpenAI (25 MB)

# OpenAI infiere el formato por el nombre de archivo → mapeamos mime → extensión.
# Las notas de voz de WhatsApp suelen ser ogg/opus.
_EXT_POR_MIME = {
    "audio/ogg": "ogg", "audio/opus": "ogg", "audio/oga": "ogg",
    "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/mp4": "mp4", "audio/m4a": "m4a", "audio/x-m4a": "m4a", "audio/aac": "m4a",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm", "audio/flac": "flac",
}

async def transcribir_audio(media_url: str, mime: str | None = None) -> str | None:
    """Descarga el audio del gateway y lo transcribe. Devuelve texto o None."""
    if not settings.feature_transcribir_audio:
        return None
    if not settings.openai_api_key:
        log.warning("whisper.sin_api_key")
        return None

    # 1) Descargar el audio (el media del gateway suele requerir auth).
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

    # 2) Enviar a OpenAI (multipart). `language` mejora precisión y latencia.
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
        return texto or None
    except Exception as e:
        log.exception("whisper.api_exc", error=str(e))
        return None
```

Notas importantes:
- **No se usa el SDK de OpenAI**, solo `httpx` (multipart). Una dependencia menos.
- OpenAI **infiere el formato por el nombre del archivo** → por eso mapeamos mime →
  extensión. WhatsApp manda `audio/ogg` (opus); con `audio.ogg` la API lo acepta.
- El parámetro `language` (`es`) **mejora precisión y baja latencia** vs. autodetección.
- Límite **25 MB** de OpenAI: una nota de voz normal pesa muy poco, no es problema.

---

## 3) Detectar la nota de voz (parser del gateway)

WhatsApp/whapi clasifica las notas de voz con distintos nombres; las normalizamos
todas a `audio`. En `app/whapi/parser.py`:

```python
# whapi type → nuestro tipo
"audio": "audio",
"voice": "audio",   # nota de voz (PTT)
"ptt":   "audio",
```

El mensaje parseado expone los campos que necesita la transcripción:

```python
@dataclass
class MensajeWhapi:
    tipo: TipoMensaje        # "audio" para notas de voz
    texto: str               # vacío en una nota de voz pura
    media_url: str | None    # URL del audio en el gateway
    media_mime: str | None   # p. ej. "audio/ogg; codecs=opus"
    ...
```

---

## 4) Engancharlo en el webhook (punto de integración)

Justo después de identificar el mensaje y antes de procesarlo, si es audio sin
texto → transcribir y **mutar `msg.texto`**:

```python
# ── Nota de voz → transcribir con Whisper ───────────────────────────────
# Mutamos msg.texto con la transcripción: se persiste en el historial,
# entra al contexto del bot y fluye como si el cliente hubiera escrito.
if msg.tipo == "audio" and msg.media_url and not (msg.texto or "").strip():
    from app.integrations.whisper import transcribir_audio
    transcripcion = await transcribir_audio(msg.media_url, msg.media_mime)
    if transcripcion:
        msg.texto = transcripcion
        log.info("webhook.audio_transcrito", cliente=msg.from_number, chars=len(transcripcion))
    else:
        log.warning("webhook.audio_no_transcrito", cliente=msg.from_number)
```

### Caso especial: notas de voz en un GRUPO
En un grupo, una nota de voz **no puede llevar @mención**, así que primero la
transcribimos y luego revisamos si nombran al bot en el texto transcrito:

```python
if not bot_interpelado and msg.tipo == "audio" and msg.media_url:
    transcripcion = await transcribir_audio(msg.media_url, msg.media_mime)
    if transcripcion:
        msg.texto = transcripcion                 # evita re-transcribir aguas abajo
        if _menciona_nombre_bot(transcripcion):
            bot_interpelado = True
```

> Como se muta `msg.texto`, los flujos posteriores **no vuelven a transcribir**
> (ya hay texto). Una sola llamada a OpenAI por mensaje.

---

## 5) Fallback (cuando no se pudo transcribir)

Si la transcripción falla, `msg.texto` queda vacío y el flujo lo detecta para
responder con amabilidad en lugar de quedarse mudo:

```python
es_audio = msg.tipo == "audio"
if not contenido_usuario.strip() and es_audio and msg.media_url:
    contenido_usuario = ("[El cliente envió una nota de voz, pero no pude entenderla. "
                         "Pídele amablemente que escriba o repita el mensaje.]")
```

---

## Replicarlo en OTRO bot — checklist

1. **Copia `app/integrations/whisper.py`** tal cual.
2. Ajusta el import de auth de descarga: `from app.whapi.client import auth_headers`
   → la función que devuelve el header para descargar el media de TU gateway.
   - Si tu gateway entrega el audio en una URL **pública** (sin auth), usa
     `headers={}` en el GET y listo.
3. Añade los **4 settings** a tu `config.py` y las vars al `.env`
   (`OPENAI_API_KEY`, `WHISPER_MODEL`, `WHISPER_IDIOMA`, `FEATURE_TRANSCRIBIR_AUDIO`).
4. Asegúrate de que tu parser marque las notas de voz como un tipo `audio` y
   exponga `media_url` + `media_mime`.
5. En tu webhook, **antes de procesar**, agrega el bloque de transcripción que
   muta `msg.texto` (sección 4).
6. (Opcional) Maneja el fallback de la sección 5.

Sin SDK extra: solo `httpx` (que ya usas para el resto de HTTP).

---

## Costos y límites (referencia)

- `whisper-1`: facturado por **minuto de audio** (~$0.006/min). Notas de voz de
  WhatsApp son cortas → costo marginal.
- Modelos `gpt-4o-mini-transcribe` / `gpt-4o-transcribe`: más nuevos, suelen dar
  mejor calidad; revisa el pricing vigente de OpenAI antes de cambiar.
- Límite de tamaño: **25 MB** por archivo (suficiente para notas de voz).
- Latencia: ~1–3 s para notas cortas con `language` fijado.
