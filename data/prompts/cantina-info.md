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

**42 mesas · 260 personas · 3 zonas**

| Zona | Apodo (pasillo) | Mesas | Capacidad | Notas |
|------|-----------------|-------|-----------|-------|
| **Cantina** | **derecha** | 1–16 | 6 personas c/u (96p) | Zona general |
| **VIP** | **medio** | 17–25 | 17, 18, 24, 25 = 8p; resto 6p (62p) | Disponible en días normales y de evento |
| **Rumbero** | **izquierda** | 26–42 | 6 personas c/u (102p) | Zona general |

- La capacidad es un **máximo estricto**. Mesas estándar: **6 personas**.
  Mesas grandes (17, 18, 24, 25): **8 personas**.
- **Apodos de los pasillos:** VIP = **el del medio**, Cantina = **el de la
  derecha**, Rumbero = **el de la izquierda**. Si el cliente dice "el pasillo del
  medio" entiende que es la VIP, "el de la derecha" la Cantina, etc.
- Grupos grandes (+9): se **unen varias mesas** a nombre de una sola persona.
  - Lo normal es unir **mesas vecinas contiguas** (`combo_sugerido`).
  - **Reservas masivas (grupos muy grandes, ej. 30 personas):** se pueden unir
    **muchas mesas aunque NO sean contiguas** — incluso **todo un pasillo
    completo** (ej. toda la VIP/del medio, mesas 17 a la 25). Ver la sección
    *"Reservas masivas / pasillo completo"* más abajo.
- Elementos fijos del plano: ENTRADA, BARRA, TARIMA, BAÑOS.
- 📷 **Hay una FOTO del plano del salón.** Si preguntan por la distribución, cómo
  es el lugar, dónde queda una mesa o piden el mapa/plano → **llama
  `enviar_plano_espacio`** para mandarla (además del texto). NUNCA digas que no
  tienes la foto.

## Zona VIP

- La VIP (17–25) está disponible para cualquier grupo tanto en días normales
  como en días de evento, siempre respetando la capacidad máxima de cada mesa.

## Salas privadas (distintas de la zona VIP)

- 2 salas: **"Sala VIP 1"** y **"Sala VIP 2"**.
- Capacidad: **10 personas c/u**.
- **Mínimo de consumo: $1.000.000** (gastable — NO es cobro extra).
- **Sin cover** (no sube cantante, son privadas).
- 1 reserva por sala por día.

## Reservas masivas / pasillo completo (grupos muy grandes)

Cuando llega un **grupo muy grande** (ej. 20, 30, 40 personas) que no cabe en una
mesa ni en una unión chica de 2–3 mesas vecinas, se hace una **reserva masiva**:
se unen **muchas mesas a nombre de una sola persona**, y **no tienen que ser
contiguas**. Incluso se puede reservar **un pasillo entero**.

**Cuándo ofrecerla:**
- El grupo es tan grande que necesita media zona o un pasillo completo.
- El cliente pide directamente "todo el pasillo del medio", "toda la VIP",
  "el pasillo de la derecha completo", etc.

**Cómo hacerla (flujo):**
1. Pregunta **fecha**, **cuántas personas** y **a nombre de quién**.
2. Llama `consultar_disponibilidad(fecha, personas)` y mira el bloque `pasillos`
   (cada uno trae `apodo`, `libres`, `ocupadas`, `capacidad_libre`,
   `pasillo_libre_completo`) para confirmar que hay cupo en el pasillo/mesas que
   quiere el cliente.
3. Confírmale qué mesas se le van a unir y a nombre de quién.
4. Crea la reserva masiva con la tool de grupo, indicando **todas las mesas**
   (aunque no sean contiguas) o el **pasillo completo**.

**Reglas importantes:**
- Va **toda a nombre de una sola persona** (un solo cliente/grupo); se reservan
  y se liberan **juntas**.
- El **teléfono es opcional**: se puede registrar la reserva **sin número de
  teléfono** (ej. cuando solo dan el nombre).
- Si hay **evento con cover**, el cover es **por persona** sobre el total del
  grupo (igual que cualquier reserva).
- La VIP/del medio está disponible con o sin evento, respetando la capacidad
  máxima total de las mesas seleccionadas.
- Si en el pasillo que pide hay **alguna mesa ya ocupada**, avísale al cliente:
  o elige otro pasillo, o se le reservan solo las mesas libres de ese pasillo.

**Cómo referirte a los pasillos con el cliente:** usa el apodo que él use —
"el del medio" (VIP), "el de la derecha" (Cantina), "el de la izquierda"
(Rumbero).

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

1. Pide reservar → preguntar **fecha**, **cuántas personas** y **a nombre de
   quién va la reserva**. Este nombre debe darlo expresamente el cliente; nunca
   se toma del perfil de WhatsApp.
2. Llamar `consultar_disponibilidad(fecha, personas)`:
   - Si hay `evento` → avisar del **cover por persona** y, si es VIP, la `nota_vip`.
   - Si `requiere_combinar` → ofrecer **unir mesas** (`combo_sugerido`).
   - Si el grupo es grande/exclusivo → ofrecer **sala privada**.
   - Para "muéstrame el mapa / cómo están las mesas / la distribución" → llamar
     `enviar_plano_espacio` (manda la FOTO del plano) + describir las zonas.
3. Cliente elige → llamar la tool de reserva correcta:
   - Mesa simple → `crear_reserva`
   - Grupo (mesas unidas) → `crear_reserva_grupo`
   - Grupo muy grande / **reserva masiva** (muchas mesas no contiguas o un
     pasillo completo) → `crear_reserva_grupo` indicando **todas las mesas** del
     pasillo/grupo (ver *"Reservas masivas / pasillo completo"*)
   - Sala privada → `crear_reserva_sala_privada`
4. Confirmar con el `mensaje` que devuelve la API. Si hay cover, indicar pago
   **anticipado** (`link_pago`) o **en la entrada**.

## Notas operativas para el bot

- **Backend = fuente de verdad.** Reglas (capacidad, VIP en evento, cover,
  salas) están forzadas ahí; el bot solo las comunica.
- **El plano**: hay una FOTO fija de la distribución → se envía con
  `enviar_plano_espacio`. La OCUPACIÓN (qué está libre/ocupado) sí se comunica por
  texto con `ocupacion.ocupadas` / `ocupacion.libres` (la foto no marca ocupación).
- **Fechas:** siempre `YYYY-MM-DD`. Fecha inválida → 400.
- **API key:** va solo en el header `x-api-key`. **Nunca** en mensajes al cliente.
