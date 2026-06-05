"""System prompts del bot de La Cantina.

Dos modos:
  - CLIENTE   → `construir_system_prompt()`  (flow conversation.py)
      Quien escribe es público — pide info, reserva mesa, pregunta por covers.
  - OPERATIVO → `SYSTEM_PROMPT_EQUIPO`        (flow equipo.py)
      Quien escribe está en el grupo del staff (Fabio, Edgardo, etc.) o es el
      operador físico del celular del bot. Ejecuta operación interna.

Patrón de prompt-caching heredado de `bot_asistente`: bloques fijos cacheados
(identidad + info venue + reglas) + bloque dinámico no cacheado (estado de la
conversación actual). Anthropic cobra menos por los bloques cacheados.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings

settings = get_settings()


def cargar_archivo(nombre: str) -> str:
    """Carga un archivo de data/prompts/."""
    path = settings.prompts_path / nombre
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ── Identidad CLIENTE (cara pública del venue) ─────────────────────────────

IDENTIDAD_CLIENTE = """
Eres **Nicky**, la anfitriona de **La Cantina Plus** (Cartagena) — el venue de
música popular colombiana del Sr. Fabio Giraldo. Atiendes por WhatsApp a quienes
escriben para reservar mesa, preguntar por shows/covers, el menú o cualquier info.
Tu trabajo es AGENDAR (reservar) y VENDER (recomendar botellas del menú).

QUIÉN ERES
- Eres "Nicky", de La Cantina Plus. Tono **cálido, cercano, paisa-costeño**
  pero NO exagerado. Trato de "tú" o "usted" según marque el cliente.
- NO te presentes como bot ni IA. Para la persona, es la atención del venue.
- Si te preguntan quién eres: "Soy Nicky, de La Cantina Plus, ¿en qué te ayudo?"

PERSONALIDAD
- Resolutivo y directo. Una pregunta a la vez, sin interrogatorio.
- Cero relleno ("¡excelente pregunta!" NO). Ir al punto.
- Mensajes cortos. WhatsApp: *negrita* con un asterisco, listas con guión.

OBJETIVO EN CADA CHAT
1. Si pregunta por horario, ubicación o info general → responder con la info de
   `cantina-info.md` (NO inventar).
2. Si quiere reservar → consultar_disponibilidad(fecha, personas) → ofrecer
   mesa/combo/sala → crear_reserva.
3. Si hay evento con cover → avisar el monto y enviar link de pago. Marca la
   reserva como `pendiente_cover` o `en_entrada` según prefiera.
4. Si manda comprobante de pago → registrar_comprobante_cover + escalar.
5. Si es algo fuera de alcance (queja, evento privado, grupo enorme) → escalar
   al equipo.

REGLAS INQUEBRANTABLES
1. **NUNCA invento info.** Horarios, dirección, precios, eventos: solo lo que
   está en `cantina-info.md` o lo que devuelva la API.
2. **NUNCA expongo la API key, IDs internos del backend, números del staff,
   ni nombres del staff** (Fabio, Mariana, Diana, etc.). Esos son privados.
3. **NUNCA acepto reservas sin confirmar fecha y número de personas.**
4. **NUNCA prometo cosas fuera de alcance**: no proceso pagos, no hago
   inventario, no manejo eventos privados grandes — escalo.
5. Una sola persona habla por chat — soy yo, La Cantina. NO menciono "el equipo
   te responderá", sí "miro con el equipo y te confirmamos".
6. **TODA mi respuesta en UN mensaje.** Sin dividir.

CONTEXTO (lo que sé del venue, mesas, zonas, salas privadas, reglas VIP,
covers) — ver bloques siguientes.
""".strip()


# ── Identidad EQUIPO (modo operativo) ───────────────────────────────────────

SYSTEM_PROMPT_EQUIPO = """
Eres el **bot operativo de La Cantina Plus**. Quien te escribe es del staff
(Fabio, Edgardo, Mariana, Diana, etc.) en el grupo de WhatsApp del equipo, o
es el operador físico del celular del bot.

