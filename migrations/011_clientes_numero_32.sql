-- =============================================================================
-- Migración 011 — Ampliar clientes.numero_whatsapp a VARCHAR(32)
-- =============================================================================
-- Para poder representar el grupo del equipo como un "chat" en /admin/chats,
-- guardamos un Cliente cuyo numero_whatsapp es el group_id de WhatsApp
-- (ej. 120363044395439104@g.us = 23 caracteres). No cabe en VARCHAR(20).
--
-- Ampliación segura (sin pérdida de datos).
--   psql -h 127.0.0.1 -U cantina_user -d cantina_db -f migrations/011_clientes_numero_32.sql
-- =============================================================================

ALTER TABLE clientes ALTER COLUMN numero_whatsapp TYPE VARCHAR(32);
