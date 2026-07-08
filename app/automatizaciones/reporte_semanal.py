"""Reporte semanal en PDF para La Cantina Plus."""

from __future__ import annotations

import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.eventos import extraer_eventos
from app.integrations import cantina_api
from app.logging_setup import log
from app.whapi.client import enviar_imagen_bytes


settings = get_settings()
TZ = ZoneInfo(settings.tz or "America/Bogota")
REPORT_DIR = Path(settings.data_dir) / "reportes" / "semanales"
LETTERHEAD_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "pdf"
    / "Membrete_DTGP_Portada_enblanco.pdf"
)
DAY_LABELS = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]
DAY_LONG = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
FA_SOLID_FONT = "FontAwesome6FreeSolid"
FA_ICONS = {
    "trend": "\uf201",  # chart-line
    "calendar": "\uf274",  # calendar-check
    "people": "\uf0c0",  # users
    "user": "\uf007",
    "chat": "\uf086",  # comments
    "x": "\uf057",  # circle-xmark
    "headset": "\uf590",
    "trophy": "\uf091",
    "calendar-off": "\uf273",  # calendar-xmark
    "clock": "\uf017",
    "bulb": "\uf0eb",
}
MONTHS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


def rango_reporte(periodo: str | None = None, ahora: datetime | None = None) -> tuple[date, date, str]:
    """Devuelve rango local inclusivo para el reporte."""
    hoy = (ahora or datetime.now(timezone.utc)).astimezone(TZ).date()
    if periodo == "ultimos_7_dias":
        fin = hoy - timedelta(days=1)
        inicio = fin - timedelta(days=6)
        return inicio, fin, "ultimos_7_dias"
    # Semana completa anterior: lunes-domingo.
    lunes_actual = hoy - timedelta(days=hoy.weekday())
    fin = lunes_actual - timedelta(days=1)
    inicio = fin - timedelta(days=6)
    return inicio, fin, "semana_anterior"


def _dias(inicio: date, fin: date) -> list[date]:
    total = (fin - inicio).days
    return [inicio + timedelta(days=i) for i in range(total + 1)]


def _dia_label(fecha: str | date, *, incluir_mes: bool = False) -> str:
    dia = date.fromisoformat(fecha) if isinstance(fecha, str) else fecha
    if incluir_mes:
        return f"{DAY_LABELS[dia.weekday()]} {dia.month:02d}/{dia.day:02d}"
    return f"{DAY_LABELS[dia.weekday()]} {dia.day:02d}"


def _payload(resp: dict) -> dict:
    if not isinstance(resp, dict):
        return {}
    data = resp.get("data")
    return data if isinstance(data, dict) else resp


