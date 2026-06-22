-- Estados de WhatsApp de una sola ejecucion, programados desde el chat del equipo.

CREATE TABLE IF NOT EXISTS estados_programados (
    id                  SERIAL PRIMARY KEY,
    tipo                VARCHAR(10) NOT NULL CHECK (tipo IN ('imagen', 'video')),
    media_path          TEXT NOT NULL,
    media_mime          VARCHAR(100) NOT NULL,
    caption             TEXT,
    programado_para     TIMESTAMPTZ NOT NULL,
    proximo_intento     TIMESTAMPTZ NOT NULL,
    estado              VARCHAR(20) NOT NULL DEFAULT 'pendiente'
                        CHECK (estado IN ('pendiente', 'procesando', 'publicado', 'fallido', 'cancelado')),
    intentos            INTEGER NOT NULL DEFAULT 0,
    whapi_message_id    VARCHAR(120),
    error               TEXT,
    creado_por          VARCHAR(100),
    publicado_en        TIMESTAMPTZ,
    cancelado_en        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_estados_programados_pendientes
    ON estados_programados(proximo_intento)
    WHERE estado = 'pendiente';
