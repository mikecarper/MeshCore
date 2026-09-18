"""Pinned Google Zopfli helpers for static and host-generated DEFLATE data.

Zopfli writes standards-compliant DEFLATE streams, so the existing device
decoders need no additional firmware code or RAM.  It is deliberately used
only where compression happens on a development machine or host controller:
firmware assets, test vectors, and BLE mOTA transport blocks.
"""

from __future__ import annotations

ZOPFLI_VERSION = "0.4.3"

try:
    import zopfli
    from zopfli import gzip as _gzip
    from zopfli import zlib as _zlib
except ImportError as error:
    raise RuntimeError(
        "Google Zopfli 0.4.3 is required to generate MeshCore compressed "
        "assets. Install it with 'python -m pip install -r "
        "requirements-build.txt'."
    ) from error

if zopfli.__version__ != ZOPFLI_VERSION:
    raise RuntimeError(
        "MeshCore requires Google Zopfli %s, but %s is installed. Run "
        "'python -m pip install -r requirements-build.txt'."
        % (ZOPFLI_VERSION, zopfli.__version__)
    )

# Above zlib's maximum level 9: Zopfli refines the LZ77 model repeatedly.
# Fifteen iterations is the upstream binding's practical default for assets
# of this size and keeps CI/build times reasonable.
ITERATIONS = 15


def _options() -> dict[str, int | bool]:
    return {
        "numiterations": ITERATIONS,
        "blocksplitting": True,
        "blocksplittinglast": False,
        "blocksplittingmax": 15,
    }


def gzip_compress(data: bytes) -> bytes:
    """Compress *data* as deterministic RFC 1952 gzip using Google Zopfli."""
    encoded = bytearray(_gzip.compress(data, **_options()))
    # The gzip OS byte is informational. Normalize it to RFC 1952's unknown
    # value so a checked-in asset is identical across operating systems.
    if len(encoded) < 10 or encoded[:3] != b"\x1f\x8b\x08":
        raise RuntimeError("Zopfli returned an invalid gzip stream")
    encoded[9] = 0xFF
    return bytes(encoded)


def raw_deflate_compress(data: bytes) -> bytes:
    """Compress *data* to an RFC 1951 raw DEFLATE stream using Google Zopfli."""
    encoded = _zlib.compress(data, **_options())
    # A zlib wrapper is exactly a two-byte header and four-byte Adler-32
    # trailer. Removing it preserves the RFC 1951 body expected by tinf.
    if len(encoded) < 6 or (encoded[0] & 0x0F) != 8:
        raise RuntimeError("Zopfli returned an invalid zlib stream")
    return encoded[2:-4]
