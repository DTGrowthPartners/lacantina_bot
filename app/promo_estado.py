"""Estado/promo actual de La Cantina — la imagen o video que el equipo publica
como estado de WhatsApp y que el bot reenvía a los clientes que lo pidan.

El equipo manda la imagen/video al bot ("publica esto como estado"); el bot lo
publica como story Y lo guarda acá. Cuando un cliente pide "el estado/la promo",
el bot lo envía. Un solo estado vigente a la vez (se sobrescribe).
"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings

_DIR = Path(get_settings().data_dir) / "media"
_BASE = "estado-actual"
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_VID_EXTS = (".mp4", ".mov")
_EXTS = _IMG_EXTS + _VID_EXTS
_CAPTION = _DIR / f"{_BASE}.txt"
_EXT_POR_MIME = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "video/mp4": ".mp4", "video/quicktime": ".mov",
}


def guardar_estado(media_bytes: bytes, mime: str | None, caption: str | None) -> Path:
    """Guarda la imagen/video como el estado vigente (sobrescribe el anterior)."""
    _DIR.mkdir(parents=True, exist_ok=True)
    m = (mime or "").lower()
    ext = _EXT_POR_MIME.get(m) or (".mp4" if m.startswith("video/") else ".jpg")
    for e in _EXTS:  # un solo estado vigente: borra cualquier variante anterior
        old = _DIR / f"{_BASE}{e}"
        if old.exists():
            try:
                old.unlink()
            except Exception:
                pass
    path = _DIR / f"{_BASE}{ext}"
    path.write_bytes(media_bytes)
    if caption and caption.strip():
        _CAPTION.write_text(caption.strip(), encoding="utf-8")
    elif _CAPTION.exists():
        _CAPTION.unlink()
    return path


def cargar_estado() -> tuple[Path, str | None] | None:
    """Devuelve (ruta, caption) del estado vigente, o None si no hay."""
    for e in _EXTS:
        p = _DIR / f"{_BASE}{e}"
        if p.exists():
            caption = _CAPTION.read_text(encoding="utf-8") if _CAPTION.exists() else None
            return p, caption
    return None


def es_video(path: Path) -> bool:
    return path.suffix.lower() in _VID_EXTS


def mime_de(path: Path) -> str:
    s = path.suffix.lower()
    return {
        ".png": "image/png", ".webp": "image/webp",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".mp4": "video/mp4", ".mov": "video/quicktime",
    }.get(s, "image/jpeg")
