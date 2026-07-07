import unittest
from unittest.mock import AsyncMock, patch

from app.claude import tools


class CasaLlenaClienteTests(unittest.IsolatedAsyncioTestCase):
    async def test_disponibilidad_casa_llena_devuelve_respuesta_cliente(self):
        with patch.object(
            tools.cantina_api,
            "disponibilidad",
            new=AsyncMock(return_value={
                "ok": True,
                "casa_llena": True,
                "motivo": "Casa llena",
                "mesas_disponibles": [{"numero": 1}],
            }),
        ):
            result = await tools.handler_consultar_disponibilidad(
                {"fecha": "2026-07-07", "personas": 4},
                {"cliente_numero": "+573001112233"},
            )

        self.assertFalse(result["hay_disponibilidad"])
        self.assertTrue(result["casa_llena"])
        self.assertEqual(result["mesas_disponibles"], [])
        self.assertIn("casa llena", result["respuesta_cliente"])
        self.assertNotIn("backend", result["respuesta_cliente"].casefold())
        self.assertNotIn("reabr", result["respuesta_cliente"].casefold())
        self.assertIn("No menciones backend", result["instruccion"])

    async def test_crear_reserva_bloqueada_por_casa_llena_no_notifica_reserva(self):
        ctx = {
            "cliente_numero": "+573001112233",
            "nombre_reserva_confirmado": "Edgardo Meza",
            "outbox": [],
        }

        with (
            patch.object(
                tools.cantina_api,
                "listar_reservas",
                new=AsyncMock(return_value={"ok": True, "reservas": []}),
            ),
            patch.object(
                tools.cantina_api,
                "crear_reserva",
                new=AsyncMock(return_value={
                    "ok": False,
                    "status": 409,
                    "error": "bloqueada por cierre de casa llena",
                }),
            ),
        ):
            result = await tools.handler_crear_reserva(
                {
                    "fecha": "2026-07-07",
                    "mesa_id": 1,
                    "nombre_cliente": "Edgardo Meza",
                    "num_personas": 4,
                },
                ctx,
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["bloqueo_casa_llena"])
        self.assertIn("casa llena", result["respuesta_cliente"])
        self.assertIn("no podemos recibir más reservas", result["respuesta_cliente"])
        self.assertEqual(ctx["outbox"], [])


if __name__ == "__main__":
    unittest.main()
