# Arquitectura — Cantina Bot

Cómo está organizado el código y qué hace cada módulo.

## Vista de 30 segundos

```
WhatsApp ──webhook──▶ FastAPI ──┬─▶ Routing (main.py)
                                │     │
                                │     ├─ Flow CLIENTE (público)
                                │     │   └─ Claude + tools (mesas, reservas)
                                │     │       └─▶ cantina-api (HTTP) → Backend Node
                                │     │
                                │     └─ Flow EQUIPO (staff grupo)
                                │         └─ Claude + tools_equipo
                                │             └─▶ cantina-api (HTTP)
                                │
                                ├─ Persistencia chats (Postgres)
                                ├─ Admin panel /admin (HTMX + SQLAdmin)
                                └─ Scheduler (cron jobs BD-driven)
```

## Módulos

### `app/main.py`
Entry point FastAPI. Define:
- `/webhook` (POST) — recibe eventos de whapi (mensajes nuevos + statuses).
- `/admin/...` — panel web (sidebar + chats + dashboard + contactos + etc.).
- Webhooks externos (en `bot_asistente` había `/webhooks/calcom` — borrar aquí).

Lógica del webhook (en orden de prioridad):
1. **Dedupe** por `whapi_message_id`.
2. **own_outbound** (eco del propio bot vía API) → ignorar.
3. **Mensajes en grupos** → solo procesar si es el grupo del equipo (`equipo_cantina_group_id`).
4. **Eventos sin contenido** (reactions, taps, polls vacías) → ignorar.
5. **Self-chat** (Mensaje a ti mismo del operador del celular del bot) → ruta al equipo.
6. **is_from_human** (operador escribió manual desde celular) → pausa 1h, persiste como `direccion=humano`.
7. **Estado** del cliente: cliente_id, etiqueta, pausa activa.
8. **Routing por etiqueta**:
   - `equipo` → silencio (el equipo opera en el grupo, no en chat 1:1).
   - `cliente_wl` / `cliente` → flow equipo (cliente WL, operación scoped).
   - `personal` → silencio total.
   - `NULL`/`prospecto` → flow CLIENTE.
9. **Filtros previos a flow**: bloqueado, número interno, dueño bloqueado, pausa.

### `app/config.py`
Settings (Pydantic Settings, lee `.env`):
- DB: `database_url`
- Anthropic: `anthropic_api_key`, modelo principal/intent, max tokens
- whapi: `whapi_token`, `whapi_numero_bot`, `whapi_base_url`
- **Cantina API**: `cantina_api_base_url`, `cantina_api_key`
- Grupo equipo: `equipo_cantina_group_id`
- Admin: `admin_user`, `admin_password`, `admin_session_secret`
- Humanización: delay min/max, feature flags

### `app/identidades.py`
Registry de identidades. La Cantina usa solo UNA identidad (canal principal:
"La Cantina Plus", +573008641322).

### `app/integrations/cantina_api.py` ★
Cliente HTTP del backend de mesas. Todas las funciones devuelven
`{ok: bool, data?: ..., error?: str}` y nunca levantan.

Funciones:
- `healthcheck()`, `disponibilidad(fecha, personas?)`, `combos(...)`,
- `crear_reserva(payload)`, `crear_reserva_grupo(payload)`,
- `listar_reservas(fecha)`, `detalle_reserva(id)`, `actualizar_reserva(id, cambios)`, `cancelar_reserva(id)`,
- `listar_salas()`, `disponibilidad_salas(fecha)`, `crear_reserva_sala(payload)`, `cancelar_reserva_sala(id)`,
- `consultar_evento(fecha)`, `crear_evento(payload)`, `borrar_evento(fecha)`,
- `listar_mesas()`, `layout()`, `plano()`,
- `resumen_dia(fecha)` (uso interno equipo).

### `app/claude/`
| Archivo | Qué hace |
|---------|----------|
| `anthropic_client.py` | Inicializa el cliente Anthropic (Dario proxy + fallback API directa). |
| `client.py` | `conversar()` — tool-use loop. Llama al modelo, ejecuta tools, devuelve respuesta. Incluye fix anti-respuesta-vacía. |
| `intent.py` | Clasificador con Haiku (rápido + barato). |
| `prompts.py` | System prompts cliente + equipo + clasificador. Usa bloques cacheables Anthropic. |
| `tools.py` | Tools de cliente público (8): disponibilidad, evento, reservas, comprobante, escalar. |
| `tools_equipo.py` | Tools operativas del staff (9): resumen, listar/editar/cancelar, marcar covers, eventos. |

