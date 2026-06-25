"""
Directorio del equipo — lee de Postgres (tablas equipo_miembros + numeros_internos).

Edita desde /admin (UI web) o vía SQL directo.

Cache:
    Mantiene un cache en memoria con TTL de 30s para no hacer una query a la DB
    en cada mensaje. Cuando alguien edita un miembro desde /admin, el cache
    expira automáticamente en máximo 30 segundos.

API sincrónica (los handlers ya viven en contexto async pero esta API se
expone síncrona porque se llama desde tools/main donde a veces no hay
session disponible — usa SQLAlchemy sincrónico bajo el capó).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
import re

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ContactoWhitelist, EquipoMiembro, NumeroInterno
from app.logging_setup import log

settings = get_settings()

# Engine sincrónico aparte (no compite con el async del bot)
_sync_engine = create_engine(settings.database_url_sync, pool_size=2, max_overflow=2, pool_pre_ping=True)


@dataclass(frozen=True)
class Miembro:
    nombre: str
    numero_whatsapp: str
    rol: str | None
    areas: tuple[str, ...]
    es_fallback: bool
    activo: bool
    notas: str | None = None


# ────────────────────────────────────────────────────────────────────────────
# Cache con TTL
# ────────────────────────────────────────────────────────────────────────────

CACHE_TTL_SECONDS = 30

_cache: dict[str, Any] = {
    "loaded_at": 0.0,
    "miembros": [],
    "miembros_por_numero": {},
    "numeros_internos": set(),
    "numeros_internos_norm": set(),
    "clientes_whitelist": {},  # numero -> Miembro(rol='cliente')
    "clientes_whitelist_norm": {},
}


def _normalizar_numero(numero: str | None) -> str:
    """Normaliza números guardados a la misma forma que llega desde whapi."""
    if not numero:
        return ""
    base = str(numero).split("@", 1)[0]
    digitos = re.sub(r"\D", "", base)
    return f"+{digitos}" if digitos else ""


def _cargar_si_caducado() -> None:
    """Recarga miembros y números internos si el cache caducó."""
    ahora = time.time()
    if ahora - _cache["loaded_at"] < CACHE_TTL_SECONDS:
        return

    try:
        with Session(_sync_engine) as session:
            miembros_rows = session.execute(
                select(EquipoMiembro).where(EquipoMiembro.activo.is_(True))
            ).scalars().all()
            internos_rows = session.execute(
                select(NumeroInterno.numero_whatsapp).where(NumeroInterno.activo.is_(True))
            ).scalars().all()
            clientes_rows = session.execute(
                select(ContactoWhitelist).where(
                    ContactoWhitelist.activo.is_(True),
                    ContactoWhitelist.rol == "cliente",
                )
            ).scalars().all()
    except Exception as e:
        log.error("equipo.cache.load_fail", error=str(e))
        return

    miembros: list[Miembro] = []
    miembros_por_numero: dict[str, Miembro] = {}
    for m in miembros_rows:
        miembro = Miembro(
            nombre=m.nombre,
            numero_whatsapp=m.numero_whatsapp,
            rol=m.rol,
            areas=tuple(m.areas or []),
            es_fallback=bool(m.es_fallback),
            activo=bool(m.activo),
            notas=m.notas,
        )
        miembros.append(miembro)
        numero_norm = _normalizar_numero(m.numero_whatsapp)
        if numero_norm:
            miembros_por_numero[numero_norm] = miembro

    clientes: dict[str, Miembro] = {}
    clientes_norm: dict[str, Miembro] = {}
    for c in clientes_rows:
        cliente = Miembro(
            nombre=c.nombre or c.empresa or c.numero_whatsapp,
            numero_whatsapp=c.numero_whatsapp,
            rol="cliente",
            areas=(),
            es_fallback=False,
            activo=True,
            notas=c.empresa,
        )
        clientes[c.numero_whatsapp] = cliente
        numero_norm = _normalizar_numero(c.numero_whatsapp)
        if numero_norm:
            clientes_norm[numero_norm] = cliente

    _cache["loaded_at"] = ahora
    _cache["miembros"] = miembros
    _cache["miembros_por_numero"] = miembros_por_numero
    _cache["numeros_internos"] = set(internos_rows)
    _cache["numeros_internos_norm"] = {
        n for n in (_normalizar_numero(num) for num in internos_rows) if n
    }
    _cache["clientes_whitelist"] = clientes
    _cache["clientes_whitelist_norm"] = clientes_norm
    log.debug("equipo.cache.reloaded",
              miembros=len(miembros), numeros_internos=len(internos_rows),
              clientes_whitelist=len(clientes))


def invalidar_cache() -> None:
    """Forzar recarga en la próxima consulta (útil tras edición en admin)."""
    _cache["loaded_at"] = 0.0


# ────────────────────────────────────────────────────────────────────────────
# API pública
# ────────────────────────────────────────────────────────────────────────────


def superior_para(area: str | None = None) -> Miembro | None:
    """Compatibilidad: devuelve EL primer miembro responsable de un área."""
    miembros = superiores_para(area)
    return miembros[0] if miembros else None


def superiores_para(area: str | None = None) -> list[Miembro]:
    """TODOS los miembros que deben recibir notificación para un área.

    Notifica a TODOS los miembros fallback en paralelo (no solo al primero),
    para que cualquiera del equipo disponible pueda atender.
    """
    _cargar_si_caducado()
    miembros: list[Miembro] = _cache["miembros"]
    if not miembros:
        return []

    if area:
        coincidentes = [m for m in miembros if area in m.areas]
        if coincidentes:
            return coincidentes

    fallbacks = [m for m in miembros if m.es_fallback]
    if fallbacks:
        return fallbacks

    return miembros[:1]


def listar_miembros_equipo() -> list[Miembro]:
    """Lista de miembros activos (para la tool consultar_equipo)."""
    _cargar_si_caducado()
    return list(_cache["miembros"])


def es_numero_interno(numero: str) -> bool:
    """¿Este número pertenece al equipo interno (no es cliente)?"""
    _cargar_si_caducado()
    numero_norm = _normalizar_numero(numero)
    if numero_norm in _cache["miembros_por_numero"]:
        return False
    return numero in _cache["numeros_internos"] or numero_norm in _cache["numeros_internos_norm"]


def es_miembro_equipo(numero: str) -> Miembro | None:
    """
    ¿Este número es un MIEMBRO ACTIVO del equipo (recibe escalaciones, manda
    instrucciones)? Devuelve el Miembro si sí, None si no.

    Distinto a `es_numero_interno`:
    - es_numero_interno → asesoras, bodegas, otros bots — el bot las IGNORA
    - es_miembro_equipo → Fabio, supervisores — el bot HABLA con ellos
    """
    _cargar_si_caducado()
    return _cache["miembros_por_numero"].get(_normalizar_numero(numero))


def whitelist_cliente(numero: str) -> Miembro | None:
    """¿Este número es un CLIENTE whitelisted (rol=cliente)? Devuelve un Miembro
    con rol='cliente' para enrutarlo al flujo operativo con permisos scoped.

    Distinto de es_miembro_equipo (equipo interno con acceso total).
    """
    _cargar_si_caducado()
    return (
        _cache["clientes_whitelist"].get(numero)
        or _cache["clientes_whitelist_norm"].get(_normalizar_numero(numero))
    )


def fabio_phone() -> str:
    """Compatibilidad: devuelve el número del miembro fallback (hoy Fabio).
    Vacío si no hay miembros configurados."""
    m = superior_para()
    return m.numero_whatsapp if m else ""


def todos_los_miembros() -> list[Miembro]:
    _cargar_si_caducado()
    return list(_cache["miembros"])


def config_escalacion() -> dict[str, Any]:
    """
    Devuelve config global de escalación. Hoy hardcoded; en el futuro podría
    venir de una tabla `config_bot`.
    """
    return {
        "enviar_mensaje_real": True,
        "prefijo_mensajes_fabio": "[BOT ASISTENTE]",
        "reescalacion_tras_minutos": 60,
    }
