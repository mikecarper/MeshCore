#!/usr/bin/env python3
"""Exercise combined boot selection, setting persistence and service isolation in an ARM ELF."""
import argparse
import errno
from pathlib import Path
import struct
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from arm_image import ArmImage


def boot(elf, mask, fail_id=False, interval=None, test_commands=True):
    image = ArmImage(elf)
    ids, starts, loads, saved = [], [], [], []
    scratch = 0x20039000
    fail_save = False
    for name in ("z_impl_k_mutex_lock", "z_impl_k_mutex_unlock", "bt_gatt_authorization_cb_register"):
        image.hook(name, lambda: image.ret(0))

    def bt_ready():
        starts.append("Bluetooth")
        image.ret(0)

    def load():
        loads.append(1)
        image.ret(0)

    def id_get():
        image.put_word(image.arg(1), 1 + len(ids))  # Includes the default identity.
        image.ret(0)

    def allocate():
        ids.append(len(ids) + 1)
        image.ret(-errno.ENOMEM if fail_id else ids[-1])

    def wake():
        threads = {image.symbols["_k_thread_obj_apple_worker"]: "A",
                   image.symbols["_k_thread_obj_sidewalk_worker"]: "C"}
        starts.append(threads[image.arg(0)])
        image.ret(0)

    def record(role):
        starts.append(role)
        image.ret(0)

    def read_setting():
        image.cpu.mem_write(image.arg(1), stored)
        image.ret(len(stored))

    def save():
        assert bytes(image.cpu.mem_read(image.arg(0), 18)).split(b"\0")[0] == b"rak_pings/enabled"
        assert image.arg(2) == 1
        if not fail_save:
            saved.append(image.cpu.mem_read(image.arg(1), 1)[0])
        image.ret(-errno.EIO if fail_save else 0)

    image.hook("sid_ble_bt_enable", bt_ready)
    image.hook("bt_id_get", id_get)
    image.hook("bt_id_create", allocate)
    image.hook("z_impl_k_wakeup", wake)
    image.hook("tag_mesh_start", lambda: record("mesh"))
    image.hook("findhub_entry", lambda: record("B"))
    image.hook("settings_load", load)
    image.hook("settings_save_one", save)
    image.hook("adc_nrfx_channel_setup", read_setting)
    if interval is not None:
        stored = struct.pack("<H", interval)
        image.cpu.mem_write(scratch, b"interval\0")
        assert image.call("load_ping_setting", scratch, len(stored), image.symbols["adc_nrfx_channel_setup"] | 1, 0) == 0
    if mask is not None:
        stored = bytes((mask,))
        image.cpu.mem_write(scratch, b"enabled\0")
        assert image.call("load_ping_setting", scratch, 1, image.symbols["adc_nrfx_channel_setup"] | 1, 0) == 0
    expected = 7 if mask is None else mask
    err = image.call("main")
    if fail_id:
        assert err == (-errno.ENOMEM & 0xffffffff) and starts == ["Bluetooth", "mesh"]
        return
    assert err == 0 and ids == [1, 2, 3]
    assert starts == ["Bluetooth", "mesh"] + [letter for letter, bit in (("A", 1), ("C", 4), ("B", 2)) if expected & bit]
    assert image.word(image.symbols["boot_mask"]) == expected
    assert image.call("__wrap_settings_load") == 0 and loads == [1]

    assert image.call("rak_ping_interval_units") == (interval or 10) * 1600
    if not test_commands:
        return image
    image.hook("shell_fprintf_normal", lambda: image.ret(0))

    def select(command, letter):
        image.cpu.mem_write(scratch + 32, command.encode() + b"\0")
        image.cpu.mem_write(scratch + 48, letter.encode() + b"\0")
        image.cpu.mem_write(scratch, struct.pack("<II", scratch + 32, scratch + 48))
        return image.call("configure", 0, 2, scratch)

    assert select("off", "all") == 0
    assert select("on", "B") == 0 and saved[-1] == 2
    fail_save = True
    assert select("on", "C") == (-errno.EIO & 0xffffffff)
    assert image.word(image.symbols["next_mask"]) == 2
    assert image.word(image.symbols["boot_mask"]) == expected


def isolation(elf):
    image = ArmImage(elf)
    scratch = 0x20039000
    uuid = scratch + 64
    android_allowed = True
    protocol_ready = True

    def info():
        # Pinned nRF52840 ABI for bt_conn_info.id.
        image.cpu.mem_write(image.arg(1) + 2, bytes((identity,)))
        image.ret(0)

    def primary():
        image.put_word(image.arg(6), uuid)
        image.ret(0)

    image.hook("bt_conn_get_info", info)
    image.hook("bt_gatt_attr_get_handle", lambda: image.ret(10))
    image.hook("bt_gatt_foreach_attr_type", primary)
    image.hook("fmna_is_ready", lambda: image.ret(protocol_ready))
    image.hook("rak_sidewalk_running", lambda: image.ret(protocol_ready))
    for name in ("z_impl_k_mutex_lock", "z_impl_k_mutex_unlock"):
        image.hook(name, lambda: image.ret(0))
    image.hook("adc_nrfx_channel_setup", lambda: image.ret(android_allowed))
    callback = image.symbols["adc_nrfx_channel_setup"] | 1
    image.cpu.mem_write(scratch, struct.pack("<II", callback, callback))
    assert image.call("rak_register_android_gatt", scratch) == 0
    services = (
        (0x1800, (1, 2, 3)), (0x1801, (1, 2, 3)), (0xfe03, (1,)),
        (0xfe2c, (2,)), (0x180a, (2,)), (0xfd44, (3,)), (0xbeef, ()),
        ("15190001-12f4-c226-88ed-2ac5579f2a85", (2,)),
        ("87290102-3c51-43b1-a1a9-11b9dc38478b", (3,)),
    )
    for service, owners in services:
        encoded = struct.pack("<BxH", 0, service) if isinstance(service, int) else b"\x02" + UUID(service).bytes[::-1]
        image.cpu.mem_write(uuid, encoded)
        for identity in range(4):
            for write in range(2):
                assert image.call("allow_attr", 1, 2, write) == int(identity in owners)
    android_allowed = False
    identity = 2
    image.cpu.mem_write(uuid, struct.pack("<BxH", 0, 0xfe2c))
    assert image.call("allow_attr", 1, 2, 0) == 0
    protocol_ready = False
    for identity, service in ((1, 0xfe03), (3, 0xfd44)):
        image.cpu.mem_write(uuid, struct.pack("<BxH", 0, service))
        assert image.call("allow_attr", 1, 2, 1) == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    args = parser.parse_args()
    for selected in [None, *range(8)]:
        boot(args.elf, selected)
    boot(args.elf, 7, fail_id=True)
    isolation(args.elf)
    print("PASS all eight boot selections, default ABC, shared host, flash errors and per-identity GATT access")
