-- Migracion 013 - Difusiones WhatsApp para eventos y anuncios.
--
-- Permite crear campanas auditables con destinatarios congelados al momento
-- de prepararlas. Cada envio queda trazado por contacto.

CREATE TABLE IF NOT EXISTS difusiones (
    id                  SERIAL PRIMARY KEY,
    nombre              VARCHAR(140) NOT NULL,
    mensaje             TEXT NOT NULL,
    media_url           TEXT,
    filtro_etiqueta     VARCHAR(20) NOT NULL DEFAULT 'todos',
    filtro_tag_id       INT REFERENCES tags(id) ON DELETE SET NULL,
    incluir_sin_chat    BOOLEAN NOT NULL DEFAULT TRUE,
    estado              VARCHAR(20) NOT NULL DEFAULT 'borrador'
                        CHECK (estado IN ('borrador','enviando','enviada','pausada','cancelada','error')),
    total_destinatarios INT NOT NULL DEFAULT 0,
    enviados            INT NOT NULL DEFAULT 0,
    fallidos            INT NOT NULL DEFAULT 0,
    omitidos            INT NOT NULL DEFAULT 0,
    delay_min_s         NUMERIC(6,2) NOT NULL DEFAULT 12,
    delay_max_s         NUMERIC(6,2) NOT NULL DEFAULT 25,
    dry_run             BOOLEAN NOT NULL DEFAULT FALSE,
    creado_por          VARCHAR(60),
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_difusiones_estado ON difusiones(estado, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_difusiones_tag ON difusiones(filtro_tag_id);

CREATE TABLE IF NOT EXISTS difusion_destinatarios (
    id                BIGSERIAL PRIMARY KEY,
    difusion_id       INT NOT NULL REFERENCES difusiones(id) ON DELETE CASCADE,
    cliente_id        INT REFERENCES clientes(id) ON DELETE SET NULL,
    numero_whatsapp   VARCHAR(32) NOT NULL,
    nombre            VARCHAR(255),
    estado            VARCHAR(20) NOT NULL DEFAULT 'pendiente'
                      CHECK (estado IN ('pendiente','enviado','fallido','omitido')),
    whapi_message_id  VARCHAR(100),
    error             TEXT,
    enviado_en        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (difusion_id, numero_whatsapp)
);

CREATE INDEX IF NOT EXISTS idx_dif_dest_difusion_estado
    ON difusion_destinatarios(difusion_id, estado);
CREATE INDEX IF NOT EXISTS idx_dif_dest_cliente
    ON difusion_destinatarios(cliente_id);
