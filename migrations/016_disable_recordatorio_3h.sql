-- Desactiva el recordatorio de reservas del mismo día / 3h.
-- Se conserva el recordatorio de confirmación 24h y el de cover pendiente.

UPDATE tareas_programadas
SET activo = false,
    proxima_ejecucion = NULL,
    updated_at = now()
WHERE accion = 'recordatorio_reservas_clientes'
  AND (
      nombre = 'Reservas: recordatorio 3h'
      OR parametros->>'tipo' = '3h'
  );
