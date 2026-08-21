import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import notif_equipo
from app.claude import tools
from app.flows.conversation import (
    _debe_auto_encolar_comprobante,
    _esperaba_comprobante_pago,
    _esperaba_confirmacion_reserva,
)


class ComprobanteFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_detecta_comprobante_como_confirmacion_de_reserva(self):
        historial = [
            SimpleNamespace(direccion="inbound", contenido="A nombre de Santiago Gallo"),
            SimpleNamespace(
                direccion="outbound",
                contenido=(
                    "Resumen antes de confirmar: mesa 17, 4 personas, "
                    "a nombre de Santiago Gallo. ¿Confirmamos?"
                ),
            ),
        ]

        self.assertTrue(_esperaba_confirmacion_reserva(historial))

    async def test_no_detecta_confirmacion_en_outbound_generico(self):
        historial = [
            SimpleNamespace(direccion="outbound", contenido="Cuando pagues mándame el comprobante."),
        ]

        self.assertFalse(_esperaba_confirmacion_reserva(historial))
        self.assertTrue(_esperaba_comprobante_pago(historial))

    async def test_imagen_sin_texto_no_se_auto_encola_solo_por_intent(self):
        msg = SimpleNamespace(
            media_url="https://example.test/buenos-dias.jpg",
            tipo="imagen",
            texto=None,
        )
        historial = [
            SimpleNamespace(direccion="inbound", contenido=None),
        ]

        self.assertFalse(_debe_auto_encolar_comprobante(
            msg=msg,
            intent="envia_comprobante_pago",
            contenido_usuario="[El cliente envió una imagen sin texto.]",
            historial_db=historial,
        ))

    async def test_imagen_sin_texto_si_se_auto_encola_si_esperaba_comprobante(self):
        msg = SimpleNamespace(
            media_url="https://example.test/comprobante.jpg",
            tipo="imagen",
            texto=None,
        )
        historial = [
            SimpleNamespace(direccion="outbound", contenido="Cuando pagues mándame el comprobante."),
        ]

        self.assertTrue(_debe_auto_encolar_comprobante(
            msg=msg,
            intent="envia_comprobante_pago",
            contenido_usuario="[El cliente envió una imagen sin texto.]",
            historial_db=historial,
        ))

    async def test_notification_prefers_downloaded_image_bytes(self):
        with (
            patch.object(
                notif_equipo.settings,
                "equipo_cantina_group_id",
                "equipo@g.us",
            ),
            patch.object(
                notif_equipo,
                "enviar_imagen_bytes",
                new=AsyncMock(return_value={"ok": True}),
            ) as enviar_imagen,
            patch.object(
                notif_equipo,
                "_descargar_media",
                new=AsyncMock(),
            ) as descargar,
        ):
            sent = await notif_equipo.notificar_equipo(
                "Comprobante",
                media_url="https://example.test/comprobante.jpg",
                media_bytes=b"imagen",
                media_mime="image/png",
            )

        self.assertTrue(sent)
        enviar_imagen.assert_awaited_once_with(
            "equipo@g.us",
            b"imagen",
            mime="image/png",
            caption="Comprobante",
        )
        descargar.assert_not_awaited()

    async def test_notification_sends_pdf_as_document(self):
        with (
            patch.object(
                notif_equipo.settings,
                "equipo_cantina_group_id",
                "equipo@g.us",
            ),
            patch.object(
                notif_equipo,
                "enviar_documento_bytes",
                new=AsyncMock(return_value={"ok": True}),
            ) as enviar_documento,
            patch.object(
                notif_equipo,
                "enviar_imagen_bytes",
                new=AsyncMock(return_value={"ok": True}),
            ) as enviar_imagen,
            patch.object(
                notif_equipo,
                "_descargar_media",
                new=AsyncMock(return_value=(b"%PDF", "application/pdf")),
            ),
        ):
            sent = await notif_equipo.notificar_equipo(
                "Comprobante PDF",
                media_url="https://example.test/comprobante.pdf",
            )

        self.assertTrue(sent)
        enviar_documento.assert_awaited_once_with(
            "equipo@g.us",
            b"%PDF",
            mime="application/pdf",
            filename="comprobante.pdf",
            caption="Comprobante PDF",
        )
        enviar_imagen.assert_not_awaited()

    async def test_notification_sends_audio_as_document_with_audio_filename(self):
        with (
            patch.object(
                notif_equipo.settings,
                "equipo_cantina_group_id",
                "equipo@g.us",
            ),
            patch.object(
                notif_equipo,
                "enviar_documento_bytes",
                new=AsyncMock(return_value={"ok": True}),
            ) as enviar_documento,
            patch.object(
                notif_equipo,
                "enviar_imagen_bytes",
                new=AsyncMock(return_value={"ok": True}),
            ) as enviar_imagen,
            patch.object(
                notif_equipo,
                "_descargar_media",
                new=AsyncMock(return_value=(b"OggS", "audio/ogg")),
            ),
        ):
            sent = await notif_equipo.notificar_equipo(
                "Audio no entendido",
                media_url="https://example.test/voz.ogg",
            )

        self.assertTrue(sent)
        enviar_documento.assert_awaited_once_with(
            "equipo@g.us",
            b"OggS",
            mime="audio/ogg",
            filename="audio-cliente.ogg",
            caption="Audio no entendido",
        )
        enviar_imagen.assert_not_awaited()

    async def test_escalacion_enriches_existing_receipt_without_duplicate(self):
        outbox = [{
            "tipo": "comprobante_cover",
            "mensaje": "Comprobante recibido",
            "media_url": "https://example.test/comprobante.jpg",
        }]
        ctx = {
            "cliente_id": 10,
            "cliente_numero": "+573001112233",
            "intent": "envia_comprobante_pago",
            "outbox": outbox,
        }

        result = await tools.handler_escalar_a_equipo(
            {"tipo": "otro", "mensaje": "Transferencia por $100.000"},
            ctx,
        )

        self.assertTrue(result["comprobante_adjuntado"])
        self.assertEqual(len(outbox), 1)
        self.assertIn("Transferencia por $100.000", outbox[0]["mensaje"])

    async def test_tool_escalacion_adjunta_audio_entrante(self):
        outbox = []
        ctx = {
            "cliente_id": 10,
            "cliente_numero": "+573001112233",
            "intent": "pide_humano",
            "outbox": outbox,
            "incoming_media_tipo": "audio",
            "incoming_media_url": "https://example.test/voz.ogg",
            "incoming_media_mime": "audio/ogg",
        }

        result = await tools.handler_escalar_a_equipo(
            {"tipo": "pide_humano", "mensaje": "No pude entender la nota de voz."},
            ctx,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(outbox[0]["media_url"], "https://example.test/voz.ogg")
        self.assertEqual(outbox[0]["media_mime"], "audio/ogg")
        self.assertIn("Nota de voz adjunta", outbox[0]["mensaje"])

    async def test_register_receipt_uses_media_from_context(self):
        outbox = [{
            "tipo": "comprobante_cover",
            "mensaje": "Comprobante recibido",
            "media_url": "https://example.test/comprobante.jpg",
        }]
        ctx = {
            "cliente_numero": "+573001112233",
            "incoming_media_url": "https://example.test/comprobante.jpg",
            "outbox": outbox,
        }
        detalle = {
            "ok": True,
            "reserva": {"telefono": "+573001112233"},
        }

        with (
            patch.object(
                tools.cantina_api,
                "detalle_reserva",
                new=AsyncMock(return_value=detalle),
            ),
            patch.object(
                tools.cantina_api,
                "actualizar_reserva",
                new=AsyncMock(return_value={"ok": True}),
            ) as actualizar,
        ):
            result = await tools.handler_registrar_comprobante_cover(
                {"reserva_id": 78},
                ctx,
            )

        self.assertTrue(result["ok"])
        actualizar.assert_awaited_once()
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["reserva_id"], 78)
        self.assertIn("reserva #78", outbox[0]["mensaje"])
