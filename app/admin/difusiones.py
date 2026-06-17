"""/admin/difusiones — campañas WhatsApp para eventos y anuncios."""

from __future__ import annotations

import asyncio
import html
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin._shell import ICON_SPRITE, SHELL_STYLES, THEME_TOGGLE_JS, sidebar_html
from app.db.session import get_session
from app.difusiones import (
    SegmentoDifusion,
    cancelar_difusion,
    contar_destinatarios,
    ejecutar_difusion_background,
    normalizar_etiqueta_difusion,
    pausar_difusion,
    preparar_difusion,
)
from app.logging_setup import log

router = APIRouter(prefix="/admin/difusiones", tags=["admin-difusiones"])


def _check_auth(request: Request) -> bool:
    return "admin_token" in request.session


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    try:
        return dt.astimezone(ZoneInfo("America/Bogota")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)[:16]


def _pill_estado(estado: str) -> str:
    cls = {
        "borrador": "draft",
        "enviando": "running",
        "enviada": "sent",
        "pausada": "paused",
        "cancelada": "cancelled",
        "error": "error",
    }.get(estado, "draft")
    return f'<span class="status {cls}">{html.escape(estado)}</span>'


async def _tags_options(session: AsyncSession, selected: int | None = None) -> str:
    rows = (await session.execute(sa_text(
        "SELECT id, nombre FROM tags ORDER BY orden, nombre"
    ))).fetchall()
    options = ['<option value="">Todos los tags</option>']
    for tid, nombre in rows:
        sel = " selected" if selected and int(selected) == int(tid) else ""
        options.append(f'<option value="{tid}"{sel}>{html.escape(nombre)}</option>')
    return "".join(options)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def lista(
    request: Request,
    etiqueta: str = "todos",
    tag_id: str = "",
    incluir_sin_chat: bool = True,
    session: AsyncSession = Depends(get_session),
):
    if not _check_auth(request):
        raise HTTPException(401, "No autenticado")

    etiqueta = normalizar_etiqueta_difusion(etiqueta)
    tag_id_int = int(tag_id) if str(tag_id).strip() else None
    segmento = SegmentoDifusion(
        etiqueta=etiqueta,
        tag_id=tag_id_int,
        incluir_sin_chat=incluir_sin_chat,
    )
    elegibles = await contar_destinatarios(session, segmento)
    rows = (await session.execute(sa_text(
        """
        SELECT d.id, d.nombre, d.estado, d.total_destinatarios, d.enviados,
               d.fallidos, d.omitidos, d.created_at, d.started_at, d.finished_at,
               d.dry_run, t.nombre AS tag_nombre
          FROM difusiones d
     LEFT JOIN tags t ON t.id = d.filtro_tag_id
         ORDER BY d.created_at DESC
         LIMIT 60
        """
    ))).mappings().all()

    flash = ""
    msg = request.query_params.get("msg")
    if msg == "creada":
        flash = '<div class="flash">Difusión preparada. Revisa destinatarios y ejecútala cuando estés listo.</div>'
    elif msg == "ejecutando":
        flash = '<div class="flash">Difusión en ejecución. Puedes salir de esta pantalla.</div>'
    elif msg == "pausada":
        flash = '<div class="flash">Difusión pausada.</div>'
    elif msg == "cancelada":
        flash = '<div class="flash warn">Difusión cancelada.</div>'

    items = []
    for r in rows:
        progress = f'{r["enviados"]}/{r["total_destinatarios"]}'
        if r["fallidos"]:
            progress += f' · {r["fallidos"]} fallidos'
        if r["omitidos"]:
            progress += f' · {r["omitidos"]} omitidos'
        dry = '<span class="mini">prueba</span>' if r["dry_run"] else ""
        tag = f'<span class="mini">tag: {html.escape(r["tag_nombre"])}</span>' if r["tag_nombre"] else ""
        items.append(f"""
        <a class="campaign-row" href="/admin/difusiones/{r['id']}">
          <div>
            <div class="campaign-name">{html.escape(r['nombre'])} {dry}</div>
            <div class="campaign-meta">{_fmt_dt(r['created_at'])} · {tag}</div>
          </div>
          <div>{_pill_estado(r['estado'])}</div>
          <div class="progress">{html.escape(progress)}</div>
        </a>
        """)

    html_resp = _TEMPLATE_LISTA \
        .replace("__SHELL_STYLES__", SHELL_STYLES) \
        .replace("__EXTRA_STYLES__", _EXTRA_STYLES) \
        .replace("__ICON_SPRITE__", ICON_SPRITE) \
        .replace("__SIDEBAR__", sidebar_html(active="difusiones")) \
        .replace("__THEME_JS__", THEME_TOGGLE_JS) \
        .replace("{{flash}}", flash) \
        .replace("{{items}}", "".join(items) or '<p class="empty">Todavía no hay difusiones.</p>') \
        .replace("{{tag_options}}", await _tags_options(session, tag_id_int)) \
        .replace("{{elegibles}}", str(elegibles)) \
        .replace("{{sel_todos}}", "selected" if etiqueta == "todos" else "") \
        .replace("{{sel_cliente}}", "selected" if etiqueta == "cliente" else "") \
        .replace("{{sel_prospecto}}", "selected" if etiqueta == "prospecto" else "") \
        .replace("{{sel_sin}}", "selected" if etiqueta == "sin_clasificar" else "") \
        .replace("{{checked_sin_chat}}", "checked" if incluir_sin_chat else "")
    return HTMLResponse(html_resp)


