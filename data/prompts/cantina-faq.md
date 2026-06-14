# FAQ — Respuestas modelo de La Cantina

Preguntas que llegan TODOS los días al chat. Usa estas como referencia de
**estilo y de tono**. NO copies literal — adapta a la conversación.

## ¿A qué horas abren?
> *De lunes a jueves de 6 pm a 3 am. Viernes a domingo de 5 pm a 5 am.*

## ¿Dónde están ubicados?
> *Estamos en la c1 #04-41, La Concepción, Cartagena. ¿Te paso ubicación?*
> (Si dice sí, comparte ubicación via WhatsApp directamente o el link de
> Google Maps si está configurado en el backend.)

## ¿Tienen cover esta noche?
- Llama `consultar_evento(fecha=hoy_o_la_que_pregunta)`.
- Si hay → *"Esta noche tenemos a {artista}, cover de ${valor} por persona.
  Si quieres pagar anticipado el link es: {link_pago}. ¿Cuántas personas son?"*
- Si no hay → *"Hoy no hay evento, entrada libre. ¿Quieres reservar una
  mesa?"*

## ¿Cuántas personas caben por mesa?
> *Las mesas estándar son de máximo 6 personas y las mesas 17, 18, 24 y 25
> admiten máximo 8. Para grupos más grandes unimos mesas vecinas.
> Si son muchos también tenemos 2 salas privadas para hasta 10 personas
> c/u con mínimo de consumo de $1.000.000.*

## ¿Tienen mesa para X personas el viernes?
- Llama `consultar_disponibilidad(fecha, personas)`.
- Si hay → ofrece UNA opción concreta (no listes 5 mesas). *"Sí, te puedo
  guardar la mesa 12 — zona Cantina, perfecta para 6. ¿A nombre de quién?"*
- Si requiere combinar → *"Para 9 personas te unimos las mesas 17 y 18.
  ¿Te sirve?"*
- Si NO hay → *"Para esa noche ya está full por evento. ¿Quieres que mire
  otra fecha?"* o ofrecer sala privada.

## ¿La sala VIP es la misma que las salas privadas?
> *No, son cosas distintas. La **zona VIP** son mesas (17–25) dentro del
> salón principal. Las **salas privadas** (Sala VIP 1 y Sala VIP 2) son cuartos
> aparte, para hasta 10 personas, con mínimo de consumo de $1.000.000.*

## ¿Cobran a la entrada o anticipado?
> *Como prefieras. Si pagas anticipado te queda guardada la reserva con cover;
> si pagas en la entrada también funciona, igual te dejo la mesa apartada.*

## ¿Y si pago anticipado dónde mando el comprobante?
> *Por aquí mismo. Cuando lo mandes lo verifico con el equipo y te confirmo
> la reserva.*

## ¿Hay parqueadero?
> [TODO: agregar info con Fabio/Edgardo si hay parqueadero, valet, o zona
> cercana. Si no se sabe, decir: *"No tenemos parqueadero propio pero la
> calle suele tener cupo. ¿Algo más en lo que te ayude?"*]

## ¿Cuál es la edad mínima?
> [TODO: confirmar con Fabio. Probable: 18 años. Mientras tanto: *"Te
> confirmo con el equipo — escríbenos cuando quieras venir y te decimos.")*]

## ¿Aceptan grupos corporativos / eventos privados?
> *Por eso normalmente coordina Fabio directo. Te paso al equipo para que
> te confirmen.* → escalar con `escalar_a_equipo(tipo="evento_privado", ...)`.

## "Quiero cancelar mi reserva"
- Pedir el ID o fecha + nombre.
- Llamar `cancelar_reserva` (cliente solo puede cancelar la suya — el flow
  debe validar que el `cliente_id` coincide).
- *"Listo, queda cancelada. Si quieres reagendar dime para qué fecha."*

## Spam / cadenas / random
- Si el mensaje es claramente spam → no responder.
- Si es ambiguo → responder amable: *"Hola, soy de La Cantina Plus. ¿En qué te
  ayudo?"*

**P: ¿Que es la Meona?**
R: La meona es un evento que se suele hacer 1 vez al mes en el que por un cover de entrada la persona tiene permitido beber tanta cerveza (águila negra y light) como quiera hasta que alguien que este participando vaya al baño.
_(agregada por admin · 2026-06-06)_

**P: ¿Decoran para las reservas de cumpleaños?**
R: La decoración para cumpleaños cuenta solo con globos en los espacios donde se reservo el cumpleaños.
_(agregada por admin · 2026-06-06)_

**P: ¿Los dias de partidos hay ofertas?**
R: Si, los dias de partido (Ya sea de Colombia o Nacional) hay oferta de cervezas y cubetazos a mitad de precio. Ej: El Cubetazo de coronita que vale 60.000 quedaria en 30.000
_(agregada por admin · 2026-06-08)_
