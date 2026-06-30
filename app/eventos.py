"""Helpers para normalizar eventos del backend de mesas.

El backend mantiene compatibilidad con `evento` singular, pero ahora puede
devolver varios eventos para una misma fecha en `eventos`.
"""

from __future__ import annotations

from typing import Any


def extraer_eventos(resp: Any) -> list[dict]:
    """Devuelve una lista sin importar si la API respondió evento o eventos."""
    if not isinstance(resp, dict):
        if isinstance(resp, list):
            return [e for e in resp if isinstance(e, dict)]
        return []
    if resp.get("ok") is False:
        return []

    data = resp.get("data", resp)
    if isinstance(data, dict):
        eventos = data.get("eventos")
        if isinstance(eventos, list):
            return [e for e in eventos if isinstance(e, dict)]
        evento = data.get("evento")
        if isinstance(evento, dict):
            return [evento]
        if data.get("fecha") or data.get("nombre"):
            return [data]
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    return []


def hora_evento(evento: dict) -> str:
    return str(evento.get("hora_inicio") or evento.get("hora") or "").strip()


def hora_fin_evento(evento: dict) -> str:
    return str(evento.get("hora_fin") or "").strip()


def etiqueta_hora(evento: dict) -> str:
    inicio = hora_evento(evento)
    fin = hora_fin_evento(evento)
    if inicio and fin:
        return f"{inicio}-{fin}"
    return inicio


def clave_orden_evento(evento: dict) -> tuple[str, str, str]:
    return (
        str(evento.get("fecha") or ""),
        hora_evento(evento),
        str(evento.get("created_at") or ""),
    )


def resumen_eventos(eventos: list[dict]) -> str | None:
    if not eventos:
        return None
    partes: list[str] = []
    for evento in sorted(eventos, key=clave_orden_evento):
        nombre = str(evento.get("nombre") or evento.get("evento") or "Evento")
        hora = etiqueta_hora(evento)
        partes.append(f"{hora} {nombre}".strip())
    return " · ".join(partes)

