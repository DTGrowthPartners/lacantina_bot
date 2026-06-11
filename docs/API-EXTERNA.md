# API externa — control y monitoreo del bot

Esta API permite a una **plataforma de administración externa**:

1. **Activar / desactivar** el bot (kill-switch remoto).
2. Consultar **estadísticas** (reservas del día, mensajes, chats, pendientes).
3. Consultar **health** (bot vivo + conexión de WhatsApp).
4. Recibir **webhooks salientes** firmados cuando ocurren eventos.

- **Base URL:** `https://cantinabot.dtgrowthpartners.com`
- **Prefijo:** `/api/v1`
- **Formato:** JSON (UTF-8).
- **Identificador del bot:** cada bot tiene un `bot_id` (este es `cantina-plus`).
  Sirve para que la plataforma distinga entre varios bots.

---

## Autenticación (entrada)

Todas las llamadas a `/api/v1/*` requieren el header:

```
Authorization: Bearer <CONTROL_API_KEY>
```

- La llave la entrega el operador del bot (variable `CONTROL_API_KEY`).
- Si la llave no está configurada en el bot, la API responde `503`.
- Token inválido o ausente → `401`.

---

## Endpoints

### `GET /api/v1/health`
Estado de salud para monitoreo.

```json
{
  "ok": true,
  "bot_id": "cantina-plus",
  "vivo": true,
  "bot_activo": true,
  "whatsapp": { "conectado": true, "estado": "AUTH" },
  "ts": "2026-06-09T22:10:00+00:00"
}
```

- `bot_activo`: si el bot está respondiendo (kill-switch).
- `whatsapp.conectado`: si la sesión de WhatsApp del canal está autenticada.

---

### `GET /api/v1/estado`
Estado del kill-switch del bot.

```json
{
  "ok": true,
  "bot_id": "cantina-plus",
  "activo": true,
  "pausado_por": null,
  "pausado_en": null,
  "razon": null
}
```

Cuando está pausado:

```json
{
  "ok": true,
  "bot_id": "cantina-plus",
  "activo": false,
  "pausado_por": "api_externa",
  "pausado_en": "2026-06-09T22:00:00+00:00",
  "razon": "Mantenimiento"
}
```

---

### `POST /api/v1/estado`
Activa o desactiva el bot. **Desactivar = silencio total** (el bot no envía
ningún mensaje, ni a clientes ni al equipo) hasta reactivarlo.

**Body:**
```json
{ "activo": false, "razon": "Mantenimiento" }
```
- `activo` (bool, requerido).
- `razon` (string, opcional) — solo se usa al desactivar.

**Respuesta:** el nuevo estado (igual que `GET /api/v1/estado`).

Ejemplo:
```bash
curl -X POST https://cantinabot.dtgrowthpartners.com/api/v1/estado \
  -H "Authorization: Bearer $CONTROL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"activo": false, "razon": "Mantenimiento"}'
```

Este endpoint también dispara el webhook `bot.estado_cambiado`.

---

### `GET /api/v1/stats`
Métricas operativas (zona horaria del bot: America/Bogota; los timestamps van en UTC).

```json
{
  "ok": true,
  "bot_id": "cantina-plus",
  "ts": "2026-06-09T22:10:00+00:00",
  "bot_activo": true,
  "reservas_hoy": {
    "ok": true,
    "mesas_ocupadas": 8,
    "mesas_totales": 42,
    "total_personas": 34,
    "covers_pendientes": 2,
    "evento": "Tributo Yeison Jiménez"
  },
  "mensajes_hoy": { "recibidos": 120, "enviados": 98, "total": 218 },
  "chats_activos_hoy": 27,
  "clientes_activos_7d": 140,
  "pendientes": 3
}
```

- `reservas_hoy.ok=false` si el backend de mesas no respondió (trae `error`).
- `pendientes`: alertas internas sin resolver (errores, escalaciones, etc.).

---

## Webhooks salientes (push)

El bot hace `POST` a la URL que configure la plataforma (`WEBHOOK_URL`) cada vez
que ocurre un evento. Son **best-effort**: si tu endpoint falla, el bot no se
bloquea (reintenta en el siguiente evento, no reencola el fallido).

### Cuerpo
```json
{
  "bot_id": "cantina-plus",
  "evento": "reserva.creada",
  "datos": { "tipo": "reserva_nueva", "mensaje": "🪑 Reserva nueva: ...", "cliente_numero": "+57..." },
  "ts": "2026-06-09T22:10:00+00:00"
}
```

### Headers
```
Content-Type: application/json
X-Cantina-Bot: cantina-plus
X-Cantina-Evento: reserva.creada
X-Cantina-Signature: sha256=<hmac>
```

### Eventos
| `evento`                | Cuándo                                              |
|-------------------------|-----------------------------------------------------|
| `reserva.creada`        | Nueva reserva (mesa, grupo o sala) desde el bot      |
| `comprobante.recibido`  | Cliente envió comprobante de cover (`datos.media_url`) |
| `escalacion`            | El bot escaló algo al equipo                         |
| `alerta`                | Error/alerta interna (p. ej. falló la IA)            |
| `bot.estado_cambiado`   | Se activó/desactivó el bot (`datos.activo`)          |

### Verificar la firma (HMAC-SHA256)
La firma se calcula sobre el **cuerpo crudo** (los bytes exactos recibidos) con
el secreto compartido `WEBHOOK_SECRET`:

```python
import hashlib, hmac

def firma_valida(raw_body: bytes, header_signature: str, secret: str) -> bool:
    esperado = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, header_signature)
```

> Importante: firma sobre el **cuerpo sin reparsear** (no vuelvas a serializar el
> JSON; usa el body tal cual llegó), o la firma no coincidirá.

---

## Configuración (lado del bot)

En el `.env` del bot:

```ini
BOT_ID=cantina-plus
CONTROL_API_KEY=<llave-secreta-para-entrada>
WEBHOOK_URL=https://tu-plataforma.com/webhooks/cantina   # opcional (push)
WEBHOOK_SECRET=<secreto-hmac-para-webhooks>              # opcional
```

- Sin `CONTROL_API_KEY` → la API de entrada responde `503` (deshabilitada).
- Sin `WEBHOOK_URL` → no se envían webhooks salientes (no-op).

---

## Códigos de error
| Código | Significado                                  |
|--------|----------------------------------------------|
| `401`  | Token ausente o inválido                     |
| `503`  | API no configurada (falta `CONTROL_API_KEY`) |
| `422`  | Body inválido en `POST /estado`              |
