"""/admin/reservas — Ver y crear reservas (contra el backend de mesas).

Usa el cliente HTTP `cantina_api` (mismo backend que ve el bot). Permite:
- Listar las reservas de una fecha + resumen (ocupación, personas, covers).
- Crear una reserva (mesa, cliente, personas, notas).
- Cancelar una reserva.
"""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.admin._shell import ICON_SPRITE, SHELL_STYLES, THEME_TOGGLE_JS, sidebar_html
from app.integrations import cantina_api
from app.logging_setup import log

router = APIRouter(prefix="/admin/reservas", tags=["admin-reservas"])


def _check_auth(request: Request) -> bool:
    return "admin_token" in request.session


def _hoy() -> str:
    return datetime.now(ZoneInfo("America/Bogota")).date().isoformat()


def _reservas_de(resp: dict) -> list[dict]:
    if not isinstance(resp, dict):
        return []
    data = resp.get("data", resp)
    if isinstance(data, dict):
        return [r for r in (data.get("reservas") or []) if isinstance(r, dict)]
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def vista(request: Request, fecha: str = ""):
    if not _check_auth(request):
        raise HTTPException(401)
    fecha = (fecha or "").strip() or _hoy()

    error = ""
    reservas: list[dict] = []
    resumen: dict = {}
    mesas: list[dict] = []
    try:
        rl = await cantina_api.listar_reservas(fecha)
        reservas = _reservas_de(rl)
        rs = await cantina_api.resumen_dia(fecha)
        resumen = rs.get("data", rs) if isinstance(rs, dict) and rs.get("ok") else {}
        ml = await cantina_api.listar_mesas()
        md = ml.get("data", ml) if isinstance(ml, dict) else ml
        mesas = [m for m in (md.get("mesas") if isinstance(md, dict) else md) or [] if isinstance(m, dict)]
    except Exception as e:
        error = str(e)[:200]
        log.warning("admin.reservas.cargar_fail", error=error)

    # Resumen header
    rsum = ""
    if resumen:
        partes = []
        if resumen.get("mesas_ocupadas") is not None:
            partes.append(f"{resumen.get('mesas_ocupadas')}/{resumen.get('mesas_totales','?')} mesas")
        if resumen.get("total_personas") is not None:
            partes.append(f"{resumen.get('total_personas')} personas")
        if resumen.get("covers_pendientes"):
            partes.append(f"{resumen.get('covers_pendientes')} cover pend.")
        ev = resumen.get("evento")
        if isinstance(ev, dict) and ev.get("nombre"):
            partes.append(f"🎤 {ev.get('nombre')}")
        rsum = " · ".join(partes)

    # Filas de reservas
    filas = []
    for r in reservas:
        rid = r.get("id")
        cover = r.get("cover_estado") or "no_aplica"
        cover_badge = "" if cover == "no_aplica" else f'<span class="badge cover-{html.escape(cover)}">{html.escape(cover)}</span>'
        tel = html.escape(str(r.get("telefono") or ""))
        notas = html.escape((r.get("notas") or "")[:80])
        filas.append(f"""
        <tr>
          <td>#{rid}</td>
          <td><span class="cell-main">{html.escape(str(r.get('nombre_cliente') or '—'))}</span>
              <div class="cell-sub">{tel}</div></td>
          <td>Mesa {html.escape(str(r.get('mesa_numero') or '?'))} <span class="cell-sub">{html.escape(str(r.get('mesa_zona') or ''))}</span></td>
          <td>{html.escape(str(r.get('num_personas') or ''))}</td>
          <td><span class="badge estado-{html.escape(str(r.get('estado') or ''))}">{html.escape(str(r.get('estado') or ''))}</span> {cover_badge}</td>
          <td class="notas">{notas}</td>
          <td style="text-align:right;">
            <form method="POST" action="/admin/reservas/{rid}/cancelar" style="margin:0;display:inline;"
                  onsubmit="return confirm('¿Cancelar la reserva #{rid}? Libera la mesa.');">
              <input type="hidden" name="fecha" value="{html.escape(fecha)}"/>
              <button class="btn-mini danger" title="Cancelar reserva">Cancelar</button>
            </form>
          </td>
        </tr>""")
    cuerpo = "".join(filas)
    if error:
        cuerpo = f'<tr><td colspan="7" class="empty">No se pudo leer el backend de mesas: {html.escape(error)}</td></tr>'
    elif not filas:
        cuerpo = '<tr><td colspan="7" class="empty">No hay reservas para esta fecha.</td></tr>'

    # Opciones de mesa (para el form de crear)
    mesas.sort(key=lambda m: (m.get("numero") or 0))
    opts_mesa = "".join(
        f'<option value="{m.get("id")}">Mesa {m.get("numero")} · {html.escape(str(m.get("zona") or ""))} · {m.get("capacidad")}p</option>'
        for m in mesas if m.get("activa", 1)
    )

    flash = ""
    msg = request.query_params.get("msg")
    if msg == "creada":
        flash = '<div class="flash ok">Reserva creada.</div>'
    elif msg == "cancelada":
        flash = '<div class="flash ok">Reserva cancelada.</div>'
    elif msg and msg.startswith("error:"):
        flash = f'<div class="flash err">{html.escape(msg[6:][:200])}</div>'

    body = (
        _TEMPLATE
        .replace("__SHELL_STYLES__", SHELL_STYLES)
        .replace("__ICON_SPRITE__", ICON_SPRITE)
        .replace("__SIDEBAR__", sidebar_html(active="reservas"))
        .replace("__THEME_JS__", THEME_TOGGLE_JS)
        .replace("{{fecha}}", html.escape(fecha))
        .replace("{{resumen}}", html.escape(rsum))
        .replace("{{filas}}", cuerpo)
        .replace("{{opts_mesa}}", opts_mesa)
        .replace("{{flash}}", flash)
        .replace("{{total}}", str(len(reservas)))
    )
    return HTMLResponse(body)


