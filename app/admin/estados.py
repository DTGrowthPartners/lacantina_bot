"""/admin/estados — Estados de WhatsApp (banco de imágenes + texto).

Sube imágenes a un banco local y decide cuándo publicarlas como estado de
WhatsApp (duran 24 h). También permite publicar un estado de solo texto.
Usado para promocionar eventos de La Cantina.

Las imágenes se guardan en `data/estados_banco/` (no se versiona).
"""

from __future__ import annotations

import html
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from app.admin._shell import ICON_SPRITE, SHELL_STYLES, THEME_TOGGLE_JS, sidebar_html
from app.config import get_settings
from app.logging_setup import log
from app.whapi.client import publicar_story_imagen_bytes, publicar_story_texto

router = APIRouter(prefix="/admin/estados", tags=["admin-estados"])

settings = get_settings()
_BANK = Path(settings.data_dir) / "estados_banco"
_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_EXT_OK = {".jpg", ".jpeg", ".png", ".webp"}
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _check_auth(request: Request) -> bool:
    return "admin_token" in request.session


def _safe_name(name: str) -> str:
    return _SAFE.sub("_", (name or "").strip())[:120]


def _bank_files() -> list[Path]:
    if not _BANK.exists():
        return []
    return sorted(
        [p for p in _BANK.iterdir() if p.is_file() and p.suffix.lower() in _EXT_OK],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def vista(request: Request):
    if not _check_auth(request):
        raise HTTPException(401)

    cards = []
    for p in _bank_files():
        n = html.escape(p.name)
        kb = max(1, p.stat().st_size // 1024)
        cards.append(f"""
        <div class="img-card">
          <img src="/admin/estados/img/{n}" alt="{n}" loading="lazy"/>
          <div class="img-meta">{n[:28]} · {kb} KB</div>
          <div class="img-actions">
            <button class="g-btn primary" data-action="publicar" data-name="{n}">Publicar</button>
            <form method="POST" action="/admin/estados/eliminar/{n}" style="margin:0;"
                  onsubmit="return confirm('¿Borrar esta imagen del banco?');">
              <button class="g-btn danger">Borrar</button>
            </form>
          </div>
        </div>""")
    banco = "".join(cards) or '<div class="empty">El banco está vacío. Sube imágenes para tenerlas listas.</div>'

    flash = ""
    msg = request.query_params.get("msg")
    if msg == "publicado":
        flash = '<div class="flash ok">Estado publicado. Dura 24 h en WhatsApp.</div>'
    elif msg == "borrado":
        flash = '<div class="flash ok">Imagen borrada del banco.</div>'
    elif msg and msg.startswith("error:"):
        flash = f'<div class="flash err">{html.escape(msg[6:][:200])}</div>'

    body = (
        _TEMPLATE
        .replace("__SHELL_STYLES__", SHELL_STYLES)
        .replace("__ICON_SPRITE__", ICON_SPRITE)
        .replace("__SIDEBAR__", sidebar_html(active="estados"))
        .replace("__THEME_JS__", THEME_TOGGLE_JS)
        .replace("{{banco}}", banco)
        .replace("{{n}}", str(len(cards)))
        .replace("{{flash}}", flash)
    )
    return HTMLResponse(body)


@router.get("/img/{filename}")
async def servir_imagen(filename: str, request: Request):
    if not _check_auth(request):
        raise HTTPException(401)
    p = _BANK / _safe_name(filename)
    if not p.exists() or p.suffix.lower() not in _EXT_OK:
        raise HTTPException(404)
    return FileResponse(str(p), media_type=_MIME.get(p.suffix.lower(), "image/jpeg"))


@router.post("/upload")
async def upload(request: Request):
    if not _check_auth(request):
        raise HTTPException(401)
    _BANK.mkdir(parents=True, exist_ok=True)
    form = await request.form()
    archivos = [v for v in form.getlist("files") if hasattr(v, "filename") and getattr(v, "filename", "")]
    guardados = 0
    for a in archivos:
        ext = Path(a.filename).suffix.lower()
        if ext not in _EXT_OK:
            continue
        data = await a.read()
        if not data:
            continue
        nombre = f"{int(time.time())}_{_safe_name(Path(a.filename).stem)}{ext}"
        (_BANK / nombre).write_bytes(data)
        guardados += 1
    log.info("admin.estados.upload", guardados=guardados)
    return JSONResponse({"ok": True, "guardados": guardados})


@router.post("/publicar/{filename}")
async def publicar_imagen(filename: str, request: Request):
    if not _check_auth(request):
        raise HTTPException(401)
    p = _BANK / _safe_name(filename)
    if not p.exists():
        return JSONResponse({"ok": False, "error": "imagen no encontrada"}, status_code=404)
    form = await request.form()
    caption = (form.get("caption") or "").strip() if isinstance(form.get("caption"), str) else ""
    try:
        await publicar_story_imagen_bytes(
            p.read_bytes(), caption=caption or None,
            filename=p.name, mime=_MIME.get(p.suffix.lower(), "image/jpeg"),
        )
    except Exception as e:
        log.exception("admin.estados.publicar_fail", filename=filename, error=str(e))
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=502)
    log.warning("admin.estados.publicado", filename=filename)
    return {"ok": True}


@router.post("/eliminar/{filename}")
async def eliminar(filename: str, request: Request):
    if not _check_auth(request):
        raise HTTPException(401)
    p = _BANK / _safe_name(filename)
    try:
        if p.exists():
            p.unlink()
    except Exception as e:
        log.warning("admin.estados.eliminar_fail", error=str(e))
    return RedirectResponse("/admin/estados?msg=borrado", status_code=303)


@router.post("/texto")
async def publicar_texto(request: Request):
    if not _check_auth(request):
        raise HTTPException(401)
    form = await request.form()
    caption = (form.get("caption") or "").strip() if isinstance(form.get("caption"), str) else ""
    if not caption:
        return RedirectResponse("/admin/estados?msg=error:texto vacío", status_code=303)
    try:
        await publicar_story_texto(caption)
    except Exception as e:
        log.exception("admin.estados.texto_fail", error=str(e))
        return RedirectResponse(f"/admin/estados?msg=error:{str(e)[:120]}", status_code=303)
    return RedirectResponse("/admin/estados?msg=publicado", status_code=303)


_TEMPLATE = """<!doctype html>
<html lang="es" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Estados WA — La Cantina</title>
__SHELL_STYLES__
<style>
  .page-title { font-size: 22px; font-weight: 600; margin: 0 0 4px; color: var(--text-primary); }
  .page-subtitle { color: var(--text-secondary); font-size: 13px; margin-bottom: 16px; }
  .flash { padding: 8px 12px; border-radius: 8px; font-size: 13px; margin-bottom: 14px; }
  .flash.ok { background: var(--accent-positive-bg); color: var(--accent-positive); border: 1px solid var(--accent-positive); }
  .flash.err { background: var(--accent-negative-bg); color: var(--accent-negative); border: 1px solid var(--accent-negative); }
  .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; box-shadow: var(--shadow-card); margin-bottom: 16px; }
  .card-header { padding: 14px 18px; border-bottom: 1px solid var(--border-subtle); display: flex; align-items: center; gap: 10px; }
  .card-title { font-size: 14px; font-weight: 600; color: var(--text-primary); }
  .card-body { padding: 16px 18px; }
  .btn-primary { padding: 9px 16px; background: var(--btn-primary-bg); color: var(--btn-primary-text); border: none;
                 border-radius: 8px; font: inherit; font-weight: 600; font-size: 13px; cursor: pointer; }
  .upload-label { display: inline-flex; align-items: center; gap: 8px; padding: 9px 16px; border-radius: 8px;
                  border: 1px solid var(--border); background: var(--bg-card); color: var(--text-primary);
                  font-size: 13px; font-weight: 500; cursor: pointer; }
  .img-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px,1fr)); gap: 14px; }
  .img-card { border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: var(--bg-card); }
  .img-card img { width: 100%; height: 200px; object-fit: cover; display: block; background: var(--bg-soft); }
  .img-meta { padding: 8px 10px; font-size: 11px; color: var(--text-tertiary); }
  .img-actions { display: flex; gap: 6px; padding: 0 10px 10px; }
  .g-btn { padding: 6px 10px; border-radius: 7px; font: inherit; font-size: 12px; cursor: pointer;
           border: 1px solid var(--border); background: var(--bg-card); color: var(--text-primary); flex: 1; }
  .g-btn.primary { background: var(--btn-primary-bg); color: var(--btn-primary-text); border-color: var(--btn-primary-bg); }
  .g-btn.danger:hover { background: var(--accent-negative-bg); color: var(--accent-negative); border-color: var(--accent-negative); }
  .empty { padding: 28px; text-align: center; color: var(--text-tertiary); font-size: 13px; grid-column: 1/-1; }
  textarea { width: 100%; min-height: 90px; box-sizing: border-box; border: 1px solid var(--border); border-radius: 10px;
             padding: 10px 12px; font: inherit; font-size: 14px; background: var(--bg-card); color: var(--text-primary); }
  /* Modal publicar imagen */
  .pub-modal { position: fixed; inset: 0; z-index: 10000; background: rgba(0,0,0,.55); display: none; align-items: center; justify-content: center; padding: 20px; }
  .pub-modal.open { display: flex; }
  .pub-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px; width: 100%; max-width: 460px; padding: 22px; }
  .pub-card h3 { margin: 0 0 10px; color: var(--text-primary); font-size: 16px; }
  .pub-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 14px; }
  .pub-actions button { border: none; padding: 9px 16px; border-radius: 10px; font-size: 13px; font-weight: 600; cursor: pointer; }
  .btn-cancelar { background: var(--bg-card); color: var(--text-secondary); border: 1px solid var(--border) !important; }
  .toast-stack { position: fixed; bottom: 16px; right: 16px; display: flex; flex-direction: column; gap: 8px; z-index: 9999; }
  .toast { padding: 10px 16px; border-radius: 10px; font-size: 13px; color: #fff; background: var(--accent-positive); min-width: 180px; }
  .toast.error { background: var(--accent-negative); }
</style>
</head><body>
__ICON_SPRITE__
<div class="app">
  __SIDEBAR__
  <main class="main">
    <h1 class="page-title">Estados de WhatsApp</h1>
    <p class="page-subtitle">Sube imágenes al banco y publícalas como estado (duran 24 h). Ideal para promocionar eventos.</p>
    {{flash}}

    <div class="card">
      <div class="card-header">
        <div class="card-title">Banco de imágenes ({{n}})</div>
        <label class="upload-label" style="margin-left:auto;">⬆️ Subir imágenes
          <input type="file" id="uploader" accept="image/*" multiple hidden/>
        </label>
      </div>
      <div class="card-body"><div class="img-grid">{{banco}}</div></div>
    </div>

    <div class="card">
      <div class="card-header"><div class="card-title">Estado de solo texto</div></div>
      <div class="card-body">
        <form method="POST" action="/admin/estados/texto">
          <textarea name="caption" placeholder="Ej: 🎶 Hoy en vivo… ¡reserva tu mesa!"></textarea>
          <div style="margin-top:10px;"><button type="submit" class="btn-primary">Publicar texto al estado</button></div>
        </form>
      </div>
    </div>
  </main>
</div>

<div class="pub-modal" id="pub-modal">
  <div class="pub-card">
    <h3>Publicar como estado</h3>
    <div id="pub-target" style="font-size:12px;color:var(--text-secondary);margin-bottom:10px;"></div>
    <textarea id="pub-caption" placeholder="Texto opcional (pie de la imagen)…"></textarea>
    <div class="pub-actions">
      <button type="button" class="btn-cancelar" id="pub-cancelar">Cancelar</button>
      <button type="button" class="btn-primary" id="pub-enviar">Publicar</button>
    </div>
  </div>
</div>
<div class="toast-stack" id="toast-stack"></div>

__THEME_JS__
<script>
(function(){
  var stack = document.getElementById('toast-stack');
  function toast(m, err){ var e=document.createElement('div'); e.className='toast'+(err?' error':''); e.textContent=m;
    stack.appendChild(e); setTimeout(function(){e.style.opacity='0';},2400); setTimeout(function(){try{stack.removeChild(e);}catch(x){}},2800); }

  // Upload
  var up = document.getElementById('uploader');
  up.addEventListener('change', async function(){
    if(!up.files.length) return;
    var fd = new FormData();
    Array.from(up.files).forEach(function(f){ fd.append('files', f, f.name); });
    toast('Subiendo…');
    try {
      var r = await fetch('/admin/estados/upload', { method:'POST', body:fd, headers:{'Accept':'application/json'} });
      var d = await r.json();
      if(r.ok && d.ok){ toast(d.guardados + ' imagen(es) subida(s)'); setTimeout(function(){ location.reload(); }, 700); }
      else toast('Error al subir', true);
    } catch(e){ toast('Error de red', true); }
  });

  // Publicar imagen (modal)
  var modal=document.getElementById('pub-modal'), tgt=document.getElementById('pub-target'),
      cap=document.getElementById('pub-caption'), btn=document.getElementById('pub-enviar');
  var current=null;
  document.querySelectorAll('.g-btn[data-action="publicar"]').forEach(function(b){
    b.addEventListener('click', function(){ current=b.dataset.name; tgt.textContent=current; cap.value='';
      modal.classList.add('open'); });
  });
  document.getElementById('pub-cancelar').addEventListener('click', function(){ modal.classList.remove('open'); });
  modal.addEventListener('click', function(e){ if(e.target===modal) modal.classList.remove('open'); });
  btn.addEventListener('click', async function(){
    if(!current) return; btn.disabled=true;
    try {
      var fd = new FormData(); if(cap.value.trim()) fd.append('caption', cap.value.trim());
      var r = await fetch('/admin/estados/publicar/'+encodeURIComponent(current), { method:'POST', body:fd, headers:{'Accept':'application/json'} });
      var d = await r.json();
      if(r.ok && d.ok){ toast('Estado publicado 🎉'); modal.classList.remove('open'); }
      else toast('Error: '+(d.error||r.status), true);
    } catch(e){ toast('Error de red', true); }
    btn.disabled=false;
  });
})();
</script>
</body></html>"""
