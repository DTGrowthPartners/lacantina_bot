"""/admin/contexto — Editor del contexto/prompts del bot (sin tocar código).

Permite a los administradores (Fabio, Edgardo) ajustar lo que el bot sabe y
responde: info del lugar, preguntas frecuentes, menú y la personalidad. Al
guardar, el bot aplica los cambios de inmediato (se recarga el cache de prompts,
sin reiniciar). Incluye un "agregar pregunta rápida" para sumar datos puntuales
(ej. ¿se puede entrar con armas?, ¿menores de edad?, ¿hay parqueadero?).
"""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.admin._shell import ICON_SPRITE, SHELL_STYLES, THEME_TOGGLE_JS, sidebar_html
from app.claude import prompts as prompts_mod
from app.config import get_settings
from app.logging_setup import log

router = APIRouter(prefix="/admin/contexto", tags=["admin-contexto"])

settings = get_settings()

# Documentos editables (clave -> metadata). El whitelist evita path traversal.
DOCS: dict[str, dict] = {
    "info": {
        "archivo": "cantina-info.md",
        "titulo": "Información del lugar",
        "desc": "Horarios, dirección, zonas, reglas (parqueadero, menores, etc.). El bot lo usa como verdad oficial.",
    },
    "faq": {
        "archivo": "cantina-faq.md",
        "titulo": "Preguntas frecuentes",
        "desc": "Respuestas a preguntas comunes. Agrega aquí cualquier duda nueva que te hagan.",
    },
    "menu": {
        "archivo": "cantina-menu.md",
        "titulo": "Menú / precios",
        "desc": "Lista de botellas, tragos y precios. El bot la usa para recomendar y mandar la carta.",
    },
    "identidad": {
        "archivo": "cantina-identidad.md",
        "titulo": "Personalidad del bot (avanzado)",
        "desc": "Cómo se comporta Nicky y sus reglas. Edítalo con cuidado: cambios aquí afectan TODO el comportamiento.",
    },
}
_MAX = 100_000  # tope de tamaño por documento


def _check_auth(request: Request) -> bool:
    return "admin_token" in request.session


def _leer(archivo: str) -> str:
    p = settings.prompts_path / archivo
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception as e:
        log.warning("admin.contexto.leer_fail", archivo=archivo, error=str(e))
        return ""


def _guardar(archivo: str, contenido: str) -> None:
    p = settings.prompts_path / archivo
    p.parent.mkdir(parents=True, exist_ok=True)
    # Backup del contenido anterior (recuperable si un cambio sale mal).
    try:
        if p.exists():
            (p.parent / f"{archivo}.bak").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass
    p.write_text(contenido, encoding="utf-8")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def vista(request: Request):
    if not _check_auth(request):
        raise HTTPException(401)

    secciones = []
    for key, meta in DOCS.items():
        contenido = html.escape(_leer(meta["archivo"]))
        secciones.append(f"""
        <div class="ctx-card" id="doc-{key}">
          <div class="ctx-head">
            <div>
              <div class="ctx-titulo">{html.escape(meta['titulo'])}</div>
              <div class="ctx-desc">{html.escape(meta['desc'])}</div>
            </div>
            <span class="ctx-file">{html.escape(meta['archivo'])}</span>
          </div>
          <form method="POST" action="/admin/contexto/guardar">
            <input type="hidden" name="doc" value="{key}"/>
            <textarea name="contenido" spellcheck="false">{contenido}</textarea>
            <div class="ctx-actions">
              <button type="submit" class="btn-primary">💾 Guardar y aplicar</button>
              <span class="ctx-hint">Al guardar, el bot usa el cambio al instante (sin reiniciar).</span>
            </div>
          </form>
        </div>""")

    flash = ""
    msg = request.query_params.get("msg")
    if msg == "guardado":
        doc = request.query_params.get("doc", "")
        flash = f'<div class="flash ok">✅ Guardado «{html.escape(DOCS.get(doc,{}).get("titulo",doc))}». El bot ya lo está usando.</div>'
    elif msg == "faq_ok":
        flash = '<div class="flash ok">✅ Pregunta agregada a las FAQ. El bot ya la puede responder.</div>'
    elif msg and msg.startswith("error:"):
        flash = f'<div class="flash err">{html.escape(msg[6:][:200])}</div>'

    body = (
        _TEMPLATE
        .replace("__SHELL_STYLES__", SHELL_STYLES)
        .replace("__ICON_SPRITE__", ICON_SPRITE)
        .replace("__SIDEBAR__", sidebar_html(active="contexto"))
        .replace("__THEME_JS__", THEME_TOGGLE_JS)
        .replace("{{secciones}}", "".join(secciones))
        .replace("{{flash}}", flash)
    )
    return HTMLResponse(body)


@router.post("/guardar")
async def guardar(request: Request, doc: str = Form(...), contenido: str = Form(...)):
    if not _check_auth(request):
        raise HTTPException(401)
    meta = DOCS.get(doc)
    if not meta:
        return RedirectResponse("/admin/contexto?msg=error:documento inválido", status_code=303)
    if len(contenido) > _MAX:
        return RedirectResponse("/admin/contexto?msg=error:texto demasiado largo", status_code=303)
    autor = request.session.get("admin_user", "admin")
    _guardar(meta["archivo"], contenido.replace("\r\n", "\n"))
    prompts_mod.recargar_prompts()
    log.warning("admin.contexto.guardado", doc=doc, archivo=meta["archivo"], autor=autor, chars=len(contenido))
    return RedirectResponse(f"/admin/contexto?msg=guardado&doc={doc}#doc-{doc}", status_code=303)


