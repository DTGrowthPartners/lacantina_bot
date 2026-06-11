"""Estado/promo actual de La Cantina — la imagen que el equipo publica como
estado de WhatsApp y que el bot reenvía a los clientes que la pidan.

El equipo manda la imagen al bot ("publica esto como estado"); el bot la publica
como story Y la guarda acá. Cuando un cliente pide "el estado/la promo", el bot
envía esta imagen. Un solo estado vigente a la vez (se sobrescribe).
"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings

_DIR = Path(get_settings().data_dir) / "media"
_BASE = "estado-actual"
_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_CAPTION = _DIR / f"{_BASE}.txt"
_EXT_POR_MIME = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
}


def guardar_estado(imagen_bytes: bytes, mime: str | None, caption: str | None) -> Path:
    """Guarda la imagen como el estado vigente (sobrescribe el anterior)."""
    _DIR.mkdir(parents=True, exist_ok=True)
    ext = _EXT_POR_MIME.get((mime or "").lower(), ".jpg")
    for e in _EXTS:  # un solo estado vigente: borra cualquier variante anterior
        old = _DIR / f"{_BASE}{e}"
        if old.exists():
            try:
                old.unlink()
            except Exception:
                pass
    path = _DIR / f"{_BASE}{ext}"
    path.write_bytes(imagen_bytes)
    if caption and caption.strip():
        _CAPTION.write_text(caption.strip(), encoding="utf-8")
    elif _CAPTION.exists():
        _CAPTION.unlink()
    return path


def cargar_estado() -> tuple[Path, str | None] | None:
    """Devuelve (ruta_imagen, caption) del estado vigente, o None si no hay."""
    for e in _EXTS:
        p = _DIR / f"{_BASE}{e}"
        if p.exists():
            caption = _CAPTION.read_text(encoding="utf-8") if _CAPTION.exists() else None
            return p, caption
    return None


def mime_de(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else (
        "image/webp" if path.suffix.lower() == ".webp" else "image/jpeg"
    )
