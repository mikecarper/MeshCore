"""Compile bounded peripheral drivers in the environment's build directory."""
from pathlib import Path
import re
import subprocess
import tempfile


def patched_copy(original, patch, destination):
    # Use Git's context checks so framework updates cannot silently skip a fix.
    with tempfile.TemporaryDirectory() as directory:
        copy = Path(directory) / original.name
        copy.write_bytes(original.read_bytes().replace(b"\r\n", b"\n"))
        subprocess.run(["git", "apply", "--no-index", "--whitespace=nowarn", str(patch)],
                       cwd=directory, check=True)
        data = copy.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_bytes() != data:
        destination.write_bytes(data)


def is_esp32_2017(framework):
    version = (framework / "cores/esp32/esp_arduino_version.h").read_text()
    components = dict(re.findall(r"^#define ESP_ARDUINO_VERSION_(MAJOR|MINOR|PATCH)\s+(\d+)\s*$",
                                 version, re.MULTILINE))
    return components == {"MAJOR": "2", "MINOR": "0", "PATCH": "17"}


def install(env):
    patches = Path(env.subst("$PROJECT_DIR")) / "scripts/framework_timeouts"
    output = Path(env.subst("$BUILD_DIR")) / "peripheral-timeouts"

    def replace_source(build_env, node):
        original = Path(node.srcnode().get_abspath())
        destination = output / original.name
        patched_copy(original, patches / (original.name + ".patch"), destination)
        build_env.AppendUnique(CPPPATH=[str(original.parent), env.subst("$PROJECT_DIR/src")])
        return build_env.File(str(destination))

    platform = env.PioPlatform()
    if platform.name == "nordicnrf52":
        env.AddBuildMiddleware(replace_source, "*libraries*Wire*Wire_nRF52.cpp")
        env.AddBuildMiddleware(replace_source, "*cores*nRF5*wiring_analog_nRF52.c")
    elif platform.name == "raspberrypi":
        # Arduino-Pico normally links this translation unit from libpico.a.
        # Link our build-local object first, using the matching SDK sources.
        framework = Path(platform.get_package_dir("framework-arduinopico"))
        original = framework / "pico-sdk/src/rp2_common/hardware_i2c/i2c.c"
        directory = output / "pico-sdk"
        patched_copy(original, patches / "i2c.c.patch", directory / original.name)
        env.BuildSources(str(output / "objects"), str(directory), src_filter=["+<i2c.c>"])
    elif platform.name == "espressif32":
        framework = Path(platform.get_package_dir("framework-arduinoespressif32"))
        if is_esp32_2017(framework):
            env.AddBuildMiddleware(replace_source, "*libraries*Wire*src*Wire.cpp")
            env.AddBuildMiddleware(replace_source, "*cores*esp32*esp32-hal-i2c.c")


if "Import" in globals():
    Import("env")
    install(env)