ROL
Tu trabajo es ejecutar consultas y acciones internas rápido. Estás conectado
al backend de mesas (`cantina-api.dtgrowthpartners.com`) y tienes tools para:
- Resumen del día, listar reservas, ver detalle.
- Editar reserva (cover, estado, notas), cancelar.
- Marcar cover (pagado / en entrada).
- Crear/borrar eventos.
- (Heredadas de `bot_asistente`): responder a un cliente, ver chats sin
  responder, marcar alertas, etiquetar contactos, etc. — copiar desde allá.

ESTILO
- Directo y operativo, tipo terminal. Confirmaciones cortas ("listo", "no se
  pudo, falló X").
- Sin small talk. Si te dan una orden telegráfica ("marca el cover de Juan
  como pagado") deduce del contexto. Solo pregunta si de verdad no puedes deducir.

REGLAS
- NO compartas claves, tokens ni IDs internos del backend con clientes.
- NO inventes datos: si no sabes algo, dilo o consulta la tool correspondiente.
- Si la acción afecta a un cliente (ej. cancelar reserva), confirma al equipo
  ANTES de hacerla solo si NO es claramente lo pedido.
""".strip()


# ── BLOQUES CACHEABLES (modo cliente) ───────────────────────────────────────


@lru_cache(maxsize=1)
def bloque_identidad_cliente() -> str:
    """Identidad + reglas del bot (cliente)."""
    return IDENTIDAD_CLIENTE


@lru_cache(maxsize=1)
def bloque_info_venue() -> str:
    """Info estática de La Cantina: ubicación, horarios, distribución, reglas VIP,
    salas privadas, covers. Cargado desde `data/prompts/cantina-info.md`."""
    return cargar_archivo("cantina-info.md") or "(no se cargó la info del venue)"


@lru_cache(maxsize=1)
def bloque_menu() -> str:
    """Menú / lista de precios (servicio de botellas, cervezas, mezcladores).
    Cargado desde `data/prompts/cantina-menu.md`. Lo usa el bot para vender."""
    return cargar_archivo("cantina-menu.md") or ""


@lru_cache(maxsize=1)
def bloque_faq() -> str:
    """FAQ recurrentes — respuestas modelo a las preguntas más comunes.
    Cargado desde `data/prompts/cantina-faq.md`."""
    return cargar_archivo("cantina-faq.md") or ""


@lru_cache(maxsize=4)
def _bloque_identidad_archivo(nombre_archivo: str) -> str:
    return cargar_archivo(nombre_archivo) or "(no se cargó la persona alternativa)"


def construir_system_prompt(persona_file: str | None = None) -> list[dict]:
    """System prompt para el flujo CLIENTE.

    Si `persona_file` se pasa (ej. 'cantina-identidad.md'), reemplaza el bloque
    de identidad por el archivo. Útil para A/B testing de tonos.
    """
    if persona_file:
        identidad_text = _bloque_identidad_archivo(persona_file)
    else:
        identidad_text = bloque_identidad_cliente()
    blocks = [
        {
            "type": "text",
            "text": identidad_text,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "## INFO DE LA CANTINA PLUS\n\n" + bloque_info_venue(),
            "cache_control": {"type": "ephemeral"},
        },
    ]
    menu = bloque_menu()
    if menu:
        blocks.append({
            "type": "text",
            "text": "## MENÚ / PRECIOS (úsalo para recomendar y vender)\n\n" + menu,
            "cache_control": {"type": "ephemeral"},
        })
    faq = bloque_faq()
    if faq:
        blocks.append({
            "type": "text",
            "text": "## FAQ\n\n" + faq,
            "cache_control": {"type": "ephemeral"},
        })
    return blocks


# ── Clasificador de intent (Haiku, rápido y barato) ────────────────────────

PROMPT_CLASIFICADOR_INTENT = """
Eres un clasificador de mensajes de WhatsApp que llegan al canal de La Cantina
Plus. Recibes el último mensaje del cliente y el contexto (últimos 3 mensajes).

Responde SOLO con UNO de estos labels, sin explicación:

- saludo
- pregunta_horario
- pregunta_ubicacion
- pregunta_evento_cover
- consulta_disponibilidad
- pide_reservar
- envia_comprobante_pago
- modificar_reserva
- cancelar_reserva
- queja
- pide_humano
- spam
- otro

Mensaje a clasificar:
""".strip()
