# API Backend — `cantina-api.dtgrowthpartners.com`

Referencia completa de la API REST del backend de mesas. Fuente original:
`botplatform/cantina-bot-info.md`.

> El cliente HTTP listo en Python está en
> [`app/integrations/cantina_api.py`](../app/integrations/cantina_api.py). Las
> tools que ve Claude lo consumen.

## 🔑 Conexión

| Dato | Valor |
|------|-------|
| **Base URL** | `https://cantina-api.dtgrowthpartners.com` |
| **Auth header** | `x-api-key: 0913915748ca4548aedbb3923cbd7bca` |
| **Endpoint sin auth** | `GET /api/health` |
| **Process** | pm2 `cantina-mesas`, puerto `3071`, VPS `149.56.133.201` |
| **Stack** | Node 24 + Express + `node:sqlite` (DB: `data/cantina.db`, WAL) |

> ⚠️ **API key no se expone al cliente final.** Va solo en el header.

**Forma de respuesta:**
- Éxito: `{ "ok": true, ... }`
- Error: `{ "ok": false, "error": "..." }`
- Códigos: `400` validación · `401` auth · `404` no encontrado · `409` doble reserva · `500` interno

## Endpoints (resumen)

### Health
| Método | Ruta | Auth | Función |
|--------|------|------|---------|
| GET | `/api/health` | No | Healthcheck (`{ok, servicio, ts}`). |

### Catálogo
| Método | Ruta | Función |
|--------|------|---------|
| GET | `/api/mesas` | Lista todas las mesas (`{id, numero, capacidad, zona, activa}`). |
| GET | `/api/layout` | `canvas`, `zonas`, `fijos` (ENTRADA/BARRA/TARIMA/BAÑOS), coords mesas. Para dibujar el mapa. |
| GET | `/api/plano` | URL de la imagen estática del plano (`{ok, plano_url}`). `404` si no se subió aún. |

### Disponibilidad ★ (llamada principal del bot)
**`GET /api/disponibilidad?fecha=YYYY-MM-DD&personas=N`**

Devuelve TODO el contexto del día:

| Campo | Función |
|-------|---------|
| `evento` (or `null`) | `{nombre, artista, tiene_cover, valor_cover, link_pago}`. |
| `plano_url` | Foto del plano. |
| `ocupacion.ocupadas[]` / `libres[]` | Mapa real **sin filtrar**. |
| `mesas_disponibles[]` + `total_disponibles` | **Filtrado** por capacidad máxima. **Úsalo después de tener el # personas.** |
| `requiere_combinar` (bool) + `combo_sugerido` + `combos[]` | Si no cabe en una sola mesa. |
| `nota_vip` | Campo legacy; actualmente siempre es `null`. |
| `salas_privadas.disponibles[]` | Salas libres ese día. |

**Auxiliar de combos:** `GET /api/combos?fecha&personas` → `{sugerido, combos}`.

### Reservas de mesa

| Método | Ruta | Función |
|--------|------|---------|
| POST | `/api/reservas` | Reserva una mesa simple. Auto-aplica cover si hay evento. |
| POST | `/api/reservas/grupo` | Reserva grupo sobre varias mesas vecinas (valida contigüidad). |
| GET | `/api/reservas?fecha=YYYY-MM-DD` | Listar reservas del día. `&incluir_canceladas=true` opcional. |
| GET | `/api/reservas/:id` | Detalle. |
| PATCH | `/api/reservas/:id` | Editar `cover_estado`, `estado`, `notas`, `num_personas`, `nombre_cliente`, `telefono`, `monto_cover`. (Cambiar `num_personas` NO reasigna/valida la mesa.) |
| DELETE | `/api/reservas/:id` | Cancelar (soft delete → `cancelada`, libera mesa). |
| DELETE | `/api/reservas/grupo/:grupoId` | Liberar grupo completo. |

**Body POST `/api/reservas`:**
```json
{
  "fecha": "2026-06-10",
  "mesa_id": 12,
  "nombre_cliente": "Juan Pérez",
  "telefono": "+57 300 1234567",
  "num_personas": 4,
  "notas": ""
}
```
Obligatorios: `fecha`, `nombre_cliente`, `num_personas` (≥1). `409` si la mesa ya está reservada esa fecha.

