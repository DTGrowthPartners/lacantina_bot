import unittest
from unittest.mock import patch

from app.utils import humanizer


class HumanizerIntentTests(unittest.TestCase):
    def test_reservas_responden_entre_5_y_20_segundos(self):
        with patch("app.utils.humanizer.random.uniform", return_value=7):
            delay = humanizer._delay_para(500, "pide_reservar")
        self.assertGreaterEqual(delay, 5)
        self.assertLessEqual(delay, 20)

    def test_conversacion_casual_conserva_configuracion_general(self):
        with patch("app.utils.humanizer.random.uniform", return_value=30):
            delay = humanizer._delay_para(20, "otro")
        self.assertGreaterEqual(delay, humanizer.settings.humanization_delay_min_s)
