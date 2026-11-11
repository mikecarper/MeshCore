#!/usr/bin/env python3
"""Keep the boot-only FS view outside identity writes and asynchronous tasks."""
from pathlib import Path
import configparser
import re
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("simple_repeater", "simple_room_server")


def role_sources(role):
    path = ROOT / "examples" / role
    return (extract_braced((path / "main.cpp").read_text(), "void setup()"),
            extract_braced((path / "MyMesh.cpp").read_text(),
                           "void MyMesh::begin(FILESYSTEM *fs"))


def require_safe_lifecycle(main, mesh):
    # Cache contents witness the completed durable identity, including recovery
    # and a possible first write. The failed-identity path returns beforehand.
    identity = main.index("if (!identity_ready)")
    failure = extract_braced(main, "if (!identity_ready)")
    assert "board.reboot();" in failure and "return;" in failure
    inventory = main.index("static mesh::Esp32BootFileSystem boot_fs(*fs);")
    assert identity + len(failure) < inventory
    assert inventory < main.index("fs = &boot_fs;") < main.index("boot_fs.beginInventory();")
    assert main.index("boot_fs.beginInventory();") < main.index("the_mesh.begin(fs);")
    assert main[main.rfind("#if", 0, inventory):].startswith("#if defined(ESP32_PLATFORM)")
    # The main fallback runs before boot completion; MyMesh must end earlier
    # because its bridge may already own a task when begin() returns.
    assert main.index("mesh::endEsp32BootFileInventory();") < main.index("board.onBootComplete();")
    stop = mesh.index("mesh::endEsp32BootFileInventory();")
    assert mesh[mesh.rfind("#if", 0, stop):].startswith("#if defined(ESP32_PLATFORM)")
    for token in ("new MQTTBridge(", "active_bridge->begin();", "bridge->begin();",
                  "beginRS232Bridge()", "setEspNowBridgeState(true)",
                  "startWebConfig(false, wc_reply)"):
        if token in mesh:
            assert stop < mesh.index(token), token
    assert mesh.index("_cli.loadPrefs(_fs);") < stop
    assert mesh.index("acl.load(_fs, self_id);") < stop


class BootFileLifecycleTests(unittest.TestCase):
    def test_ordinary_esp32_role_filters_include_the_inventory_implementation(self):
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read([str(ROOT / "platformio.ini"),
                     *(str(path) for path in sorted((ROOT / "variants").glob("*/platformio.ini")))])

        def resolve(section, option):
            if option in config[section]:
                value = config[section][option]
            else:
                parent = config[section].get("extends", "").split(",")[0].strip()
                assert parent, (section, option)
                return resolve(parent, option)
            return re.sub(r"\$\{([^}]+)\}",
                          lambda match: resolve(*match.group(1).rsplit(".", 1)), value)

        targets = ("env:Xiao_C6_repeater_", "env:Heltec_v2_repeater",
                   "env:heltec_v4_room_server", "env:Xiao_S3_WIO_repeater")
        for target in targets:
            with self.subTest(target=target):
                source_filter = resolve(target, "build_src_filter")
                assert "+<helpers/esp32/BootFileSystem.cpp>" in source_filter

    def test_both_roles_keep_static_view_after_identity_and_before_tasks(self):
        for role in ROLES:
            with self.subTest(role=role):
                require_safe_lifecycle(*role_sources(role))

    def test_missing_task_boundary_is_detected(self):
        for role in ROLES:
            main, mesh = role_sources(role)
            mesh = mesh.replace("mesh::endEsp32BootFileInventory();", "")
            with self.subTest(role=role), self.assertRaises((ValueError, AssertionError)):
                require_safe_lifecycle(main, mesh)

    def test_short_lived_view_is_detected(self):
        for role in ROLES:
            main, mesh = role_sources(role)
            main = main.replace("static mesh::Esp32BootFileSystem", "mesh::Esp32BootFileSystem")
            with self.subTest(role=role), self.assertRaises((ValueError, AssertionError)):
                require_safe_lifecycle(main, mesh)

    def test_inventory_before_identity_is_detected(self):
        for role in ROLES:
            main, mesh = role_sources(role)
            token = "static mesh::Esp32BootFileSystem boot_fs(*fs);"
            main = main.replace(token, "").replace("void setup() {", "void setup() {\n" + token)
            with self.subTest(role=role), self.assertRaises((ValueError, AssertionError)):
                require_safe_lifecycle(main, mesh)

    def test_missing_boot_complete_fallback_is_detected(self):
        for role in ROLES:
            main, mesh = role_sources(role)
            main = main.replace("mesh::endEsp32BootFileInventory();", "")
            with self.subTest(role=role), self.assertRaises((ValueError, AssertionError)):
                require_safe_lifecycle(main, mesh)


if __name__ == "__main__":
    unittest.main()
