import unittest

from app.claude.tools import _anotar_politica_horario_cover


class PoliticaHorarioCoverTests(unittest.TestCase):
    def test_anota_evento_con_cover(self):
        respuesta = {
            "ok": True,
            "evento": {"tiene_cover": True, "valor_cover": 30000},
        }

        resultado = _anotar_politica_horario_cover(respuesta)

        politica = resultado["politica_horario_cover"]
        self.assertIn("9:00 p. m.", politica)
        self.assertIn("antes", politica)
        self.assertIn("se queden al evento", politica)
        self.assertIn("debe retirarse", politica)

    def test_anota_reserva_con_cover_pendiente(self):
        respuesta = {
            "ok": True,
            "reserva": {"cover_estado": "pendiente", "monto_cover": 150000},
        }

        resultado = _anotar_politica_horario_cover(respuesta)

        self.assertIn("politica_horario_cover", resultado)

    def test_no_anota_dia_sin_cover(self):
        respuesta = {
            "ok": True,
            "evento": None,
            "reserva": {"cover_estado": "no_aplica", "monto_cover": 0},
        }

        resultado = _anotar_politica_horario_cover(respuesta)

        self.assertNotIn("politica_horario_cover", resultado)


if __name__ == "__main__":
    unittest.main()
