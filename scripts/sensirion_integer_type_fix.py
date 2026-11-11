"""Rename Sensirion Core's integer-width enum in private build copies.

Sensirion Core 0.7.3 and SNMP_Agent both declare global IntegerType types.
The former is an integer-width enum; the latter is a BER class. The different
definitions violate the C++ one-definition rule when both libraries are linked.
Keep the enum's width, values and decoding behavior and only give it a unique
name. Installed dependencies are never modified, including shared libdeps.
"""

import hashlib
from pathlib import Path
import re


PINNED_VERSION = "0.7.3"
SOURCE_HASHES = {
    "SensirionRxFrame.h": "b76524da88d2db4062e81fdc21bd54a6cad8743ab9c3f097652394d593cc13f1",
    "SensirionRxFrame.cpp": "a28fc72445c64fc13a4a214e4f7173795e6bbc6097d9d36041e88f5803d2a43f",
}
TOKEN_COUNTS = {"SensirionRxFrame.h": 2, "SensirionRxFrame.cpp": 1}
_OVERLAYS = {}


def patched_source(name, content):
    content = content.replace("\r\n", "\n")
    if (name not in SOURCE_HASHES or
            hashlib.sha256(content.encode("ascii")).hexdigest() != SOURCE_HASHES[name]):
        raise RuntimeError("Sensirion type fix: changed reviewed source; review dependency update")
    if len(re.findall(r"\bIntegerType\b", content)) != TOKEN_COUNTS[name]:
        raise RuntimeError("Sensirion type fix: changed reviewed integer-width declarations")
    return re.sub(r"\bIntegerType\b", "SensirionIntegerType", content)


def private_overlay(library, build_dir):
    library, build_dir = Path(library), Path(build_dir)
    metadata = (library / "library.properties").read_text(encoding="ascii")
    if (re.findall(r"^name=(.*)$", metadata, re.MULTILINE) != ["Sensirion Core"] or
            re.findall(r"^version=(.*)$", metadata, re.MULTILINE) != [PINNED_VERSION]):
        raise RuntimeError("Sensirion type fix: unreviewed library version")
    original = library / "src"
    files = {}
    for path in original.rglob("*"):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise RuntimeError("Sensirion type fix: unsafe library source")
        if path.is_file():
            files[path.relative_to(original)] = path.read_bytes()
    for name in SOURCE_HASHES:
        if Path(name) not in files:
            raise RuntimeError("Sensirion type fix: missing reviewed source")
        files[Path(name)] = patched_source(name, files[Path(name)].decode("ascii")).encode("ascii")
    destination = build_dir / "patched-sensirion-core"
    destination.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.is_file() or output.read_bytes() != content:
            output.write_bytes(content)
    return destination


def replace_sensirion_source(build_env, node):
    # Middleware runs on each library's cloned build environment. Prepend the
    # complete private header tree there as well as for application sources;
    # a patched RxFrame.cpp alone would leave its consumers using the old enum.
    library = (Path(build_env.subst("$PROJECT_LIBDEPS_DIR")) /
               build_env.subst("$PIOENV") / "Sensirion Core")
    if not library.is_dir():
        return node
    build_dir = Path(build_env.subst("$BUILD_DIR"))
    key = (str(library), str(build_dir))
    if key not in _OVERLAYS:
        _OVERLAYS[key] = private_overlay(library, build_dir)
    overlay = _OVERLAYS[key]
    build_env.PrependUnique(CPPPATH=[str(overlay)])
    source = Path(node.srcnode().get_abspath())
    try:
        relative = source.relative_to(library / "src")
    except ValueError:
        return node
    # Every Core source resolves its quoted sibling includes in the overlay.
    # Other libraries find the same headers via the first include directory.
    return build_env.File(str(overlay / relative))


def install(build_env):
    build_env.AddBuildMiddleware(replace_sensirion_source)


if "Import" in globals():
    Import("env")
    install(env)