@router.post("/crear")
async def crear(
    request: Request,
    nombre: str = Form(...),
    mensaje: str = Form(...),
    media_url: str = Form(""),
    etiqueta: str = Form("todos"),
    tag_id: str = Form(""),
    incluir_sin_chat: str | None = Form(None),
    delay_min_s: Decimal = Form(12),
    delay_max_s: Decimal = Form(25),
    dry_run: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    if not _check_auth(request):
        raise HTTPException(401, "No autenticado")
    nombre = nombre.strip()
    mensaje = mensaje.strip()
    if not nombre or not mensaje:
        return RedirectResponse("/admin/difusiones?msg=datos_incompletos", 303)

    segmento = SegmentoDifusion(
        etiqueta=normalizar_etiqueta_difusion(etiqueta),
        tag_id=int(tag_id) if tag_id.strip() else None,
        incluir_sin_chat=bool(incluir_sin_chat),
    )
    difusion_id = await preparar_difusion(
        session,
        nombre=nombre,
        mensaje=mensaje,
        media_url=media_url,
        segmento=segmento,
        delay_min_s=delay_min_s,
        delay_max_s=delay_max_s,
        dry_run=bool(dry_run),
        creado_por=request.session.get("admin_user", "admin"),
    )
    return RedirectResponse(f"/admin/difusiones/{difusion_id}?msg=creada", 303)


@router.get("/{difusion_id}", response_class=HTMLResponse)
async def detalle(
    difusion_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    if not _check_auth(request):
        raise HTTPException(401, "No autenticado")
    row = (await session.execute(sa_text(
        """
        SELECT d.*, t.nombre AS tag_nombre
          FROM difusiones d
     LEFT JOIN tags t ON t.id = d.filtro_tag_id
         WHERE d.id=:id
        """
    ), {"id": difusion_id})).mappings().first()
    if not row:
        return RedirectResponse("/admin/difusiones", 303)

    dests = (await session.execute(sa_text(
        """
        SELECT numero_whatsapp, nombre, estado, error, enviado_en
          FROM difusion_destinatarios
         WHERE difusion_id=:id
         ORDER BY id
         LIMIT 300
        """
    ), {"id": difusion_id})).mappings().all()
    dest_rows = []
    for d in dests:
        err = f'<div class="dest-error">{html.escape(d["error"] or "")}</div>' if d["error"] else ""
        dest_rows.append(f"""
          <tr>
            <td>{html.escape(d['nombre'] or 'Sin nombre')}</td>
            <td><code>{html.escape(d['numero_whatsapp'])}</code></td>
            <td>{_pill_estado(d['estado'])}</td>
            <td>{_fmt_dt(d['enviado_en'])}{err}</td>
          </tr>
        """)

    puede_ejecutar = row["estado"] in ("borrador", "pausada", "error") and row["total_destinatarios"] > 0
    ejecutar_btn = f"""
      <form method="POST" action="/admin/difusiones/{difusion_id}/ejecutar">
        <button class="btn-primary" {'disabled' if not puede_ejecutar else ''}>Enviar difusión</button>
      </form>
    """
    pausar_btn = f"""
      <form method="POST" action="/admin/difusiones/{difusion_id}/pausar">
        <button class="btn-ghost" {'disabled' if row['estado'] != 'enviando' else ''}>Pausar</button>
      </form>
    """
    cancelar_btn = f"""
      <form method="POST" action="/admin/difusiones/{difusion_id}/cancelar"
            onsubmit="return confirm('¿Cancelar esta difusión? Los pendientes quedarán omitidos.');">
        <button class="btn-danger" {'disabled' if row['estado'] in ('enviada','cancelada') else ''}>Cancelar</button>
      </form>
    """
    media = row["media_url"] or "Sin imagen"
    html_resp = _TEMPLATE_DETALLE \
        .replace("__SHELL_STYLES__", SHELL_STYLES) \
        .replace("__EXTRA_STYLES__", _EXTRA_STYLES) \
        .replace("__ICON_SPRITE__", ICON_SPRITE) \
        .replace("__SIDEBAR__", sidebar_html(active="difusiones")) \
        .replace("__THEME_JS__", THEME_TOGGLE_JS) \
        .replace("{{nombre}}", html.escape(row["nombre"])) \
        .replace("{{estado}}", _pill_estado(row["estado"])) \
        .replace("{{mensaje}}", html.escape(row["mensaje"])) \
        .replace("{{media}}", html.escape(media)) \
        .replace("{{segmento}}", html.escape(_segmento_label(row))) \
        .replace("{{stats}}", html.escape(
            f'{row["enviados"]}/{row["total_destinatarios"]} enviados · '
            f'{row["fallidos"]} fallidos · {row["omitidos"]} omitidos'
        )) \
        .replace("{{acciones}}", ejecutar_btn + pausar_btn + cancelar_btn) \
        .replace("{{destinatarios}}", "".join(dest_rows) or '<tr><td colspan="4">Sin destinatarios.</td></tr>')
    return HTMLResponse(html_resp)


@router.post("/{difusion_id}/ejecutar")
async def ejecutar(difusion_id: int, request: Request):
    if not _check_auth(request):
        raise HTTPException(401, "No autenticado")
    asyncio.create_task(ejecutar_difusion_background(difusion_id))
    log.info("admin.difusion.ejecutar", difusion_id=difusion_id)
    return RedirectResponse(f"/admin/difusiones/{difusion_id}?msg=ejecutando", 303)


@router.post("/{difusion_id}/pausar")
async def pausar(
    difusion_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    if not _check_auth(request):
        raise HTTPException(401, "No autenticado")
    await pausar_difusion(session, difusion_id)
    return RedirectResponse(f"/admin/difusiones/{difusion_id}?msg=pausada", 303)


@router.post("/{difusion_id}/cancelar")
async def cancelar(
    difusion_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    if not _check_auth(request):
        raise HTTPException(401, "No autenticado")
    await cancelar_difusion(session, difusion_id)
    return RedirectResponse(f"/admin/difusiones/{difusion_id}?msg=cancelada", 303)


def _segmento_label(row) -> str:
    label = row["filtro_etiqueta"]
    if row["tag_nombre"]:
        label += f" · tag {row['tag_nombre']}"
    if not row["incluir_sin_chat"]:
        label += " · solo con chat"
    if row["dry_run"]:
        label += " · prueba"
    return label


_EXTRA_STYLES = """
<style>
  .page-title { font-size: 22px; font-weight: 600; margin: 0 0 4px; color: var(--text-primary); }
  .page-subtitle { color: var(--text-secondary); font-size: 13px; margin-bottom: 18px; }
  .flash { background: var(--accent-positive-bg); color: var(--accent-positive); border: 1px solid var(--accent-positive); padding: 9px 12px; border-radius: 8px; font-size: 13px; margin-bottom: 14px; }
  .flash.warn { background: var(--accent-negative-bg); color: var(--accent-negative); border-color: var(--accent-negative); }
  .panel { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; box-shadow: var(--shadow-card); }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .field label { display:block; font-size:12px; color:var(--text-secondary); margin-bottom:5px; }
  .field input, .field select, .field textarea { width:100%; box-sizing:border-box; border:1px solid var(--border); border-radius:8px; background:var(--bg-card); color:var(--text-primary); font:inherit; font-size:13px; padding:9px 10px; }
  .field textarea { min-height:112px; resize:vertical; }
  .field.full { grid-column:1/-1; }
  .hint { font-size:11px; color:var(--text-tertiary); margin-top:4px; }
  .actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:12px; }
  .btn-primary { background: var(--btn-primary-bg); color: var(--btn-primary-text); border: none; padding: 9px 16px; border-radius: 8px; font: inherit; font-weight: 600; font-size: 13px; cursor: pointer; }
  .btn-primary:disabled, .btn-ghost:disabled, .btn-danger:disabled { opacity:.45; cursor:not-allowed; }
  .btn-ghost { background:var(--bg-card); color:var(--text-primary); border:1px solid var(--border); padding:8px 12px; border-radius:8px; font:inherit; font-size:13px; cursor:pointer; text-decoration:none; }
  .btn-danger { background:var(--accent-negative-bg); color:var(--accent-negative); border:1px solid var(--accent-negative); padding:8px 12px; border-radius:8px; font:inherit; font-size:13px; cursor:pointer; }
  .campaign-row { display:grid; grid-template-columns:minmax(0,1fr) 120px 160px; gap:12px; align-items:center; padding:13px 14px; border-bottom:1px solid var(--border); color:inherit; text-decoration:none; }
  .campaign-row:last-child { border-bottom:none; }
  .campaign-name { font-weight:600; color:var(--text-primary); font-size:14px; }
  .campaign-meta, .progress { font-size:12px; color:var(--text-secondary); }
  .mini { font-size:10px; border:1px solid var(--border); border-radius:999px; padding:1px 7px; color:var(--text-secondary); margin-left:4px; }
  .status { display:inline-flex; align-items:center; justify-content:center; min-width:72px; font-size:11px; font-weight:700; border-radius:999px; padding:4px 8px; border:1px solid transparent; }
  .status.draft { background:var(--bg-soft); color:var(--text-secondary); border-color:var(--border); }
  .status.running { background:#DBEAFE; color:#1D4ED8; border-color:#93C5FD; }
  .status.sent { background:var(--accent-positive-bg); color:var(--accent-positive); border-color:var(--accent-positive); }
  .status.paused { background:#FEF3C7; color:#B45309; border-color:#FDE68A; }
  .status.cancelled, .status.error { background:var(--accent-negative-bg); color:var(--accent-negative); border-color:var(--accent-negative); }
  .empty { padding:24px; text-align:center; color:var(--text-tertiary); }
  .preview { white-space:pre-wrap; border:1px solid var(--border); border-radius:8px; padding:12px; color:var(--text-primary); background:var(--bg-soft); font-size:13px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:9px 8px; border-bottom:1px solid var(--border); vertical-align:top; }
  th { color:var(--text-secondary); font-size:11px; text-transform:uppercase; }
  .dest-error { color:var(--accent-negative); font-size:11px; margin-top:3px; }
  @media (max-width: 820px) {
    .grid, .campaign-row { grid-template-columns:1fr; }
  }
</style>
"""


_TEMPLATE_LISTA = """<!doctype html>
<html lang="es" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Difusiones — La Cantina</title>
__SHELL_STYLES__
__EXTRA_STYLES__
</head><body>
__ICON_SPRITE__
<div class="app">
  __SIDEBAR__
  <main class="main">
    <h1 class="page-title">Difusiones</h1>
    <p class="page-subtitle">Campañas de WhatsApp para eventos y anuncios. Elegibles actuales: {{elegibles}}</p>
    {{flash}}

    <section class="panel">
      <form method="POST" action="/admin/difusiones/crear">
        <div class="grid">
          <div class="field">
            <label>Nombre de campaña</label>
            <input name="nombre" required placeholder="Evento sábado - cover anticipado">
          </div>
          <div class="field">
            <label>Imagen pública opcional</label>
            <input name="media_url" placeholder="https://.../flyer.jpg">
          </div>
          <div class="field">
            <label>Etiqueta</label>
            <select name="etiqueta" onchange="this.form.method='GET'; this.form.action='/admin/difusiones'; this.form.submit();">
              <option value="todos" {{sel_todos}}>Todos elegibles</option>
              <option value="cliente" {{sel_cliente}}>Clientes</option>
              <option value="prospecto" {{sel_prospecto}}>Prospectos</option>
              <option value="sin_clasificar" {{sel_sin}}>Sin clasificar</option>
            </select>
          </div>
          <div class="field">
            <label>Tag de seguimiento</label>
            <select name="tag_id" onchange="this.form.method='GET'; this.form.action='/admin/difusiones'; this.form.submit();">
              {{tag_options}}
            </select>
          </div>
          <div class="field">
            <label>Delay mínimo por contacto</label>
            <input name="delay_min_s" type="number" min="0" step="0.5" value="12">
          </div>
          <div class="field">
            <label>Delay máximo por contacto</label>
            <input name="delay_max_s" type="number" min="0" step="0.5" value="25">
          </div>
          <div class="field full">
            <label>Mensaje</label>
            <textarea name="mensaje" required placeholder="Hola {primer_nombre}, este sábado tenemos evento..."></textarea>
            <div class="hint">Variables: {nombre}, {primer_nombre}, {numero}. Se excluyen bloqueados, equipo, personales, internos y grupos.</div>
          </div>
        </div>
        <div class="actions">
          <label><input type="checkbox" name="incluir_sin_chat" value="1" {{checked_sin_chat}}> Incluir contactos importados sin chat</label>
          <label><input type="checkbox" name="dry_run" value="1"> Modo prueba, no envía WhatsApp</label>
          <button class="btn-primary">Preparar difusión</button>
        </div>
      </form>
    </section>

    <section class="panel" style="padding:0;">
      {{items}}
    </section>
  </main>
</div>
__THEME_JS__
</body></html>
"""


_TEMPLATE_DETALLE = """<!doctype html>
<html lang="es" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Difusión — La Cantina</title>
__SHELL_STYLES__
__EXTRA_STYLES__
</head><body>
__ICON_SPRITE__
<div class="app">
  __SIDEBAR__
  <main class="main">
    <a class="btn-ghost" href="/admin/difusiones">Volver</a>
    <h1 class="page-title" style="margin-top:14px;">{{nombre}} {{estado}}</h1>
    <p class="page-subtitle">{{segmento}} · {{stats}}</p>
    <section class="panel">
      <div class="actions">{{acciones}}</div>
      <p class="hint">Media: {{media}}</p>
      <div class="preview">{{mensaje}}</div>
    </section>
    <section class="panel">
      <table>
        <thead><tr><th>Nombre</th><th>Número</th><th>Estado</th><th>Enviado</th></tr></thead>
        <tbody>{{destinatarios}}</tbody>
      </table>
    </section>
  </main>
</div>
__THEME_JS__
</body></html>
"""
