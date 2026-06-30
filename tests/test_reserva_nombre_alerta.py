import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.claude import tools
from app.flows.conversation import _nombre_reserva_explicito


class NombreReservaTests(unittest.TestCase):
    def test_acepta_respuesta_despues_de_preguntar_nombre(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="Perfecto. ¿A nombre de quién hago la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="Laura Martínez"),
        ]

        nombre = _nombre_reserva_explicito("Laura Martínez", historial)

        self.assertEqual(nombre, "Laura Martínez")

    def test_no_toma_un_mensaje_cualquiera_como_nombre(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="¿Prefieres la mesa 17 o la 18?",
            ),
            SimpleNamespace(direccion="inbound", contenido="La 18"),
        ]

        nombre = _nombre_reserva_explicito("La 18", historial)

        self.assertIsNone(nombre)

    def test_no_toma_por_favor_como_nombre(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="¿A nombre de quién hago la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="Por favor"),
        ]

        nombre = _nombre_reserva_explicito("Por favor", historial)

        self.assertIsNone(nombre)

    def test_acepta_nombre_dado_proactivamente(self):
        nombre = _nombre_reserva_explicito(
            "La reserva va a nombre de Carlos Pérez",
            [SimpleNamespace(direccion="inbound", contenido="mensaje actual")],
        )

        self.assertEqual(nombre, "Carlos Pérez")

    def test_limpia_prefijo_mi_nombre_sin_es(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="¿A nombre de quién hago la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="Mi nombre Natalia Álvarez"),
        ]

        nombre = _nombre_reserva_explicito("Mi nombre Natalia Álvarez", historial)

        self.assertEqual(nombre, "Natalia Álvarez")

    def test_conserva_nombre_despues_de_confirmar(self):
        historial = [
            SimpleNamespace(
                direccion="inbound",
                contenido="Opción uno, unir 2 mesas, a nombre de Ernesto Portela.",
            ),
            SimpleNamespace(
                direccion="outbound",
                contenido=(
                    "Te reservo las mesas 9 y 10 a nombre de Ernesto Portela. "
                    "¿Confirmo?"
                ),
            ),
            SimpleNamespace(direccion="inbound", contenido="Correcto"),
        ]

        nombre = _nombre_reserva_explicito("Correcto", historial)

        self.assertEqual(nombre, "Ernesto Portela")

    def test_conserva_respuesta_simple_en_el_siguiente_turno(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="¿A nombre de quién hago la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="David Arriola"),
            SimpleNamespace(
                direccion="outbound",
                contenido="¿Me confirmas exactamente cómo quieres que quede la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="David Arriola está bien"),
        ]

        nombre = _nombre_reserva_explicito("David Arriola está bien", historial)

        self.assertEqual(nombre, "David Arriola")

    def test_no_reutiliza_nombre_de_reserva_ya_cerrada(self):
        historial = [
            SimpleNamespace(
                direccion="inbound",
                contenido="La reserva va a nombre de Carlos Pérez",
            ),
            SimpleNamespace(
                direccion="outbound",
                contenido="Tu reserva quedó confirmada. ¡Te esperamos!",
            ),
            SimpleNamespace(
                direccion="inbound",
                contenido="Quiero reservar otra mesa para mañana",
            ),
        ]

        nombre = _nombre_reserva_explicito(
            "Quiero reservar otra mesa para mañana",
            historial,
        )

        self.assertIsNone(nombre)


class ReservaGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_bloquea_reserva_si_solo_hay_nombre_inferido(self):
        ctx = {
            "cliente_numero": "+573001112233",
            "nombre_reserva_confirmado": None,
            "outbox": [],
        }

        with patch.object(
            tools.cantina_api,
            "crear_reserva",
            new=AsyncMock(),
        ) as crear:
            result = await tools.handler_crear_reserva(
                {
                    "fecha": "2026-06-20",
                    "mesa_id": 17,
                    "nombre_cliente": "Nombre del perfil",
                    "num_personas": 5,
                },
                ctx,
            )

        self.assertTrue(result["falta_nombre_confirmado"])
        crear.assert_not_awaited()

    async def test_alerta_grupo_incluye_detalle_completo(self):
        ctx = {
            "cliente_numero": "+573001112233",
            "nombre_reserva_confirmado": "Laura Martínez",
            "outbox": [],
        }
        respuesta = {
            "ok": True,
            "grupo_id": 44,
            "mesas": [17, 18],
            "cover_estado": "pendiente",
            "monto_cover": 250000,
            "reservas": [{
                "id": 101,
                "estado": "confirmada",
                "mesa_numero": 17,
                "mesa_zona": "VIP",
            }],
        }

        with (
            patch.object(
                tools.cantina_api,
                "listar_reservas",
                new=AsyncMock(return_value={"ok": True, "reservas": []}),
            ),
            patch.object(
                tools.cantina_api,
                "crear_reserva_grupo",
                new=AsyncMock(return_value=respuesta),
            ) as crear,
        ):
            result = await tools.handler_crear_reserva_grupo(
                {
                    "fecha": "2026-06-20",
                    "mesa_numeros": [17, 18],
                    "nombre_cliente": "Nombre inventado",
                    "num_personas": 8,
                    "notas": "Cumpleaños",
                },
                ctx,
            )

        self.assertTrue(result["ok"])
        crear.assert_awaited_once()
        self.assertEqual(
            crear.await_args.args[0]["nombre_cliente"],
            "Laura Martínez",
        )
        mensaje = ctx["outbox"][0]["mensaje"]
        self.assertIn("*Nueva reserva de grupo*", mensaje)
        self.assertIn("*A nombre de:* Laura Martínez", mensaje)
        self.assertIn("*Teléfono:* +573001112233", mensaje)
        self.assertIn("*Fecha:* 2026-06-20", mensaje)
        self.assertIn("*Personas:* 8", mensaje)
        self.assertIn("*Mesas:* 17, 18", mensaje)
        self.assertIn("*Zona:* VIP", mensaje)
        self.assertIn("*ID:* 44", mensaje)
        self.assertIn("*Estado:* confirmada", mensaje)
        self.assertIn("*Cover:* pendiente · $250.000", mensaje)
        self.assertIn("*Notas:* Cumpleaños", mensaje)

    async def test_no_repite_la_misma_creacion_en_un_turno(self):
        ctx = {
            "cliente_numero": "+573206655564",
            "nombre_reserva_confirmado": "Ernesto Portela",
            "outbox": [],
        }
        args = {
            "fecha": "2026-06-23",
            "mesa_numeros": [9, 10],
            "nombre_cliente": "Ernesto Portela",
            "num_personas": 8,
        }
        respuesta = {"ok": True, "grupo_id": 87, "mesas": [9, 10]}

        with (
            patch.object(
                tools.cantina_api,
                "listar_reservas",
                new=AsyncMock(return_value={"ok": True, "reservas": []}),
            ),
            patch.object(
                tools.cantina_api,
                "crear_reserva_grupo",
                new=AsyncMock(return_value=respuesta),
            ) as crear,
        ):
            primero = await tools.handler_crear_reserva_grupo(dict(args), ctx)
            segundo = await tools.handler_crear_reserva_grupo(dict(args), ctx)

        self.assertTrue(primero["ok"])
        self.assertTrue(segundo["ok"])
        self.assertTrue(segundo["reintento_omitido"])
        crear.assert_awaited_once()
        self.assertEqual(len(ctx["outbox"]), 1)


class CambioMesaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = {
            "cliente_numero": "+573154652226",
            "intent": "modificar_reserva",
            "outbox": [],
        }
        self.anterior = {
            "id": 158,
            "fecha": "2026-06-23",
            "mesa_id": 11,
            "mesa_numero": 11,
            "nombre_cliente": "Natalia Álvarez",
            "telefono": "+573154652226",
            "num_personas": 5,
            "estado": "confirmada",
            "tipo_reserva": "mesa",
            "grupo_id": None,
        }

    async def test_cambia_mesa_y_notifica_como_modificacion(self):
        nueva = {
            "id": 159,
            "fecha": "2026-06-23",
            "mesa_numero": 3,
            "nombre_cliente": "Natalia Álvarez",
            "num_personas": 5,
            "estado": "confirmada",
        }
        with (
            patch.object(
                tools.cantina_api,
                "reservas_cliente",
                new=AsyncMock(return_value={"ok": True, "reservas": [self.anterior]}),
            ),
            patch.object(
                tools.cantina_api,
                "crear_reserva",
                new=AsyncMock(return_value={"ok": True, "reserva": nueva}),
            ) as crear,
            patch.object(
                tools.cantina_api,
                "cancelar_reserva",
                new=AsyncMock(return_value={"ok": True}),
            ) as cancelar,
        ):
            resultado = await tools.handler_cambiar_mesa_reserva_cliente(
                {"fecha": "2026-06-23", "mesa_nueva": 3},
                self.ctx,
            )

        self.assertTrue(resultado["modificada"])
        crear.assert_awaited_once()
        cancelar.assert_awaited_once_with(158)
        self.assertEqual(len(self.ctx["outbox"]), 1)
        mensaje = self.ctx["outbox"][0]["mensaje"]
        self.assertIn("*Reserva modificada", mensaje)
        self.assertIn("*Mesa anterior:* 11", mensaje)
        self.assertIn("*Mesa nueva:* 3", mensaje)
        self.assertNotIn("Nueva reserva de mesa", mensaje)

    async def test_conserva_original_si_mesa_nueva_falla(self):
        with (
            patch.object(
                tools.cantina_api,
                "reservas_cliente",
                new=AsyncMock(return_value={"ok": True, "reservas": [self.anterior]}),
            ),
            patch.object(
                tools.cantina_api,
                "crear_reserva",
                new=AsyncMock(return_value={"ok": False, "error": "ocupada"}),
            ),
            patch.object(
                tools.cantina_api,
                "cancelar_reserva",
                new=AsyncMock(),
            ) as cancelar,
        ):
            resultado = await tools.handler_cambiar_mesa_reserva_cliente(
                {"fecha": "2026-06-23", "mesa_nueva": 3},
                self.ctx,
            )

        self.assertFalse(resultado["ok"])
        self.assertTrue(resultado["reserva_original_conservada"])
        cancelar.assert_not_awaited()

    async def test_reintento_no_crea_ni_notifica_otra_reserva(self):
        actual = dict(self.anterior, id=160, mesa_id=3, mesa_numero=3)
        with (
            patch.object(
                tools.cantina_api,
                "reservas_cliente",
                new=AsyncMock(return_value={"ok": True, "reservas": [actual]}),
            ),
            patch.object(tools.cantina_api, "crear_reserva", new=AsyncMock()) as crear,
            patch.object(tools.cantina_api, "cancelar_reserva", new=AsyncMock()) as cancelar,
        ):
            resultado = await tools.handler_cambiar_mesa_reserva_cliente(
                {"fecha": "2026-06-23", "mesa_nueva": 3},
                self.ctx,
            )

        self.assertTrue(resultado["ya_estaba_en_mesa"])
        crear.assert_not_awaited()
        cancelar.assert_not_awaited()
        self.assertEqual(self.ctx["outbox"], [])

    async def test_cancelacion_se_bloquea_durante_cambio(self):
        with patch.object(
            tools.cantina_api,
            "reservas_cliente",
            new=AsyncMock(),
        ) as consultar:
            resultado = await tools.handler_cancelar_reserva_cliente(
                {"fecha": "2026-06-23", "reserva_id": 158},
                self.ctx,
            )

        self.assertTrue(resultado["usar_cambio_mesa"])
        consultar.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
