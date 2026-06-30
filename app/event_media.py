"""Archivos locales asociados a eventos (flyers y descripción).

Los eventos pueden repetirse en una misma fecha, así que la clave principal es
`fecha + hora_inicio`. Para compatibilidad, si no hay archivo específico por
hora, se puede caer al archivo legacy por fecha.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config import get_settings

_settings = get_settings()
FLYERS_DIR = Path(_settings.data_dir) / "media" / "flyers"
EXT_OK = {".jpg", ".jpeg", ".png", ".webp"}
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
EXT_POR_MIME = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _limpiar_segmento(valor: str | None) -> str:
    texto = str(valor or "").strip().lower()
    texto = texto.replace(":", "-")
    texto = re.sub(r"[^a-z0-9_-]+", "-", texto)
    return texto.strip("-")


def clave_evento(fecha: str | None, hora_inicio: str | None = None) -> str:
    fecha_limpia = _limpiar_segmento(fecha)
    hora_limpia = _limpiar_segmento(hora_inicio)
    return f"{fecha_limpia}__{hora_limpia}" if fecha_limpia and hora_limpia else fecha_limpia


def clave_desde_evento(evento: dict) -> str:
    return clave_evento(evento.get("fecha"), evento.get("hora_inicio") or evento.get("hora"))


def flyer_path(fecha: str | None, hora_inicio: str | None = None, *, fallback_fecha: bool = True) -> Path | None:
    claves = [clave_evento(fecha, hora_inicio)]
    if fallback_fecha:
        solo_fecha = clave_evento(fecha)
        if solo_fecha not in claves:
            claves.append(solo_fecha)
    for clave in [c for c in claves if c]:
        for ext in EXT_OK:
            path = FLYERS_DIR / f"{clave}{ext}"
            if path.exists():
                return path
    return None


def flyer_path_evento(evento: dict, *, fallback_fecha: bool = True) -> Path | None:
    return flyer_path(evento.get("fecha"), evento.get("hora_inicio") or evento.get("hora"), fallback_fecha=fallback_fecha)


def descripcion_path(fecha: str | None, hora_inicio: str | None = None, *, fallback_fecha: bool = True) -> Path | None:
    claves = [clave_evento(fecha, hora_inicio)]
    if fallback_fecha:
        solo_fecha = clave_evento(fecha)
        if solo_fecha not in claves:
            claves.append(solo_fecha)
    for clave in [c for c in claves if c]:
        path = FLYERS_DIR / f"{clave}.txt"
        if path.exists():
            return path
    return None


def leer_descripcion_evento(evento: dict) -> str | None:
    path = descripcion_path(evento.get("fecha"), evento.get("hora_inicio") or evento.get("hora"))
    if not path:
        return None
    return path.read_text(encoding="utf-8")


def guardar_flyer(fecha: str | None, hora_inicio: str | None, data: bytes | None, mime: str | None) -> Path | None:
    if not (fecha and data):
        return None
    clave = clave_evento(fecha, hora_inicio)
    if not clave:
        return None
    ext = EXT_POR_MIME.get((mime or "").lower(), ".jpg")
    FLYERS_DIR.mkdir(parents=True, exist_ok=True)
    for old_ext in EXT_OK:
        old = FLYERS_DIR / f"{clave}{old_ext}"
        if old.exists():
            old.unlink()
    path = FLYERS_DIR / f"{clave}{ext}"
    path.write_bytes(data)
    return path


def guardar_descripcion(fecha: str | None, hora_inicio: str | None, texto: str | None) -> Path | None:
    if not (fecha and (texto or "").strip()):
        return None
    clave = clave_evento(fecha, hora_inicio)
    if not clave:
        return None
    FLYERS_DIR.mkdir(parents=True, exist_ok=True)
    path = FLYERS_DIR / f"{clave}.txt"
    path.write_text((texto or "").strip(), encoding="utf-8")
    return path

