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
    1:  (250,  57),  2: (346,  57),  3: (442,  57),  4: (538,  57),
    5:  (634,  57),  6: (730,  57),  7: (826,  57),  8: (922,  57),
    9:  (937, 147), 10: (826, 147), 11: (730, 147), 12: (634, 147),
    13: (538, 147), 14: (442, 147), 15: (346, 147), 16: (250, 147),
    # VIP — fila 1 L→R, fila 2 R→L
    17: (253, 318), 18: (349, 318), 19: (445, 318), 20: (541, 318), 21: (637, 318),
    22: (541, 420), 23: (445, 420), 24: (349, 420), 25: (253, 420),
    # Rumbero — fila 1 L→R, columna derecha, fila 2 R→L
    26: (250, 555), 27: (346, 555), 28: (442, 535), 29: (538, 535),
    30: (634, 535), 31: (730, 535), 32: (826, 535),
    33: (955, 450), 34: (955, 535),
    35: (922, 655), 36: (826, 655), 37: (730, 655), 38: (634, 655),
    39: (538, 655), 40: (442, 655), 41: (346, 655), 42: (250, 655),
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
