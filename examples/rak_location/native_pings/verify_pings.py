#!/usr/bin/env python3
"""Run the three native ARM beacon paths against one virtual controller and passive scanner."""
import argparse
import asyncio
from collections import defaultdict
from pathlib import Path
import selectors
import struct
import sys

from bumble import hci
from bumble.controller import Controller
from bumble.device import AdvertisingEventProperties, AdvertisingParameters, Device
from bumble.link import LocalLink
from bumble.transport.common import AsyncPipeSink

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from verify_boot import boot

SLOTS = {3: "A", 2: "B", 1: "C"}


def sdk_advertisements(elf, interval=None):
    image = boot(elf, None, interval=interval, test_commands=False)
    # All memory, credentials, addresses and controllers used by this test are local fixtures.
    base, scratch, response = 0x20038000, 0x20039000, 0x20039200
    image.cpu.mem_write(scratch, bytes(128))
    sets = {}

    def create():
        param = image.arg(0)
        identity = image.cpu.mem_read(param, 1)[0]
        assert image.word(param + 8) == image.word(param + 12)
        slot = next(i for i in range(4) if base + 32 * i not in sets)
        adv = base + 32 * slot
        sets[adv] = dict(identity=identity, options=image.word(param + 4),
                         interval=image.word(param + 8) * 0.625, enabled=False)
        image.put_word(image.arg(2), adv)
        image.ret(0)

    def fields(ptr, count):
        encoded = bytearray()
        for i in range(count):
            kind, size, data = struct.unpack("<BB2xI", image.cpu.mem_read(ptr + i * 8, 8))
            encoded.extend((size + 1, kind))
            encoded.extend(image.cpu.mem_read(data, size))
        return bytes(encoded)

    def set_data():
        item = sets[image.arg(0)]
        item["ad"] = fields(image.arg(1), image.arg(2))
        item["sd"] = fields(image.arg(3), image.arg(4))
        image.ret(0)

    def start():
        item = sets[image.arg(0)]
        assert item["ad"] and not item["enabled"]
        item["enabled"] = True
        image.ret(0)

    def delete():
        del sets[image.arg(0)]
        image.ret(0)

    def command():
        # Both vendor SDKs set advertising TX power through the controller.
        assert image.arg(0) == 0xfc0e
        image.put_word(image.arg(2), response)
        image.put_word(response + 12, response + 64)
        image.ret(0)

    image.hook("bt_le_ext_adv_create", create)
    image.hook("bt_le_ext_adv_set_data", set_data)
    image.hook("bt_le_ext_adv_start", start)
    image.hook("bt_le_ext_adv_delete", delete)
    image.hook("bt_le_ext_adv_get_index", lambda: image.ret((image.arg(0) - base) // 32))
    image.hook("bt_id_reset", lambda: image.ret(image.arg(0)))
    image.hook("bt_hci_get_adv_handle", lambda: image.ret(0))
    image.hook("bt_hci_cmd_create", lambda: image.ret(response + 128))
    image.hook("bt_hci_cmd_alloc", lambda: image.ret(response + 128))
    image.hook("net_buf_simple_add", lambda: image.ret(response + 192))
    image.hook("bt_hci_cmd_send_sync", command)
    image.hook("net_buf_unref", lambda: image.ret(0))
    image.hook("k_work_reschedule", lambda: image.ret(1))
    image.hook("k_work_submit", lambda: image.ret(1))

    # Pairing and fast reconnect remain discoverable at their SDK intervals.
    for identity in (2, 3):
        p, out = scratch + 128, scratch + 192
        image.cpu.mem_write(p, struct.pack("<B3xIII16x", identity, 3, 48, 48))
        assert image.call("__wrap_bt_le_ext_adv_create", p, 0, out) == 0
        adv = image.word(out)
        assert sets[adv]["interval"] == 30  # milliseconds
        assert image.call("bt_le_ext_adv_delete", adv) == 0

    # Enter the vendor advertising paths with empty synthetic fixture data.
    # Pairing, clock acquisition and gateway registration are outside this radio test.
    assert image.call("fmna_adv_init", 3) == 0
    assert image.call("fmna_adv_start_separated", scratch) == 0
    # Run the actual locator application's configuration of the FHN identity
    # and interval. Stop before the later enrollment/UI callbacks.
    for name in ("app_ui_init", "bt_conn_auth_cb_register", "app_battery_init", "app_ring_init",
                 "bt_fast_pair_adv_manager_id_set", "bt_fast_pair_adv_manager_info_cb_register"):
        image.hook(name, lambda: image.ret(0))
    image.hook("bt_fast_pair_fhn_info_cb_register", lambda: image.ret(1))
    image.call("init_work_handle", 0)
    assert image.call("fp_fhn_state_init") == 0
    image.call("fhn_state_battery_level_changed")
    assert image.call("fhn_adv_start") == 0
    assert image.call("sid_ble_advert_update", scratch + 80, 8) == 0
    assert image.call("sid_ble_advert_start") == 0

    assert len(sets) == 3 and all(item["enabled"] for item in sets.values())
    assert {item["identity"] for item in sets.values()} == SLOTS.keys()
    assert all(item["interval"] == (interval or 10) * 1000 for item in sets.values())
    for counter in ("accepted_updates", "accepted_starts"):
        assert [image.word(image.symbols[counter] + i * 4) for i in range(3)] == [1, 1, 1], counter
    # Query the firmware's USB status with the same host state. Then end only
    # C's advertising in the controller, as happens on timeout or connection.
    status = {}
    def get_info():
        item = sets[image.arg(0)]
        info = image.arg(1)
        image.cpu.mem_write(info, bytes((item["identity"],)))
        image.cpu.mem_write(info + 8, bytes((int(item["enabled"]),)))
        image.ret(0)

    def show():
        slot = bytes(image.cpu.mem_read(image.arg(2), 1)).decode()
        state = bytes(image.cpu.mem_read(image.arg(3), 4)).split(b"\0")[0]
        status[slot] = (state, image.arg(4), image.arg(5))
        image.ret(0)

    image.hook("bt_le_ext_adv_get_info", get_info)
    image.hook("shell_fprintf_normal", show)
    assert image.call("ping_status", 0, 1, 0) == 0
    assert status == dict.fromkeys("ABC", (b"on", 1, 1))
    c = next(item for item in sets.values() if item["identity"] == 1)
    c["enabled"] = False
    assert image.call("ping_status", 0, 1, 0) == 0
    assert status == {"A": (b"on", 1, 1), "B": (b"on", 1, 1), "C": (b"off", 1, 1)}
    c["enabled"] = True
    return {SLOTS[item["identity"]]: item for item in sets.values()}


def identify(encoded):
    fields = []
    while encoded:
        size = encoded[0]
        assert 1 <= size < len(encoded), f"invalid field length {size} in {len(encoded)} bytes"
        fields.append((encoded[1], encoded[2:1 + size]))
        encoded = encoded[1 + size:]
    for kind, data in fields:
        if kind == 0xff and len(data) == 29 and data.startswith(b"\x4c\x00\x12\x19"):
            return "A"
        if kind == 0x16 and len(data) == 24 and data[:3] in (b"\xaa\xfe\x40", b"\xaa\xfe\x41"):
            return "B"
    if (3, b"\x03\xfe") in fields and any(k == 0xff and d.startswith(b"\x71\x01") for k, d in fields):
        return "C"
    raise AssertionError("Unexpected advertising payload")


async def scan(sets, seconds):
    # In-memory LE link only. This test has no hardware or external transport option.
    link = LocalLink()
    def device(name, address):
        controller = Controller(name, public_address=address, link=link)
        return Device.with_hci(name, hci.Address(address), controller, AsyncPipeSink(controller))
    tag = device("tag", "F0:00:00:00:00:01")
    observer = device("observer", "F0:00:00:00:00:02")
    await tag.power_on()
    await observer.power_on()
    times = defaultdict(list)
    clock = asyncio.get_running_loop().time
    addresses = {}
    unexpected = []

    @observer.host.on("advertising_report")
    def heard(report):
        if report.event_type & hci.HCI_LE_Extended_Advertising_Report_Event.EventType.SCAN_RESPONSE:
            return
        encoded = bytes(report.data)
        try:
            slot = identify(encoded)
            assert encoded == sets[slot]["ad"], f"{slot} length {len(encoded)} expected {len(sets[slot]['ad'])}"
            assert addresses.setdefault(slot, report.address) == report.address, f"{slot} address changed"
            times[slot].append(clock())
        except Exception as exc:
            unexpected.append(exc)

    await observer.start_scanning(active=False, filter_duplicates=False)
    handles = []
    try:
        for slot, item in sets.items():
            assert identify(item["ad"]) == slot
            assert item["options"] & 3 == 3 and not item["options"] & (1 << 10)
            assert item["interval"] == seconds * 1000
            params = AdvertisingParameters(
                advertising_event_properties=AdvertisingEventProperties(is_legacy=True, is_connectable=True,
                                                                         is_scannable=True),
                primary_advertising_interval_min=item["interval"],
                primary_advertising_interval_max=item["interval"])
            handles.append(await tag.create_advertising_set(
                advertising_parameters=params, random_address=hci.Address(f"F1:00:00:00:00:0{item['identity']}"),
                advertising_data=item["ad"], scan_response_data=item["sd"]))
        async with asyncio.timeout(2 * seconds + 3):
            while any(len(times[slot]) < 3 for slot in "ABC") and not unexpected:
                await asyncio.sleep(0.1)
        for slot in "ABC":
            gaps = [b - a for a, b in zip(times[slot], times[slot][1:])]
            assert len(gaps) >= 2 and all(abs(gap - seconds) < 0.5 for gap in gaps), (slot, gaps)
        assert not unexpected, unexpected
        assert len(set(addresses.values())) == 3
        print(f"PASS {seconds} s, passive scanner, three distinct addresses; observed gaps in seconds:",
              {slot: [round(b - a, 3) for a, b in zip(ts, ts[1:])] for slot, ts in sorted(times.items())})
    finally:
        for handle in handles:
            await handle.stop()
            await handle.remove()
        await observer.stop_scanning()


def virtual_scan_loop():
    """Advance to actual asyncio timer deadlines on an in-memory Bluetooth link."""
    class VirtualSelector(selectors.DefaultSelector):
        now = 0.0

        def select(self, timeout=None):
            ready = super().select(0)
            if not ready and timeout is not None:
                self.now += timeout
            return ready

    selector = VirtualSelector()
    loop = asyncio.SelectorEventLoop(selector)
    loop.time = lambda: selector.now
    return loop


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    parser.add_argument("--interval", type=int, help="Saved interval in seconds (default tests a fresh tag)")
    parser.add_argument("--virtual-time", action="store_true", help="Advance the in-memory scanner clock without waiting")
    args = parser.parse_args()
    if args.interval is not None and not 1 <= args.interval <= 300:
        parser.error("interval must be between 1 and 300 seconds")
    sets = sdk_advertisements(args.elf, args.interval)
    with asyncio.Runner(loop_factory=virtual_scan_loop if args.virtual_time else None) as runner:
        runner.run(scan(sets, args.interval or 10))
