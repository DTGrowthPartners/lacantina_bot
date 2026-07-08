import unittest
from datetime import datetime, timezone

from app.admin.dashboard import _inicio_dia_colombia_utc, _reservas_payload_desde_resumen


class DashboardReservasTests(unittest.TestCase):
    def test_hoy_operativo_usa_fecha_colombia_no_utc(self):
        inicio_utc, fecha = _inicio_dia_colombia_utc(
            datetime(2026, 7, 8, 2, 30, tzinfo=timezone.utc)
        )

        self.assertEqual(fecha, "2026-07-07")
        self.assertEqual(inicio_utc.isoformat(), "2026-07-07T05:00:00+00:00")

    def test_total_reservas_cuenta_reservas_y_salas(self):
        resumen, reservas = _reservas_payload_desde_resumen({
            "mesas_ocupadas": 2,
            "mesas_totales": 42,
            "total_personas": 16,
            "reservas": [
                {
                    "id": 1,
                    "nombre_cliente": "Ana",
                    "mesa_numero": 1,
                    "mesa_zona": "Cantina",
                    "num_personas": 4,
                },
                {
                    "id": 2,
                    "nombre_cliente": "Luis",
                    "mesa_numero": 2,
                    "mesa_zona": "Cantina",
                    "num_personas": 2,
                },
            ],
            "salas": [
                {
                    "nombre": "Sala VIP 1",
                    "reserva": {
                        "id": 3,
                        "nombre_cliente": "Marta",
                        "num_personas": 10,
                    },
                },
            ],
        })

        self.assertEqual(resumen["total_reservas"], 3)
        self.assertEqual(len(reservas), 3)
        self.assertEqual(reservas[-1]["mesa_label"], "Sala VIP 1")


if __name__ == "__main__":
    unittest.main()
