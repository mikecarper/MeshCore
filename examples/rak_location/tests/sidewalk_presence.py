#!/usr/bin/env python3
"""Run the Sidewalk presence application with simulated SDK, RTOS and flash responses."""
import argparse
from unicorn.arm_const import UC_ARM_REG_PC

from arm_image import ArmImage


def check(elf):
    image = ArmImage(elf)
    handle = 0x20039000
    calls = []
    waits = 0

    def read_header():
        assert image.word(image.arg(0) + 4) + image.arg(1) == 0xd1000 and image.arg(3) == 4
        image.cpu.mem_write(image.arg(2), b"SID0")
        image.ret(0)

    def init():
        image.put_word(image.arg(1), handle)
        calls.append("init")
        image.ret(0)

    def start():
        assert image.arg(0) == handle and image.arg(1) == 1  # BLE
        calls.append("start")
        image.ret(0)

    def process():
        assert image.arg(0) == handle
        calls.append("process")
        image.ret(0)

    def worker():
        assert image.arg(3) == image.symbols["_ZN12_GLOBAL__N_16workerEPvS0_S0_"] | 1
        calls.append("mesh")
        image.ret(image.arg(0))

    def wait():
        nonlocal waits
        # k_timeout_t is a 64-bit signed tick count in this build.
        assert (image.arg(2), image.arg(3)) == (0xffffffff, 0xffffffff)
        waits += 1
        if waits == 3:
            # End the emulated main after two SDK wakeups.
            image.cpu.reg_write(UC_ARM_REG_PC, image.stop | 1)
        else:
            image.ret(0)

    def unexpected():
        raise AssertionError("Application requested an active connection or location service")

    image.hook("z_impl_device_is_ready", lambda: image.ret(1))
    for name in ("adc_nrfx_channel_setup", "z_impl_k_mutex_lock", "z_impl_k_mutex_unlock",
                 "sid_platform_init", "sid_pal_mfg_store_get_version"):
        image.hook(name, lambda: image.ret(0))
    image.hook("flash_area_read", read_header)
    image.hook("sid_init", init)
    image.hook("sid_start", start)
    image.hook("sid_process", process)
    image.hook("z_impl_k_thread_create", worker)
    image.hook("z_impl_k_sem_take", wait)
    for name in ("sid_ble_bcn_connection_request", "sid_location_run", "sid_location_init"):
        if name in image.symbols:
            image.hook(name, unexpected)
    image.call("main")
    assert calls == ["init", "start", "mesh", "process", "process", "process"] and waits == 3
    assert image.word(image.symbols["running"]) == 1
    image.hook("shell_fprintf_error", lambda: image.ret(0))
    for command in (b"connect\0", b"locate\0"):
        image.cpu.mem_write(handle, command)
        image.put_word(handle + 32, handle)
        assert image.call("request_cmd", 0, 1, handle + 32) == (-134 & 0xffffffff)  # newlib ENOTSUP
    print(f"PASS {elf}: BLE start, MeshCore worker, event-driven processing, both location commands disabled")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    check(parser.parse_args().elf)