@router.post("/faq-rapida")
async def faq_rapida(request: Request, pregunta: str = Form(...), respuesta: str = Form(...)):
    if not _check_auth(request):
        raise HTTPException(401)
    pregunta = pregunta.strip()
    respuesta = respuesta.strip()
    if not pregunta or not respuesta:
        return RedirectResponse("/admin/contexto?msg=error:pregunta y respuesta requeridas", status_code=303)
    autor = request.session.get("admin_user", "admin")
    hoy = datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m-%d")
    actual = _leer("cantina-faq.md")
    bloque = f"\n\n**P: {pregunta}**\nR: {respuesta}\n_(agregada por {autor} · {hoy})_"
    _guardar("cantina-faq.md", (actual.rstrip() + bloque).lstrip("\n"))
    prompts_mod.recargar_prompts()
    log.warning("admin.contexto.faq_rapida", autor=autor, pregunta=pregunta[:60])
    return RedirectResponse("/admin/contexto?msg=faq_ok#doc-faq", status_code=303)


_TEMPLATE = """<!doctype html>
<html lang="es" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Contexto del bot — La Cantina</title>
__SHELL_STYLES__
<style>
  .page-title { font-size: 22px; font-weight: 600; margin: 0 0 4px; color: var(--text-primary); }
  .page-subtitle { color: var(--text-secondary); font-size: 13px; margin-bottom: 16px; max-width: 760px; }
  .flash { padding: 9px 12px; border-radius: 8px; font-size: 13px; margin-bottom: 14px; }
  .flash.ok { background: var(--accent-positive-bg); color: var(--accent-positive); border: 1px solid var(--accent-positive); }
  .flash.err { background: var(--accent-negative-bg); color: var(--accent-negative); border: 1px solid var(--accent-negative); }
  .ctx-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; box-shadow: var(--shadow-card); margin-bottom: 16px; padding: 16px 18px; }
  .ctx-card.quick { border-color: var(--chip-purple); }
  .ctx-head { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
  .ctx-titulo { font-weight: 600; font-size: 15px; color: var(--text-primary); }
  .ctx-desc { font-size: 12.5px; color: var(--text-secondary); margin-top: 2px; max-width: 640px; }
  .ctx-file { margin-left: auto; font-size: 11px; color: var(--text-tertiary); background: var(--bg-soft); padding: 2px 8px; border-radius: 6px; white-space: nowrap; }
  textarea { width: 100%; box-sizing: border-box; border: 1px solid var(--border); border-radius: 10px;
             padding: 12px 14px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px;
             line-height: 1.5; background: var(--bg-canvas); color: var(--text-primary); min-height: 320px; resize: vertical; }
  .ctx-card.quick input { width: 100%; box-sizing: border-box; padding: 9px 12px; border: 1px solid var(--border);
             border-radius: 8px; background: var(--bg-card); color: var(--text-primary); font: inherit; font-size: 14px; margin-bottom: 10px; }
  .ctx-card.quick textarea { min-height: 80px; }
  .ctx-actions { display: flex; align-items: center; gap: 12px; margin-top: 10px; }
  .ctx-hint { font-size: 12px; color: var(--text-tertiary); }
  .btn-primary { padding: 9px 16px; background: var(--btn-primary-bg); color: var(--btn-primary-text); border: none;
                 border-radius: 8px; font: inherit; font-weight: 600; font-size: 13px; cursor: pointer; }
  .ctx-nav { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
  .ctx-nav a { font-size: 12.5px; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--border);
               color: var(--text-secondary); text-decoration: none; background: var(--bg-card); }
  .ctx-nav a:hover { border-color: var(--chip-purple); color: var(--chip-purple); }
</style>
</head><body>
__ICON_SPRITE__
<div class="app">
  __SIDEBAR__
  <main class="main">
    <h1 class="page-title">Contexto del bot</h1>
    <p class="page-subtitle">Aquí ajustas lo que Nicky sabe y responde, sin tocar código. Cuando guardas, el bot aplica el cambio al instante. Para datos puntuales que te pregunten (¿se puede entrar con armas?, ¿menores?, ¿parqueadero?), usa "Agregar pregunta rápida".</p>
    {{flash}}

    <div class="ctx-nav">
      <a href="#quick">➕ Pregunta rápida</a>
      <a href="#doc-info">Información del lugar</a>
      <a href="#doc-faq">Preguntas frecuentes</a>
      <a href="#doc-menu">Menú</a>
      <a href="#doc-identidad">Personalidad</a>
    </div>

    <div class="ctx-card quick" id="quick">
      <div class="ctx-head"><div>
        <div class="ctx-titulo">➕ Agregar pregunta rápida</div>
        <div class="ctx-desc">La forma más fácil de enseñarle algo nuevo: escribe la pregunta y la respuesta. Se suma a las FAQ.</div>
      </div></div>
      <form method="POST" action="/admin/contexto/faq-rapida">
        <input name="pregunta" required placeholder="Pregunta — ej: ¿Hay parqueadero?"/>
        <textarea name="respuesta" required placeholder="Respuesta — ej: Sí, hay parqueadero público a media cuadra, sobre la misma calle."></textarea>
        <div class="ctx-actions"><button type="submit" class="btn-primary">Agregar a FAQ</button></div>
      </form>
    </div>

    {{secciones}}
  </main>
</div>
__THEME_JS__
</body></html>"""
