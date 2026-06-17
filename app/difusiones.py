"""Motor de difusiones WhatsApp.

Una difusión congela destinatarios antes de enviar. Esto evita que una campaña
cambie a mitad de ejecución si alguien edita tags o importa contactos.
"""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import guardar_conversacion
from app.db.session import async_session_factory
from app.logging_setup import log
from app.whapi.client import WhapiError, enviar_imagen_bytes, enviar_imagen_url, enviar_texto


ETIQUETAS_DIFUSION = {"todos", "cliente", "prospecto", "sin_clasificar"}


@dataclass(frozen=True)
class SegmentoDifusion:
    etiqueta: str = "todos"
    tag_id: int | None = None
    incluir_sin_chat: bool = True


def normalizar_etiqueta_difusion(etiqueta: str | None) -> str:
    value = (etiqueta or "todos").strip().lower()
    return value if value in ETIQUETAS_DIFUSION else "todos"


def render_mensaje_difusion(template: str, *, nombre: str | None, numero: str) -> str:
    """Render sencillo y seguro para variables de difusión.

    Soporta `{nombre}`, `{primer_nombre}` y `{numero}`. Si no hay nombre, usa
    "parce" como fallback cálido pero neutro para La Cantina.
    """
    nombre_limpio = " ".join((nombre or "").split())
    primer_nombre = nombre_limpio.split(" ", 1)[0] if nombre_limpio else "parce"
    data = {
        "nombre": nombre_limpio or primer_nombre,
        "primer_nombre": primer_nombre,
        "numero": numero,
    }

    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip().lower()
        return data.get(key, match.group(0))

    return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", repl, template)


async def contar_destinatarios(
    session: AsyncSession,
    segmento: SegmentoDifusion,
) -> int:
    row = (await session.execute(_query_destinatarios(count_only=True), _params(segmento))).first()
    return int(row[0] if row else 0)


async def preparar_difusion(
    session: AsyncSession,
    *,
    nombre: str,
    mensaje: str,
    media_url: str | None,
    segmento: SegmentoDifusion,
    delay_min_s: Decimal,
    delay_max_s: Decimal,
    dry_run: bool,
    creado_por: str | None,
) -> int:
    """Crea la difusión y congela destinatarios elegibles."""
    delay_min_s, delay_max_s = _ordenar_delays(delay_min_s, delay_max_s)
    result = await session.execute(sa_text(
        """
        INSERT INTO difusiones (
            nombre, mensaje, media_url, filtro_etiqueta, filtro_tag_id,
            incluir_sin_chat, delay_min_s, delay_max_s, dry_run, creado_por
        )
        VALUES (
            :nombre, :mensaje, NULLIF(:media_url, ''), :etiqueta, :tag_id,
            :incluir_sin_chat, :delay_min_s, :delay_max_s, :dry_run, :creado_por
        )
        RETURNING id
        """
    ), {
        "nombre": nombre.strip()[:140],
        "mensaje": mensaje.strip(),
        "media_url": (media_url or "").strip(),
        "etiqueta": segmento.etiqueta,
        "tag_id": segmento.tag_id,
        "incluir_sin_chat": segmento.incluir_sin_chat,
        "delay_min_s": delay_min_s,
        "delay_max_s": delay_max_s,
        "dry_run": dry_run,
        "creado_por": creado_por,
    })
    difusion_id = int(result.scalar_one())
    await session.execute(sa_text(
        f"""
        INSERT INTO difusion_destinatarios (difusion_id, cliente_id, numero_whatsapp, nombre)
        SELECT :difusion_id, q.id, q.numero_whatsapp, q.nombre
        FROM ({_query_destinatarios(count_only=False).text}) q
        ON CONFLICT (difusion_id, numero_whatsapp) DO NOTHING
        """
    ), {"difusion_id": difusion_id, **_params(segmento)})
    await _recalcular_contadores(session, difusion_id)
    await session.commit()
    log.info("difusion.preparada", difusion_id=difusion_id, creado_por=creado_por)
    return difusion_id


async def ejecutar_difusion_background(difusion_id: int) -> None:
    """Ejecuta una difusión en su propia sesión."""
    async with async_session_factory() as session:
        try:
            await ejecutar_difusion(session, difusion_id)
        except Exception as exc:
            await session.rollback()
            async with async_session_factory() as fail_session:
                await fail_session.execute(sa_text(
                    """
                    UPDATE difusiones
                       SET estado='error',
                           metadata = metadata || jsonb_build_object('error', :error),
                           finished_at=now(), updated_at=now()
                     WHERE id=:id
                    """
                ), {"id": difusion_id, "error": str(exc)[:500]})
                await fail_session.commit()
            log.exception("difusion.background.fail", difusion_id=difusion_id, error=str(exc))


