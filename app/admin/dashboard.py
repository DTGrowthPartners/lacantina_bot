"""Endpoint /admin/dashboard — métricas clave del bot de La Cantina.

Las métricas de conversación / costo / alertas salen de la DB del bot
(Postgres). Las de reservas/covers salen del backend de mesas vía HTTP
(`cantina_api.resumen_dia`), best-effort: si el backend no responde, el panel
lo indica sin romper el dashboard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin._shell import sidebar_html
from app.db.models import AlertaFabio, Conversacion
from app.db.session import get_session
from app.integrations import cantina_api

router = APIRouter(prefix="/admin", tags=["admin"])


def _check_auth(request: Request) -> bool:
    """Misma sesión que SQLAdmin."""
    return "admin_token" in request.session


@router.get("/dashboard.json")
async def dashboard_json(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    ahora = datetime.now(timezone.utc)
    hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    hace_7d = ahora - timedelta(days=7)
    hace_30d = ahora - timedelta(days=30)

    # ── Conversaciones ──────────────────────────────────────────────────────
    conv_hoy = (await session.execute(
        select(func.count()).select_from(Conversacion)
        .where(Conversacion.timestamp >= hoy)
    )).scalar_one()
    inbound_hoy = (await session.execute(
        select(func.count()).select_from(Conversacion)
        .where(and_(Conversacion.timestamp >= hoy, Conversacion.direccion == "inbound"))
    )).scalar_one()
    outbound_hoy = (await session.execute(
        select(func.count()).select_from(Conversacion)
        .where(and_(
            Conversacion.timestamp >= hoy,
            Conversacion.direccion.in_(["outbound", "humano"]),
        ))
    )).scalar_one()
    chats_activos_hoy = (await session.execute(
        select(func.count(func.distinct(Conversacion.cliente_id)))
        .where(and_(Conversacion.timestamp >= hoy, Conversacion.direccion == "inbound"))
    )).scalar_one()
    clientes_activos = (await session.execute(
        select(func.count(func.distinct(Conversacion.cliente_id)))
        .where(Conversacion.timestamp >= hace_7d)
    )).scalar_one()

    # ── Costo Claude + cache ───────────────────────────────────────────────
    costo_hoy = (await session.execute(
        select(func.coalesce(func.sum(Conversacion.costo_usd), 0))
        .where(Conversacion.timestamp >= hoy)
    )).scalar_one()
    costo_30d = (await session.execute(
        select(func.coalesce(func.sum(Conversacion.costo_usd), 0))
        .where(Conversacion.timestamp >= hace_30d)
    )).scalar_one()
    cache_hoy = (await session.execute(
        select(
            func.coalesce(func.sum(Conversacion.tokens_input), 0),
            func.coalesce(func.sum(Conversacion.cache_read_tokens), 0),
        ).where(Conversacion.timestamp >= hoy)
    )).one()
    cache_hit_rate = 0.0
    if cache_hoy[0] + cache_hoy[1]:
        cache_hit_rate = float(cache_hoy[1]) / float(cache_hoy[0] + cache_hoy[1]) * 100

    # ── Alertas / pendientes ────────────────────────────────────────────────
    alertas_pendientes = (await session.execute(
        select(func.count()).select_from(AlertaFabio).where(AlertaFabio.resuelto.is_(False))
    )).scalar_one()
    alertas_recientes_rows = (await session.execute(
        select(AlertaFabio).where(AlertaFabio.resuelto.is_(False))
        .order_by(AlertaFabio.created_at.desc()).limit(6)
    )).scalars().all()
    alertas_recientes = [
        {
            "id": a.id,
            "tipo": a.tipo,
            "preview": (a.mensaje or "")[:90],
            "creada": a.created_at.isoformat() if a.created_at else None,
        }
        for a in alertas_recientes_rows
    ]

    # ── Reservas de hoy (backend de mesas, best-effort) ─────────────────────
    fecha_hoy = hoy.date().isoformat()
    resumen = await cantina_api.resumen_dia(fecha_hoy)
    if isinstance(resumen, dict) and resumen.get("ok"):
        p = resumen.get("data") if isinstance(resumen.get("data"), dict) else resumen
        ev = p.get("evento")
        reservas_list = [
            {
                "id": r.get("id"),
                "nombre": r.get("nombre_cliente") or "—",
                "mesa": r.get("mesa_numero"),
                "zona": r.get("mesa_zona"),
                "personas": r.get("num_personas"),
                "cover": r.get("cover_estado"),
                "notas": (r.get("notas") or "")[:60],
            }
            for r in (p.get("reservas") or [])
            if isinstance(r, dict)
        ]
        reservas_backend = {
            "ok": True,
            "resumen": {
                "mesas_ocupadas": p.get("mesas_ocupadas"),
                "mesas_totales": p.get("mesas_totales"),
                "total_personas": p.get("total_personas"),
                "covers_pendientes": p.get("covers_pendientes"),
                "evento": (ev.get("nombre") if isinstance(ev, dict) else None),
            },
            "reservas": reservas_list,
        }
    else:
        reservas_backend = {
            "ok": False,
            "error": (resumen or {}).get("error", "Backend de mesas no disponible"),
        }

    # ── Serie diaria últimos 7 días (conversaciones) ────────────────────────
    serie_7d: list[dict] = []
    for i in range(6, -1, -1):
        dia = (ahora - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        dia_siguiente = dia + timedelta(days=1)
        n_conv = (await session.execute(
            select(func.count()).select_from(Conversacion)
            .where(and_(Conversacion.timestamp >= dia, Conversacion.timestamp < dia_siguiente))
        )).scalar_one()
        serie_7d.append({
            "fecha": dia.strftime("%Y-%m-%d"),
            "label": dia.strftime("%a %d"),
            "conversaciones": int(n_conv),
        })

    return {
        "hora_consulta": ahora.isoformat(),
        "conversaciones": {
            "total_hoy": int(conv_hoy),
            "inbound_hoy": int(inbound_hoy),
            "outbound_hoy": int(outbound_hoy),
            "chats_activos_hoy": int(chats_activos_hoy),
            "clientes_activos_7d": int(clientes_activos),
        },
        "claude": {
            "costo_usd_hoy": float(costo_hoy),
            "costo_usd_30d": float(costo_30d),
            "cache_hit_rate_pct": round(cache_hit_rate, 1),
        },
        "alertas_pendientes": int(alertas_pendientes),
        "alertas_recientes": alertas_recientes,
        "reservas_backend": reservas_backend,
        "serie_7d": serie_7d,
        "bot_estado": await _bot_estado(session),
    }


async def _bot_estado(session: AsyncSession) -> dict:
    from sqlalchemy import text as sa_text
    row = (await session.execute(sa_text(
        "SELECT activo, pausado_por, pausado_en, razon FROM bot_estado WHERE id=1"
    ))).first()
    if not row:
        return {"activo": True, "pausado_por": None, "pausado_en": None, "razon": None}
    return {
        "activo": bool(row[0]),
        "pausado_por": row[1],
        "pausado_en": row[2].isoformat() if row[2] else None,
        "razon": row[3],
    }


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_html(request: Request):
    """Página HTML mínima que llama al JSON y renderiza con JS."""
    if not _check_auth(request):
        return HTMLResponse(
            '<p>No autenticado. <a href="/admin/login">Login</a></p>',
            status_code=401,
        )
    html = _TEMPLATE_DASHBOARD.replace("__SIDEBAR__", sidebar_html(active="dashboard"))
    return HTMLResponse(html)


_TEMPLATE_DASHBOARD = r"""<!doctype html>
<html lang="es" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard — La Cantina Plus</title>
<script>
(function(){
  try {
    var saved = localStorage.getItem('theme');
    document.documentElement.setAttribute('data-theme', saved === 'dark' ? 'dark' : 'light');
  } catch(e) {}
})();
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg-canvas: #F7F7F8; --bg-card: #FFFFFF; --bg-sidebar: #FAFAFA; --bg-soft: #F4F4F5;
    --border: #EAEAEC; --border-subtle: #F0F0F1;
    --text-primary: #1A1A1A; --text-secondary: #6B7280; --text-tertiary: #9CA3AF;
    --accent-positive: #16A34A; --accent-positive-bg: rgba(22,163,74,.10);
    --accent-negative: #DC2626; --accent-negative-bg: rgba(220,38,38,.10);
    --shadow-card: 0 1px 2px rgba(0,0,0,0.04);
    --btn-primary-bg: #1A1A1A; --btn-primary-text: #FFFFFF;
    --chip-purple: #7C3AED; --chip-purple-bg: rgba(124,58,237,.12);
    --chip-blue: #2563EB; --chip-blue-bg: rgba(37,99,235,.12);
    --chip-orange: #EA580C; --chip-orange-bg: rgba(234,88,12,.12);
    --chip-pink: #DB2777; --chip-pink-bg: rgba(219,39,119,.12);
    --chip-green: #16A34A; --chip-green-bg: rgba(22,163,74,.12);
  }
  [data-theme="dark"] {
    --bg-canvas: #09090B; --bg-card: #18181B; --bg-sidebar: #0F0F10; --bg-soft: #1F1F23;
    --border: #27272A; --border-subtle: #1F1F23;
    --text-primary: #FAFAFA; --text-secondary: #A1A1AA; --text-tertiary: #71717A;
    --accent-positive: #22C55E; --accent-positive-bg: rgba(34,197,94,.15);
    --accent-negative: #EF4444; --accent-negative-bg: rgba(239,68,68,.15);
    --shadow-card: 0 1px 2px rgba(0,0,0,0.3);
    --btn-primary-bg: #FAFAFA; --btn-primary-text: #0A0A0A;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  html { background: var(--bg-canvas); }
  body {
    background: var(--bg-canvas) !important; color: var(--text-primary);
    font-family: 'Inter', system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    font-size: 14px; line-height: 1.5; -webkit-font-smoothing: antialiased;
  }
  .app { display: grid; grid-template-columns: 240px 1fr; min-height: 100vh; background: var(--bg-canvas) !important; }
  .sidebar {
    background: var(--bg-sidebar) !important; border-right: 1px solid var(--border);
    padding: 20px 14px; display: flex; flex-direction: column;
    position: sticky; top: 0; height: 100vh; overflow-y: auto;
  }
  .main { background: var(--bg-canvas) !important; padding: 28px 32px; }
  .brand { display: flex; align-items: center; gap: 10px; padding: 4px 10px 16px; }
  .brand-logo { width: 32px; height: 32px; border-radius: 8px; background: var(--chip-orange-bg); color: var(--chip-orange); display: grid; place-items: center; font-weight: 700; font-size: 14px; }
  .brand-name { font-weight: 600; font-size: 14px; color: var(--text-primary); }
  .nav-group { margin-bottom: 16px; }
  .nav-group-label { font-size: 11px; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: .5px; padding: 6px 12px; font-weight: 500; }
  .nav-item { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-radius: 8px; color: var(--text-secondary); text-decoration: none; font-size: 13.5px; font-weight: 500; margin: 1px 0; }
  .nav-item:hover { background: var(--bg-soft); color: var(--text-primary); }
  .nav-item.active { background: var(--bg-soft); color: var(--text-primary); }
  .nav-item .ico { width: 16px; height: 16px; flex-shrink: 0; }
  .nav-bottom { margin-top: auto; padding-top: 16px; border-top: 1px solid var(--border-subtle); }
  .topbar { display: flex; align-items: center; gap: 16px; margin-bottom: 20px; }
  .top-actions { margin-left: auto; display: flex; align-items: center; gap: 8px; }
  .page-title { font-size: 22px; font-weight: 600; margin: 0 0 4px; color: var(--text-primary); }
  .page-subtitle { color: var(--text-secondary); font-size: 13px; margin-bottom: 24px; }
  .btn-primary { padding: 7px 14px; background: var(--btn-primary-bg); color: var(--btn-primary-text); border: none; border-radius: 8px; font: inherit; font-size: 13px; font-weight: 500; cursor: pointer; }
  .btn-primary:hover { opacity: .9; }
  .btn-ghost { padding: 6px 12px; background: transparent; border: 1px solid var(--border); border-radius: 8px; color: var(--text-primary); font: inherit; font-size: 12.5px; cursor: pointer; }
  .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; box-shadow: var(--shadow-card); }
  .card-header { padding: 14px 18px; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--border-subtle); }
  .card-title { font-size: 14px; font-weight: 600; color: var(--text-primary); }
  .chip { width: 32px; height: 32px; border-radius: 8px; display: grid; place-items: center; flex-shrink: 0; }
  .chip.purple { background: var(--chip-purple-bg); color: var(--chip-purple); }
  .chip.blue { background: var(--chip-blue-bg); color: var(--chip-blue); }
  .chip.orange { background: var(--chip-orange-bg); color: var(--chip-orange); }
  .chip.pink { background: var(--chip-pink-bg); color: var(--chip-pink); }
  .chip.green { background: var(--chip-green-bg); color: var(--chip-green); }
  .kpi-grid { display: grid; gap: 16px; margin-bottom: 24px; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
  .kpi { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; box-shadow: var(--shadow-card); }
  .kpi-top { display: flex; align-items: center; gap: 10px; }
  .kpi-top .label { font-size: 13px; color: var(--text-secondary); flex: 1; }
  .kpi-value { font-size: 30px; font-weight: 700; margin: 12px 0 8px; letter-spacing: -.5px; color: var(--text-primary); }
  .kpi-foot { display: flex; align-items: center; justify-content: space-between; font-size: 12px; }
  .kpi-foot .vs { color: var(--text-tertiary); }
  .bot-banner { display: flex; align-items: center; justify-content: space-between; gap: 16px; background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 18px; margin-bottom: 20px; box-shadow: var(--shadow-card); }
  .bot-status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; }
  .bot-status-dot.active { background: var(--accent-positive); }
  .bot-status-dot.inactive { background: var(--accent-negative); }
  .row-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  @media (max-width: 1024px) { .row-2col { grid-template-columns: 1fr; } }
  table { width: 100%; border-collapse: collapse; }
  thead th { text-align: left; font-size: 11.5px; font-weight: 500; color: var(--text-tertiary); padding: 10px 18px; text-transform: uppercase; letter-spacing: .5px; border-bottom: 1px solid var(--border-subtle); }
  tbody td { padding: 12px 18px; border-bottom: 1px solid var(--border-subtle); font-size: 13.5px; }
  tbody tr:last-child td { border-bottom: none; }
  .badge-state { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 500; background: var(--bg-soft); color: var(--text-secondary); border: 1px solid var(--border); }
  .cell-main { font-weight: 500; color: var(--text-primary); }
  .cell-sub { font-size: 11.5px; color: var(--text-tertiary); margin-top: 2px; }
  .chart-card { padding: 0; }
  .chart-canvas-wrap { padding: 18px; height: 300px; position: relative; }
  .empty { padding: 32px 18px; text-align: center; color: var(--text-tertiary); font-size: 13px; }
  .ico { display: inline-block; vertical-align: middle; }
