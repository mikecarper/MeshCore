import asyncio
import importlib.util
from pathlib import Path
import unittest

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, InvalidStatus

spec = importlib.util.spec_from_file_location("browser_wifi_bridge", Path(__file__).with_name("browser_wifi_bridge.py"))
bridge_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge_module)


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connections = 0

        async def radio(reader, writer):
            self.connections += 1
            try:
                while data := await reader.read(4096):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        self.radio = await asyncio.start_server(radio, "127.0.0.1", 0)
        target = self.radio.sockets[0].getsockname()[1]
        self.bridge = bridge_module.Bridge("127.0.0.1", target, "test-capability")
        self.server = await serve(self.bridge.handle, "127.0.0.1", 0,
                                  origins=[bridge_module.ORIGIN], compression=None)
        self.port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}/test-capability"

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.radio.close()
        await self.radio.wait_closed()

    async def test_binary_forwarding_and_reconnect(self):
        for payload in (bytes(range(256)), b'\0\x3c\xff\x0a'):
            async with connect(self.url, origin=bridge_module.ORIGIN) as socket:
                await socket.send(payload)
                self.assertEqual(await asyncio.wait_for(socket.recv(), 2), payload)
            for _ in range(100):
                if not self.bridge.active:
                    break
                await asyncio.sleep(.01)
        self.assertEqual(self.connections, 2)

    async def test_bad_origin_and_capability_never_open_radio(self):
        with self.assertRaises(InvalidStatus):
            async with connect(self.url, origin="https://evil.example"):
                pass
        async with connect(self.url + "wrong", origin=bridge_module.ORIGIN) as socket:
            with self.assertRaises(ConnectionClosed):
                await socket.recv()
        self.assertEqual(self.connections, 0)

    async def test_single_client_and_binary_only(self):
        async with connect(self.url, origin=bridge_module.ORIGIN) as first:
            await first.send(b'x')
            self.assertEqual(await first.recv(), b'x')
            async with connect(self.url, origin=bridge_module.ORIGIN) as second:
                with self.assertRaises(ConnectionClosed):
                    await second.recv()
            self.assertEqual(self.connections, 1)
            await first.send('not binary')
            with self.assertRaises(ConnectionClosed):
                await first.recv()


if __name__ == "__main__":
    unittest.main()
