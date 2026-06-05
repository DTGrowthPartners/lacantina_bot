"""/admin/grupos — Grupos de WhatsApp del bot (en vivo desde whapi, sin DB).

- Lista los grupos donde está el canal del bot (GET /groups de whapi).
- Permite escribir manualmente a un grupo (texto + imagen opcional).

No usa tabla local: cada carga consulta whapi directamente. Útil para que el
equipo mande un mensaje puntual a un grupo desde el panel.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.admin._shell import ICON_SPRITE, SHELL_STYLES, THEME_TOGGLE_JS, sidebar_html
from app.logging_setup import log
from app.whapi.client import enviar_imagen_bytes, enviar_texto, listar_grupos

router = APIRouter(prefix="/admin/grupos", tags=["admin-grupos"])


def _check_auth(request: Request) -> bool:
    return "admin_token" in request.session


def _nombre(g: dict) -> str:
    return g.get("name") or g.get("subject") or "(sin nombre)"


def _participantes(g: dict) -> int:
    p = g.get("participants")
    if isinstance(p, list):
        return len(p)
    if isinstance(p, int):
        return p
    return 0


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def vista_grupos(request: Request):
    if not _check_auth(request):
        raise HTTPException(401)

    error = ""
    grupos: list[dict] = []
    try:
        grupos = await listar_grupos(count=100)
        # Orden: con participantes primero, luego por nombre
        grupos.sort(key=lambda g: (-_participantes(g), _nombre(g).lower()))
    except Exception as e:
        error = str(e)[:200]
        log.warning("admin.grupos.listar_fail", error=error)

    items: list[str] = []
    for g in grupos:
        gid = html.escape(str(g.get("id") or ""))
        nombre = html.escape(_nombre(g))
        n = _participantes(g)
        items.append(f"""
        <div class="grupo-card">
          <div class="g-info">
            <div class="g-name">{nombre}</div>
            <div class="g-sub">{n} participantes · {gid}</div>
          </div>
          <button type="button" class="g-btn primary" data-action="send"
                  data-gid="{gid}" data-name="{nombre}">Enviar</button>
        </div>""")

    cuerpo_lista = "".join(items)
    if error:
        cuerpo_lista = f'<div class="empty">No se pudo leer los grupos de whapi: {html.escape(error)}</div>'
    elif not items:
        cuerpo_lista = '<div class="empty">No hay grupos. El bot debe estar dentro del grupo para que aparezca aquí.</div>'

    body = (
        _TEMPLATE
        .replace("__SHELL_STYLES__", SHELL_STYLES)
        .replace("__ICON_SPRITE__", ICON_SPRITE)
        .replace("__SIDEBAR__", sidebar_html(active="grupos"))
        .replace("__THEME_JS__", THEME_TOGGLE_JS)
        .replace("{{items}}", cuerpo_lista)
        .replace("{{total}}", str(len(items)))
    )
    return HTMLResponse(body)


@router.post("/send")
async def enviar(request: Request):
    """Envía texto (+ imágenes opcionales) a un grupo. Form: group_id, mensaje, files[]."""
    if not _check_auth(request):
        raise HTTPException(401)
    form = await request.form()
    gid = (form.get("group_id") or "").strip()
    texto = (form.get("mensaje") or "").strip() if isinstance(form.get("mensaje"), str) else ""
    if not gid:
        return JSONResponse({"ok": False, "error": "falta group_id"}, status_code=400)

    archivos = [
        v for v in form.getlist("files")
        if hasattr(v, "filename") and getattr(v, "filename", "")
    ]
    if not texto and not archivos:
        return JSONResponse({"ok": False, "error": "mensaje vacío"}, status_code=400)

    autor = request.session.get("admin_user", "admin")
    enviados = 0
    errores: list[str] = []
    try:
        for idx, archivo in enumerate(archivos):
            data = await archivo.read()
            if not data:
                continue
            mime = (archivo.content_type or "image/jpeg").lower()
            if not mime.startswith("image/"):
                errores.append(f"{archivo.filename}: solo imágenes por ahora")
                continue
            caption = texto if (idx == 0 and texto) else None
            await enviar_imagen_bytes(gid, data, mime=mime, caption=caption,
                                      filename=archivo.filename or "image.jpg")
            enviados += 1
        # Texto suelto solo si no fue caption de una imagen
        if texto and not archivos:
            await enviar_texto(gid, texto)
            enviados += 1
    except Exception as e:
        log.exception("admin.grupos.send_fail", gid=gid, error=str(e))
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=502)

    log.warning("admin.grupos.enviado", gid=gid, autor=autor, enviados=enviados, errores=len(errores))
    return {"ok": True, "enviados": enviados, "errores": errores}


_TEMPLATE = """<!doctype html>
<html lang="es" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grupos WhatsApp — La Cantina</title>
__SHELL_STYLES__
<style>
  .page-title { font-size: 22px; font-weight: 600; margin: 0 0 4px; color: var(--text-primary); }
  .page-subtitle { color: var(--text-secondary); font-size: 13px; margin-bottom: 18px; }
  .grupos-list { display: flex; flex-direction: column; gap: 10px; }
  .grupo-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px;
                padding: 14px 16px; display: flex; gap: 14px; align-items: center;
                box-shadow: var(--shadow-card); }
  .g-info { flex: 1; min-width: 0; }
  .g-name { font-weight: 600; font-size: 14.5px; color: var(--text-primary); }
  .g-sub { font-size: 12px; color: var(--text-tertiary); margin-top: 3px; word-break: break-all; }
  .g-btn { padding: 7px 14px; border-radius: 8px; font: inherit; font-size: 13px; font-weight: 500;
           border: 1px solid var(--border); background: var(--bg-card); color: var(--text-primary); cursor: pointer; }
  .g-btn.primary { background: var(--btn-primary-bg); color: var(--btn-primary-text); border-color: var(--btn-primary-bg); }
  .empty { padding: 32px; text-align: center; color: var(--text-tertiary); font-size: 13px;
           border: 1px dashed var(--border); border-radius: 12px; background: var(--bg-card); }
  /* Modal */
  .send-modal { position: fixed; inset: 0; z-index: 10000; background: rgba(0,0,0,.55);
                display: none; align-items: center; justify-content: center; padding: 20px; }
  .send-modal.open { display: flex; }
  .send-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px;
               width: 100%; max-width: 520px; padding: 22px; box-shadow: 0 20px 60px rgba(0,0,0,.4); }
  .send-card h3 { margin: 0 0 4px; color: var(--text-primary); font-size: 16px; }
  .send-target { font-size: 12px; color: var(--text-secondary); margin-bottom: 14px; word-break: break-all; }
  .send-card textarea { width: 100%; min-height: 110px; resize: vertical; box-sizing: border-box;
                        border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px;
                        font: inherit; font-size: 14px; background: var(--bg-card); color: var(--text-primary); }
  .file-row { margin-top: 10px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .file-row label { padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border);
                    cursor: pointer; font-size: 12px; color: var(--text-secondary); }
  .file-list { font-size: 12px; color: var(--text-secondary); }
  .send-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 14px; }
  .send-actions button { border: none; padding: 9px 16px; border-radius: 10px; font-size: 13px; font-weight: 600; cursor: pointer; }
  .btn-cancelar { background: var(--bg-card); color: var(--text-secondary); border: 1px solid var(--border) !important; }
  .btn-enviar { background: var(--btn-primary-bg); color: var(--btn-primary-text); }
  .btn-enviar:disabled { opacity: .6; cursor: wait; }
  .send-state { font-size: 12px; color: var(--text-tertiary); margin-top: 8px; }
  .send-state.ok { color: var(--accent-positive); }
  .send-state.err { color: var(--accent-negative); }
  .toast-stack { position: fixed; bottom: 16px; right: 16px; display: flex; flex-direction: column; gap: 8px; z-index: 9999; }
  .toast { padding: 10px 16px; border-radius: 10px; font-size: 13px; color: #fff; background: var(--accent-positive);
           box-shadow: 0 4px 12px rgba(0,0,0,.15); min-width: 180px; }
  .toast.error { background: var(--accent-negative); }