</style>
</head>
<body>

<svg width="0" height="0" style="position:absolute">
  <defs>
    <symbol id="i-dashboard" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></symbol>
    <symbol id="i-messages" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></symbol>
    <symbol id="i-users" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></symbol>
    <symbol id="i-alert" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></symbol>
    <symbol id="i-settings" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></symbol>
    <symbol id="i-spark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9 11 13 15 21 7"/></symbol>
    <symbol id="i-bot" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="18" height="13" rx="2"/><circle cx="9" cy="13" r="1"/><circle cx="15" cy="13" r="1"/><path d="M12 7V3"/></symbol>
    <symbol id="i-money" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></symbol>
    <symbol id="i-theme" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></symbol>
  </defs>
</svg>

<div class="app">
  __SIDEBAR__

  <main class="main">
    <div class="topbar">
      <div class="top-actions">
        <button class="btn-ghost" onclick="location.href='/admin/chats'">Ver chats</button>
        <button class="btn-primary" onclick="window.print()">Exportar</button>
      </div>
    </div>

    <h1 class="page-title">Resumen de La Cantina Plus</h1>
    <p class="page-subtitle" id="hora">Cargando…</p>

    <div class="bot-banner" id="bot-banner">
      <div style="display:flex;align-items:center;gap:12px;">
        <div class="chip purple"><svg class="ico" width="16" height="16"><use href="#i-bot"/></svg></div>
        <div>
          <div style="font-weight:600;font-size:13.5px;" id="bot-banner-title">Cargando…</div>
          <div style="font-size:12px;color:var(--text-tertiary);" id="bot-banner-sub"></div>
        </div>
      </div>
      <form method="POST" action="/admin/actions/bot/toggle">
        <button type="submit" class="btn-primary" id="bot-banner-btn">…</button>
      </form>
    </div>

    <div class="kpi-grid" id="kpis"></div>

    <div class="row-2col">
      <div class="card">
        <div class="card-header">
          <div class="chip blue"><svg class="ico" width="16" height="16"><use href="#i-money"/></svg></div>
          <div class="card-title">Reservas de hoy</div>
          <div id="reservas-resumen" style="margin-left:auto;font-size:12px;color:var(--text-secondary);"></div>
        </div>
        <table>
          <thead><tr><th>Cliente</th><th>Mesa</th><th>Pers.</th><th>Cover</th></tr></thead>
          <tbody id="reservas-body"></tbody>
        </table>
      </div>

      <div class="card">
        <div class="card-header">
          <div class="chip orange"><svg class="ico" width="16" height="16"><use href="#i-alert"/></svg></div>
          <div class="card-title">Pendientes recientes</div>
        </div>
        <table>
          <thead><tr><th>Tipo</th><th>Detalle</th></tr></thead>
          <tbody id="alertas-body"></tbody>
        </table>
      </div>
    </div>

    <div class="card chart-card">
      <div class="card-header">
        <div class="chip green"><svg class="ico" width="16" height="16"><use href="#i-spark"/></svg></div>
        <div class="card-title">Conversaciones — últimos 7 días</div>
      </div>
      <div class="chart-canvas-wrap"><canvas id="chart-7d"></canvas></div>
    </div>
  </main>