### `app/flows/`
| Archivo | Qué hace |
|---------|----------|
| `conversation.py` | Flow CLIENTE: construye historial 48h, clasifica intent, llama Claude con `tools.py`, humaniza, envía vía whapi, persiste. |
| `equipo.py` | Flow EQUIPO: similar pero con `tools_equipo.py` y `SYSTEM_PROMPT_EQUIPO`. Responde en el grupo (no chat personal). |

### `app/db/`
| Archivo | Qué hace |
|---------|----------|
| `session.py` | Engine + `AsyncSession` factory. |
| `models.py` | Tablas SQLAlchemy 2.0. **Limpiar** Prospecto/Cita (DTGP-only). |
| `repos.py` | Helpers reusables (get_or_create_cliente, guardar_conversacion, pausar_bot, registrar_alerta, etc.). |

### `app/whapi/`
| Archivo | Qué hace |
|---------|----------|
| `client.py` | Cliente HTTP de whapi: `enviar_texto`, `enviar_imagen_bytes`, `enviar_documento_bytes`, `enviar_botones` (quick-reply), `enviar_typing`, etc. |
| `parser.py` | Convierte payload whapi → `MensajeWhapi`. Maneja `is_from_bot`/`is_from_human` (source), outbounds de grupo (usa `from`, no `chat_id`). |

### `app/equipo/directorio.py`
Cache en memoria del directorio:
- `equipo_miembros` (Postgres) — staff que opera en el grupo.
- `contactos_whitelist` — clientes activos con tratamiento especial.
- `numeros_internos` — números a ignorar (asesoras, bodegas — en La Cantina:
  el staff que no es "equipo operativo").

### `app/automatizaciones/`
Scheduler BD-driven. `tareas_programadas` (tabla) define crons. `scheduler.py`
las ejecuta. `acciones.py` registra los handlers.

### `app/admin/`
Panel web `/admin`:
- `_shell.py` — sidebar, styles base, theme dark/light.
- `chats.py` — lista de chats + chat individual con polling en vivo.
- `dashboard.py` — métricas. **Adaptar para Cantina** (reservas/covers).
- `etiquetas.py` — CRUD de tags de seguimiento.
- `actions.py` — endpoints AJAX (etiquetar, pausar, reactivar, reintentar, toggle tag, etc.).
- `views.py` — SQLAdmin ModelViews (CRUD genérico de cada tabla).
- `automatizaciones.py` — UI para los crons.
- `contactos.py`, `grupos.py` — vistas de gestión.

Específicos DTGP (BORRAR o adaptar): `servicios.py`, `seguimiento.py`, `stories.py`.

### `app/utils/humanizer.py`
Delays anti-detección Meta. `sleep_humano(texto)` espera 1-3 min según
longitud del mensaje. `dentro_horario()` chequea ventana. `puede_enviar()`
rate limit.

### `app/notif_equipo.py`
Helper para mandar al grupo del equipo: `notificar_equipo(texto)`,
`notificar_equipo_con_botones(body, botones)` (alertas accionables con
quick-reply).

### `app/logging_setup.py`
structlog → JSON logs a stdout. Filtrable con `journalctl --user -u
cantina-bot.service | jq`.

## Patrón outbox (importante)

Los flows acumulan acciones que requieren I/O externo (mandar mensaje al
grupo, escalar, etc.) en un `outbox: list` dentro de `ctx`. El flow hace:
1. Procesa el mensaje, llama tools, persiste cambios.
2. **`commit` de la DB**.
3. Después del commit, **drena el outbox** (manda los mensajes al equipo).

Razón: si el commit falla, no quedan mensajes "fantasma" enviados sin estado
en BD. Si el envío al equipo falla, ya el cliente recibió respuesta y la BD
está consistente.

## Race-condition guard

Cuando el bot está procesando un mensaje del cliente y entra OTRO mensaje
del mismo cliente, el flow del primer mensaje **aborta** antes de enviar.
El siguiente flow procesa todo junto con contexto completo. Evita que el
bot responda 3 veces a un cliente que mandó 3 mensajes seguidos.

## Identidad y atribución `from_me`

whapi puede traer eventos con `from_me=true` de varias fuentes:
- `source=api` → eco de la API (el bot escribió vía nuestra API) → ignorar.
- `source=mobile/web/etc.` → operador físico tocó el celular → procesar.
- En outbounds de GRUPO: `chat_id = group_id` (no del autor). Para sacar el
  número real del autor, usar el campo `from` del evento. El parser ya lo
  maneja correctamente en `whapi/parser.py`.
