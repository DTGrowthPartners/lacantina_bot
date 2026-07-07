import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

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

    def test_no_toma_ubicacion_como_nombre(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="¿A nombre de quién hago la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="Es en toda la esquina"),
        ]

        nombre = _nombre_reserva_explicito("Es en toda la esquina", historial)

        self.assertIsNone(nombre)

    def test_no_toma_pregunta_de_puesto_como_nombre(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="¿A nombre de quién hago la reserva?",
            ),
            SimpleNamespace(
                direccion="inbound",
                contenido="Y me dice en qué puesto quedaríamos",
            ),
        ]

        nombre = _nombre_reserva_explicito(
            "Y me dice en qué puesto quedaríamos",
            historial,
        )

        self.assertIsNone(nombre)

    def test_no_toma_pedido_ubicacion_como_nombre(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="¿A nombre de quién hago la reserva?",
            ),
            SimpleNamespace(
                direccion="inbound",
                contenido="Puedes mandarme la ubicación",
            ),
        ]

        nombre = _nombre_reserva_explicito(
            "Puedes mandarme la ubicación",
            historial,
        )

        self.assertIsNone(nombre)

    def test_no_toma_me_confirmas_como_nombre(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="¿A nombre de quién hago la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="Me confirmas"),
        ]

        nombre = _nombre_reserva_explicito("Me confirmas", historial)

        self.assertIsNone(nombre)

    def test_limpia_por_favor_antes_del_nombre(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="¿A nombre de quién hago la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="Por favor Juan Otalvaro"),
        ]

        nombre = _nombre_reserva_explicito("Por favor Juan Otalvaro", historial)

        self.assertEqual(nombre, "Juan Otalvaro")

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

    def test_recupera_nombre_previo_si_ultimo_mensaje_no_es_nombre(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="¿A nombre de quién hago la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="DIEGO CARRILLO RAMOS"),
            SimpleNamespace(
                direccion="outbound",
                contenido="¿Me confirmas exactamente cómo quieres que quede la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="Puedes mandarme la ubicación"),
        ]

        nombre = _nombre_reserva_explicito("Puedes mandarme la ubicación", historial)

        self.assertEqual(nombre, "DIEGO CARRILLO RAMOS")

    def test_recupera_nombre_previo_si_ultimo_mensaje_es_confirmacion(self):
        historial = [
            SimpleNamespace(
                direccion="outbound",
                contenido="¿A nombre de quién hago la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="Melissa Urueta"),
            SimpleNamespace(
                direccion="outbound",
                contenido="¿Me confirmas exactamente cómo quieres que quede la reserva?",
            ),
            SimpleNamespace(direccion="inbound", contenido="Me confirmas"),
        ]

        nombre = _nombre_reserva_explicito("Me confirmas", historial)

        self.assertEqual(nombre, "Melissa Urueta")


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

    async def test_bloquea_reserva_si_nombre_confirmado_es_frase(self):
        ctx = {
            "cliente_numero": "+573015117671",
            "nombre_reserva_confirmado": "Y me dice en qué puesto quedaríamos",
            "outbox": [],
        }

        with patch.object(
            tools.cantina_api,
            "crear_reserva",
            new=AsyncMock(),
        ) as crear:
            result = await tools.handler_crear_reserva(
                {
                    "fecha": "2026-07-03",
                    "mesa_id": 2,
                    "nombre_cliente": "Y me dice en qué puesto quedaríamos",
                    "num_personas": 3,
                },
                ctx,
            )

        self.assertTrue(result["falta_nombre_confirmado"])
        crear.assert_not_awaited()

    async def test_bloquea_reserva_si_nombre_confirmado_pide_ubicacion(self):
        ctx = {
            "cliente_numero": "+573002235156",
            "nombre_reserva_confirmado": "Puedes mandarme la ubicación",
            "outbox": [],
        }

        with patch.object(
            tools.cantina_api,
            "crear_reserva",
            new=AsyncMock(),
        ) as crear:
            result = await tools.handler_crear_reserva(
                {
                    "fecha": "2026-07-03",
                    "mesa_id": 3,
                    "nombre_cliente": "DIEGO CARRILLO RAMOS",
                    "num_personas": 3,
                },
                ctx,
            )

        self.assertTrue(result["falta_nombre_confirmado"])
        crear.assert_not_awaited()

    async def test_bloquea_reserva_si_nombre_confirmado_es_me_confirmas(self):
        ctx = {
            "cliente_numero": "+573127549273",
            "nombre_reserva_confirmado": "Me confirmas",
            "outbox": [],
        }

        with patch.object(
            tools.cantina_api,
            "crear_reserva",
            new=AsyncMock(),
        ) as crear:
            result = await tools.handler_crear_reserva(
                {
                    "fecha": "2026-07-03",
                    "mesa_id": 28,
                    "nombre_cliente": "Melissa Urueta",
                    "num_personas": 4,
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
            patch.object(
                tools.cantina_api,
                "actualizar_reserva",
                new=AsyncMock(),
            ) as actualizar,
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
        actualizar.assert_not_awaited()
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

    async def test_corrige_nombre_basura_antes_de_alertar_al_grupo(self):
        ctx = {
            "cliente_numero": "+573108291174",
            "nombre_reserva_confirmado": "Juan Otalvaro",
            "outbox": [],
        }
        respuesta = {
            "ok": True,
            "reserva": {
                "id": 199,
                "estado": "confirmada",
                "nombre_cliente": "Por favor",
                "mesa_numero": 20,
                "mesa_zona": "VIP",
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
            ),
            patch.object(
                tools.cantina_api,
                "actualizar_reserva",
                new=AsyncMock(return_value={"ok": True}),
            ) as actualizar,
        ):
            result = await tools.handler_crear_reserva(
                {
                    "fecha": "2026-07-03",
                    "mesa_id": 20,
                    "nombre_cliente": "Por favor",
                    "num_personas": 6,
                },
                ctx,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["nombre_autocorregido"])
        actualizar.assert_awaited_once_with(199, {"nombre_cliente": "Juan Otalvaro"})
        mensaje = ctx["outbox"][0]["mensaje"]
        self.assertIn("*A nombre de:* Juan Otalvaro", mensaje)
        self.assertNotIn("*A nombre de:* Por favor", mensaje)

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

    async def test_permite_segunda_mesa_misma_fecha_mismo_telefono(self):
        ctx = {
            "cliente_numero": "+573135341582",
            "nombre_reserva_confirmado": "Stefany Pérez",
            "outbox": [],
        }
        existentes = [{
            "id": 201,
            "fecha": "2026-07-03",
            "mesa_numero": 18,
            "telefono": "+573135341582",
            "estado": "confirmada",
        }]
        respuesta = {
            "ok": True,
            "reserva": {
                "id": 202,
                "estado": "confirmada",
                "mesa_numero": 24,
                "mesa_zona": "VIP",
            },
        }

        with (
            patch.object(
                tools.cantina_api,
                "listar_reservas",
                new=AsyncMock(return_value={"ok": True, "reservas": existentes}),
            ),
            patch.object(
                tools.cantina_api,
                "crear_reserva",
                new=AsyncMock(return_value=respuesta),
            ) as crear,
        ):
            result = await tools.handler_crear_reserva(
                {
                    "fecha": "2026-07-03",
                    "mesa_id": 24,
                    "nombre_cliente": "Stefany Pérez",
                    "num_personas": 6,
                },
                ctx,
            )

        self.assertTrue(result["ok"])
        crear.assert_awaited_once()
        self.assertEqual(crear.await_args.args[0]["mesa_id"], 24)
        self.assertEqual(crear.await_args.args[0]["telefono"], "+573135341582")
        self.assertEqual(len(ctx["outbox"]), 1)
        self.assertIn("*Mesa:* 24", ctx["outbox"][0]["mensaje"])

    async def test_bloquea_segunda_reserva_si_repite_misma_mesa(self):
        ctx = {
            "cliente_numero": "+573135341582",
            "nombre_reserva_confirmado": "Stefany Pérez",
            "outbox": [],
        }
        existentes = [{
            "id": 201,
            "fecha": "2026-07-03",
            "mesa_numero": 18,
            "telefono": "+573135341582",
            "estado": "confirmada",
        }]

        with (
            patch.object(
                tools.cantina_api,
                "listar_reservas",
                new=AsyncMock(return_value={"ok": True, "reservas": existentes}),
            ),
            patch.object(
                tools.cantina_api,
                "crear_reserva",
                new=AsyncMock(),
            ) as crear,
        ):
            result = await tools.handler_crear_reserva(
                {
                    "fecha": "2026-07-03",
                    "mesa_id": 18,
                    "nombre_cliente": "Stefany Pérez",
                    "num_personas": 8,
                },
                ctx,
            )

        self.assertTrue(result["ya_reservado"])
        self.assertIn("mesa 18", result["error"])
        crear.assert_not_awaited()
        self.assertEqual(ctx["outbox"], [])


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


class CambioPersonasTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = {
            "cliente_numero": "+573022043954",
            "intent": "modificar_reserva",
            "outbox": [],
        }
        self.reserva = {
            "id": 246,
            "fecha": "2026-07-07",
            "mesa_id": 21,
            "mesa_numero": 21,
            "mesa_zona": "VIP",
            "nombre_cliente": "Rubén Ramos",
            "telefono": "+573022043954",
            "num_personas": 2,
            "estado": "confirmada",
            "tipo_reserva": "mesa",
            "grupo_id": None,
        }

    async def test_actualiza_personas_sin_pedir_validacion(self):
        actualizada = dict(self.reserva, num_personas=4)
        with (
            patch.object(
                tools.cantina_api,
                "reservas_cliente",
                new=AsyncMock(return_value={"ok": True, "reservas": [self.reserva]}),
            ),
            patch.object(
                tools.cantina_api,
                "actualizar_reserva",
                new=AsyncMock(return_value={"ok": True, "reserva": actualizada}),
            ) as actualizar,
        ):
            resultado = await tools.handler_actualizar_personas_reserva_cliente(
                {"reserva_id": 246, "num_personas": 4},
                self.ctx,
            )

        self.assertTrue(resultado["modificada"])
        self.assertIn("No pidas validación adicional", resultado["instruccion"])
        actualizar.assert_awaited_once_with(246, {"num_personas": 4})
        self.assertEqual(len(self.ctx["outbox"]), 1)
        mensaje = self.ctx["outbox"][0]["mensaje"]
        self.assertIn("*Reserva modificada", mensaje)
        self.assertIn("*Personas antes:* 2", mensaje)
        self.assertIn("*Personas ahora:* 4", mensaje)

    async def test_no_actualiza_reserva_ajena_o_inexistente(self):
        with (
            patch.object(
                tools.cantina_api,
                "reservas_cliente",
                new=AsyncMock(return_value={"ok": True, "reservas": [self.reserva]}),
            ),
            patch.object(
                tools.cantina_api,
                "actualizar_reserva",
                new=AsyncMock(),
            ) as actualizar,
        ):
            resultado = await tools.handler_actualizar_personas_reserva_cliente(
                {"reserva_id": 999, "num_personas": 4},
                self.ctx,
            )

        self.assertFalse(resultado["ok"])
        actualizar.assert_not_awaited()

    async def test_infiere_reserva_por_fecha_del_chat(self):
        hoy = datetime.now(ZoneInfo("America/Bogota")).date()
        reserva_hoy = dict(self.reserva, fecha=hoy.isoformat())
        reserva_manana = dict(
            self.reserva,
            id=247,
            fecha=(hoy + timedelta(days=1)).isoformat(),
            mesa_numero=22,
        )
        self.ctx["mensaje_actual_cliente"] = "actualiza la de hoy a 4 personas"
        with (
            patch.object(
                tools.cantina_api,
                "reservas_cliente",
                new=AsyncMock(return_value={
                    "ok": True,
                    "reservas": [reserva_hoy, reserva_manana],
                }),
            ),
            patch.object(
                tools.cantina_api,
                "actualizar_reserva",
                new=AsyncMock(return_value={"ok": True}),
            ) as actualizar,
        ):
            resultado = await tools.handler_actualizar_personas_reserva_cliente(
                {"num_personas": 4},
                self.ctx,
            )

        self.assertTrue(resultado["modificada"])
        actualizar.assert_awaited_once_with(246, {"num_personas": 4})

    async def test_infiere_reserva_por_mesa_del_historial(self):
        reserva_21 = dict(self.reserva, id=246, mesa_numero=21)
        reserva_22 = dict(self.reserva, id=247, mesa_numero=22)
        self.ctx["mensaje_actual_cliente"] = "actualízala a 4 personas"
        self.ctx["historial_cliente_reciente"] = [
            {
                "direccion": "outbound",
                "contenido": "Tu reserva #246 para hoy quedó en mesa 21 VIP.",
            }
        ]
        with (
            patch.object(
                tools.cantina_api,
                "reservas_cliente",
                new=AsyncMock(return_value={
                    "ok": True,
                    "reservas": [reserva_21, reserva_22],
                }),
            ),
            patch.object(
                tools.cantina_api,
                "actualizar_reserva",
                new=AsyncMock(return_value={"ok": True}),
            ) as actualizar,
        ):
            resultado = await tools.handler_actualizar_personas_reserva_cliente(
                {"num_personas": 4},
                self.ctx,
            )

        self.assertTrue(resultado["modificada"])
        actualizar.assert_awaited_once_with(246, {"num_personas": 4})

    def test_tool_cliente_incluye_actualizar_personas(self):
        nombres = {tool["name"] for tool in tools.TOOL_DEFINITIONS}

        self.assertIn("actualizar_personas_reserva_cliente", nombres)
        self.assertIn("actualizar_personas_reserva_cliente", tools.HANDLERS)


if __name__ == "__main__":
    unittest.main()
