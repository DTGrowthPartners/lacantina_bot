"""Clasificación rápida de intención del mensaje del cliente con Haiku."""

from __future__ import annotations

import re

from app.claude.anthropic_client import get_anthropic_client
from app.claude.prompts import PROMPT_CLASIFICADOR_INTENT
from app.config import get_settings
from app.logging_setup import log

settings = get_settings()

_client = get_anthropic_client()


# Deben coincidir EXACTAMENTE con los labels de PROMPT_CLASIFICADOR_INTENT
# (prompts.py). Si no coinciden, todo cae a "otro".
INTENTS_VALIDOS = {
    "saludo",
    "pregunta_horario",
    "pregunta_ubicacion",
    "pregunta_evento_cover",
    "consulta_disponibilidad",
    "consultar_reserva",
    "pide_reservar",
    "envia_comprobante_pago",
    "modificar_reserva",
    "cancelar_reserva",
    "pide_estado",
    "queja",
    "pide_humano",
    "spam",
    "otro",
}


async def clasificar(mensaje: str, contexto_reciente: list[str] | None = None) -> str:
    """
    Devuelve el intent label. 'otro' como fallback.

    contexto_reciente: lista de strings con últimos mensajes (más antiguo → más reciente).
    """
    if not mensaje.strip():
        return "otro"

    normalizado = re.sub(r"[^\wáéíóúüñ]+", " ", mensaje.lower()).strip()
    if normalizado in {"confirmar", "confirmo", "confirmada", "si confirmo"}:
        return "consultar_reserva"
    if normalizado in {"cancelar", "cancelo", "cancelala", "cancelarla"}:
        return "cancelar_reserva"

    if re.search(
        r"\b(?:conf[ií]rmame|confirmar|consultar|ver|recordar)\b.{0,30}\bmi reserva\b"
        r"|\bmi reserva\b.{0,30}\b(?:sigue|est[aá]|activa|confirmada|datos)\b"
        r"|\btengo una reserva\b",
        mensaje,
        flags=re.IGNORECASE,
    ):
        return "consultar_reserva"

    ctx = ""
    if contexto_reciente:
        ctx = "\n\nContexto (últimos mensajes):\n" + "\n".join(f"- {m}" for m in contexto_reciente[-3:])

    user_msg = f"{ctx}\n\nMensaje actual:\n{mensaje}".strip()

    try:
        resp = await _client.messages.create(
            model=settings.claude_model_intent,
            max_tokens=20,
            system=PROMPT_CLASIFICADOR_INTENT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:
        log.error("intent.clasificar.fail", error=str(e))
        return "otro"

    if not resp.content:
        return "otro"

    raw = (resp.content[0].text or "").strip().lower().split()[0] if resp.content else ""
    intent = raw.rstrip(".,!?")

    if intent not in INTENTS_VALIDOS:
        log.warning("intent.label_no_valido", raw=raw)
        return "otro"

    return intent
