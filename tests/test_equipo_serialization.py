import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from app import main


class _FakeSession:
    async def execute(self, *_args, **_kwargs):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


@asynccontextmanager
async def _fake_session_factory():
    yield _FakeSession()


class EquipoSerializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_messages_from_same_chat_are_serialized(self):
        active = 0
        max_active = 0
        order = []

        async def fake_process(*, msg, **_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            order.append(f"start:{msg.id}")
            await asyncio.sleep(0.01)
            order.append(f"end:{msg.id}")
            active -= 1

        main._equipo_locks.clear()
        miembro = SimpleNamespace(nombre="Fabio", numero_whatsapp="+573001112233")
        msg_1 = SimpleNamespace(id="msg-1")
        msg_2 = SimpleNamespace(id="msg-2")

        with (
            patch.object(main, "async_session_factory", _fake_session_factory),
            patch.object(main, "procesar_mensaje_equipo", fake_process),
        ):
            await asyncio.gather(
                main._procesar_equipo_async(
                    miembro, msg_1, responder_a="equipo@g.us"
                ),
                main._procesar_equipo_async(
                    miembro, msg_2, responder_a="equipo@g.us"
                ),
            )

        self.assertEqual(max_active, 1)
        self.assertEqual(
            order,
            ["start:msg-1", "end:msg-1", "start:msg-2", "end:msg-2"],
        )

    def test_group_uses_shared_chat_key(self):
        fabio = SimpleNamespace(numero_whatsapp="+573001112233")
        edgardo = SimpleNamespace(numero_whatsapp="+573004445566")

        self.assertEqual(
            main._equipo_chat_key(fabio, "equipo@g.us"),
            main._equipo_chat_key(edgardo, "equipo@g.us"),
        )
        self.assertNotEqual(
            main._equipo_chat_key(fabio),
            main._equipo_chat_key(edgardo),
        )