**Body POST `/api/reservas/grupo`:**
```json
{
  "fecha": "2026-06-10",
  "mesa_numeros": [19, 18],
  "nombre_cliente": "Ana Gómez",
  "telefono": "+57 300 7654321",
  "num_personas": 8,
  "notas": ""
}
```

### Salas privadas

| Método | Ruta | Función |
|--------|------|---------|
| GET | `/api/salas` | Lista salas privadas. |
| GET | `/api/salas/disponibilidad?fecha=...` | Estado de cada sala. |
| GET | `/api/reservas-salas?fecha=...` | Reservas de salas del día. |
| POST | `/api/reservas-salas` | Reservar sala. |
| PATCH | `/api/reservas-salas/:id` | Editar. |
| DELETE | `/api/reservas-salas/:id` | Cancelar. |

**Body POST:**
```json
{
  "fecha": "2026-06-10",
  "sala_id": 1,
  "nombre_cliente": "Carlos Ruiz",
  "telefono": "+57 300 1112222",
  "num_personas": 10,
  "notas": ""
}
```
Mínimo de consumo $1.000.000. 1 reserva por sala por día, máx 10p.

### Eventos / Covers

| Método | Ruta | Función |
|--------|------|---------|
| GET | `/api/eventos?fecha=YYYY-MM-DD` | Evento del día. |
| POST | `/api/eventos` | Crear/actualizar (`fecha`, `nombre` obligatorios; `artista`, `tiene_cover`, `valor_cover`, `link_pago`). |
| DELETE | `/api/eventos/:fecha` | Apagar modo evento (las reservas se conservan). |

### Resumen del día (equipo)

| Método | Ruta | Función |
|--------|------|---------|
| GET | `/api/resumen?fecha=YYYY-MM-DD` | Reservas, conteos, recaudo de cover (esperado/anticipado/en_entrada/pendiente) y salas. **Para el equipo, NO para clientes.** |

### CRUD mesas (admin)

| Método | Ruta | Función |
|--------|------|---------|
| POST | `/api/mesas` | Crear mesa (`numero`, `capacidad`, `zona`). |
| PATCH | `/api/mesas/:id` | Editar. |
| DELETE | `/api/mesas/:id` | Eliminar. Bloquea si hay reservas — usa `activa=0`. |

## Estados internos

| Campo | Valores | Notas |
|-------|---------|-------|
| `reservas.estado` | `confirmada` · `cancelada` | Soft delete via `cancelada`. |
| `reservas.cover_estado` | `no_aplica` · `pendiente` · `anticipado` · `en_entrada` | `no_aplica` si el día no tiene evento. |

## Reglas que el backend YA fuerza

El bot NO necesita validar estas — el backend devuelve `400`/`409` con
mensaje claro:

- Capacidad máxima por mesa.
- Mesas estándar: máximo 6 personas; mesas 17, 18, 24 y 25: máximo 8.
- Reservas combinadas: `num_personas` no puede superar la capacidad total.
- Salas privadas: mínimo $1.000.000 (gastable), sin cover, máx 10p.
- Cover por persona = `valor_cover × num_personas` (automático).
- Índice único: una mesa no puede tener dos reservas la misma fecha.

## Errores comunes

| Caso | Status | Mensaje típico |
|------|--------|----------------|
| API key faltante/incorrecta | 401 | `{"ok": false, "error": "API key inválida o ausente."}` |
| Fecha inválida | 400 | `{"ok": false, "error": "fecha inválida"}` |
| Mesa ya reservada esa fecha | 409 | `{"ok": false, "error": "mesa ocupada"}` |
| Personas por encima de la capacidad | 400 | Error con la capacidad máxima y sugerencia de combinar mesas |
| Sala con personas >10 | 400 | error claro |

## Cómo se usa desde el bot

```python
from app.integrations import cantina_api

# Consultar disponibilidad
res = await cantina_api.disponibilidad("2026-06-10", personas=4)
if not res["ok"]:
    return "Tuve un problema consultando la agenda — escríbenos en un momento."

# Crear reserva
res = await cantina_api.crear_reserva({
    "fecha": "2026-06-10",
    "mesa_id": 12,
    "nombre_cliente": "Pedro",
    "telefono": "+573001112233",
    "num_personas": 4,
})
if res["ok"]:
    # Usar res["mensaje"] como confirmación al cliente
    ...
```

Cuando algo falla, el handler de la tool devuelve el `error` y Claude
decide cómo continuar la conversación (sin exponer detalles internos
como la URL/key al cliente).
