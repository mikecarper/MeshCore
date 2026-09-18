"""Pinned Google Brotli helper for the browser-decoded WebConfig asset."""

from __future__ import annotations

BROTLI_VERSION = "1.2.0"
QUALITY = 11
# Maximum RFC 7932/browser-compatible Brotli window (16 MiB). The page is
# smaller than the default 4 MiB window, but pin the requested maximum anyway.
LGWIN = 24

try:
    import brotli
except ImportError as error:
    raise RuntimeError(
        "Google Brotli 1.2.0 is required to generate the MeshCore WebConfig "
        "asset. Install it with 'python -m pip install -r "
        "requirements-build.txt'."
    ) from error

if brotli.__version__ != BROTLI_VERSION:
    raise RuntimeError(
        "MeshCore requires Google Brotli %s, but %s is installed. Run "
        "'python -m pip install -r requirements-build.txt'."
        % (BROTLI_VERSION, brotli.__version__)
    )


def compress_html(data: bytes) -> bytes:
    """Return a deterministic, maximum-quality RFC 7932 Brotli text stream."""
    return brotli.compress(data, mode=brotli.MODE_TEXT, quality=QUALITY,
                           lgwin=LGWIN)
