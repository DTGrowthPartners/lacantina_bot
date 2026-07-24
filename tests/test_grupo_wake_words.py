import unittest

from app.main import _menciona_nikki


class GrupoWakeWordsTests(unittest.TestCase):
    def test_reconoce_nombre_visible_del_bot(self):
        casos = (
            "@La Cantina Plus este es el link de pago",
            "La Cantina Plus casa llena",
            "cantina plus abre reservas",
        )

        for caso in casos:
            with self.subTest(caso=caso):
                self.assertTrue(_menciona_nikki(caso))

    def test_conserva_variantes_de_nicky(self):
        for caso in ("Nicky revisa esto", "Nikki revisa esto", "Niqui revisa esto"):
            with self.subTest(caso=caso):
                self.assertTrue(_menciona_nikki(caso))


if __name__ == "__main__":
    unittest.main()
