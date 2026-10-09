#!/usr/bin/env python3
"""Deliver real signed fleet requests to the production CommonCLI clock branches.

Receiver, codec and anti-replay storage run unchanged. Shared fleet-runtime
fixtures provide hardware I/O and real OpenSSL Ed25519/AES/MAC interfaces. The
CommonCLI setter and manual-clock hook ordering are extracted from production;
only the RTC peripheral and reply date formatting are adapted for the host.
"""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body
from test_fleet_channel_runtime import FILESYSTEM, HARNESS as RUNTIME, MESH, PROFILE, REGION_MAP, UTILS


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <ctime>

// Date formatting is a host peripheral adapter; it never mutates the RTC.
class DateTime {
  std::tm value_{};
 public:
  explicit DateTime(uint32_t seconds) {
    const std::time_t epoch = seconds;
    const std::tm* value = std::gmtime(&epoch);
    assert(value); value_ = *value;
  }
  int hour() const { return value_.tm_hour; }
  int minute() const { return value_.tm_min; }
  int day() const { return value_.tm_mday; }
  int month() const { return value_.tm_mon + 1; }
  int year() const { return value_.tm_year + 1900; }
};

@ATOI@
struct ManualClockCallbacks {
  Clock& clock;
  unsigned hooks = 0;
  uint32_t observed = 0;
  void onManualClockSet() {
    assert(clock.sets == hooks + 1); // The production hook follows the setter.
    ++hooks; observed = clock.getCurrentTime();
  }
};
class CommonCLI {
  Clock& rtc_;
 public:
  ManualClockCallbacks callbacks;
  ManualClockCallbacks* _callbacks = &callbacks;
  explicit CommonCLI(Clock& rtc) : rtc_(rtc), callbacks{rtc} {}
  Clock* getRTCClock() { return &rtc_; }
  void clockCommand(uint32_t sender_timestamp, const char* command, char* reply) {
    @CLOCK_BRANCHES@
    else assert(false && "fleet clock dispatch accepted an unexpected command");
  }
};

