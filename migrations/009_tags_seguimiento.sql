-- ============================================================================
-- Migración 009 — Tags libres por cliente (multi-etiqueta)
-- ============================================================================
-- La columna `clientes.etiqueta` (cliente/equipo/personal) controla el routing
-- del bot. Esta migración suma una capa de "tags" libres opcionales que el
-- equipo puede crear desde el admin (ej. "Cliente recurrente", "VIP"). Para un
-- bar/disco NO hay funnel de ventas, así que arranca SIN tags precargados.
--
-- Modelo: many-to-many entre clientes y tags.
-- ============================================================================

CREATE TABLE IF NOT EXISTS tags (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(50) NOT NULL UNIQUE,
    color VARCHAR(7) NOT NULL DEFAULT '#7C3AED',  -- hex como #RRGGBB
    descripcion TEXT,
    orden INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by VARCHAR(60)
);

CREATE INDEX IF NOT EXISTS idx_tags_orden ON tags(orden, nombre);

CREATE TABLE IF NOT EXISTS cliente_tags (
    cliente_id INT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    tag_id INT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by VARCHAR(60),
    PRIMARY KEY (cliente_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_cliente_tags_tag ON cliente_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_cliente_tags_cliente ON cliente_tags(cliente_id);

-- Sin seed: el bar no usa funnel de ventas. Si el equipo quiere tags
-- (ej. "Cliente recurrente", "VIP", "No-show"), los crea desde /admin/etiquetas.

DO $$
DECLARE n INT;
BEGIN
    SELECT COUNT(*) INTO n FROM tags;
    RAISE NOTICE 'Migración 009 lista. Tags totales: %', n;
END $$;
