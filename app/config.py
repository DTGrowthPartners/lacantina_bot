"""Configuración leída de .env vía Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Entorno ─────────────────────────────────────────────────────────────
    bot_env: str = "development"
    tz: str = "America/Bogota"

    # ── DB del bot (chats, miembros, alertas, pausas). ──────────────────────
    # La DB de MESAS vive aparte (cantina-mesas-backend, SQLite) y se consume
    # vía HTTP en `cantina_api.py`.
    database_url: str = Field(
        default="postgresql+asyncpg://cantina_user:CHANGEME@127.0.0.1:5432/cantina_db"
    )
    database_url_sync: str = Field(
        default="postgresql://cantina_user:CHANGEME@127.0.0.1:5432/cantina_db"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 10

    # ── Anthropic / Claude ──────────────────────────────────────────────────
    anthropic_api_key: str = Field(default="")
    claude_model_principal: str = "claude-sonnet-4-6"
    claude_model_intent: str = "claude-haiku-4-5-20251001"
    claude_max_tokens_output: int = 1024
    claude_provider: str = "direct"           # direct | dario | fallback
    dario_base_url: str = "http://127.0.0.1:3456"
    dario_api_key: str = "dario"

    # ── OpenAI (Whisper — transcripción de notas de voz) ────────────────────
    openai_api_key: str = Field(default="")
    whisper_model: str = "whisper-1"          # whisper-1 | gpt-4o-mini-transcribe | gpt-4o-transcribe
    whisper_idioma: str = "es"                # hint de idioma → mejor precisión
    feature_transcribir_audio: bool = True

    # ── whapi (canal WhatsApp de La Cantina) ────────────────────────────────
    whapi_base_url: str = "https://gate.whapi.cloud"
    whapi_token: str = Field(default="")
    whapi_numero_bot: str = "+573008641322"   # número del bot oficial del venue
    whapi_webhook_secret: str = Field(default="")
    whapi_webhook_url: str = "https://cantina-bot.dtgrowthpartners.com/webhook"

    # ── Identidad ───────────────────────────────────────────────────────────
    identidad_principal_nombre: str = "La Cantina Plus"
    identidad_principal_persona_file: str = "cantina-identidad.md"
    # Política estricta = false: la Cantina recibe randoms del público (Instagram,
    # Google, gente que pregunta horarios). NO silenciar a desconocidos.
    identidad_principal_estricta: bool = False

    # ── CANTINA API (backend de mesas — fuente de verdad) ──────────────────
    cantina_api_base_url: str = "https://cantina-api.dtgrowthpartners.com"
    cantina_api_key: str = Field(default="")

    # ── Grupo WhatsApp del equipo (alertas) ────────────────────────────────
    # Donde el bot publica: comprobantes de cover, escalaciones, resúmenes
    # diarios, etc. Miembros: Fabio, Edgardo, Mariana, Diana, etc.
    equipo_cantina_group_id: str = Field(default="")
    # Otros grupos donde el bot acepta instrucciones del equipo. Lista CSV.
    # Las alertas automáticas siguen saliendo al grupo principal de arriba.
    equipo_cantina_group_ids: str = Field(default="")

    # ── Bot HTTP ────────────────────────────────────────────────────────────
    bot_host: str = "127.0.0.1"
    bot_port: int = 8012
    bot_log_level: str = "INFO"
    bot_log_file: str = "/home/ubuntu/cantina-bot/logs/cantina.log"

    # ── Rate limit ──────────────────────────────────────────────────────────
    rate_limit_mensajes_por_minuto_cliente: int = 10
    humanization_max_out_per_hour: int = 100

    # ── Humanización ────────────────────────────────────────────────────────
    humanization_delay_min_s: float = 30.0
    humanization_delay_max_s: float = 120.0
    humanization_delay_por_caracter_s: float = 0.04
    humanization_typing_indicator: bool = True
    horario_inicio_hora: int = 10
    horario_fin_hora: int = 23
    feature_humanizacion: bool = True
    feature_responder_24_7: bool = True       # opera tarde/noche, no cerramos

    # ── API externa / plataforma de administración ──────────────────────────
    # Permite a una plataforma externa: activar/desactivar el bot, ver stats y
    # health, y recibir webhooks salientes firmados (HMAC-SHA256).
    bot_id: str = "cantina-plus"               # identifica ESTE bot ante la plataforma
    control_api_key: str = Field(default="")   # Bearer para /api/v1/* (vacío = API deshabilitada)
    webhook_url: str = Field(default="")       # URL de la plataforma para push (vacío = sin push)
    webhook_secret: str = Field(default="")    # secreto HMAC de los webhooks salientes

    # ── Admin panel ────────────────────────────────────────────────────────
    admin_user: str = "admin"
    admin_password: str = "cambiame_en_env"
    admin_session_secret: str = "set_un_string_random_de_32_chars"

    # ── Paths ───────────────────────────────────────────────────────────────
    # Ajustar a la ruta de deploy. Por defecto, asume estructura del proyecto.
    data_dir: str = "data"
    prompts_dir: str = "data/prompts"

    # ── Feature flags ───────────────────────────────────────────────────────
    feature_human_takeover: bool = True       # si staff responde manual → pausa bot 1h

    @property
    def is_production(self) -> bool:
        return self.bot_env.lower() == "production"

    @property
    def prompts_path(self) -> Path:
        return Path(self.prompts_dir)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
