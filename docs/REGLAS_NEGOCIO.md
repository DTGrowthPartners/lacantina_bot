# Reglas de Negocio — La Cantina Plus

Toda la info operativa que el bot necesita conocer del venue. Si esto cambia,
también hay que actualizar [`data/prompts/cantina-info.md`](../data/prompts/cantina-info.md)
que es lo que ve Claude en el system prompt.

## El venue

- **Nombre:** La Cantina Plus
- **Dueño:** Señor Fabio Giraldo
- **Ciudad:** Cartagena de Indias, Bolívar
- **Dirección:** c1 #04-41, La Concepción
- **Tipo:** Venue de entretenimiento de música popular colombiana
- **Promoción:** *"El lugar #1 de la música popular"* / *"La mejor cantina de Cartagena"*

## Horarios

| Días | Horario |
|------|---------|
| Lunes a Jueves | 6 pm a 3 am |
| Viernes a Domingo | 5 pm a 5 am |

## Distribución del salón

**Total: 42 mesas · 260 personas · 3 zonas**

| Zona | Mesas | Capacidad | Notas |
|------|-------|-----------|-------|
| Cantina | 1–16 | 6p c/u (96p total) | Zona general |
| VIP | 17–25 | 17, 18, 24, 25 = 8p; resto 6p (62p total) | Disponible con o sin evento |
| Rumbero | 26–42 | 6p c/u (102p total) | Zona general |

- Mesas estándar: **6 personas máximo**.
- Mesas grandes (17, 18, 24, 25): **8 personas**.
- Grupos +9: unión de mesas vecinas contiguas.
- Elementos fijos: ENTRADA, BARRA, TARIMA, BAÑOS.

## Zona VIP

La VIP está disponible para cualquier grupo con o sin evento. El backend
únicamente exige respetar la capacidad máxima de las mesas seleccionadas.

## Salas privadas (distintas de la zona VIP)

| Sala | Capacidad | Mínimo de consumo | Cover |
|------|-----------|-------------------|-------|
| Sala VIP 1 | 10p | $1.000.000 (gastable) | NO |
| Sala VIP 2 | 10p | $1.000.000 (gastable) | NO |

- "Mínimo de consumo gastable" = no es cobro extra; el cliente consume esa
  cantidad en el venue (comida + bebidas).
- 1 reserva por sala por día.

## Eventos y covers

- ~1 evento al mes con cantante invitado.
- Cover **por persona** (`monto_total = valor_cover × num_personas`).
- El cover aplica desde la **hora de inicio del evento** (`hora_inicio`); quienes
  ingresan desde esa hora pagan cover, y quienes ingresaron antes pero se quedan
  durante el evento también deben pagarlo.
- Quien entra antes y no desea pagar cover puede quedarse sin pagar solo hasta
  que empiece el evento; debe retirarse antes de que empiece.
- Pago:
  - **Anticipado** (transferencia, hay `link_pago`).
  - **En la entrada** (paga en puerta).

### Estados de cover (en BD)

| Valor | Significado |
|-------|-------------|
| `no_aplica` | Día sin evento. |
| `pendiente` | El cliente debe pagar el cover. Default si hay evento. |
| `anticipado` | Cliente envió comprobante. Pendiente verificación humana. |
| `en_entrada` | Cliente eligió pagar en puerta. |

### Flujo cover

1. Cliente reserva en fecha con evento → backend marca `cover_estado=pendiente`.
2. Bot avisa monto + link de pago, pregunta si paga anticipado o en entrada.
3. Si anticipado:
   - Cliente paga, manda comprobante.
   - Bot llama `registrar_comprobante_cover(reserva_id, url)`.
   - Cover queda `anticipado`.
   - **Equipo verifica desde el grupo o admin** → marca como pagado.
4. Si en entrada:
   - Bot llama tool para marcar `cover_estado=en_entrada`.
   - Reserva queda confirmada.

### Política liberación

Si una reserva con cover `pendiente` no se paga en N horas (configurable),
el cron `liberar_pendientes_cover` libera la mesa. Default sugerido: 24 h.

## Tabla de staff (no son clientes)

Lista oficial — estos números **NO** los responde el bot como cliente. Van a
`equipo_miembros` (con rol "equipo") o `numeros_internos` (interno general).

| Nombre | Número | Rol | Categoría |
|--------|--------|-----|-----------|
| La Cantina Plus | +57 300 8641322 | 🤖 Bot | (no aplica — es el propio bot) |
| Mariano Mesa | +57 322 6616509 | 👤 Admin | interno |
| Santiago Giraldo | +57 312 2708650 | 🎤 Cantante | interno |
| Alejo Palacio | +57 311 7681858 | 🎤🧾 Cantante / Cajero | interno |
| Diana Cantina | +57 301 1062354 | 🍽️ Mesera | interno |
| Jordi | +57 301 7866478 | 🔒 Guardia | interno |
| Mariana Cantina | +57 321 5098448 | 🍽️ Mesera | interno |
| **Señor Fabio** | +57 313 5861776 | 👑 Dueño | **equipo** (fallback) |
| Dana | +57 301 3364137 | 🍽️ Mesera | interno |
| Brilly Giraldo | +57 324 4824083 | 🎤 Cantante | interno |
| Nicol Giraldo | +57 311 6378746 | 🎤 Cantante | interno |
| **Edgardo** | +57 311 6123189 | 💻 Desarrollador | **equipo** |

- **equipo**: tienen acceso al flow operativo (pueden pedirle al bot consultar
  reservas, marcar covers, etc.). Estarán en el grupo de WhatsApp del equipo.
- **interno**: el bot solo los silencia. Si escriben no se les responde como
  cliente. Si quieren operar el bot, deben usar el grupo del equipo.

Seed inicial: `scripts/seed_staff.py` (a crear — ver ROADMAP Paso 7).

## Lo que el bot NO hace (alcance explícito)

- ❌ No procesa pagos (solo envía link + registra comprobante).
- ❌ No se integra con Logro (software interno del venue).
- ❌ No maneja inventario de barra / cocina.
- ❌ No toma reservas grupales corporativas / eventos privados → escala.
- ❌ No publica en Instagram ni en el feed.
- ❌ No atiende los otros negocios de Fabio.

## KPIs propuestos (post go-live)

- Tiempo de primera respuesta < 30 s.
- % conversaciones resueltas sin escalación: > 75%.
- # reservas creadas / mes vs canceladas / no-show.
- # covers anticipados (señal de éxito del flow de pago).
- Alertas abiertas > 4 h (si crece, ajustar).
- Costo Claude / mes y por conversación.
