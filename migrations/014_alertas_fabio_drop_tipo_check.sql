-- 014_alertas_fabio_drop_tipo_check.sql
--
-- La tabla alertas_fabio venía con un CHECK heredado del bot anterior que solo
-- admitía tipos de su embudo (comprobante_pago, ref_desconocida, duda_mayorista,
-- pedido_confirmado, etc.). La cantina usa otros tipos (claude_api_fail,
-- pide_humano, ...), así que cualquier INSERT con un tipo nuevo fallaba el CHECK
-- → la transacción quedaba en PendingRollbackError y la alerta se perdía
-- (justo cuando más se necesitaba: al fallar Claude).
--
-- El modelo (app/db/models.py) define `tipo` como String(50) SIN enum: la app es
-- la fuente de verdad de los tipos. Eliminamos el CHECK para que las alertas
-- siempre se registren. Idempotente.

ALTER TABLE alertas_fabio DROP CONSTRAINT IF EXISTS alertas_fabio_tipo_check;
