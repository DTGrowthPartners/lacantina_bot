import unittest
from unittest.mock import AsyncMock, patch

from app.claude import tools
from app.whapi.parser import normalizar_numero, parsear_mensaje


class WhapiLidTests(unittest.IsolatedAsyncioTestCase):
    def test_normalizar_numero_preserva_lid(self):
        self.assertEqual(
            normalizar_numero("186290297381022@lid"),
            "186290297381022@lid",
        )

    def test_parsear_mensaje_lid_con_phone_hint(self):
        msg = parsear_mensaje({
            "id": "lid-1",
            "from": "186290297381022@lid",
            "chat_id": "186290297381022@lid",
            "from_me": False,
            "type": "text",
            "text": {"body": "Hola"},
            "timestamp": 1770000000,
            "contact": {"phone": "573001112233"},
            "from_name": "Cecilia",
        })

        self.assertIsNotNone(msg)
        self.assertEqual(msg.from_number, "186290297381022@lid")
        self.assertEqual(msg.lid, "186290297381022@lid")
        self.assertEqual(msg.phone_hint, "+573001112233")
        self.assertEqual(msg.from_name, "Cecilia")

    async def test_reserva_en_chat_lid_usa_phone_hint_para_backend(self):
        ctx = {
            "cliente_numero": "186290297381022@lid",
            "cliente_phone_hint": "+573001112233",
            "nombre_reserva_confirmado": "Cecilia Moreno",
            "outbox": [],
        }
        respuesta = {
            "ok": True,
            "reserva": {
                "id": 300,
                "estado": "confirmada",
                "mesa_numero": 8,
            },
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
                new=AsyncMock(return_value=respuesta),
            ) as crear,
        ):
            result = await tools.handler_crear_reserva(
                {
                    "fecha": "2026-08-22",
                    "mesa_id": 8,
                    "nombre_cliente": "Cecilia Moreno",
                    "num_personas": 2,
                },
                ctx,
            )

        self.assertTrue(result["ok"])
        payload = crear.await_args.args[0]
        self.assertEqual(payload["telefono"], "+573001112233")


if __name__ == "__main__":
    unittest.main()
