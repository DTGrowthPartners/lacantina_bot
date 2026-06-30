import unittest
from unittest.mock import AsyncMock, patch

from app.claude import tools, tools_equipo
from app.eventos import extraer_eventos, resumen_eventos


class EventosMultiplesTests(unittest.IsolatedAsyncioTestCase):
    def test_extrae_eventos_lista_y_resume_con_hora(self):
        resp = {
            "ok": True,
            "evento": {"fecha": "2026-07-03", "nombre": "Primero"},
            "eventos": [
                {"fecha": "2026-07-03", "hora_inicio": "22:00", "nombre": "Show noche"},
                {"fecha": "2026-07-03", "hora_inicio": "18:00", "nombre": "Partido"},
            ],
        }

        eventos = extraer_eventos(resp)

        self.assertEqual(len(eventos), 2)
        self.assertEqual(
            resumen_eventos(eventos),
            "18:00 Partido · 22:00 Show noche",
        )

    async def test_cliente_consultar_evento_devuelve_todos_los_eventos_del_dia(self):
        backend = {
            "ok": True,
            "evento": {"fecha": "2026-07-03", "nombre": "Partido", "hora_inicio": "18:00"},
            "eventos": [
                {"fecha": "2026-07-03", "nombre": "Show noche", "hora_inicio": "22:00", "tiene_cover": True, "valor_cover": 20000},
                {"fecha": "2026-07-03", "nombre": "Partido", "hora_inicio": "18:00", "tiene_cover": False, "valor_cover": 0},
            ],
        }
        ctx = {}

        with patch.object(
            tools.cantina_api,
            "consultar_evento",
            new=AsyncMock(return_value=backend),
        ):
            res = await tools.handler_consultar_evento({"fecha": "2026-07-03"}, ctx)

        self.assertEqual(res["total_eventos"], 2)
        self.assertEqual([e["nombre"] for e in res["eventos"]], ["Partido", "Show noche"])
        self.assertIn("politica_horario_cover", res)
        self.assertEqual(ctx["flyer_evento_fecha"], "2026-07-03")

    async def test_equipo_eventos_del_mes_conserva_dos_eventos_misma_fecha(self):
        backend = {
            "ok": True,
            "eventos": [
                {"fecha": "2026-07-03", "nombre": "Show noche", "hora_inicio": "22:00"},
                {"fecha": "2026-07-03", "nombre": "Partido", "hora_inicio": "18:00"},
                {"fecha": "2026-08-01", "nombre": "Otro mes", "hora_inicio": "20:00"},
            ],
        }

        with patch.object(
            tools_equipo.cantina_api,
            "listar_eventos",
            new=AsyncMock(return_value=backend),
        ):
            res = await tools_equipo.handler_eventos_del_mes({"mes": "2026-07"}, {})

        self.assertEqual(res["total"], 2)
        self.assertEqual([e["nombre"] for e in res["eventos"]], ["Partido", "Show noche"])

