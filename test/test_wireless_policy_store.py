#!/usr/bin/env python3
"""Run wireless master policy transactions against each production FS backend."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/WirelessPolicyStore.h>
#include <helpers/PersistentStoreFormat.h>
#include <cassert>
using namespace mesh::wireless;
using Bytes = std::vector<uint8_t>;
constexpr char Path[] = "/wireless_policy";
Bytes image(const Policy& policy) {
  Bytes bytes={'W','L','P','1',policy.mode,policy.mask,policy.primary_espnow,0,0,0,0,0};
  mesh::storage::writeLE32(bytes.data()+8,
      mesh::storage::updateCRC32(0xffffffff,bytes.data(),8));
  return bytes;
}
int main() {
  const Policy canonical{}, off{1,5,2}, primary_off{1,5,1};
  GuestMemoryFS fs;PolicyStore store;
  assert(store.begin(&fs)&&store.healthy()&&!store.present()&&store.value()==canonical);
  assert(fs.writes==0);
  fs.files["/unrelated"]={1,2,3};
  assert(store.save(off)&&store.present()&&store.value()==off);
  assert(fs.files.at(Path)==image(off));
  const auto writes=fs.writes;
  assert(store.save(off)&&fs.writes==writes);
  PolicyStore reboot;assert(reboot.begin(&fs)&&reboot.value()==off);
  assert(reboot.save(primary_off));
  PolicyStore restarted;assert(restarted.begin(&fs)&&restarted.value()==primary_off);
  assert(fs.files.at("/unrelated")==Bytes({1,2,3}));
  for(unsigned mode=0;mode<=1;++mode)for(unsigned mask=0;mask<=7;++mask)
    for(unsigned primary=0;primary<=2;++primary) {
      Policy selected{uint8_t(mode),uint8_t(mask),uint8_t(primary)};
      assert(restarted.save(selected));
      PolicyStore again;assert(again.begin(&fs)&&again.value()==selected);
    }
  for(auto invalid:{Policy{2,0,0},Policy{0,8,0},Policy{0,0,3}})
    assert(!restarted.save(invalid));

  // Only a genuinely absent file may adopt the compatibility defaults.
  for(unsigned length=0;length<25;++length) {
    if(length==12)continue;
    GuestMemoryFS broken;broken.files[Path]=image(off);broken.files[Path].resize(length);
    PolicyStore read;assert(!read.begin(&broken)&&read.value().mode==1);
    assert(read.healthy()&&read.save(canonical));
  }
  for(unsigned byte=0;byte<12;++byte)for(unsigned bit=0;bit<8;++bit) {
    GuestMemoryFS broken;broken.files[Path]=image(off);broken.files[Path][byte]^=1<<bit;
    PolicyStore read;assert(!read.begin(&broken)&&read.value().mode==1
        &&read.value().primary_espnow==1);
  }
  PolicyStore missing;assert(!missing.begin(nullptr)&&!missing.save(canonical));
#if !defined(RP2040_PLATFORM)
  GuestMemoryFS inaccessible;inaccessible.files[Path]=image(off);inaccessible.fail_metadata=true;
  PolicyStore unreadable;assert(!unreadable.begin(&inaccessible)&&!unreadable.healthy());
  assert(unreadable.value().mode==1&&!unreadable.save(canonical)&&inaccessible.writes==0);
#endif
  // Admission/write/staging/publication errors cannot acknowledge a new
  // mask or overwrite the old complete record with an empty default.
  for(unsigned fault=0;fault<7;++fault) {
    GuestMemoryFS broken;broken.files[Path]=image(off);PolicyStore read;assert(read.begin(&broken));
    if(fault==0)broken.fail_open_write=true;
    if(fault==1)broken.short_write=5;
    if(fault==2)broken.corrupt_stage=true;
    if(fault==3)broken.fail_stage_open=true;
    if(fault==4)broken.fail_stage_read=true;
    if(fault==5)broken.fail_publish=true;
    if(fault==6)broken.false_publish=true;
    assert(!read.save(canonical)&&read.value()==off);
    assert(broken.files.at(Path)==image(off));
  }
  // Simulate power loss at every filesystem boundary. Group gate and restore
  // mask must be recovered together, from either the old or the new record.
  for(auto old:{canonical,off,primary_off}) {
    const Policy next=old==off?canonical:off;
    GuestMemoryFS backing;backing.files[Path]=image(old);PolicyStore read;assert(read.begin(&backing));
    backing.capture=true;assert(read.save(next));
    const auto snapshots=backing.snapshots;assert(snapshots.size()>10);
    for(const auto& snapshot:snapshots) {
      GuestMemoryFS restored;restored.files=snapshot;PolicyStore recovered;
      assert(recovered.begin(&restored));
      assert(recovered.value()==old||recovered.value()==next);
      if(snapshot.count(Path))assert(image(recovered.value())==snapshot.at(Path));
      else if(snapshot.count("/wireless_policy.bak"))
        assert(image(recovered.value())==snapshot.at("/wireless_policy.bak"));
    }
  }
  GuestMemoryFS scratch;scratch.files["/wireless_policy.tmp"]=image(off);
  PolicyStore discarded;assert(discarded.begin(&scratch)&&discarded.value()==canonical);
}
'''


class WirelessPolicyStoreTests(unittest.TestCase):
    def test_reboot_corruption_and_failure_boundaries_on_all_backends(self):
        compiler = shutil.which(os.environ.get("CXX", "g++"))
        self.assertIsNotNone(compiler, "host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="mesh-wireless-policy-") as directory:
            work = Path(directory)
            mocks = work / "mocks"
            shutil.copytree(ROOT / "test/fixtures/guest_login/mocks", mocks)
            for file in mocks.rglob("*"):
                if file.is_file():
                    file.write_text(file.read_text().replace("/guest_login", "/wireless_policy"))
            harness = work / "test.cpp"
            harness.write_text(HARNESS)
            for backend in ("NRF52", "STM32", "ESP32", "RP2040"):
                with self.subTest(backend=backend):
                    binary = work / backend
                    flags = ["-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                             "-Wno-misleading-indentation", "-Wno-sign-compare",
                             "-D" + backend + "_PLATFORM"]
                    if backend == "ESP32":
                        flags += ["-DESP32"]
                    if os.name != "nt":
                        flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
                    sources = [str(harness), str(ROOT / "src/helpers/WirelessPolicyStore.cpp")]
                    if backend in ("NRF52", "STM32"):
                        sources += [str(ROOT / "src/helpers/AtomicFileWriter.cpp")]
                    compiled = subprocess.run([compiler, *flags, "-I", str(mocks), "-I",
                        str(ROOT / "src"), *sources, "-o", str(binary)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
