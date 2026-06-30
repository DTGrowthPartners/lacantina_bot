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
from app.eventos import clave_orden_evento, etiqueta_hora, extraer_eventos
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


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def vista(request: Request):
    if not _check_auth(request):
        raise HTTPException(401)

    error = ""
    eventos: list[dict] = []
    try:
        eventos = extraer_eventos(await cantina_api.listar_eventos())
        eventos.sort(key=clave_orden_evento)
    except Exception as e:
        error = str(e)[:200]
        log.warning("admin.eventos.listar_fail", error=error)

    hoy = _hoy()
    cards = []
    for e in eventos:
        fecha = str(e.get("fecha") or "")
        hora = etiqueta_hora(e)
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
        txt = _FLYERS / f"{fecha}.txt"
        desc_raw = ""
        if txt.exists():
            try:
                desc_raw = txt.read_text(encoding="utf-8")[:300]
            except Exception:
                desc_raw = ""
        desc = html.escape(desc_raw)
        cover_val = "" if cover is None else str(cover)
        fe = html.escape(fecha)
        data_attrs = (
            f'data-fecha="{fe}" '
            f'data-horainicio="{html.escape(str(e.get("hora_inicio") or ""))}" '
            f'data-horafin="{html.escape(str(e.get("hora_fin") or ""))}" '
            f'data-nombre="{html.escape(str(e.get("nombre") or ""))}" '
            f'data-artista="{html.escape(str(e.get("artista") or ""))}" '
            f'data-cover="{html.escape(cover_val)}" '
            f'data-link="{html.escape(str(link or ""))}" '
            f'data-tienecover="{"1" if tiene_cover else ""}" '
            f'data-desc="{html.escape(desc_raw)}"'
        )
        cards.append(f"""
        <div class="ev-card {'ev-pasada' if pasada else ''}" {data_attrs}>
          {flyer_html}
          <div class="ev-body">
            <div class="ev-fecha">📅 {fe}{f' · {html.escape(hora)}' if hora else ''}{' · (pasado)' if pasada else ''}</div>
            <div class="ev-nombre">{nombre}</div>
            {f'<div class="ev-artista">🎤 {artista}</div>' if artista else ''}
            <div class="ev-cover">{html.escape(cover_txt)}</div>
            {f'<div class="ev-desc-txt">{desc}</div>' if desc else ''}
            {link_html}
            <div class="ev-acts">
              <button type="button" class="ev-act" onclick="editarEvento(this)">✏️ Editar</button>
              <form method="POST" action="/admin/eventos/{fe}/flyer" enctype="multipart/form-data" style="margin:0;">
                <label class="ev-act" style="cursor:pointer;">📷 {'Cambiar' if flyer else 'Agregar'} flyer
                  <input type="file" name="flyer" accept="image/*" onchange="this.form.submit()" hidden/>
                </label>
              </form>
            </div>
          </div>
          <form method="POST" action="/admin/eventos/{fe}/borrar" style="margin:0;"
                onsubmit="return confirm('¿Borrar el evento del {fe}? (las reservas se conservan)');">
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
        flash = '<div class="flash ok">Evento guardado.</div>'
    elif msg == "flyer_ok":
        flash = '<div class="flash ok">Flyer actualizado.</div>'
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
    hora_inicio: str = Form(""),
    hora_fin: str = Form(""),
    valor_cover: str = Form(""),
    link_pago: str = Form(""),
    tiene_cover: str = Form(""),
    descripcion: str = Form(""),
):
    if not _check_auth(request):
        raise HTTPException(401)
    fecha = fecha.strip()
    cover_on = bool(tiene_cover) or bool(valor_cover.strip())
    payload: dict = {"fecha": fecha, "nombre": nombre.strip(), "tiene_cover": cover_on}
    if artista.strip():
        payload["artista"] = artista.strip()
    if hora_inicio.strip():
        payload["hora_inicio"] = hora_inicio.strip()
    if hora_fin.strip():
        payload["hora_fin"] = hora_fin.strip()
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
    # Descripción (sidecar local; el backend de eventos no la guarda).
    if descripcion.strip():
        _FLYERS.mkdir(parents=True, exist_ok=True)
        (_FLYERS / f"{fecha}.txt").write_text(descripcion.strip(), encoding="utf-8")
    log.info("admin.eventos.creado", fecha=fecha, nombre=nombre)
    return RedirectResponse("/admin/eventos?msg=creado", status_code=303)


@router.post("/{fecha}/flyer")
async def subir_flyer(fecha: str, request: Request):
    """Sube/reemplaza SOLO el flyer de un evento ya existente."""
    if not _check_auth(request):
        raise HTTPException(401)
    form = await request.form()
    archivo = form.get("flyer")
    if archivo is None or not getattr(archivo, "filename", ""):
        return RedirectResponse("/admin/eventos?msg=error:no adjuntaste imagen", status_code=303)
    ext = Path(archivo.filename).suffix.lower()
    if ext not in _EXT_OK:
        return RedirectResponse("/admin/eventos?msg=error:formato no soportado (usa jpg/png/webp)", status_code=303)
    data = await archivo.read()
    if not data:
        return RedirectResponse("/admin/eventos?msg=error:imagen vacía", status_code=303)
    _FLYERS.mkdir(parents=True, exist_ok=True)
    for e in _EXT_OK:
        old = _FLYERS / f"{fecha}{e}"
        if old.exists():
            old.unlink()
    (_FLYERS / f"{fecha}{ext}").write_bytes(data)
    log.info("admin.eventos.flyer_subido", fecha=fecha)
    return RedirectResponse("/admin/eventos?msg=flyer_ok", status_code=303)


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
    txt = _FLYERS / f"{fecha}.txt"
    if txt.exists():
        try:
            txt.unlink()
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
  .ev-desc-txt { font-size:12px; color:var(--text-tertiary); margin-top:6px; line-height:1.4; }
  .ev-link { display:inline-block; margin-top:6px; font-size:12.5px; color:var(--chip-blue); text-decoration:none; }
  .ev-acts { display:flex; gap:8px; margin-top:12px; flex-wrap:wrap; }
  .ev-act { display:inline-flex; align-items:center; gap:4px; padding:5px 10px; font-size:12px; border:1px solid var(--border); border-radius:8px; background:var(--bg-soft); color:var(--text-secondary); cursor:pointer; font:inherit; font-size:12px; }
  .ev-act:hover { border-color:var(--btn-primary-bg); color:var(--text-primary); }
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
      <div class="card-header"><div class="card-title" id="ev-form-title">+ Nuevo evento</div></div>
      <form method="POST" action="/admin/eventos/crear" enctype="multipart/form-data" id="ev-form">
        <div class="form-grid">
          <div><label>Fecha</label><input type="date" name="fecha" value="{{hoy}}" required/></div>
          <div><label>Hora inicio</label><input type="time" name="hora_inicio"/></div>
          <div><label>Hora fin</label><input type="time" name="hora_fin"/></div>
          <div><label>Nombre del evento</label><input name="nombre" required placeholder="Ej: Noche de Carin León"/></div>
          <div><label>Artista</label><input name="artista" placeholder="Cantante / show"/></div>
          <div><label>Valor cover (COP)</label><input name="valor_cover" inputmode="numeric" placeholder="Ej: 50000"/></div>
          <div style="grid-column:1/-1;"><label>Link de pago (pasarela, opcional)</label><input name="link_pago" placeholder="https://..."/></div>
          <div style="grid-column:1/-1;"><label>Descripción (opcional)</label><input name="descripcion" placeholder="Ej: Noche de música popular con Carin León en vivo. Cover incluye…"/></div>
          <div class="chk"><input type="checkbox" name="tiene_cover" id="tc" value="1"/><label for="tc" style="margin:0;">Tiene cover (entrada con costo)</label></div>
          <div><label>Flyer (imagen, opcional)</label><input type="file" name="flyer" accept="image/*"/></div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn-primary" id="ev-submit">Crear evento</button>
          <button type="button" class="ev-act" id="ev-cancel" style="display:none;margin-left:8px;" onclick="cancelarEdicion()">Cancelar edición</button>
        </div>
      </form>
    </div>

    <div class="card">
      <div class="card-header"><div class="card-title">Eventos registrados</div></div>
      <div style="padding:16px 18px;"><div class="ev-grid">{{lista}}</div></div>
    </div>
  </main>
</div>
__THEME_JS__
<script>
function editarEvento(btn){
  var c = btn.closest('.ev-card'); if(!c) return;
  var f = document.getElementById('ev-form');
  f.fecha.value        = c.dataset.fecha || '';
  f.hora_inicio.value  = c.dataset.horainicio || '';
  f.hora_fin.value     = c.dataset.horafin || '';
  f.nombre.value       = c.dataset.nombre || '';
  f.artista.value      = c.dataset.artista || '';
  f.valor_cover.value  = c.dataset.cover || '';
  f.link_pago.value    = c.dataset.link || '';
  f.descripcion.value  = c.dataset.desc || '';
  f.querySelector('[name=tiene_cover]').checked = (c.dataset.tienecover === '1');
  document.getElementById('ev-form-title').textContent = '✏️ Editar evento · ' + (c.dataset.fecha || '');
  document.getElementById('ev-submit').textContent = 'Guardar cambios';
  document.getElementById('ev-cancel').style.display = '';
  f.scrollIntoView({behavior:'smooth', block:'start'});
  try { f.nombre.focus(); } catch(e){}
}
function cancelarEdicion(){
  var f = document.getElementById('ev-form');
  f.reset();
  document.getElementById('ev-form-title').textContent = '+ Nuevo evento';
  document.getElementById('ev-submit').textContent = 'Crear evento';
  document.getElementById('ev-cancel').style.display = 'none';
}
</script>
</body></html>"""
