"""Genera el plano del salón con mesas reservadas marcadas con X roja.

Uso:
    from app.utils.plano import generar_plano_con_reservas
    png_bytes = generar_plano_con_reservas([1, 5, 17])
"""

from __future__ import annotations

import io
from pathlib import Path

from app.config import get_settings

_PLANO_PATH = Path(get_settings().data_dir) / "media" / "plano-espacio.png"

# Coordenadas (cx, cy) del centro de cada mesa en la imagen 1024×735 px.
# Zona Cantina (1-16): 2 filas en serpentina.
# Zona VIP (17-25): 2 filas en serpentina, dentro del rectángulo dorado.
# Zona Rumbero (26-42): fila superior, columna derecha (33-34), fila inferior en serpentina.
MESAS_XY: dict[int, tuple[int, int]] = {
    # Cantina — fila 1 L→R, fila 2 R→L
    1:  (213,  75),  2: (309,  75),  3: (405,  75),  4: (501,  75),
    5:  (597,  75),  6: (693,  75),  7: (789,  75),  8: (885,  75),
    9:  (885, 165), 10: (789, 165), 11: (693, 165), 12: (597, 165),
    13: (501, 165), 14: (405, 165), 15: (309, 165), 16: (213, 165),
    # VIP — fila 1 L→R, fila 2 R→L
    17: (213, 300), 18: (309, 300), 19: (405, 300), 20: (501, 300), 21: (597, 300),
    22: (501, 375), 23: (405, 375), 24: (309, 375), 25: (213, 375),
    # Rumbero — fila 1 L→R, columna derecha, fila 2 R→L
    26: (213, 455), 27: (309, 455), 28: (405, 455), 29: (501, 455),
    30: (597, 455), 31: (693, 455), 32: (789, 455),
    33: (971, 375), 34: (971, 455),
    35: (885, 600), 36: (789, 600), 37: (693, 600), 38: (597, 600),
    39: (501, 600), 40: (405, 600), 41: (309, 600), 42: (213, 600),
}


def generar_plano_con_reservas(mesas_reservadas: list[int]) -> bytes:
    """Devuelve el plano como PNG con X rojas sobre las mesas ya reservadas.

    Si Pillow no está disponible o la imagen base no existe, devuelve None
    para que el caller use la imagen estática.
    """
    try:
        from PIL import Image, ImageDraw  # type: ignore[import]
    except ImportError:
        return _leer_plano_estatico()

    if not _PLANO_PATH.exists():
        return _leer_plano_estatico()

    img = Image.open(_PLANO_PATH).convert("RGBA")

    if not mesas_reservadas:
        return _encode(img)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    r = 18  # radio del círculo en píxeles

    for num in mesas_reservadas:
        if num not in MESAS_XY:
            continue
        cx, cy = MESAS_XY[num]
        # Círculo rojo semi-transparente
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     fill=(210, 30, 30, 195), outline=(255, 255, 255, 220), width=2)
        # X blanca
        m = r - 5
        draw.line([cx - m, cy - m, cx + m, cy + m], fill=(255, 255, 255, 255), width=3)
        draw.line([cx + m, cy - m, cx - m, cy + m], fill=(255, 255, 255, 255), width=3)

    result = Image.alpha_composite(img, overlay).convert("RGB")
    return _encode(result)


def _encode(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _leer_plano_estatico() -> bytes | None:
    if _PLANO_PATH.exists():
        return _PLANO_PATH.read_bytes()
    return None
