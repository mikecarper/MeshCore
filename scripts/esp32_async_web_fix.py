"""Build private AsyncTCP fixes and the current vendored OTA implementation."""
import hashlib
from pathlib import Path


PINNED_ASYNCTCP_SHA256 = "27e3e22a6030cc5c9c9f32f28d0a3a36189b1ea6a5fa270777b2d2790a89c365"
ORIGINAL_BIND = "  msg->err = tcp_bind(pcb, msg->bind.addr, msg->bind.port);"
REUSABLE_BIND = """  // A stopped HTTP listener may still have completed connections in
  // TIME_WAIT. Permit immediate rebinding during WebConfig/OTA handoffs.
#if SO_REUSE
  ip_set_option(pcb, SOF_REUSEADDR);
#endif
""" + ORIGINAL_BIND


def patched_async_tcp(source):
    source = source.replace("\r\n", "\n")
    if (hashlib.sha256(source.encode("ascii")).hexdigest() != PINNED_ASYNCTCP_SHA256
            or source.count(ORIGINAL_BIND) != 1):
        raise RuntimeError("AsyncTCP restart fix: changed pinned source; review library update")
    return source.replace(ORIGINAL_BIND, REUSABLE_BIND, 1)


def replace_async_source(build_env, node):
    source = Path(node.srcnode().get_abspath())
    if source.name == "AsyncTCP.cpp":
        content = patched_async_tcp(source.read_text(encoding="utf-8"))
        include_dir = source.parent
    elif source.name == "AsyncElegantOTA.cpp":
        # file:// dependencies can retain an earlier copy in .pio/libdeps.
        # Compile this checkout's vendored source without editing that cache.
        include_dir = Path(build_env.subst("$PROJECT_DIR")) / "arch/esp32/AsyncElegantOTA/src"
        content = (include_dir / source.name).read_text(encoding="ascii")
    else:
        return node
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-esp32-web" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.name == "AsyncElegantOTA.cpp":
        # Quoted includes resolve beside this private source, ahead of any
        # stale library include path inherited by PlatformIO's build clone.
        for name in ("AsyncElegantOTA.h", "Hash.h", "elegantWebpage.h"):
            header = (include_dir / name).read_bytes()
            target = destination.parent / name
            if not target.exists() or target.read_bytes() != header:
                target.write_bytes(header)
    if not destination.exists() or destination.read_text(encoding="ascii") != content:
        destination.write_text(content, encoding="ascii")
    build_env.PrependUnique(CPPPATH=[str(include_dir)])
    if source.name == "AsyncElegantOTA.cpp":
        # PlatformIO compiles libraries in cloned environments which need not
        # inherit the application src include path. The current uploader uses
        # the shared runtime management credential header from that directory.
        build_env.AppendUnique(CPPPATH=[build_env.subst("$PROJECT_SRC_DIR")])
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(replace_async_source, "*AsyncTCP*src*AsyncTCP.cpp")
    build_env.AddBuildMiddleware(replace_async_source, "*AsyncElegantOTA*src*AsyncElegantOTA.cpp")


if "Import" in globals():
    Import("env")
    install(env)
