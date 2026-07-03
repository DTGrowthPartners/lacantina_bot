import unittest

from app.claude.tools_equipo import HANDLERS_EQUIPO, TOOL_DEFINITIONS_EQUIPO
from app.flows.equipo import _correccion_nombre_reserva_pedida


class CorreccionesEquipoTests(unittest.TestCase):
    def test_detecta_correccion_nombre_reserva(self):
        pedido = (
            'Reserva ID 199 — 2026-07-03, mesa 20, 6 personas, teléfono '
            '+573108291174. El nombre quedó guardado como "Por favor" por '
            'error del bot. El nombre correcto es "Juan Otalvaro". '
            'Por favor corregirlo en el sistema.'
        )

        self.assertEqual(
            _correccion_nombre_reserva_pedida(pedido),
            {"reserva_id": 199, "nombre_cliente": "Juan Otalvaro"},
        )

    def test_ignora_mensajes_sin_nombre_correcto(self):
        self.assertIsNone(
            _correccion_nombre_reserva_pedida("Reserva ID 199 quedó rara, revísala")
        )

    def test_tiene_tool_para_revisar_historial_cliente(self):
        nombres = {tool["name"] for tool in TOOL_DEFINITIONS_EQUIPO}

        self.assertIn("consultar_historial_cliente", nombres)
        self.assertIn("consultar_historial_cliente", HANDLERS_EQUIPO)
