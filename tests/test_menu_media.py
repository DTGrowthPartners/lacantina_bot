import unittest

from app.menu_media import imagenes_menu, pide_imagen_menu


class MenuMediaTests(unittest.TestCase):
    def test_detecta_solicitud_de_imagen_del_menu(self):
        self.assertTrue(pide_imagen_menu("dame la imagen del menu"))
        self.assertTrue(pide_imagen_menu("pásame la carta en fotos"))

    def test_no_intercepta_consulta_generica_de_precios(self):
        self.assertFalse(pide_imagen_menu("cuanto vale el Old Parr"))
        self.assertFalse(pide_imagen_menu("pásame el link del menú"))

    def test_estan_las_tres_paginas_renderizadas(self):
        paginas = imagenes_menu()
        self.assertEqual([p.name for p in paginas], ["menu-1.png", "menu-2.png", "menu-3.png"])
        self.assertTrue(all(p.stat().st_size > 50_000 for p in paginas))
