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

    async def test_publicar_estado_crea_evento_con_hora_normal_si_flyer_no_tiene_hora(self):
        ctx = {
            "rol": "equipo",
            "imagen_bytes": b"flyer",
            "imagen_mime": "image/png",
            "miembro_nombre": "Edgardo",
        }
        with patch(
            "app.whapi.client.publicar_story_imagen_bytes",
            new=AsyncMock(),
        ), patch(
            "app.promo_estado.guardar_estado",
        ) as guardar_estado, patch.object(
            tools_equipo.cantina_api,
            "consultar_evento",
            new=AsyncMock(return_value={"ok": True, "eventos": []}),
        ), patch.object(
            tools_equipo.cantina_api,
            "crear_evento",
            new=AsyncMock(return_value={"ok": True}),
        ) as crear_evento, patch.object(
            tools_equipo,
            "guardar_flyer",
            return_value=object(),
        ) as guardar_flyer:
            resultado = await tools_equipo.handler_publicar_estado(
                {
                    "caption": "Partido Colombia",
                    "evento_fecha": "2099-01-02",
                    "evento_nombre": "Colombia vs Argentina",
                    "evento_artista": "Transmision en vivo",
                    "evento_tiene_cover": False,
                },
                ctx,
            )

        self.assertTrue(resultado["ok"])
        payload = crear_evento.await_args.args[0]
        self.assertEqual(payload["fecha"], "2099-01-02")
        self.assertEqual(payload["hora_inicio"], "17:00")
        self.assertEqual(payload["nombre"], "Colombia vs Argentina")
        self.assertFalse(payload["tiene_cover"])
        guardar_estado.assert_called_once()
        guardar_flyer.assert_called_once_with("2099-01-02", "17:00", b"flyer", "image/png")
        self.assertTrue(resultado["evento_sync"]["evento_desde_estado"]["creado"])
        self.assertTrue(resultado["evento_sync"]["evento_desde_estado"]["hora_asumida"])

    async def test_publicar_estado_no_duplica_evento_si_fecha_hora_ya_existe(self):
        ctx = {
            "rol": "equipo",
            "imagen_bytes": b"flyer",
            "imagen_mime": "image/jpeg",
            "miembro_nombre": "Edgardo",
        }
        with patch(
            "app.whapi.client.publicar_story_imagen_bytes",
            new=AsyncMock(),
        ), patch(
            "app.promo_estado.guardar_estado",
        ), patch.object(
            tools_equipo.cantina_api,
            "consultar_evento",
            new=AsyncMock(return_value={
                "ok": True,
                "eventos": [
                    {"fecha": "2099-01-02", "hora_inicio": "20:30", "nombre": "Evento previo"},
                ],
            }),
        ), patch.object(
            tools_equipo.cantina_api,
            "crear_evento",
            new=AsyncMock(return_value={"ok": True}),
        ) as crear_evento, patch.object(
            tools_equipo,
            "guardar_flyer",
            return_value=object(),
        ):
            resultado = await tools_equipo.handler_publicar_estado(
                {
                    "evento_fecha": "2099-01-02",
                    "evento_nombre": "Evento previo",
                    "evento_hora_inicio": "20:30",
                    "evento_tiene_cover": True,
                    "evento_valor_cover": 20000,
                },
                ctx,
            )

        self.assertTrue(resultado["ok"])
        crear_evento.assert_not_awaited()
        self.assertFalse(resultado["evento_sync"]["evento_desde_estado"]["creado"])
        self.assertFalse(resultado["evento_sync"]["evento_desde_estado"]["actualizado"])
        self.assertTrue(resultado["evento_sync"]["evento_desde_estado"]["omitido_por_existente"])

    async def test_publicar_estado_sin_hora_usa_evento_existente_y_no_crea_otro(self):
        ctx = {
            "rol": "equipo",
            "imagen_bytes": b"flyer",
            "imagen_mime": "image/png",
            "miembro_nombre": "Edgardo",
        }
        with patch(
            "app.whapi.client.publicar_story_imagen_bytes",
            new=AsyncMock(),
        ), patch(
            "app.promo_estado.guardar_estado",
        ), patch.object(
            tools_equipo.cantina_api,
            "consultar_evento",
            new=AsyncMock(return_value={
                "ok": True,
                "eventos": [
                    {"fecha": "2099-01-02", "hora_inicio": "20:30", "nombre": "Evento previo"},
                ],
            }),
        ), patch.object(
            tools_equipo.cantina_api,
            "crear_evento",
            new=AsyncMock(return_value={"ok": True}),
        ) as crear_evento, patch.object(
            tools_equipo,
            "guardar_flyer",
            return_value=object(),
        ) as guardar_flyer:
            resultado = await tools_equipo.handler_publicar_estado(
                {
                    "evento_fecha": "2099-01-02",
                    "evento_nombre": "Evento previo",
                    "evento_tiene_cover": True,
                    "evento_valor_cover": 20000,
                },
                ctx,
            )

        self.assertTrue(resultado["ok"])
        crear_evento.assert_not_awaited()
        guardar_flyer.assert_called_once_with("2099-01-02", "20:30", b"flyer", "image/png")
        evento_sync = resultado["evento_sync"]["evento_desde_estado"]
        self.assertFalse(evento_sync["creado"])
        self.assertTrue(evento_sync["omitido_por_existente"])
        self.assertEqual(evento_sync["hora_inicio"], "20:30")
