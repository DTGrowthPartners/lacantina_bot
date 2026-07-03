import unittest

from app.nombres import limpiar_nombre_reserva, validar_nombre_reserva


class NombreValidatorTests(unittest.TestCase):
    def test_acepta_nombres_reales(self):
        casos = (
            "Melissa Urueta",
            "DIEGO CARRILLO RAMOS",
            "Juan de Dios Pérez",
            "María del Mar",
            "Por favor Juan Otalvaro",
            "Mi nombre Natalia Álvarez",
            "David Arriola está bien",
        )

        for caso in casos:
            with self.subTest(caso=caso):
                self.assertTrue(validar_nombre_reserva(caso).es_nombre)

    def test_limpia_prefijos_y_sufijos(self):
        self.assertEqual(
            limpiar_nombre_reserva("Por favor Juan Otalvaro"),
            "Juan Otalvaro",
        )
        self.assertEqual(
            limpiar_nombre_reserva("David Arriola está bien"),
            "David Arriola",
        )

    def test_rechaza_frases_de_ubicacion_instruccion_y_confirmacion(self):
        casos = (
            "Es en toda la esquina",
            "Y me dice en qué puesto quedaríamos",
            "Puedes mandarme la ubicación",
            "Me confirmas",
            "Y me dice en qué puesto quedaríamos",
            "Quiero mesa cerca de la pantalla",
            "En la esquina",
            "Mesa 28",
            "La 18",
            "Correcto",
        )

        for caso in casos:
            with self.subTest(caso=caso):
                self.assertFalse(validar_nombre_reserva(caso).es_nombre)


if __name__ == "__main__":
    unittest.main()
