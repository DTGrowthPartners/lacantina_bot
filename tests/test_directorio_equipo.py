import time
import unittest

from app.equipo import directorio
from app.equipo.directorio import Miembro


class DirectorioEquipoTests(unittest.TestCase):
    def setUp(self):
        self._cache_original = {
            key: value.copy() if hasattr(value, "copy") else value
            for key, value in directorio._cache.items()
        }

    def tearDown(self):
        directorio._cache.clear()
        directorio._cache.update(self._cache_original)

    def test_miembro_equipo_matchea_numero_con_espacios(self):
        yordi = Miembro(
            nombre="Yordi",
            numero_whatsapp="+57 301 7866478",
            rol="Seguridad",
            areas=(),
            es_fallback=False,
            activo=True,
        )
        directorio._cache.update(
            {
                "loaded_at": time.time(),
                "miembros": [yordi],
                "miembros_por_numero": {"+573017866478": yordi},
                "numeros_internos": {"+573017866478"},
                "numeros_internos_norm": {"+573017866478"},
                "clientes_whitelist": {},
                "clientes_whitelist_norm": {},
            }
        )

        self.assertEqual(
            directorio.es_miembro_equipo("+573017866478"),
            yordi,
        )
        self.assertFalse(directorio.es_numero_interno("+57 301 7866478"))
