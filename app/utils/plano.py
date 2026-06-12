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
    1:  (225,  57),  2: (321,  57),  3: (417,  57),  4: (513,  57),
    5:  (609,  57),  6: (705,  57),  7: (801,  57),  8: (897,  57),
    9:  (897, 147), 10: (801, 147), 11: (705, 147), 12: (609, 147),
    13: (513, 147), 14: (417, 147), 15: (321, 147), 16: (225, 147),
    # VIP — fila 1 L→R, fila 2 R→L
    17: (228, 308), 18: (324, 308), 19: (420, 308), 20: (516, 308), 21: (612, 308),
    22: (516, 410), 23: (420, 410), 24: (324, 410), 25: (228, 410),
    # Rumbero — fila 1 L→R, columna derecha, fila 2 R→L
    26: (225, 495), 27: (321, 495), 28: (417, 495), 29: (513, 495),
    30: (609, 495), 31: (705, 495), 32: (801, 495),
    33: (930, 410), 34: (930, 495),
    35: (897, 615), 36: (801, 615), 37: (705, 615), 38: (609, 615),
    39: (513, 615), 40: (417, 615), 41: (321, 615), 42: (225, 615),
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
