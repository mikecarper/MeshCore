#!/usr/bin/env python3
"""Run both collar roles against a USB tracker Companion using simulated GPS, motion and LoRa."""
import argparse
import errno
from pathlib import Path
import struct
import sys

from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R1, UC_ARM_REG_SP

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from arm_image import ArmImage

EPOCH = 1700000000
COLLAR, OWNER = bytes(range(32)), bytes(range(32, 64))
SCRATCH = 0x20039000


class Tag:
    def __init__(self, elf, identity, settings=None):
        self.image = im = ArmImage(elf)
        im.cpu.mem_write(SCRATCH, b"pet\0")
        entry = im.call("z_shell_find_cmd", 0, SCRATCH, SCRATCH + 64)
        assert entry, "pet command missing from the USB shell"
        im.symbols["pet_command"] = im.word(entry + 12)  # shell_static_entry.handler on nRF52840
        self.settings = settings if settings is not None else {}
        self.settings["tagmesh/seed"] = identity
        self.fail_key = None
        self.clock = 0
        self.fix = None
        self.gps = False
        self.motion = 0
        self.inbox, self.sent = [], []
        self.sleeps = 0
        for name in ("settings_subsys_init", "z_impl_k_mutex_lock", "z_impl_k_mutex_unlock",
                     "shell_fprintf_normal", "shell_fprintf_error", "_Z15tag_gps_processv",
                     "_Z15tag_radio_applyRK15TagRadioProfile", "_Z14tag_radio_idlev",
                     "_Z16tag_radio_listenv", "tag_motion_start", "tag_motion_stop"):
            im.hook(name, lambda: im.ret(0))
        for name in ("_Z17tag_gps_availablev", "_Z19tag_radio_availablev"):
            im.hook(name, lambda: im.ret(1))
        im.hook("tag_battery_mv", lambda: im.ret(3900))

        def load():
            key = self.string(im.arg(0))
            value = self.settings.get(key)
            if value is not None:
                assert len(value) == im.arg(2)
                im.cpu.mem_write(im.arg(1), value)
            im.ret(-errno.ENOENT if value is None else 0)

        def save():
            key = self.string(im.arg(0))
            if key == self.fail_key:
                im.ret(-errno.EIO)
            else:
                self.settings[key] = bytes(im.cpu.mem_read(im.arg(1), im.arg(2)))
                im.ret(0)

        def keypair():
            im.cpu.mem_write(im.arg(0), identity)
            im.cpu.mem_write(im.arg(1), identity * 2)
            im.ret(0)

        def shared():
            # Fixed session secret; packet encryption, authentication and ACK hashing run on ARM.
            im.cpu.mem_write(im.arg(0), b'k' * 32)
            im.ret(0)

        def sample():
            value, self.motion = self.motion, 0
            im.ret(value)

        def power(on):
            self.gps = on
            if not on:
                self.fix = None
            im.ret(int(on))

        def latest():
            valid = self.gps and self.fix and self.clock - self.fix[3] <= 15000
            if valid:
                im.cpu.mem_write(im.arg(0), struct.pack("<iiI4xq", *self.fix))
            im.ret(int(bool(valid)))

        def read():
            frame = self.inbox.pop(0) if self.inbox else b""
            im.cpu.mem_write(im.arg(0), frame)
            im.ret(len(frame))

        def send():
            self.sent.append(bytes(im.cpu.mem_read(im.arg(0), im.arg(1))))
            im.ret(0)

        def ticks():
            tick = (self.clock * 32768 + 999) // 1000
            im.cpu.reg_write(UC_ARM_REG_R1, tick >> 32)
            im.ret(tick)

        def sleep():
            self.sleeps += 1
            im.ret(0)
            im.cpu.emu_stop()

        im.hook("_ZN12_GLOBAL__N_14loadEPKcPvj", load)
        im.hook("settings_save_one", save)
        im.hook("ed25519_create_keypair", keypair)
        im.hook("ed25519_key_exchange", shared)
        im.hook("tag_motion_sample", sample)
        im.hook("_Z13tag_gps_beginv", lambda: power(True))
        im.hook("_Z11tag_gps_endv", lambda: power(False))
        im.hook("_Z14tag_gps_latestR6TagFix", latest)
        im.hook("_Z14tag_radio_readPhj", read)
        im.hook("_Z14tag_radio_sendPKhj", send)
        im.hook("z_impl_k_uptime_ticks", ticks)
        im.hook("z_impl_k_sleep", sleep)
        im.cpu.reg_write(UC_ARM_REG_SP, SCRATCH - 256)
        im.cpu.reg_write(UC_ARM_REG_LR, im.stop | 1)
        im.cpu.reg_write(UC_ARM_REG_PC, im.symbols["_ZN12_GLOBAL__N_16workerEPvS0_S0_"] | 1)
        self.worker = im.cpu.context_save()
        self.step(0)

    def string(self, address):
        return bytes(self.image.cpu.mem_read(address, 32)).split(b"\0")[0].decode()

    def step(self, ms, position=None, utc=None):
        if position is not None:
            self.fix = (*position, utc or EPOCH + ms // 1000, ms)
        self.clock = ms
        im = self.image
        im.cpu.context_restore(self.worker)
        sleeps = self.sleeps
        im.cpu.emu_start(im.cpu.reg_read(UC_ARM_REG_PC) | 1, im.stop, count=800000)
        assert self.sleeps == sleeps + 1, f"Worker stalled at {im.cpu.reg_read(UC_ARM_REG_PC):#x}"
        self.worker = im.cpu.context_save()

    def command(self, *args):
        im = self.image
        pointer = SCRATCH + 128
        for i, arg in enumerate(("pet", *args)):
            data = arg.encode() + b"\0"
            im.cpu.mem_write(pointer, data)
            im.put_word(SCRATCH + 4 * i, pointer)
            pointer += len(data)
        return im.call("pet_command", 0, len(args) + 1, SCRATCH)

    def alerts(self):
        return [p for p in self.sent if (p[0] >> 2) & 15 == 2]


def check(collar_elf, companion_elf):
    collar = Tag(collar_elf, COLLAR)
    owner = Tag(companion_elf, OWNER)
    assert not collar.gps and not owner.gps
    for tag in (owner, collar):
        assert tag.command("radio", "915000000", "125", "9", "5") == 0
    assert collar.command("fence", "0", "170", "1", "-170") != 0
    assert owner.command("watch", COLLAR.hex()) == 0
    assert collar.command("owner", OWNER.hex()) == 0
    for bounds in [("1", "1", "1", "2"), ("0", "0", "91", "1"),
                   ("0", "-181", "1", "1"), ("0", "1", "1", "1"), ("NaN", "0", "1", "1")]:
        assert collar.command("fence", *bounds) != 0
    # Cross the date line; the north and south edges are inside the box.
    assert collar.command("fence", "0", "170", "1", "-170") == 0
    collar.step(25)
    assert not collar.gps
    assert collar.command("on") == 0
    collar.step(50)
    assert collar.gps
    inside = (0, 1790000000)
    other_edge = (10000000, -1790000000)
    outside = (-1250000, -1790000000)
    collar.step(1000, inside)
    collar.step(2000, other_edge)  # Silent baseline. Ordinary pet check also runs.
    collar.step(22000)
    assert not collar.gps and not collar.alerts()
    collar.motion = -1  # An accelerometer fault cannot masquerade as movement.
    collar.step(24000)
    assert not collar.gps
    collar.image.call("rak_apple_motion_start")
    collar.image.call("rak_android_motion_start")
    collar.motion = 1
    assert collar.image.call("rak_android_motion_detected")
    collar.step(26000)
    assert collar.gps and collar.image.call("rak_apple_motion_detected")
    collar.step(27000, outside)
    collar.step(28000, outside, utc=EPOCH + 27)
    collar.step(29000, inside)
    collar.step(30000, outside)
    assert not collar.alerts()
    collar.step(31000, outside)
    assert len(collar.alerts()) == 1
    exit_frame = collar.alerts()[0]
    # Lost ACK and subsequent return. Preserve both events through reboot.
    collar.step(32000, inside)
    collar.step(33000, inside)
    collar.inbox.append(bytes((14, 0, 1, 2, 3, 4)))  # Wrong ACK.
    collar.step(34000)
    collar = Tag(collar_elf, COLLAR, collar.settings)
    assert collar.alerts() == [exit_frame]
    owner.step(1000)
    corrupt = bytearray(exit_frame)
    corrupt[-1] ^= 1
    owner.inbox.append(bytes(corrupt))
    owner.step(2000)
    assert "tagmesh/watchalert1" not in owner.settings
    owner.inbox.append(exit_frame)
    owner.step(3000)
    received = owner.settings["tagmesh/watchalert1"]
    assert b"Collar left the fence at -0.1250000, -179.0000000\0" in received
    assert received[:32] == COLLAR and struct.unpack_from("<I", received, 32)[0] == EPOCH + 31
    # Simulate a dropped ACK and verify retransmission, receiver dedup and queue draining.
    collar.step(21000)
    collar.step(59000)
    assert len(collar.alerts()) == 1
    collar.step(60000)
    assert len(collar.alerts()) == 2 and collar.alerts()[-1] != exit_frame
    owner.inbox.append(collar.alerts()[-1])
    owner.step(61000)
    assert owner.settings["tagmesh/watchalert1"] == received and len(owner.sent) == 2
    collar.inbox.append(owner.sent[-1])
    collar.step(62000)
    assert len(collar.alerts()) == 3
    owner.inbox.append(collar.alerts()[-1])
    owner.step(63000)
    assert b"Collar returned to the fence at 0.0000000, 179.0000000\0" in owner.settings["tagmesh/watchalert1"]
    collar.inbox.append(owner.sent[-1])
    collar.step(64000)
    collar.step(125000)
    assert len(collar.alerts()) == 3
    assert collar.command("off") == 0
    assert collar.command("owner", bytes(reversed(OWNER)).hex()) == 0
    assert collar.command("on") == 0
    collar.step(126000)
    for t in (127000, 128000):
        collar.step(t, outside)
    assert len(collar.alerts()) == 3
    assert collar.command("fence", "off") == 0
    # A collar can record motion fixes without a box and can disable them again.
    assert collar.command("motion", "on") == 0
    collar.step(152000)
    assert not collar.gps
    collar.motion = 1
    collar.step(154000)
    assert collar.gps
    collar.step(155000, other_edge)
    collar.step(165000)
    assert not collar.gps and len(collar.alerts()) == 3
    # No GPS signal during motion: stop the acquisition and do not manufacture a crossing.
    collar.motion = 1
    collar.step(167000)
    assert collar.gps
    collar.step(288000)
    assert not collar.gps and len(collar.alerts()) == 3
    assert collar.command("motion", "off") == 0
    collar.motion = 1
    collar.step(291000)
    assert not collar.gps
    # A normal western-hemisphere box, starting outside, with a failed flash write.
    assert collar.command("fence", "47.610", "-122.210", "47.612", "-122.206") == 0
    collar.step(292000)
    collar.step(293000, (476110000, -1222150000))
    collar.step(294000, (476110000, -1222160000))
    assert len(collar.alerts()) == 3
    collar.motion = 1
    collar.step(295000, (476120000, -1222060000))
    collar.fail_key = "tagmesh/fence2"
    collar.step(296000, (476110000, -1222080000))
    assert len(collar.alerts()) == 3
    collar.fail_key = None
    collar.step(297000, (476100000, -1222100000))
    assert len(collar.alerts()) == 4
    print(f"{collar_elf}: motion wake, silent baseline, fresh crossings, encrypted owner alerts, retries, reboot and pause passed")


def check_shape(collar_elf, companion_elf, args, inside, outside, back):
    collar, owner = Tag(collar_elf, COLLAR), Tag(companion_elf, OWNER)
    for tag in (owner, collar):
        assert tag.command("radio", "915000000", "125", "9", "5") == 0
    assert collar.command("owner", OWNER.hex()) == owner.command("watch", COLLAR.hex()) == 0
    assert collar.command("fence", *args.split()) == 0
    saved = collar.settings["tagmesh/fence2"]
    for invalid in ("0 0 0", "0 0 -1", "0 0 1.5", "0 0 4294967296", "91 0 100",
                    "0 0 1 1 2", "0 0 1 1 2 2", "0 0 2 2 0 2 2 0", "0 0 0 1 0 0", "0 180 1 -180",
                    "0 0 0 1 0 2 1 2 2 2 2 1 2 0 1.5 0 1 0 0.5 0"):
        assert collar.command("fence", *invalid.split()) != 0, invalid
        assert collar.settings["tagmesh/fence2"] == saved
    collar = Tag(collar_elf, COLLAR, collar.settings)
    assert collar.command("on") == 0

    def fix(ms, point):
        collar.step(ms, tuple(round(c * 10000000) for c in point))

    collar.step(50)
    fix(1000, inside)
    fix(2000, inside)
    collar.step(22000)
    assert not collar.alerts()
    collar.motion = 1
    collar.step(24000)
    for ms, point, expected in [(25000, outside, b"left"), (31000, back, b"returned to")]:
        fix(ms, point)
        fix(ms + 1000, point)
        assert len(collar.alerts()) == (1 if expected == b"left" else 2)
        owner.inbox.append(collar.alerts()[-1])
        owner.step(ms + 2000)
        assert b"Collar " + expected + b" the fence at " in owner.settings["tagmesh/watchalert1"]
        collar.inbox.append(owner.sent[-1])
        collar.step(ms + 2100)
    print(f"{collar_elf}: {args} exit and return acknowledged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sensor", type=Path)
    parser.add_argument("companion", type=Path)
    paths = parser.parse_args()
    for firmware in (paths.sensor, paths.companion):
        check(firmware, paths.companion)
        # At 60° latitude the two points across the date line are ~95 m and ~111 m east.
        check_shape(firmware, paths.companion, "60 179.999 100",
                    (60, -179.9993), (60, -179.999), (60, 179.999))
        check_shape(firmware, paths.companion, "0 0 1", (0, 0.000008), (0, 0.000010), (0, 0))
        check_shape(firmware, paths.companion, "1 1 -1 -1", (0, 0), (-2, 0), (-1, 1))
        check_shape(firmware, paths.companion, "0 0 0 2 2 0", (0.5, 0.5), (1.5, 1.5), (1, 1))
        # Concave outline straddling the date line, a point in the notch and a point on its edge.
        check_shape(firmware, paths.companion, "-1 170 2 170 2 -170 1 -170 1 175 -1 175",
                    (1.5, -175), (0, -175), (1, 175))
        # All nine vertices fit the existing USB command, with no repeated closing vertex.
        check_shape(firmware, paths.companion, "0 0 0 1 0 2 1 2 2 2 2 1 2 0 1.5 0 1 0",
                    (1, 1), (1, 3), (1, 0))
