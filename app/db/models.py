"""SQLAlchemy 2.0 models — mapeo de las tablas creadas por schema.sql."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship  # noqa: F401


class Base(DeclarativeBase):
    pass


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 32 chars: caben números E.164 y también group_id de WhatsApp (…@g.us, 23 ch)
    # para representar el grupo del equipo como un "chat" en el admin.
    numero_whatsapp: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    nombre: Mapped[str | None] = mapped_column(String(255))
    cedula: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(120))
    ciudad: Mapped[str | None] = mapped_column(String(100))
    barrio: Mapped[str | None] = mapped_column(String(150))
    primer_contacto: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ultimo_contacto: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    es_mayorista: Mapped[bool] = mapped_column(Boolean, default=False)
    bloqueado: Mapped[bool] = mapped_column(Boolean, default=False)
    razon_bloqueo: Mapped[str | None] = mapped_column(Text)
    # Etiqueta de relación: cliente / equipo / personal / NULL.
    # `personal` = el bot NUNCA debe responder (contacto privado del dueño del número).
    etiqueta: Mapped[str | None] = mapped_column(String(20))
    etiqueta_actualizada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etiqueta_actualizada_por: Mapped[str | None] = mapped_column(String(60))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class Conversacion(Base):
    __tablename__ = "conversaciones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    whapi_message_id: Mapped[str | None] = mapped_column(String(100))
    direccion: Mapped[str] = mapped_column(String(10))  # inbound | outbound | humano
    tipo: Mapped[str] = mapped_column(String(20))
    contenido: Mapped[str | None] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(Text)
    media_path_local: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(50))
    tokens_input: Mapped[int | None] = mapped_column(Integer)
    tokens_output: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_create_tokens: Mapped[int | None] = mapped_column(Integer)
    costo_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    modelo: Mapped[str | None] = mapped_column(String(50))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)

    cliente: Mapped["Cliente"] = relationship(lazy="selectin")


class WebhookProcesado(Base):
    __tablename__ = "webhooks_procesados"

    message_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    procesado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tag(Base):
    """Tag de seguimiento aplicable a clientes (M2M). Independiente de
    `cliente.etiqueta` que clasifica el routing del bot."""
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(50), unique=True)
    color: Mapped[str] = mapped_column(String(7), default="#7C3AED")
    descripcion: Mapped[str | None] = mapped_column(Text)
    orden: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(60))


class ClienteTag(Base):
    """Asignación cliente↔tag (M2M)."""
    __tablename__ = "cliente_tags"

    cliente_id: Mapped[int] = mapped_column(
        ForeignKey("clientes.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    added_by: Mapped[str | None] = mapped_column(String(60))


class IntervencionHumana(Base):
    __tablename__ = "intervencion_humana"

    cliente_id: Mapped[int] = mapped_column(
        ForeignKey("clientes.id", ondelete="CASCADE"), primary_key=True
    )
    pausado_hasta: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    razon: Mapped[str | None] = mapped_column(Text)
    activado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activado_por: Mapped[str | None] = mapped_column(String(50))


class Sesion(Base):
    __tablename__ = "sesiones"

    cliente_id: Mapped[int] = mapped_column(
        ForeignKey("clientes.id", ondelete="CASCADE"), primary_key=True
    )
    estado: Mapped[str] = mapped_column(String(50), default="inicial")
    producto_actual_ref: Mapped[str | None] = mapped_column(String(50))
    productos_mostrados: Mapped[list[str]] = mapped_column(JSONB, default=list)
    talla_interes: Mapped[str | None] = mapped_column(String(20))
    color_interes: Mapped[str | None] = mapped_column(String(50))
    metodo_pago_elegido: Mapped[str | None] = mapped_column(String(50))
    banco_elegido: Mapped[str | None] = mapped_column(String(50))
    barrio: Mapped[str | None] = mapped_column(String(150))
    direccion_envio: Mapped[str | None] = mapped_column(Text)
    notas_internas: Mapped[str | None] = mapped_column(Text)
    ultima_interaccion: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    proximo_seguimiento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertaFabio(Base):
    __tablename__ = "alertas_fabio"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id", ondelete="SET NULL"))
    tipo: Mapped[str] = mapped_column(String(50))
    mensaje: Mapped[str] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(Text)
    whapi_message_id: Mapped[str | None] = mapped_column(String(100))
    enviado_a_fabio_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resuelto: Mapped[bool] = mapped_column(Boolean, default=False)
    resuelto_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EquipoMiembro(Base):
    """Superior del equipo que recibe escalaciones del bot."""
    __tablename__ = "equipo_miembros"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100))
    numero_whatsapp: Mapped[str] = mapped_column(String(20), unique=True)
    rol: Mapped[str | None] = mapped_column(String(50))
    areas: Mapped[list[str]] = mapped_column(JSONB, default=list)
    es_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    horario_lunes_sabado: Mapped[str | None] = mapped_column(String(30))
    horario_domingo: Mapped[str | None] = mapped_column(String(30))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    notas: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __str__(self) -> str:
        return f"{self.nombre} ({self.numero_whatsapp})"


class NumeroInterno(Base):
    """Número del equipo que el bot ignora silenciosamente."""
    __tablename__ = "numeros_internos"

    id: Mapped[int] = mapped_column(primary_key=True)
    numero_whatsapp: Mapped[str] = mapped_column(String(20), unique=True)
    nombre: Mapped[str | None] = mapped_column(String(100))
    razon: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __str__(self) -> str:
        return f"{self.numero_whatsapp} — {self.nombre or '?'}"


# ────────────────────────────────────────────────────────────────────────────
# Whitelist + memoria
# ────────────────────────────────────────────────────────────────────────────


class ContactoWhitelist(Base):
    """Whitelist maestra de contactos conocidos.

    Define quién es cada número conocido y con qué permisos. El routing del
    webhook consulta esta tabla: si el número está aquí (rol equipo o cliente)
    → flujo OPERATIVO; si no → flujo cliente normal.
    """
    __tablename__ = "contactos_whitelist"

    id: Mapped[int] = mapped_column(primary_key=True)
    numero_whatsapp: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    rol: Mapped[str] = mapped_column(String(20))  # equipo | cliente
    nombre: Mapped[str | None] = mapped_column(String(150))
    empresa: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(120))
    nit: Mapped[str | None] = mapped_column(String(30))
    dtos_client_id: Mapped[str | None] = mapped_column(String(60))   # id del cliente en DT-OS
    meta_account_id: Mapped[str | None] = mapped_column(String(60))  # act_... de Meta Ads
    permisos: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    notas: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __str__(self) -> str:
        return f"{self.nombre or '?'} ({self.numero_whatsapp}) — {self.rol}"


class Memoria(Base):
    """Cosas que María APRENDE y recuerda entre conversaciones (reglas, hechos,
    preferencias). Se cargan al system prompt cada turn según el scope.
    """
    __tablename__ = "memorias"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(20))   # general | contacto | equipo
    contacto_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"))
    titulo: Mapped[str] = mapped_column(String(180))
    contenido: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(String(30), default="regla")
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_por: Mapped[str | None] = mapped_column(String(60))
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Recordatorio(Base):
    """Pendiente con fecha (promesa, follow-up, seguimiento). El heartbeat los lee."""
    __tablename__ = "recordatorios"

    id: Mapped[int] = mapped_column(primary_key=True)
    contacto_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"))
    accion: Mapped[str] = mapped_column(Text)
    motivo: Mapped[str | None] = mapped_column(Text)
    vence_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    origen: Mapped[str] = mapped_column(String(30), default="manual")
    creado_por: Mapped[str | None] = mapped_column(String(60))
    atendido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    atendido_notas: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TareaProgramada(Base):
    """Tarea recurrente disparada por cron. Ejecutada por app.automatizaciones.scheduler."""
    __tablename__ = "tareas_programadas"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    cron: Mapped[str] = mapped_column(String(60))  # ej "0 19 * * *"
    zona_horaria: Mapped[str] = mapped_column(String(60), default="America/Bogota")
    accion: Mapped[str] = mapped_column(String(60))  # ver acciones.py
    parametros: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    ultima_ejecucion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proxima_ejecucion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_resultado: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    creado_por: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __str__(self) -> str:
        return f"{self.nombre} ({self.accion})"


class EstadoProgramado(Base):
    """Estado de WhatsApp de una sola ejecucion."""
    __tablename__ = "estados_programados"

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10))
    media_path: Mapped[str] = mapped_column(Text)
    media_mime: Mapped[str] = mapped_column(String(100))
    caption: Mapped[str | None] = mapped_column(Text)
    programado_para: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    proximo_intento: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    whapi_message_id: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(Text)
    creado_por: Mapped[str | None] = mapped_column(String(100))
    publicado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Difusion(Base):
    """Campaña de difusión WhatsApp con destinatarios congelados."""
    __tablename__ = "difusiones"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(140))
    mensaje: Mapped[str] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(Text)
    filtro_etiqueta: Mapped[str] = mapped_column(String(20), default="todos")
    filtro_tag_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id", ondelete="SET NULL"))
    incluir_sin_chat: Mapped[bool] = mapped_column(Boolean, default=True)
    estado: Mapped[str] = mapped_column(String(20), default="borrador")
    total_destinatarios: Mapped[int] = mapped_column(Integer, default=0)
    enviados: Mapped[int] = mapped_column(Integer, default=0)
    fallidos: Mapped[int] = mapped_column(Integer, default=0)
    omitidos: Mapped[int] = mapped_column(Integer, default=0)
    delay_min_s: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=12)
    delay_max_s: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=25)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    creado_por: Mapped[str | None] = mapped_column(String(60))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DifusionDestinatario(Base):
    """Estado de envío de una difusión para un contacto concreto."""
    __tablename__ = "difusion_destinatarios"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    difusion_id: Mapped[int] = mapped_column(ForeignKey("difusiones.id", ondelete="CASCADE"))
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id", ondelete="SET NULL"))
    numero_whatsapp: Mapped[str] = mapped_column(String(32))
    nombre: Mapped[str | None] = mapped_column(String(255))
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    whapi_message_id: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    enviado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
