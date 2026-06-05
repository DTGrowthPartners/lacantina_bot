# Cantina Bot

Bot WhatsApp de reservas para **La Cantina Plus** (Cartagena, cliente: Sr. Fabio
Giraldo). Atiende público que escribe pidiendo reserva, info, covers, y al
**staff interno** vía un grupo de WhatsApp.

> **Estado**: ESQUELETO replicado desde `bot_asistente/` (proyecto María/DTGP).
> Falta adaptar varios módulos. Sigue [`docs/ROADMAP.md`](docs/ROADMAP.md) en orden.

---

## Lectura obligatoria antes de tocar código

| Doc | Para qué |
|-----|----------|
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | **Empezar por aquí**. Lista paso a paso lo que falta hacer. |
| [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) | Cómo está organizado el proyecto, qué hace cada módulo. |
| [`docs/REGLAS_NEGOCIO.md`](docs/REGLAS_NEGOCIO.md) | Info del venue: mesas, zonas, salas, covers, VIP. Fuente de verdad operativa. |
| [`docs/API_BACKEND.md`](docs/API_BACKEND.md) | Cómo consumir el backend de mesas (`cantina-api.dtgrowthpartners.com`). |
| [`docs/DIFERENCIAS_VS_MARIA.md`](docs/DIFERENCIAS_VS_MARIA.md) | Qué se quitó/cambió/mantuvo respecto a `bot_asistente/`. |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Cómo levantar el bot (local + VPS). |


---

## Stack

- **Python 3.12** · FastAPI · uvicorn
- **Postgres** (DB del bot: chats, miembros, alertas, pausas)
- **SQLAlchemy 2.0 async + asyncpg**
- **Anthropic Claude** (Sonnet 4.6 principal, Haiku para clasificador)
- **whapi.cloud** (canal WhatsApp del venue, +573008641322)
- **Backend de mesas externo**: Node + SQLite, expuesto en
  `https://cantina-api.dtgrowthpartners.com` — fuente de verdad de reservas.

---

## Arquitectura en una página

```
┌──────────────────────────────────────────────────────────────────────┐
│                          WhatsApp (whapi)                            │
│             Número público: +573008641322 (La Cantina)               │
└───────────────────────────────────┬──────────────────────────────────┘
                                    │ webhook POST /webhook
┌───────────────────────────────────▼──────────────────────────────────┐
│                            cantina_bot (este proyecto)               │
│                                                                       │
│  app/main.py — router del webhook                                     │
│    │                                                                  │
│    ├─ Cliente público     → app/flows/conversation.py                 │
│    │                         └─ Claude + tools (app/claude/tools.py)  │
│    │                                                                  │
│    ├─ Grupo equipo / self → app/flows/equipo.py                       │
│    │                         └─ Claude + tools (tools_equipo.py)      │
│    │                                                                  │
│    └─ Persistencia chats  → Postgres (clientes, conversaciones,       │
│                              alertas, miembros, tags)                 │
│                                                                       │
│  app/integrations/cantina_api.py  ← cliente HTTP del backend mesas    │
└───────────────────────────────────┬──────────────────────────────────┘
                                    │ HTTP x-api-key
┌───────────────────────────────────▼──────────────────────────────────┐
│              cantina-mesas-backend (otro proyecto)                   │
│              Node + Express + SQLite — fuente de verdad de:          │
│              mesas, eventos, reservas, salas privadas, covers.       │
│              URL: https://cantina-api.dtgrowthpartners.com           │
│              Repo: /home/ubuntu/cantina-mesas-backend (VPS)          │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Reglas no negociables

1. **El backend de mesas es la fuente de verdad.** Capacidad por mesa, regla
   VIP en evento, índice único de reserva, mínimo de consumo en salas: todo
   eso lo fuerza el backend. **Este bot SOLO comunica.** Si quieres cambiar
   una regla, se cambia en el backend.

2. **API key de Cantina nunca se expone al cliente.** Va sólo en el header
   `x-api-key`. Si se filtra en un mensaje WhatsApp es un incidente.

3. **Números del staff (Fabio, Edgardo, Mariana, etc.) son privados.**
   El bot los ignora como si fueran clientes (se silencian via tabla
   `numeros_internos` o `equipo_miembros`). Si uno escribe, no se le contesta
   como cliente. Lista completa en [`docs/REGLAS_NEGOCIO.md`](docs/REGLAS_NEGOCIO.md).

4. **El bot opera tarde/noche** (la Cantina abre de 5–6 pm a 3–5 am). Pero
   responde 24/7 — la pauta puede llegar a cualquier hora.

5. **No procesa pagos.** Solo envía link, registra comprobante, escala al
   equipo para verificar.

---

## Cómo arrancar local (resumen)

```bash
cd cantina_bot
python -m venv venv && source venv/bin/activate    # Linux/Mac
# venv\Scripts\activate                            # Windows

pip install -e .
cp .env.example .env                               # editar valores
# Levantar Postgres y crear DB cantina_db
psql cantina_db -f schema.sql
psql cantina_db -f migrations/002_equipo_a_db.sql
psql cantina_db -f migrations/008_etiquetas_contacto.sql
psql cantina_db -f migrations/009_tags_seguimiento.sql

uvicorn app.main:app --host 127.0.0.1 --port 8012 --reload
```

Detalles completos en [`docs/DEPLOY.md`](docs/DEPLOY.md).

---

## Contacto

- Cliente final: Fabio Giraldo (dueño de La Cantina Plus).
- Stakeholder operativo del proyecto: Edgardo.
- Plataforma: DT Growth Partners — Stiven Antequera.
