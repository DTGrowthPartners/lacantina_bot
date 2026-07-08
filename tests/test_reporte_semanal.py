import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app.automatizaciones import reporte_semanal


class ReporteSemanalTests(unittest.TestCase):
    def test_rango_default_usa_semana_completa_anterior_colombia(self):
        inicio, fin, periodo = reporte_semanal.rango_reporte(
            ahora=datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(periodo, "semana_anterior")
        self.assertEqual(inicio.isoformat(), "2026-06-29")
        self.assertEqual(fin.isoformat(), "2026-07-05")

    def test_rango_ultimos_7_dias_termina_ayer_colombia(self):
        inicio, fin, periodo = reporte_semanal.rango_reporte(
            "ultimos_7_dias",
            ahora=datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(periodo, "ultimos_7_dias")
        self.assertEqual(inicio.isoformat(), "2026-07-01")
        self.assertEqual(fin.isoformat(), "2026-07-07")

    def test_generar_pdf_reporte_crea_archivo_pdf(self):
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab no esta instalado en este entorno")

        data = {
            "inicio": "2026-07-01",
            "fin": "2026-07-07",
            "filas_dia": [
                {
                    "fecha": f"2026-07-0{i}",
                    "evento_txt": "Evento" if i in (3, 5) else "",
                    "reservas": i,
                    "personas": i * 4,
                    "mesas": i + 1,
                }
                for i in range(1, 8)
            ],
            "bot": {
                "mensajes": 120,
                "inbound": 70,
                "outbound": 48,
                "humano": 2,
                "chats": 35,
                "horas_top": [{"hora": 18, "inbound": 16}, {"hora": 20, "inbound": 11}],
                "intents": [{"intent": "reservar", "total": 25}, {"intent": "menu", "total": 9}],
            },
            "totales": {
                "reservas": 28,
                "personas": 112,
                "mesas": 35,
                "salas": 0,
                "telefonos_unicos": 24,
                "canceladas": 2,
                "eventos": 2,
                "dias_con_reservas": 7,
                "personas_por_reserva": 4,
            },
            "top_dia": {"fecha": "2026-07-07", "personas": 28, "reservas": 7},
            "bajo_dia": {"fecha": "2026-07-01", "personas": 4, "reservas": 1},
            "insights": [
                "Viernes y sabado concentraron la demanda.",
                "Las horas con mas inbound ayudan a decidir pauta.",
            ],
        }

        with TemporaryDirectory() as tmp:
            original = reporte_semanal.REPORT_DIR
            try:
                reporte_semanal.REPORT_DIR = Path(tmp)
                pdf = reporte_semanal.generar_pdf_reporte(data)
            finally:
                reporte_semanal.REPORT_DIR = original

            self.assertTrue(pdf.exists())
            self.assertGreater(pdf.stat().st_size, 1000)
            self.assertEqual(pdf.read_bytes()[:4], b"%PDF")

    def test_line_chart_dibuja_tramo_de_tendencia(self):
        try:
            from reportlab.graphics.shapes import Line
        except ImportError:
            self.skipTest("reportlab no esta instalado en este entorno")

        drawing = reporte_semanal._line_chart(
            [
                {"fecha": "2026-07-01", "personas": 0},
                {"fecha": "2026-07-02", "personas": 12},
                {"fecha": "2026-07-03", "personas": 6},
            ],
            width=420,
            height=160,
        )

        lineas = [item for item in drawing.contents if isinstance(item, Line)]
        self.assertGreaterEqual(len(lineas), 3)


class ReporteSemanalMetricasTests(unittest.IsolatedAsyncioTestCase):
    async def test_metricas_bot_envia_fechas_como_date(self):
        class _Mappings:
            def __init__(self, rows):
                self.rows = rows

            def first(self):
                return self.rows[0]

            def all(self):
                return self.rows

        class _Result:
            def __init__(self, rows):
                self.rows = rows

            def mappings(self):
                return _Mappings(self.rows)

        class _Session:
            def __init__(self):
                self.params = []

            async def execute(self, _query, params):
                self.params.append(params)
                if len(self.params) == 1:
                    return _Result([{
                        "mensajes": 0,
                        "inbound": 0,
                        "outbound": 0,
                        "humano": 0,
                        "chats": 0,
                    }])
                return _Result([])

        session = _Session()
        await reporte_semanal._metricas_bot(session, date(2026, 7, 1), date(2026, 7, 7))

        self.assertTrue(session.params)
        self.assertIsInstance(session.params[0]["inicio"], date)
        self.assertIsInstance(session.params[0]["fin"], date)


if __name__ == "__main__":
    unittest.main()
