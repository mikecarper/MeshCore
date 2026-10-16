#!/usr/bin/env python3
"""Run the production boot-only SPIFFS inventory over SDK/POSIX doubles.

The actual FS decorator, owner registry, metadata helper and transaction
recovery run unchanged. Directory and flash failures occur only at peripheral
boundaries. The reusable deadline workload reports a 129 ms/name-scan model,
not physical timing or a qualification of flash corruption.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / "src/helpers/esp32/BootFileSystem.cpp"

FS_HEADER = r'''
#pragma once
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
using String = std::string;
using boolean = bool;
namespace fs {
class FileImpl {
public:
  bool directory = false;
  std::string name;
  virtual ~FileImpl() = default;
};
using FileImplPtr = std::shared_ptr<FileImpl>;
class File {
public:
  FileImplPtr impl;
  File(FileImplPtr value = {}) : impl(value) {}
  explicit operator bool() const { return bool(impl); }
  bool isDirectory() const { return impl && impl->directory; }
};
class FSImpl {
protected:
  const char* _mountpoint = nullptr;
public:
  virtual ~FSImpl() = default;
  virtual FileImplPtr open(const char*, const char*, bool) = 0;
  virtual bool exists(const char*) = 0;
  virtual bool rename(const char*, const char*) = 0;
  virtual bool remove(const char*) = 0;
  virtual bool mkdir(const char*) = 0;
  virtual bool rmdir(const char*) = 0;
  void mountpoint(const char* value) { _mountpoint = value; }
  const char* mountpoint() { return _mountpoint; }
};
using FSImplPtr = std::shared_ptr<FSImpl>;
class FS {
protected:
  FSImplPtr _impl;
public:
  explicit FS(FSImplPtr impl) : _impl(impl) {}
  File open(const char* path, const char* mode = "r", bool create = false) {
    return File(_impl->open(path, mode, create));
  }
  bool exists(const char* path) { return _impl->exists(path); }
  bool remove(const char* path) { return _impl->remove(path); }
  bool rename(const char* from, const char* to) { return _impl->rename(from, to); }
  bool mkdir(const char* path) { return _impl->mkdir(path); }
  bool rmdir(const char* path) { return _impl->rmdir(path); }
};
}
using fs::File;
'''

DIRENT_HEADER = r'''
#pragma once
#include <cstddef>
struct DIR { size_t cursor = 0; };
struct dirent { unsigned char d_type = 1; char d_name[256] = {}; };
#define DT_REG 1
#define DT_DIR 2
DIR* opendir(const char*);
struct dirent* readdir(DIR*);
int closedir(DIR*);
'''

STAT_HEADER = r'''
#pragma once
struct stat { unsigned st_mode = 0; };
int stat(const char*, struct stat*);
'''

HARNESS = r'''
#include <cassert>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <new>
#include <set>
#include <string>
#include <vector>
#include <strings.h>
#include <FS.h>
#include <dirent.h>
#include <sys/stat.h>
#include <helpers/esp32/BootFileSystem.h>
#include <helpers/esp32/BootFilePresence.h>
#include <helpers/FilePresence.h>
#include <helpers/CommonPrefsRecovery.h>

static unsigned new_calls = 0;
void* operator new(size_t count) {
  ++new_calls;
  if (void* address = std::malloc(count)) return address;
  throw std::bad_alloc();
}
void operator delete(void* address) noexcept { std::free(address); }
void operator delete(void* address, size_t) noexcept { std::free(address); }

struct Peripherals {
  std::vector<std::string> names;
  unsigned scans = 0, directory_opens = 0, directory_reads = 0, directory_closes = 0;
  unsigned native_exists = 0, native_opens = 0, stats = 0, allocations = 0;
  unsigned mutations = 0, frees = 0, reallocations = 0;
  size_t shrink_bytes = 0;
  bool fail_open = false, fail_close = false, fail_malloc = false, metadata_error = false;
  bool fail_realloc = false;
  bool dirty_success_errno = false, unterminated_name = false;
  int fail_read_at = -1;
  const void* owner = nullptr;
} peripheral;
static std::map<void*, size_t> inventory_buffers;

void* inventory_malloc(size_t count) {
  ++peripheral.allocations;
  assert(count == 8192);
  if (peripheral.fail_malloc) return nullptr;
  void* address = std::malloc(count);
  if (address) inventory_buffers[address] = count;
  return address;
}
void* inventory_realloc(void* address, size_t count) {
  ++peripheral.reallocations;
  peripheral.shrink_bytes = count;
  assert(inventory_buffers.count(address) && inventory_buffers[address] == 8192);
  assert(count > 0 && count < 8192);
  if (peripheral.fail_realloc) return nullptr;
  const auto found = inventory_buffers.find(address);
  void* compact = std::realloc(address, count);
  if (compact) {
    inventory_buffers.erase(found);
    inventory_buffers[compact] = count;
  }
  return compact;
}
void inventory_free(void* address) {
  if (address) {
    assert(inventory_buffers.erase(address) == 1);
    ++peripheral.frees;
  }
  std::free(address);
}
DIR* opendir(const char* path) {
  ++peripheral.directory_opens;
  ++peripheral.scans; // One complete SPIFFS lookup-table traversal, when valid.
  assert(std::string(path) == "/spiffs" || std::string(path) == "/spiffs/");
  if (peripheral.fail_open) { errno = EIO; return nullptr; }
  return new DIR;
}
struct dirent* readdir(DIR* directory) {
  static struct dirent entry;
  ++peripheral.directory_reads;
  if (int(directory->cursor) == peripheral.fail_read_at) { errno = EIO; return nullptr; }
  if (directory->cursor == peripheral.names.size()) return nullptr;
  entry = {};
  const std::string& name = peripheral.names[directory->cursor++];
  assert(name.size() < sizeof(entry.d_name));
  memcpy(entry.d_name, name.c_str(), name.size() + 1);
  if (peripheral.unterminated_name) memset(entry.d_name, 'x', sizeof(entry.d_name));
  if (peripheral.dirty_success_errno) errno = EIO;
  return &entry;
}
int closedir(DIR* directory) {
  ++peripheral.directory_closes;
  delete directory;
  if (peripheral.fail_close) { errno = EIO; return -1; }
  return 0;
}

class NativeFSImpl : public fs::FSImpl {
public:
  std::set<std::string> files;
  std::set<std::string> directories;
  fs::FileImplPtr last_open;
  bool fail_mutation = false, unreadable = false;
  bool rename_false_after_move = false, rename_noop_success = false;
  bool rename_metadata_error = false;
  NativeFSImpl() { mountpoint("/spiffs"); }
  static void requireInvalidated() {
    ++peripheral.mutations;
    assert(!mesh::esp32BootFileKnownAbsent(peripheral.owner, "/never-created"));
  }
  fs::FileImplPtr open(const char* path, const char* mode, bool create) override {
    ++peripheral.native_opens;
    const bool write = !mode || (strcmp(mode, "r") && strcmp(mode, "rb")) || create;
    if (write) {
      requireInvalidated();
      if (fail_mutation) return {};
      if (path) files.insert(path);
    }
    if (path && strlen(path) > 31) { errno = ENAMETOOLONG; return {}; }
    const bool present = path && files.count(path);
    peripheral.scans += present ? 3 : 2; // pinned VFS stat + constructor stat (+ fopen).
    if (unreadable && present) return {};
    last_open = std::make_shared<fs::FileImpl>();
    last_open->directory = !present || directories.count(path);
    last_open->name = path ? path : "";
    return last_open; // Missing r-open is a truthy SPIFFS Directory.
  }
  bool exists(const char* path) override {
    ++peripheral.native_exists;
    const bool present = path && files.count(path);
    peripheral.scans += present ? 3 : 2;
    return present && !unreadable;
  }
  bool rename(const char* from, const char* to) override {
    requireInvalidated();
    if (fail_mutation || !files.count(from) || files.count(to)) return false;
    if (rename_noop_success) return true;
    files.erase(from); files.insert(to);
    if (rename_metadata_error) peripheral.metadata_error = true;
    return !rename_false_after_move;
  }
  bool remove(const char* path) override {
    requireInvalidated();
    return !fail_mutation && files.erase(path);
  }
  bool mkdir(const char*) override { requireInvalidated(); return !fail_mutation; }
  bool rmdir(const char*) override { requireInvalidated(); return !fail_mutation; }
};
static NativeFSImpl* metadata_fs;
int stat(const char* absolute, struct stat* info) {
  ++peripheral.stats; ++peripheral.scans;
  if (peripheral.metadata_error) { errno = EIO; return -1; }
  assert(strncmp(absolute, "/spiffs", 7) == 0);
  if (!metadata_fs->files.count(absolute + 7)) { errno = ENOENT; return -1; }
  info->st_mode = 0100000;
  return 0;
}

// This shell executes the real transaction recovery's probes/rename policy.
#define FILESYSTEM fs::FS
namespace mesh {
class ContactFileTransaction {
public:
  using PresenceProbe = bool (*)(FILESYSTEM*, const char*, bool&);
  @PROBE@
  @MOVE_STATE@
  @MOVE@
  @RECOVER@
};
}
struct CommonCLI { bool recoverCommonPrefsFiles(fs::FS*); };
@COMMON_RECOVER@

static void populate(NativeFSImpl& native, const std::vector<std::string>& names) {
  peripheral.names = names;
  for (const std::string& name : names)
    native.files.insert(name[0] == '/' ? name : "/" + name);
}
static void requireInactiveFallback(mesh::Esp32BootFileSystem& view) {
  assert(!view.isInventoryActive());
  assert(!mesh::esp32BootFileKnownAbsent(&view, "/never-created"));
  const unsigned calls = peripheral.native_exists;
  assert(!view.exists("/never-created"));
  assert(peripheral.native_exists == calls + 1);
  const unsigned directory_calls = peripheral.directory_opens;
  assert(!view.beginInventory());
  assert(peripheral.directory_opens == directory_calls);
}

static void deadlineWorkload(fs::FS& fs) {
  assert(mesh::ContactFileTransaction::recover(&fs, "/com_prefs"));
  assert(!fs.exists("/com_prefs") && !fs.exists("/node_prefs") && !fs.exists("/prefs.json"));
  assert(mesh::ContactFileTransaction::recover(&fs, "/data_tx"));
  assert(!fs.exists("/data_tx") && !fs.exists("/management") && !fs.exists("/management.bak"));
  assert(mesh::ContactFileTransaction::recover(
      &fs, "/telemetry_history_tx", mesh::filePresence<fs::FS>));
  // Repeated metadata calls follow the empty ACL/replay startup recovery shape.
  for (const char* path : {"/s_contacts", "/s_contacts.bak", "/client_login",
                          "/client_login.bak", "/client_login.tmp", "/s_contacts",
                          "/s_contacts.bak", "/s_contacts.tmp", "/s_contacts",
                          "/telemetry_history_tx"}) {
    bool present = true;
    assert(mesh::filePresence(&fs, path, present) && !present);
  }
  for (const char* path : {"/flood_filters", "/flood_filters.tmp", "/flood_filters.bak",
                          "/clock_sync"}) assert(!fs.exists(path));
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string which = argv[1];
  auto native = std::make_shared<NativeFSImpl>();
  metadata_fs = native.get();
  fs::FS original(native);
  const unsigned construction_allocations = new_calls;
  mesh::Esp32BootFileSystem view(original);
  assert(new_calls == construction_allocations); // Embedded alias reuses SDK control block.
  peripheral.owner = &view;

  if (which == "timing_on" || which == "timing_off") {
    if (which == "timing_on") assert(view.beginInventory());
    deadlineWorkload(view);
    const unsigned elapsed = 1000 + 129 * peripheral.scans;
    std::cout << "{\"elapsed_ms\":" << elapsed << ",\"metadata_lookups\":" << peripheral.scans
              << ",\"native_exists\":" << peripheral.native_exists
              << ",\"native_opens\":" << peripheral.native_opens
              << ",\"snapshot_reads\":" << peripheral.directory_reads << "}\n";
    return 0;
  }

  if (which == "basic" || which == "owner" || which == "positive_fault" || which == "positive_directory") {
    populate(*native, {"/identity/_main.id", "com_prefs", "com_prefs.tmp",
                       "com_prefs.bak", "mqtt_prefs", "s_contacts", "corrupt", "Dir/leaf"});
    assert(view.beginInventory() && view.isInventoryActive());
    size_t expected_bytes = 0;
    for (const std::string& name : peripheral.names)
      expected_bytes += name.size() + (name[0] == '/' ? 1 : 2);
    assert(peripheral.allocations == 1 && peripheral.reallocations == 1);
    assert(peripheral.shrink_bytes == expected_bytes);
    assert(inventory_buffers.size() == 1 && inventory_buffers.begin()->second == expected_bytes);
    assert(!view.exists("/never-created"));
    assert(!view.open("/never-created", "r"));
    assert(!view.open("/never-created", "rb"));
    assert(peripheral.native_exists == 0 && peripheral.native_opens == 0);
    bool present = true;
    assert(mesh::filePresence(&view, "/never-created", present) && !present);
    assert(peripheral.stats == 0);
    if (which == "owner") {
      assert(!mesh::esp32BootFileKnownAbsent(&original, "/never-created"));
      assert(!mesh::esp32BootFileKnownAbsent(nullptr, "/never-created"));
      assert(mesh::filePresence(&original, "/never-created", present) && !present);
      assert(peripheral.stats == 1);
      assert(original.open("/never-created").isDirectory());
      mesh::Esp32BootFileSystem other_view(original);
      const unsigned directory_calls = peripheral.directory_opens;
      assert(!other_view.beginInventory());
      assert(peripheral.directory_opens == directory_calls);
      assert(!mesh::esp32BootFileKnownAbsent(&other_view, "/never-created"));
      assert(mesh::esp32BootFileKnownAbsent(&view, "/never-created"));
    } else if (which == "positive_fault") {
      peripheral.metadata_error = true;
      assert(!mesh::filePresence(&view, "/com_prefs", present));
      native->unreadable = true;
      assert(!view.open("/com_prefs"));
      assert(peripheral.stats == 1 && peripheral.native_opens == 1);
    } else if (which == "positive_directory") {
      native->directories.insert("/corrupt");
      const auto file = view.open("/corrupt");
      assert(file.isDirectory() && file.impl == native->last_open);
      assert(peripheral.native_opens == 1);
    } else {
      for (const char* path : {"/com_prefs", "/com_prefs.tmp", "/com_prefs.bak",
                              "/mqtt_prefs", "/s_contacts", "/corrupt"}) {
        assert(view.exists(path));
        auto file = view.open(path);
        assert(file.impl == native->last_open && !file.isDirectory());
      }
      assert(mesh::filePresence(&view, "/com_prefs", present) && present);
      assert(peripheral.stats == 1); // Positive metadata remains a real stat.
      const unsigned before = peripheral.native_opens;
      for (const char* path : {"/", "/identity", "/IDENTITY", "/dir", "/Dir/", "/Dir//leaf",
                              "/./missing", "/../missing", "relative"}) {
        assert(view.open(path)); // Delegate to original, preserving SDK directory behavior.
      }
      assert(peripheral.native_opens == before + 9);
      assert(!view.exists("/COM_PREFS")); // Exact file lookup stays case-sensitive.
      const std::string long_path = "/" + std::string(31, 'x');
      const unsigned long_before = peripheral.native_opens;
      errno = 0;
      assert(!view.open(long_path.c_str()) && errno == ENAMETOOLONG);
      assert(peripheral.native_opens == long_before + 1);
      assert(!mesh::esp32BootFileKnownAbsent(&view, long_path.c_str()));
    }
    assert(view.isInventoryActive());
    view.endInventory();
    assert(inventory_buffers.empty());
    requireInactiveFallback(view);
  } else if (which.rfind("recovery_", 0) == 0) {
    populate(*native, {"com_prefs.bak"});
    assert(view.beginInventory());
    native->rename_false_after_move = which == "recovery_false_after_move";
    native->rename_noop_success = which == "recovery_noop_success";
    native->rename_metadata_error = which == "recovery_metadata_error";
    native->fail_mutation = which == "recovery_no_move_failure";
    const bool recovered = mesh::ContactFileTransaction::recover(
        &view, "/com_prefs", mesh::filePresence<fs::FS>);
    const bool moved = !native->rename_noop_success && !native->fail_mutation;
    assert(recovered == (moved && !native->rename_metadata_error));
    assert(native->files.count("/com_prefs") == unsigned(moved));
    assert(native->files.count("/com_prefs.bak") == unsigned(!moved));
    assert(peripheral.mutations == 1);
    assert(peripheral.stats == (native->rename_metadata_error ? 2U : 3U));
    requireInactiveFallback(view);
  } else if (which == "rename_backup" || which == "rename_temp") {
    const char* source = which == "rename_backup" ? "/com_prefs.bak" : "/com_prefs.tmp";
    populate(*native, {source + 1});
    assert(view.beginInventory());
    assert(!view.exists("/com_prefs") && view.exists(source));
    if (which == "rename_backup") {
      assert(mesh::ContactFileTransaction::recover(&view, "/com_prefs"));
      assert(view.exists("/com_prefs") && !view.exists(source));
      assert(view.open("/com_prefs").impl == native->last_open);
    } else {
      CommonCLI cli;
      assert(cli.recoverCommonPrefsFiles(&view)); // First-save temp is uncommitted.
      assert(!view.exists("/com_prefs") && !view.exists(source));
    }
    requireInactiveFallback(view);
  } else if (which == "mutation_before_begin" || which == "end_before_begin") {
    if (which == "mutation_before_begin") view.open("/new", "w");
    else view.endInventory();
    assert(!view.beginInventory());
    assert(peripheral.directory_opens == 0 && peripheral.allocations == 0);
  } else if (which.rfind("mutation_", 0) == 0) {
    assert(view.beginInventory());
    native->fail_mutation = which.find("fail") != std::string::npos;
    const std::string operation = which.substr(9);
    if (operation == "rplus") view.open("/new", "r+");
    else if (operation == "rbplus") view.open("/new", "rb+");
    else if (operation == "write") view.open("/new", "w");
    else if (operation == "append") view.open("/new", "a");
    else if (operation == "unknown") view.open("/new", "unexpected");
    else if (operation == "null") view.open("/new", nullptr);
    else if (operation == "create") view.open("/new", "r", true);
    else if (operation == "open_fail") view.open("/new", "w");
    else if (operation == "rename_fail") assert(!view.rename("/absent", "/new"));
    else if (operation == "remove_fail") assert(!view.remove("/absent"));
    else if (operation == "mkdir") view.mkdir("/dir");
    else if (operation == "rmdir") view.rmdir("/dir");
    else assert(false);
    assert(peripheral.mutations == 1);
    requireInactiveFallback(view);
  } else if (which == "end" || which == "global_end") {
    assert(view.beginInventory());
    if (which == "end") view.endInventory();
    else mesh::endEsp32BootFileInventory();
    requireInactiveFallback(view);
  } else if (which == "null_backend") {
    fs::FS null_fs(fs::FSImplPtr{});
    mesh::Esp32BootFileSystem null_view(null_fs);
    assert(!null_view.beginInventory() && !null_view.isInventoryActive());
    assert(peripheral.directory_opens == 0 && peripheral.allocations == 0);
  } else if (which == "other_mount") {
    native->mountpoint("/sd");
    mesh::Esp32BootFileSystem other_view(original);
    assert(!other_view.beginInventory() && !other_view.isInventoryActive());
    assert(peripheral.directory_opens == 0 && peripheral.allocations == 0);
  } else if (which == "dirty_errno") {
    populate(*native, {"present"});
    peripheral.dirty_success_errno = true;
    assert(view.beginInventory()); // Stale errno from a successful read is not an EOF error.
    assert(view.isInventoryActive() && !view.exists("/missing"));
    assert(peripheral.directory_closes == 1);
  } else if (which == "empty") {
    assert(view.beginInventory());
    assert(view.isInventoryActive() && !view.exists("/missing") && !view.open("/missing"));
    assert(peripheral.native_exists == 0 && peripheral.native_opens == 0);
    assert(peripheral.allocations == 1 && peripheral.frees == 1 && peripheral.reallocations == 0);
    assert(inventory_buffers.empty());
  } else {
    populate(*native, {"present"});
    if (which == "open_error") peripheral.fail_open = true;
    else if (which == "close_error") peripheral.fail_close = true;
    else if (which == "read_error_first") peripheral.fail_read_at = 0;
    else if (which == "read_error_partial") peripheral.fail_read_at = 1;
    else if (which == "oom") peripheral.fail_malloc = true;
    else if (which == "shrink_failure") peripheral.fail_realloc = true;
    else if (which == "overflow") {
      peripheral.names.clear();
      for (unsigned i = 0; i < 400; ++i)
        peripheral.names.push_back(std::string(25, 'x') + std::to_string(i));
    } else if (which == "bad_name_empty") peripheral.names = {""};
    else if (which == "bad_name_dot") peripheral.names = {"../identity"};
    else if (which == "bad_name_repeat") peripheral.names = {"identity//key"};
    else if (which == "bad_name_absolute") peripheral.names = {"//identity/key"};
    else if (which == "bad_name_long") peripheral.names = {std::string(31, 'x')};
    else if (which == "bad_name_unterminated") peripheral.unterminated_name = true;
    else assert(false);
    assert(!view.beginInventory());
    assert(inventory_buffers.empty());
    if (which == "shrink_failure") {
      assert(peripheral.reallocations == 1 && peripheral.frees == 1);
      assert(peripheral.shrink_bytes == strlen("/present") + 1);
    }
    requireInactiveFallback(view);
    if (peripheral.directory_opens && !peripheral.fail_open)
      assert(peripheral.directory_closes == 1);
  }
}
'''


def compile_boot_inventory_harness(work, mutation=None, transaction_mutation=None):
    """Return a compiled actual-class harness; all generated files live in work."""
    work = Path(work)
    compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        raise unittest.SkipTest("a host C++17 compiler is required")
    (work / "sys").mkdir(exist_ok=True)
    (work / "FS.h").write_text(FS_HEADER, encoding="utf-8")
    (work / "FSImpl.h").write_text('#include "FS.h"\n', encoding="utf-8")
    (work / "Arduino.h").write_text('#include "FS.h"\n', encoding="utf-8")
    (work / "sdkconfig.h").write_text('#define CONFIG_SPIFFS_OBJ_NAME_LEN 32\n', encoding="utf-8")
    (work / "dirent.h").write_text(DIRENT_HEADER, encoding="utf-8")
    (work / "sys/stat.h").write_text(STAT_HEADER, encoding="utf-8")
    transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text(encoding="utf-8")
    if transaction_mutation is not None:
        transaction = transaction_mutation(transaction)
    probe = extract_braced(transaction, "static bool probe(")
    move_state = extract_braced(transaction, "enum class MoveState") + ";"
    move = extract_braced(transaction, "static MoveState moveState(")
    recover = extract_braced(transaction, "static bool recover(")
    common_recover = extract_braced(
        (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="utf-8"),
        "bool CommonCLI::recoverCommonPrefsFiles(")
    harness = HARNESS.replace("@PROBE@", probe).replace("@MOVE_STATE@", move_state).replace(
        "@MOVE@", move).replace("@RECOVER@", recover).replace("@COMMON_RECOVER@", common_recover)
    cpp = work / "harness.cpp"
    cpp.write_text(harness, encoding="utf-8")
    # Include real production under allocation-boundary substitution. Standard
    # declarations are read first; no production logic is replaced by a stub.
    production = PRODUCTION.read_text(encoding="utf-8")
    if mutation is not None:
        production = mutation(production)
    wrapped = work / "production.cpp"
    wrapped.write_text(
        '#include <cstdlib>\n#include <cstddef>\n'
        'void* inventory_malloc(size_t);\nvoid* inventory_realloc(void*, size_t);\n'
        'void inventory_free(void*);\n'
        'namespace std { using ::inventory_malloc; using ::inventory_realloc; using ::inventory_free; }\n'
        '#define malloc inventory_malloc\n#define realloc inventory_realloc\n#define free inventory_free\n' + production,
        encoding="utf-8")
    binary = work / "inventory"
    compiled = subprocess.run([
        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-DESP32_PLATFORM=1",
        *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
          if sys.platform.startswith("linux") else []),
        f"-I{work}", f"-I{ROOT / 'src'}", f"-I{PRODUCTION.parent}",
        str(cpp), str(wrapped), "-o", str(binary),
    ], capture_output=True, text=True, timeout=60)
    if compiled.returncode != 0:
        raise AssertionError(compiled.stdout + compiled.stderr)
    return binary


def run_boot_inventory_timing(disable_inventory=False):
    """Return modeled readiness and native lookup counts for GUI deadline tests."""
    with tempfile.TemporaryDirectory(prefix="boot-inventory-timing-") as folder:
        binary = compile_boot_inventory_harness(folder)
        case = "timing_off" if disable_inventory else "timing_on"
        result = subprocess.run([str(binary), case], cwd=folder,
                                capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
        return json.loads(result.stdout)


class Esp32BootFileInventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="boot-inventory-")
        cls.addClassCleanup(cls.folder.cleanup)
        cls.binary = compile_boot_inventory_harness(cls.folder.name)

    def run_case(self, case):
        result = subprocess.run([str(self.binary), case], cwd=self.folder.name,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_missing_positive_transaction_and_directory_paths(self):
        for case in ("basic", "empty", "rename_backup", "rename_temp", "positive_fault", "positive_directory"):
            with self.subTest(case=case):
                self.run_case(case)

    def test_recovery_observes_namespace_after_inventory_invalidation(self):
        for case in ("recovery_observed_move", "recovery_false_after_move",
                     "recovery_noop_success", "recovery_no_move_failure", "recovery_metadata_error"):
            with self.subTest(case=case):
                self.run_case(case)

    def test_old_rename_result_recovery_misreports_observed_namespace(self):
        def restore_rename_result(transaction):
            before = ("    fs->rename(backup, target);\n"
                      "    return moveState(fs, backup, target, presence) == MoveState::Moved;")
            self.assertEqual(transaction.count(before), 1)
            return transaction.replace(before, "    return fs->rename(backup, target);", 1)

        with tempfile.TemporaryDirectory(prefix="boot-recovery-negative-") as folder:
            binary = compile_boot_inventory_harness(folder, transaction_mutation=restore_rename_result)
            for case in ("recovery_false_after_move", "recovery_noop_success"):
                with self.subTest(case=case):
                    result = subprocess.run([str(binary), case], cwd=folder,
                                            capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("recovered == (moved && !native->rename_metadata_error)", result.stderr)

    def test_exact_owner_identity_and_permanent_end(self):
        for case in ("owner", "end", "global_end", "null_backend", "other_mount", "end_before_begin"):
            with self.subTest(case=case):
                self.run_case(case)

    def test_mutation_invalidates_before_delegation_even_failure(self):
        self.run_case("mutation_before_begin")
        for kind in ("rplus", "rbplus", "write", "append", "unknown", "null", "create",
                     "open_fail", "rename_fail", "remove_fail", "mkdir", "rmdir"):
            with self.subTest(kind=kind):
                self.run_case("mutation_" + kind)

    def test_complete_eof_is_required_and_stale_errno_is_cleared(self):
        for case in ("open_error", "read_error_first", "read_error_partial", "close_error", "dirty_errno"):
            with self.subTest(case=case):
                self.run_case(case)

    def test_oom_capacity_and_malformed_names_fall_back(self):
        for case in ("oom", "shrink_failure", "overflow", "bad_name_empty", "bad_name_dot", "bad_name_repeat",
                     "bad_name_absolute", "bad_name_long", "bad_name_unterminated"):
            with self.subTest(case=case):
                self.run_case(case)

    def test_native_lookup_cost_crosses_unchanged_gui_deadline(self):
        active = json.loads(self.run_case("timing_on"))
        disabled = json.loads(self.run_case("timing_off"))
        self.assertLess(active["elapsed_ms"], 5000)
        self.assertGreaterEqual(disabled["elapsed_ms"], 5000)
        self.assertEqual(active["native_exists"], 0)
        self.assertEqual(active["metadata_lookups"], 1)
        self.assertGreater(disabled["native_exists"], 0)

    def test_compiled_negative_controls_detect_missing_guards(self):
        controls = (
            ("skip_negative_open",
             "else if (owner_.knownAbsent(path)) return fs::FileImplPtr();",
             "else if (false) return fs::FileImplPtr();", "basic", "!view.open"),
            ("skip_mutation_invalidation", "owner_.endInventory();", "(void)0;",
             "mutation_write", "!mesh::esp32BootFileKnownAbsent"),
            ("accept_partial_scan", "complete = errno == 0;", "complete = true;",
             "read_error_partial", "!view.beginInventory()"),
            ("hide_positive_file", "strcmp(name, path) == 0", "false",
             "basic", "view.exists(path)"),
        )
        for name, before, after, case, expected_assertion in controls:
            with self.subTest(control=name):
                def mutate(source, before=before, after=after):
                    self.assertIn(before, source)
                    return source.replace(before, after)

                with tempfile.TemporaryDirectory(prefix="boot-inventory-negative-") as folder:
                    binary = compile_boot_inventory_harness(folder, mutate)
                    result = subprocess.run([str(binary), case], cwd=folder,
                                            capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected_assertion, result.stderr)


if __name__ == "__main__":
    unittest.main()
