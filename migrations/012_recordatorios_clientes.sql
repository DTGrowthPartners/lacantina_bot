-- Recordatorios automáticos para reservas y covers.
-- Idempotente por nombre para poder aplicarlo en cada despliegue.

INSERT INTO tareas_programadas
    (nombre, cron, zona_horaria, accion, parametros, activo, creado_por)
SELECT
    'Reservas: confirmación 24h',
    '0 19 * * *',
    'America/Bogota',
    'recordatorio_reservas_clientes',
    '{"offset_dias": 1, "tipo": "24h"}'::jsonb,
    true,
    'migration-012'
WHERE NOT EXISTS (
    SELECT 1 FROM tareas_programadas
    WHERE nombre = 'Reservas: confirmación 24h'
);

INSERT INTO tareas_programadas
    (nombre, cron, zona_horaria, accion, parametros, activo, creado_por)
SELECT
    'Reservas: recordatorio 3h',
    '0 19 * * *',
    'America/Bogota',
    'recordatorio_reservas_clientes',
    '{"offset_dias": 0, "tipo": "3h"}'::jsonb,
    true,
    'migration-012'
WHERE NOT EXISTS (
    SELECT 1 FROM tareas_programadas
    WHERE nombre = 'Reservas: recordatorio 3h'
);

INSERT INTO tareas_programadas
    (nombre, cron, zona_horaria, accion, parametros, activo, creado_por)
SELECT
    'Reservas: cover pendiente',
    '0 12 * * *',
    'America/Bogota',
    'recordatorio_cover_clientes',
    '{"offset_dias": 0}'::jsonb,
    true,
    'migration-012'
WHERE NOT EXISTS (
    SELECT 1 FROM tareas_programadas
    WHERE nombre = 'Reservas: cover pendiente'
);
