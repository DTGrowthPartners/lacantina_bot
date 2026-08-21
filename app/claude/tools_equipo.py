"""Tools del flujo EQUIPO (Fabio, Edgardo, staff) — operación interna.

Se invocan desde el grupo WhatsApp del equipo. Cubren:
- Resumen del día (reservas, covers, recaudo)
- Buscar / editar / cancelar reservas
- Marcar covers (pagado anticipado / en entrada / pendiente / invitado)
- Crear o editar eventos del día

Todas las tools golpean el backend de mesas (`cantina_api.py`).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Cliente, Conversacion
from app.event_media import guardar_descripcion, guardar_flyer
from app.eventos import clave_orden_evento, extraer_eventos
from app.integrations import cantina_api
from app.logging_setup import log

_PLANO = Path(get_settings().data_dir) / "media" / "plano-espacio.png"
_HORA_APERTURA_LUN_JUE = "18:00"
_HORA_APERTURA_VIE_DOM = "17:00"


def _telefono_canonico(raw: str) -> str:
    value = (raw or "").strip().replace(" ", "")
    if value.endswith("@lid"):
        return value
    digitos = "".join(ch for ch in (raw or "") if ch.isdigit())
    if not digitos:
        return ""
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if digitos.startswith("57") and len(digitos) >= 12:
        return "+" + digitos[:12]
    if len(digitos) == 10:
        return "+57" + digitos
    return "+" + digitos


def _ultimos_10(raw: str) -> str:
    if (raw or "").strip().endswith("@lid"):
        return (raw or "").strip()
    digitos = "".join(ch for ch in (raw or "") if ch.isdigit())
    return digitos[-10:]


def _schema_evento_desde_estado() -> dict:
    return {
        "evento_fecha": {
            "type": "string",
            "description": (
                "YYYY-MM-DD del evento/promocion que aparece en el flyer. "
                "No es la fecha de publicacion del estado."
            ),
        },
        "evento_nombre": {
            "type": "string",
            "description": "Nombre/titulo del evento extraido del flyer.",
        },
        "evento_artista": {
            "type": "string",
            "description": "Artista, partido o protagonista del evento si aparece.",
        },
        "evento_hora_inicio": {
            "type": "string",
            "description": (
                "Hora de inicio del evento en HH:MM 24h. Si el flyer no muestra hora, "
                "omite este campo y el bot usara la hora normal de apertura."
            ),
        },
        "evento_tiene_cover": {
            "type": "boolean",
            "description": "true si el flyer indica cover o precio de entrada.",
        },
        "evento_valor_cover": {
            "type": "integer",
            "description": "Valor del cover en COP si aparece.",
        },
        "evento_link_pago": {
            "type": "string",
            "description": "Link de pago si el equipo lo escribio junto al flyer.",
        },
        "evento_descripcion": {
            "type": "string",
            "description": "Descripcion corta extraida del flyer para recordar detalles.",
        },
    }


TOOL_DEFINITIONS_EQUIPO: list[dict] = [
    {
        "name": "marcar_casa_llena",
        "description": (
            "Cierra nuevas reservas para una fecha. Úsala cuando el equipo diga "
            "'casa llena', 'estamos llenos', 'no acepten más reservas' o equivalente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "YYYY-MM-DD; si no se indica, usa hoy.",
                },
                "motivo": {"type": "string"},
            },
        },
    },
    {
        "name": "reabrir_reservas",
        "description": (
            "Vuelve a permitir reservas para una fecha cerrada por casa llena."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "YYYY-MM-DD; si no se indica, usa hoy.",
                },
            },
        },
    },
    {
        "name": "enviar_plano_espacio",
        "description": (
            "Envía el plano del salón con las mesas ya reservadas marcadas en rojo "
            "para la fecha indicada. Úsalo cuando pregunten por la distribución, "
            "cómo están las mesas, el mapa/plano o la disponibilidad visual. "
            "SÍ existe la foto — NUNCA digas que no la tienes. "
            "Acompáñala con una breve descripción de las 3 zonas (Cantina, VIP, Rumbero)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "YYYY-MM-DD de la noche a mostrar. Si no se mencionó, usa hoy.",
                },
            },
        },
    },
    {
        "name": "consultar_reservas_del_dia",
        "description": "Lista las reservas de un día (resumen para el equipo).",
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "incluir_canceladas": {"type": "boolean"},
            },
            "required": ["fecha"],
        },
    },
    {
        "name": "resumen_dia",
        "description": (
            "Resumen ejecutivo del día: # reservas, ocupación, cover esperado vs "
            "anticipado/en_entrada/pendiente/invitado, salas. Los invitados no "
            "cuentan en el recaudo esperado (el backend los rastrea aparte: "
            "invitado, n_invitado, personas_invitadas). Para uso interno."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"fecha": {"type": "string"}},
            "required": ["fecha"],
        },
    },
    {
        "name": "consultar_reserva",
        "description": "Detalle de una reserva por id.",
        "input_schema": {
            "type": "object",
            "properties": {"reserva_id": {"type": "integer"}},
            "required": ["reserva_id"],
        },
    },
    {
        "name": "crear_reserva",
        "description": (
            "Crea una reserva de UNA mesa (el equipo reserva a nombre de un "
            "cliente). Si la fecha tiene evento con cover, el backend lo aplica. "
            "Usa consultar_reservas_del_dia/resumen_dia antes si dudas de la mesa."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "mesa_id": {"type": "integer", "description": "Número de la mesa (1-42)"},
                "nombre_cliente": {"type": "string"},
                "telefono": {"type": "string"},
                "num_personas": {"type": "integer"},
                "notas": {"type": "string"},
            },
            "required": ["fecha", "mesa_id", "nombre_cliente", "num_personas"],
        },
    },
    {
        "name": "crear_reserva_grupo",
        "description": (
            "Reserva un GRUPO grande sobre varias mesas vecinas contiguas (el "
            "backend valida contigüidad)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string"},
                "mesa_numeros": {"type": "array", "items": {"type": "integer"}},
                "nombre_cliente": {"type": "string"},
                "telefono": {"type": "string"},
                "num_personas": {"type": "integer"},
                "notas": {"type": "string"},
            },
            "required": ["fecha", "mesa_numeros", "nombre_cliente", "num_personas"],
        },
    },
    {
        "name": "crear_reserva_sala_privada",
        "description": (
            "Reserva una SALA PRIVADA (1 o 2). 10p, mínimo de consumo $1.000.000 "
            "(gastable), sin cover."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string"},
                "sala_id": {"type": "integer", "description": "1 o 2"},
                "nombre_cliente": {"type": "string"},
                "telefono": {"type": "string"},
                "num_personas": {"type": "integer"},
                "notas": {"type": "string"},
            },
            "required": ["fecha", "sala_id", "nombre_cliente", "num_personas"],
        },
    },
    {
        "name": "actualizar_reserva",
        "description": (
            "Edita una reserva. Cambios admitidos: cover_estado, estado, notas, "
            "num_personas, nombre_cliente, telefono. Para cancelar prefiere "
            "`cancelar_reserva`. OJO: cambiar num_personas NO reasigna ni valida "
            "la mesa — si el grupo ya no cabe en su(s) mesa(s), avísale al equipo "
            "que quizá toque mover la reserva."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reserva_id": {"type": "integer"},
                "cover_estado": {
                    "type": "string",
                    "enum": ["no_aplica", "pendiente", "anticipado", "en_entrada", "invitado"],
                },
                "estado": {"type": "string", "enum": ["confirmada", "cancelada"]},
                "notas": {"type": "string"},
                "num_personas": {"type": "integer", "description": "Nueva cantidad de personas (≥1)"},
                "nombre_cliente": {"type": "string", "description": "Corregir el nombre del cliente"},
                "telefono": {"type": "string", "description": "Corregir el teléfono, +57..."},
            },
            "required": ["reserva_id"],
        },
    },
    {
        "name": "cancelar_reserva",
        "description": "Cancela una reserva (soft delete + libera la mesa).",
        "input_schema": {
            "type": "object",
            "properties": {"reserva_id": {"type": "integer"}},
            "required": ["reserva_id"],
        },
    },
    {
        "name": "marcar_cover_pagado",
        "description": (
            "Marca el cover de una reserva como pagado anticipado (después de "
            "verificar el comprobante)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reserva_id": {"type": "integer"}},
            "required": ["reserva_id"],
        },
    },
    {
        "name": "marcar_cover_en_entrada",
        "description": "Marca el cover como 'paga en la entrada' (sin pago anticipado).",
        "input_schema": {
            "type": "object",
            "properties": {"reserva_id": {"type": "integer"}},
            "required": ["reserva_id"],
        },
    },
    {
        "name": "marcar_cover_invitado",
        "description": (
            "Marca la reserva como INVITADO: el dueño invitó a ese grupo y NO paga "
            "cover. No cuenta en el recaudo esperado. ÚSALO SOLO cuando el dueño/jefe "
            "lo indique explícitamente (ej. 'la mesa de Juan va como invitada', 'a "
            "esos no les cobres cover'). Nunca lo asumas por tu cuenta: si dudas, "
            "deja el cover como estaba."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reserva_id": {"type": "integer"}},
            "required": ["reserva_id"],
        },
    },
    {
        "name": "eventos_del_mes",
        "description": (
            "Lista los eventos/covers registrados en el backend para un mes completo. "
            "Úsala cuando el equipo pregunte 'qué eventos hay este mes', "
            "'eventos de junio', 'agenda de eventos del mes' o similar. "
            "No respondas con Instagram ni digas que no tienes herramienta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mes": {
                    "type": "string",
                    "description": "Mes en formato YYYY-MM. Si no se menciona, usa el mes actual.",
                },
            },
        },
    },
    {
        "name": "crear_evento",
        "description": (
            "Crea/actualiza un evento de un día (hora_inicio, artista, cover, "
            "link de pago, descripción). Puede haber hasta 2 eventos el mismo día, "
            "diferenciados por hora_inicio. Solo el equipo. **Si el equipo adjuntó una imagen en el "
            "mismo mensaje, se guarda como FLYER del evento automáticamente** y el "
            "bot se la enviará a los clientes que pregunten por ese día."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "nombre": {"type": "string"},
                "artista": {"type": "string"},
                "hora_inicio": {"type": "string", "description": "Hora en formato HH:MM, ej. 20:00"},
                "hora_fin": {"type": "string", "description": "Hora final opcional en formato HH:MM"},
                "tiene_cover": {"type": "boolean"},
                "valor_cover": {"type": "integer", "description": "COP por persona"},
                "link_pago": {"type": "string", "description": "Link de la pasarela de pago"},
                "descripcion": {"type": "string", "description": "Descripción corta del evento"},
            },
            "required": ["fecha", "nombre"],
        },
    },
    {
        "name": "guardar_flyer_evento",
        "description": (
            "Guarda la imagen ADJUNTA en el mensaje como flyer del evento de una "
            "fecha y hora (para agregar/cambiar el flyer de un evento ya creado). "
            "Si hay varios eventos el mismo día, hora_inicio es obligatoria para "
            "asociar el flyer al evento correcto. Requiere que el equipo haya mandado una imagen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "hora_inicio": {"type": "string", "description": "HH:MM del evento, si hay varios ese día"},
            },
            "required": ["fecha"],
        },
    },
    {
        "name": "borrar_evento",
        "description": "Apaga el 'modo evento' de un día. Las reservas se conservan.",
        "input_schema": {
            "type": "object",
            "properties": {"fecha": {"type": "string"}},
            "required": ["fecha"],
        },
    },
    {
        "name": "avisar_cliente",
        "description": (
            "Envía un mensaje de WhatsApp a un CLIENTE (no al grupo del equipo). "
            "Úsalo cuando el equipo te diga 'dile a X que...' o para notificar a un "
            "cliente (cambio de mesa, confirmación, etc.). Necesitas el teléfono del "
            "cliente en formato +57...; si no lo tienes, búscalo con "
            "`consultar_reservas_del_dia` o pídeselo al equipo. Redacta el mensaje en "
            "primera persona como La Cantina (cálido y claro)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "telefono": {"type": "string", "description": "Número del cliente, +57..."},
                "mensaje": {"type": "string", "description": "Texto a enviarle al cliente"},
            },
            "required": ["telefono", "mensaje"],
        },
    },
    {
        "name": "publicar_estado",
        "description": (
            "Publica la IMAGEN o VIDEO ADJUNTO como estado de WhatsApp de La Cantina "
            "Y lo guarda como 'estado vigente' para que el bot pueda reenviárselo a "
            "los clientes que lo pidan. Úsalo cuando el equipo mande una imagen o un "
            "video diciendo 'publica esto como estado', 'sube este estado', 'pon esta "
            "promo', etc. Requiere que el equipo haya adjuntado una imagen o video en "
            "el mensaje. Si incluyen un texto para acompañar la promo, pásalo en `caption`. "
            "Si la imagen es un flyer de evento, extrae fecha/nombre/hora/cover del flyer "
            "y pasa los campos `evento_*`: si ya existe un evento compatible en esa fecha "
            "NO se crea otro; solo se guarda el flyer. Si no existe, la tool crea el evento."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "caption": {"type": "string", "description": "Texto opcional para acompañar el estado/promo"},
                **_schema_evento_desde_estado(),
            },
        },
    },
    {
        "name": "programar_estado",
        "description": (
            "Programa la IMAGEN o VIDEO ADJUNTO para publicarse como estado de WhatsApp "
            "en una fecha y hora futuras de Colombia. Usala cuando el equipo diga "
            "'programa este estado', 'subelo mañana a las 7 PM' o similar. La fecha "
            "debe ser YYYY-MM-DD y la hora debe conservar formato de 12 horas con AM/PM. "
            "Requiere imagen o video adjunto en el mismo mensaje. Si la imagen es un flyer "
            "de evento, extrae fecha/nombre/hora/cover del flyer y pasa los campos `evento_*`; "
            "si ya existe un evento compatible no crea otro, solo guarda el flyer. Si no existe, "
            "la tool crea el evento de inmediato."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "Fecha local de Colombia en formato YYYY-MM-DD.",
                },
                "hora": {
                    "type": "string",
                    "description": "Hora Colombia en formato de 12 horas, por ejemplo 7:30 PM.",
                },
                "caption": {
                    "type": "string",
                    "description": "Texto opcional que acompañara el estado.",
                },
                **_schema_evento_desde_estado(),
            },
            "required": ["fecha", "hora"],
        },
    },
    {
        "name": "listar_estados_programados",
        "description": (
            "Lista los proximos estados de WhatsApp pendientes de publicacion. "
            "Usala SOLO cuando pregunten que estados hay programados, pendientes, "
            "en cola o a que hora salen. NO la uses para 'estados activos' o "
            "'estados publicados ahora'; en ese caso usa enviar_estado_actual."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancelar_estado_programado",
        "description": (
            "Cancela un estado de WhatsApp programado que aun no se ha publicado. "
            "Necesita el ID mostrado por listar_estados_programados."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "estado_id": {"type": "integer", "description": "ID del estado programado."},
            },
            "required": ["estado_id"],
        },
    },
    {
        "name": "enviar_estado_actual",
        "description": (
            "Envia al chat TODAS las IMAGENES o VIDEOS de estados/promos vigentes "
            "publicados ahora en WhatsApp. Usalo cuando alguien pida 'los estados', "
            "'estados activos', 'estados de WhatsApp', 'el estado', 'la promo', "
            "'la foto que subieron', 'el flyer', etc. Si no hay estados vigentes, "
            "la tool te avisa y lo dices con amabilidad."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "borrar_ultimo_estado",
        "description": (
            "Borra el último estado real publicado por La Cantina en WhatsApp. "
            "Úsalo cuando el equipo diga 'borra el último estado', 'elimina el "
            "estado reciente', 'quita el estado que subiste' o similar. Ignora "
            "registros vacíos, acciones de borrado pendientes y estados ya revocados."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "reenviar_comprobante_cliente",
        "description": (
            "Reenvía a ESTE chat (el grupo del equipo) la última IMAGEN que envió "
            "un cliente — típicamente su comprobante de pago de cover. Úsalo cuando "
            "el equipo diga 'reenvía el comprobante de +57...', 'pásame la imagen "
            "que mandó X' o similar. SÍ tienes esta herramienta: recuperas la imagen "
            "guardada del cliente y la mandas al grupo; NUNCA digas que toca hacerlo "
            "manualmente. Necesitas el teléfono del cliente en formato +57...; si no "
            "lo tienes, búscalo con consultar_reservas_del_dia."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "telefono": {"type": "string", "description": "Número del cliente, +57..."},
            },
            "required": ["telefono"],
        },
    },
    {
        "name": "consultar_historial_cliente",
        "description": (
            "Lee los últimos mensajes guardados del chat de un cliente por teléfono. "
            "Úsala cuando el equipo diga 'revisa el chat del cliente', 'mira qué "
            "nombre dio', 'qué dijo el cliente', o cuando haya que corregir una "
            "reserva con nombre raro revisando la conversación. SÍ tienes acceso al "
            "historial guardado en la base de datos; no digas que no puedes leerlo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "telefono": {"type": "string", "description": "Número del cliente, +57..."},
                "limite": {"type": "integer", "description": "Cantidad de mensajes, default 12"},
            },
            "required": ["telefono"],
        },
    },
    {
        "name": "listar_grupos_whatsapp",
        "description": (
            "Lista los GRUPOS de WhatsApp donde el bot (La Cantina) es miembro, con su "
            "nombre y número de participantes. Úsalo cuando el equipo te pregunte 'a qué "
            "grupos puedes escribir', o ANTES de `enviar_mensaje_grupo` si no estás "
            "seguro del nombre exacto del grupo de destino."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "enviar_mensaje_grupo",
        "description": (
            "Envía un mensaje de texto a un GRUPO de WhatsApp donde el bot es miembro. "
            "SÍ tienes esta herramienta para escribirles a grupos — NUNCA digas que no "
            "puedes o que toca hacerlo manualmente. Úsalo cuando el equipo te diga "
            "'anuncia en el grupo X que...', 'manda al grupo Y...', etc. Identifica el "
            "grupo por su NOMBRE (búsqueda sin distinguir mayúsculas; basta una parte "
            "del nombre) o por su id `...@g.us` si lo tienes. Si el nombre coincide con "
            "varios grupos o con ninguno, la herramienta te devolverá la lista para que "
            "confirmes. Redacta el mensaje en primera persona como La Cantina."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "grupo": {
                    "type": "string",
                    "description": "Nombre del grupo (o parte) o su id '...@g.us'.",
                },
                "mensaje": {"type": "string", "description": "Texto a enviar al grupo"},
            },
            "required": ["grupo", "mensaje"],
        },
    },
]


# ── HANDLERS ────────────────────────────────────────────────────────────────


async def handler_consultar_reservas_del_dia(args: dict, ctx: dict) -> dict:
    return await cantina_api.listar_reservas(
        args.get("fecha"), bool(args.get("incluir_canceladas")),
    )


async def handler_resumen_dia(args: dict, ctx: dict) -> dict:
    return await cantina_api.resumen_dia(args.get("fecha"))


async def handler_consultar_reserva(args: dict, ctx: dict) -> dict:
    return await cantina_api.detalle_reserva(args.get("reserva_id"))


async def handler_crear_reserva(args: dict, ctx: dict) -> dict:
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "mesa_id": args.get("mesa_id"),
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": args.get("telefono"),
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    return await cantina_api.crear_reserva(payload)


async def handler_crear_reserva_grupo(args: dict, ctx: dict) -> dict:
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "mesa_numeros": args.get("mesa_numeros") or [],
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": args.get("telefono"),
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    return await cantina_api.crear_reserva_grupo(payload)


async def handler_crear_reserva_sala(args: dict, ctx: dict) -> dict:
    payload = {k: v for k, v in {
        "fecha": args.get("fecha"),
        "sala_id": args.get("sala_id"),
        "nombre_cliente": args.get("nombre_cliente"),
        "telefono": args.get("telefono"),
        "num_personas": args.get("num_personas"),
        "notas": args.get("notas"),
    }.items() if v is not None}
    return await cantina_api.crear_reserva_sala(payload)


async def handler_actualizar_reserva(args: dict, ctx: dict) -> dict:
    campos = ("cover_estado", "estado", "notas", "num_personas", "nombre_cliente", "telefono")
    cambios = {k: args[k] for k in campos if args.get(k) is not None}
    if not cambios:
        return {"ok": False, "error": "Sin cambios a aplicar"}
    return await cantina_api.actualizar_reserva(args.get("reserva_id"), cambios)


async def handler_cancelar_reserva(args: dict, ctx: dict) -> dict:
    return await cantina_api.cancelar_reserva(args.get("reserva_id"))


async def handler_marcar_cover_pagado(args: dict, ctx: dict) -> dict:
    return await cantina_api.actualizar_reserva(
        args.get("reserva_id"), {"cover_estado": "anticipado"},
    )


async def handler_marcar_cover_en_entrada(args: dict, ctx: dict) -> dict:
    return await cantina_api.actualizar_reserva(
        args.get("reserva_id"), {"cover_estado": "en_entrada"},
    )


async def handler_marcar_cover_invitado(args: dict, ctx: dict) -> dict:
    return await cantina_api.actualizar_reserva(
        args.get("reserva_id"), {"cover_estado": "invitado"},
    )


def _mes_actual_bogota() -> str:
    return datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m")


def _normalizar_mes(raw: str | None) -> tuple[str | None, str | None]:
    value = (raw or "").strip()
    if not value:
        return _mes_actual_bogota(), None
    # Permite que el modelo mande YYYY-MM-DD; se usa el mes de esa fecha.
    if len(value) >= 7 and value[4] == "-":
        month = value[:7]
        try:
            datetime.strptime(month, "%Y-%m")
            return month, None
        except ValueError:
            pass
    return None, "mes debe venir en formato YYYY-MM"


async def handler_eventos_del_mes(args: dict, ctx: dict) -> dict:
    mes, error = _normalizar_mes(args.get("mes"))
    if error:
        return {"ok": False, "error": error}
    res = await cantina_api.listar_eventos()
    if not isinstance(res, dict) or not res.get("ok", False):
        return res
    eventos = [
        e for e in extraer_eventos(res)
        if str(e.get("fecha") or "").startswith(mes or "")
    ]
    eventos.sort(key=clave_orden_evento)
    return {
        "ok": True,
        "mes": mes,
        "total": len(eventos),
        "eventos": eventos,
        "nota": (
            "Responde por WhatsApp con una lista corta por fecha. "
            "Si total=0, di que no hay eventos registrados en backend para ese mes."
        ),
    }


def _hora_apertura_normal(fecha: str | None) -> str | None:
    try:
        dia = datetime.strptime(str(fecha or ""), "%Y-%m-%d").date()
    except ValueError:
        return None
    if dia.weekday() <= 3:
        return _HORA_APERTURA_LUN_JUE
    return _HORA_APERTURA_VIE_DOM


def _normalizar_hora_inicio_evento(fecha: str | None, raw: str | None) -> str | None:
    hora = str(raw or "").strip()
    if hora:
        try:
            return datetime.strptime(hora, "%H:%M").strftime("%H:%M")
        except ValueError:
            pass
    return _hora_apertura_normal(fecha)


def _int_cover(valor: object) -> int:
    if valor is None or valor == "":
        return 0
    try:
        return max(0, int(str(valor).replace(".", "").replace(",", "").strip()))
    except ValueError:
        return 0


def _bool_cover(raw: object, valor_cover: int) -> bool:
    if isinstance(raw, bool):
        return raw
    texto = str(raw or "").strip().lower()
    if texto in {"true", "si", "sí", "1", "cover", "con cover"}:
        return True
    if texto in {"false", "no", "0", "sin cover"}:
        return False
    return valor_cover > 0


def _normalizar_texto_evento(valor: object) -> str:
    texto = str(valor or "").casefold().strip()
    texto = texto.translate(str.maketrans("áéíóúüñ", "aeiouun"))
    return " ".join(texto.split())


def _evento_existente_para_estado(
    existentes: list[dict],
    *,
    nombre: str,
    hora_inicio: str,
    hora_explicitada: bool,
) -> dict | None:
    if not existentes:
        return None

    if hora_explicitada:
        for evento in existentes:
            if str(evento.get("hora_inicio") or "") == hora_inicio:
                return evento
        return None

    nombre_norm = _normalizar_texto_evento(nombre)
    if nombre_norm:
        for evento in existentes:
            existente_norm = _normalizar_texto_evento(
                evento.get("nombre") or evento.get("evento")
            )
            if existente_norm and existente_norm == nombre_norm:
                return evento

    if len(existentes) == 1:
        return existentes[0]

    return None


async def _sincronizar_evento_desde_estado(args: dict, ctx: dict, *, media_bytes: bytes | None, media_mime: str | None) -> dict | None:
    """Crea/actualiza el evento asociado a un flyer publicado como estado.

    Es idempotente: si ya existe un evento compatible para la fecha/hora, no
    vuelve a crearlo; solo guarda el flyer/descripción local.
    """
    if not media_bytes:
        return None
    fecha = str(args.get("evento_fecha") or "").strip()
    nombre = str(args.get("evento_nombre") or "").strip()
    if not (fecha and nombre):
        return None
    hora_raw = str(args.get("evento_hora_inicio") or "").strip()
    hora_inicio = _normalizar_hora_inicio_evento(fecha, hora_raw)
    if not hora_inicio:
        return {
            "ok": False,
            "error": "No pude interpretar la fecha del evento para asignar hora normal.",
        }

    descripcion = (args.get("evento_descripcion") or "").strip() or None
    valor_cover = _int_cover(args.get("evento_valor_cover"))
    payload = {
        "fecha": fecha,
        "nombre": nombre,
        "hora_inicio": hora_inicio,
        "tiene_cover": _bool_cover(args.get("evento_tiene_cover"), valor_cover),
        "valor_cover": valor_cover,
    }
    if args.get("evento_artista"):
        payload["artista"] = str(args.get("evento_artista")).strip()
    if args.get("evento_link_pago"):
        payload["link_pago"] = str(args.get("evento_link_pago")).strip()

    existentes: list[dict] = []
    consulta = await cantina_api.consultar_evento(fecha)
    if isinstance(consulta, dict) and consulta.get("ok"):
        existentes = extraer_eventos(consulta)
    evento_existente = _evento_existente_para_estado(
        existentes,
        nombre=nombre,
        hora_inicio=hora_inicio,
        hora_explicitada=bool(hora_raw),
    )
    if evento_existente:
        hora_existente = str(evento_existente.get("hora_inicio") or "") or None
        path_flyer = guardar_flyer(fecha, hora_existente, media_bytes, media_mime)
        guardar_descripcion(fecha, hora_existente, descripcion)
        res = {
            "ok": True,
            "evento": evento_existente,
            "evento_desde_estado": {
                "ok": True,
                "fecha": fecha,
                "hora_inicio": hora_existente,
                "nombre": evento_existente.get("nombre") or nombre,
                "creado": False,
                "actualizado": False,
                "omitido_por_existente": True,
                "flyer_guardado": bool(path_flyer),
                "hora_asumida": not bool(hora_raw),
            },
        }
        log.info(
            "tools_equipo.estado.evento_sync_omitido",
            fecha=fecha,
            hora=hora_existente,
            flyer=bool(path_flyer),
            por=ctx.get("miembro_nombre"),
        )
        return res

    res = await cantina_api.crear_evento(payload)
    if isinstance(res, dict) and res.get("ok"):
        path_flyer = guardar_flyer(fecha, hora_inicio, media_bytes, media_mime)
        guardar_descripcion(fecha, hora_inicio, descripcion)
        res["evento_desde_estado"] = {
            "ok": True,
            "fecha": fecha,
            "hora_inicio": hora_inicio,
            "nombre": nombre,
            "creado": True,
            "actualizado": False,
            "omitido_por_existente": False,
            "flyer_guardado": bool(path_flyer),
            "hora_asumida": not bool(hora_raw),
        }
        log.info(
            "tools_equipo.estado.evento_sync",
            fecha=fecha,
            hora=hora_inicio,
            creado=True,
            flyer=bool(path_flyer),
            por=ctx.get("miembro_nombre"),
        )
    return res


async def handler_crear_evento(args: dict, ctx: dict) -> dict:
    fecha = args.get("fecha")
    hora_inicio = args.get("hora_inicio")
    descripcion = args.get("descripcion")
    # El backend de eventos no guarda descripción ni flyer → van aparte (local).
    payload = {k: v for k, v in args.items()
               if v is not None and k not in ("descripcion",)}
    res = await cantina_api.crear_evento(payload)
    if isinstance(res, dict) and res.get("ok"):
        path_flyer = guardar_flyer(fecha, hora_inicio, ctx.get("imagen_bytes"), ctx.get("imagen_mime"))
        guardar_descripcion(fecha, hora_inicio, descripcion)
        res["flyer_guardado"] = bool(path_flyer)
        if not path_flyer:
            res["nota_flyer"] = ("Evento creado. No adjuntaste imagen, así que NO hay flyer. "
                                 "Si quieres flyer, mándame la imagen y usa guardar_flyer_evento "
                                 "indicando fecha y hora del evento.")
        else:
            res["nota_flyer"] = "Evento creado CON flyer. El bot se lo enviará a quien pregunte por ese día."
        log.info("tools_equipo.crear_evento", fecha=fecha, hora=hora_inicio, flyer=bool(path_flyer))
    return res


async def handler_guardar_flyer_evento(args: dict, ctx: dict) -> dict:
    fecha = args.get("fecha")
    hora_inicio = args.get("hora_inicio")
    if not ctx.get("imagen_bytes"):
        return {"ok": False, "error": "No recibí ninguna imagen. Adjunta el flyer en el mismo mensaje."}
    path = guardar_flyer(fecha, hora_inicio, ctx.get("imagen_bytes"), ctx.get("imagen_mime"))
    ok = bool(path)
    log.info("tools_equipo.guardar_flyer_evento", fecha=fecha, hora=hora_inicio, ok=ok)
    return {"ok": ok, "fecha": fecha, "hora_inicio": hora_inicio} if ok else {"ok": False, "error": "no se pudo guardar el flyer"}


async def handler_borrar_evento(args: dict, ctx: dict) -> dict:
    return await cantina_api.borrar_evento(args.get("fecha"))


async def handler_avisar_cliente(args: dict, ctx: dict) -> dict:
    """Envía un WhatsApp a un cliente y lo deja registrado en su chat."""
    tel = (args.get("telefono") or "").strip()
    mensaje = (args.get("mensaje") or "").strip()
    if not tel or not mensaje:
        return {"ok": False, "error": "telefono y mensaje son requeridos"}
    numero = _telefono_canonico(tel)
    objetivo_citado = ctx.get("cliente_objetivo_citado") or {}
    tel_objetivo = _telefono_canonico(objetivo_citado.get("telefono") or "")
    if tel_objetivo:
        if numero and _ultimos_10(numero) != _ultimos_10(tel_objetivo):
            log.warning(
                "tools_equipo.avisar_cliente.override_objetivo_citado",
                solicitado=numero,
                objetivo=tel_objetivo,
                por=ctx.get("miembro_nombre"),
            )
        numero = tel_objetivo
    if not numero:
        return {"ok": False, "error": "telefono invalido"}
    from app.whapi.client import enviar_texto
    try:
        await enviar_texto(numero, mensaje)
    except Exception as e:
        log.warning("tools_equipo.avisar_cliente.fail", tel=numero, error=str(e))
        return {"ok": False, "error": f"no se pudo enviar: {str(e)[:160]}"}
    # Registrar el outbound en el chat del cliente (aparece en /admin/chats).
    session = ctx.get("session")
    if session is not None:
        try:
            from app.db.repos import get_or_create_cliente, guardar_conversacion
            cli = await get_or_create_cliente(session, numero)
            await guardar_conversacion(
                session, cliente_id=cli.id, direccion="outbound", tipo="texto",
                contenido=mensaje,
                metadata={"enviado_por_equipo": ctx.get("miembro_nombre")},
            )
        except Exception as e:
            log.debug("tools_equipo.avisar_cliente.persist_fail", error=str(e))
    log.info("tools_equipo.avisar_cliente", tel=numero, por=ctx.get("miembro_nombre"))
    return {"ok": True, "enviado_a": numero}


def _nombre_grupo(g: dict) -> str:
    return (g.get("name") or g.get("subject") or "").strip()


def _participantes_grupo(g: dict) -> int:
    participantes = g.get("participants")
    if isinstance(participantes, list):
        return len(participantes)
    if isinstance(participantes, int):
        return participantes
    return 0


async def handler_listar_grupos_whatsapp(args: dict, ctx: dict) -> dict:
    """Lista los grupos de WhatsApp donde esta el canal del bot."""
    from app.whapi.client import listar_grupos

    try:
        grupos = await listar_grupos(count=100)
    except Exception as exc:
        log.warning("tools_equipo.listar_grupos.fail", error=str(exc))
        return {"ok": False, "error": f"no se pudieron leer los grupos: {str(exc)[:160]}"}
    items = [
        {
            "id": grupo.get("id"),
            "nombre": _nombre_grupo(grupo) or "(sin nombre)",
            "participantes": _participantes_grupo(grupo),
        }
        for grupo in grupos
    ]
    items.sort(key=lambda item: (-item["participantes"], item["nombre"].lower()))
    return {"ok": True, "total": len(items), "grupos": items}


async def handler_enviar_mensaje_grupo(args: dict, ctx: dict) -> dict:
    """Envia un texto a un grupo de WhatsApp resolviendo por nombre o id."""
    grupo_ref = (args.get("grupo") or "").strip()
    mensaje = (args.get("mensaje") or "").strip()
    if not grupo_ref or not mensaje:
        return {"ok": False, "error": "grupo y mensaje son requeridos"}

    from app.whapi.client import enviar_texto, listar_grupos

    if grupo_ref.endswith("@g.us"):
        gid, nombre = grupo_ref, grupo_ref
    else:
        try:
            grupos = await listar_grupos(count=100)
        except Exception as exc:
            log.warning("tools_equipo.enviar_grupo.listar_fail", error=str(exc))
            return {"ok": False, "error": f"no se pudieron leer los grupos: {str(exc)[:160]}"}
        ref = grupo_ref.lower()
        exactos = [grupo for grupo in grupos if _nombre_grupo(grupo).lower() == ref]
        parciales = [grupo for grupo in grupos if ref in _nombre_grupo(grupo).lower()]
        candidatos = exactos or parciales
        if not candidatos:
            disponibles = sorted(
                (_nombre_grupo(grupo) or "(sin nombre)" for grupo in grupos), key=str.lower
            )
            return {
                "ok": False,
                "error": f"No encontré ningún grupo que coincida con '{grupo_ref}'.",
                "grupos_disponibles": disponibles,
            }
        if len(candidatos) > 1:
            return {
                "ok": False,
                "error": (
                    f"'{grupo_ref}' coincide con {len(candidatos)} grupos. "
                    "Pídele al equipo que confirme cuál."
                ),
                "coincidencias": [
                    _nombre_grupo(grupo) or "(sin nombre)" for grupo in candidatos
                ],
            }
        grupo = candidatos[0]
        gid, nombre = grupo.get("id"), _nombre_grupo(grupo) or "(sin nombre)"

    if not gid:
        return {"ok": False, "error": "el grupo no tiene id válido"}
    try:
        await enviar_texto(gid, mensaje)
    except Exception as exc:
        log.warning("tools_equipo.enviar_grupo.fail", grupo=gid, error=str(exc))
        return {"ok": False, "error": f"no se pudo enviar al grupo: {str(exc)[:160]}"}
    log.info(
        "tools_equipo.enviar_grupo",
        grupo=gid,
        nombre=nombre,
        por=ctx.get("miembro_nombre"),
    )
    return {"ok": True, "enviado_a_grupo": nombre, "group_id": gid}


async def handler_publicar_estado(args: dict, ctx: dict) -> dict:
    """Publica la imagen/video adjunto como estado de WhatsApp y lo guarda como vigente."""
    imagen_bytes = ctx.get("imagen_bytes")
    video_bytes = ctx.get("video_bytes")
    if not imagen_bytes and not video_bytes:
        return {"ok": False, "error": "No recibí ninguna imagen ni video. Adjunta el "
                                      "contenido del estado en el mismo mensaje."}
    caption = (args.get("caption") or "").strip() or None
    from app import promo_estado
    from app.whapi.client import publicar_story_imagen_bytes, publicar_story_video_bytes
    # 1. Publicar como estado de WhatsApp (video tiene prioridad si llegan ambos).
    try:
        if video_bytes:
            data, mime = video_bytes, (ctx.get("video_mime") or "video/mp4")
            await publicar_story_video_bytes(data, caption=caption, mime=mime)
        else:
            data, mime = imagen_bytes, (ctx.get("imagen_mime") or "image/jpeg")
            await publicar_story_imagen_bytes(data, caption=caption, mime=mime)
    except Exception as e:
        log.warning("tools_equipo.publicar_estado.story_fail", error=str(e))
        return {"ok": False, "error": f"no pude publicar el estado: {str(e)[:160]}"}
    # 2. Guardar como estado vigente (para reenviar a clientes).
    try:
        promo_estado.guardar_estado(data, mime, caption)
    except Exception as e:
        log.warning("tools_equipo.publicar_estado.guardar_fail", error=str(e))
        return {"ok": True, "nota": "Estado publicado, pero no pude guardarlo para "
                                    "reenviar a clientes. Avísale al admin."}
    evento_sync = None
    if not video_bytes:
        evento_sync = await _sincronizar_evento_desde_estado(
            args,
            ctx,
            media_bytes=data,
            media_mime=mime,
        )
    tipo = "video" if video_bytes else "imagen"
    log.info("tools_equipo.publicar_estado", por=ctx.get("miembro_nombre"), tipo=tipo)
    nota = (
        f"Estado ({tipo}) publicado en WhatsApp y guardado. Ahora el bot puede "
        "reenviárselo a los clientes que pregunten por la promo/estado."
    )
    if isinstance(evento_sync, dict):
        ev = evento_sync.get("evento_desde_estado") if evento_sync.get("ok") else None
        if ev:
            extra = " (hora normal asumida)" if ev.get("hora_asumida") else ""
            if ev.get("omitido_por_existente"):
                nota += (
                    f" El evento del {ev['fecha']} a las {ev['hora_inicio']}{extra} "
                    "ya existía, no lo dupliqué; solo guardé el flyer."
                )
            else:
                nota += (
                    f" Además, creé el evento del {ev['fecha']} a las "
                    f"{ev['hora_inicio']}{extra} y guardé el flyer."
                )
        elif not evento_sync.get("ok"):
            nota += f" No pude crear el evento automático: {evento_sync.get('error') or 'error del backend'}."
    elif not video_bytes and (args.get("evento_fecha") or args.get("evento_nombre")):
        nota += " No creé evento automático porque faltó fecha o nombre del evento."
    return {
        "ok": True,
        "tipo": tipo,
        "evento_sync": evento_sync,
        "nota": nota,
    }


async def handler_programar_estado(args: dict, ctx: dict) -> dict:
    """Guarda un estado adjunto para publicarlo una sola vez en hora Colombia."""
    if (ctx.get("rol") or "").lower() == "cliente":
        return {"ok": False, "error": "Solo el equipo puede programar estados."}
    imagen_bytes = ctx.get("imagen_bytes")
    video_bytes = ctx.get("video_bytes")
    if not imagen_bytes and not video_bytes:
        return {
            "ok": False,
            "error": "Adjunta la imagen o el video que quieres programar en el mismo mensaje.",
        }

    from app import estados_programados

    try:
        programado = estados_programados.parsear_fecha_hora_colombia(
            str(args.get("fecha") or ""),
            str(args.get("hora") or ""),
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if video_bytes:
        data = video_bytes
        mime = ctx.get("video_mime") or "video/mp4"
        tipo = "video"
    else:
        data = imagen_bytes
        mime = ctx.get("imagen_mime") or "image/jpeg"
        tipo = "imagen"
    caption = (args.get("caption") or "").strip() or None
    estado_id = await estados_programados.crear(
        ctx["session"],
        media_bytes=data,
        media_mime=mime,
        tipo=tipo,
        caption=caption,
        programado_para=programado,
        creado_por=ctx.get("miembro_nombre"),
    )
    evento_sync = None
    if tipo == "imagen":
        evento_sync = await _sincronizar_evento_desde_estado(
            args,
            ctx,
            media_bytes=data,
            media_mime=mime,
        )
    log.info(
        "tools_equipo.programar_estado",
        estado_id=estado_id,
        por=ctx.get("miembro_nombre"),
        programado_para=programado.isoformat(),
    )
    return {
        "ok": True,
        "estado_id": estado_id,
        "tipo": tipo,
        "programado_para": estados_programados.formatear_hora_colombia(programado),
        "evento_sync": evento_sync,
        "nota": (
            "Confirma brevemente el ID, la fecha y la hora Colombia. "
            "Si evento_sync trae evento_desde_estado, menciona tambien si el evento "
            "quedo creado o si ya existia y solo se guardo el flyer."
        ),
    }


async def handler_listar_estados_programados(args: dict, ctx: dict) -> dict:
    if (ctx.get("rol") or "").lower() == "cliente":
        return {"ok": False, "error": "Solo el equipo puede ver estados programados."}
    from app import estados_programados

    items = await estados_programados.listar(ctx["session"])
    return {
        "ok": True,
        "total": len(items),
        "estados": [
            {
                "id": item["id"],
                "tipo": item["tipo"],
                "caption": item["caption"],
                "estado": item["estado"],
                "programado_para": estados_programados.formatear_hora_colombia(
                    item["programado_para"]
                ),
            }
            for item in items
        ],
        "nota": "Si no hay estados, dilo directamente. No inventes programaciones.",
    }


async def handler_cancelar_estado_programado(args: dict, ctx: dict) -> dict:
    if (ctx.get("rol") or "").lower() == "cliente":
        return {"ok": False, "error": "Solo el equipo puede cancelar estados programados."}
    from app import estados_programados

    estado_id = int(args.get("estado_id") or 0)
    if estado_id <= 0:
        return {"ok": False, "error": "Indica un ID de estado valido."}
    cancelado = await estados_programados.cancelar(ctx["session"], estado_id)
    if not cancelado:
        return {
            "ok": False,
            "error": "No encontre ese estado pendiente; pudo publicarse o cancelarse antes.",
        }
    log.info("tools_equipo.cancelar_estado_programado", estado_id=estado_id)
    return {"ok": True, "estado_id": estado_id, "nota": "Estado programado cancelado."}


async def handler_enviar_estado_actual(args: dict, ctx: dict) -> dict:
    """Marca que hay que enviar la imagen/video del estado vigente al chat del equipo."""
    from app import promo_estado
    if not await promo_estado.cargar_estados_activos(max_estados=1):
        return {"ok": False, "sin_estado": True,
                "nota": "No hay un estado/promo vigente guardado. Avísale que por ahora no hay una promo publicada."}
    ctx["enviar_estado_actual"] = True
    return {"ok": True, "nota": "La imagen del estado/promo se enviará junto con tu respuesta."}


async def handler_borrar_ultimo_estado(args: dict, ctx: dict) -> dict:
    """Borra el último estado propio con contenido real (imagen/video/texto)."""
    from app.whapi.client import eliminar_mensaje, listar_stories

    stories = await listar_stories(count=100)
    items = stories.get("stories") or stories.get("messages") or stories.get("data") or []
    candidatos: list[dict] = []

    for story in items:
        if not isinstance(story, dict) or not story.get("from_me"):
            continue
        if story.get("subtype") == "revoke" or story.get("action"):
            continue
        media_kind = next(
            (kind for kind in ("image", "video", "audio", "text") if isinstance(story.get(kind), dict)),
            None,
        )
        if not media_kind:
            continue
        candidatos.append(story)

    if not candidatos:
        return {
            "ok": False,
            "sin_estado": True,
            "nota": "No encontré estados activos con imagen, video o texto para borrar.",
        }

    ultimo = max(candidatos, key=lambda s: int(s.get("timestamp") or 0))
    message_id = ultimo.get("id")
    if not message_id:
        return {"ok": False, "error": "Encontré un estado, pero no trae id para borrarlo."}

    await eliminar_mensaje(message_id)
    log.info(
        "tools_equipo.borrar_ultimo_estado",
        por=ctx.get("miembro_nombre"),
        message_id=message_id,
        timestamp=ultimo.get("timestamp"),
    )
    return {
        "ok": True,
        "message_id": message_id,
        "nota": (
            "Último estado enviado a borrar. WhatsApp/Whapi puede tardar unos minutos "
            "en reflejarlo; si aparece como pendiente, es sincronización del canal."
        ),
    }


async def handler_reenviar_comprobante_cliente(args: dict, ctx: dict) -> dict:
    """Recupera la última imagen que mandó un cliente (su comprobante) y la
    reenvía al chat actual (el grupo del equipo)."""
    from app.db.repos import ultima_imagen_inbound
    from app.whapi.parser import normalizar_numero
    from app.whapi.client import auth_headers, enviar_imagen_bytes

    tel = normalizar_numero((args.get("telefono") or "").strip())
    if not tel:
        return {"ok": False, "error": "telefono requerido en formato +57..."}
    destino = ctx.get("destino_envio")
    if not destino:
        return {"ok": False, "error": "no hay chat destino para reenviar la imagen"}
    session = ctx.get("session")
    if session is None:
        return {"ok": False, "error": "sin sesión de BD"}

    conv = await ultima_imagen_inbound(session, tel)
    if not conv or not conv.media_url:
        return {"ok": False, "error": (
            f"No encuentro ninguna imagen reciente enviada por {tel}. "
            "Verifica el número o pídele al cliente que reenvíe el comprobante."
        )}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(conv.media_url, headers=auth_headers())
        if r.status_code >= 400 or not r.content:
            log.warning("tools_equipo.reenviar_comprobante.download_http",
                        status=r.status_code, tel=tel)
            return {"ok": False, "error": "no pude descargar la imagen del cliente (link expirado)"}
        mime = r.headers.get("content-type") or "image/jpeg"
        await enviar_imagen_bytes(
            destino, r.content, mime=mime,
            caption=f"📎 Comprobante de {tel} (reenviado al grupo)",
        )
    except Exception as e:
        log.warning("tools_equipo.reenviar_comprobante.fail", tel=tel, error=str(e))
        return {"ok": False, "error": f"no se pudo reenviar la imagen: {str(e)[:160]}"}
    log.info("tools_equipo.reenviar_comprobante_cliente", tel=tel, destino=destino)
    return {"ok": True, "nota": f"Comprobante de {tel} reenviado a este grupo. "
                                "Confírmalo brevemente en tu texto."}


async def handler_consultar_historial_cliente(args: dict, ctx: dict) -> dict:
    from app.whapi.parser import normalizar_numero

    tel = normalizar_numero((args.get("telefono") or "").strip())
    if not tel:
        return {"ok": False, "error": "telefono requerido en formato +57..."}
    session = ctx.get("session")
    if session is None:
        return {"ok": False, "error": "sin sesión de BD"}

    limite = int(args.get("limite") or 12)
    limite = max(1, min(limite, 30))
    stmt = (
        select(Conversacion)
        .join(Cliente, Cliente.id == Conversacion.cliente_id)
        .where(Cliente.numero_whatsapp == tel)
        .order_by(Conversacion.timestamp.desc(), Conversacion.id.desc())
        .limit(limite)
    )
    rows = list(reversed((await session.execute(stmt)).scalars().all()))
    if not rows:
        return {
            "ok": False,
            "sin_historial": True,
            "telefono": tel,
            "error": "No encontré mensajes guardados para ese teléfono.",
        }

    mensajes = []
    for row in rows:
        contenido = (row.contenido or "").strip()
        if not contenido and row.media_url:
            contenido = f"[{row.tipo} con media]"
        mensajes.append({
            "id": row.id,
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            "direccion": row.direccion,
            "tipo": row.tipo,
            "contenido": contenido[:800],
        })
    return {
        "ok": True,
        "telefono": tel,
        "total": len(mensajes),
        "mensajes": mensajes,
        "nota": (
            "Usa estos mensajes para inferir el nombre real si el cliente lo dijo "
            "claramente. Si no aparece, pregunta al cliente con avisar_cliente."
        ),
    }


async def handler_enviar_plano_espacio(args: dict, ctx: dict) -> dict:
    """Manda el plano del salón con mesas reservadas marcadas en rojo."""
    if ctx.get("_plano_enviado"):
        return {"ok": True, "nota": "El plano ya se envió en este turno. NO lo mandes otra vez."}
    destino = ctx.get("destino_envio")
    if not destino:
        return {"ok": False, "error": "no hay chat destino para enviar la imagen"}
    if not _PLANO.exists():
        log.warning("tools_equipo.enviar_plano.no_existe", path=str(_PLANO))
        return {"ok": False, "error": "no encuentro la foto del plano en el servidor"}

    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.whapi.client import enviar_imagen_bytes
    from app.integrations import cantina_api
    from app.utils.plano import generar_plano_con_reservas

    fecha = args.get("fecha") or datetime.now(ZoneInfo("America/Bogota")).date().isoformat()

    # Obtener mesas ocupadas para la fecha
    mesas_reservadas: list[int] = []
    try:
        disp = await cantina_api.disponibilidad(fecha)
        ocupadas = (disp.get("ocupacion") or {}).get("ocupadas") or []
        mesas_reservadas = [int(m) for m in ocupadas if str(m).isdigit()]
    except Exception as e:
        log.warning("tools_equipo.enviar_plano.disponibilidad_fail", error=str(e))

    png_bytes = generar_plano_con_reservas(mesas_reservadas)
    if png_bytes is None:
        png_bytes = _PLANO.read_bytes()

    try:
        cap = f"🗺️ Plano del salón — {fecha} (🔴 = ya reservada)"
        await enviar_imagen_bytes(destino, png_bytes, mime="image/png",
                                  filename="plano-espacio.png", caption=cap)
    except Exception as e:
        log.warning("tools_equipo.enviar_plano.fail", destino=destino, error=str(e))
        return {"ok": False, "error": f"no se pudo enviar la foto: {str(e)[:160]}"}

    ctx["_plano_enviado"] = True
    log.info("tools_equipo.enviar_plano_espacio", destino=destino, fecha=fecha,
             reservadas=len(mesas_reservadas))
    return {
        "ok": True,
        "fecha": fecha,
        "mesas_marcadas": len(mesas_reservadas),
        "nota": "Plano enviado con mesas reservadas en rojo. Describe las 3 zonas brevemente.",
    }


async def handler_marcar_casa_llena(args: dict, ctx: dict) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    fecha = args.get("fecha") or datetime.now(
        ZoneInfo("America/Bogota")
    ).date().isoformat()
    return await cantina_api.marcar_casa_llena(
        fecha,
        args.get("motivo") or "Casa llena",
        ctx.get("miembro_nombre"),
    )


async def handler_reabrir_reservas(args: dict, ctx: dict) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    fecha = args.get("fecha") or datetime.now(
        ZoneInfo("America/Bogota")
    ).date().isoformat()
    return await cantina_api.reabrir_reservas(fecha)


# ── DISPATCHER ──────────────────────────────────────────────────────────────

Handler = Callable[[dict, dict], Awaitable[dict]]

HANDLERS_EQUIPO: dict[str, Handler] = {
    "marcar_casa_llena": handler_marcar_casa_llena,
    "reabrir_reservas": handler_reabrir_reservas,
    "enviar_plano_espacio": handler_enviar_plano_espacio,
    "consultar_reservas_del_dia": handler_consultar_reservas_del_dia,
    "resumen_dia": handler_resumen_dia,
    "consultar_reserva": handler_consultar_reserva,
    "crear_reserva": handler_crear_reserva,
    "crear_reserva_grupo": handler_crear_reserva_grupo,
    "crear_reserva_sala_privada": handler_crear_reserva_sala,
    "actualizar_reserva": handler_actualizar_reserva,
    "cancelar_reserva": handler_cancelar_reserva,
    "marcar_cover_pagado": handler_marcar_cover_pagado,
    "marcar_cover_en_entrada": handler_marcar_cover_en_entrada,
    "marcar_cover_invitado": handler_marcar_cover_invitado,
    "eventos_del_mes": handler_eventos_del_mes,
    "crear_evento": handler_crear_evento,
    "guardar_flyer_evento": handler_guardar_flyer_evento,
    "borrar_evento": handler_borrar_evento,
    "avisar_cliente": handler_avisar_cliente,
    "listar_grupos_whatsapp": handler_listar_grupos_whatsapp,
    "enviar_mensaje_grupo": handler_enviar_mensaje_grupo,
    "reenviar_comprobante_cliente": handler_reenviar_comprobante_cliente,
    "consultar_historial_cliente": handler_consultar_historial_cliente,
    "publicar_estado": handler_publicar_estado,
    "programar_estado": handler_programar_estado,
    "listar_estados_programados": handler_listar_estados_programados,
    "cancelar_estado_programado": handler_cancelar_estado_programado,
    "enviar_estado_actual": handler_enviar_estado_actual,
    "borrar_ultimo_estado": handler_borrar_ultimo_estado,
}


async def ejecutar_tool_equipo(name: str, args: dict, ctx: dict) -> dict:
    handler = HANDLERS_EQUIPO.get(name)
    if not handler:
        return {"error": f"Tool de equipo desconocida: {name}"}
    try:
        return await handler(args, ctx)
    except Exception as e:
        log.exception("tools_equipo.exec.fail", tool=name, error=str(e))
        return {"error": str(e)[:200]}
