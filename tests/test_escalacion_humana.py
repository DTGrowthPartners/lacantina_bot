import unittest

from app.flows.conversation import _asegurar_escalacion_humana


class EscalacionHumanaTests(unittest.TestCase):
    def test_encola_aviso_cuando_modelo_omite_tool(self):
        outbox = []

        agregada = _asegurar_escalacion_humana(
            outbox,
            intent="pide_humano",
            cliente_numero="+573001112233",
            mensaje_cliente="¿Puedo llevar un pudin?",
        )

        self.assertTrue(agregada)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["clase"], "escalacion")
        self.assertIn("+573001112233", outbox[0]["mensaje"])
        self.assertIn("¿Puedo llevar un pudin?", outbox[0]["mensaje"])

    def test_no_duplica_escalacion_creada_por_tool(self):
        outbox = [{"clase": "escalacion", "mensaje": "Ya encolada"}]

        agregada = _asegurar_escalacion_humana(
            outbox,
            intent="queja",
            cliente_numero="+573001112233",
            mensaje_cliente="Tengo una queja",
        )

        self.assertFalse(agregada)
        self.assertEqual(len(outbox), 1)

    def test_no_escala_intent_ordinario(self):
        outbox = []

        agregada = _asegurar_escalacion_humana(
            outbox,
            intent="pregunta_horario",
            cliente_numero="+573001112233",
            mensaje_cliente="¿A qué hora abren?",
        )

        self.assertFalse(agregada)
        self.assertEqual(outbox, [])

    def test_no_escala_cierre_simple_aunque_clasifique_humano(self):
        outbox = []

        agregada = _asegurar_escalacion_humana(
            outbox,
            intent="pide_humano",
            cliente_numero="+573126509610",
            mensaje_cliente="Si ya vamos para allá\nGracias",
        )

        self.assertFalse(agregada)
        self.assertEqual(outbox, [])

    def test_no_escala_pedido_generico_de_informacion(self):
        outbox = []

        agregada = _asegurar_escalacion_humana(
            outbox,
            intent="pide_humano",
            cliente_numero="186290297381022@lid",
            mensaje_cliente="¡Hola! Quiero más información",
        )

        self.assertFalse(agregada)
        self.assertEqual(outbox, [])

    def test_no_escala_pedido_generico_de_asesor_sin_motivo(self):
        outbox = []

        agregada = _asegurar_escalacion_humana(
            outbox,
            intent="pide_humano",
            cliente_numero="+573144226354",
            mensaje_cliente="Hola buenos días\nPara hablar con un asesor",
        )

        self.assertFalse(agregada)
        self.assertEqual(outbox, [])

    def test_no_escala_pregunta_de_vestimenta(self):
        outbox = []

        agregada = _asegurar_escalacion_humana(
            outbox,
            intent="pide_humano",
            cliente_numero="19782736863356@lid",
            mensaje_cliente=(
                "Cliente pregunta si hay código de vestimenta, específicamente "
                "si se puede entrar en bermudas y chanclas."
            ),
        )

        self.assertFalse(agregada)
        self.assertEqual(outbox, [])

    def test_queja_real_sigue_escalando(self):
        outbox = []

        agregada = _asegurar_escalacion_humana(
            outbox,
            intent="queja",
            cliente_numero="+573126509610",
            mensaje_cliente="Necesito hablar con alguien, llevo media hora esperando la reserva",
        )

        self.assertTrue(agregada)
        self.assertEqual(len(outbox), 1)

    def test_adjunta_audio_en_escalacion_auto(self):
        outbox = []

        agregada = _asegurar_escalacion_humana(
            outbox,
            intent="pide_humano",
            cliente_numero="+573001112233",
            mensaje_cliente="[El cliente envió una nota de voz, pero no pude entenderla.]",
            media_url="https://example.test/audio.ogg",
            media_mime="audio/ogg",
        )

        self.assertTrue(agregada)
        self.assertEqual(outbox[0]["media_url"], "https://example.test/audio.ogg")
        self.assertEqual(outbox[0]["media_mime"], "audio/ogg")
