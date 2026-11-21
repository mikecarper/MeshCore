"""Package only the RAK application and check the reserved storage boundary."""
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from check_nrf52_uf2 import check_uf2


def write_tracker_uf2(build, output, *, app_limit=0xC3000):
    binary = build / "zephyr/zephyr.bin"
    image = binary.read_bytes()
    base = 0x26000
    stack, reset = struct.unpack_from("<II", image)
    if not (0x20000000 < stack <= 0x20040000 and stack % 8 == 0
            and reset & 1 and base <= (reset & ~1) < base + len(image) <= app_limit):
        raise SystemExit("Invalid RAK Tag application vectors or flash bounds")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / "bin/uf2conv/uf2conv.py"), str(binary),
                    "-c", "-b", hex(base), "-f", "0xADA52840", "-o", str(output)], check=True)
    start, end, blocks = check_uf2(output, app_end=app_limit)
    print(f"RAK Tag UF2 verified, {blocks} blocks at {start:#x}..{end:#x}")
