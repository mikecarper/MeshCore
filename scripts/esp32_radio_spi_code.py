"""Keep the pinned Arduino-ESP32 SPI path resident on opted-in boards.

Copy selected library files into the build directory; never modify a shared
library/framework installation. SPI transfers and transaction locks are unchanged. This does not make the complete call chain cache-disabled safe.
"""
from pathlib import Path
import hashlib

SOURCES = {'libraries/SPI/src/SPI.cpp': ('94d37b33a1e80826f6efc7c05a1c0104d4563ef02d64ddcd3c813cffb5233d4e',
                               [('void SPIClass::beginTransaction(', 1),
                                ('void SPIClass::endTransaction(', 1),
                                ('void SPIClass::transferBytes(', 1),
                                ('uint32_t SPIClass::getClockDivider(', 1)]),
 'cores/esp32/esp32-hal-spi.c': ('48683c0ab06f203e98cfc26fec20215fb3d2c2824f469079a2c7aae875621289',
                                 [('void spiTransaction(', 1),
                                  ('void spiEndTransaction(', 1),
                                  ('void spiTransferBytesNL(', 1),
                                  ('uint32_t spiGetClockDiv(', 1)])}


def enabled(env):
    defines = {str(d[0]): str(d[1]) if len(d) > 1 else "1"
               for d in env.get("CPPDEFINES", []) if isinstance(d, (tuple, list))}
    defines.update({d: "1" for d in env.get("CPPDEFINES", []) if isinstance(d, str)})
    return (defines.get("ESP32_PLATFORM", defines.get("ESP32", "0")) != "0"
            and defines.get("MC_ESP32_RADIO_SPI_CODE") == "1")


def transform(source, key):
    digest, methods = SOURCES[key]
    if hashlib.sha256(source.encode()).hexdigest() != digest:
        raise RuntimeError("Radio command residency: pinned Arduino-ESP32 source changed: " + key)
    for signature, count in methods:
        if source.count(signature) != count:
            raise RuntimeError("Radio command residency: signature changed: " + signature)
        source = source.replace(signature, "IRAM_ATTR " + signature)
    return '#include <esp_attr.h>\n' + source


def replace_source(env, node):
    if not enabled(env):
        return node
    src = Path(node.srcnode().get_abspath())
    key = next((k for k in SOURCES if str(src).endswith("/" + k)), None)
    if key is None:
        return node
    body = transform(src.read_text(), key)
    dest = Path(env.subst("$BUILD_DIR")) / "resident-radio-spi" / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.read_text() != body:
        dest.write_text(body)
    env.AppendUnique(CPPPATH=[str(src.parent)])
    return env.File(str(dest))


if "Import" in globals():
    Import("env")
    env.AddBuildMiddleware(replace_source, "*")
