#!/usr/bin/env python3
"""Exercise Companion traversal against real LittleFS and competing writes."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
LFS = ROOT / "arch/stm32/Adafruit_LittleFS_stm32/src/littlefs"

HARNESS = r'''
#include <lfs.h>
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <future>
#include <mutex>
#include <thread>
#include <vector>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
using namespace std::chrono_literals;
struct FILESYSTEM {
  lfs_t fs{};
  lfs_config cfg{};
  std::mutex mutex, gate_mutex;
  std::condition_variable gate;
  std::atomic<bool> locked{false}, writer_attempted{false}, writer_done{false};
  unsigned locks=0, unlocks=0;
  bool armed=false, entered=false, released=false, fail_reads=false, null_raw=false;
  std::vector<uint8_t> bytes;
  FILESYSTEM():bytes(4096*32,0xFF) {
    cfg.context=this;cfg.read=read;cfg.prog=prog;cfg.erase=erase;cfg.sync=sync;
    cfg.read_size=16;cfg.prog_size=16;cfg.block_size=4096;cfg.block_count=32;
    cfg.lookahead=32;
    _lockFS();assert(lfs_format(&fs,&cfg)==0);assert(lfs_mount(&fs,&cfg)==0);_unlockFS();
  }
  ~FILESYSTEM() {_lockFS();assert(lfs_unmount(&fs)==0);_unlockFS();}
  void _lockFS() {mutex.lock();assert(!locked.exchange(true));++locks;}
  void _unlockFS() {++unlocks;assert(locked.exchange(false));mutex.unlock();}
  lfs_t* _getFS() {assert(locked);return null_raw?nullptr:&fs;}
  static FILESYSTEM& owner(const lfs_config* cfg) {
    auto& owner=*static_cast<FILESYSTEM*>(cfg->context);assert(owner.locked);return owner;
  }
  static int read(const lfs_config* cfg,lfs_block_t block,lfs_off_t off,void* out,lfs_size_t len) {
    auto& self=owner(cfg);
    if(self.fail_reads) return LFS_ERR_IO;
    {
      std::unique_lock<std::mutex> guard(self.gate_mutex);
      if(self.armed) {
        self.armed=false;self.entered=true;self.gate.notify_all();
        assert(self.gate.wait_for(guard,3s,[&]{return self.released;}));
      }
    }
    assert(block<cfg->block_count&&off+len<=cfg->block_size);
    memcpy(out,self.bytes.data()+block*cfg->block_size+off,len);return 0;
  }
  static int prog(const lfs_config* cfg,lfs_block_t block,lfs_off_t off,const void* in,lfs_size_t len) {
    auto& self=owner(cfg);assert(block<cfg->block_count&&off+len<=cfg->block_size);
    auto* dest=self.bytes.data()+block*cfg->block_size+off;
    auto* src=static_cast<const uint8_t*>(in);
    for(unsigned i=0;i<len;++i) {assert((dest[i]&src[i])==src[i]);dest[i]=src[i];}
    return 0;
  }
  static int erase(const lfs_config* cfg,lfs_block_t block) {
    auto& self=owner(cfg);assert(block<cfg->block_count);
    memset(self.bytes.data()+block*cfg->block_size,0xFF,cfg->block_size);return 0;
  }
  static int sync(const lfs_config* cfg) {owner(cfg);return 0;}
  void write(unsigned marker) {
    _lockFS();lfs_file_t file{};
    assert(lfs_file_open(&fs,&file,"/contacts",LFS_O_WRONLY|LFS_O_CREAT|LFS_O_TRUNC)==0);
    std::vector<uint8_t> contents(5000,uint8_t(marker));
    assert(lfs_file_write(&fs,&file,contents.data(),contents.size())==int(contents.size()));
    assert(lfs_file_close(&fs,&file)==0);_unlockFS();
  }
  void verify(unsigned marker) {
    _lockFS();lfs_file_t file{};
    assert(lfs_file_open(&fs,&file,"/contacts",LFS_O_RDONLY)==0);
    std::vector<uint8_t> contents(5000);
    assert(lfs_file_read(&fs,&file,contents.data(),contents.size())==int(contents.size()));
    for(auto byte:contents) assert(byte==uint8_t(marker));
    assert(lfs_file_close(&fs,&file)==0);_unlockFS();
  }
};
@METHODS@
int main() {
  assert(_getLfsUsedBlockCount(nullptr)==-1&&!validateLfsFilesystem(nullptr));
  FILESYSTEM fs;assert(validateLfsFilesystem(&fs));fs.write(7);
  assert(_getLfsUsedBlockCount(&fs)>0&&fs.locks==fs.unlocks&&!fs.locked);
  fs.verify(7);

  // Failed/uninitialized mounts must fail closed without dereferencing their
  // raw handle/configuration or leaving the filesystem mutex held.
  fs.null_raw=true;
  assert(_getLfsUsedBlockCount(&fs)==-1&&!validateLfsFilesystem(&fs));
  assert(fs.locks==fs.unlocks&&!fs.locked);fs.null_raw=false;
  const auto* mounted_config=fs.fs.cfg;fs.fs.cfg=nullptr;
  assert(_getLfsUsedBlockCount(&fs)==-1&&!validateLfsFilesystem(&fs));
  assert(fs.locks==fs.unlocks&&!fs.locked);fs.fs.cfg=mounted_config;
  const auto mounted_blocks=fs.cfg.block_count;fs.cfg.block_count=0;
  assert(_getLfsUsedBlockCount(&fs)==-1&&!validateLfsFilesystem(&fs));
  assert(fs.locks==fs.unlocks&&!fs.locked);fs.cfg.block_count=mounted_blocks;
  fs.verify(7);assert(validateLfsFilesystem(&fs));

  // Pause a real metadata read inside lfs_traverse. A competing writer must
  // wait for the same filesystem mutex until the entire traversal finishes.
  for(unsigned repeat=0;repeat<10;++repeat) {
    {
      std::lock_guard<std::mutex> guard(fs.gate_mutex);
      fs.armed=true;fs.entered=false;fs.released=false;
    }
    fs.writer_attempted=false;fs.writer_done=false;
    auto traversal=std::async(std::launch::async,[&]{return _getLfsUsedBlockCount(&fs);});
    {
      std::unique_lock<std::mutex> guard(fs.gate_mutex);
      assert(fs.gate.wait_for(guard,3s,[&]{return fs.entered;}));
    }
    auto writer=std::async(std::launch::async,[&] {
      fs.writer_attempted=true;fs.write(repeat+10);fs.writer_done=true;
    });
    const auto deadline=std::chrono::steady_clock::now()+3s;
    while(!fs.writer_attempted) {assert(std::chrono::steady_clock::now()<deadline);std::this_thread::yield();}
    assert(fs.locked&&!fs.writer_done);
    // The lock cannot be acquired from a second thread while the traversal
    // callback is paused, independent of scheduler timing for the writer.
    assert(!fs.mutex.try_lock());
    {
      std::lock_guard<std::mutex> guard(fs.gate_mutex);fs.released=true;fs.gate.notify_all();
    }
    assert(traversal.get()>0);writer.get();fs.verify(repeat+10);
    assert(fs.locks==fs.unlocks&&!fs.locked);
  }

  // I/O failure must release the lock and report a failed validation, never
  // an empty healthy filesystem. A later write and traversal still succeed.
  fs.fail_reads=true;
  assert(_getLfsUsedBlockCount(&fs)==-1&&!validateLfsFilesystem(&fs));
  assert(fs.locks==fs.unlocks&&!fs.locked);
  fs.fail_reads=false;fs.write(99);fs.verify(99);assert(validateLfsFilesystem(&fs));

  // Existing corruption bounds remain inclusive at block_count, and cyclic
  // metadata cannot traverse more blocks than the physical filesystem has.
  LfsTraversalState state={0,3};
  assert(_countLfsBlock(&state,3)==LFS_ERR_CORRUPT&&state.visited==0);
  assert(_countLfsBlock(&state,0)==0&&_countLfsBlock(&state,1)==0);
  assert(_countLfsBlock(&state,2)==0&&state.visited==3);
  assert(_countLfsBlock(&state,0)==LFS_ERR_CORRUPT&&state.visited==3);
  LfsTraversalState empty={0,0};assert(_countLfsBlock(&empty,0)==LFS_ERR_CORRUPT);
}
'''


class CompanionLfsTraversalTest(unittest.TestCase):
    def test_actual_traversal_serializes_metadata_writes_and_unlocks_on_error(self):
        source = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
        methods = extract_braced(source, "struct LfsTraversalState") + ";\n"
        methods += "\n".join(extract_braced(source, signature) for signature in (
            "int _countLfsBlock(", "lfs_ssize_t _getLfsUsedBlockCount(",
            "static bool validateLfsFilesystem(FILESYSTEM* fs) {",
        ))
        with tempfile.TemporaryDirectory(prefix="mesh-lfs-traversal-") as temp:
            work = Path(temp)
            path = work / "test.cpp"
            path.write_text(HARNESS.replace("@METHODS@", methods))
            flags = ["-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                     "-fno-pie", "-no-pie", "-I", str(LFS)]
            objects = []
            for name in ("lfs", "lfs_util"):
                obj = work / (name + ".o")
                compiled = subprocess.run(["cc", *flags, "-c", str(LFS / (name + ".c")),
                                           "-o", str(obj)], capture_output=True, text=True, timeout=60)
                self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                objects.append(str(obj))
            binary = work / "test"
            compiled = subprocess.run([
                "c++", "-std=c++17", "-Werror", *flags, "-pthread", str(path),
                *objects, "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