</div>

<script>
(function(){
  const saved = localStorage.getItem('theme');
  document.documentElement.setAttribute('data-theme', saved === 'dark' ? 'dark' : 'light');
  const tt = document.getElementById('theme-toggle');
  if (tt) {
    tt.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme') || 'light';
      const nxt = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', nxt);
      localStorage.setItem('theme', nxt);
      const lbl = document.getElementById('theme-label');
      if (lbl) lbl.textContent = nxt === 'dark' ? 'Modo claro' : 'Modo oscuro';
      if (window._chart7dData) renderChart(window._chart7dData);
    });
    const lbl = document.getElementById('theme-label');
    if (lbl) lbl.textContent = document.documentElement.getAttribute('data-theme') === 'dark' ? 'Modo claro' : 'Modo oscuro';
  }
})();

const fmt = n => new Intl.NumberFormat('es-CO').format(n);
const esc = s => (s == null ? '' : String(s)).replace(/</g, '&lt;');

function renderChart(d) {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  const gridColor = isDark ? '#27272A' : '#F0F0F1';
  const textColor = isDark ? '#A1A1AA' : '#6B7280';
  if (window._chart7d) window._chart7d.destroy();
  const ctx = document.getElementById('chart-7d').getContext('2d');
  window._chart7d = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: d.serie_7d.map(s => s.label),
      datasets: [
        { label: 'Conversaciones', data: d.serie_7d.map(s => s.conversaciones),
          backgroundColor: 'rgba(124,58,237,0.7)', borderRadius: 6 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: textColor, font: { family: 'Inter' } } },
        y: { grid: { color: gridColor, borderDash: [3,3] }, ticks: { color: textColor, precision: 0 } },
      },
    },
  });
}

