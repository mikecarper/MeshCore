#!/usr/bin/env python3
"""Local, single-client WebSocket bridge to a Wi-Fi Companion's TCP port.

The browser never chooses the TCP target: the operator supplies it on the
command line. A fresh capability URL and an origin check limit which page can
attach. Data is forwarded unchanged and is not logged.
"""
import argparse
import asyncio
import ipaddress
import secrets
import sys
from urllib.parse import urlsplit

try:
    from websockets.asyncio.server import serve
    from websockets.exceptions import ConnectionClosed
except ImportError:
    sys.exit("Install the bridge dependency: python -m pip install 'websockets>=13,<18'")

ORIGIN = "https://mikecarper.github.io"
MAX_MESSAGE = 65536


class Bridge:
    def __init__(self, host, port, token):
        self.host, self.port, self.path = host, port, "/" + token
        self.active = False

    async def handle(self, websocket):
        if not secrets.compare_digest(websocket.request.path, self.path):
            await websocket.close(1008, "Invalid bridge address")
            return
        if self.active:
            await websocket.close(1013, "Bridge already in use")
            return
        self.active = True
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=10)

            async def to_radio():
                async for message in websocket:
                    if not isinstance(message, bytes):
                        await websocket.close(1003, "Binary messages required")
                        return
                    writer.write(message)
                    await asyncio.wait_for(writer.drain(), timeout=15)

            async def to_browser():
                while data := await reader.read(4096):
                    await websocket.send(data)

            tasks = [asyncio.create_task(to_radio()), asyncio.create_task(to_browser())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, asyncio.TimeoutError, ConnectionClosed):
            pass
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
            await websocket.close()
            self.active = False


async def run(host, port, listen_port, local_page_origin=None):
    token = secrets.token_urlsafe(32)
    bridge = Bridge(host, port, token)
    origins = [ORIGIN] + ([local_page_origin] if local_page_origin else [])
    async with serve(bridge.handle, "127.0.0.1", listen_port, origins=origins,
                     max_size=MAX_MESSAGE, max_queue=16, compression=None):
        print("Paste this address into the browser updater:", flush=True)
        print(f"ws://127.0.0.1:{listen_port}/{token}", flush=True)
        print("Keep this window open while updating. Press Ctrl+C to stop.", flush=True)
        await asyncio.Future()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="Wi-Fi Companion IPv4 address")
    parser.add_argument("--port", type=int, default=5000, help="Companion TCP port (default 5000)")
    parser.add_argument("--local-page-origin", help="Allow one local development page origin, e.g. http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        address = ipaddress.IPv4Address(args.host)
        if address.is_unspecified or address.is_multicast or address.is_loopback:
            raise ValueError()
    except ValueError:
        parser.error("host must be a unicast IPv4 address of the Wi-Fi Companion")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.local_page_origin:
        parsed = urlsplit(args.local_page_origin)
        if (parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost")
                or not parsed.port or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment):
            parser.error("local page origin must use http(s)://127.0.0.1:PORT or localhost:PORT")
    try:
        asyncio.run(run(str(address), args.port, 8765, args.local_page_origin))
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        sys.exit("Could not start local bridge: " + str(exc))


if __name__ == "__main__":
    main()
