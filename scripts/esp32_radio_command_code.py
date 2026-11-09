"""Keep the pinned RadioLib command path resident on opted-in ESP32 boards.

Copy selected library files into the build directory; never modify a shared
library/framework installation. Command bytes, BUSY waits and status checks
are unchanged. This does not make the complete call chain cache-disabled safe.
"""
from pathlib import Path
import hashlib

SOURCES = {'Module.cpp': ('6899ee39319d95fe48289b3756ed00455501456d504425ab5ca7b561272ddc05',
                [('int16_t Module::SPIreadStream(', 2),
                 ('int16_t Module::SPIwriteStream(', 2),
                 ('int16_t Module::SPIcheckStream(', 1),
                 ('int16_t Module::SPItransferStream(', 1)]),
 'hal/Arduino/ArduinoHal.cpp': ('018c2555fd5f039e1b9159eded9c18bcdce19f72cadd6a929842d55d111160d1',
                                [('void inline ArduinoHal::digitalWrite(', 1),
                                 ('uint32_t inline ArduinoHal::digitalRead(', 1),
                                 ('void inline ArduinoHal::delayMicroseconds(', 1),
                                 ('RadioLibTime_t inline ArduinoHal::millis(', 1),
                                 ('void inline ArduinoHal::yield(', 1),
                                 ('void inline ArduinoHal::spiBeginTransaction(', 1),
                                 ('void inline ArduinoHal::spiEndTransaction(', 1)]),
 'modules/SX126x/SX126x_commands.cpp': ('11b61c1c892cb84e04221bc0a4abc9ffbd2a69ddb753eb4a93121b0bceec2904',
                                        [('int16_t SX126x::setFs(', 1),
                                         ('int16_t SX126x::setRx(', 1),
                                         ('int16_t SX126x::clearIrqStatus(', 1),
                                         ('int16_t SX126x::setRfFrequency(', 1),
                                         ('int16_t SX126x::setModulationParams(', 1)]),
 'modules/SX126x/SX126x_config.cpp': ('25406680f5972b69c0e16e4eaf2b98db27d559bd292a4192e6147a1165298ef4',
                                      [('int16_t SX126x::setFrequencyRaw(', 1)]),
 'modules/SX126x/SX126x.cpp': ('b4b82318bd68b45bfb1832698a7f33f2a0a22b5f50e0d0fdbff1a9fd87033b87',
                               [('uint32_t SX126x::getIrqFlags(', 1),
                                ('int16_t SX126x::SPIparseStatus(', 1),
                                ('RadioLibTime_t SX126x::calculateTimeOnAir(', 1)]),
 'modules/SX126x/SX1262.cpp': ('c2899a897bb05a717873d2645cc3fa3e5e5f2db3a116c667263a00541cb7e3e6',
                               [('int16_t SX1262::setFrequency(', 2)])}


def enabled(env):
    defines = {str(d[0]): str(d[1]) if len(d) > 1 else "1"
               for d in env.get("CPPDEFINES", []) if isinstance(d, (tuple, list))}
    defines.update({d: "1" for d in env.get("CPPDEFINES", []) if isinstance(d, str)})
    return (defines.get("ESP32_PLATFORM", defines.get("ESP32", "0")) != "0"
            and defines.get("MC_ESP32_RADIO_COMMAND_CODE") == "1")


def transform(source, key):
    digest, methods = SOURCES[key]
    if hashlib.sha256(source.encode()).hexdigest() != digest:
        raise RuntimeError("Radio command residency: pinned RadioLib source changed: " + key)
    for signature, count in methods:
        if source.count(signature) != count:
            raise RuntimeError("Radio command residency: signature changed: " + signature)
        source = source.replace(signature, "IRAM_ATTR " + signature)
    return '#include <esp_attr.h>\n' + source


def replace_source(env, node):
    if not enabled(env):
        return node
    src = Path(node.srcnode().get_abspath())
    key = next((k for k in SOURCES if str(src).endswith("/RadioLib/src/" + k)), None)
    if key is None:
        return node
    body = transform(src.read_text(), key)
    dest = Path(env.subst("$BUILD_DIR")) / "resident-radio" / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.read_text() != body:
        dest.write_text(body)
    env.AppendUnique(CPPPATH=[str(src.parent)])
    return env.File(str(dest))


if "Import" in globals():
    Import("env")
    env.AddBuildMiddleware(replace_source, "*RadioLib*src*.cpp")
