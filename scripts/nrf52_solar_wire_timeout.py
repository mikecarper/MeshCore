"""Bound Wire master event waits in SolarXiao charger builds."""

from pathlib import Path


def patch_source(source):
    include = '#include "Wire.h"'
    if source.count(include) != 1:
        raise ValueError("Unexpected nRF52 Wire include")
    source = source.replace(include, include + '\n#include <helpers/nrf52/SolarXiaoWireTimeout.h>')
    for direction, method, start, failure in (
        ("RX", "requestFrom(uint8_t address, size_t quantity, bool stopBit)", "STARTRX", 0),
        ("TX", "endTransmission(bool stopBit)", "STARTTX", 4),
    ):
        begin = source.index("uint8_t TwoWire::" + method)
        end = source.index("\n}", begin)
        body = source[begin:end]
        trigger = f"_p_twim->TASKS_{start} = 0x1UL;"
        if body.count(trigger) != 1:
            raise ValueError(f"Unexpected Wire {start} task")
        body = body.replace(trigger, "mesh::solar::WireWait timeout;\n  " + trigger)
        for event, accept_error in ((direction+"STARTED", True), ("LAST"+direction, True),
                                    ("STOPPED", False), ("SUSPENDED", False)):
            register = "_p_twim->EVENTS_" + event
            old = f"while(!{register}" + (" && !_p_twim->EVENTS_ERROR" if accept_error else "") + ");"
            if body.count(old) != 1:
                raise ValueError(f"Unexpected Wire {method} wait for {event}")
            flag = "true" if accept_error else "false"
            body = body.replace(old, f"if (!timeout.wait(_p_twim, {register}, {flag})) return {failure};")
        source = source[:begin] + body + source[end:]
    return source


def replace_wire(build_env, node):
    original = Path(node.srcnode().get_abspath())
    patched = patch_source(original.read_text(encoding="utf-8"))
    dest = Path(build_env.subst("$BUILD_DIR")) / "solar-wire" / original.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.read_text(encoding="utf-8") != patched:
        dest.write_text(patched, encoding="utf-8")
    build_env.AppendUnique(CPPPATH=[str(original.parent), build_env.subst("$PROJECT_DIR/src")])
    return build_env.File(str(dest))


if "Import" in globals():
    Import("env")
    defines = env.ParseFlags(env.get("BUILD_FLAGS", []))["CPPDEFINES"]
    if any(isinstance(item, (tuple, list)) and list(item) == ["MESH_SOLARXIAO_POWER", "1"]
           for item in defines):
        env.AddBuildMiddleware(replace_wire, "*libraries*Wire*Wire_nRF52.cpp")
