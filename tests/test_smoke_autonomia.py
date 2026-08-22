import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.claude import tools
from app.claude.tools_equipo import handler_avisar_cliente
from app.flows.conversation import (
    _asegurar_escalacion_humana,
    _nombre_reserva_explicito,
)
from app.flows.equipo import _cliente_objetivo_desde_alerta_citada


class SmokeAutonomiaTests(unittest.IsolatedAsyncioTestCase):
    def test_no_escala_mensaje_de_cierre_simple(self):
        outbox = []

        agregado = _asegurar_escalacion_humana(
            outbox,
            intent="pide_humano",
            cliente_numero="+573126509610",
            mensaje_cliente="Si ya vamos para alla\nGracias",
        )

        self.assertFalse(agregado)
        self.assertEqual(outbox, [])

    def test_no_escala_lead_generico_de_informacion(self):
        outbox = []

        agregado = _asegurar_escalacion_humana(
            outbox,
            intent="pide_humano",
            cliente_numero="186290297381022@lid",
            mensaje_cliente="¡Hola! Quiero más información",
        )

        self.assertFalse(agregado)
        self.assertEqual(outbox, [])

    async def test_tool_escalar_omite_lead_generico_de_informacion(self):
        ctx = {
            "cliente_id": 123,
            "cliente_numero": "186290297381022@lid",
            "mensaje_actual_cliente": "¡Hola! Quiero más información",
            "outbox": [],
        }

        result = await tools.handler_escalar_a_equipo(
            {
                "tipo": "pide_humano",
                "mensaje": "Cliente necesita ayuda: ¡Hola! Quiero más información",
            },
            ctx,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["escalado"])
        self.assertEqual(ctx["outbox"], [])

    async def test_tool_escalar_omite_pedido_generico_de_asesor(self):
        ctx = {
            "cliente_id": 124,
            "cliente_numero": "+573144226354",
            "mensaje_actual_cliente": "Hola buenos días\nPara hablar con un asesor",
            "outbox": [],
        }

        result = await tools.handler_escalar_a_equipo(
            {
                "tipo": "pide_humano",
                "mensaje": "Cliente pide hablar con asesor",
            },
            ctx,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["escalado"])
        self.assertEqual(ctx["outbox"], [])

    def test_recupera_nombre_aunque_haya_flyer_entre_pregunta_y_respuesta(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="Lo hacemos entonces? A nombre de quien aparto la mesa?",
            ),
            SimpleNamespace(direccion="outbound", contenido="[flyer del evento]"),
        ]

        self.assertEqual(
            _nombre_reserva_explicito("Astrid Rojas", historial),
            "Astrid Rojas",
        )

    def test_si_confirma_nombre_previo_no_pide_equipo(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="Lo hacemos entonces? A nombre de quien aparto la mesa?",
            ),
            SimpleNamespace(direccion="outbound", contenido="[flyer del evento]"),
            SimpleNamespace(direccion="inbound", contenido="Astrid Rojas"),
            SimpleNamespace(
                direccion="outbound",
                contenido="Solo para confirmar antes de apartar - es Astrid Rojas?",
            ),
        ]

        self.assertEqual(_nombre_reserva_explicito("Si", historial), "Astrid Rojas")

    async def test_reserva_con_nombre_confirmado_se_crea_sin_escalacion(self):
        ctx = {
            "cliente_numero": "+573203632333",
            "nombre_reserva_confirmado": "Astrid Rojas",
            "outbox": [],
        }
        respuesta_api = {
            "ok": True,
            "reserva": {
                "id": 280,
                "estado": "confirmada",
                "mesa_numero": 17,
                "mesa_zona": "VIP",
                "cover_estado": "no_aplica",
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
                new=AsyncMock(return_value=respuesta_api),
            ) as crear,
        ):
            result = await tools.handler_crear_reserva(
                {
                    "fecha": "2026-07-25",
                    "mesa_id": 17,
                    "nombre_cliente": "Astrid Rojas",
                    "telefono": "+573203632333",
                    "num_personas": 4,
                    "notas": "Evento de cumpleanos. Pide decoracion con globos.",
                },
                ctx,
            )

        self.assertTrue(result["ok"])
        crear.assert_awaited_once()
        self.assertEqual(ctx["outbox"][0]["tipo"], "reserva_nueva")
        self.assertNotIn("escalacion", {item.get("clase") for item in ctx["outbox"]})
        self.assertIn("Astrid Rojas", ctx["outbox"][0]["mensaje"])

    async def test_nombre_dentro_de_frase_reciente_no_termina_en_equipo(self):
        ctx = {
            "cliente_numero": "+573156592245",
            "nombre_reserva_confirmado": None,
            "mensaje_actual_cliente": "Correcto",
            "historial_cliente_reciente": [
                {
                    "direccion": "inbound",
                    "contenido": "Perfecto!! Diana Paez por favor",
                },
            ],
            "outbox": [],
        }
        respuesta_api = {
            "ok": True,
            "reserva": {
                "id": 281,
                "estado": "confirmada",
                "mesa_numero": 8,
                "mesa_zona": "Cantina",
                "cover_estado": "no_aplica",
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
                new=AsyncMock(return_value=respuesta_api),
            ) as crear,
        ):
            result = await tools.handler_crear_reserva(
                {
                    "fecha": "2026-07-26",
                    "mesa_id": 8,
                    "nombre_cliente": "Diana Paez",
                    "telefono": "+573156592245",
                    "num_personas": 2,
                },
                ctx,
            )

        self.assertTrue(result["ok"])
        crear.assert_awaited_once()
        self.assertEqual(ctx["nombre_reserva_confirmado"], "Diana Paez")
        self.assertNotIn("escalacion", {item.get("clase") for item in ctx["outbox"]})
        self.assertIn("Diana Paez", ctx["outbox"][0]["mensaje"])

    def test_extrae_cliente_de_alerta_citada_del_equipo(self):
        alerta = (
            "Cliente necesita ayuda del equipo\n"
            "Cliente: +573024166892\n"
            "Consulta: [El cliente respondio/cito este mensaje anterior tuyo:\n"
            '"A que horas cierran?"]\n\n'
            "Su respuesta: no se"
        )

        objetivo = _cliente_objetivo_desde_alerta_citada(alerta)

        self.assertEqual(objetivo["telefono"], "+573024166892")
        self.assertIn("A que horas cierran", objetivo["consulta"])

    async def test_reply_citado_fuerza_telefono_correcto(self):
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


if __name__ == "__main__":
    unittest.main()
