# ROADMAP — Cantina Bot

Lo que hay hecho, lo que falta, y el orden en que lo recomiendo terminar.

> **Para la IA que toma este proyecto**: lee `docs/ARQUITECTURA.md` primero
> para entender los módulos, después aquí. Marca como hecho cada paso (✅)
> cuando lo termines y haz commit por paso.

---

## ✅ Hecho

| # | Tarea | Notas |
|---|-------|-------|
| ✅ | Estructura del proyecto replicada de `bot_asistente/` | 104 archivos, sin caches ni .env. |
| ✅ | Eliminados archivos DTGP-only | `app/integrations/{calcom,dtos,fish_audio,metasuite,voz}.py`, `app/pauta.py`, `app/grupos.py`, `app/shopify/`, prompts DTGP, scripts retail. |
| ✅ | `app/config.py` reescrito | Solo settings de La Cantina (`cantina_api_*`, `equipo_cantina_group_id`, etc.). |
| ✅ | `app/identidades.py` reescrito | Solo identidad principal "La Cantina Plus". |
| ✅ | `app/integrations/cantina_api.py` creado | Cliente HTTP completo del backend de mesas (todos los endpoints). |
| ✅ | `app/claude/prompts.py` reescrito | Identidad cliente + identidad equipo + clasificador intent. |
| ✅ | `app/claude/tools.py` reescrito | 8 tools de cliente: disponibilidad, evento, reserva (mesa/grupo/sala), comprobante, escalar. |
| ✅ | `app/claude/tools_equipo.py` reescrito | 9 tools operativas: resumen, listar/editar/cancelar reservas, marcar covers, eventos. |
| ✅ | `data/prompts/cantina-identidad.md` | Persona del bot. |
| ✅ | `data/prompts/cantina-info.md` | Info del venue (mesas, zonas, covers, salas, reglas VIP). |
| ✅ | `data/prompts/cantina-faq.md` | Respuestas modelo de FAQ típicas. |
| ✅ | Migrations limpiadas | Solo 002 (equipo), 008 (etiquetas), 009 (tags). Borradas las DTGP-only. |
| ✅ | `.env.example` reescrito | Variables Cantina, sin DTGP. |
| ✅ | `pyproject.toml` actualizado | Sin Shopify/BeautifulSoup; deps mínimas. |

---

## 🚧 Por hacer (en orden recomendado)

### Paso 1 — Adaptar `app/main.py`  ★ alta prioridad

`main.py` viene heredado tal cual de `bot_asistente` y tiene:
- Imports que ya no existen (`servicios`, `stories`, `seguimiento`, `dashboard` con datos DTGP).
- Rutas Cal.com (`/webhooks/calcom`) — **borrar**.
- Rutas con identidad secundaria — **borrar** (Cantina usa solo principal).
- Lógica de `is_pauta_canned`, `referral` (Meta Ads click-to-WhatsApp) — **mantener** si Fabio piensa hacer pauta; si no, simplificar.
- Lógica de "self-chat" y "operador en grupo equipo desde celular del bot" — **mantener** sirve igual.
- Lógica de "número del dueño bloqueado" (DTGP-specific) — **borrar**.

**Acción concreta:**
1. Comentar/borrar imports rotos.
2. Eliminar la ruta `/webhooks/calcom`.
3. Eliminar referencias a `prospecto`/`prospecto_caliente` que asumen modelo `Prospecto` DTGP.
4. Reemplazar `equipo_dtgp_group_id` por `equipo_cantina_group_id` en el código.
5. Ajustar `bot_port` a `8012` (ver `.env.example`).
6. Probar arranque local: `uvicorn app.main:app --reload`.

### Paso 2 — Reescribir `app/flows/conversation.py` (flow cliente) ★

Este flow procesa los mensajes del público (cualquier número no clasificado
como equipo/personal). En `bot_asistente` está pensado para PROSPECTOS DTGP
(con `recordar_sobre_prospecto`, modelo `Prospecto`, agenda Cal.com, etc.).

