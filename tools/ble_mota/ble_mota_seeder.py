#!/usr/bin/env python3
"""Reference BLE controller/seeder for an nRF52 Full Companion.

This is primarily for Linux testing (including a Raspberry Pi Zero). A phone
app can implement the same documented GATT and Companion frames.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import shutil
import signal
import struct
import subprocess
import sys
from pathlib import Path

BLEAK_IMPORT_ERROR: ImportError | None = None
try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
except ImportError as exc:  # pragma: no cover - depends on the test host
    BLEAK_IMPORT_ERROR = exc
    BleakClient = None  # type: ignore[assignment,misc]
    BleakScanner = None  # type: ignore[assignment,misc]

    class BleakError(Exception):
        pass


NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # host -> Companion
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Companion -> host
MOTA_REQUEST_UUID = "2bfaa1ee-7030-459a-b65a-e7cfd5b09735"
MOTA_RESPONSE_UUID = "acf38a51-dd58-4dce-917f-0b1135e41b1a"

CMD_EXEC_LOCAL_OTA_CONTROL = 0x4A
CMD_BLE_MOTA_SOURCE = 0x4B
MOTA_ACTION_STATUS = 0
MOTA_ACTION_START = 1
MOTA_ACTION_STOP = 2

RESP_OK = 0
RESP_ERR = 1
MOTA_FLAG_CHANNEL_READY = 0x01
MOTA_FLAG_ATTACHED = 0x02
MOTA_FLAG_ANOTHER_LINK_ACTIVE = 0x04

OP_COUNT = 0x01
OP_DESCRIBE = 0x02
OP_READ = 0x03
STATUS_OK = 0
STATUS_ERR = 1
MOTA_READ_MAX = 192
MOTA_DESC_WIRE = 38
MOTA_HEADER_LEN = 8
MOTA_MANIFEST_LEN = 197
MOTA_TRAILER = b"vk496"


def xor_bytes(data: bytes, seed: int = 0) -> int:
    result = seed
    for value in data:
        result ^= value
    return result


@dataclasses.dataclass(frozen=True)
class MotaFile:
    path: Path
    size: int
    descriptor: bytes

    @staticmethod
    def load(path: Path) -> "MotaFile":
        size = path.stat().st_size
        if size > 0xFFFFFFFF:
            raise ValueError("container is too large for the mOTA protocol")
        if size < MOTA_HEADER_LEN + MOTA_MANIFEST_LEN + len(MOTA_TRAILER):
            raise ValueError("container is too short")
        with path.open("rb") as stream:
            header = stream.read(MOTA_HEADER_LEN)
            manifest = stream.read(MOTA_MANIFEST_LEN)
            stream.seek(-len(MOTA_TRAILER), 2)
            trailer = stream.read(len(MOTA_TRAILER))
        if header[:4] != b"mOTA":
            raise ValueError("bad mOTA magic")
        if struct.unpack_from("<I", header, 4)[0] != size:
            raise ValueError("declared size does not match file size")
        if trailer != MOTA_TRAILER:
            raise ValueError("bad mOTA trailer")

        flags = manifest[1]
        target_id = struct.unpack_from("<I", manifest, 3)[0]
        firmware_version = struct.unpack_from("<I", manifest, 7)[0]
        payload_size = struct.unpack_from("<I", manifest, 15)[0]
        block_size_log2 = manifest[19]
        if not 1 <= block_size_log2 <= 24 or payload_size == 0:
            raise ValueError("bad block geometry")
        block_size = 1 << block_size_log2
        block_count = (payload_size + block_size - 1) // block_size
        leaves_offset = MOTA_HEADER_LEN + MOTA_MANIFEST_LEN
        payload_offset = leaves_offset + block_count * 4
        if payload_offset + payload_size + len(MOTA_TRAILER) != size:
            raise ValueError("container geometry does not match file size")

        descriptor = bytearray(MOTA_DESC_WIRE)
        descriptor[0:4] = manifest[20:24]
        struct.pack_into("<I", descriptor, 4, target_id)
        struct.pack_into("<I", descriptor, 8, firmware_version)
        descriptor[12] = manifest[56]
        descriptor[13] = flags
        struct.pack_into("<I", descriptor, 14, size)
        struct.pack_into("<I", descriptor, 18, leaves_offset)
        struct.pack_into("<I", descriptor, 22, block_count)
        struct.pack_into("<I", descriptor, 26, payload_offset)
        struct.pack_into("<I", descriptor, 30, payload_size)
        descriptor[34] = block_size_log2
        return MotaFile(path=path, size=size, descriptor=bytes(descriptor))


class Catalog:
    def __init__(self, files: list[MotaFile], verbose: bool) -> None:
        self.files = files
        self.verbose = verbose

    @staticmethod
    def scan(directory: Path, recursive: bool, motatool: str,
             verbose: bool) -> "Catalog":
        pattern = "**/*.mota" if recursive else "*.mota"
        paths = sorted(path for path in directory.glob(pattern) if path.is_file())
        if not paths:
            raise ValueError(f"no .mota files found under {directory}")
        if len(paths) > 255:
            raise ValueError("the Bluetooth mOTA catalog is limited to 255 files")

        executable = shutil.which(motatool)
        if executable is None:
            raise ValueError(
                f"{motatool!r} was not found; install motatool before serving"
            )
        files: list[MotaFile] = []
        for path in paths:
            verified = subprocess.run(
                [executable, "verify", str(path)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if verified.returncode != 0:
                raise ValueError(
                    f"motatool verification failed for {path}:\n"
                    + verified.stdout
                )
            try:
                files.append(MotaFile.load(path))
            except (OSError, ValueError) as exc:
                raise ValueError(f"cannot serve {path}: {exc}") from exc
        return Catalog(files, verbose)

    def handle_request(self, frame: bytes) -> bytes | None:
        if len(frame) < 4 or frame[:2] != b"MS":
            return None
        op = frame[2]
        args = frame[3:-1]
        if frame[-1] != xor_bytes(args, op):
            return None

        status = STATUS_ERR
        payload = b""
        if op == OP_COUNT and not args:
            status = STATUS_OK
            payload = bytes([len(self.files)])
        elif op == OP_DESCRIBE and len(args) == 1:
            index = args[0]
            if index < len(self.files):
                status = STATUS_OK
                payload = self.files[index].descriptor
        elif op == OP_READ and len(args) == 7:
            index = args[0]
            offset = struct.unpack_from("<I", args, 1)[0]
            length = struct.unpack_from("<H", args, 5)[0]
            if index < len(self.files) and length <= MOTA_READ_MAX:
                mota = self.files[index]
                end = offset + length
                if end >= offset and end <= mota.size:
                    try:
                        if mota.path.stat().st_size == mota.size:
                            with mota.path.open("rb") as stream:
                                stream.seek(offset)
                                payload = stream.read(length)
                            if len(payload) == length:
                                status = STATUS_OK
                    except OSError:
                        payload = b""

        response = bytearray(b"ms")
        response.extend((op, status))
        response.extend(payload)
        response.append(xor_bytes(response))
        if self.verbose:
            if op == OP_COUNT:
                detail = f"count={len(self.files)}"
            elif op == OP_DESCRIBE and args:
                detail = f"index={args[0]}"
            elif op == OP_READ and len(args) == 7:
                detail = f"index={args[0]} offset={struct.unpack_from('<I', args, 1)[0]}"
            else:
                detail = "invalid"
            print(f"mOTA op=0x{op:02x} {detail} status={status}")
        return bytes(response)


class BleSession:
    def __init__(self, client: BleakClient, catalog: Catalog) -> None:
        self.client = client
        self.catalog = catalog
        self.mota_requests: asyncio.Queue[bytes] = asyncio.Queue()
        self.companion_chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self.mota_response_chunk_size = 20

    async def negotiate_mtu(self) -> None:
        """Use the negotiated ATT payload when the backend can expose it.

        BlueZ otherwise reports Bleak's conservative 23-byte default and each
        192-byte mOTA read takes ten acknowledged writes. Its backend provides
        the acquisition hook referenced by Bleak's own warning. Other backends
        already expose ``mtu_size`` directly. Failure is only a performance
        issue, so retain the universally safe 20-byte ATT payload fallback.
        """
        mtu_size = 23
        backend = getattr(self.client, "_backend", None)
        acquire_mtu = getattr(backend, "_acquire_mtu", None)
        if callable(acquire_mtu):
            try:
                await acquire_mtu()
                candidate = getattr(backend, "_mtu_size", None)
                if isinstance(candidate, int) and candidate >= 23:
                    mtu_size = candidate
            except Exception:
                pass
        else:
            try:
                candidate = self.client.mtu_size
                if isinstance(candidate, int) and candidate >= 23:
                    mtu_size = candidate
            except Exception:
                pass

        self.mota_response_chunk_size = min(
            MOTA_READ_MAX + 5, mtu_size - 3
        )
        if self.catalog.verbose:
            print(
                f"BLE MTU {mtu_size}; mOTA response chunks "
                f"{self.mota_response_chunk_size} bytes"
            )

    def on_mota_request(self, _characteristic: object, data: bytearray) -> None:
        self.mota_requests.put_nowait(bytes(data))

    def on_companion_data(self, _characteristic: object,
                          data: bytearray) -> None:
        self.companion_chunks.put_nowait(bytes(data))

    async def serve(self, stop: asyncio.Event) -> None:
        while not stop.is_set() and self.client.is_connected:
            try:
                request = await asyncio.wait_for(self.mota_requests.get(), 0.5)
            except asyncio.TimeoutError:
                continue
            response = self.catalog.handle_request(request)
            if response is None:
                continue
            chunk_size = self.mota_response_chunk_size
            for offset in range(0, len(response), chunk_size):
                await self.client.write_gatt_char(
                    MOTA_RESPONSE_UUID,
                    response[offset : offset + chunk_size],
                    response=True,
                )

    async def companion_command(self, frame: bytes, expected_length,
                                timeout: float = 8.0) -> bytes:
        while not self.companion_chunks.empty():
            self.companion_chunks.get_nowait()
        await self.client.write_gatt_char(NUS_RX_UUID, frame, response=True)

        deadline = asyncio.get_running_loop().time() + timeout
        result = bytearray()
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            chunk = await asyncio.wait_for(
                self.companion_chunks.get(), remaining
            )
            # Companion push frames have their own atomic BLE notification and
            # use codes 0x80-0xff. They can arrive at any time, including while
            # a command reply is pending, so do not splice one into the reply.
            if not result and chunk and chunk[0] >= 0x80:
                continue
            result.extend(chunk)
            wanted = expected_length(result)
            if wanted is None:
                continue
            if len(result) != wanted:
                raise RuntimeError(
                    f"malformed Companion response length {len(result)}/{wanted}"
                )
            return bytes(result)

    async def local_control(self, command: str) -> str:
        encoded = command.encode("ascii")
        if not 1 <= len(encoded) <= 174:
            raise ValueError("local command must be 1-174 ASCII bytes")
        response = await self.companion_command(
            bytes([CMD_EXEC_LOCAL_OTA_CONTROL]) + encoded,
            lambda data: (
                2 if data and data[0] == RESP_ERR
                else 2 + data[1] if len(data) >= 2 and data[0] == RESP_OK
                else 1 if data and data[0] not in (RESP_OK, RESP_ERR)
                else None
            ),
        )
        if not response:
            raise RuntimeError("empty Companion response")
        if response[0] == RESP_ERR:
            code = response[1] if len(response) > 1 else -1
            raise RuntimeError(f"Companion rejected local command (error {code})")
        if response[0] != RESP_OK:
            raise RuntimeError(f"unexpected Companion response 0x{response[0]:02x}")
        return response[2:].decode("ascii", errors="replace")

    async def source_action(
        self, action: int
    ) -> tuple[int, int, int, int | None]:
        response = await self.companion_command(
            bytes([CMD_BLE_MOTA_SOURCE, action]),
            lambda data: (
                2 if data and data[0] == RESP_ERR
                else 11 if len(data) > 7 and data[0] == RESP_OK
                else 7 if len(data) == 7 and data[0] == RESP_OK
                else None if data and data[0] == RESP_OK
                else 1 if data
                else None
            ),
            timeout=30.0,
        )
        return parse_source_status(response, action)


def parse_source_status(
    response: bytes, expected_action: int
) -> tuple[int, int, int, int | None]:
    if not response:
        raise RuntimeError("empty Companion response")
    if response[0] == RESP_ERR:
        code = response[1] if len(response) > 1 else -1
        raise RuntimeError(f"BLE mOTA source action failed (error {code})")
    if (
        len(response) not in (7, 11)
        or response[0] != RESP_OK
        or response[1] != expected_action
    ):
        raise RuntimeError(f"malformed source status: {response.hex()}")
    flags = response[2]
    offered = struct.unpack_from("<H", response, 3)[0]
    advertised = struct.unpack_from("<H", response, 5)[0]
    packets_sent = (
        struct.unpack_from("<I", response, 7)[0]
        if len(response) == 11
        else None
    )
    return flags, offered, advertised, packets_sent


async def resolve_device(device: str):
    wanted = device.casefold()
    found = await BleakScanner.find_device_by_filter(
        lambda candidate, _advertisement: (
            candidate.address.casefold() == wanted
            or (candidate.name is not None and candidate.name == device)
        ),
        timeout=10.0,
    )
    if found is None:
        raise RuntimeError(f"Bluetooth device {device!r} was not found")
    return found


def describe_status(
    flags: int,
    offered: int,
    advertised: int,
    packets_sent: int | None,
) -> str:
    states = [
        "channel-ready" if flags & MOTA_FLAG_CHANNEL_READY else "channel-not-ready",
        "attached" if flags & MOTA_FLAG_ATTACHED else "detached",
    ]
    if flags & MOTA_FLAG_ANOTHER_LINK_ACTIVE:
        states.append("another-source-link-active")
    packet_detail = (
        f"; {packets_sent} LoRa packets sent"
        if packets_sent is not None
        else "; LoRa packet count unavailable (legacy firmware)"
    )
    return (
        f"{', '.join(states)}; advertising {advertised}/{offered} files"
        f"{packet_detail}"
    )


async def run(args: argparse.Namespace) -> None:
    if BLEAK_IMPORT_ERROR is not None:
        raise RuntimeError(
            "bleak is required; install it in an isolated environment, for "
            "example with 'pipx runpip <environment> install bleak'"
        ) from BLEAK_IMPORT_ERROR
    if args.directory is not None:
        catalog = Catalog.scan(
            args.directory, args.recursive, args.motatool, args.verbose
        )
    else:
        catalog = Catalog([], args.verbose)

    device = await resolve_device(args.device)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            pass

    def disconnected(_client: BleakClient) -> None:
        loop.call_soon_threadsafe(stop.set)

    async with BleakClient(
        device, disconnected_callback=disconnected, pair=args.pair
    ) as client:
        session = BleSession(client, catalog)
        await session.negotiate_mtu()
        await client.start_notify(NUS_TX_UUID, session.on_companion_data)
        needs_mota_channel = (
            args.directory is not None or args.source == "start"
        )
        serve_task: asyncio.Task[None] | None = None
        if needs_mota_channel:
            await client.start_notify(MOTA_REQUEST_UUID, session.on_mota_request)
            serve_task = asyncio.create_task(session.serve(stop))
        source_started = False
        temp_radio_requested = False
        try:
            for command in args.local:
                print(await session.local_control(command))
                if command.startswith("tempradio "):
                    temp_radio_requested = True

            action = args.source
            if action is None and args.directory is not None:
                action = "start"
            if action is not None:
                action_code = {
                    "status": MOTA_ACTION_STATUS,
                    "start": MOTA_ACTION_START,
                    "stop": MOTA_ACTION_STOP,
                }[action]
                flags, offered, advertised, packets_sent = (
                    await session.source_action(action_code)
                )
                print(describe_status(
                    flags, offered, advertised, packets_sent
                ))
                source_started = action == "start" and bool(
                    flags & MOTA_FLAG_ATTACHED
                )

            if source_started:
                if args.seconds > 0:
                    try:
                        await asyncio.wait_for(stop.wait(), args.seconds)
                    except asyncio.TimeoutError:
                        pass
                else:
                    print("Serving over Bluetooth; press Ctrl-C to stop")
                    await stop.wait()
        finally:
            if client.is_connected and source_started:
                try:
                    flags, offered, advertised, packets_sent = (
                        await session.source_action(MOTA_ACTION_STOP)
                    )
                    print(describe_status(
                        flags, offered, advertised, packets_sent
                    ))
                except Exception as exc:  # best effort during disconnect
                    print(f"warning: could not stop BLE source cleanly: {exc}",
                          file=sys.stderr)
            if (client.is_connected and temp_radio_requested
                    and not args.leave_temp_radio):
                try:
                    print(await session.local_control("normalradio"))
                except Exception as exc:  # best effort during disconnect
                    print(f"warning: could not restore normal radio: {exc}",
                          file=sys.stderr)
            stop.set()
            if serve_task is not None:
                await serve_task


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Control and serve LoRa mOTA files through an nRF52 Full "
            "Companion's encrypted Bluetooth link"
        )
    )
    parser.add_argument(
        "--device", required=True,
        help="BLE address or exact advertised MeshCore device name",
    )
    parser.add_argument(
        "--dir", dest="directory", type=Path,
        help="folder of verified .mota files to serve",
    )
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--motatool", default="motatool")
    parser.add_argument(
        "--local", action="append", default=[], metavar="COMMAND",
        help="run an allowed tempradio, normalradio, or ota command",
    )
    parser.add_argument(
        "--source", choices=("status", "start", "stop"),
        help="query or change BLE source state; --dir defaults to start",
    )
    parser.add_argument(
        "--seconds", type=float, default=0,
        help="stop serving after this many seconds (default: until Ctrl-C)",
    )
    parser.add_argument(
        "--pair", action="store_true",
        help="request pairing before accessing the MITM-protected service",
    )
    parser.add_argument(
        "--leave-temp-radio", action="store_true",
        help="do not send normalradio when this process exits",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.seconds < 0:
        raise SystemExit("--seconds cannot be negative")
    if args.source == "start" and args.directory is None:
        raise SystemExit("--source start requires --dir")
    try:
        asyncio.run(run(args))
    except (BleakError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
