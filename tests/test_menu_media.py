import unittest
from unittest.mock import AsyncMock, patch

from app.claude.prompts import construir_system_prompt
from app.flows.conversation import _enviar_link_menu
from app.menu_media import MENU_URL, imagenes_menu, pide_imagen_menu, pide_menu


class MenuMediaTests(unittest.TestCase):
    def test_detecta_solicitud_de_imagen_del_menu(self):
        self.assertTrue(pide_imagen_menu("dame la imagen del menu"))
        self.assertTrue(pide_imagen_menu("pásame la carta en fotos"))

    def test_no_intercepta_consulta_generica_de_precios(self):
        self.assertFalse(pide_imagen_menu("cuanto vale el Old Parr"))
        self.assertFalse(pide_imagen_menu("pásame el link del menú"))

    def test_detecta_cualquier_consulta_del_menu_para_enviar_link(self):
        self.assertTrue(pide_menu("cuanto vale el Old Parr"))
        self.assertTrue(pide_menu("qué cervezas tienen"))
        self.assertTrue(pide_menu("pásame la carta"))
        self.assertTrue(pide_menu("tienen comida?"))
        self.assertFalse(pide_menu("qué eventos tienen este mes"))

    def test_estan_las_tres_paginas_renderizadas(self):
        paginas = imagenes_menu()
        self.assertEqual([p.name for p in paginas], ["menu-1.png", "menu-2.png", "menu-3.png"])
        self.assertTrue(all(p.stat().st_size > 50_000 for p in paginas))

    def test_prompt_cliente_no_carga_lista_de_precios(self):
        texto = "\n".join(
            bloque.get("text", "") for bloque in construir_system_prompt()
        )

        self.assertNotIn("MENÚ / PRECIOS", texto)
        self.assertNotIn("Johnnie Walker Blue Tiffany", texto)


class EnvioMenuLinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_envia_solo_link_canonico(self):
        session = object()
        with (
            patch(
                "app.flows.conversation.enviar_texto",
                new=AsyncMock(),
            ) as enviar,
            patch(
                "app.flows.conversation.guardar_conversacion",
                new=AsyncMock(),
            ) as guardar,
        ):
            await _enviar_link_menu(session, 10, "+573001112233")

        enviar.assert_awaited_once_with("+573001112233", MENU_URL)
        guardar.assert_awaited_once()
        self.assertEqual(guardar.await_args.kwargs["contenido"], MENU_URL)
        self.assertEqual(guardar.await_args.kwargs["tipo"], "texto")
