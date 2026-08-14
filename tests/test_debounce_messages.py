import unittest

from app.main import _combinar_mensajes_debounce
from app.whapi.parser import MensajeWhapi


def _mensaje(
    mid: str,
    texto: str,
    media_url: str | None = None,
    *,
    tipo: str | None = None,
    media_mime: str | None = None,
) -> MensajeWhapi:
    return MensajeWhapi(
        id=mid,
        from_number="+573000000000",
        to_number=None,
        direccion="inbound",
        is_from_bot=False,
        is_from_human=False,
        tipo=tipo or ("imagen" if media_url else "texto"),
        texto=texto,
        media_url=media_url,
        media_mime=media_mime or ("image/jpeg" if media_url else None),
        caption=None,
        timestamp=int(mid),
        chat_id="573000000000@s.whatsapp.net",
        raw={},
    )


class DebounceMessagesTests(unittest.TestCase):
    def test_conserva_imagen_y_usa_id_del_ultimo_mensaje(self):
        agrupado = _combinar_mensajes_debounce([
            _mensaje("1", "comprobante", "https://example.com/pago.jpg"),
            _mensaje("2", "ya pagué"),
        ])
        self.assertEqual(agrupado.id, "2")
        self.assertEqual(agrupado.media_url, "https://example.com/pago.jpg")
        self.assertEqual(agrupado.tipo, "imagen")
        self.assertEqual(agrupado.texto, "comprobante\nya pagué")

    def test_conserva_pdf_de_comprobante(self):
        agrupado = _combinar_mensajes_debounce([
            _mensaje(
                "1",
                "",
                "https://example.com/pago.pdf",
                tipo="pdf",
                media_mime="application/pdf",
            ),
            _mensaje("2", "este es el comprobante"),
        ])
        self.assertEqual(agrupado.id, "2")
        self.assertEqual(agrupado.media_url, "https://example.com/pago.pdf")
        self.assertEqual(agrupado.media_mime, "application/pdf")
        self.assertEqual(agrupado.tipo, "pdf")
        self.assertEqual(agrupado.texto, "este es el comprobante")
