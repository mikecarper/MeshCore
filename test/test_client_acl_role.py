"""Role-safe ACL layout and unchanged production persistence, without PlatformIO."""

import configparser
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("client_acl_role", ROOT / "scripts/client_acl_role.py")
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)
DEFINE = POLICY.DEFINITION
FIXTURE = ROOT / "test/fixtures/client_acl_spiffs"


class ClientAclRoleTests(unittest.TestCase):
    def test_only_complete_repeater_role_qualifies(self):
        for value in ("+<../examples/simple_repeater>", "+<../examples/simple_repeater/*.cpp>",
                      ["+<*.cpp>", "+<..\\examples\\simple_repeater\\*.cpp>"]):
            self.assertEqual(POLICY.role_policy(value), {DEFINE: 1})
        for role in POLICY.OTHER_ROLES:
            self.assertEqual(POLICY.role_policy("+<../examples/" + role + ">"), {})
            self.assertEqual(POLICY.role_policy("+<../examples/simple_repeater> "
                                               "+<../examples/" + role + ">"), {})
        for value in ("", "+<*>", "+<../examples/simple_repeater/main.cpp>",
                      "+<../examples/simple_repeater> -<../examples/simple_repeater/MyMesh.cpp>"):
            self.assertEqual(POLICY.role_policy(value), {})

    def test_ordered_source_selection_and_explicit_overrides(self):
        role = "+<../examples/simple_repeater>"
        self.assertEqual(POLICY.role_policy(role + " -<../examples/simple_repeater>"), {})
        self.assertEqual(POLICY.role_policy(role + " -<../examples/simple_repeater> " + role), {DEFINE: 1})
        for flag in ("-D" + DEFINE + "=0", "-D" + DEFINE + "=1", "-U" + DEFINE):
            self.assertEqual(POLICY.role_policy(role, flag), {})
        self.assertEqual(POLICY.role_policy(role, cppdefines=[(DEFINE, 1)]), {})
        self.assertEqual(POLICY.role_policy(role, "-DCOMPANION_RADIO_FULL=1"), {})
        for source in ("", "+<../examples/simple_room_server>", "+<*>"):
            with self.assertRaises(ValueError):
                POLICY.role_policy(source, "-D" + DEFINE + "=1")
        for value in ("true", "2", "-1"):
            with self.assertRaises(ValueError):
                POLICY.role_policy(role, "-D" + DEFINE + "=" + value)

    def test_one_common_definition_not_header_local(self):
        class Env(dict):
            def AppendUnique(self, **options): self["appended"] = options
            def GetProjectOption(self, *args): raise AssertionError("effective filter required")
        env = Env(SRC_FILTER="+<../examples/simple_repeater>", BUILD_FLAGS="", CPPDEFINES=[])
        POLICY.install(env)
        self.assertEqual(env["appended"], {"CPPDEFINES": [(DEFINE, 1)]})
        env["SRC_FILTER"] = "+<../examples/simple_sensor>"
        del env["appended"]
        POLICY.install(env)
        self.assertNotIn("appended", env)

    def test_every_platform_base_installs_role_hook(self):
        ini = (ROOT / "platformio.ini").read_text()
        for name in ("esp32_base", "nrf52_base", "rp2040_base", "stm32_base"):
            section = ini.split("[" + name + "]", 1)[1].split("\n[", 1)[0]
            self.assertIn("pre:scripts/client_acl_role.py", section)

    def test_real_board_roles_and_inherited_hooks(self):
        config = configparser.ConfigParser(interpolation=None, strict=False,
                                           inline_comment_prefixes=(";",))
        config.read([str(ROOT / "platformio.ini")]
                    + [str(path) for path in sorted((ROOT / "variants").glob("*/platformio.ini"))])
        def option(section, key):
            if config.has_option(section, key):
                raw = config.get(section, key)
            else:
                parents = re.split(r"[,\n]", config.get(section, "extends", fallback=""))
                raw = next((option(parent.strip(), key) for parent in parents
                            if parent.strip() and option(parent.strip(), key)), "")
            return re.sub(r"\$\{([^}]+)\}", lambda match: option(*match[1].rsplit(".", 1)), raw)
        platforms = set()
        repeaters = 0
        for section in config.sections():
            if not section.startswith("env:") or section == "env:native": continue
            filt = option(section, "build_src_filter")
            definitions = POLICY.role_policy(filt, option(section, "build_flags"))
            if definitions:
                repeaters += 1
                platforms.add(option(section, "platform"))
                self.assertIn("pre:scripts/client_acl_role.py", option(section, "extra_scripts"), section)
        self.assertGreater(repeaters, 100)
        self.assertGreaterEqual(len(platforms), 4)

    def test_compact_and_complete_acl_have_identical_persistence(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        source = r'''
#define main full_fixture_main
#include "test_client_acl_spiffs.cpp"
#undef main
#include <type_traits>
static_assert(std::is_trivially_copyable<ClientInfo>::value, "ACL remains copyable");
#if MESH_CLIENT_REPEATER_ONLY
static_assert(sizeof(((ClientInfo*)0)->extra) == 4, "only persisted sync field retained");
#else
static_assert(sizeof(((ClientInfo*)0)->extra.sensor.min_deltas) == 14, "sensor state retained");
static_assert(sizeof(((ClientInfo*)0)->extra.room.pending_ack) == 4, "room state retained");
static_assert(sizeof(((ClientInfo*)0)->extra.room) <= sizeof(((ClientInfo*)0)->extra.sensor),
              "topic delivery must fit the existing shared union");
#endif
int main() {
  CHECK(full_fixture_main() == 0); // replay, corruption, OOM and rollback coverage
  FakeFilesystem fs;
  ClientACL acl;
  acl.load(&fs, SELF);
  for (int i = 0; i < MAX_CLIENTS; ++i) {
    uint8_t key[PUB_KEY_SIZE] = {}; key[0] = uint8_t(i + 1);
    auto* c = acl.putClient(mesh::Identity(key), PERM_ACL_ADMIN);
    CHECK(c);
    c->extra.room.sync_since = 0x12345678u + i;
    c->last_timestamp = 100 + i;
    c->out_path_is_persistable = true;
    c->out_path_len = 1; c->out_path[0] = uint8_t(i + 3);
    c->alt_path_len = 1; c->alt_path[0] = uint8_t(i + 5);
#if !MESH_CLIENT_REPEATER_ONLY
    c->extra.room.pending_ack = 99; c->extra.room.push_failures = 3;
    c->extra.room.topic_seen_revision = 7;
    c->extra.room.pending_topic_revision = 8;
    c->extra.room.topic_failures = 2;
#endif
  }
  CHECK(acl.save(&fs));
  ClientACL restored;
  restored.load(&fs, SELF);
  CHECK(restored.getNumClients() == MAX_CLIENTS);
  for (int i = 0; i < MAX_CLIENTS; ++i) {
    const auto* c = restored.getClientByIdx(i);
    CHECK(c->extra.room.sync_since == 0x12345678u + i);
    CHECK(c->out_path_len == 1 && c->out_path[0] == i + 3);
    CHECK(c->alt_path_len == 1 && c->alt_path[0] == i + 5);
    CHECK(c->isAdmin());
#if !MESH_CLIENT_REPEATER_ONLY
    CHECK(c->extra.room.topic_seen_revision == 0);
    CHECK(c->extra.room.pending_topic_revision == 0);
    CHECK(c->extra.room.topic_failures == 0);
#endif
  }
  std::printf("SIZE:%zu\nCONTACTS:", sizeof(ClientInfo));
  for (auto byte : fs.files["/s_contacts"]) std::printf("%02x", unsigned(byte));
  std::puts("");
}
'''
        results = []
        with tempfile.TemporaryDirectory(prefix="compact-client-acl-") as directory:
            work = Path(directory)
            (work / "test.cpp").write_text(source)
            for compact in (0, 1):
                binary = work / ("acl-" + str(compact))
                command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-DESP32=1", "-DESP32_PLATFORM=1",
                           "-D" + DEFINE + "=" + str(compact), "-I" + str(FIXTURE / "mocks"),
                           "-I" + str(FIXTURE), "-I" + str(ROOT / "src"), str(work / "test.cpp"), "-o", str(binary)]
                command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                built = subprocess.run(command, text=True, capture_output=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                results.append(run.stdout)
        full, compact = results
        self.assertEqual(full.split("CONTACTS:")[1], compact.split("CONTACTS:")[1])
        sizes = [int(re.search(r"SIZE:(\d+)", result)[1]) for result in results]
        self.assertGreaterEqual(sizes[0] - sizes[1], 36)


if __name__ == "__main__": unittest.main()
