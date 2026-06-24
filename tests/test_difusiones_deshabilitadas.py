import unittest

from app.admin._shell import sidebar_html
from app.claude.tools_equipo import (
    HANDLERS_EQUIPO,
    TOOL_DEFINITIONS_EQUIPO,
    ejecutar_tool_equipo,
)
from app.main import app


class DifusionesDeshabilitadasTests(unittest.IsolatedAsyncioTestCase):
    def test_no_aparece_en_dashboard(self):
        self.assertNotIn("/admin/difusiones", sidebar_html())
        self.assertFalse(
            any(route.path.startswith("/admin/difusiones") for route in app.routes)
        )

    async def test_no_existe_tool_de_texto(self):
        nombres = {tool["name"] for tool in TOOL_DEFINITIONS_EQUIPO}
        self.assertNotIn("crear_difusion_evento", nombres)
        self.assertNotIn("crear_difusion_evento", HANDLERS_EQUIPO)

        resultado = await ejecutar_tool_equipo("crear_difusion_evento", {}, {})

        self.assertIn("desconocida", resultado["error"])


if __name__ == "__main__":
    unittest.main()
