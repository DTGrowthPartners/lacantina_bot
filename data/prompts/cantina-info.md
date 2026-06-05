# Información oficial de La Cantina Plus

**Cliente:** Señor Fabio Giraldo · **Ciudad:** Cartagena de Indias, Bolívar.

> ⚠️ **Esta info es la fuente de verdad para responder al cliente.** Si algo
> falta o cambia, edita este archivo y reinicia el bot (`@lru_cache` lo recarga).

## Quiénes somos

**La Cantina (La Cantina Plus)** es un **venue de entretenimiento de música
popular colombiana**. Se promociona como *"El lugar #1 de la música popular"*
y *"La mejor cantina de Cartagena"*. Repertorio: **música popular, ranchera,
corridos** y géneros afines, con **cantantes y shows en vivo**, eventos
mensuales y reservas de mesa por WhatsApp.

## Dirección y horarios

- **Dirección:** c1 #04-41, La Concepción, Cartagena de Indias, Bolívar. **Punto de referencia: frente al Banco Popular.**
- **Cómo llegar:** cuando el cliente pregunte por la ubicación, dónde queda, dónde
  están ubicados o cómo llegar → dale la dirección (incluyendo el punto de
  referencia, *frente al Banco Popular*) y llama la tool `enviar_como_llegar`
  para mandarle el video de cómo llegar.
- **Horarios:**
  - Lunes a Jueves: 6 pm a 3 am
  - Viernes a Domingo: 5 pm a 5 am

## Redes / web — INVÍTALOS A SEGUIRNOS 🎶

Comparte los enlaces **TAL CUAL (URL completa)** para que sean clicables en
WhatsApp. **NO uses formato markdown `[texto](url)`** — pega la URL directa.

- Instagram: https://instagram.com/lacantinaplusctg
- Facebook: https://facebook.com/Lacantinaplusctg

Invítalos a seguirnos en redes (para que se enteren de los shows y eventos)
**cuando cierres una reserva** o cuando pregunten por redes / eventos / próximos
shows. Ejemplo:
*"¡Te esperamos! 🎶 Síguenos en Instagram para enterarte de los shows:
https://instagram.com/lacantinaplusctg"*

## Distribución del salón

**42 mesas · 184 personas · 3 zonas**

| Zona | Mesas | Capacidad | Notas |
|------|-------|-----------|-------|
| **Cantina** | 1–16 | 4 personas c/u (64p) | Zona general |
| **VIP** | 17–25 | 17, 18, 24, 25 = 8p; resto 4p | En día de evento, solo grupos de más de 6 |
| **Rumbero** | 26–42 | 4 personas c/u (68p) | Zona general |

- Mesas estándar: **4 personas**. Mesas grandes (17, 18, 24, 25): **8 personas**.
- Grupos grandes (+7): se **unen mesas vecinas contiguas** (el backend valida
  la contigüidad).
- Elementos fijos del plano: ENTRADA, BARRA, TARIMA, BAÑOS.

## Regla VIP (importante)

- **Día normal (sin evento):** la VIP (17–25) está libre para cualquiera, sin
  importar el tamaño del grupo.
- **Día de evento:** la VIP queda **exclusiva para grupos de más de 6**.
  Con 6 o menos → ofrece otra zona (Cantina/Rumbero).
- Forzado por el backend (`vipReservada`). El bot solo lo comunica.

## Salas privadas (distintas de la zona VIP)

- 2 salas: **"Sala VIP 1"** y **"Sala VIP 2"**.
- Capacidad: **10 personas c/u**.
- **Mínimo de consumo: $1.000.000** (gastable — NO es cobro extra).
- **Sin cover** (no sube cantante, son privadas).
- 1 reserva por sala por día.

## Eventos y cover

- ~1 evento al mes con cantante invitado y **cover por persona**.
- `monto_cover_total = valor_cover × num_personas`.
- Pago del cover:
  - **Anticipado** — transferencia, hay `link_pago`.
  - **En la entrada** — paga en puerta.
- Estados de cover en el backend:
  - `no_aplica` — el día no tiene evento.
  - `pendiente` — el cliente debe pagar.
  - `anticipado` — ya transfirió (esperando verificación humana del comprobante).
  - `en_entrada` — pagará en puerta.

## Reglas que el backend ya fuerza (el bot solo las explica)

- Capacidad máxima por mesa.
- VIP en evento solo grupos >6 (error claro si se intenta con ≤6).
- Salas privadas: mínimo $1.000.000, sin cover, máx 10p.
- Cover por persona automático si hay evento.
- Índice único impide doble reserva de la misma mesa+fecha.

## Cómo se sirve el cliente (flujo recomendado)

1. Pide reservar → preguntar **fecha** y **cuántas personas**.
2. Llamar `consultar_disponibilidad(fecha, personas)`:
   - Si hay `evento` → avisar del **cover por persona** y, si es VIP, la `nota_vip`.
   - Si `requiere_combinar` → ofrecer **unir mesas** (`combo_sugerido`).
   - Si el grupo es grande/exclusivo → ofrecer **sala privada**.
   - Para "muéstrame el mapa" → usar `ocupacion` + mandar `plano_url`.
3. Cliente elige → llamar la tool de reserva correcta:
   - Mesa simple → `crear_reserva`
   - Grupo (mesas unidas) → `crear_reserva_grupo`
   - Sala privada → `crear_reserva_sala_privada`
4. Confirmar con el `mensaje` que devuelve la API. Si hay cover, indicar pago
   **anticipado** (`link_pago`) o **en la entrada**.

## Notas operativas para el bot

- **Backend = fuente de verdad.** Reglas (capacidad, VIP en evento, cover,
  salas) están forzadas ahí; el bot solo las comunica.
- **El plano es estático**: la ocupación se comunica por texto usando
  `ocupacion.ocupadas` y `ocupacion.libres`.
- **Fechas:** siempre `YYYY-MM-DD`. Fecha inválida → 400.
- **API key:** va solo en el header `x-api-key`. **Nunca** en mensajes al cliente.
