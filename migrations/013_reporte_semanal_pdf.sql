-- Reporte semanal en PDF para Edgardo mientras se valida el formato.
-- Ejecuta cada lunes 9:00 AM America/Bogota con la semana completa anterior.

INSERT INTO tareas_programadas
    (nombre, cron, zona_horaria, accion, parametros, activo, creado_por)
SELECT
    'Reporte semanal PDF - Edgardo',
    '0 9 * * 1',
    'America/Bogota',
    'reporte_semanal_pdf',
    '{"destino_id": "+573116123189", "periodo": "semana_anterior"}'::jsonb,
    true,
    'migration-013'
WHERE NOT EXISTS (
    SELECT 1 FROM tareas_programadas
    WHERE nombre = 'Reporte semanal PDF - Edgardo'
);
