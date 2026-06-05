"""Seed inicial del staff de La Cantina Plus.

Carga los números del equipo en la BD del bot para que NO sean atendidos como
clientes:

  - tipo "equipo"  → tabla `equipo_miembros`. El bot SÍ les responde en el grupo
    del equipo (operación: resúmenes, reservas, covers). Hoy: Fabio y Edgardo.
  - tipo "interno" → tabla `numeros_internos`. El bot los IGNORA en silencio
    (meseros, cantantes, guardia, etc.).

El número del propio bot (+573008641322) NO se incluye (se maneja por la lógica
de self-chat / operador en el webhook).

Uso (desde la raíz del repo, con el venv activo y .env configurado):

    python -m scripts.seed_staff

Idempotente: ON CONFLICT DO NOTHING / UPDATE — se puede correr varias veces.
Edita la lista STAFF abajo si cambian los números o roles.
"""

import asyncio

from sqlalchemy import text

from app.db.session import async_session_factory

# (numero_whatsapp, nombre, rol, tipo)  — tipo: "equipo" | "interno"
# Los "equipo" reciben atención operativa del bot en el grupo del equipo.
# es_fallback (recibe escalaciones) se asigna a los roles Dueño/Administrador.
STAFF: list[tuple[str, str, str, str]] = [
    ("+573135861776", "Señor Fabio", "Dueño", "equipo"),
    ("+573226616509", "Mariano Mesa La Cantina", "Administrador", "equipo"),
    ("+573026444564", "Stiven (admin provisional)", "Administrador", "equipo"),
    ("+573116123189", "Edgardo", "Desarrollador / Equipo", "equipo"),
    ("+573122708650", "Santiago Giraldo Cantina", "Cantante", "interno"),
    ("+573117681858", "Alejo Palacio", "Cantante/Cajero", "interno"),
    ("+573011062354", "Diana Cantina", "Mesera", "interno"),
    ("+573017866478", "Jordi", "Guardia", "interno"),
    ("+573215098448", "Mariana Cantina", "Mesera", "interno"),
    ("+573013364137", "Dana", "Mesera", "interno"),
    ("+573244824083", "Brilly Giraldo", "Cantante", "interno"),
    ("+573116378746", "Nicol Giraldo", "Cantante", "interno"),
]


async def main() -> None:
    equipo = internos = 0
    async with async_session_factory() as s:
        for numero, nombre, rol, tipo in STAFF:
            if tipo == "equipo":
                await s.execute(
                    text(
                        """
                        INSERT INTO equipo_miembros (nombre, numero_whatsapp, rol, activo, es_fallback)
                        VALUES (:n, :w, :r, true, :fb)
                        ON CONFLICT (numero_whatsapp) DO UPDATE
                          SET nombre = EXCLUDED.nombre,
                              rol = EXCLUDED.rol,
                              activo = true
                        """
                    ),
                    {"n": nombre, "w": numero, "r": rol,
                     "fb": rol in ("Dueño", "Administrador")},
                )
                equipo += 1
            else:
                await s.execute(
                    text(
                        """
                        INSERT INTO numeros_internos (numero_whatsapp, nombre, razon, activo)
                        VALUES (:w, :nom, :r, true)
                        ON CONFLICT (numero_whatsapp) DO UPDATE
                          SET nombre = EXCLUDED.nombre,
                              razon = EXCLUDED.razon,
                              activo = true
                        """
                    ),
                    {"w": numero, "nom": nombre, "r": rol},
                )
                internos += 1
        await s.commit()
    print(f"seed staff ok — {equipo} en equipo_miembros, {internos} en numeros_internos")


if __name__ == "__main__":
    asyncio.run(main())
