"""Webhooks SALIENTES: el bot empuja eventos a la plataforma de administración.

Hace POST a `settings.webhook_url` con el evento en JSON y una firma HMAC-SHA256
en el header `X-Cantina-Signature`, para que la plataforma verifique que el
webhook viene realmente de este bot.

Diseño:
  - **Best-effort**: nunca rompe el flujo del bot. Cualquier fallo va solo al log.
  - **No-op si no hay `webhook_url`** configurada (la plataforma aún no la dio).
  - La firma cubre EXACTAMENTE el cuerpo (bytes) que se envía.

Eventos típicos (`evento`):
  - `reserva.creada`        — nueva reserva (desde el bot o el panel)
  - `comprobante.recibido`  — cliente envió comprobante de cover
  - `escalacion`            — el bot escaló algo al equipo
  - `alerta`               — error/alerta interna (p. ej. falló Claude)
  - `bot.estado_cambiado`   — se activó/desactivó el bot
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx

from app.config import get_settings
from app.logging_setup import log


def firmar(body: bytes, secret: str) -> str:
    """Firma HMAC-SHA256 del cuerpo. Formato `sha256=<hexdigest>`."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def emitir_evento(evento: str, datos: dict | None = None) -> None:
    """Empuja un evento a la plataforma externa (si está configurada).

    No lanza nunca: si no hay URL o falla el POST, solo se registra en el log.
    """
    s = get_settings()
    url = (s.webhook_url or "").strip()
    if not url:
        return  # sin destino configurado → no-op silencioso

    payload = {
        "bot_id": s.bot_id,
        "evento": evento,
        "datos": datos or {},
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"cantina-bot/{s.bot_id}",
        "X-Cantina-Bot": s.bot_id,
        "X-Cantina-Evento": evento,
    }
    if s.webhook_secret:
        headers["X-Cantina-Signature"] = firmar(body, s.webhook_secret)

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(url, content=body, headers=headers)
        if r.status_code >= 300:
            log.warning("webhook_saliente.no_2xx", evento=evento, status=r.status_code)
        else:
            log.info("webhook_saliente.ok", evento=evento, status=r.status_code)
    except Exception as e:
        log.warning("webhook_saliente.fail", evento=evento, error=str(e)[:160])