@router.post("/crear")
async def crear(
    request: Request,
    fecha: str = Form(...),
    mesa_id: int = Form(...),
    nombre_cliente: str = Form(...),
    num_personas: int = Form(...),
    telefono: str = Form(""),
    notas: str = Form(""),
):
    if not _check_auth(request):
        raise HTTPException(401)
    autor = request.session.get("admin_user", "admin")
    payload = {
        "fecha": fecha.strip(),
        "mesa_id": mesa_id,
        "nombre_cliente": nombre_cliente.strip(),
        "num_personas": num_personas,
    }
    if telefono.strip():
        payload["telefono"] = telefono.strip()
    nota = notas.strip()
    payload["notas"] = (nota + f" · creada por {autor} desde el admin").strip(" ·")
    res = await cantina_api.crear_reserva(payload)
    if isinstance(res, dict) and res.get("ok"):
        log.info("admin.reservas.creada", mesa_id=mesa_id, fecha=fecha, autor=autor)
        # Avisar al grupo del equipo de la reserva nueva.
        r = res.get("reserva") or res.get("data") or {}
        mesa_n = r.get("mesa_numero") if isinstance(r, dict) else None
        try:
            from app.notif_equipo import notificar_equipo
            await notificar_equipo(
                f"🪑 *Reserva nueva* (desde el panel)\n"
                f"👤 {nombre_cliente.strip()} · 👥 {num_personas}p\n"
                f"🍽️ Mesa {mesa_n or mesa_id} · 📅 {fecha.strip()}"
                + (f"\n📝 {nota}" if nota else "")
            )
        except Exception as e:
            log.warning("admin.reservas.notif_fail", error=str(e))
        return RedirectResponse(f"/admin/reservas?fecha={fecha}&msg=creada", status_code=303)
    err = (res or {}).get("error", "no se pudo crear")
    log.warning("admin.reservas.crear_fail", error=err)
    return RedirectResponse(f"/admin/reservas?fecha={fecha}&msg=error:{err}", status_code=303)


@router.post("/{reserva_id}/cancelar")
async def cancelar(reserva_id: int, request: Request, fecha: str = Form("")):
    if not _check_auth(request):
        raise HTTPException(401)
    res = await cantina_api.cancelar_reserva(reserva_id)
    fecha = (fecha or "").strip() or _hoy()
    if isinstance(res, dict) and res.get("ok"):
        log.info("admin.reservas.cancelada", reserva_id=reserva_id)
        return RedirectResponse(f"/admin/reservas?fecha={fecha}&msg=cancelada", status_code=303)
    err = (res or {}).get("error", "no se pudo cancelar")
    return RedirectResponse(f"/admin/reservas?fecha={fecha}&msg=error:{err}", status_code=303)


