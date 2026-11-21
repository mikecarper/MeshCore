"""Scan two cold boots of the Zephyr fixture with Bumble 0.0.236."""
import asyncio
from collections import Counter
from pathlib import Path
import sys
import tempfile

from bumble import hci
from bumble.controller import Controller
from bumble.core import AdvertisingData
from bumble.device import Device
from bumble.link import LocalLink
from bumble.transport.common import AsyncPipeSink, StreamPacketSink, StreamPacketSource


NAMES = {"A": "RAK-A-Apple", "B": "RAK-B-Android", "C": "RAK-C-Sidewalk"}


class TagController(Controller):
    """Record radio starts and stops, and fail B once during a group start."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rejected = False
        self.changes = []

    def on_hci_le_set_extended_advertising_enable_command(self, command):
        handles = command.advertising_handles or list(self.advertising_sets)
        for handle in handles:
            payload = AdvertisingData.from_bytes(bytes(self.advertising_sets[handle].data))
            name = payload.get(AdvertisingData.COMPLETE_LOCAL_NAME)
            if command.enable and name == NAMES["B"] and not self.rejected:
                self.rejected = True
                return hci.HCI_StatusReturnParameters(hci.HCI_ErrorCode.COMMAND_DISALLOWED_ERROR)
            self.changes.append((bool(command.enable), name))
        return super().on_hci_le_set_extended_advertising_enable_command(command)


async def scan_boot(executable, flash, seed):
    loop = asyncio.get_running_loop()
    link = LocalLink()
    tag = TagController("tag", link=link, public_address="12:34:56:78:9A:01")
    observer = Controller("scanner", link=link, public_address="12:34:56:78:9A:02")
    scanner = Device.with_hci("scanner", hci.Address("F0:00:00:00:00:02"), observer, AsyncPipeSink(observer))
    reports = []
    markers = []
    parameters = []

    @scanner.on("advertisement")
    def received(report):
        name = report.data.get(AdvertisingData.COMPLETE_LOCAL_NAME)
        if name:
            reports.append((loop.time(), name, report.address))

    class ControllerPort(StreamPacketSource):
        def connection_made(self, transport):
            self.set_packet_sink(tag)
            tag.set_packet_sink(StreamPacketSink(transport))

    await scanner.power_on()
    await scanner.start_scanning(active=False, filter_duplicates=False)
    async with await loop.create_server(ControllerPort, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        proc = await asyncio.create_subprocess_exec(
            "stdbuf", "-oL", executable, f"-bt-dev=127.0.0.1:{port}",
            f"-flash={flash}", f"-seed={seed}", "-rt", "-stop_at=25",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        output = []
        try:
            async with asyncio.timeout(35):
                async for line in proc.stdout:
                    message = line.decode(errors="replace").strip()
                    output.append(message)
                    if message.startswith("scan "):
                        markers.append((message, loop.time()))
                        if message != "scan end":
                            active_sets = [item for item in tag.advertising_sets.values() if item.enabled]
                            parameters.append([item.parameters.advertising_event_properties for item in active_sets])
                await proc.wait()
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            await scanner.stop_scanning()
        assert proc.returncode == 0 and "BLE command checks passed" in output, "\n".join(output)

    assert tag.rejected and not tag.advertising_sets
    properties = hci.HCI_LE_Set_Extended_Advertising_Parameters_Command.AdvertisingProperties
    expected_windows = ("C", "BC", "ABC", "AC", "A", "AB", "B", "none")
    assert len(markers) == len(expected_windows) * 2
    identities = {}
    for index, slots in enumerate(expected_windows):
        (label, start), (end_label, end) = markers[index * 2:index * 2 + 2]
        assert (label, end_label) == (f"scan {slots}", "scan end")
        expected = set() if slots == "none" else {NAMES[slot] for slot in slots}
        assert parameters[index] == [properties.USE_LEGACY_ADVERTISING_PDUS] * len(expected)
        counts = Counter()
        for t, name, address in reports:
            if start + 0.1 < t < end - 0.1:
                assert address.is_static and identities.setdefault(name, address) == address
                counts[name] += 1
        assert set(counts) == expected and all(n >= 3 for n in counts.values()), (slots, counts)
        print(f"Boot {seed}, active {slots}: {dict(counts)}")
    # Check continuity through the commands too, including rollback and idempotent calls.
    changes = ["+C", "+A", "-A", "+B", "+A", "-B", "-C", "+B", "-A", "-B"]
    assert tag.changes == [(action == "+", NAMES[slot]) for action, slot in changes], tag.changes
    assert len(set(identities.values())) == 3
    return identities


async def main(executable):
    with tempfile.TemporaryDirectory() as temp:
        flash = Path(temp) / "flash.bin"
        first = await scan_boot(executable, flash, 1)
        second = await scan_boot(executable, flash, 2)
    assert first == second, (first, second)
    for name, address in sorted(first.items()):
        print(f"{name}  {address}  same after reboot")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]).resolve()))
