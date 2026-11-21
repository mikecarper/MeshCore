#!/usr/bin/env python3
"""Run the ARM firmware's application entry with erased locator storage and simulated drivers.

Usage: python blank_boot.py {sidewalk,findhub} build/zephyr/zephyr.elf
Requires pyelftools and unicorn 2.1.4. This checks the app-to-RTOS boundary.
"""
import argparse
import errno
from arm_image import ArmImage


def check(network, filename, flash_error=False):
    image = ArmImage(filename)
    page = 0xd1000 if network == "sidewalk" else 0xc8000
    pages_read = []
    workers = []

    def read_flash():
        area = image.arg(0)
        address = image.word(area + 4) + image.arg(1)
        size = image.arg(3)
        pages_read.append((address, size))
        if not flash_error:
            image.cpu.mem_write(image.arg(2), bytes(image.cpu.mem_read(address, size)))
        image.ret(-errno.EIO if flash_error else 0)

    def create_thread():
        workers.append(image.arg(3))
        image.ret(image.arg(0))

    def unexpected_finder():
        raise AssertionError("Finder initialization on an empty or unreadable credential page")

    image.hook("z_impl_device_is_ready", lambda: image.ret(1))
    image.hook("adc_nrfx_channel_setup", lambda: image.ret(0))
    image.hook("flash_area_read", read_flash)
    image.hook("z_impl_k_thread_create", create_thread)
    image.hook("sid_platform_init" if network == "sidewalk" else "rak_findhub_start", unexpected_finder)
    if network == "sidewalk":
        image.hook("z_impl_k_mutex_lock", lambda: image.ret(0))
        image.hook("z_impl_k_mutex_unlock", lambda: image.ret(0))

    assert image.call("main") == 0
    assert pages_read == [(page, 4)], pages_read
    assert workers == [image.symbols["_ZN12_GLOBAL__N_16workerEPvS0_S0_"] | 1], workers
    err = "last_error" if network == "sidewalk" else "startup_err"
    assert image.word(image.symbols[err]) == (-(errno.EIO if flash_error else errno.ENODATA) & 0xffffffff)
    print(f"PASS {filename}: {'unreadable' if flash_error else 'blank'} page at {page:#x}; "
          "finder idle; MeshCore worker scheduled")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", choices=("sidewalk", "findhub"))
    parser.add_argument("elf")
    args = parser.parse_args()
    for read_error in (False, True):
        check(args.network, args.elf, read_error)