**Acción:**
1. Cambiar el import `from app.claude.prompts import construir_system_prompt` —
   ya está correcto, pero ahora devuelve el prompt de Cantina (3 bloques en
   vez de 4, sin "## CÓMO ATENDER A UN PROSPECTO").
2. Eliminar la query a `Prospecto` (no existe en este proyecto).
3. Eliminar la lógica de `Cita` (no existe).
4. Mantener: humanización, fuera de horario (con `feature_responder_24_7=True`
   el bloque no se ejecuta), nota de voz → transcribir, race-condition guard,
   imagen multimodal.
5. Crear un nuevo `_construir_contexto_cliente()` que devuelva:
   - Tags actuales del cliente (si tiene "Reservó con cover pagado", "Cliente
     recurrente", etc. — útil para que el bot personalice).
   - **Reservas activas** del cliente — query a `cantina_api.listar_reservas`
     filtrando por `cliente_numero`. Si tiene una reserva próxima, contexto.
   - Memoria evolutiva si se mantiene.
6. Conectar el `outbox` para que las notificaciones al grupo del equipo se
   manden tras el commit (mismo patrón que en `bot_asistente`).

### Paso 3 — Reescribir `app/flows/equipo.py` (flow equipo/staff)

Procesa los mensajes en el grupo de WhatsApp del staff y los self-chat.
La estructura es la misma que en `bot_asistente`, solo cambia:
- `SYSTEM_PROMPT_EQUIPO` (ya viene del nuevo `prompts.py`).
- Tools registradas: importar de `app.claude.tools_equipo` (ya reescrito).
- Quitar referencias a `Prospecto`, `Cita`, DT-OS.

### Paso 4 — Limpiar admin panel (`app/admin/`)

El admin actual tiene vistas DTGP-específicas que no aplican:

| Archivo | Acción |
|---------|--------|
| `app/admin/servicios.py` | **Eliminar.** Era el editor de prompts DTGP. Reemplazar por una vista simple "Editar prompts de Cantina" si se quiere. |
| `app/admin/seguimiento.py` | **Eliminar.** Era seguimiento de prospectos pauta. Aquí no aplica (o reescribir para "reservas pendientes de confirmar cover"). |
| `app/admin/stories.py` | **Eliminar.** Era publicación de stories WA — no aplica. |
| `app/admin/dashboard.py` | **Adaptar.** Dashboard con métricas DTGP. Reemplazar por: reservas hoy / semana, ocupación por zona, covers pagados / pendientes, alertas abiertas. |
| `app/admin/chats.py` | **Mantener.** Es genérico. Solo cambiar branding "Dairo · DTGP" → "Cantina Plus". |
| `app/admin/contactos.py` | **Mantener.** Importa contactos. Útil para cargar los números del staff. |
| `app/admin/grupos.py` | **Mantener.** Listado de grupos WA. |
| `app/admin/automatizaciones.py` | **Adaptar.** Reemplazar cron jobs DTGP por los de Cantina (ver Paso 6). |
| `app/admin/etiquetas.py` | **Mantener.** El CRUD de tags es reusable. |
| `app/admin/actions.py` | **Adaptar.** Tiene endpoint `cliente/{id}/etiqueta` que dispara flow prospecto — apuntar al flow cliente Cantina. |
| `app/admin/views.py` | **Adaptar.** Las ModelViews de SQLAdmin — quitar `ProspectoAdmin`, `CitaAdmin`. |
| `app/admin/_shell.py` | **Adaptar branding.** Sidebar y logo a "Cantina Plus". |

### Paso 5 — Limpiar modelos DB (`app/db/models.py`)

`models.py` tiene tablas DTGP-only:
- `Prospecto` — eliminar (no aplica).
- `Cita` — eliminar (no aplica).
- `Memoria` / `Recordatorio` — opcionales; útiles si quieres que el bot
  recuerde preferencias de clientes recurrentes (ej. *"Pedro siempre pide la
  mesa 12"*).

Mantener:
- `Cliente`, `Conversacion`, `WebhookProcesado`, `IntervencionHumana`,
  `Sesion`, `AlertaFabio` (renombrar mentalmente a "Alerta" — no es para
  Fabio sino para el grupo del equipo), `EquipoMiembro`, `ContactoWhitelist`,
  `NumeroInterno`, `Tag`, `ClienteTag`.

### Paso 6 — Adaptar cron jobs (`app/automatizaciones/`)

`scheduler.py` corre tareas BD-driven. El motor sigue siendo el mismo.
Lo que cambia son las ACCIONES en `acciones.py`. Hay que crear:

| Cron | Frecuencia | Acción |
|------|-----------|--------|
| `resumen_dia` | 9 AM | Llamar `cantina_api.resumen_dia(hoy)` y mandar al grupo equipo. |
| `pre_evento` | cada 15 min | Si quedan 2-3 h para un evento → resumen final. |
| `recordatorio_cover` | 10 AM | Si hay evento dentro de 24 h con covers `pendiente` → recordar al equipo (y opcionalmente al cliente). |
| `saturacion_check` | cada 30 min | Si capacidad ≥ 85% para fecha con evento → alerta. |
| `cierre_dia` | 3 AM | Resumen del día anterior, marca no-shows. |
| `liberar_pendientes_cover` | cada 30 min | Reservas en `pendiente` > N horas → libera mesa. |

Borrar los crons DTGP (`enviar_pendientes_apertura`, `sync_chats_whapi` se
puede mantener, `heartbeat` es genérico).

### Paso 7 — Seed inicial del staff

El staff de La Cantina (ver lista en [`docs/REGLAS_NEGOCIO.md`](REGLAS_NEGOCIO.md))
debe entrar a la tabla `numeros_internos` o `equipo_miembros` para que el bot
NO les responda como clientes.

Crear `scripts/seed_staff.py`:

```python
import asyncio
from sqlalchemy import text
from app.db.session import async_session_factory

STAFF = [
    ("+573226616509", "Mariano Mesa La Cantina", "Admin", "interno"),
    ("+573122708650", "Santiago Giraldo Cantina", "Cantante", "interno"),
    ("+573117681858", "Alejo Palacio", "Cantante/Cajero", "interno"),
    ("+573011062354", "Diana Cantina", "Mesera", "interno"),
    ("+573017866478", "Jordi", "Guardia", "interno"),
    ("+573215098448", "Mariana Cantina", "Mesera", "interno"),
    ("+573135861776", "Señor Fabio", "Dueño", "equipo"),
    ("+573013364137", "Dana", "Mesera", "interno"),
    ("+573244824083", "Brilly Giraldo", "Cantante", "interno"),
    ("+573116378746", "Nicol Giraldo", "Cantante", "interno"),
    ("+573116123189", "Edgardo", "Desarrollador / Equipo", "equipo"),
]

async def main():
    async with async_session_factory() as s:
        for numero, nombre, rol, tipo in STAFF:
            if tipo == "equipo":
                await s.execute(text("""
                    INSERT INTO equipo_miembros (nombre, numero_whatsapp, rol, activo, es_fallback)
                    VALUES (:n, :w, :r, true, :fb)
                    ON CONFLICT (numero_whatsapp) DO NOTHING
                """), {"n": nombre, "w": numero, "r": rol, "fb": nombre.startswith("Señor")})
            else:
                await s.execute(text("""
                    INSERT INTO numeros_internos (numero_whatsapp, nombre, razon, activo)
                    VALUES (:n, :nom, :r, true)
                    ON CONFLICT (numero_whatsapp) DO NOTHING
                """), {"n": numero, "nom": nombre, "r": rol})
        await s.commit()
    print("seed staff ok")

asyncio.run(main())
```

### Paso 8 — Configurar webhook de whapi al servidor

Una vez deployado, en el panel de whapi.cloud (canal de La Cantina):
- Settings → Webhook URL: `https://cantina-bot.dtgrowthpartners.com/webhook`
- Events: `messages.post`, `statuses.post`.

### Paso 9 — Pruebas end-to-end

Antes de hacer go-live abrir el bot al público:

1. **Disponibilidad y reserva**: como cliente desconocido, escribir "¿tienen
   mesa para 4 el viernes?" → confirmar que llama `consultar_disponibilidad`,
   ofrece mesa y crea reserva.
2. **Cover anticipado**: en una fecha con evento, reservar → recibir link
   pago → mandar comprobante (foto) → confirmar que escala al grupo equipo.
3. **Grupo grande**: pedir reserva para 8 → confirmar que ofrece `combo`
   (unir mesas vecinas) o sala privada.
4. **Día de evento + VIP con ≤6 personas**: confirmar que el bot NO ofrece
   VIP, sino Cantina/Rumbero.
5. **Staff escribiendo desde su número**: que NO le conteste como cliente.
6. **Equipo en grupo**: mandar "¿cómo va el viernes?" en el grupo equipo →
   confirmar resumen.
7. **Reactivar pausa**: pausar un chat desde admin, reactivar → confirmar
   que el bot retoma.

### Paso 10 — Documentación final para Fabio/Edgardo

Crear `docs/MANUAL_EQUIPO.md` para que Edgardo entienda cómo usar el admin
(`/admin/chats`, `/admin/pendientes`, `/admin/automatizaciones`) sin necesitar
soporte DTGP cada vez. Lenguaje no técnico.

---

## 📦 Estructura final esperada

```
cantina_bot/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── schema.sql                       ← limpiar tablas DTGP-only
├── docs/
│   ├── README.md (este)
│   ├── ROADMAP.md
│   ├── ARQUITECTURA.md
│   ├── API_BACKEND.md
│   ├── REGLAS_NEGOCIO.md
│   ├── DIFERENCIAS_VS_MARIA.md
│   ├── DEPLOY.md
│   └── MANUAL_EQUIPO.md             ← crear en Paso 10
├── migrations/
│   ├── 002_equipo_a_db.sql          (heredado)
│   ├── 008_etiquetas_contacto.sql   (heredado)
│   └── 009_tags_seguimiento.sql     (heredado)
├── scripts/
│   └── seed_staff.py                ← crear en Paso 7
├── data/
│   └── prompts/
│       ├── cantina-identidad.md
│       ├── cantina-info.md
│       └── cantina-faq.md
├── app/
│   ├── main.py                      ← Paso 1
│   ├── config.py                    ✅
│   ├── identidades.py               ✅
│   ├── logging_setup.py
│   ├── notif_equipo.py
│   ├── memoria.py                   (opcional, mantener o borrar)
│   ├── db/                          (limpiar models.py — Paso 5)
│   ├── claude/
│   │   ├── client.py
│   │   ├── anthropic_client.py
│   │   ├── intent.py
│   │   ├── prompts.py               ✅
│   │   ├── tools.py                 ✅
│   │   └── tools_equipo.py          ✅
│   ├── flows/
│   │   ├── conversation.py          ← Paso 2 (renombrar a `cliente.py`?)
│   │   └── equipo.py                ← Paso 3
│   ├── integrations/
│   │   └── cantina_api.py           ✅
│   ├── automatizaciones/            ← Paso 6
│   ├── admin/                       ← Paso 4
│   ├── equipo/
│   ├── utils/
│   ├── validators/
│   └── whapi/
└── tests/                           (a crear)
```

---

## 🎯 Definición de "listo"

El proyecto está listo para go-live cuando:

- [ ] Arranca sin errores: `uvicorn app.main:app` no falla.
- [ ] `/admin` carga (login + dashboard sin errores).
- [ ] Webhook recibe mensajes y los persiste en BD.
- [ ] El bot responde a un mensaje de prueba en menos de 30s.
- [ ] Una reserva creada por el bot aparece en `cantina-api.dtgrowthpartners.com/api/reservas`.
- [ ] El grupo del equipo recibe la alerta `reserva_nueva`.
- [ ] Pruebas del Paso 9 pasan.
