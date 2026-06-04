-- =============================================================================
-- Migración 010 — Tablas core que faltaban tras la limpieza DTGP→La Cantina
-- =============================================================================
-- schema.sql + migraciones 002/008/009 NO crean estas tablas (venían de las
-- migraciones DTGP 003–007 que se eliminaron). Son necesarias en runtime:
--   - contactos_whitelist : la consulta el directorio del equipo en CADA mensaje
--     (si falta, falla la carga de equipo_miembros y se rompe el ruteo del staff).
--   - bot_estado          : kill-switch global (modo todos/solo_prospectos/off).
--   - memorias            : memoria evolutiva (la usan los flows en cada turno).
--   - recordatorios       : pendientes con fecha (heartbeat).
--   - tareas_programadas  : crons editables desde el admin (scheduler).
--
-- Ejecutar:
--   psql -h 127.0.0.1 -U cantina_user -d cantina_db -f migrations/010_cantina_core.sql
-- Idempotente (IF NOT EXISTS).
-- =============================================================================

-- Whitelist maestra (rol equipo | cliente). El staff "equipo" va a equipo_miembros;
-- esta tabla soporta clientes con permisos scoped y la consulta el directorio.
CREATE TABLE IF NOT EXISTS contactos_whitelist (
    id               SERIAL PRIMARY KEY,
    numero_whatsapp  VARCHAR(20) UNIQUE NOT NULL,
    rol              VARCHAR(20) NOT NULL,          -- equipo | cliente
    nombre           VARCHAR(150),
    empresa          VARCHAR(150),
    email            VARCHAR(120),
    nit              VARCHAR(30),
    dtos_client_id   VARCHAR(60),
    meta_account_id  VARCHAR(60),
    permisos         JSONB NOT NULL DEFAULT '{}'::jsonb,
    activo           BOOLEAN NOT NULL DEFAULT TRUE,
    notas            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_contactos_wl_numero ON contactos_whitelist(numero_whatsapp);
CREATE INDEX IF NOT EXISTS idx_contactos_wl_rol ON contactos_whitelist(rol) WHERE activo;

-- Estado global del bot (1 sola fila, id=1).
CREATE TABLE IF NOT EXISTS bot_estado (
    id              INTEGER PRIMARY KEY,
    activo          BOOLEAN NOT NULL DEFAULT TRUE,
    modo            VARCHAR(20) DEFAULT 'todos',    -- todos | solo_prospectos | off
    pausado_por     VARCHAR(60),
    pausado_en      TIMESTAMPTZ,
    razon           TEXT,
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO bot_estado (id, activo, modo) VALUES (1, TRUE, 'todos')
    ON CONFLICT (id) DO NOTHING;

-- Memoria evolutiva (reglas/hechos/preferencias que el bot recuerda).
CREATE TABLE IF NOT EXISTS memorias (
    id           SERIAL PRIMARY KEY,
    scope        VARCHAR(20) NOT NULL,              -- general | contacto | equipo
    contacto_id  INTEGER REFERENCES clientes(id) ON DELETE CASCADE,
    titulo       VARCHAR(180) NOT NULL,
    contenido    TEXT NOT NULL,
    tipo         VARCHAR(30) NOT NULL DEFAULT 'regla',
    activa       BOOLEAN NOT NULL DEFAULT TRUE,
    creado_por   VARCHAR(60),
    tags         JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_memorias_scope ON memorias(scope) WHERE activa;
CREATE INDEX IF NOT EXISTS idx_memorias_contacto ON memorias(contacto_id) WHERE activa;

-- Recordatorios (pendientes con fecha).
CREATE TABLE IF NOT EXISTS recordatorios (
    id              SERIAL PRIMARY KEY,
    contacto_id     INTEGER REFERENCES clientes(id) ON DELETE CASCADE,
    accion          TEXT NOT NULL,
    motivo          TEXT,
    vence_en        TIMESTAMPTZ NOT NULL,
    estado          VARCHAR(20) NOT NULL DEFAULT 'pendiente',  -- pendiente | atendido | descartado
    origen          VARCHAR(30) NOT NULL DEFAULT 'manual',
    creado_por      VARCHAR(60),
    atendido_en     TIMESTAMPTZ,
    atendido_notas  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_recordatorios_pend ON recordatorios(vence_en) WHERE estado = 'pendiente';

-- Tareas programadas (crons editables desde el admin; las corre el scheduler).
CREATE TABLE IF NOT EXISTS tareas_programadas (
    id                 SERIAL PRIMARY KEY,
    nombre             VARCHAR(120) NOT NULL,
    cron               VARCHAR(60) NOT NULL,
    zona_horaria       VARCHAR(60) NOT NULL DEFAULT 'America/Bogota',
    accion             VARCHAR(60) NOT NULL,
    parametros         JSONB NOT NULL DEFAULT '{}'::jsonb,
    activo             BOOLEAN NOT NULL DEFAULT TRUE,
    ultima_ejecucion   TIMESTAMPTZ,
    proxima_ejecucion  TIMESTAMPTZ,
    ultimo_resultado   JSONB,
    creado_por         VARCHAR(60),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tareas_activas ON tareas_programadas(proxima_ejecucion) WHERE activo;
