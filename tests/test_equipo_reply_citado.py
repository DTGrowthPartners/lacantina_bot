import unittest
from unittest.mock import AsyncMock, patch

from app.claude.tools_equipo import handler_avisar_cliente
from app.flows.equipo import _cliente_objetivo_desde_alerta_citada


class EquipoReplyCitadoTests(unittest.IsolatedAsyncioTestCase):
    def test_extrae_cliente_desde_alerta_citada(self):
        alerta = (
            "🧑 cliente necesita ayuda del equipo\n"
            "Cliente: +573024166892\n"
            "Consulta: [El cliente respondió/citó este mensaje anterior tuyo:\n"
            '"A qué horas cierran?"]\n\n'
            "Su respuesta: 🤷🏻‍♂️"
        )

        objetivo = _cliente_objetivo_desde_alerta_citada(alerta)

        self.assertEqual(objetivo["telefono"], "+573024166892")
        self.assertIn("A qué horas cierran", objetivo["consulta"])
        self.assertIn("🤷", objetivo["respuesta_cliente"])

    def test_extrae_lid_desde_alerta_citada(self):
        alerta = (
            "🙋 Cliente necesita ayuda del equipo\n"
            "Cliente: 186290297381022@lid\n"
            "Consulta: ¡Hola! Quiero más información"
        )

        objetivo = _cliente_objetivo_desde_alerta_citada(alerta)

        self.assertEqual(objetivo["telefono"], "186290297381022@lid")
        self.assertIn("Quiero más información", objetivo["consulta"])

    def test_extrae_lid_desde_alerta_con_campo_numero(self):
        alerta = (
            "Cliente pregunta si hay código de vestimenta — específicamente si se "
            "puede entrar en bermudas y chanclas. Solicita respuesta del equipo. "
            "Número: 19782736863356@lid"
        )

        objetivo = _cliente_objetivo_desde_alerta_citada(alerta)

        self.assertEqual(objetivo["telefono"], "19782736863356@lid")

    async def test_avisar_cliente_fuerza_telefono_de_alerta_citada(self):
        enviar = AsyncMock()

        with patch("app.whapi.client.enviar_texto", enviar):
            result = await handler_avisar_cliente(
                {
                    "telefono": "+573203632333",
                    "mensaje": "Hola, cerramos a las 4:00 a. m.",
                },
                {
                    "miembro_nombre": "Mariano",
                    "cliente_objetivo_citado": {"telefono": "+573024166892"},
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["enviado_a"], "+573024166892")
        enviar.assert_awaited_once_with(
            "+573024166892",
            "Hola, cerramos a las 4:00 a. m.",
        )

    async def test_avisar_cliente_fuerza_lid_de_alerta_citada(self):
        enviar = AsyncMock()

        with patch("app.whapi.client.enviar_texto", enviar):
            result = await handler_avisar_cliente(
                {
                    "telefono": "+570000000000",
                    "mensaje": "Hola, te paso la info del evento.",
                },
                {
                    "miembro_nombre": "Mariano",
                    "cliente_objetivo_citado": {"telefono": "186290297381022@lid"},
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["enviado_a"], "186290297381022@lid")
        enviar.assert_awaited_once_with(
            "186290297381022@lid",
            "Hola, te paso la info del evento.",
        )


if __name__ == "__main__":
    unittest.main()
