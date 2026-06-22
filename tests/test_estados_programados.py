import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app import estados_programados
from app.claude import tools_equipo


class FechaEstadoProgramadoTests(unittest.TestCase):
    def setUp(self):
        self.ahora = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)

    def test_parsea_hora_colombia_con_minutos(self):
        valor = estados_programados.parsear_fecha_hora_colombia(
            "2026-06-23", "7:30 p. m.", ahora=self.ahora
        )
        self.assertEqual(valor.isoformat(), "2026-06-23T19:30:00-05:00")

    def test_parsea_mediodia_y_medianoche(self):
        mediodia = estados_programados.parsear_fecha_hora_colombia(
            "2026-06-23", "12 PM", ahora=self.ahora
        )
        medianoche = estados_programados.parsear_fecha_hora_colombia(
            "2026-06-24", "12:00 AM", ahora=self.ahora
        )
        self.assertEqual(mediodia.hour, 12)
        self.assertEqual(medianoche.hour, 0)

    def test_rechaza_formato_24_horas(self):
        with self.assertRaisesRegex(ValueError, "12 horas"):
            estados_programados.parsear_fecha_hora_colombia(
                "2026-06-23", "19:00", ahora=self.ahora
            )

    def test_rechaza_fecha_pasada(self):
        with self.assertRaisesRegex(ValueError, "futuro"):
            estados_programados.parsear_fecha_hora_colombia(
                "2026-06-21", "7 PM", ahora=self.ahora
            )


class ToolEstadoProgramadoTests(unittest.IsolatedAsyncioTestCase):
    async def test_exige_medio_adjunto(self):
        resultado = await tools_equipo.handler_programar_estado(
            {"fecha": "2099-01-01", "hora": "7 PM"},
            {"rol": "equipo"},
        )
        self.assertFalse(resultado["ok"])
        self.assertIn("Adjunta", resultado["error"])

    async def test_programa_imagen_en_hora_colombia(self):
        ctx = {
            "rol": "equipo",
            "session": object(),
            "imagen_bytes": b"imagen",
            "imagen_mime": "image/png",
            "miembro_nombre": "Edgardo",
        }
        with patch.object(
            estados_programados,
            "crear",
            new=AsyncMock(return_value=42),
        ) as crear:
            resultado = await tools_equipo.handler_programar_estado(
                {
                    "fecha": "2099-01-01",
                    "hora": "7:15 PM",
                    "caption": "Nos vemos en La Cantina",
                },
                ctx,
            )

        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["estado_id"], 42)
        self.assertIn("7:15 PM", resultado["programado_para"])
        crear.assert_awaited_once()
        self.assertEqual(crear.await_args.kwargs["tipo"], "imagen")
        self.assertEqual(crear.await_args.kwargs["creado_por"], "Edgardo")
