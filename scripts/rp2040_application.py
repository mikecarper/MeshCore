"""Verify that an RP2040 UF2 carries the exact application offered as BIN."""
from pathlib import Path
import struct

BLOCK_BYTES = 512
PAYLOAD_BYTES = 256
FLASH_BASE = 0x10000000
FLASH_END = 0x11000000  # RP2040's maximum 16 MiB XIP flash window.
FAMILY = 0xE48BFF56


def read_application(uf2_path: Path, bin_path: Path) -> bytes:
    """Return real flash bytes, excluding UF2 headers and alignment padding.

    Both public formats must agree. ELF strings, unused sector bytes, and
    bytes outside the BIN's application extent cannot supply capability proof.
    The board's ordinary linker/size checks retain its stricter flash limit.
    """
    size = uf2_path.stat().st_size
    if not size or size % BLOCK_BYTES or size > (FLASH_END - FLASH_BASE) * 2:
        raise ValueError("RP2040 UF2 has an invalid sector extent")
    application_size = bin_path.stat().st_size
    if not 0 < application_size <= FLASH_END - FLASH_BASE:
        raise ValueError("RP2040 BIN is empty or exceeds the XIP flash window")
    data = uf2_path.read_bytes()
    if len(data) != size:
        raise ValueError("RP2040 UF2 changed during application qualification")
    count = size // BLOCK_BYTES
    payload = bytearray()
    for index in range(count):
        offset = index * BLOCK_BYTES
        magic0, magic1, flags, address, length, number, total, family = struct.unpack_from("<8I", data, offset)
        trailer = struct.unpack_from("<I", data, offset + 508)[0]
        if (magic0, magic1, trailer) != (0x0A324655, 0x9E5D5157, 0x0AB16F30):
            raise ValueError("RP2040 UF2 has an invalid sector header/trailer")
        if flags != 0x2000 or family != FAMILY:
            raise ValueError("RP2040 UF2 must contain only main-flash blocks with the RP2040 family")
        if length != PAYLOAD_BYTES or number != index or total != count:
            raise ValueError("RP2040 UF2 has inconsistent payload lengths or block numbers")
        if address != FLASH_BASE + index * PAYLOAD_BYTES or address + length > FLASH_END:
            raise ValueError("RP2040 UF2 has gaps, overlaps, or addresses outside its XIP flash window")
        payload.extend(data[offset + 32:offset + 32 + length])
    application = bin_path.read_bytes()
    tail = payload[application_size:]
    if len(payload) < application_size or len(tail) >= PAYLOAD_BYTES or payload[:application_size] != application:
        raise ValueError("RP2040 UF2 application does not match the packaged BIN")
    if tail and set(tail) not in ({0}, {255}):
        raise ValueError("RP2040 UF2 has nonuniform application-alignment padding")
    return bytes(payload[:application_size])
