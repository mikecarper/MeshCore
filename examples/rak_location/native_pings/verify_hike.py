#!/usr/bin/env python3
"""Run owner enrollment, timeout, tune timing and settings against the linked collar firmware."""
import argparse
from pathlib import Path
from uuid import UUID

from verify_fence import COLLAR, OWNER, SCRATCH, Tag


def check(elf):
    tag = Tag(elf, COLLAR)
    im = tag.image
    assert tag.command("tune", "2", "1000:100,0:50,1500:200") == 0
    stored = tag.settings["tagmesh/tune1"]
    for invalid in ["20:100", "800:1", "800:150,", "800:500," * 8 + "900:50"]:
        assert tag.command("tune", "2", invalid) != 0
    tag.fail_key = "tagmesh/tune1"
    assert tag.command("tune", "off") != 0
    assert tag.settings["tagmesh/tune1"] == stored
    tag = Tag(elf, COLLAR, tag.settings)
    im = tag.image

    def sound(t, outside, callback=False):
        tag.clock = t
        if callback:
            im.call("advance", 0)
        else:
            im.call("pet_tune_tick", outside, 0, t, 0)  # int64 aligned to R2/R3

    sound(1000, 1)
    sound(1100, 1, True)
    sound(1150, 1, True)
    sound(1350, 1, True)
    sound(2999, 1)
    sound(3000, 1)
    sound(3010, 0)
    sound(6010, 0)
    assert tag.tones == [(1000,1000),(1100,0),(1150,1500),(1350,0),(3000,1000),(3010,0)]
    assert tag.command("tune", "off") == 0
    sound(6100, 1)
    assert tag.tones[-1] == (3010, 0)
    assert tag.command("tune", "play") == 0
    sound(6200, 0)
    assert tag.tones[-1] == (6200, 1000)

    # Stable RAM addresses represent peers. Only the host, radio and clock are simulated.
    phones = [SCRATCH + 512 + i * 16 for i in range(4)]
    levels = [2] * 4
    bonded = [True] * 4
    identities = [0, 0, 0, 2]
    mask = 0
    disconnected = []

    def setup():
        nonlocal im
        im = tag.image
        for i, phone in enumerate(phones):
            im.cpu.mem_write(phone, bytes([1, i+1, 2, 3, 4, 5, 0xc0]))
        def info():
            i = phones.index(im.arg(0))
            out = im.arg(1)
            # Pinned nRF ABI offsets of id, le.dst, state, security.level.
            im.cpu.mem_write(out + 2, bytes([identities[i]]))
            im.put_word(out + 8, phones[i])
            im.cpu.mem_write(out + 32, bytes([2, levels[i]]))
            im.ret(0)
        def visit():
            im.put_word(im.arg(2), mask)
            im.ret(0)
        def create():
            im.put_word(im.arg(2), SCRATCH + 800)
            im.ret(0)
        def data():
            entry = im.arg(1) + 8  # second bt_data is the service UUID
            assert im.cpu.mem_read(entry, 2) == bytes([7,16])
            assert im.cpu.mem_read(im.word(entry + 4),16) == UUID("caa50001-a471-47ba-898a-f85af2612101").bytes[::-1]
            im.ret(0)
        def random():
            im.put_word(im.arg(0), 12345)
            im.ret(0)
        def disconnect():
            disconnected.append(im.arg(0))
            im.ret(0)
        im.hook("z_impl_sys_csrand_get", random)
        im.hook("rak_host_ready", lambda: im.ret(0))
        im.hook("bt_conn_get_info", info)
        im.hook("bt_conn_get_dst", lambda: im.ret(im.arg(0)))
        im.hook("bt_conn_disconnect", disconnect)
        im.hook("bt_le_bond_exists", lambda: im.ret(bonded[phones.index(im.arg(1))]))
        im.hook("bt_conn_foreach", visit)
        im.hook("bt_foreach_bond", lambda: im.ret(0))
        im.hook("bt_le_ext_adv_get_index", lambda: im.ret(4))
        im.hook("bt_le_ext_adv_create", create)
        im.hook("bt_le_ext_adv_set_data", data)
        im.hook("bt_le_ext_adv_start", lambda: im.ret(0))
        im.hook("bt_le_ext_adv_stop", lambda: im.ret(0))
    setup()

    def claim(i, code=b"012345"):
        im.cpu.mem_write(SCRATCH + 850, code)
        return im.call("enroll", phones[i], 0, SCRATCH + 850, len(code), 0, 0)

    def tick(t, *connected):
        nonlocal mask
        tag.clock = t
        addr = SCRATCH + 900
        im.put_word(addr, 0)
        for i in connected:
            im.call("check_conn", phones[i], addr)
        mask = im.word(addr)
        tag.step(t)
        return bool(im.cpu.mem_read(im.symbols["lost"], 1)[0])

    assert tag.command("hike","on") != 0
    assert claim(0) != 6
    assert tag.command("phone","1","enroll") == 0
    tick(7000)
    assert im.call("hike_pairing_accept", phones[0], 0) == 0
    assert claim(0,b"112345") != 6
    assert claim(3) != 6
    bonded[0] = False
    assert claim(0) != 6
    bonded[0] = True
    levels[0] = 1
    assert claim(0) != 6
    levels[0] = 2
    assert claim(0) == 6
    assert tag.command("phone","1","enroll") != 0
    assert tag.command("phone","2","enroll") == 0
    assert im.call("hike_pairing_accept", phones[0], 0) != 0
    assert claim(0) != 6
    assert claim(1) == 6
    assert tag.command("hike","on") == 0
    assert not tick(8000, 0, 1) and mask == 3
    assert not tick(9000, 1) and mask == 2
    assert not tick(200000, 1)
    assert tag.command("radio","915000000","125","9","5") == 0
    assert tag.command("owner",OWNER.hex()) == 0
    assert tag.command("on") == 0
    tick(200500, 1)
    tag.step(200600, (0,0))
    assert len(tag.sent) == 1
    assert not tick(201000, 2, 3) and mask == 0 and phones[2] in disconnected
    assert not tick(380999)
    assert tick(381000) and tag.gps
    tag.step(381100, (0,0))
    assert len(tag.sent) == 2  # Accelerated loss check, before the ordinary 15-minute interval.
    levels[0] = 1
    assert tick(382000, 0)
    levels[0] = 2
    assert not tick(383000, 0)
    assert tag.command("hike","timeout","6") == 0
    assert not tick(384000)
    assert tick(390000)
    assert tag.command("hike","off") == 0
    assert not tick(391000)
    assert tag.command("hike","on") == 0
    tag = Tag(elf, COLLAR, tag.settings)
    setup()
    assert not tick(500, 0, 1) and mask == 3
    assert not tick(1000)
    assert tick(7000)
    assert tag.command("phone","1","forget") == 0
    assert tick(8000, 0) and mask == 0
    assert not tick(9000, 1) and mask == 2
    assert tag.command("phone","2","forget") == 0
    assert not tick(16000)
    assert tag.command("hike","on") != 0
    assert tag.command("phone","1","enroll") == 0
    assert not tick(136000)
    assert claim(2) != 6
    print(f"{elf}: PASS tune repeat, mute, preview, owner enrollment, two phones, loss, return and persistence")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path, nargs="+")
    for path in parser.parse_args().elf:
        check(path)
