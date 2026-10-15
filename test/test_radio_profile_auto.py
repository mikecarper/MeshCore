"""Run the production profile CLI's RAM-only automatic ownership layer."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
// Reuse only the existing memory FS/radio fixture; run dedicated scenarios.
#define main existing_cli_main
#include "test/fixtures/radio_profiles/cli_test.cpp"
#undef main

static mesh::RadioProfileConfig candidate(float frequency = 910.5f) {
  mesh::RadioProfileConfig config;
  config.mode = mesh::RadioProfileMode::RxTx;
  config.params.freq = frequency; config.params.bw = 250;
  config.params.sf = 11; config.params.cr = 5; config.params.preamble = 16;
  config.params.protocol = mesh::RadioProfileProtocol::Meshtext;
  return config;
}

static void rewriteCRC(std::vector<uint8_t>& image) {
  uint32_t crc = 0xffffffffU;
  for (size_t i = 0; i < image.size() - 4; ++i) {
    crc ^= image[i];
    for (unsigned bit = 0; bit < 8; ++bit) crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
  }
  memcpy(image.data() + image.size() - 4, &crc, 4);
}

static void lifetime() {
  Fixture f;
  const auto config = candidate();
  assert(f.cli.canAutoSecondary());
  assert(f.cli.requestAutoSecondary(config));
  assert(f.cli.autoSecondaryActive());
  assert(f.radio.p.secondary.params == config.params);
  assert(strstr(f.cmd("get radio2"), "auto-owned"));
  const auto generation = f.radio.p.generation[1];
  for (unsigned i = 0; i < 8; ++i) {
    assert(f.cli.requestAutoSecondary(config)); f.advance(1, false);
    assert(f.radio.p.generation[1] == generation);
  }
  assert(!f.cli.requestAutoSecondary(candidate(911.5f))); // no last-request-wins conflict
  assert(f.radio.p.secondary.params == config.params);
  assert(f.fs.files.empty()); // RF adoption never writes flash
  f.cli.clearAutoSecondary();
  assert(!f.cli.autoSecondaryActive() && !f.radio.p.enabled());
  const auto cleared = f.radio.p.generation[1];
  f.cli.clearAutoSecondary(); f.advance(1, false);
  assert(f.radio.p.generation[1] == cleared && f.fs.files.empty());
  auto invalid = config; invalid.mode = mesh::RadioProfileMode::Off;
  assert(!f.cli.requestAutoSecondary(invalid));
  invalid = config; invalid.params.freq = 0;
  assert(!f.cli.requestAutoSecondary(invalid));
}

static void savedIntent() {
  Fixture f(true);
  assert(f.cli.requestAutoSecondary(candidate()));
  f.cmd("set radio2.cross off");
  assert(f.cli.savePrimaryPreamble(120));
  f.cmd("set tx.reply radio2 force");
  assert(f.cli.autoSecondaryActive());
  const auto image = f.fs.files["/radio_profiles"];
  assert(image.size() == 24 && image[2] == 2 && image[3] == 0);
  Radio reboot; mesh::RadioProfileCLI restored;
  restored.begin(&f.fs, &reboot, &f.clock, true);
  assert(!reboot.p.enabled() && restored.canAutoSecondary());
  assert(!restored.manualSecondaryConfigured());

  f.cmd("set radio2 off");
  assert(!f.cli.autoSecondaryActive() && !f.cli.canAutoSecondary());
  assert(f.fs.files["/radio_profiles"][3] == 0x80);
  f.advance(2000);
  assert(!f.radio.p.enabled());
  restored.begin(&f.fs, &reboot, &f.clock);
  assert(!restored.canAutoSecondary() && !restored.requestAutoSecondary(candidate()));

  // Version 1 cannot distinguish explicit Off from unused Off. Preserve it.
  auto legacy = image; legacy[2] = 1; rewriteCRC(legacy);
  f.fs.files["/radio_profiles"] = legacy;
  restored.begin(&f.fs, &reboot, &f.clock);
  assert(!restored.canAutoSecondary());
}

static void manualTakeover() {
  Fixture f;
  assert(f.cli.requestAutoSecondary(candidate()));
  const auto intent = f.cli.secondaryIntentGeneration();
  f.fs.fail_write = true;
  f.cmd("set radio2 910.5,250,11,5,rxtx,16", false);
  assert(f.cli.autoSecondaryActive() && f.cli.secondaryIntentGeneration() == intent);
  f.fs.fail_write = false;
  f.cmd("set radio2 910.5,250,11,5,rxtx,16");
  assert(!f.cli.autoSecondaryActive());
  assert(f.cli.secondaryIntentGeneration() == intent + 1);
  assert(f.cli.manualSecondaryConfigured() && !f.cli.canAutoSecondary());
  f.advance(2000);
  assert(f.radio.p.secondary.params.protocol == mesh::RadioProfileProtocol::MeshCore);
  const auto manual_generation = f.radio.p.generation[1];
  f.cli.clearAutoSecondary();
  assert(f.radio.p.generation[1] == manual_generation && f.radio.p.enabled());
  f.cmd("set radio2 910.5,250,11,5,rxtx,16");
  assert(f.cli.secondaryIntentGeneration() == intent + 2); // identical manual RF still owns intent
  f.advance(2000);
  assert(f.radio.p.generation[1] == manual_generation);
  Radio reboot; mesh::RadioProfileCLI restored;
  restored.begin(&f.fs, &reboot, &f.clock);
  assert(restored.manualSecondaryConfigured() && !restored.canAutoSecondary());
}

static void overlay() {
  Fixture f;
  f.cmd("set radio2 910.5,500,8,5,rx,80"); f.advance(2000);
  const auto saved = f.fs.files["/radio_profiles"];
  const auto rf = f.radio.p.secondary.params;
  assert(f.cli.useSecondaryProtocol(mesh::RadioProfileProtocol::Meshtext));
  assert(f.radio.p.secondary.params.freq == rf.freq && f.radio.p.secondary.params.bw == rf.bw);
  assert(f.radio.p.secondary.params.sf == rf.sf && f.radio.p.secondary.params.cr == rf.cr);
  assert(f.radio.p.secondary.mode == mesh::RadioProfileMode::Rx);
  assert(f.radio.p.secondary.params.preamble == 16);
  assert(f.radio.p.secondary.params.protocol == mesh::RadioProfileProtocol::Meshtext);
  assert(f.cli.savedSecondaryConfig().params == rf);
  assert(f.fs.files["/radio_profiles"] == saved);
  const auto generation = f.radio.p.generation[1];
  for (unsigned i = 0; i < 4; ++i) {
    assert(f.cli.useSecondaryProtocol(mesh::RadioProfileProtocol::Meshtext)); f.advance(1, false);
    assert(f.radio.p.generation[1] == generation);
  }
  f.cli.clearSecondaryProtocol();
  assert(f.radio.p.secondary.params == rf && f.fs.files["/radio_profiles"] == saved);
  assert(!f.cli.useSecondaryProtocol(static_cast<mesh::RadioProfileProtocol>(9)));
  assert(f.cli.useSecondaryProtocol(mesh::RadioProfileProtocol::Meshtext));
  f.cmd("set radio2 912.5,500,8,5,rxtx,80"); f.advance(2000);
  f.cli.clearSecondaryProtocol();
  assert(f.radio.p.secondary.params.freq == 912.5f);
  assert(f.radio.p.secondary.params.protocol == mesh::RadioProfileProtocol::MeshCore);
  assert(f.radio.p.secondary.params.preamble == 80);
}

static void leasesAndSchedules() {
  Fixture f;
  assert(f.cli.requestAutoSecondary(candidate()));
  f.cmd("set tempradio2 911.5,500,8,5,rxtx,1,80");
  assert(!f.cli.canAutoSecondary() && !f.cli.autoSecondaryActive());
  assert(!f.cli.requestAutoSecondary(candidate()));
  f.advance(2000);
  assert(f.radio.p.secondary_temporary && f.radio.p.secondary.params.freq == 911.5f);
  const auto lease_generation = f.radio.p.generation[1];
  f.cli.clearAutoSecondary(); f.cli.clearSecondaryProtocol();
  assert(f.radio.p.generation[1] == lease_generation);
  f.advance(60000);
  assert(!f.radio.p.enabled() && f.cli.canAutoSecondary());
  assert(f.cli.requestAutoSecondary(candidate()));
  f.cmd("set radioat2 912.5,500,8,5,rxtx,+1,80");
  assert(!f.cli.canAutoSecondary() && !f.cli.autoSecondaryActive());
  assert(!f.cli.requestAutoSecondary(candidate()));
  f.cmd("del radioat2 all");
  assert(f.cli.canAutoSecondary());
  f.cmd("set tempradioat2 912.5,500,8,5,rxtx,+1,+2,80");
  assert(!f.cli.canAutoSecondary());
  f.cmd("del tempradioat2 all");
  assert(f.cli.canAutoSecondary());
}

static void primaryTemporary() {
  Fixture f;
  assert(f.cli.requestAutoSecondary(candidate()));
  f.radio.p.primary_temporary = true; f.advance(1, false);
  assert(!f.radio.p.enabled() && !f.cli.autoSecondaryActive());
  assert(!f.cli.canAutoSecondary() && !f.cli.requestAutoSecondary(candidate()));
  const auto generation = f.radio.p.generation[1];
  for (unsigned i = 0; i < 4; ++i) f.advance(1, false);
  assert(f.radio.p.generation[1] == generation);
  f.radio.p.primary_temporary = false; f.advance(1, false);
  assert(f.cli.autoSecondaryActive());
}

static void automaticPermission() {
  Fixture f;
  f.cmd("set radio2 off"); f.advance(2000);
  assert(strstr(f.cmd("get radio2"), "manual"));
  const auto off_image = f.fs.files["/radio_profiles"];
  f.fs.fail_write = true; f.cmd("set radio2 auto", false);
  assert(!f.cli.canAutoSecondary() && f.fs.files["/radio_profiles"] == off_image);
  f.fs.fail_write = false; f.cmd("set radio2 auto");
  assert(f.fs.files["/radio_profiles"][2] == 2 && f.fs.files["/radio_profiles"][3] == 0);
  assert(!f.cli.canAutoSecondary()); // two-second publication barrier
  f.advance(2000);
  assert(f.cli.canAutoSecondary() && strstr(f.cmd("get radio2"), "automatic allowed"));
  Radio reboot; mesh::RadioProfileCLI restored;
  restored.begin(&f.fs, &reboot, &f.clock);
  assert(restored.canAutoSecondary());
  f.cmd("set tempradio2 911.5,500,8,5,rxtx,1,80");
  f.cmd("set radio2 auto", false);
  f.advance(2000); f.cmd("set radio2 auto", false);
  assert(f.radio.p.secondary_temporary);
  f.advance(60000);
  f.cmd("set radioat2 912.5,500,8,5,rxtx,+1,80");
  f.cmd("set radio2 auto", false);
  assert(strstr(f.cmd("get radioat2 all"), "1@"));
  f.cmd("del radioat2 all");
  f.cmd("set radio2 auto"); f.advance(2000);
  assert(f.cli.canAutoSecondary());
}

static void unmanagedTakeover() {
  Fixture f;
  assert(f.cli.requestAutoSecondary(candidate()));
  auto external = candidate(912.5f);
  external.params.protocol = mesh::RadioProfileProtocol::MeshCore;
  external.params.preamble = 80;
  f.radio.p.setSecondary(external, false);
  f.cli.clearAutoSecondary();
  assert(!f.cli.canAutoSecondary() && !f.cli.autoSecondaryActive());
  const auto generation = f.radio.p.generation[1];
  for (unsigned i = 0; i < 4; ++i) f.advance(1, false);
  assert(f.radio.p.secondary.params == external.params && f.radio.p.generation[1] == generation);
  f.cmd("set radio2 913.5,500,8,5,rxtx,80"); f.advance(2000);
  assert(f.cli.manualSecondaryConfigured() && f.radio.p.secondary.params.freq == 913.5f);
  assert(f.cli.useSecondaryProtocol(mesh::RadioProfileProtocol::Meshtext));
  external.params.freq = 914.5f;
  f.radio.p.setSecondary(external, false);
  f.cli.clearSecondaryProtocol(); f.advance(1, false);
  assert(f.radio.p.secondary.params == external.params);
  assert(!f.cli.useSecondaryProtocol(mesh::RadioProfileProtocol::Meshtext));
}

static void remoteBarrier() {
  for (bool delivered : {false, true}) {
    Fixture f(true);
    assert(f.cli.requestAutoSecondary(candidate()));
    const auto generation = f.radio.p.generation[1];
    f.cli.beginReplyCommand(); f.cmd("set radio2 912.5,500,8,5,rxtx,80"); f.cli.endReplyCommand();
    assert(f.cli.hasReplyMutation() && !f.cli.canAutoSecondary());
    f.advance(10000);
    assert(f.radio.p.generation[1] == generation);
    assert(f.cli.finishReplyMutation(delivered)); f.advance(1);
    if (delivered) {
      assert(f.cli.manualSecondaryConfigured() && !f.cli.autoSecondaryActive());
      assert(f.radio.p.secondary.params.freq == 912.5f);
    } else {
      assert(f.cli.autoSecondaryActive() && f.radio.p.generation[1] == generation);
      assert(f.fs.files.count("/radio_profiles") == 0);
    }
  }
  Fixture f(true);
  assert(f.cli.requestAutoSecondary(candidate()));
  const auto generation = f.radio.p.generation[1];
  f.cli.beginReplyCommand(); f.cmd("set radio2 off"); f.cli.endReplyCommand();
  f.cli.clearAutoSecondary(); // alias removed while the old generation owns a reply
  f.advance(10000);
  assert(f.radio.p.generation[1] == generation);
  assert(f.cli.finishReplyMutation(true)); f.advance(1);
  assert(!f.radio.p.enabled() && !f.cli.canAutoSecondary());
}

static void storageGuards() {
  Fixture f;
  f.fs.files["/radio_profiles"] = {0};
  Radio reboot; mesh::RadioProfileCLI restored;
  restored.begin(&f.fs, &reboot, &f.clock);
  assert(!restored.canAutoSecondary() && !restored.requestAutoSecondary(candidate()));
  assert(!restored.useSecondaryProtocol(mesh::RadioProfileProtocol::Meshtext));
  assert(f.fs.files["/radio_profiles"] == std::vector<uint8_t>{0});
  restored.begin(nullptr, &reboot, &f.clock);
  assert(!restored.canAutoSecondary());
  Fixture valid;
  valid.cmd("set radio2.cross off");
  const auto before = valid.fs.files["/radio_profiles"];
  valid.fs.fail_read_open = true;
  restored.begin(&valid.fs, &reboot, &valid.clock);
  assert(!restored.canAutoSecondary() && valid.fs.files["/radio_profiles"] == before);

  Fixture backup;
  backup.cmd("set radio2 910.5,500,8,5,rxtx,80");
  const auto committed = backup.fs.files["/radio_profiles"];
  assert(backup.fs.rename("/radio_profiles", "/radio_profiles.bak"));
  backup.fs.files["/radio_profiles"] = {0}; backup.fs.fail_remove = true;
  restored.begin(&backup.fs, &reboot, &backup.clock);
  assert(restored.manualSecondaryConfigured() && reboot.p.enabled());
  assert(!restored.canAutoSecondary());
  assert(!restored.useSecondaryProtocol(mesh::RadioProfileProtocol::Meshtext));
  assert(backup.fs.files["/radio_profiles.bak"] == committed);

  Fixture malformed;
  malformed.cmd("set radio2 910.5,500,8,5,rxtx,80");
  auto image = malformed.fs.files["/radio_profiles"];
  image[3] &= 0x7F; rewriteCRC(image); // enabled v2 profile cannot lack manual intent
  malformed.fs.files["/radio_profiles"] = image;
  Radio rejected;
  restored.begin(&malformed.fs, &rejected, &malformed.clock);
  assert(!rejected.p.enabled() && !restored.canAutoSecondary());
}

static void featureOff() {
  Fixture f;
  assert(!f.cli.canAutoSecondary() && !f.cli.requestAutoSecondary(candidate()));
  assert(!f.cli.autoSecondaryActive());
  assert(!f.cli.useSecondaryProtocol(mesh::RadioProfileProtocol::Meshtext));
  f.cli.clearAutoSecondary(); f.cli.clearSecondaryProtocol();
  assert(!f.radio.p.enabled() && f.fs.files.empty());
  f.cmd("set radio2 910.5,500,8,5,rxtx,80"); f.advance(2000);
  assert(f.radio.p.enabled() && f.fs.files["/radio_profiles"][2] == 1);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "lifetime")) lifetime();
  else if (!strcmp(argv[1], "saved_intent")) savedIntent();
  else if (!strcmp(argv[1], "manual_takeover")) manualTakeover();
  else if (!strcmp(argv[1], "overlay")) overlay();
  else if (!strcmp(argv[1], "leases")) leasesAndSchedules();
  else if (!strcmp(argv[1], "primary_temporary")) primaryTemporary();
  else if (!strcmp(argv[1], "automatic_permission")) automaticPermission();
  else if (!strcmp(argv[1], "unmanaged_takeover")) unmanagedTakeover();
  else if (!strcmp(argv[1], "remote_barrier")) remoteBarrier();
  else if (!strcmp(argv[1], "storage")) storageGuards();
  else if (!strcmp(argv[1], "feature_off")) featureOff();
  else return 2;
  puts("PASS auto secondary ownership");
}
'''


class RadioProfileAutoTest(unittest.TestCase):
    def test_ota_raw_slot_does_not_enable_foreign_ownership_or_v2_settings(self):
        code = r'''
#define main existing_cli_main
#include "test/fixtures/radio_profiles/cli_test.cpp"
#undef main
int main() {
  Fixture f(true);
  const bool foreign = MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL;
#if defined(ENABLE_OTA)
  static_assert(MESH_ENABLE_OTA_KISS_DIRECT, "exercise the actual direct-OTA capability");
  static_assert(MESH_ENABLE_FOREIGN_RADIO, "exercise the shared raw scheduler");
#endif
  assert(f.cli.canAutoSecondary() == foreign);
  assert(!f.cli.autoSecondaryActive());
  mesh::RadioProfileConfig config;
  config.mode = mesh::RadioProfileMode::RxTx;
  config.params.freq = 909.5f; config.params.bw = 250;
  config.params.sf = 11; config.params.cr = 5; config.params.preamble = 16;
#if MESH_ENABLE_MESHTEXT_CHAT
  config.params.protocol = mesh::RadioProfileProtocol::Meshtext;
#else
  config.params.protocol = mesh::RadioProfileProtocol::Ethernet;
#endif
  assert(f.cli.requestAutoSecondary(config) == foreign);
  f.cli.clearAutoSecondary();
  assert(!f.radio.p.enabled());
  assert(f.cli.useSecondaryProtocol(mesh::RadioProfileProtocol::Meshtext) == false);
  char reply[160] = "unchanged";
  assert(f.cli.handle("set radio2 auto", reply, sizeof reply));
  assert((!strncmp(reply, "OK", 2)) == foreign);
  if (foreign) f.advance(2000);
  assert(f.cli.savePrimaryPreamble(120));
  const auto image = f.fs.files["/radio_profiles"];
  assert(image.size() == 24 && image[2] == (foreign ? 2 : 1));
  f.cmd("set radio2 off");
  assert(!f.cli.autoSecondaryActive());
  assert(f.fs.files["/radio_profiles"][2] == (foreign ? 2 : 1));
  Radio reboot; mesh::RadioProfileCLI restored;
  restored.begin(&f.fs, &reboot, &f.clock, true);
  assert(!reboot.p.enabled() && !restored.canAutoSecondary());
#if defined(MESH_HIL_TX_FREQ_MHZ)
  assert(restored.handle("get lab.tx", reply, sizeof reply));
  assert(!strcmp(reply, "> HIL TX lock=909.500 MHz; off-frequency TX blocked; primary RX-only when off-frequency"));
#else
  assert(!restored.handle("get lab.tx", reply, sizeof reply));
#endif
  assert(!restored.handle("set lab.tx 909.5", reply, sizeof reply));
  printf("profile_size=%zu\n", sizeof(mesh::RadioProfileCLI));
}
'''
        sizes = {}
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        with tempfile.TemporaryDirectory(prefix="radio-profile-capability-") as work:
            source, binary = Path(work) / "capability.cpp", Path(work) / "capability"
            source.write_text(code)
            for name, ota, meshtext, ethernet, guard in (
                ("baseline", 0, 0, 0, 0), ("ota", 1, 0, 0, 0),
                ("meshtext", 1, 1, 0, 0), ("ethernet", 1, 0, 1, 0),
                ("ota_guard", 1, 0, 0, 1),
            ):
                flags = [f"-DMESH_ENABLE_MESHTEXT_CHAT={meshtext}",
                         f"-DMESH_ENABLE_ETHERNET_TUNNEL={ethernet}",
                         "-DMESH_ENABLE_FLEET_CONTROL=0"]
                if ota:
                    flags.append("-DENABLE_OTA=1")
                if guard:
                    flags.append("-DMESH_HIL_TX_FREQ_MHZ=909.5")
                result = subprocess.run([
                    compiler, "-std=c++17", "-Wall", "-Wextra", *flags,
                    "-I", str(ROOT), "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                    "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                    str(ROOT / "src/helpers/RadioProfileCLI.cpp"), str(source), "-o", str(binary),
                ], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual(run.returncode, 0, name + ": " + run.stdout + run.stderr)
                sizes[name] = int(run.stdout.split("profile_size=", 1)[1].splitlines()[0])
        self.assertEqual(sizes["baseline"], sizes["ota"])
        self.assertEqual(sizes["baseline"], sizes["ota_guard"])
        self.assertGreater(sizes["meshtext"], sizes["ota"])
        self.assertEqual(sizes["meshtext"], sizes["ethernet"])

    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")
        cls.work = tempfile.TemporaryDirectory(prefix="radio-profile-auto-")
        cls.addClassCleanup(cls.work.cleanup)
        directory = Path(cls.work.name)
        source = directory / "auto.cpp"
        source.write_text(HARNESS, encoding="utf-8")
        cls.binaries = {}
        for enabled, fleet in ((0, 0), (1, 0), (1, 1)):
            binary = directory / f"auto-{enabled}-{fleet}.exe"
            result = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra",
                f"-DMESH_ENABLE_MESHTEXT_CHAT={enabled}", f"-DMESH_ENABLE_FLEET_CONTROL={fleet}",
                "-I", str(ROOT), "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                str(ROOT / "src/helpers/RadioProfileCLI.cpp"), str(source), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[enabled, fleet] = binary

    def test_feature_off_keeps_existing_role_behavior(self):
        self.run_case("feature_off", enabled=0, fleet=0)

    def test_ownership_intent_and_precedence(self):
        for fleet in (0, 1):
            for case in ("lifetime", "saved_intent", "manual_takeover", "overlay", "leases",
                         "primary_temporary", "automatic_permission", "unmanaged_takeover",
                         "remote_barrier", "storage"):
                with self.subTest(fleet=fleet, case=case):
                    self.run_case(case, fleet=fleet)

    def run_case(self, case, enabled=1, fleet=0):
        result = subprocess.run([str(self.binaries[enabled, fleet]), case],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS auto secondary", result.stdout)


if __name__ == "__main__":
    unittest.main()
