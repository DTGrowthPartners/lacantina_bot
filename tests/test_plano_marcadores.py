import io
import unittest

from app.utils import plano


class PlanoMarcadoresTests(unittest.TestCase):
    def test_todas_las_mesas_tienen_coordenadas_dentro_del_plano(self):
        self.assertEqual(set(plano.MESAS_XY), set(range(1, 43)))
        for numero, (x, y) in plano.MESAS_XY.items():
            with self.subTest(mesa=numero):
                self.assertGreaterEqual(x, 0)
                self.assertLess(x, 1024)
                self.assertGreaterEqual(y, 0)
                self.assertLess(y, 735)

    def test_el_marcador_se_dibuja_en_el_centro_configurado(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow no está instalado")

        datos = plano.generar_plano_con_reservas([1, 21, 34, 42])
        imagen = Image.open(io.BytesIO(datos)).convert("RGB")

        for numero in (1, 21, 34, 42):
            with self.subTest(mesa=numero):
                x, y = plano.MESAS_XY[numero]
                pixeles_rojos = 0
                for px in range(x - 12, x + 13):
                    for py in range(y - 12, y + 13):
                        rojo, verde, azul = imagen.getpixel((px, py))
                        if rojo > 140 and rojo > verde * 1.4 and rojo > azul * 1.4:
                            pixeles_rojos += 1
                self.assertGreater(pixeles_rojos, 150)


if __name__ == "__main__":
    unittest.main()
