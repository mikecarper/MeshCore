#!/usr/bin/env python3
"""Check finder ping counters at the Bluetooth host boundary of a linked RAK ELF."""
import argparse
import errno
import struct
from pathlib import Path

from arm_image import ArmImage


def check(profile, elf):
    image = ArmImage(elf)
    config = (Path(elf).parent / ".config").read_text().splitlines()

    def setting(name):
        return int(next(line.split("=")[1] for line in config if line.startswith(name + "=")))

    # Deliberately synthetic fields. This test never starts a radio or loads owner data.
    google_eid_len = setting("CONFIG_BT_FAST_PAIR_FHN_ECC_LEN") if profile == "B" else 20
    apple = [(0xff, b"\x4c\x00\x12\x19" + bytes(25))]
    google = [(0x16, b"\xaa\xfe\x40" + bytes(google_eid_len + 1))]
    sidewalk = [(0x03, b"\x03\xfe"), (0xff, b"\x71\x01\x00")]
    valid = {"A": apple, "B": google, "C": sidewalk}[profile]
    ad, payload, adv, result_slot = 0x20039000, 0x20039100, 0x20039200, 0x20039300
    result = 0
    expected_args = ()

    def host_call():
        assert tuple(image.arg(i) for i in range(len(expected_args))) == expected_args
        image.ret(result)

    def create_set():
        if not result:
            image.put_word(image.arg(2), adv)
        host_call()

    image.hook("bt_le_ext_adv_create", create_set)
    image.hook("bt_le_ext_adv_set_data", host_call)
    image.hook("bt_le_ext_adv_start", host_call)
    image.hook("bt_le_ext_adv_get_index", lambda: image.ret(image.arg(0) - adv))

    def call(function, *args, error=0):
        nonlocal result, expected_args
        result, expected_args = error, args
        assert image.call("__wrap_bt_le_ext_adv_" + function, *args) == (error & 0xffffffff)

    def update(fields, error=0):
        offset = payload
        for index, (kind, data) in enumerate(fields):
            image.cpu.mem_write(ad + 8 * index, struct.pack("<BB2xI", kind, len(data), offset))
            image.cpu.mem_write(offset, data)
            offset += len(data)
        # Include a scan response to verify all five stack/register arguments pass through.
        call("set_data", adv, ad, len(fields), ad, len(fields), error=error)

    def counts():
        return tuple(image.word(image.symbols[k]) for k in ("accepted_updates", "accepted_starts"))

    call("create", 0, 0, result_slot)
    rejected = [[(0x09, b"RAK-A-Apple")], [(0x16, b"\x2c\xfe\x01")],
                [(0xff, b"\x4c\x00\x12\x02\x00\x00")], sidewalk[:1], sidewalk[1:]]
    rejected += [fields for fields in (apple, google, sidewalk) if fields != valid]
    for fields in rejected:
        update(fields)
        call("start", adv, 0)
    assert counts() == (0, 0)
    update(valid, error=-errno.EIO)
    call("start", adv, 0)
    assert counts() == (0, 0)
    update(valid)
    call("start", adv, 0, error=-errno.EIO)
    assert counts() == (1, 0)
    if setting("CONFIG_BT_EXT_ADV_MAX_ADV_SET") > 1:
        call("start", adv + 1, 0)
        assert counts() == (1, 0)
    update(rejected[0], error=-errno.EIO)
    call("create", 0, 0, result_slot, error=-errno.ENOMEM)
    call("start", adv, 0)
    assert counts() == (1, 1)
    update(rejected[0])
    call("start", adv, 0)
    assert counts() == (1, 1)
    # Reuse the controller's advertising slot for an unrelated advertiser.
    update(valid)
    call("create", 0, 0, result_slot)
    call("start", adv, 0)
    assert counts() == (2, 1)
    if profile == "B":
        update([(0x16, b"\xaa\xfe\x41" + bytes(google_eid_len + 1))])
        call("start", adv, 0)
        assert counts() == (3, 2)
    print(f"PASS {profile} {elf}: native frame filter, host errors, set reuse and start counters")


def check_sdk_calls(profile, elf):
    # Exercise the SDK's linked advertising functions with an emulated Bluetooth host.
    # Starts below are host calls only. The emulator has no RF peripheral.
    image = ArmImage(elf)
    adv, scratch = 0x20039000, 0x20039100

    def create_set():
        image.put_word(image.arg(2), adv)
        image.ret(0)

    image.hook("bt_le_ext_adv_create", create_set)
    for name in ("bt_le_ext_adv_set_data", "bt_le_ext_adv_start", "bt_le_ext_adv_get_index"):
        image.hook(name, lambda: image.ret(0))

    if profile == "A":
        # Peripherals for identity selection and transmitter power configuration.
        for name in ("bt_id_reset", "bt_hci_get_adv_handle", "bt_hci_cmd_send_sync", "net_buf_unref"):
            image.hook(name, lambda: image.ret(0))
        image.hook("bt_hci_cmd_create", lambda: image.ret(scratch + 128))
        image.hook("net_buf_simple_add", lambda: image.ret(scratch + 192))
        assert image.call("fmna_adv_start_separated", scratch) == 0
    elif profile == "B":
        image.put_word(image.symbols["fhn_adv_set"], adv)
        image.call("fhn_state_battery_level_changed")
        assert image.call("fhn_adv_start") == 0
    else:
        image.hook("k_work_reschedule", lambda: image.ret(1))
        assert image.call("sid_ble_advert_update", scratch, 8) == 0
        assert image.call("sid_ble_advert_start") == 0

    assert image.word(image.symbols["accepted_updates"]) == 1
    assert image.word(image.symbols["accepted_starts"]) == 1
    print(f"PASS {profile} {elf}: linked native SDK advertising code reaches both ping counters")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=("A", "B", "C"))
    parser.add_argument("elf")
    args = parser.parse_args()
    check(args.profile, args.elf)
    check_sdk_calls(args.profile, args.elf)
