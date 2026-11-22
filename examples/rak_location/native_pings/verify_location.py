#!/usr/bin/env python3
"""Exercise the combined firmware's Sidewalk worker and USB locate command with a simulated gateway."""
import argparse
from pathlib import Path
import struct
import sys

from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R1, UC_ARM_REG_SP

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from arm_image import ArmImage


def check(elf, seconds):
    image = ArmImage(elf)
    scratch = 0x20039000
    handle = scratch + 128
    image.put_word(image.symbols["boot_interval_s"], seconds)
    attempts, connects, waits = [], [], []
    clock = 0

    def read_header():
        assert image.word(image.arg(0) + 4) + image.arg(1) == 0xd1000
        image.cpu.mem_write(image.arg(2), b"SID0")
        image.ret(0)

    def init():
        image.put_word(image.arg(1), handle)
        image.ret(0)

    def location_init():
        assert image.arg(0) == handle
        cfg = image.arg(1)
        assert image.cpu.mem_read(cfg, 1) == b"\x01"  # BLE gateway
        assert image.word(cfg + 4) == image.symbols["on_location"] | 1
        image.ret(0)

    def locate():
        assert image.arg(0) == handle and image.arg(2) == 0
        assert image.cpu.mem_read(image.arg(1), 2) == bytes((6, 3))  # default effort, scan and send
        attempts.append(clock)
        image.ret(0)

    def connect():
        assert image.arg(0) == handle and image.arg(1) == 1
        connects.append(clock)
        image.ret(0)

    def ticks():
        image.cpu.reg_write(UC_ARM_REG_R1, 0)
        image.ret(clock * 32768)

    def wait():
        waits.append(image.arg(2))
        image.ret(0)
        image.cpu.emu_stop()

    image.hook("z_impl_device_is_ready", lambda: image.ret(1))
    for name in ("adc_nrfx_channel_setup", "z_impl_k_mutex_lock", "z_impl_k_mutex_unlock",
                 "sid_platform_init", "sid_pal_mfg_store_get_version", "sid_start", "sid_process",
                 "z_impl_k_sem_give", "shell_fprintf_normal", "shell_fprintf_error"):
        image.hook(name, lambda: image.ret(0))
    image.hook("flash_area_read", read_header)
    image.hook("sid_init", init)
    image.hook("sid_location_init", location_init)
    image.hook("sid_location_run", locate)
    image.hook("sid_ble_bcn_connection_request", connect)
    image.hook("z_impl_k_uptime_ticks", ticks)
    image.hook("z_impl_k_sem_take", wait)

    # The worker has its own stack. USB commands and SDK callbacks run between waits.
    image.cpu.reg_write(UC_ARM_REG_SP, scratch - 256)
    image.cpu.reg_write(UC_ARM_REG_LR, image.stop | 1)
    image.cpu.reg_write(UC_ARM_REG_PC, image.symbols["sidewalk_entry"] | 1)
    worker = image.cpu.context_save()

    def step(now):
        nonlocal clock, worker
        clock = now
        image.cpu.context_restore(worker)
        count = len(waits)
        image.cpu.emu_start(image.cpu.reg_read(UC_ARM_REG_PC) | 1, image.stop, count=20000)
        assert len(waits) == count + 1
        worker = image.cpu.context_save()

    def manual():
        image.cpu.mem_write(scratch, b"locate\0")
        image.put_word(scratch + 32, scratch)
        return image.call("request_cmd", 0, 1, scratch + 32)

    def location_event(status, error=0):
        image.cpu.mem_write(scratch, struct.pack("Bb", status, error))
        image.call("on_location", scratch, 0)

    step(0)
    assert connects == [0] and not attempts and manual() != 0
    # Pinned SDK ABI for a registered, synchronized, cloud-connected BLE device.
    image.cpu.mem_write(scratch, struct.pack("<B3xBB2xII", 0, 0, 0, 1, 1))
    image.call("on_status", scratch, 0)
    location_event(1)  # Gateway explicitly reports location unavailable.
    step(0)
    assert not attempts and manual() != 0
    location_event(0)  # Gateway is ready.
    step(0)
    assert attempts == [0] and manual() != 0  # An uplink is pending.
    assert 0 < waits[-1] <= min(seconds, 5) * 32768
    location_event(2)  # Successful send.
    assert image.word(image.symbols["sent"]) == 1
    step(seconds - 1)
    assert attempts == [0]
    step(seconds)
    assert attempts == [0, seconds]
    location_event(2)
    assert manual() == 0
    step(seconds)
    assert attempts == [0, seconds, seconds]
    location_event(2, -1)
    step(seconds)
    step(seconds + 59)
    assert len(attempts) == 3 and image.word(image.symbols["sent"]) == 2
    step(seconds + 60)
    assert attempts[-1] == seconds + 60


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    args = parser.parse_args()
    for interval in (1, 10, 300):
        check(args.elf, interval)
    print("PASS Sidewalk gateway availability, manual and automatic locate, pending guard, success and retry at 1/10/300 s")
