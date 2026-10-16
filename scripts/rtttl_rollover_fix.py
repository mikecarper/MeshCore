"""Fix reviewed RTTTL timers in private build copies, never in libdeps.

An absolute millis() comparison can truncate a tone at rollover or keep it
busy until the next wrap, blocking queued speech. Use a signed 32-bit delta
and make the initial deadline due now, including in the upper counter half.
As with other Arduino deadline comparisons, waits must remain below 2^31 ms;
the firmware's note durations and repeat gaps are far below that limit.
"""
import hashlib
from pathlib import Path


# Each entry binds the actual source, its matching header, and the result.
# The recipes allow both releases; do not silently upgrade cached 1.3.0.
REVIEWED = {
    "b50c159c8806d177398972bf8f94ca762532a855b60cb1d3aa9c3ebf021423ca": (
        "abea0a4870e58222fdefd1e88d291136518912e83880af30d1c85d39d7bd5cc1",
        "29204341d99b8adeef501c0d4cb47a4137534ebe6a30f2e6b3032d558aeef802",
    ),
    "d4fddfa663c8adeacea3b1daf56ec5fbe63ecaaf68e5477ac96dc4fb2e28d363": (
        "5a33eec77e2ba53ed9b83a8242633b8ae11873d3b4f8cfd257abbb2eb61b6220",
        "e7710c4e3309b25517f5bace697c0c2574a5ce219f3cb156d352f739c178d75f",
    ),
}
ORIGINAL_BEGIN = "  noteDelay = 0;\n"
PATCHED_BEGIN = "  noteDelay = millis();\n"
ORIGINAL_COMPARE = "  if (m < noteDelay)\n"
PATCHED_COMPARE = "  if (static_cast<int32_t>(static_cast<uint32_t>(m - noteDelay)) < 0)\n"


def digest(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def reviewed_pair(source):
    source_hash = digest(source.replace("\r\n", "\n"))
    if source_hash in REVIEWED:
        return REVIEWED[source_hash]
    for header_hash, patched_hash in REVIEWED.values():
        if source_hash == patched_hash:
            return header_hash, patched_hash
    raise RuntimeError("RTTTL timing fix: changed reviewed source; review dependency update")


def patched_source(source):
    source = source.replace("\r\n", "\n")
    _, patched_hash = reviewed_pair(source)
    if digest(source) == patched_hash:
        return source
    if source.count(ORIGINAL_BEGIN) != 1 or source.count(ORIGINAL_COMPARE) != 1:
        raise RuntimeError("RTTTL timing fix: changed reviewed timing statements")
    source = source.replace(ORIGINAL_BEGIN, PATCHED_BEGIN, 1)
    source = source.replace(ORIGINAL_COMPARE, PATCHED_COMPARE, 1)
    # Version 1.4.0 has a degree symbol only in a disabled debug string.
    # Keep generated project-private source ASCII without changing playback.
    source = source.replace("\u00b0", "")
    if digest(source) != patched_hash:
        raise RuntimeError("RTTTL timing fix: unexpected transform result")
    source.encode("ascii")
    return source


def write_if_changed(path, content):
    if not path.exists() or path.read_text(encoding="ascii") != content:
        path.write_text(content, encoding="ascii")


def replace_rtttl_source(build_env, node):
    source = Path(node.srcnode().get_abspath())
    if source.name != "NonBlockingRtttl.cpp" or source.parent.name != "src":
        return node
    original = source.read_text(encoding="utf-8")
    header_hash, _ = reviewed_pair(original)
    try:
        header = source.with_suffix(".h").read_text(encoding="ascii").replace("\r\n", "\n")
    except (OSError, UnicodeError) as error:
        raise RuntimeError("RTTTL timing fix: missing or changed reviewed header") from error
    if digest(header) != header_hash:
        raise RuntimeError("RTTTL timing fix: changed reviewed header; review dependency update")
    patched = patched_source(original)
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-rtttl" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A quoted include must resolve to the reviewed matching header, not a
    # same-named library earlier in the build clone's include path.
    write_if_changed(destination.with_suffix(".h"), header)
    write_if_changed(destination, patched)
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(replace_rtttl_source, "*NonBlockingRtttl.cpp")


if "Import" in globals():
    Import("env")
    install(env)
