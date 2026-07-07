import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.claude import tools, tools_equipo
from app.flows.equipo import _pide_marcar_casa_llena_hoy, _pide_reabrir_reservas_hoy


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


class CasaLlenaEquipoTests(unittest.IsolatedAsyncioTestCase):
    def test_detecta_comandos_directos_para_hoy(self):
        casos = (
            "Casa llena",
            "@La Cantina Plus casa llena",
            "activar casa llena",
            "estamos llenos",
            "no acepten mas reservas",
            "cierren reservas",
        )

        for caso in casos:
            with self.subTest(caso=caso):
                self.assertTrue(_pide_marcar_casa_llena_hoy(caso))

    def test_no_detecta_consultas_ni_fechas_futuras(self):
        casos = (
            "¿Hay casa llena?",
            "revisa si hay casa llena",
            "casa llena mañana",
            "casa llena para el 10",
            "estado de casa llena",
        )

        for caso in casos:
            with self.subTest(caso=caso):
                self.assertFalse(_pide_marcar_casa_llena_hoy(caso))

    def test_detecta_comandos_directos_para_reabrir_hoy(self):
        casos = (
            "Abrir reservas",
            "@La Cantina Plus abrir reservas",
            "abre reservas",
            "reabrir reservas",
            "quitar casa llena",
            "desactivar casa llena",
        )

        for caso in casos:
            with self.subTest(caso=caso):
                self.assertTrue(_pide_reabrir_reservas_hoy(caso))

    def test_no_reabre_por_consultas_ni_fechas_futuras(self):
        casos = (
            "¿Están abiertas las reservas?",
            "revisa si están abiertas las reservas",
            "abrir reservas mañana",
            "abrir reservas para el 10",
            "estado de reservas",
        )

        for caso in casos:
            with self.subTest(caso=caso):
                self.assertFalse(_pide_reabrir_reservas_hoy(caso))

    async def test_tool_equipo_sin_fecha_cierra_solo_hoy(self):
        hoy = datetime.now(ZoneInfo("America/Bogota")).date().isoformat()

        with patch.object(
            tools_equipo.cantina_api,
            "marcar_casa_llena",
            new=AsyncMock(return_value={"ok": True}),
        ) as marcar:
            result = await tools_equipo.handler_marcar_casa_llena(
                {},
                {"miembro_nombre": "Edgardo"},
            )

        self.assertTrue(result["ok"])
        marcar.assert_awaited_once_with(hoy, "Casa llena", "Edgardo")

    async def test_tool_equipo_sin_fecha_reabre_solo_hoy(self):
        hoy = datetime.now(ZoneInfo("America/Bogota")).date().isoformat()

        with patch.object(
            tools_equipo.cantina_api,
            "reabrir_reservas",
            new=AsyncMock(return_value={"ok": True}),
        ) as reabrir:
            result = await tools_equipo.handler_reabrir_reservas(
                {},
                {"miembro_nombre": "Edgardo"},
            )

        self.assertTrue(result["ok"])
        reabrir.assert_awaited_once_with(hoy)


if __name__ == "__main__":
    unittest.main()
