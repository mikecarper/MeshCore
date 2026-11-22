#!/usr/bin/env python3
"""Check the USB interval setting and pass the linked host's actual commands to the linked radio controller."""
import argparse
import errno
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from arm_image import ArmImage


def check_setting(elf):
    image = ArmImage(elf)
    scratch = 0x20039000
    saved = []
    stored = b""
    fail = False
    for name in ("z_impl_k_mutex_lock", "z_impl_k_mutex_unlock", "shell_fprintf_normal",
                 "shell_fprintf_error"):
        if name in image.symbols:
            image.hook(name, lambda: image.ret(0))
    image.hook("rak_host_ready", lambda: image.ret(0))

    def save():
        assert bytes(image.cpu.mem_read(image.arg(0), 19)) == b"rak_pings/interval\0"
        assert image.arg(2) == 2
        if not fail:
            saved.append(bytes(image.cpu.mem_read(image.arg(1), 2)))
        image.ret(-errno.EIO if fail else 0)

    def read():
        assert image.arg(2) == len(stored)
        image.cpu.mem_write(image.arg(1), stored)
        image.ret(len(stored))

    image.hook("settings_save_one", save)
    image.hook("adc_nrfx_channel_setup", read)
    image.cpu.mem_write(scratch + 32, b"interval\0")
    image.cpu.mem_write(scratch, struct.pack("<II", scratch + 32, scratch + 64))

    def command(value=None):
        if value is not None:
            image.cpu.mem_write(scratch + 64, value.encode() + b"\0")
        return image.call("interval_cmd", 0, 1 if value is None else 2, scratch)

    assert command() == 0 and command("10") == 0 and not saved
    for text in ("0", "301", "", "-1", "+1", "1.5", "10s", " 10", "3000", "999999999999"):
        assert command(text) == (-errno.EINVAL & 0xffffffff) and not saved
    for value in (1, 30, 299, 300):
        assert command(str(value)) == 0 and saved[-1] == struct.pack("<H", value)
    fail = True
    assert command("10") == (-errno.EIO & 0xffffffff)
    assert command("300") == 0 and len(saved) == 4  # A failed save leaves the previous selection.
    for stored in (b"\0\0", struct.pack("<H", 301), b'\xff', b'\x01\0\0'):
        assert image.call("load_ping_setting", scratch + 32, len(stored),
                          image.symbols["adc_nrfx_channel_setup"] | 1, 0) == (-errno.EINVAL & 0xffffffff)
    assert command("300") == 0 and image.call("rak_ping_interval_units") == 16000
    print("PASS USB query, 1–300 bounds, invalid text and saved records, flash errors, next-boot selection")


def check_radio(elf):
    host = ArmImage(elf)
    radio = ArmImage(elf)
    # Nordic nRF52840 peripheral and Cortex-M interrupt registers. The radio
    # scheduler and entropy callback are fixtures. SDC processes real commands.
    for base, size in ((0x40000000, 0x200000), (0xe000e000, 0x1000)):
        radio.cpu.mem_map(base, size)
    radio.hook("mpsl_is_initialized", lambda: radio.ret(1))
    def open_timeslot():
        radio.cpu.mem_write(radio.arg(1), bytes(1))
        radio.ret(0)
    radio.hook("mpsl_timeslot_session_open", open_timeslot)
    noop = radio.symbols["adc_nrfx_channel_setup"] | 1
    radio.hook("adc_nrfx_channel_setup", lambda: radio.ret(0))
    assert radio.call("sdc_init", noop) == 0
    radio.call("sdc_support_ext_adv")
    radio.call("sdc_support_peripheral")
    scratch = 0x20039000
    radio.put_word(scratch, noop)
    assert radio.call("sdc_rand_source_register", scratch) == 0
    assert radio.call("sdc_enable", noop, radio.symbols["sdc_mempool"]) == 0

    # Pinned Zephyr ABI. id_addr[4] and id_count lead struct bt_dev.
    dev = host.symbols["bt_dev"]
    host.cpu.mem_write(dev, bytes((1, 1, 2, 3, 4, 5, 0xc6)) * 4 + bytes((4,)))
    param = scratch + 1024
    buf, cp = scratch + 1280, scratch + 1536
    host.hook("bt_hci_cmd_alloc", lambda: host.ret(buf))
    host.hook("net_buf_simple_add", lambda: host.ret(cp))
    host.hook("net_buf_unref", lambda: host.ret(0))

    def address_type():
        host.cpu.mem_write(host.arg(3), b"\x01")
        host.ret(0)

    host.hook("bt_id_set_adv_own_addr", address_type)
    seen = []
    def send():
        assert host.arg(0) == 0x2036  # LE Set Extended Advertising Parameters, v1
        packet = bytes(host.cpu.mem_read(cp, 25))
        assert int.from_bytes(packet[1:3], "little") == 0x13, packet.hex()  # legacy scannable ADV_IND
        assert int.from_bytes(packet[3:6], "little") == int.from_bytes(packet[6:9], "little") == units
        radio.cpu.mem_write(scratch, packet)
        assert radio.call("sdc_hci_cmd_le_set_ext_adv_params", scratch, scratch + 128) == 0
        host.put_word(host.arg(2), buf)
        host.put_word(buf + 12, cp + 64)
        seen.append(units)
        host.ret(0)

    host.hook("bt_hci_cmd_send_sync", send)
    for seconds in (1, 10, 11, 30, 41, 42, 299, 300):
        units = seconds * 1600
        host.cpu.mem_write(param, struct.pack("<B3xIII16x", 1, 3, units, units))
        assert host.call("bt_le_ext_adv_update_param", scratch, param) == 0
    assert len(seen) == 8
    print("PASS Zephyr encodes and Nordic controller accepts 1, 10, 11, 30, 41, 42, 299 and 300 s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    args = parser.parse_args()
    check_setting(args.elf)
    check_radio(args.elf)
