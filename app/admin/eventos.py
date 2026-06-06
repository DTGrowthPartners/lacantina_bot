"""/admin/eventos — Eventos del venue (contra el backend de mesas) + flyers.

Lista, crea y borra eventos (cantina_api). Cada evento puede tener un flyer
(imagen) que se guarda localmente en `data/media/flyers/<fecha>.<ext>`; el bot
lo envía cuando un cliente pregunta por el evento de esa fecha.

Pagos: los eventos pueden tener un link de pago (pasarela). El bot NO está
conectado a la pasarela — solo informa el link y verifica el comprobante que el
cliente envíe.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.admin._shell import ICON_SPRITE, SHELL_STYLES, THEME_TOGGLE_JS, sidebar_html
from app.config import get_settings
from app.integrations import cantina_api
from app.logging_setup import log

router = APIRouter(prefix="/admin/eventos", tags=["admin-eventos"])

settings = get_settings()
_FLYERS = Path(settings.data_dir) / "media" / "flyers"
_EXT_OK = {".jpg", ".jpeg", ".png", ".webp"}
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _check_auth(request: Request) -> bool:
    return "admin_token" in request.session


def _hoy() -> str:
    return datetime.now(ZoneInfo("America/Bogota")).date().isoformat()


def flyer_path(fecha: str) -> Path | None:
    """Ruta del flyer de una fecha si existe (cualquier extensión soportada)."""
    for ext in _EXT_OK:
        p = _FLYERS / f"{fecha}{ext}"
        if p.exists():
            return p
    return None


def _eventos_de(resp: dict) -> list[dict]:
    if not isinstance(resp, dict) or not resp.get("ok", True):
        return []
    data = resp.get("data", resp)
    if isinstance(data, dict):
        evs = data.get("eventos")
        if isinstance(evs, list):
            return [e for e in evs if isinstance(e, dict)]
        # respuesta de un solo evento
        if data.get("nombre") or data.get("fecha"):
            return [data]
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def vista(request: Request):
    if not _check_auth(request):
        raise HTTPException(401)

    error = ""
    eventos: list[dict] = []
    try:
        eventos = _eventos_de(await cantina_api.listar_eventos())
        eventos.sort(key=lambda e: str(e.get("fecha") or ""))
    except Exception as e:
        error = str(e)[:200]
        log.warning("admin.eventos.listar_fail", error=error)

    hoy = _hoy()
    cards = []
    for e in eventos:
        fecha = str(e.get("fecha") or "")
        nombre = html.escape(str(e.get("nombre") or "Evento"))
        artista = html.escape(str(e.get("artista") or ""))
        cover = e.get("valor_cover")
        tiene_cover = e.get("tiene_cover")
        link = e.get("link_pago")
        pasada = fecha and fecha < hoy
        flyer = flyer_path(fecha)
        flyer_html = (f'<img class="ev-flyer" src="/admin/eventos/flyer/{html.escape(fecha)}" loading="lazy"/>'
                      if flyer else '<div class="ev-flyer ev-noflyer">sin flyer</div>')
        cover_txt = (f"Cover ${cover:,}".replace(",", ".") if (tiene_cover and cover) else
                     ("Con cover" if tiene_cover else "Entrada libre"))
        link_html = (f'<a href="{html.escape(str(link))}" target="_blank" class="ev-link">🔗 link de pago</a>'
                     if link else "")
        cards.append(f"""
        <div class="ev-card {'ev-pasada' if pasada else ''}">
          {flyer_html}
          <div class="ev-body">
            <div class="ev-fecha">📅 {html.escape(fecha)}{' · (pasado)' if pasada else ''}</div>
            <div class="ev-nombre">{nombre}</div>
            {f'<div class="ev-artista">🎤 {artista}</div>' if artista else ''}
            <div class="ev-cover">{html.escape(cover_txt)}</div>
            {link_html}
          </div>
          <form method="POST" action="/admin/eventos/{html.escape(fecha)}/borrar" style="margin:0;"
                onsubmit="return confirm('¿Borrar el evento del {html.escape(fecha)}? (las reservas se conservan)');">
            <button class="ev-del" title="Borrar evento">×</button>
          </form>
        </div>""")
    lista = "".join(cards)
    if error:
        lista = f'<div class="empty">No se pudo leer eventos: {html.escape(error)}</div>'
    elif not cards:
        lista = '<div class="empty">No hay eventos registrados. Crea el primero arriba.</div>'

    flash = ""
    msg = request.query_params.get("msg")
    if msg == "creado":
        flash = '<div class="flash ok">Evento creado.</div>'
    elif msg == "borrado":
        flash = '<div class="flash ok">Evento borrado.</div>'
    elif msg and msg.startswith("error:"):
        flash = f'<div class="flash err">{html.escape(msg[6:][:200])}</div>'

    body = (
        _TEMPLATE
        .replace("__SHELL_STYLES__", SHELL_STYLES)
        .replace("__ICON_SPRITE__", ICON_SPRITE)
        .replace("__SIDEBAR__", sidebar_html(active="eventos"))
        .replace("__THEME_JS__", THEME_TOGGLE_JS)
        .replace("{{hoy}}", hoy)
        .replace("{{lista}}", lista)
        .replace("{{flash}}", flash)
    )
    return HTMLResponse(body)


@router.get("/flyer/{fecha}")
async def servir_flyer(fecha: str, request: Request):
    if not _check_auth(request):
        raise HTTPException(401)
    p = flyer_path(fecha)
    if not p:
        raise HTTPException(404)
    return FileResponse(str(p), media_type=_MIME.get(p.suffix.lower(), "image/jpeg"))


@router.post("/crear")
async def crear(
    request: Request,
    fecha: str = Form(...),
    nombre: str = Form(...),
    artista: str = Form(""),
    valor_cover: str = Form(""),
    link_pago: str = Form(""),
    tiene_cover: str = Form(""),
):
    if not _check_auth(request):
        raise HTTPException(401)
    fecha = fecha.strip()
    cover_on = bool(tiene_cover) or bool(valor_cover.strip())
    payload: dict = {"fecha": fecha, "nombre": nombre.strip(), "tiene_cover": cover_on}
    if artista.strip():
        payload["artista"] = artista.strip()
    if valor_cover.strip():
        try:
            payload["valor_cover"] = int(re_int(valor_cover))
        except Exception:
            pass
    if link_pago.strip():
        payload["link_pago"] = link_pago.strip()

    res = await cantina_api.crear_evento(payload)
    if not (isinstance(res, dict) and res.get("ok")):
        err = (res or {}).get("error", "no se pudo crear")
        return RedirectResponse(f"/admin/eventos?msg=error:{err}", status_code=303)

    # Flyer (opcional)
    form = await request.form()
    archivo = form.get("flyer")
    if archivo is not None and getattr(archivo, "filename", ""):
        ext = Path(archivo.filename).suffix.lower()
        if ext in _EXT_OK:
            data = await archivo.read()
            if data:
                _FLYERS.mkdir(parents=True, exist_ok=True)
                # un flyer por fecha: borra otros con la misma fecha
                for e in _EXT_OK:
                    old = _FLYERS / f"{fecha}{e}"
                    if old.exists():
                        old.unlink()
                (_FLYERS / f"{fecha}{ext}").write_bytes(data)
    log.info("admin.eventos.creado", fecha=fecha, nombre=nombre)
    return RedirectResponse("/admin/eventos?msg=creado", status_code=303)


@router.post("/{fecha}/borrar")
async def borrar(fecha: str, request: Request):
    if not _check_auth(request):
        raise HTTPException(401)
    res = await cantina_api.borrar_evento(fecha)
    p = flyer_path(fecha)
    if p:
        try:
            p.unlink()
        except Exception:
            pass
    if isinstance(res, dict) and res.get("ok"):
        return RedirectResponse("/admin/eventos?msg=borrado", status_code=303)
    err = (res or {}).get("error", "no se pudo borrar")
    return RedirectResponse(f"/admin/eventos?msg=error:{err}", status_code=303)


def re_int(s: str) -> str:
    import re
    return re.sub(r"[^\d]", "", s or "") or "0"


_TEMPLATE = """<!doctype html>
<html lang="es" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Eventos — La Cantina</title>
__SHELL_STYLES__
<style>
  .page-title { font-size: 22px; font-weight: 600; margin: 0 0 4px; color: var(--text-primary); }
  .page-subtitle { color: var(--text-secondary); font-size: 13px; margin-bottom: 16px; }
  .flash { padding: 8px 12px; border-radius: 8px; font-size: 13px; margin-bottom: 14px; }
  .flash.ok { background: var(--accent-positive-bg); color: var(--accent-positive); border: 1px solid var(--accent-positive); }
  .flash.err { background: var(--accent-negative-bg); color: var(--accent-negative); border: 1px solid var(--accent-negative); }
  .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; box-shadow: var(--shadow-card); margin-bottom: 16px; }
  .card-header { padding: 14px 18px; border-bottom: 1px solid var(--border-subtle); }
  .card-title { font-size: 14px; font-weight: 600; color: var(--text-primary); }
  .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap: 12px; padding: 16px 18px; }
  .form-grid label { display:block; font-size:12px; color:var(--text-secondary); margin-bottom:4px; }
  .form-grid input { width:100%; padding:8px 10px; border:1px solid var(--border); border-radius:8px; background:var(--bg-card); color:var(--text-primary); font:inherit; font-size:13px; box-sizing:border-box; }
  .chk { display:flex; align-items:center; gap:8px; font-size:13px; color:var(--text-secondary); }
  .chk input { width:auto; }
  .form-actions { padding: 0 18px 16px; }
  .btn-primary { padding:9px 16px; background:var(--btn-primary-bg); color:var(--btn-primary-text); border:none; border-radius:8px; font:inherit; font-weight:600; font-size:13px; cursor:pointer; }
  .ev-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(240px,1fr)); gap:14px; }
  .ev-card { border:1px solid var(--border); border-radius:12px; overflow:hidden; background:var(--bg-card); position:relative; }
  .ev-card.ev-pasada { opacity:.6; }
  .ev-flyer { width:100%; height:160px; object-fit:cover; display:block; background:var(--bg-soft); }
  .ev-noflyer { display:grid; place-items:center; color:var(--text-tertiary); font-size:12px; }
  .ev-body { padding:12px 14px; }
  .ev-fecha { font-size:12px; color:var(--text-tertiary); }
  .ev-nombre { font-weight:600; font-size:15px; color:var(--text-primary); margin:2px 0; }
  .ev-artista { font-size:13px; color:var(--text-secondary); }
  .ev-cover { font-size:12.5px; color:var(--text-secondary); margin-top:4px; }
  .ev-link { display:inline-block; margin-top:6px; font-size:12.5px; color:var(--chip-blue); text-decoration:none; }
  .ev-del { position:absolute; top:8px; right:8px; width:28px; height:28px; border-radius:8px; border:none; cursor:pointer; font-size:18px; line-height:1; background:rgba(0,0,0,.45); color:#fff; }
  .empty { padding:28px; text-align:center; color:var(--text-tertiary); font-size:13px; grid-column:1/-1; border:1px dashed var(--border); border-radius:12px; }
</style>
</head><body>
__ICON_SPRITE__
<div class="app">
  __SIDEBAR__
  <main class="main">
    <h1 class="page-title">Eventos</h1>
    <p class="page-subtitle">Agrega los eventos con su fecha y flyer. El bot informa del evento y, si lo pides, envía el flyer. Los pagos van por el link (pasarela); el bot solo verifica el comprobante.</p>
    {{flash}}

    <div class="card">
      <div class="card-header"><div class="card-title">+ Nuevo evento</div></div>
      <form method="POST" action="/admin/eventos/crear" enctype="multipart/form-data">
        <div class="form-grid">
          <div><label>Fecha</label><input type="date" name="fecha" value="{{hoy}}" required/></div>
          <div><label>Nombre del evento</label><input name="nombre" required placeholder="Ej: Noche de Carin León"/></div>
          <div><label>Artista</label><input name="artista" placeholder="Cantante / show"/></div>
          <div><label>Valor cover (COP)</label><input name="valor_cover" inputmode="numeric" placeholder="Ej: 50000"/></div>
          <div style="grid-column:1/-1;"><label>Link de pago (pasarela, opcional)</label><input name="link_pago" placeholder="https://..."/></div>
          <div class="chk"><input type="checkbox" name="tiene_cover" id="tc" value="1"/><label for="tc" style="margin:0;">Tiene cover (entrada con costo)</label></div>
          <div><label>Flyer (imagen, opcional)</label><input type="file" name="flyer" accept="image/*"/></div>
        </div>
        <div class="form-actions"><button type="submit" class="btn-primary">Crear evento</button></div>
      </form>
    </div>

    <div class="card">
      <div class="card-header"><div class="card-title">Eventos registrados</div></div>
      <div style="padding:16px 18px;"><div class="ev-grid">{{lista}}</div></div>
    </div>
  </main>
</div>
__THEME_JS__
</body></html>"""