def _lista_reservas(resp: dict) -> list[dict[str, Any]]:
    if not isinstance(resp, dict) or not resp.get("ok", True):
        return []
    data = resp.get("data", resp)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("reservas", "items", "data"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
    return []


def _reservas_resumen_payload(payload: dict) -> tuple[int, int, int, int]:
    reservas = [r for r in (payload.get("reservas") or []) if isinstance(r, dict)]
    salas = [
        s for s in (payload.get("salas") or [])
        if isinstance(s, dict) and isinstance(s.get("reserva"), dict)
    ]
    total = payload.get("total_reservas")
    if total is None:
        total = len(reservas) + len(salas)
    return (
        int(total or 0),
        int(payload.get("total_personas") or 0),
        int(payload.get("mesas_ocupadas") or 0),
        len(salas),
    )


async def recolectar_reporte_semanal(
    session: AsyncSession,
    *,
    inicio: date,
    fin: date,
) -> dict[str, Any]:
    """Recolecta metricas agregadas del bot y backend de mesas."""
    dias = _dias(inicio, fin)
    filas_dia: list[dict[str, Any]] = []
    telefonos: set[str] = set()
    canceladas = 0
    eventos_total = 0
    personas_evento = 0
    personas_normal = 0
    reservas_evento = 0
    reservas_normal = 0

    for dia in dias:
        fecha = dia.isoformat()
        resumen = await cantina_api.resumen_dia(fecha)
        payload = _payload(resumen) if isinstance(resumen, dict) and resumen.get("ok") else {}
        total_reservas, personas, mesas, salas = _reservas_resumen_payload(payload)
        eventos = extraer_eventos(payload)
        eventos_total += len(eventos)

        for r in _lista_reservas(
            await cantina_api.listar_reservas(fecha, incluir_canceladas=True)
        ):
            telefono = "".join(ch for ch in str(r.get("telefono") or "") if ch.isdigit())
            if telefono:
                telefonos.add(telefono[-10:])
            if str(r.get("estado") or "").lower() == "cancelada":
                canceladas += 1

        if eventos:
            reservas_evento += total_reservas
            personas_evento += personas
        else:
            reservas_normal += total_reservas
            personas_normal += personas

        filas_dia.append({
            "fecha": fecha,
            "dow": dia.weekday(),
            "reservas": total_reservas,
            "personas": personas,
            "mesas": mesas,
            "salas": salas,
            "eventos": eventos,
            "evento_txt": " / ".join(
                str(e.get("nombre") or "Evento") for e in eventos
            ),
        })

    bot = await _metricas_bot(session, inicio, fin)
    total_reservas = sum(f["reservas"] for f in filas_dia)
    total_personas = sum(f["personas"] for f in filas_dia)
    total_mesas = sum(f["mesas"] for f in filas_dia)
    dias_con_reservas = sum(1 for f in filas_dia if f["reservas"] > 0)
    top_dia = max(filas_dia, key=lambda f: (f["reservas"], f["personas"])) if filas_dia else {}
    bajo_dia = min(filas_dia, key=lambda f: (f["reservas"], f["personas"])) if filas_dia else {}
    personas_por_reserva = round(total_personas / total_reservas, 1) if total_reservas else 0

    return {
        "inicio": inicio.isoformat(),
        "fin": fin.isoformat(),
        "filas_dia": filas_dia,
        "bot": bot,
        "totales": {
            "reservas": total_reservas,
            "personas": total_personas,
            "mesas": total_mesas,
            "salas": sum(f["salas"] for f in filas_dia),
            "telefonos_unicos": len(telefonos),
            "canceladas": canceladas,
            "eventos": eventos_total,
            "dias_con_reservas": dias_con_reservas,
            "personas_por_reserva": personas_por_reserva,
            "reservas_evento": reservas_evento,
            "personas_evento": personas_evento,
            "reservas_normal": reservas_normal,
            "personas_normal": personas_normal,
        },
        "top_dia": top_dia,
        "bajo_dia": bajo_dia,
        "insights": _insights(filas_dia, bot),
    }


async def _metricas_bot(session: AsyncSession, inicio: date, fin: date) -> dict[str, Any]:
    params = {"inicio": inicio, "fin": fin}
    base = (await session.execute(sa_text("""
        SELECT
          count(*) AS mensajes,
          count(*) FILTER (WHERE direccion='inbound') AS inbound,
          count(*) FILTER (WHERE direccion='outbound') AS outbound,
          count(*) FILTER (WHERE direccion='humano') AS humano,
          count(DISTINCT cliente_id) AS chats
        FROM conversaciones
        WHERE date(timestamp AT TIME ZONE 'America/Bogota') BETWEEN :inicio AND :fin
    """), params)).mappings().first()
    intents_rows = (await session.execute(sa_text("""
        SELECT coalesce(intent, 'sin_intent') AS intent, count(*) AS total
        FROM conversaciones
        WHERE date(timestamp AT TIME ZONE 'America/Bogota') BETWEEN :inicio AND :fin
          AND direccion='inbound'
        GROUP BY 1 ORDER BY total DESC LIMIT 5
    """), params)).mappings().all()
    horas_rows = (await session.execute(sa_text("""
        SELECT extract(hour from timestamp AT TIME ZONE 'America/Bogota')::int AS hora,
               count(*) FILTER (WHERE direccion='inbound') AS inbound
        FROM conversaciones
        WHERE date(timestamp AT TIME ZONE 'America/Bogota') BETWEEN :inicio AND :fin
        GROUP BY 1 ORDER BY inbound DESC LIMIT 3
    """), params)).mappings().all()
    return {
        "mensajes": int(base["mensajes"] or 0),
        "inbound": int(base["inbound"] or 0),
        "outbound": int(base["outbound"] or 0),
        "humano": int(base["humano"] or 0),
        "chats": int(base["chats"] or 0),
        "intents": [dict(r) for r in intents_rows],
        "horas_top": [dict(r) for r in horas_rows],
    }


def _insights(filas: list[dict[str, Any]], bot: dict[str, Any]) -> list[str]:
    insights: list[str] = []
    con_evento = [f for f in filas if f["eventos"]]
    sin_evento = [f for f in filas if not f["eventos"]]
    if con_evento and sin_evento:
        avg_ev = mean([f["personas"] for f in con_evento])
        avg_norm = mean([f["personas"] for f in sin_evento])
        if avg_ev > avg_norm:
            insights.append(
                f"Los dias con evento promediaron {avg_ev:.0f} personas vs {avg_norm:.0f} en dias normales."
            )
    top = max(filas, key=lambda f: f["personas"], default=None)
    if top and top["personas"]:
        etiqueta = top["evento_txt"] or "dia normal"
        insights.append(
            f"El pico fue {_dia_label(top['fecha'], incluir_mes=True)} ({etiqueta}) con {top['personas']} personas."
        )
    horas = bot.get("horas_top") or []
    if horas:
        hs = ", ".join(f"{int(h['hora']):02d}:00" for h in horas[:3])
        insights.append(f"Las horas con mas inbound fueron {hs}; buen bloque para pauta y estados.")
    if not insights:
        insights.append("Semana con pocos datos: mantener registro constante de eventos y reservas.")
    return insights[:4]


def generar_pdf_reporte(data: dict[str, Any]) -> Path:
    """Genera un PDF ejecutivo de una sola pagina con el resumen semanal."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    inicio = data["inicio"]
    fin = data["fin"]
    path = REPORT_DIR / f"reporte-semanal-{inicio}_a_{fin}.pdf"
    content_path = REPORT_DIR / f"reporte-semanal-{inicio}_a_{fin}-contenido.pdf"

    c = canvas.Canvas(str(content_path), pagesize=letter)
    _draw_executive_report(c, data)
    c.save()
    _aplicar_membrete(content_path, path)
    try:
        content_path.unlink()
    except OSError:
        log.warning("reporte_semanal.temp_no_eliminado", path=str(content_path))
    return path


def generar_imagen_reporte(pdf_path: Path) -> Path:
    """Renderiza la primera pagina del reporte PDF a PNG para enviarla por WhatsApp."""
    output_prefix = pdf_path.with_suffix("")
    png_path = output_prefix.with_suffix(".png")
    try:
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-singlefile",
                "-r",
                "180",
                str(pdf_path),
                str(output_prefix),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("pdftoppm no esta instalado; no puedo generar imagen del reporte") from exc
    except subprocess.CalledProcessError as exc:
        detalle = (exc.stderr or exc.stdout or "").strip()[:300]
        raise RuntimeError(f"pdftoppm fallo al generar imagen del reporte: {detalle}") from exc
    if not png_path.exists() or png_path.stat().st_size == 0:
        raise RuntimeError("pdftoppm no genero la imagen del reporte")
    return png_path


def _periodo_largo(inicio: str, fin: str) -> str:
    ini = date.fromisoformat(inicio)
    end = date.fromisoformat(fin)
    return f"{DAY_LONG[ini.weekday()]} {ini.month:02d}/{ini.day:02d} a {DAY_LONG[end.weekday()]} {end.month:02d}/{end.day:02d}"


def _money_color(hex_value: str):
    from reportlab.lib import colors

    return colors.HexColor(hex_value)


def _draw_executive_report(c, data: dict[str, Any]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter

    width, height = letter
    navy = colors.HexColor("#061B3A")
    blue = colors.HexColor("#004FB6")
    light_blue = colors.HexColor("#EEF6FF")
    border = colors.HexColor("#CAD7EA")
    grey = colors.HexColor("#5B6472")
    orange = colors.HexColor("#F05A14")
    green = colors.HexColor("#0B8F3A")
    yellow = colors.HexColor("#D9A300")

    totals = data["totales"]
    bot = data["bot"]
    filas = data["filas_dia"]
    top = data["top_dia"] or {}
    second = _segundo_dia(filas, top)
    sin_reserva = [f for f in filas if int(f.get("reservas") or 0) == 0]
    dias_mov = len(filas) - len(sin_reserva)
    horas_top = bot.get("horas_top") or []

    # Cover body and footer from the letterhead, keeping the DTGP blue header.
    c.setFillColor(colors.white)
    c.rect(0, 0, width, 700, fill=1, stroke=0)

    left = 28
    right = 584
    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 27)
    c.drawString(left, 662, "Reporte semanal - Agente Nicky")
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(blue)
    c.drawString(left, 639, "La Cantina Plus")
    c.setFont("Helvetica", 9.5)
    c.setFillColor(grey)
    c.drawString(left, 623, f"Periodo: {_periodo_largo(data['inicio'], data['fin'])}")

    _rounded_box(c, left, 548, right - left, 58, radius=7, fill=colors.white, stroke=border)
    _draw_circle_icon(c, left + 40, 577, 25, blue, "trend")
    _draw_segments(
        c,
        left + 82,
        584,
        [
            ("Esta semana La Agente Nicky ayudo a generar ", "Helvetica", 12.4, navy),
            (str(totals["reservas"]), "Helvetica-Bold", 15, blue),
            (f" reservas para {totals['personas']} personas.", "Helvetica", 12.4, navy),
        ],
    )
    _draw_segments(
        c,
        left + 82,
        565,
        [
            ("La actividad se concentro en ", "Helvetica", 12.4, navy),
            (str(dias_mov), "Helvetica-Bold", 15, blue),
            (" dias y hubo ", "Helvetica", 12.4, navy),
            (str(len(sin_reserva)), "Helvetica-Bold", 15, blue),
            (" dias sin reservas.", "Helvetica", 12.4, navy),
        ],
    )

    _section_title(c, "Resumen rapido", left, 523, navy)
    card_y = 430
    gap = 10
    card_w = (right - left - gap * 5) / 6
    metric_cards = [
        ("Reservas", totals["reservas"], "calendar", blue),
        ("Personas", totals["personas"], "people", blue),
        ("Promedio por reserva", totals["personas_por_reserva"], "user", blue),
        ("Chats atendidos", bot["chats"], "chat", blue),
        ("Cancelaciones", totals["canceladas"], "x", orange),
        ("Atenciones humanas", bot["humano"], "headset", blue),
    ]
    for idx, (label, value, icon, color) in enumerate(metric_cards):
        x = left + idx * (card_w + gap)
        _metric_card(c, x, card_y, card_w, 82, label, str(value), icon, color)

    _section_title(c, "Lo que debes ver rapido", left, 407, navy)
    high_y = 300
    high_gap = 9
    high_w = (right - left - high_gap * 3) / 4
    zero_txt = ", ".join(_dia_label(f["fecha"], incluir_mes=True) for f in sin_reserva[:5]) or "Sin dias vacios"
    if len(sin_reserva) > 5:
        zero_txt += "..."
    horas_txt = _horas_humanas(horas_top[:3])
    highlights = [
        ("Mejor dia", _dia_largo_corto(top.get("fecha")), f"{top.get('personas', 0)} personas - {top.get('reservas', 0)} reservas", "trophy", green, colors.HexColor("#EEFBF2")),
        ("Segundo mejor dia", _dia_largo_corto(second.get("fecha")), f"{second.get('personas', 0)} personas - {second.get('reservas', 0)} reservas", "trend", blue, colors.HexColor("#EFF6FF")),
        ("Dias sin reservas", f"{len(sin_reserva)} de {len(filas)} dias", zero_txt, "calendar-off", orange, colors.HexColor("#FFF7ED")),
        ("Horas con mas mensajes", horas_txt[0], horas_txt[1], "clock", yellow, colors.HexColor("#FFFBEB")),
    ]
    for idx, item in enumerate(highlights):
        x = left + idx * (high_w + high_gap)
        _highlight_card(c, x, high_y, high_w, 92, *item)

    _section_title(c, "Comportamiento por dia", left, 283, navy)
    chart_x, chart_y, chart_w, chart_h = left, 145, 315, 126
    table_x, table_y, table_w, table_h = left + 325, 145, 231, 126
    _bar_chart(c, filas, chart_x, chart_y, chart_w, chart_h, blue)
    _daily_table(c, filas, table_x, table_y, table_w, table_h, blue, green)

    _section_title(c, "Conclusiones de la semana", left, 127, navy)
    _rounded_box(c, left, 48, right - left, 66, radius=7, fill=colors.white, stroke=border)
    _draw_circle_icon(c, left + 38, 81, 24, colors.HexColor("#DCEBFF"), "bulb", blue)
    bullets = _conclusiones(data, top, second, sin_reserva, horas_top)
    c.setFont("Helvetica", 8.7)
    c.setFillColor(navy)
    y = 96
    for bullet in bullets[:4]:
        c.setFillColor(blue)
        c.circle(left + 82, y + 2, 2.3, fill=1, stroke=0)
        c.setFillColor(navy)
        c.drawString(left + 95, y, bullet)
        y -= 13

    c.setStrokeColor(blue)
    c.setLineWidth(0.7)
    c.line(left, 37, right, 37)
    c.setFillColor(blue)
    c.setFont("Helvetica", 12)
    c.drawCentredString(width / 2, 23, "Impulsamos crecimiento con estrategia, tecnologia y ejecucion.")
    c.setFillColor(colors.HexColor("#7A8290"))
    c.setFont("Helvetica", 8.5)
    c.drawCentredString(width / 2, 10, "dtgrowthpartners.com | +57 300 7189383 | dairotraslavina.com")


def _segundo_dia(filas: list[dict[str, Any]], top: dict[str, Any]) -> dict[str, Any]:
    ordenados = sorted(filas, key=lambda f: (int(f.get("personas") or 0), int(f.get("reservas") or 0)), reverse=True)
    top_fecha = top.get("fecha")
    for fila in ordenados:
        if fila.get("fecha") != top_fecha:
            return fila
    return {}


def _dia_largo_corto(fecha: str | None) -> str:
    if not fecha:
        return "Sin datos"
    dia = date.fromisoformat(fecha)
    return f"{DAY_LONG[dia.weekday()]} {dia.day:02d} de {MONTHS[dia.month - 1]}"


def _horas_humanas(rows: list[dict[str, Any]]) -> tuple[str, str]:
    if not rows:
        return "Sin datos", ""
    partes = [_hora_12(int(r["hora"])) for r in rows[:3]]
    if len(partes) == 1:
        return partes[0], ""
    return ", ".join(partes[:-1]), f"y {partes[-1]}"


def _hora_12(hora: int) -> str:
    sufijo = "a. m." if hora < 12 else "p. m."
    h = hora % 12 or 12
    return f"{h}:00 {sufijo}"


def _conclusiones(
    data: dict[str, Any],
    top: dict[str, Any],
    second: dict[str, Any],
    sin_reserva: list[dict[str, Any]],
    horas_top: list[dict[str, Any]],
) -> list[str]:
    top_txt = DAY_LONG[date.fromisoformat(top["fecha"]).weekday()].lower() if top.get("fecha") else "el mejor dia"
    second_txt = DAY_LONG[date.fromisoformat(second["fecha"]).weekday()].lower() if second.get("fecha") else "otro dia"
    horas = ", ".join(_hora_12(int(h["hora"])) for h in horas_top[:3]) or "sin datos"
    return [
        f"Las reservas se concentraron en {top_txt} y {second_txt}.",
        f"{len(sin_reserva)} de los {len(data['filas_dia'])} dias no tuvieron reservas.",
        f"Los mejores horarios para activar comunicacion fueron {horas}.",
        "Conviene reforzar mensajes, pauta o recordatorios en los dias sin reservas.",
    ]


def _section_title(c, text: str, x: float, y: float, color) -> None:
    c.setFillColor(color)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x, y, text)


def _rounded_box(c, x: float, y: float, w: float, h: float, *, radius: float, fill, stroke) -> None:
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(0.6)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=1)


def _metric_card(c, x: float, y: float, w: float, h: float, label: str, value: str, icon: str, color) -> None:
    from reportlab.lib import colors

    _rounded_box(c, x, y, w, h, radius=7, fill=colors.white, stroke=colors.HexColor("#DDE3EC"))
    _draw_icon(c, x + w / 2, y + 58, icon, color, 18)
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 7.8 if len(label) > 16 else 8.7)
    c.drawCentredString(x + w / 2, y + 34, label)
    c.setFillColor(color)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(x + w / 2, y + 12, value)


def _highlight_card(
    c,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    main: str,
    sub: str,
    icon: str,
    color,
    fill,
) -> None:
    from reportlab.lib import colors

    _rounded_box(c, x, y, w, h, radius=7, fill=fill, stroke=colors.Color(color.red, color.green, color.blue, alpha=0.25))
    _draw_circle_icon(c, x + w / 2, y + h - 24, 19, colors.Color(color.red, color.green, color.blue, alpha=0.12), icon, color)
    c.setFillColor(color)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawCentredString(x + w / 2, y + 43, title)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(x + w / 2, y + 27, main[:25])
    c.setFont("Helvetica", 8.2)
    for idx, line in enumerate(_split_center(sub, 28)[:2]):
        c.drawCentredString(x + w / 2, y + 13 - idx * 10, line)


def _split_center(text: str, max_chars: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _draw_segments(c, x: float, y: float, segments: list[tuple[str, str, float, Any]]) -> None:
    current = x
    for text, font, size, color in segments:
        c.setFont(font, size)
        c.setFillColor(color)
        c.drawString(current, y, text)
        current += c.stringWidth(text, font, size)


def _bar_chart(c, filas: list[dict[str, Any]], x: float, y: float, w: float, h: float, blue) -> None:
    from reportlab.lib import colors

    _rounded_box(c, x, y, w, h, radius=6, fill=colors.white, stroke=colors.HexColor("#DDE3EC"))
    left = x + 28
    bottom = y + 28
    top = y + h - 28
    right = x + w - 12
    chart_h = top - bottom
    values = [int(f.get("personas") or 0) for f in filas]
    max_value = max(values or [0])
    scale_max = max(10, ((max_value + 39) // 40) * 40)
    avg = sum(values) / len(values) if values else 0

    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(colors.black)
    c.drawString(x + 36, y + h - 15, "Personas reservadas por dia")
    c.setFillColor(blue)
    c.setFont("Helvetica", 7)
    c.drawRightString(right, y + h - 15, f"--- Promedio semanal: {avg:.0f}")

    c.setStrokeColor(colors.HexColor("#D7DEE8"))
    c.setLineWidth(0.4)
    for i in range(5):
        yy = bottom + chart_h * i / 4
        val = int(scale_max * i / 4)
        c.line(left, yy, right, yy)
        c.setFillColor(colors.HexColor("#5B6472"))
        c.setFont("Helvetica", 6.5)
        c.drawRightString(left - 8, yy - 2, str(val))

    avg_y = bottom + (avg / scale_max * chart_h if scale_max else 0)
    c.setStrokeColor(blue)
    c.setDash(3, 2)
    c.line(left, avg_y, right, avg_y)
    c.setDash()

    bar_gap = (right - left) / len(filas)
    bar_w = min(18, bar_gap * 0.42)
    for idx, fila in enumerate(filas):
        value = int(fila.get("personas") or 0)
        cx = left + bar_gap * idx + bar_gap / 2
        bar_h = value / scale_max * chart_h if scale_max else 0
        c.setFillColor(blue)
        c.rect(cx - bar_w / 2, bottom, bar_w, bar_h, fill=1, stroke=0)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 6.8)
        c.drawCentredString(cx, bottom + bar_h + 5, str(value))
        c.setFont("Helvetica-Bold", 6.8)
        c.drawCentredString(cx, y + 10, _dia_label(fila["fecha"], incluir_mes=True))

    c.setStrokeColor(colors.HexColor("#8A94A6"))
    c.setLineWidth(0.5)
    c.line(left, bottom, right, bottom)
    c.line(left, bottom, left, top)


def _daily_table(c, filas: list[dict[str, Any]], x: float, y: float, w: float, h: float, blue, green) -> None:
    from reportlab.lib import colors

    _rounded_box(c, x, y, w, h, radius=6, fill=colors.white, stroke=colors.HexColor("#DDE3EC"))
    headers = ["Dia", "Personas", "Reservas", "Estado"]
    col_w = [48, 50, 50, w - 148]
    row_h = h / 8
    c.setFillColor(blue)
    c.roundRect(x, y + h - row_h, w, row_h, 5, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 7.2)
    xx = x
    for idx, header in enumerate(headers):
        c.drawCentredString(xx + col_w[idx] / 2, y + h - row_h + 8, header)
        xx += col_w[idx]

    top = max(filas, key=lambda f: int(f.get("personas") or 0), default={})
    second = _segundo_dia(filas, top)
    for idx, fila in enumerate(filas):
        yy = y + h - row_h * (idx + 2)
        c.setFillColor(colors.white)
        c.rect(x, yy, w, row_h, fill=1, stroke=0)
        c.setStrokeColor(colors.HexColor("#E5E7EB"))
        c.line(x, yy, x + w, yy)
        estado = "Sin reservas"
        estado_color = colors.black
        if fila.get("fecha") == top.get("fecha") and int(fila.get("personas") or 0) > 0:
            estado = "Mejor dia"
            estado_color = green
        elif fila.get("fecha") == second.get("fecha") and int(fila.get("personas") or 0) > 0:
            estado = "Buen movimiento"
            estado_color = blue
        values = [_dia_label(fila["fecha"], incluir_mes=True), str(fila["personas"]), str(fila["reservas"]), estado]
        xx = x
        for col, value in enumerate(values):
            c.setFillColor(estado_color if col == 3 else colors.black)
            c.setFont("Helvetica-Bold" if col in (0, 3) else "Helvetica", 6.8)
            c.drawCentredString(xx + col_w[col] / 2, yy + 7.5, value)
            xx += col_w[col]


def _draw_circle_icon(c, cx: float, cy: float, radius: float, fill, icon: str, icon_color=None) -> None:
    c.setFillColor(fill)
    c.circle(cx, cy, radius, fill=1, stroke=0)
    _draw_icon(c, cx, cy, icon, icon_color or _money_color("#FFFFFF"), radius * 0.75)


def _fontawesome_solid_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        pdfmetrics.getFont(FA_SOLID_FONT)
        return FA_SOLID_FONT
    except KeyError:
        pass

    import fontawesomefree

    package_dir = Path(fontawesomefree.__file__).resolve().parent
    font_path = package_dir / "static" / "fontawesomefree" / "webfonts" / "fa-solid-900.ttf"
    if not font_path.exists():
        matches = list(package_dir.rglob("fa-solid-900.ttf"))
        if not matches:
            raise FileNotFoundError("No se encontro fa-solid-900.ttf de fontawesomefree")
        font_path = matches[0]
    pdfmetrics.registerFont(TTFont(FA_SOLID_FONT, str(font_path)))
    return FA_SOLID_FONT


def _draw_icon(c, cx: float, cy: float, icon: str, color, size: float) -> None:
    glyph = FA_ICONS.get(icon)
    if not glyph:
        return

    font_name = _fontawesome_solid_font()
    font_size = size * 1.08
    c.saveState()
    c.setFillColor(color)
    c.setFont(font_name, font_size)
    c.drawCentredString(cx, cy - font_size * 0.36, glyph)
    c.restoreState()


def _aplicar_membrete(content_path: Path, output_path: Path) -> None:
    if not LETTERHEAD_PATH.exists():
        log.warning("reporte_semanal.membrete_no_encontrado", path=str(LETTERHEAD_PATH))
        content_path.replace(output_path)
        return

    try:
        from pypdf import PdfReader, PdfWriter

        content_reader = PdfReader(str(content_path))
        writer = PdfWriter(clone_from=str(LETTERHEAD_PATH))
        for idx, content_page in enumerate(content_reader.pages):
            if idx >= len(writer.pages):
                writer.add_blank_page(width=content_page.mediabox.width, height=content_page.mediabox.height)
            page = writer.pages[idx]
            page.merge_page(content_page)
        with output_path.open("wb") as fh:
            writer.write(fh)
    except Exception as exc:
        log.warning("reporte_semanal.membrete_fail", error=str(exc)[:200])
        content_path.replace(output_path)


def _line_chart(filas: list[dict[str, Any]], *, width: float, height: float):
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Circle, Drawing, Line, Rect, String

    drawing = Drawing(width, height)
    left = 34
    right = 12
    top = 18
    bottom = 32
    chart_w = width - left - right
    chart_h = height - top - bottom
    values = [int(f.get("personas") or 0) for f in filas]
    max_value = max(values or [0])
    scale_max = max(10, ((max_value + 9) // 10) * 10)
    avg = sum(values) / len(values) if values else 0

    drawing.add(Rect(0, 0, width, height, fillColor=colors.HexColor("#F9FAFB"), strokeColor=colors.HexColor("#E5E7EB")))
    drawing.add(String(left, height - 10, "Personas reservadas por dia", fontName="Helvetica-Bold", fontSize=8.5, fillColor=colors.HexColor("#111827")))
    drawing.add(String(width - 104, height - 10, "Linea azul: demanda", fontName="Helvetica", fontSize=7.2, fillColor=colors.HexColor("#2563EB")))

    # Grid and y labels.
    for i in range(5):
        y = bottom + (chart_h * i / 4)
        valor = int(scale_max * i / 4)
        drawing.add(Line(left, y, width - right, y, strokeColor=colors.HexColor("#E5E7EB"), strokeWidth=0.5))
        drawing.add(String(6, y - 3, str(valor), fontName="Helvetica", fontSize=6.8, fillColor=colors.HexColor("#6B7280")))

    if values:
        avg_y = bottom + (avg / scale_max * chart_h)
        dash = 4
        x = left
        while x < width - right:
            drawing.add(Line(x, avg_y, min(x + dash, width - right), avg_y, strokeColor=colors.HexColor("#F59E0B"), strokeWidth=0.75))
            x += dash * 2
        drawing.add(String(width - 92, avg_y + 3, f"Prom. {avg:.0f}", fontName="Helvetica", fontSize=6.8, fillColor=colors.HexColor("#B45309")))

    step = chart_w / max(1, len(filas) - 1)
    points: list[tuple[float, float, int, str]] = []
    for idx, fila in enumerate(filas):
        value = int(fila.get("personas") or 0)
        x = left + step * idx
        y = bottom + (value / scale_max * chart_h if scale_max else 0)
        fecha_raw = str(fila.get("fecha") or "")
        fecha = _dia_label(fecha_raw, incluir_mes=True) if fecha_raw else ""
        points.append((x, y, value, fecha))

    for (x1, y1, *_), (x2, y2, *__) in zip(points, points[1:]):
        drawing.add(Line(x1, y1, x2, y2, strokeColor=colors.HexColor("#2563EB"), strokeWidth=2.2))

    for x, y, value, fecha in points:
        drawing.add(Circle(x, y, 3.1, fillColor=colors.HexColor("#2563EB"), strokeColor=colors.white, strokeWidth=0.8))
        drawing.add(String(x - 7, y + 7, str(value), fontName="Helvetica-Bold", fontSize=7, fillColor=colors.HexColor("#111827")))
        drawing.add(String(x - 12, 12, fecha, fontName="Helvetica", fontSize=7, fillColor=colors.HexColor("#4B5563")))

    drawing.add(Line(left, bottom, left, bottom + chart_h, strokeColor=colors.HexColor("#9CA3AF"), strokeWidth=0.75))
    drawing.add(Line(left, bottom, width - right, bottom, strokeColor=colors.HexColor("#9CA3AF"), strokeWidth=0.75))
    return drawing


def _table(rows: list[list[str]], *, col_widths: list[float], font_size: float, header: bool = True):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    t = Table(rows, colWidths=col_widths, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("LEADING", (0, 0), (-1, -1), font_size + 1.2),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style.extend([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ])
    else:
        style.extend([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ])
    t.setStyle(TableStyle(style))
    return t


async def accion_reporte_semanal_pdf(session: AsyncSession, params: dict) -> dict:
    inicio_raw = params.get("inicio")
    fin_raw = params.get("fin")
    if inicio_raw and fin_raw:
        inicio, fin = date.fromisoformat(str(inicio_raw)), date.fromisoformat(str(fin_raw))
        periodo = "custom"
    else:
        inicio, fin, periodo = rango_reporte(str(params.get("periodo") or "semana_anterior"))

    data = await recolectar_reporte_semanal(session, inicio=inicio, fin=fin)
    pdf_path = generar_pdf_reporte(data)
    image_path = generar_imagen_reporte(pdf_path)
    destino = str(params.get("destino_id") or "").strip()
    if not destino:
        return {"ok": False, "error": "falta destino_id para enviar la imagen"}

    caption = (
        f"Reporte semanal La Cantina Plus - Agente Nicky\n"
        f"{_periodo_largo(data['inicio'], data['fin'])}\n"
        f"Reservas: {data['totales']['reservas']} - Personas: {data['totales']['personas']}"
    )
    image_bytes = image_path.read_bytes()
    await enviar_imagen_bytes(
        destino,
        image_bytes,
        mime="image/png",
        filename=image_path.name,
        caption=caption,
    )
    log.info(
        "reporte_semanal.enviado",
        destino=destino,
        inicio=data["inicio"],
        fin=data["fin"],
        bytes=len(image_bytes),
        formato="imagen",
    )
    return {
        "ok": True,
        "periodo": periodo,
        "inicio": data["inicio"],
        "fin": data["fin"],
        "destino": destino,
        "pdf": str(pdf_path),
        "imagen": str(image_path),
        "bytes": len(image_bytes),
        "reservas": data["totales"]["reservas"],
        "personas": data["totales"]["personas"],
    }
