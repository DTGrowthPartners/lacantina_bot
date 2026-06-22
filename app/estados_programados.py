"""Programacion de estados de WhatsApp en hora Colombia."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app import promo_estado
from app.config import get_settings
from app.db.session import async_session_factory
from app.logging_setup import log
from app.whapi.client import publicar_story_imagen_bytes, publicar_story_video_bytes

BOGOTA = ZoneInfo("America/Bogota")
MAX_INTENTOS = 3
REINTENTO_MINUTOS = 5

_DIR = (Path(get_settings().data_dir) / "media" / "estados-programados").resolve()
_HORA_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", re.IGNORECASE)
_EXTENSIONES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}


def _normalizar_hora_12h(hora: str) -> str:
    valor = (hora or "").strip().lower().replace(".", "")
    valor = re.sub(r"\s+", " ", valor)
    valor = re.sub(r"\ba\s*m\b", "am", valor)
    valor = re.sub(r"\bp\s*m\b", "pm", valor)
    return valor.replace(" ", "")


def parsear_fecha_hora_colombia(
    fecha: str,
    hora_12h: str,
    *,
    ahora: datetime | None = None,
) -> datetime:
    """Convierte YYYY-MM-DD + hora de 12h a un datetime aware de Bogota."""
    try:
        dia = date.fromisoformat((fecha or "").strip())
    except ValueError as exc:
        raise ValueError("La fecha debe estar en formato YYYY-MM-DD.") from exc

    match = _HORA_RE.fullmatch(_normalizar_hora_12h(hora_12h))
    if not match:
        raise ValueError("La hora debe usar formato de 12 horas, por ejemplo 7:30 PM.")
    hora = int(match.group(1))
    minuto = int(match.group(2) or 0)
    periodo = match.group(3).lower()
    if not 1 <= hora <= 12 or not 0 <= minuto <= 59:
        raise ValueError("La hora indicada no es valida.")
    hora_24 = (hora % 12) + (12 if periodo == "pm" else 0)
    programado = datetime(dia.year, dia.month, dia.day, hora_24, minuto, tzinfo=BOGOTA)
    referencia = ahora or datetime.now(timezone.utc)
    if referencia.tzinfo is None:
        referencia = referencia.replace(tzinfo=timezone.utc)
    if programado.astimezone(timezone.utc) <= referencia.astimezone(timezone.utc):
        raise ValueError("La fecha y hora deben estar en el futuro.")
    return programado


def formatear_hora_colombia(valor: datetime) -> str:
    local = valor.astimezone(BOGOTA)
    hora = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%d/%m/%Y')} a las {hora} (hora Colombia)"


def _guardar_media(data: bytes, mime: str, tipo: str) -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    ext = _EXTENSIONES.get((mime or "").lower()) or (".mp4" if tipo == "video" else ".jpg")
    path = _DIR / f"{uuid4().hex}{ext}"
    path.write_bytes(data)
    return path


async def crear(
    session: AsyncSession,
    *,
    media_bytes: bytes,
    media_mime: str,
    tipo: str,
    caption: str | None,
    programado_para: datetime,
    creado_por: str | None,
) -> int:
    if tipo not in {"imagen", "video"}:
        raise ValueError("Tipo de estado no soportado.")
    path = _guardar_media(media_bytes, media_mime, tipo)
    try:
        estado_id = (await session.execute(sa_text("""
            INSERT INTO estados_programados
                (tipo, media_path, media_mime, caption, programado_para,
                 proximo_intento, creado_por)
            VALUES (:tipo, :path, :mime, :caption, :programado, :programado, :creado_por)
            RETURNING id
        """), {
            "tipo": tipo,
            "path": str(path),
            "mime": media_mime,
            "caption": caption,
            "programado": programado_para.astimezone(timezone.utc),
            "creado_por": creado_por,
        })).scalar_one()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return int(estado_id)


async def listar(session: AsyncSession, limite: int = 10) -> list[dict]:
    rows = (await session.execute(sa_text("""
        SELECT id, tipo, caption, programado_para, estado, intentos, error
        FROM estados_programados
        WHERE estado IN ('pendiente', 'procesando')
        ORDER BY programado_para ASC
        LIMIT :limite
    """), {"limite": max(1, min(int(limite), 25))})).mappings().all()
    return [dict(row) for row in rows]


async def cancelar(session: AsyncSession, estado_id: int) -> bool:
    row = (await session.execute(sa_text("""
        UPDATE estados_programados
        SET estado='cancelado', cancelado_en=now(), updated_at=now()
        WHERE id=:id AND estado='pendiente'
        RETURNING media_path
    """), {"id": estado_id})).first()
    if not row:
        return False
    try:
        Path(row[0]).unlink(missing_ok=True)
    except Exception as exc:
        log.warning("estados_programados.cancelar_archivo_fail", id=estado_id, error=str(exc))
    return True


def _extraer_message_id(respuesta: dict) -> str | None:
    if not isinstance(respuesta, dict):
        return None
    mensaje = respuesta.get("message")
    if isinstance(mensaje, dict) and mensaje.get("id"):
        return str(mensaje["id"])
    return str(respuesta["id"]) if respuesta.get("id") else None


async def _reclamar_siguiente() -> dict | None:
    async with async_session_factory() as session:
        async with session.begin():
            row = (await session.execute(sa_text("""
                SELECT id, tipo, media_path, media_mime, caption, intentos
                FROM estados_programados
                WHERE estado='pendiente' AND proximo_intento <= now()
                ORDER BY proximo_intento ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """))).mappings().first()
            if not row:
                return None
            await session.execute(sa_text("""
                UPDATE estados_programados
                SET estado='procesando', intentos=intentos+1, updated_at=now()
                WHERE id=:id
            """), {"id": row["id"]})
            item = dict(row)
            item["intentos"] = int(item["intentos"] or 0) + 1
            return item


async def _marcar_resultado(item: dict, *, respuesta: dict | None, error: str | None) -> None:
    async with async_session_factory() as session:
        if error is None:
            await session.execute(sa_text("""
                UPDATE estados_programados
                SET estado='publicado', publicado_en=now(), whapi_message_id=:mid,
                    error=NULL, updated_at=now()
                WHERE id=:id
            """), {"id": item["id"], "mid": _extraer_message_id(respuesta or {})})
        elif item["intentos"] < MAX_INTENTOS:
            await session.execute(sa_text("""
                UPDATE estados_programados
                SET estado='pendiente', proximo_intento=:proximo, error=:error,
                    updated_at=now()
                WHERE id=:id
            """), {
                "id": item["id"],
                "proximo": datetime.now(timezone.utc) + timedelta(minutes=REINTENTO_MINUTOS),
                "error": error[:500],
            })
        else:
            await session.execute(sa_text("""
                UPDATE estados_programados
                SET estado='fallido', error=:error, updated_at=now()
                WHERE id=:id
            """), {"id": item["id"], "error": error[:500]})
        await session.commit()


async def procesar_vencidos(limite: int = 5) -> int:
    """Publica estados vencidos. Es seguro frente a multiples workers."""
    async with async_session_factory() as session:
        await session.execute(sa_text("""
            UPDATE estados_programados
            SET estado='pendiente', proximo_intento=now(),
                error='Recuperado tras proceso interrumpido', updated_at=now()
            WHERE estado='procesando' AND updated_at < now() - interval '15 minutes'
        """))
        await session.commit()

    procesados = 0
    for _ in range(max(1, min(limite, 10))):
        item = await _reclamar_siguiente()
        if item is None:
            break
        try:
            path = Path(item["media_path"])
            data = path.read_bytes()
            if item["tipo"] == "video":
                respuesta = await publicar_story_video_bytes(
                    data,
                    caption=item["caption"],
                    mime=item["media_mime"],
                    filename=path.name,
                )
            else:
                respuesta = await publicar_story_imagen_bytes(
                    data,
                    caption=item["caption"],
                    mime=item["media_mime"],
                    filename=path.name,
                )
            promo_estado.guardar_estado(data, item["media_mime"], item["caption"])
            await _marcar_resultado(item, respuesta=respuesta, error=None)
            procesados += 1
            log.info("estados_programados.publicado", id=item["id"], tipo=item["tipo"])
        except Exception as exc:
            error = str(exc)
            await _marcar_resultado(item, respuesta=None, error=error)
            log.warning(
                "estados_programados.publicar_fail",
                id=item["id"],
                intento=item["intentos"],
                error=error[:200],
            )
    return procesados
