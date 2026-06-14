import unittest
from unittest.mock import AsyncMock, patch

from app.claude import intent, tools


class ConsultaReservaIntentTests(unittest.IsolatedAsyncioTestCase):
    async def test_detecta_confirmacion_dias_despues_sin_llamar_modelo(self):
        with patch.object(
            intent._client.messages,
            "create",
            new=AsyncMock(),
        ) as crear:
            resultado = await intent.clasificar(
                "Hola, confírmame por favor mi reserva",
            )

        self.assertEqual(resultado, "consultar_reserva")
        crear.assert_not_awaited()


class ConsultaReservaToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_busca_por_telefono_sin_id(self):
        reservas = [{
            "id": 81,
            "fecha": "2026-06-20",
            "nombre_cliente": "Laura Martínez",
            "telefono": "+573001112233",
            "num_personas": 6,
            "mesa_numero": 17,
            "estado": "confirmada",
        }]
        ctx = {"cliente_numero": "+573001112233"}

        with patch.object(
            tools.cantina_api,
            "reservas_cliente",
            new=AsyncMock(return_value={"ok": True, "reservas": reservas}),
        ) as buscar:
            resultado = await tools.handler_consultar_reserva_cliente({}, ctx)

        buscar.assert_awaited_once_with("+573001112233")
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["total"], 1)
        self.assertEqual(resultado["reservas"][0]["id"], 81)
        self.assertIn("NUNCA pidas ID", resultado["instruccion"])

    async def test_reutiliza_reservas_precargadas(self):
        ctx = {
            "cliente_numero": "+573001112233",
            "reservas_cliente_precargadas": [],
        }
        with patch.object(
            tools.cantina_api,
            "reservas_cliente",
            new=AsyncMock(),
        ) as buscar:
            resultado = await tools.handler_consultar_reserva_cliente({}, ctx)

        buscar.assert_not_awaited()
        self.assertEqual(resultado["total"], 0)


if __name__ == "__main__":
    unittest.main()