_TEMPLATE = """<!doctype html>
<html lang="es" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reservas — La Cantina</title>
__SHELL_STYLES__
<style>
  .page-title { font-size: 22px; font-weight: 600; margin: 0 0 4px; color: var(--text-primary); }
  .page-subtitle { color: var(--text-secondary); font-size: 13px; margin-bottom: 16px; }
  .toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
  .toolbar input[type=date] { padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px;
                              background: var(--bg-card); color: var(--text-primary); font: inherit; }
  .resumen-chip { font-size: 12.5px; color: var(--text-secondary); }
  .flash { padding: 8px 12px; border-radius: 8px; font-size: 13px; margin-bottom: 14px; }
  .flash.ok { background: var(--accent-positive-bg); color: var(--accent-positive); border: 1px solid var(--accent-positive); }
  .flash.err { background: var(--accent-negative-bg); color: var(--accent-negative); border: 1px solid var(--accent-negative); }
  .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; box-shadow: var(--shadow-card); margin-bottom: 16px; }
  .card-header { padding: 14px 18px; border-bottom: 1px solid var(--border-subtle); display:flex; align-items:center; gap:10px; }
  .card-title { font-size: 14px; font-weight: 600; color: var(--text-primary); }
  table { width: 100%; border-collapse: collapse; }
  thead th { text-align: left; font-size: 11.5px; font-weight: 500; color: var(--text-tertiary); padding: 10px 14px;
             text-transform: uppercase; letter-spacing: .5px; border-bottom: 1px solid var(--border-subtle); }
  tbody td { padding: 11px 14px; border-bottom: 1px solid var(--border-subtle); font-size: 13.5px; vertical-align: top; }
  tbody tr:last-child td { border-bottom: none; }
  .cell-main { font-weight: 500; color: var(--text-primary); }
  .cell-sub { font-size: 11.5px; color: var(--text-tertiary); margin-top: 2px; }
  .notas { color: var(--text-secondary); font-size: 12.5px; }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:500;
           background: var(--bg-soft); color: var(--text-secondary); border: 1px solid var(--border); }
  .badge.estado-confirmada { background: var(--accent-positive-bg); color: var(--accent-positive); border-color: transparent; }
  .badge.estado-cancelada { background: var(--accent-negative-bg); color: var(--accent-negative); border-color: transparent; }
  .badge.cover-pendiente { background:#FEF3C7; color:#92400E; }
  .badge.cover-anticipado { background:#DBEAFE; color:#1E40AF; }
  .badge.cover-en_entrada { background:#E0E7FF; color:#4338CA; }
  .btn-mini { padding: 5px 10px; border-radius: 7px; font: inherit; font-size: 12px; cursor: pointer;
              border: 1px solid var(--border); background: var(--bg-card); color: var(--text-primary); }
  .btn-mini.danger:hover { background: var(--accent-negative-bg); color: var(--accent-negative); border-color: var(--accent-negative); }
  .empty { padding: 28px; text-align: center; color: var(--text-tertiary); font-size: 13px; }
  .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; padding: 16px 18px; }
  .form-grid label { display: block; font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }
  .form-grid input, .form-grid select { width: 100%; padding: 8px 10px; border: 1px solid var(--border);
        border-radius: 8px; background: var(--bg-card); color: var(--text-primary); font: inherit; font-size: 13px; box-sizing: border-box; }
  .form-actions { padding: 0 18px 16px; }
  .btn-primary { padding: 9px 16px; background: var(--btn-primary-bg); color: var(--btn-primary-text);
                 border: none; border-radius: 8px; font: inherit; font-weight: 600; font-size: 13px; cursor: pointer; }
</style>
</head><body>
__ICON_SPRITE__
<div class="app">
  __SIDEBAR__
  <main class="main">
    <h1 class="page-title">Reservas</h1>
    <p class="page-subtitle">Ver y crear reservas del backend de mesas.</p>
    {{flash}}

    <div class="toolbar">
      <form method="GET" style="display:flex;align-items:center;gap:8px;">
        <label style="font-size:13px;color:var(--text-secondary);">Fecha:</label>
        <input type="date" name="fecha" value="{{fecha}}" onchange="this.form.submit()"/>
      </form>
      <span class="resumen-chip">{{resumen}}</span>
    </div>

    <div class="card">
      <div class="card-header"><div class="card-title">+ Nueva reserva</div></div>
      <form method="POST" action="/admin/reservas/crear">
        <div class="form-grid">
          <div><label>Fecha</label><input type="date" name="fecha" value="{{fecha}}" required/></div>
          <div><label>Mesa</label><select name="mesa_id" required>{{opts_mesa}}</select></div>
          <div><label>Cliente</label><input name="nombre_cliente" required placeholder="Nombre"/></div>
          <div><label>Teléfono</label><input name="telefono" placeholder="+57..."/></div>
          <div><label>Personas</label><input type="number" name="num_personas" min="1" value="2" required/></div>
          <div style="grid-column:1/-1;"><label>Notas</label><input name="notas" placeholder="Hora, pedido de botella, etc."/></div>
        </div>
        <div class="form-actions"><button type="submit" class="btn-primary">Crear reserva</button></div>
      </form>
    </div>

    <div class="card">
      <div class="card-header"><div class="card-title">Reservas del día ({{total}})</div></div>
      <table>
        <thead><tr><th>ID</th><th>Cliente</th><th>Mesa</th><th>Pers.</th><th>Estado</th><th>Notas</th><th></th></tr></thead>
        <tbody>{{filas}}</tbody>
      </table>
    </div>
  </main>
</div>
__THEME_JS__
</body></html>"""