async def ejecutar_difusion(session: AsyncSession, difusion_id: int) -> dict[str, Any]:
    row = (await session.execute(sa_text(
        """
        SELECT id, mensaje, media_url, estado, delay_min_s, delay_max_s, dry_run, metadata
          FROM difusiones
         WHERE id=:id
         FOR UPDATE
        """
    ), {"id": difusion_id})).mappings().first()
    if not row:
        return {"ok": False, "error": "difusion_no_existe"}
    if row["estado"] not in ("borrador", "pausada", "error"):
        return {"ok": False, "error": f"estado_no_ejecutable:{row['estado']}"}

    await session.execute(sa_text(
        "UPDATE difusiones SET estado='enviando', started_at=COALESCE(started_at, now()), updated_at=now() WHERE id=:id"
    ), {"id": difusion_id})
    await session.commit()

    pendientes = (await session.execute(sa_text(
        """
        SELECT id, cliente_id, numero_whatsapp, nombre
          FROM difusion_destinatarios
         WHERE difusion_id=:id AND estado='pendiente'
         ORDER BY id
        """
    ), {"id": difusion_id})).mappings().all()

    delay_min, delay_max = _ordenar_delays(row["delay_min_s"], row["delay_max_s"])
    enviados = fallidos = omitidos = 0
    for idx, dest in enumerate(pendientes):
        estado = (await session.execute(
            sa_text("SELECT estado FROM difusiones WHERE id=:id"),
            {"id": difusion_id},
        )).scalar_one_or_none()
        if estado in ("pausada", "cancelada"):
            break

        mensaje = render_mensaje_difusion(
            row["mensaje"],
            nombre=dest["nombre"],
            numero=dest["numero_whatsapp"],
        )
        try:
            metadata = row["metadata"] or {}
            media_path = metadata.get("media_path") if isinstance(metadata, dict) else None
            media_mime = metadata.get("media_mime") if isinstance(metadata, dict) else None
            if row["dry_run"]:
                resp: dict[str, Any] = {"id": "dry-run"}
            elif media_path:
                path = Path(media_path)
                if not path.exists():
                    raise WhapiError(f"media local no existe: {path}")
                resp = await enviar_imagen_bytes(
                    dest["numero_whatsapp"],
                    path.read_bytes(),
                    mime=media_mime or "image/jpeg",
                    caption=mensaje,
                    filename=path.name,
                )
            elif row["media_url"]:
                resp = await enviar_imagen_url(dest["numero_whatsapp"], row["media_url"], caption=mensaje)
            else:
                resp = await enviar_texto(dest["numero_whatsapp"], mensaje)
            whapi_id = _extraer_message_id(resp)
            await session.execute(sa_text(
                """
                UPDATE difusion_destinatarios
                   SET estado='enviado', whapi_message_id=:mid, enviado_en=now(),
                       error=NULL, updated_at=now()
                 WHERE id=:id
                """
            ), {"id": dest["id"], "mid": whapi_id})
            if dest["cliente_id"]:
                await guardar_conversacion(
                    session,
                    cliente_id=dest["cliente_id"],
                    direccion="outbound",
                    tipo="imagen" if (row["media_url"] or media_path) else "texto",
                    contenido=mensaje,
                    whapi_message_id=whapi_id,
                    media_url=row["media_url"] or media_path,
                    metadata={"origen": "difusion", "difusion_id": difusion_id},
                )
            enviados += 1
        except WhapiError as exc:
            fallidos += 1
            await _marcar_destinatario_fallido(session, dest["id"], str(exc))
        except Exception as exc:
            fallidos += 1
            await _marcar_destinatario_fallido(session, dest["id"], str(exc))
        await _recalcular_contadores(session, difusion_id)
        await session.commit()

        if idx < len(pendientes) - 1 and not row["dry_run"]:
            await asyncio.sleep(random.uniform(float(delay_min), float(delay_max)))

    await _recalcular_contadores(session, difusion_id)
    pendientes_restantes = await session.scalar(sa_text(
        "SELECT count(*) FROM difusion_destinatarios WHERE difusion_id=:id AND estado='pendiente'"
    ), {"id": difusion_id})
    estado_final = "enviada" if int(pendientes_restantes or 0) == 0 else "pausada"
    await session.execute(sa_text(
        "UPDATE difusiones SET estado=:estado, finished_at=CASE WHEN :estado='enviada' THEN now() ELSE finished_at END, updated_at=now() WHERE id=:id"
    ), {"id": difusion_id, "estado": estado_final})
    await session.commit()
    log.info(
        "difusion.ejecutada",
        difusion_id=difusion_id,
        enviados=enviados,
        fallidos=fallidos,
        omitidos=omitidos,
        estado=estado_final,
    )
    return {"ok": True, "enviados": enviados, "fallidos": fallidos, "omitidos": omitidos}