struct Fixture {
  fs::FS disk;
  Mesh mesh;
  RadioProfileCLI profiles;
  FleetChannel fleet{&disk};
  LocalIdentity publisher;
  CommonCLI cli{mesh.clock};
  unsigned dispatched = 0;
  char reply[160] = {};
  Fixture() { enroll(fleet, publisher); bind(fleet, mesh, profiles); }
  unsigned offer(Packet& packet, FleetChannel* receiver = nullptr) {
    fake_ms += 1000; // Admission checks, rather than rate throttling, decide each case.
    const unsigned before = dispatched;
    FleetChannel& destination = receiver ? *receiver : fleet;
    destination.receive(&packet, mesh);
    destination.service(mesh, profiles, "clock node",
        [&](uint32_t sequence, const char* command_text, char* response) {
          ++dispatched;
          assert(storage::readLE32(disk.files.at(Path).data() + 56) == sequence);
          assert(profiles.command);
          cli.clockCommand(sequence, command_text, response);
          strcpy(reply, response);
        });
    return dispatched - before;
  }
  void unchanged(Packet& packet) {
    const uint32_t now = mesh.clock.now;
    const unsigned setters = mesh.clock.sets, hooks = cli.callbacks.hooks;
    const auto saved = disk.files.at(Path);
    assert(offer(packet) == 0);
    assert(mesh.clock.now == now && mesh.clock.sets == setters);
    assert(cli.callbacks.hooks == hooks && disk.files.at(Path) == saved);
  }
};

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string scenario = argv[1];
  Fixture f;
  const uint32_t initial = f.mesh.clock.now;
  if (scenario == "clock_read") {
    Packet packet = command(f.publisher, initial, "clock");
    assert(f.offer(packet) == 1);
    assert(strstr(f.reply, "UTC") && !strstr(f.reply, "clock set"));
    assert(f.mesh.clock.now == initial && f.mesh.clock.sets == 0);
    assert(f.cli.callbacks.hooks == 0);
    assert(storage::readLE32(f.disk.files.at(Path).data() + 56) == initial);
  } else if (scenario == "sync_forward") {
    const uint32_t sender_time = initial + 5;
    Packet packet = command(f.publisher, sender_time, "clock sync");
    assert(f.offer(packet) == 1);
    assert(f.mesh.clock.now == sender_time + 1);
    assert(f.mesh.clock.sets == 1 && f.cli.callbacks.hooks == 1);
    assert(f.cli.callbacks.observed == sender_time + 1);
    assert(!strncmp(f.reply, "OK - clock set:", 15));
    assert(storage::readLE32(f.disk.files.at(Path).data() + 56) == sender_time);
  } else if (scenario == "sync_uint32_boundary") {
    // Fleet envelopes require a later expiry and cannot carry this timestamp.
    // Exercise the production CommonCLI branch directly to cover other CLI
    // transports as well: adding one to UINT32_MAX must never reset the RTC.
    const auto saved = f.disk.files.at(Path);
    f.mesh.clock.now = UINT32_MAX - 1;
    f.cli.clockCommand(UINT32_MAX, "clock sync", f.reply);
    assert(f.mesh.clock.now == UINT32_MAX - 1);
    assert(f.mesh.clock.sets == 0 && f.cli.callbacks.hooks == 0);
    assert(strstr(f.reply, "ERR"));
    assert(f.disk.files.at(Path) == saved && f.dispatched == 0);
    // The adjacent representable result remains a valid forward update.
    f.mesh.clock.now = UINT32_MAX - 2;
    f.cli.clockCommand(UINT32_MAX - 1, "clock sync", f.reply);
    assert(f.mesh.clock.now == UINT32_MAX);
    assert(f.mesh.clock.sets == 1 && f.cli.callbacks.hooks == 1);
    assert(f.cli.callbacks.observed == UINT32_MAX);
    assert(!strncmp(f.reply, "OK - clock set:", 15));
  } else if (scenario == "explicit_forward") {
    const uint32_t requested = initial + 20;
    const std::string text = "time " + std::to_string(requested);
    Packet packet = command(f.publisher, initial, text.c_str());
    assert(f.offer(packet) == 1);
    assert(f.mesh.clock.now == requested);
    assert(f.mesh.clock.sets == 1 && f.cli.callbacks.hooks == 1);
    assert(f.cli.callbacks.observed == requested);
    assert(!strncmp(f.reply, "OK - clock set:", 15));
    // The signed request sequence, rather than the requested clock value,
    // remains the durable anti-replay identity.
    assert(storage::readLE32(f.disk.files.at(Path).data() + 56) == initial);
  } else if (scenario == "backward_and_equal") {
    Packet sync = command(f.publisher, initial - 1, "clock sync");
    assert(f.offer(sync) == 1);
    assert(strstr(f.reply, "clock cannot go backwards"));
    Packet equal = command(f.publisher, initial, "clock sync");
    assert(f.offer(equal) == 1);
    assert(strstr(f.reply, "clock cannot go backwards"));
    const std::string backward = "time " + std::to_string(initial - 1);
    Packet earlier = command(f.publisher, initial + 1, backward.c_str());
    assert(f.offer(earlier) == 1);
    assert(strstr(f.reply, "clock cannot go backwards"));
    const std::string same = "time " + std::to_string(initial);
    Packet unchanged = command(f.publisher, initial + 2, same.c_str());
    assert(f.offer(unchanged) == 1);
    assert(strstr(f.reply, "clock cannot go backwards"));
    assert(f.mesh.clock.now == initial && f.mesh.clock.sets == 0);
    assert(f.cli.callbacks.hooks == 0);
  } else if (scenario == "signatures_and_target") {
    const std::string text = "time " + std::to_string(initial + 20);
    Packet valid = command(f.publisher, initial, text.c_str());
    uint8_t plain[184] = {};
    const int size = Utils::MACThenDecrypt(channel_key, plain, valid.payload + 1,
                                          valid.payload_len - 1);
    assert(size > 3 && plain[2] > FleetCommand::SignatureSize);
    plain[3 + plain[2] - 1] ^= 1;
    Packet invalid_signature = raw(plain, 3 + plain[2]);
    const unsigned before = verify_calls;
    f.unchanged(invalid_signature);
    assert(verify_calls == before + 1); // MAC was valid; Ed25519 denied control.
    LocalIdentity stranger;
    Packet wrong_publisher = command(stranger, initial, text.c_str());
    f.unchanged(wrong_publisher);
    Packet wrong_target = command(f.publisher, initial, text.c_str(), publicHex(stranger).c_str());
    f.unchanged(wrong_target);
    assert(f.offer(valid) == 1); // Rejected requests did not reserve the sequence.
    assert(f.mesh.clock.now == initial + 20 && f.cli.callbacks.hooks == 1);
  } else if (scenario == "durable_replay") {
    Packet packet = command(f.publisher, initial + 5, "clock sync");
    assert(f.offer(packet) == 1);
    f.unchanged(packet);
    FleetChannel rebooted(&f.disk);
    const unsigned before = f.dispatched;
    const auto saved = f.disk.files.at(Path);
    assert(f.offer(packet, &rebooted) == 0);
    assert(f.dispatched == before && f.mesh.clock.now == initial + 6);
    assert(f.mesh.clock.sets == 1 && f.cli.callbacks.hooks == 1);
    assert(f.disk.files.at(Path) == saved);
  } else if (scenario == "invalid_rtc") {
    Packet packet = command(f.publisher, initial + 5, "clock sync");
    for (uint32_t invalid : {0u, FleetCommand::MinEpoch - 1}) {
      f.mesh.clock.now = invalid;
      f.unchanged(packet);
    }
    assert(f.dispatched == 0 && f.mesh.clock.sets == 0);
    f.mesh.clock.now = initial;
    assert(f.offer(packet) == 1);
    assert(f.mesh.clock.now == initial + 6 && f.cli.callbacks.hooks == 1);
  } else if (scenario == "expired_and_future") {
    Packet expired = signedUnchecked(f.publisher, initial - 300, initial - 1, "clock sync");
    f.unchanged(expired);
    Packet future = command(f.publisher, initial + FleetCommand::MaxClockLead + 1, "clock sync");
    f.unchanged(future);
    assert(f.dispatched == 0 && f.mesh.clock.sets == 0 && f.cli.callbacks.hooks == 0);
    Packet current = command(f.publisher, initial + 1, "clock sync");
    assert(f.offer(current) == 1);
    assert(f.mesh.clock.now == initial + 2 && f.cli.callbacks.hooks == 1);
  } else if (scenario == "signed_invalid_clock_forms") {
    for (const char* text : {"get clock", "set clock", "clock sync extra", "time NaN",
                            "time -1", "time 4294967296", "time 1735689599"}) {
      Packet packet = signedUnchecked(f.publisher, initial, initial + 300, text);
      f.unchanged(packet);
    }
    assert(f.dispatched == 0 && f.mesh.clock.sets == 0 && f.cli.callbacks.hooks == 0);
  } else assert(false);
  printf("Fleet clock scenario %s passed\n", argv[1]);
}
'''


class FleetClockControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("host C++ compiler required")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-fleet-clock-")
        work = Path(cls.work.name)
        (work / "helpers").mkdir()
        clock_getter = "uint32_t getCurrentTime() const{return now;}"
        assert MESH.count(clock_getter) == 1
        mesh = MESH.replace(clock_getter, clock_getter + "\n unsigned sets=0;"
                            " void setCurrentTime(uint32_t value){++sets;now=value;}")
        for name, content in {
            "Utils.h": UTILS, "FS.h": FILESYSTEM, "Mesh.h": mesh,
            "helpers/RadioProfileCLI.h": PROFILE,
            "helpers/RegionMap.h": REGION_MAP,
            "Packet.h": '#pragma once\n#include <Mesh.h>\n',
            "Arduino.h": '#pragma once\n#include <Mesh.h>\n',
            "Adafruit_LittleFS.h": r'''
