"""Reporte semanal en PDF para La Cantina Plus."""

from __future__ import annotations

from collections import Counter
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
from app.whapi.client import enviar_documento_bytes


settings = get_settings()
TZ = ZoneInfo(settings.tz or "America/Bogota")
REPORT_DIR = Path(settings.data_dir) / "reportes" / "semanales"


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
            f"El pico fue {top['fecha']} ({etiqueta}) con {top['personas']} personas."
        )
    horas = bot.get("horas_top") or []
    if horas:
        hs = ", ".join(f"{int(h['hora']):02d}:00" for h in horas[:3])
        insights.append(f"Las horas con mas inbound fueron {hs}; buen bloque para pauta y estados.")
    if not insights:
        insights.append("Semana con pocos datos: mantener registro constante de eventos y reservas.")
    return insights[:4]


def generar_pdf_reporte(data: dict[str, Any]) -> Path:
    """Genera un PDF de una sola pagina con el resumen semanal."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    inicio = data["inicio"]
    fin = data["fin"]
    path = REPORT_DIR / f"reporte-semanal-{inicio}_a_{fin}.pdf"

    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        rightMargin=0.45 * inch,
        leftMargin=0.45 * inch,
        topMargin=0.42 * inch,
        bottomMargin=0.38 * inch,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=18, leading=20, textColor=colors.HexColor("#111827"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=10.5, leading=12, textColor=colors.HexColor("#111827"), spaceAfter=5)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=8.2, leading=10, textColor=colors.HexColor("#374151"))
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=7.4, leading=8.8, textColor=colors.HexColor("#4B5563"))

    totals = data["totales"]
    bot = data["bot"]
    story: list[Any] = [
        Paragraph("La Cantina Plus - reporte semanal", title),
        Paragraph(f"Periodo: {inicio} a {fin} - generado automaticamente por Nicky", small),
        Spacer(1, 8),
    ]

    kpis = [
        ["Reservas", str(totals["reservas"]), "Personas", str(totals["personas"]), "Chats", str(bot["chats"])],
        ["Mesas ocup.", str(totals["mesas"]), "Ticket pers/res.", str(totals["personas_por_reserva"]), "Inbound", str(bot["inbound"])],
        ["Eventos", str(totals["eventos"]), "Canceladas", str(totals["canceladas"]), "Humano", str(bot["humano"])],
    ]
    story.append(_table(kpis, col_widths=[1.0 * inch, 0.8 * inch] * 3, font_size=8.4, header=False))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Tendencia semanal", h2))
    story.append(_line_chart(data["filas_dia"], width=6.55 * inch, height=2.35 * inch))
    story.append(Spacer(1, 6))

    top = data["top_dia"]
    bajo = data["bajo_dia"]
    horas = ", ".join(f"{int(h['hora']):02d}:00 ({h['inbound']})" for h in bot.get("horas_top", [])) or "sin datos"
    intents = ", ".join(f"{i['intent']} ({i['total']})" for i in bot.get("intents", [])[:4]) or "sin datos"
    resumen_rows = [
        ["Mejor dia", f"{top.get('fecha', '-')} - {top.get('personas', 0)} personas - {top.get('reservas', 0)} reservas"],
        ["Dia mas bajo", f"{bajo.get('fecha', '-')} - {bajo.get('personas', 0)} personas"],
        ["Horas inbound", horas],
        ["Temas frecuentes", intents],
    ]
    story.append(Paragraph("Lectura rapida", h2))
    story.append(_table(resumen_rows, col_widths=[1.25 * inch, 5.0 * inch], font_size=7.7, header=False))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Insights y acciones sugeridas", h2))
    bullets = "<br/>".join(f"- {txt}" for txt in data["insights"])
    story.append(Paragraph(bullets, body))

    doc.build(story)
    return path


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
        fecha = str(fila.get("fecha") or "")[-5:]
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
    destino = str(params.get("destino_id") or "").strip()
    if not destino:
        return {"ok": False, "error": "falta destino_id para enviar el PDF"}

    caption = (
        f"Reporte semanal La Cantina Plus\n"
        f"{data['inicio']} a {data['fin']}\n"
        f"Reservas: {data['totales']['reservas']} - Personas: {data['totales']['personas']}"
    )
    pdf_bytes = pdf_path.read_bytes()
    await enviar_documento_bytes(
        destino,
        pdf_bytes,
        mime="application/pdf",
        filename=pdf_path.name,
        caption=caption,
    )
    log.info(
        "reporte_semanal.enviado",
        destino=destino,
        inicio=data["inicio"],
        fin=data["fin"],
        bytes=len(pdf_bytes),
    )
    return {
        "ok": True,
        "periodo": periodo,
        "inicio": data["inicio"],
        "fin": data["fin"],
        "destino": destino,
        "pdf": str(pdf_path),
        "bytes": len(pdf_bytes),
        "reservas": data["totales"]["reservas"],
        "personas": data["totales"]["personas"],
    }
