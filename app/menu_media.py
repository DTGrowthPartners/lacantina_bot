"""Assets y deteccion de solicitudes de la carta en imagen."""

from __future__ import annotations

import re
from pathlib import Path

from app.config import get_settings

MENU_URL = "https://menu.pirpos.com/menu/5ff4ce6ffe4b9a75e193fcb9"

_MENU_DIR = Path(get_settings().data_dir) / "media" / "menu-pages"
_MENU_RE = re.compile(
    r"\b(?:imagen|im[aá]genes|foto|fotos|p[aá]gina|p[aá]ginas)\b.{0,40}"
    r"\b(?:men[uú]|carta)\b"
    r"|\b(?:men[uú]|carta)\b.{0,40}"
    r"\b(?:imagen|im[aá]genes|foto|fotos|p[aá]gina|p[aá]ginas)\b",
    re.IGNORECASE,
)


def imagenes_menu() -> list[Path]:
    return [p for p in sorted(_MENU_DIR.glob("menu-*.png")) if p.is_file()]


def pide_imagen_menu(texto: str | None) -> bool:
    return bool(_MENU_RE.search(texto or ""))