async def pausar_difusion(session: AsyncSession, difusion_id: int) -> None:
    await session.execute(sa_text(
        "UPDATE difusiones SET estado='pausada', updated_at=now() WHERE id=:id AND estado='enviando'"
    ), {"id": difusion_id})
    await session.commit()


async def cancelar_difusion(session: AsyncSession, difusion_id: int) -> None:
    await session.execute(sa_text(
        """
        UPDATE difusion_destinatarios
           SET estado='omitido', error='campaña cancelada', updated_at=now()
         WHERE difusion_id=:id AND estado='pendiente'
        """
    ), {"id": difusion_id})
    await session.execute(sa_text(
        "UPDATE difusiones SET estado='cancelada', finished_at=now(), updated_at=now() WHERE id=:id"
    ), {"id": difusion_id})
    await _recalcular_contadores(session, difusion_id)
    await session.commit()


def _params(segmento: SegmentoDifusion) -> dict[str, Any]:
    return {
        "etiqueta": segmento.etiqueta,
        "tag_id": segmento.tag_id,
        "incluir_sin_chat": segmento.incluir_sin_chat,
    }


def _query_destinatarios(*, count_only: bool):
    select_clause = "count(*)" if count_only else "c.id, c.numero_whatsapp, c.nombre"
    return sa_text(f"""
        SELECT {select_clause}
          FROM clientes c
     LEFT JOIN numeros_internos ni
            ON ni.numero_whatsapp = c.numero_whatsapp AND ni.activo = true
     LEFT JOIN equipo_miembros em
            ON em.numero_whatsapp = c.numero_whatsapp AND em.activo = true
         WHERE c.bloqueado = false
           AND c.numero_whatsapp NOT LIKE '%@g.us'
           AND ni.id IS NULL
           AND em.id IS NULL
           AND COALESCE(c.etiqueta, '') NOT IN ('personal', 'equipo')
           AND (
                :etiqueta = 'todos'
                OR (:etiqueta = 'sin_clasificar' AND c.etiqueta IS NULL)
                OR c.etiqueta = :etiqueta
           )
           AND (
                :incluir_sin_chat = true
                OR EXISTS (
                    SELECT 1 FROM conversaciones cv
                     WHERE cv.cliente_id = c.id
                     LIMIT 1
                )
           )
           AND (
                CAST(:tag_id AS integer) IS NULL
                OR EXISTS (
                    SELECT 1 FROM cliente_tags ct
                     WHERE ct.cliente_id = c.id AND ct.tag_id = CAST(:tag_id AS integer)
                )
           )
    """)


async def _marcar_destinatario_fallido(
    session: AsyncSession,
    destinatario_id: int,
    error: str,
) -> None:
    await session.execute(sa_text(
        """
        UPDATE difusion_destinatarios
           SET estado='fallido', error=:error, updated_at=now()
         WHERE id=:id
        """
    ), {"id": destinatario_id, "error": error[:800]})


async def _recalcular_contadores(session: AsyncSession, difusion_id: int) -> None:
    await session.execute(sa_text(
        """
        UPDATE difusiones d
           SET total_destinatarios = s.total,
               enviados = s.enviados,
               fallidos = s.fallidos,
               omitidos = s.omitidos,
               updated_at = now()
          FROM (
                SELECT difusion_id,
                       count(*)::int AS total,
                       count(*) FILTER (WHERE estado='enviado')::int AS enviados,
                       count(*) FILTER (WHERE estado='fallido')::int AS fallidos,
                       count(*) FILTER (WHERE estado='omitido')::int AS omitidos
                  FROM difusion_destinatarios
                 WHERE difusion_id=:id
                 GROUP BY difusion_id
          ) s
         WHERE d.id=s.difusion_id
        """
    ), {"id": difusion_id})


def _ordenar_delays(a: Decimal | float | int, b: Decimal | float | int) -> tuple[Decimal, Decimal]:
    left = max(Decimal("0"), Decimal(str(a)))
    right = max(Decimal("0"), Decimal(str(b)))
    if right < left:
        left, right = right, left
    return left, right


def _extraer_message_id(resp: dict[str, Any]) -> str | None:
    if not isinstance(resp, dict):
        return None
    for key in ("id", "message_id"):
        if resp.get(key):
            return str(resp[key])
    message = resp.get("message")
    if isinstance(message, dict):
        for key in ("id", "message_id"):
            if message.get(key):
                return str(message[key])
    messages = resp.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        return _extraer_message_id(messages[0])
    return None