#pragma once
#include <FS.h>
using Adafruit_LittleFS = fs::FS;
namespace Adafruit_LittleFS_Namespace {}
#define FILE_O_READ 1
#define FILE_O_WRITE 2
#define LFS_ERR_NOENT -2
struct lfs_info {};
inline int lfs_stat(fs::FS* fs,const char* path,lfs_info*) {return fs->exists(path)?0:LFS_ERR_NOENT;}
''',
            "InternalFileSystem.h": '#pragma once\n#include <FS.h>\ninline fs::FS InternalFS;\n',
        }.items():
            (work / name).write_text(content)
        sha = ROOT / "test/mocks/SHA256.h"
        (work / "SHA256.h").write_text(f'''
#pragma once
#define SHA256 SHA256Base
#include "{sha}"
#undef SHA256
class SHA256 : public SHA256Base {{ public:
 void finalize(void* out,size_t length) {{ SHA256Base::finalize(static_cast<uint8_t*>(out),length); }}
 void finalizeHMAC(const uint8_t* key,size_t size,void* out,size_t length) {{
  SHA256Base::finalizeHMAC(key,size,static_cast<uint8_t*>(out),length); }}
}};
''')
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        # The ESP-only *next* command opens its #if before the clock-sync
        # branch's closing brace. Separate that following command's guard from
        # the unconditional clock body without altering any clock statements.
        sync_start = common.index('if (memcmp(command, "clock sync", 10) == 0)')
        next_guard = '#if defined(ESP_PLATFORM) && defined(ADMIN_PASSWORD) && !defined(WEBCONFIG_DISABLED)'
        guard_start = common.index(next_guard, sync_start)
        assert common[guard_start + len(next_guard):].startswith(
            '\n    } else if (memcmp(command, "start webconfig", 15) == 0')
        clock_source = common[:guard_start] + common[guard_start + len(next_guard):]
        branches = "\nelse ".join(body(clock_source, signature) for signature in (
            'if (memcmp(command, "clock sync", 10) == 0)',
            'if (memcmp(command, "clock", 5) == 0)',
            'if (memcmp(command, "time ", 5) == 0)',
        ))
        clock_fixture = HARNESS.replace("@ATOI@", body(common, "static uint32_t _atoi("))
        clock_fixture = clock_fixture.replace("@CLOCK_BRANCHES@", branches)
        transport = (ROOT / "src/helpers/TransportKeyStore.cpp").read_text()
        transport_methods = "\n".join(body(transport, signature) for signature in (
            "uint16_t TransportKey::calcTransportCode(", "bool TransportKey::isNull(",
        ))
        shared = RUNTIME[:RUNTIME.index("static unsigned apply(")]
        fixture = work / "fixture.cpp"
        fixture.write_text(shared.replace("@TRANSPORT_METHODS@", transport_methods) + clock_fixture)
        sanitizer_flags = (["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
                           if os.environ.get("MESHCORE_FLEET_SANITIZERS") == "1" else [])
        cls.binaries = {}
        for backend in ("RP2040", "NRF52"):
            binary = work / ("fleet-clock-" + backend.lower())
            sources = [str(ROOT / "src/helpers/FleetChannel.cpp"),
                       str(ROOT / "src/helpers/FleetCommand.cpp")]
            if backend == "NRF52":
                sources.append(str(ROOT / "src/helpers/AtomicFileWriter.cpp"))
            built = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-function", "-Wno-unused-parameter", "-Wno-misleading-indentation", "-Wno-sign-compare",
                "-O2", *sanitizer_flags, "-D" + backend + "_PLATFORM",
                "-I", str(work), "-isystem", str(ROOT / "test/mocks"),
                "-I", str(ROOT / "src"), str(fixture), *sources, "-lcrypto", "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            if built.returncode:
                cls.work.cleanup()
                raise AssertionError(backend + ":\n" + built.stdout + built.stderr)
            cls.binaries[backend] = binary

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def scenario(self, name):
        for backend, binary in self.binaries.items():
            with self.subTest(backend=backend):
                result = subprocess.run([str(binary), name], capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_clock_getter_preserves_rtc_and_durably_consumes_request(self):
        self.scenario("clock_read")

    def test_clock_sync_uses_signed_sender_utc_plus_one_and_calls_manual_hook(self):
        self.scenario("sync_forward")

    def test_clock_sync_uint32_boundary_never_wraps_rtc_or_fires_manual_hook(self):
        self.scenario("sync_uint32_boundary")

    def test_explicit_forward_time_calls_production_setter_and_hook(self):
        self.scenario("explicit_forward")

    def test_backward_and_equal_clock_requests_never_set_rtc_or_call_hook(self):
        self.scenario("backward_and_equal")

    def test_invalid_signature_publisher_and_target_cannot_change_clock(self):
        self.scenario("signatures_and_target")

    def test_replayed_clock_change_is_denied_before_and_after_receiver_reboot(self):
        self.scenario("durable_replay")

    def test_invalid_or_unset_rtc_has_no_signed_clock_bootstrap_exception(self):
        self.scenario("invalid_rtc")

    def test_expired_and_excessively_future_clock_envelopes_never_dispatch(self):
        self.scenario("expired_and_future")

    def test_valid_signature_never_grants_malformed_clock_commands(self):
        self.scenario("signed_invalid_clock_forms")


if __name__ == "__main__":
    unittest.main()
