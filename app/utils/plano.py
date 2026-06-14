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
    1:  (252,  49),  2: (349,  49),  3: (445,  49),  4: (542,  49),
    5:  (638,  49),  6: (735,  49),  7: (831,  49),  8: (928,  49),
    9:  (976, 140), 10: (831, 140), 11: (735, 140), 12: (638, 140),
    13: (542, 140), 14: (445, 140), 15: (349, 140), 16: (252, 140),
    # VIP — fila 1 L→R, fila 2 R→L
    17: (252, 299), 18: (349, 299), 19: (445, 299), 20: (542, 299), 21: (638, 299),
    22: (542, 429), 23: (445, 429), 24: (349, 429), 25: (252, 429),
    # Rumbero — fila 1 L→R, columna derecha, fila 2 R→L
    26: (252, 575), 27: (349, 575), 28: (445, 575), 29: (542, 575),
    30: (638, 575), 31: (735, 575), 32: (831, 575),
    33: (976, 500), 34: (976, 605),
    35: (928, 681), 36: (831, 681), 37: (735, 681), 38: (638, 681),
    39: (542, 681), 40: (445, 681), 41: (349, 681), 42: (252, 681),
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
    r = 16  # cubre la placa numerada sin tapar las sillas de la mesa

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