fetch('/admin/dashboard.json').then(r => r.json()).then(d => {
  document.getElementById('hora').textContent = 'Consultado: ' + new Date(d.hora_consulta).toLocaleString('es-CO');

  const est = d.bot_estado || { activo: true };
  document.getElementById('bot-banner-title').innerHTML = est.activo
    ? '<span class="bot-status-dot active"></span>Bot ACTIVO'
    : '<span class="bot-status-dot inactive"></span>Bot PAUSADO';
  document.getElementById('bot-banner-sub').textContent = est.activo
    ? 'El bot responde automáticamente.'
    : 'El bot no está respondiendo. ' + (est.razon || '') + (est.pausado_por ? ' · pausado por ' + est.pausado_por : '');
  const btn = document.getElementById('bot-banner-btn');
  btn.textContent = est.activo ? 'Pausar bot' : 'Reactivar bot';
  if (!est.activo) { btn.style.background = 'var(--accent-positive)'; btn.style.color = '#fff'; }

  const k = document.getElementById('kpis');
  const cards = [
    { chip: 'pink',   icon: '#i-messages', t: 'Chats activos hoy', v: d.conversaciones.chats_activos_hoy, sub: 'contactos únicos' },
    { chip: 'blue',   icon: '#i-bot',      t: 'Mensajes enviados',  v: d.conversaciones.outbound_hoy,      sub: 'hoy' },
    { chip: 'purple', icon: '#i-messages', t: 'Mensajes recibidos', v: d.conversaciones.inbound_hoy,       sub: 'hoy' },
    { chip: 'orange', icon: '#i-alert',    t: 'Pendientes',         v: d.alertas_pendientes,               sub: 'sin resolver' },
    { chip: 'green',  icon: '#i-money',    t: 'Costo IA hoy',       v: '$' + Number(d.claude.costo_usd_hoy).toFixed(2), sub: 'USD' },
    { chip: 'blue',   icon: '#i-spark',    t: 'Cache hit',          v: d.claude.cache_hit_rate_pct + '%',  sub: 'ahorro de tokens' },
  ];
  k.innerHTML = cards.map(c => `
    <div class="kpi">
      <div class="kpi-top">
        <div class="chip ${c.chip}"><svg width="16" height="16"><use href="${c.icon}"/></svg></div>
        <div class="label">${c.t}</div>
      </div>
      <div class="kpi-value">${c.v}</div>
      <div class="kpi-foot"><span class="vs">${c.sub}</span></div>
    </div>
  `).join('');

  const rb = document.getElementById('reservas-body');
  const rsum = document.getElementById('reservas-resumen');
  const rbk = d.reservas_backend || {};
  if (rbk.ok) {
    const s = rbk.resumen || {};
    const partes = [];
    if (s.mesas_ocupadas != null) partes.push(s.mesas_ocupadas + '/' + (s.mesas_totales ?? '?') + ' mesas');
    if (s.total_personas != null) partes.push(s.total_personas + ' pers.');
    if (s.covers_pendientes) partes.push(s.covers_pendientes + ' cover pend.');
    if (s.evento) partes.push('🎤 ' + esc(s.evento));
    rsum.innerHTML = partes.join(' · ');
    const rs = rbk.reservas || [];
    if (rs.length) {
      rb.innerHTML = rs.map(r => `
        <tr>
          <td><span class="cell-main">${esc(r.nombre)}</span>${r.notas ? '<div class="cell-sub">'+esc(r.notas)+'</div>' : ''}</td>
          <td>${r.mesa != null ? 'Mesa ' + esc(r.mesa) : '—'}${r.zona ? ' <span class="cell-sub">'+esc(r.zona)+'</span>' : ''}</td>
          <td>${esc(r.personas ?? '')}</td>
          <td><span class="badge-state">${esc(r.cover || 'no aplica')}</span></td>
        </tr>`).join('');
    } else {
      rb.innerHTML = '<tr><td colspan="4" class="empty">No hay reservas para hoy todavía.</td></tr>';
    }
  } else {
    rsum.innerHTML = '';
    rb.innerHTML = '<tr><td colspan="4" class="empty">' + esc(rbk.error || 'Backend de mesas no disponible.') + '</td></tr>';
  }

  const ab = document.getElementById('alertas-body');
  if (d.alertas_recientes && d.alertas_recientes.length) {
    ab.innerHTML = d.alertas_recientes.map(a => `
      <tr><td><span class="badge-state">${esc(a.tipo)}</span></td><td>${esc(a.preview)}</td></tr>
    `).join('');
  } else {
    ab.innerHTML = '<tr><td colspan="2" class="empty">No hay pendientes abiertos. 🎉</td></tr>';
  }

  window._chart7dData = d;
  renderChart(d);
}).catch(e => {
  console.error(e);
  document.body.insertAdjacentHTML('beforeend',
    '<div style="position:fixed;bottom:20px;right:20px;background:var(--accent-negative);color:#fff;padding:12px 16px;border-radius:8px;">Error: ' + e + '</div>');
});
</script>
</body>
</html>"""
