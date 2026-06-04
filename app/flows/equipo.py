"""
Flow para mensajes ENTRANTES de miembros del equipo (Fabio, supervisores).

Distinto al flow de cliente:
- Usa SYSTEM_PROMPT_EQUIPO (rol operativo, no de ventas)
- Tools distintas (responder_a_cliente, marcar_alerta_resuelta, etc.)
- NO aplica humanización (no estamos hablando con cliente; el delay no tiene sentido)
- Carga contexto: últimas alertas abiertas + últimos pedidos
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.claude.anthropic_client import get_anthropic_client
from app.claude.prompts import SYSTEM_PROMPT_EQUIPO, bloque_info_venue
from app.claude.tools_equipo import (
    HANDLERS_EQUIPO,
    TOOL_DEFINITIONS_EQUIPO,
    ejecutar_tool_equipo,
)
from app.config import get_settings
from app.db.models import AlertaFabio, Cliente, Conversacion
from app.db.repos import get_or_create_cliente, guardar_conversacion
from app.equipo.directorio import Miembro
from app.logging_setup import log
from app.whapi.client import auth_headers, enviar_texto, set_token as set_whapi_token
from app.whapi.parser import MensajeWhapi
from app.identidades import Identidad, principal as _identidad_principal

settings = get_settings()

_client = get_anthropic_client()

# Costos aproximados (mismos que client.py)
PRECIO_INPUT = Decimal("3.00") / Decimal("1000000")
PRECIO_OUTPUT = Decimal("15.00") / Decimal("1000000")
PRECIO_CACHE_READ = Decimal("0.30") / Decimal("1000000")
PRECIO_CACHE_WRITE = Decimal("3.75") / Decimal("1000000")


async def _construir_contexto(session: AsyncSession, max_alertas: int = 8) -> str:
    """Texto formateado con alertas/pendientes abiertas para Claude.

    Las reservas/eventos del día NO se precargan: el bot las consulta bajo
    demanda con las tools `resumen_dia` / `consultar_reservas_del_dia` contra
    el backend de mesas (fuente de verdad en tiempo real).
    """
    alertas_rows = (await session.execute(
        select(AlertaFabio, Cliente)
        .join(Cliente, Cliente.id == AlertaFabio.cliente_id, isouter=True)
        .where(AlertaFabio.resuelto.is_(False))
        .order_by(desc(AlertaFabio.created_at))
        .limit(max_alertas)
    )).all()

    lineas: list[str] = []
    lineas.append("## PENDIENTES / ALERTAS ABIERTAS")
    if not alertas_rows:
        lineas.append("(ninguna)")
    else:
        for a, c in alertas_rows:
            cliente_str = (c.nombre or "Sin nombre") if c else "Desconocido"
            num = c.numero_whatsapp if c else "?"
            lineas.append(
                f"- alerta_id={a.id} | tipo={a.tipo} | {cliente_str} ({num})\n"
                f"  mensaje: {(a.mensaje or '')[:250]}"
            )

    lineas.append(
        "\n## RESERVAS / EVENTOS\n"
        "Para ver reservas, ocupación o el resumen de un día, usa las tools "
        "`resumen_dia` o `consultar_reservas_del_dia` (consultan el backend en vivo)."
    )

    return "\n".join(lineas)


def _calcular_costo(usage) -> Decimal:
    if usage is None:
        return Decimal("0")
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (Decimal(inp) * PRECIO_INPUT + Decimal(out) * PRECIO_OUTPUT
            + Decimal(cr) * PRECIO_CACHE_READ + Decimal(cw) * PRECIO_CACHE_WRITE)


async def procesar_mensaje_equipo(
    *,
    session: AsyncSession,
    miembro: Miembro,
    msg: MensajeWhapi,
    identidad: Identidad | None = None,
    responder_a: str | None = None,
) -> None:
    """Procesa un inbound de un miembro del equipo y responde con confirmación.

    `identidad` define el canal whapi por el que se envía la respuesta.
    `responder_a` (opcional) — chat_id donde enviar la respuesta. Default es
    el chat personal del miembro (`miembro.numero_whatsapp`). Pasar un
    `group_id@g.us` para responder en un grupo en lugar del chat personal.
    """
    ident = identidad or _identidad_principal()
    set_whapi_token(ident.token)
    # destino_envio queda disponible para todos los envíos del flow
    destino_envio = responder_a or miembro.numero_whatsapp
    instruccion = (msg.texto or "").strip()

    # Nota de voz: no hay transcripción configurada en La Cantina; pedimos que
    # escriban la instrucción por texto.
    if not instruccion and msg.tipo == "audio" and msg.media_url:
        instruccion = "[Llegó una nota de voz pero no puedo transcribirla; pide la instrucción por texto.]"

    # Si llega una imagen sin texto, igual procesamos (multimodal) — el equipo
    # a veces manda foto de un comprobante, etc.
    if not instruccion and not (msg.tipo == "imagen" and msg.media_url):
        log.info("flow_equipo.sin_texto", miembro=miembro.nombre)
        return
    if not instruccion:
        instruccion = "[Imagen sin texto; analízala y dime qué necesitas saber o qué acción quieres que tome.]"

    # Si el equipo cita un mensaje (típicamente un mensaje del bot/cliente),
    # inyectarlo al contexto: "Fabio citó X, su respuesta es Y"
    if msg.quoted_message_id:
        quoted_preview = msg.quoted_content or ""
        quoted_msg_db = (await session.execute(
            select(Conversacion).where(
                Conversacion.whapi_message_id == msg.quoted_message_id
            ).limit(1)
        )).scalar_one_or_none()
        if quoted_msg_db and quoted_msg_db.contenido:
            quoted_preview = quoted_msg_db.contenido
        if quoted_preview:
            log.info(
                "flow_equipo.miembro_cito",
                miembro=miembro.nombre,
                quoted_id=msg.quoted_message_id,
                preview=quoted_preview[:80],
            )
            instruccion = (
                f"[Te están respondiendo/citando este mensaje anterior:\n"
                f"\"{quoted_preview[:600]}\"]\n\n"
                f"Su instrucción: {instruccion}"
            )

    log.info("flow_equipo.inbound", miembro=miembro.nombre, preview=instruccion[:100])

    # Persistir el inbound del admin para que aparezca en /admin/chats.
    # Auto-crea un "cliente" con el número del admin (ya bloqueado por la
    # lógica de webhook para que no se procese como cliente normal).
    cliente_proxy = await get_or_create_cliente(session, miembro.numero_whatsapp)
    if not cliente_proxy.nombre:
        # Bautizar con el nombre del miembro
        await session.execute(
            update(Cliente).where(Cliente.id == cliente_proxy.id).values(
                nombre=f"[ADMIN] {miembro.nombre}"
            )
        )
    await guardar_conversacion(
        session,
        cliente_id=cliente_proxy.id,
        direccion="inbound",
        tipo=msg.tipo,
        contenido=msg.texto,
        whapi_message_id=msg.id,
        media_url=msg.media_url,
        metadata={"es_equipo": True, "miembro": miembro.nombre},
    )

    # Descargar imagen si llegó (multimodal vía visión)
    imagen_b64: str | None = None
    imagen_mime: str | None = None
    if msg.tipo == "imagen" and msg.media_url:
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(msg.media_url, headers=auth_headers())
                if r.status_code < 400 and len(r.content) <= 5 * 1024 * 1024:
                    imagen_b64 = base64.b64encode(r.content).decode("ascii")
                    imagen_mime = msg.media_mime or "image/jpeg"
                    log.info("flow_equipo.imagen.descargada",
                             miembro=miembro.nombre, bytes=len(r.content))
        except Exception as e:
            log.warning("flow_equipo.imagen.fail_download", error=str(e))

    # 1. Construir contexto operativo + memoria evolutiva
    contexto = await _construir_contexto(session)
    from app import memoria as mem
    memorias = await mem.cargar_relevantes(session, contacto_id=cliente_proxy.id)
    bloque_memoria = mem.formatear_para_prompt(memorias)

    # 2. System prompt + contexto
    es_cliente = (miembro.rol or "").lower() == "cliente"
    nota_scope = ""
    if es_cliente:
        nota_scope = (
            "\n\n⚠️ QUIEN TE ESCRIBE ES UN CLIENTE, no del equipo interno. "
            "Solo puedes darle información de SU propia reserva. NO ejecutes "
            "acciones internas (editar/cancelar reservas de terceros, marcar covers, "
            "crear/borrar eventos, ver el resumen del día). Si pide algo fuera de su "
            "alcance, dile con amabilidad que lo gestiona el equipo."
        )
    system = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT_EQUIPO,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "## INFO DE LA CANTINA PLUS (úsalo como fuente oficial)\n\n" + bloque_info_venue(),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": f"Quien te escribe: {miembro.nombre} (rol: {miembro.rol or 'equipo'}).{nota_scope}\n\n"
                    f"## CONTEXTO ACTUAL\n\n{contexto}"
                    + (f"\n\n{bloque_memoria}" if bloque_memoria else ""),
        },
    ]

    # Traer historial reciente del chat admin↔bot (últimos 12 turnos, 6h max).
    # Esto evita que el bot equipo "pierda contexto" entre mensajes consecutivos
    # del mismo admin — antes el flow procesaba cada msg como turn aislado.
    from datetime import datetime, timedelta, timezone
    ventana = datetime.now(timezone.utc) - timedelta(hours=6)
    historial_db = (await session.execute(
        select(Conversacion)
        .where(Conversacion.cliente_id == cliente_proxy.id)
        .where(Conversacion.timestamp >= ventana)
        .order_by(Conversacion.timestamp.desc())
        .limit(13)  # 12 turnos + el actual que acabamos de insertar (se excluye)
    )).scalars().all()
    historial_db = list(reversed(historial_db))[:-1]  # excluir el último (es el actual)

    historial_msgs: list[dict] = []
    for h in historial_db:
        if not (h.contenido or "").strip():
            continue
        if h.direccion == "inbound":
            historial_msgs.append({"role": "user", "content": h.contenido})
        elif h.direccion in ("outbound", "humano"):
            historial_msgs.append({"role": "assistant", "content": h.contenido})

    # Construir el primer user message (multimodal si hay imagen)
    if imagen_b64:
        user_content: list[dict] | str = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": imagen_mime or "image/jpeg",
                    "data": imagen_b64,
                },
            },
            {"type": "text", "text": instruccion},
        ]
    else:
        user_content = instruccion
    messages = historial_msgs + [{"role": "user", "content": user_content}]
    ctx_tool = {
        "session": session,
        "miembro_nombre": miembro.nombre,
        "miembro_numero": miembro.numero_whatsapp,
        "rol": miembro.rol,
    }

    tokens_in = tokens_out = cache_r = cache_w = 0
    costo = Decimal("0")
    tools_usadas: list[str] = []

    # 3. Tool use loop (igual que flow cliente, max 5 rondas)
    for ronda in range(5):
        try:
            resp = await _client.messages.create(
                model=settings.claude_model_principal,
                max_tokens=settings.claude_max_tokens_output,
                system=system,
                tools=TOOL_DEFINITIONS_EQUIPO,
                messages=messages,
            )
        except Exception as e:
            log.exception("flow_equipo.claude_fail", error=str(e))
            # Si el destino es el grupo EQUIPO CANTINA o un miembro del equipo,
            # mostramos el error allí (son técnicos y entienden). Si es un
            # CLIENTE whitelisted, NO enviamos el error al cliente — solo
            # notificamos al grupo EQUIPO CANTINA y registramos alerta.
            from app.equipo.directorio import es_miembro_equipo
            dest = destino_envio or ""
            es_grupo = dest.endswith("@g.us")
            num_check = dest.replace("@s.whatsapp.net", "")
            if not num_check.startswith("+"):
                num_check = "+" + num_check
            es_canal_tecnico = es_grupo or bool(es_miembro_equipo(num_check))

            if es_canal_tecnico:
                try:
                    await enviar_texto(
                        destino_envio,
                        f"❌ Falló Claude: {str(e)[:120]}. Reintentá en un momento.",
                    )
                except Exception:
                    pass
            else:
                # Cliente WL — el cliente NO debe ver el error técnico.
                try:
                    from app.notif_equipo import notificar_equipo
                    await notificar_equipo(
                        f"⚠️ *Falló Claude atendiendo a cliente WL*\n\n"
                        f"📱 {dest}\n👤 {miembro.nombre}\n"
                        f"Error: {str(e)[:200]}\n\n"
                        f"_El cliente no recibió respuesta. Atender desde el admin._"
                    )
                except Exception:
                    pass
                try:
                    from app.db.repos import registrar_alerta_fabio
                    await registrar_alerta_fabio(
                        session, tipo="claude_api_fail",
                        mensaje=(
                            f"Falló Claude atendiendo a cliente WL {miembro.nombre} "
                            f"({dest}). Error: {str(e)[:300]}."
                        ),
                    )
                except Exception:
                    pass
            return

        usage = getattr(resp, "usage", None)
        tokens_in += getattr(usage, "input_tokens", 0) or 0
        tokens_out += getattr(usage, "output_tokens", 0) or 0
        cache_r += getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_w += getattr(usage, "cache_creation_input_tokens", 0) or 0
        costo += _calcular_costo(usage)

        text_chunks: list[str] = []
        tool_uses: list[dict] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_chunks.append(block.text)
            elif btype == "tool_use":
                tool_uses.append({"id": block.id, "name": block.name, "input": block.input})

        stop_reason = getattr(resp, "stop_reason", None)
        if stop_reason != "tool_use" or not tool_uses:
            texto_final = "\n".join(t.strip() for t in text_chunks if t and t.strip()).strip()
            if texto_final:
                # Responder al miembro (chat personal) o al grupo si vino del grupo.
                try:
                    await enviar_texto(destino_envio, texto_final)
                except Exception as e:
                    log.error("flow_equipo.enviar_confirmacion_fail", error=str(e))
                # Persistir outbound para que aparezca en /admin/chats
                try:
                    await guardar_conversacion(
                        session,
                        cliente_id=cliente_proxy.id,
                        direccion="outbound",
                        tipo="texto",
                        contenido=texto_final,
                        modelo=settings.claude_model_principal,
                        tokens_input=tokens_in,
                        tokens_output=tokens_out,
                        cache_read_tokens=cache_r,
                        cache_create_tokens=cache_w,
                        metadata={
                            "es_equipo": True,
                            "miembro": miembro.nombre,
                            "tools": tools_usadas,
                            "costo_usd": str(costo),
                        },
                    )
                except Exception as e:
                    log.warning("flow_equipo.persistir_outbound_fail", error=str(e))
            log.info(
                "flow_equipo.respondido",
                miembro=miembro.nombre,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read=cache_r,
                costo_usd=str(costo),
                tools=tools_usadas,
            )
            return

        # Hay tools — ejecutar
        assistant_content: list[dict] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif getattr(block, "type", None) == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results: list[dict] = []
        import json
        for tu in tool_uses:
            log.info("flow_equipo.tool_call", tool=tu["name"], input=tu["input"])
            tools_usadas.append(tu["name"])
            result = await ejecutar_tool_equipo(tu["name"], tu["input"], ctx_tool)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu["id"],
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

        messages.append({"role": "user", "content": tool_results})

    log.warning("flow_equipo.max_loops")
