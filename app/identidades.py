"""Registry de identidades del bot.

La Cantina usa UN solo canal whapi: el número del bot oficial del venue
(+573008641322). No hay canal secundario.

Si en el futuro Fabio quisiera un canal personal paralelo (ej. para gestionar
desde su propio celular), se añade aquí siguiendo el patrón de `bot_asistente`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings


@dataclass(frozen=True)
class Identidad:
    key: str
    nombre: str                       # "La Cantina Plus"
    numero: str                       # +573008641322
    token: str                        # whapi token
    persona_prompt_file: str | None   # data/prompts/cantina-identidad.md
    politica_estricta: bool           # False — la Cantina recibe randoms públicos
    webhook_path: str
    activa: bool


def _construir() -> dict[str, Identidad]:
    s = get_settings()
    principal = Identidad(
        key="principal",
        nombre=s.identidad_principal_nombre,
        numero=s.whapi_numero_bot,
        token=s.whapi_token,
        persona_prompt_file=s.identidad_principal_persona_file or None,
        politica_estricta=bool(s.identidad_principal_estricta),
        webhook_path="/webhook",
        activa=bool(s.whapi_token and s.whapi_numero_bot),
    )
    return {"principal": principal}


def todas() -> dict[str, Identidad]:
    return _construir()


def por_key(key: str) -> Identidad | None:
    return _construir().get(key)


def por_path(path: str) -> Identidad | None:
    for i in _construir().values():
        if i.webhook_path == path:
            return i
    return None


def principal() -> Identidad:
    """La identidad del canal principal (/webhook)."""
    return _construir()["principal"]


# Compat: si código heredado llama `maria()`, devuelve la identidad principal.
def maria() -> Identidad:
    return principal()
