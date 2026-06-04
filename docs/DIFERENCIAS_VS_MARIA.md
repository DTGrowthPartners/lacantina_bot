# Diferencias respecto a `bot_asistente/` (María/DTGP)

Este proyecto es una **réplica adaptada** de `botplatform/bot_asistente/`.
Aquí está qué se mantuvo, qué se quitó y qué cambió.

## ✅ Reusado tal cual (cero cambios)

| Pieza | Por qué |
|-------|---------|
| `app/whapi/` (client + parser) | Cliente WhatsApp es genérico. Soporta botones quick-reply (probado en grupos). |
| `app/db/session.py` | Engine async + factory. Genérico. |
| `app/equipo/directorio.py` | Cache de equipo + whitelist + internos. Patrón reusable. |
| `app/automatizaciones/scheduler.py` | Motor de cron BD-driven con fix anti-loop. |
| `app/utils/humanizer.py` | Delays anti-detección Meta. |
| `app/notif_equipo.py` | Helper para mandar al grupo del equipo (texto y botones). |
| `app/validators/output_rules.py` | Validación de salidas de Claude. |
| `app/logging_setup.py` | structlog JSON. |
| `app/admin/_shell.py` | Estilos base + sidebar + theme toggle. Solo cambiar logo/nombre. |
| `app/admin/chats.py` | Lista de chats + chat individual (polling, etiquetas, tags, polling diff). |
| `app/admin/etiquetas.py` | CRUD de tags. |
| `app/admin/contactos.py` | Importar contactos. |
| `app/admin/grupos.py` | Listado de grupos WA. |
| `app/admin/actions.py` | AJAX endpoints (pausar, reactivar, reintentar, tag toggle). |
| `app/admin/automatizaciones.py` | UI de cron jobs. |
| `migrations/002_equipo_a_db.sql` | equipo_miembros, contactos_whitelist, numeros_internos. |
| `migrations/008_etiquetas_contacto.sql` | etiqueta primaria (cliente/prospecto/equipo/personal). |
| `migrations/009_tags_seguimiento.sql` | tags + cliente_tags (M2M). |

## 🔄 Adaptado (mismo concepto, contenido distinto)

| Pieza | Cambio |
|-------|--------|
| `app/config.py` | Sin Cal.com, Fish Audio, DT-OS, Shopify, MetaSuite. Añadido `cantina_api_*`, `equipo_cantina_group_id`. |
| `app/identidades.py` | Solo identidad principal. Sin secundaria. |
| `app/claude/prompts.py` | Identidad cliente + equipo escritas desde cero para Cantina. Sin DTGP. |
| `data/prompts/*` | Reescrito: `cantina-identidad.md`, `cantina-info.md`, `cantina-faq.md`. |
| `.env.example` | Limpio, solo variables relevantes. |
| `pyproject.toml` | Sin BeautifulSoup/lxml (no se hace scraping). |

## 🆕 Nuevo (no existía en María)

| Archivo | Por qué |
|---------|---------|
| `app/integrations/cantina_api.py` | Cliente HTTP del backend de mesas. Análogo a `dtos.py` (DT-OS) pero para `cantina-api.dtgrowthpartners.com`. |
| `app/claude/tools.py` (reescrito) | Tools de cliente: disponibilidad, reserva, etc. |
| `app/claude/tools_equipo.py` (reescrito) | Tools operativas para staff. |
| `docs/` | Documentación de proyecto para la IA que termine. |

## ❌ Quitado (no aplica en Cantina)

| Archivo / Módulo | Razón |
|------------------|-------|
| `app/integrations/calcom.py` | No hay agendamiento de citas — Cantina maneja reservas vía backend propio. |
| `app/integrations/dtos.py` | DT-OS es de DTGP. |
| `app/integrations/fish_audio.py` | No es prioridad voz TTS/ASR para Cantina (puede añadirse después si Fabio lo pide). |
| `app/integrations/metasuite.py` | Meta Ads reporting es de DTGP. |
| `app/integrations/voz.py` | Idem. |
| `app/shopify/` | Sin Shopify. |
| `app/pauta.py` | Detección de canned-pauta Meta CTWA (DTGP-specific). La Cantina no hace pauta por click-to-WhatsApp por ahora. |
| `app/grupos.py` | Helpers de grupos (refrescar, send manual, etc.) — duplicado con `admin/grupos.py`. |
| `data/prompts/dairo-identidad.md`, `dtgp-empresa.md`, `dtgp-servicios.md`, `maria-booking-playbook.md` | Prompts DTGP. |
| `data/prompts/contactos.vcf` | Lista de contactos retail. |
| `scripts/import_*.py` | Imports retail / DTGP. |
| `app/admin/servicios.py` | Editor de prompts DTGP. |
| `app/admin/seguimiento.py` | Seguimiento de prospectos pauta. (Si se quiere algo similar para Cantina ej. "reservas con cover pendiente", reescribir desde cero.) |
| `app/admin/stories.py` | Publicación de stories WA. |
| `migrations/003_maria_tables.sql` | Tablas DTGP: prospectos, citas, etc. |
| `migrations/004_maria_tareas.sql` | Tareas DTGP. |
| `migrations/005_estado_tareas.sql` | Estado tareas. |
| `migrations/006_memoria_recordatorios.sql` | Memoria DTGP. |
| `migrations/007_seed_heartbeat.sql` | Heartbeat DTGP. |

## ⚠️ Por revisar / decidir

| Archivo | Estado |
|---------|--------|
| `app/main.py` | Tiene rutas DTGP (`/webhooks/calcom`) y lógica de prospecto que hay que limpiar. **Paso 1 del ROADMAP.** |
| `app/flows/conversation.py` | Pensado para PROSPECTOS DTGP. Hay que reescribir para CLIENTES de Cantina. **Paso 2 del ROADMAP.** |
| `app/flows/equipo.py` | Adaptable. **Paso 3.** |
| `app/db/models.py` | Tiene `Prospecto`, `Cita`, `Memoria` que hay que quitar/decidir. **Paso 5.** |
| `app/memoria.py` | Memoria evolutiva. Puede mantenerse si quieres que el bot recuerde preferencias de clientes recurrentes. |
| `app/admin/dashboard.py` | Métricas DTGP. **Paso 4: adaptar.** |
| `app/admin/views.py` | ModelViews DTGP. Quitar `ProspectoAdmin`, `CitaAdmin`. |
| `app/admin/_shell.py` | Branding "Dairo · DTGP" → "Cantina Plus". |
| `schema.sql` | Tiene tablas legacy (retail, DTGP). Hay que limpiarlo para que solo cree las que `models.py` use. |

## Filosofía de la réplica

- **No reescribir lo que ya funciona.** El motor (whapi, scheduler, polling
  admin, humanización, dedupe, race-condition guard, manejo `is_from_bot`/
  `is_from_human`, botones interactivos en grupo) ya está probado en
  producción con DTGP. Reusarlo tal cual.
- **Aislar lo específico del cliente** en `app/integrations/cantina_api.py`,
  `app/claude/tools*.py`, `app/claude/prompts.py`, `data/prompts/`. Eso es lo
  que cambia entre verticales.
- **Adaptar el branding al final.** Logo, nombre, colores en
  `app/admin/_shell.py` y `app/admin/dashboard.py` se ajustan último, cuando
  el resto ya funciona.
