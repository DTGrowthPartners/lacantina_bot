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
    1:  (197,  95),  2: (293,  95),  3: (389,  95),  4: (485,  95),
    5:  (581,  95),  6: (677,  95),  7: (773,  95),  8: (869,  95),
    9:  (869, 185), 10: (773, 185), 11: (677, 185), 12: (581, 185),
    13: (485, 185), 14: (389, 185), 15: (293, 185), 16: (197, 185),
    # VIP — fila 1 L→R, fila 2 R→L
    17: (197, 325), 18: (293, 325), 19: (389, 325), 20: (485, 325), 21: (581, 325),
    22: (485, 400), 23: (389, 400), 24: (293, 400), 25: (197, 400),
    # Rumbero — fila 1 L→R, columna derecha, fila 2 R→L
    26: (197, 480), 27: (293, 480), 28: (389, 480), 29: (485, 480),
    30: (581, 480), 31: (677, 480), 32: (773, 480),
    33: (955, 400), 34: (955, 480),
    35: (869, 625), 36: (773, 625), 37: (677, 625), 38: (581, 625),
    39: (485, 625), 40: (389, 625), 41: (293, 625), 42: (197, 625),
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
