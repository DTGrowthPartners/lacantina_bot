"""Estado/promo actual de La Cantina — la imagen o video que el equipo publica
como estado de WhatsApp y que el bot reenvía a los clientes que lo pidan.

El equipo manda la imagen/video al bot ("publica esto como estado"); el bot lo
publica como story Y lo guarda acá. Cuando un cliente pide "el estado/la promo",
el bot lo envía. Un solo estado vigente a la vez (se sobrescribe).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def _media_story(story: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    for kind in ("image", "video"):
        media = story.get(kind)
        if isinstance(media, dict):
            return kind, media
    return None


def _caption_story(story: dict[str, Any], media: dict[str, Any]) -> str | None:
    return (media.get("caption") or story.get("caption") or "").strip() or None


async def cargar_estados_activos(max_estados: int = 10) -> list[dict[str, Any]]:
    """Devuelve los estados propios activos con media, preferiblemente desde Whapi.

    Si Whapi no trae stories con media descargable, cae al estado local vigente
    para mantener compatibilidad con el comportamiento anterior.
    """
    from app.whapi.client import auth_headers, listar_stories
    import httpx

    estados: list[dict[str, Any]] = []
    try:
        data = await listar_stories(count=100)
        items = data.get("stories") or data.get("messages") or data.get("data") or []
        candidatos = []
        for story in items:
            if not isinstance(story, dict) or not story.get("from_me"):
                continue
            if story.get("subtype") == "revoke" or story.get("action"):
                continue
            media_info = _media_story(story)
            if media_info is None:
                continue
            candidatos.append((story, media_info))
        candidatos.sort(key=lambda item: int(item[0].get("timestamp") or 0), reverse=True)

        async with httpx.AsyncClient(timeout=60) as c:
            for story, (kind, media) in candidatos[:max_estados]:
                media_url = media.get("link") or media.get("url") or media.get("file_path")
                if not media_url and media.get("id"):
                    settings = get_settings()
                    media_url = f"{settings.whapi_base_url}/media/{media['id']}"
                if not media_url:
                    continue
                r = await c.get(media_url, headers=auth_headers())
                if r.status_code >= 400 or not r.content:
                    continue
                mime = media.get("mime_type") or r.headers.get("content-type")
                if not mime:
                    mime = "video/mp4" if kind == "video" else "image/jpeg"
                estados.append({
                    "id": story.get("id"),
                    "tipo": kind,
                    "data": r.content,
                    "mime": mime,
                    "caption": _caption_story(story, media),
                    "filename": f"estado-{story.get('id')}.{ 'mp4' if kind == 'video' else 'jpg' }",
                })
    except Exception:
        estados = []

    if estados:
        return estados

    local = cargar_estado()
    if local is None:
        return []
    path, caption = local
    return [{
        "id": None,
        "tipo": "video" if es_video(path) else "image",
        "data": path.read_bytes(),
        "mime": mime_de(path),
        "caption": caption,
        "filename": path.name,
    }]