</style>
</head><body>
__ICON_SPRITE__
<div class="app">
  __SIDEBAR__
  <main class="main">
    <h1 class="page-title">Grupos de WhatsApp</h1>
    <p class="page-subtitle">{{total}} grupos donde está el bot. Toca "Enviar" para escribirle a un grupo manualmente.</p>
    <div class="grupos-list">{{items}}</div>
  </main>
</div>

<div class="send-modal" id="send-modal">
  <div class="send-card">
    <h3>Enviar al grupo</h3>
    <div class="send-target" id="send-target">—</div>
    <textarea id="send-text" placeholder="Escribe el mensaje… (puedes adjuntar una imagen; el texto va como pie de foto)"></textarea>
    <div class="file-row">
      <label for="send-files">📎 Adjuntar imagen</label>
      <input type="file" id="send-files" accept="image/*" multiple hidden/>
      <span class="file-list" id="file-list"></span>
    </div>
    <div class="send-actions">
      <button type="button" class="btn-cancelar" id="send-cancelar">Cancelar</button>
      <button type="button" class="btn-enviar" id="send-enviar">Enviar</button>
    </div>
    <div class="send-state" id="send-state"></div>
  </div>
</div>
<div class="toast-stack" id="toast-stack"></div>

__THEME_JS__
<script>
(function(){
  var stack = document.getElementById('toast-stack');
  function toast(msg, err){
    var el = document.createElement('div'); el.className = 'toast' + (err ? ' error' : '');
    el.textContent = msg; stack.appendChild(el);
    setTimeout(function(){ el.style.opacity='0'; }, 2400);
    setTimeout(function(){ try{ stack.removeChild(el);}catch(e){} }, 2800);
  }
  var modal = document.getElementById('send-modal');
  var target = document.getElementById('send-target');
  var txt = document.getElementById('send-text');
  var state = document.getElementById('send-state');
  var btn = document.getElementById('send-enviar');
  var files = document.getElementById('send-files');
  var fileList = document.getElementById('file-list');
  var currentGid = null;

  document.querySelectorAll('.g-btn[data-action="send"]').forEach(function(b){
    b.addEventListener('click', function(){
      currentGid = b.dataset.gid;
      target.textContent = b.dataset.name + ' — ' + b.dataset.gid;
      txt.value=''; files.value=''; fileList.textContent='';
      state.textContent=''; state.className='send-state';
      modal.classList.add('open'); setTimeout(function(){ txt.focus(); }, 50);
    });
  });
  document.getElementById('send-cancelar').addEventListener('click', function(){ modal.classList.remove('open'); });
  modal.addEventListener('click', function(e){ if(e.target===modal) modal.classList.remove('open'); });
  document.addEventListener('keydown', function(e){ if(e.key==='Escape') modal.classList.remove('open'); });
  files.addEventListener('change', function(){
    fileList.textContent = Array.from(files.files).map(function(f){ return f.name; }).join(', ');
  });
  btn.addEventListener('click', async function(){
    if(!currentGid) return;
    var t = txt.value.trim();
    if(!t && files.files.length===0){ state.textContent='Vacío'; state.className='send-state err'; return; }
    btn.disabled=true; state.textContent='Enviando…'; state.className='send-state';
    try{
      var fd = new FormData();
      fd.append('group_id', currentGid);
      if(t) fd.append('mensaje', t);
      Array.from(files.files).forEach(function(f){ fd.append('files', f, f.name); });
      var r = await fetch('/admin/grupos/send', { method:'POST', body:fd,
        headers:{'Accept':'application/json','X-Requested-With':'fetch'} });
      var d = await r.json();
      if(r.ok && d.ok){
        state.textContent = '✓ Enviado'; state.className='send-state ok';
        toast('Mensaje enviado al grupo');
        setTimeout(function(){ modal.classList.remove('open'); }, 900);
      } else {
        state.textContent = 'Error: ' + (d.error || r.status); state.className='send-state err';
        toast('Error al enviar', true);
      }
    }catch(err){ state.textContent='Error de red: '+err.message; state.className='send-state err'; }
    btn.disabled=false;
  });
})();
</script>
</body></html>"""
