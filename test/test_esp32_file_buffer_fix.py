#!/usr/bin/env python3
"""Portable witnesses for the exact pinned Arduino/newlib buffer admission fix."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test/fixtures/esp32_file_buffer"
SPEC = importlib.util.spec_from_file_location("fs_buffer_fix", ROOT / "scripts/esp32_file_buffer_fix.py")
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)
RAW = (FIXTURES / "vfs_api.cpp").read_text()
PATCHED = FIX.patched_vfs_source(RAW)
SDK_VARIANTS = [((2, 0, 17), RAW, PATCHED, FIXTURES)]
for version in ((3, 1, 3), (3, 3, 11)):
    fixture = FIXTURES / ".".join(map(str, version))
    raw = json.loads((fixture / "vfs_api.json").read_text(encoding="ascii"))["source"]
    SDK_VARIANTS.append((version, raw, FIX.patched_vfs_source(raw, version), fixture))
# The reviewed 3.3.12 FS source is byte-identical; share its source witness.
SDK_VARIANTS.append(((3, 3, 12), raw, FIX.patched_vfs_source(raw, (3, 3, 12)), fixture))


def method(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for end in range(opening, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise AssertionError("unclosed function")


METHOD_PREFIX = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <set>
struct MeshFILE {
    struct { unsigned char* _base; int _size; } _bf{};
    int response = 0, selected_size = 0;
};
static unsigned calls = 0, payload_reads = 0, payload_writes = 0;
#define FILE MeshFILE
#define _IOFBF 0
static int setvbuf(FILE* f, char* supplied, int mode, size_t requested) {
    assert(f && supplied == nullptr && mode == _IOFBF);
    ++calls;
    f->_bf._size = f->selected_size ? f->selected_size : (requested ? requested : 4096);
    return f->response;
}
struct VFSFileImpl {
    bool _isDirectory;
    FILE* _f;
    bool setBufferSize(size_t);
};
'''

METHOD_MAIN = r'''
int main() {
    FILE stream{};
    VFSFileImpl file{false, &stream};
    for (size_t size : {1u, 64u, 128u, 152u, 251u, 4096u}) {
        stream.selected_size = 0;
        assert(file.setBufferSize(size));
        assert(static_cast<size_t>(stream._bf._size) == size);
    }
    stream.response = -1;
    stream.selected_size = 251;
    assert(!file.setBufferSize(251)); // even an equal stored size cannot mask error
    stream.response = 0;
    stream.selected_size = 4096;
    assert(!file.setBufferSize(251)); // real newlib successful larger fallback
    assert(!file.setBufferSize(128));
    stream.selected_size = -1;
    assert(!file.setBufferSize(251));
    stream.selected_size = 152;
    assert(!file.setBufferSize(251)); // any mismatched positive size is rejected
    stream.selected_size = 4096;
    assert(file.setBufferSize(0)); // unchanged choose-default public semantics
    stream.response = -1;
    assert(!file.setBufferSize(0));
    const unsigned before = calls;
    VFSFileImpl directory{true, &stream}, missing{false, nullptr};
    assert(!directory.setBufferSize(251) && !missing.setBufferSize(251));
    assert(calls == before && payload_reads == 0 && payload_writes == 0);
}
'''

TRANSACTION_MOCK = r'''
class Filesystem;
class File {
    Filesystem* _fs = nullptr;
    bool _writer = false;
    std::shared_ptr<FILE> _stream;
public:
    File() = default;
    File(Filesystem*, bool);
    operator bool() const { return static_cast<bool>(_stream); }
    bool setBufferSize(size_t size) {
        VFSFileImpl impl{false, _stream.get()};
        return impl.setBufferSize(size);
    }
    size_t write(const uint8_t*, size_t);
    size_t read(uint8_t*, size_t) { ++payload_reads; return 0; }
    size_t size() const;
    void flush() {}
    void close() { _stream.reset(); }
};
class Filesystem {
public:
    bool writer_fallback = false, reader_fallback = false;
    unsigned renames = 0;
    size_t length = 0;
    std::set<std::string> paths{"/contacts3"};
    bool exists(const char* path) { return paths.count(path); }
    bool remove(const char* path) { return paths.erase(path); }
    bool rename(const char*, const char*) { ++renames; return true; }
    File open(const char* path, const char* mode, bool = false) {
        if (*mode == 'w') { paths.insert(path); length = 0; }
        return File(this, *mode == 'w');
    }
};
File::File(Filesystem* fs, bool writer) : _fs(fs), _writer(writer), _stream(new FILE{}) {
    _stream->selected_size = (writer ? fs->writer_fallback : fs->reader_fallback) ? 4096 : 0;
}
size_t File::size() const { return _fs->length; }
size_t File::write(const uint8_t*, size_t len) { ++payload_writes; _fs->length += len; return len; }
#define FILESYSTEM Filesystem
#define ESP32_PLATFORM 1
'''

TRANSACTION_MAIN = r'''
int main() {
    uint8_t record[mesh::storage::CONTACT_RECORD_SIZE]{};
    Filesystem fs;
    fs.writer_fallback = true;
    {
        mesh::ContactFileTransaction transaction(&fs, "/contacts3");
        assert(!transaction);
        assert(transaction.write(record, sizeof(record)) == 0);
        assert(!transaction.commit());
        assert(payload_writes == 0 && payload_reads == 0 && fs.renames == 0);
    }
    assert(fs.exists("/contacts3") && !fs.exists("/contacts3.tmp"));
    fs.writer_fallback = false;
    fs.reader_fallback = true;
    {
        mesh::ContactFileTransaction transaction(&fs, "/contacts3");
        assert(transaction);
        assert(transaction.write(record, sizeof(record)) == sizeof(record));
        assert(transaction.serviceCommit() == mesh::ContactFileTransaction::CommitProgress::Pending);
        assert(transaction.serviceCommit() == mesh::ContactFileTransaction::CommitProgress::Failed);
        assert(payload_reads == 0 && payload_writes == 1 && fs.renames == 0);
    }
    assert(fs.exists("/contacts3") && !fs.exists("/contacts3.tmp"));
}
'''


class Node:
    def __init__(self, path): self.path = path
    def srcnode(self): return self
    def get_abspath(self): return str(self.path)
class Env(dict):
    def __init__(self, defines, build):
        super().__init__(CPPDEFINES=defines)
        self.build, self.paths, self.middleware = build, [], []
    def subst(self, value):
        assert value == "$BUILD_DIR"
        return str(self.build)
    def File(self, path): return path
    def AppendUnique(self, **kwargs):
        for path in kwargs["CPPPATH"]:
            if path not in self.paths: self.paths.append(path)
    def AddBuildMiddleware(self, callback, pattern): self.middleware.append((callback, pattern))

class FileBufferFixTest(unittest.TestCase):
    def compile_run(self, source, *, success=True, compile_failure=False, headers=None):
        with tempfile.TemporaryDirectory(prefix="mesh-fs-buffer-") as directory:
            temp = Path(directory)
            (temp / "test.cpp").write_text(source)
            for name, contents in (headers or {}).items():
                (temp / name).write_text(contents)
            built = subprocess.run([
                "c++", "-std=c++11", "-Os", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I", str(temp), "-I", str(ROOT / "src"),
                str(temp / "test.cpp"), "-o", str(temp / "test")],
                capture_output=True, text=True, timeout=30)
            if compile_failure:
                self.assertNotEqual(built.returncode, 0)
                self.assertIn("MeshCore FS buffer fix", built.stderr)
                return
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(temp / "test")], capture_output=True, text=True, timeout=10)
            if success:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0, "unsafe negative control unexpectedly passed")
                self.assertIn("file.setBufferSize(251)", result.stderr)

    def test_actual_method_honored_fallback_errors_zero_and_no_io(self):
        for version, _, patched, _ in SDK_VARIANTS:
            with self.subTest(version=version):
                self.compile_run(METHOD_PREFIX + method(patched, "bool VFSFileImpl::setBufferSize(") + METHOD_MAIN)

    def test_upstream_success_only_negative_control_rejects_fallback(self):
        for version, raw, _, _ in SDK_VARIANTS:
            with self.subTest(version=version):
                self.compile_run(METHOD_PREFIX + method(raw, "bool VFSFileImpl::setBufferSize(") + METHOD_MAIN,
                                 success=False)

    def test_actual_transaction_rejects_writer_and_verifier_fallback_before_io(self):
        transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text().replace(
            '#include "IdentityStore.h"', '#include "identity_seam.h"').replace(
            '#include "PersistentStoreFormat.h"', '#include <helpers/PersistentStoreFormat.h>').replace(
            '#pragma once\n', '', 1)
        for version, _, patched, _ in SDK_VARIANTS:
            with self.subTest(version=version):
                self.compile_run(METHOD_PREFIX + method(patched, "bool VFSFileImpl::setBufferSize(")
                                 + TRANSACTION_MOCK + transaction + TRANSACTION_MAIN,
                                 headers={"identity_seam.h": "#pragma once\n"})

    def layout_source(self, fixture=FIXTURES, guard=FIX.LAYOUT_GUARD):
        # Execute the exact header's reviewed prefix with 32-bit pointer values
        # on a native 64-bit host. The target copy additionally asserts void*32.
        reent = (fixture / "newlib_sys_reent.h").read_text()
        sbuf = method(reent, "struct __sbuf {") + ";\n"
        start = reent.index("struct __sFILE {")
        end = reent.index("#ifdef _REENT_SMALL", start)
        prefix = reent[start:end] + "};\n"
        fields = (sbuf + prefix).replace("unsigned char *", "ModelPointer ")
        source = "#include <cstdint>\nusing ModelPointer = uint32_t;\n" + fields
        source += "using FILE = __sFILE;\n"
        source += guard.replace("sizeof(void*)", "sizeof(ModelPointer)")
        source += "int main() {}\n"
        return source

    def test_exact_pinned_file_prefix_and_newlib_guards(self):
        version = (FIXTURES / "_newlib_version.h").read_text()
        headers = {"newlib.h": version}
        self.compile_run(self.layout_source(), headers=headers)
        for broken in (self.layout_source().replace("struct __sbuf _bf;", "int added; struct __sbuf _bf;"),
                       self.layout_source().replace("int\t_size;", "long long\t_size;")):
            self.compile_run(broken, headers=headers, compile_failure=True)
        self.compile_run(self.layout_source(), headers={"newlib.h": version.replace(
            "#define __NEWLIB__ 3", "#define __NEWLIB__ 4")}, compile_failure=True)

    def test_arduino3_actual_newlib_prefix_rejects_changed_layout_and_abi(self):
        fixture = FIXTURES / "newlib4"
        version = (fixture / "_newlib_version.h").read_text(encoding="ascii")
        source = self.layout_source(fixture, FIX.LAYOUT_GUARD_ARDUINO3)
        self.compile_run(source, headers={"newlib.h": version})
        for broken in (source.replace("struct __sbuf _bf;", "int added; struct __sbuf _bf;"),
                       source.replace("int\t_size;", "long long\t_size;")):
            self.compile_run(broken, headers={"newlib.h": version}, compile_failure=True)
        self.compile_run(source, headers={"newlib.h": version.replace(
            "#define __NEWLIB_MINOR__ 3", "#define __NEWLIB_MINOR__ 4")}, compile_failure=True)
        self.assertEqual(hashlib.sha256((fixture / "newlib_sys_reent.h").read_bytes()).hexdigest(),
                         "c7ba657f911f7e27b7449bda36aa4efa80a025c726768dabb8d4a792f535ec9a")

    def test_every_supported_sdk_uses_its_own_source_pin_and_keeps_shared_sdk_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="mesh-fs-multi-sdk-") as directory:
            temp = Path(directory)
            source = temp / "sdk/libraries/FS/src/vfs_api.cpp"
            source.parent.mkdir(parents=True)
            header = temp / "sdk/cores/esp32/esp_arduino_version.h"
            header.parent.mkdir(parents=True)
            node = Node(source)
            for version, raw, patched, fixture in SDK_VARIANTS:
                with self.subTest(version=version):
                    original_header = (fixture / "esp_arduino_version.h").read_bytes()
                    if version == (3, 3, 12):
                        original_header = original_header.replace(b"VERSION_PATCH 11", b"VERSION_PATCH 12")
                    source.write_text(raw, encoding="utf-8")
                    header.write_bytes(original_header)
                    self.assertEqual(FIX.require_pinned_framework(source), version)
                    self.assertTrue(patched.isascii())
                    self.assertEqual(FIX.patched_vfs_source(patched, version), patched)
                    self.assertEqual(FIX.patched_vfs_source(raw.replace("\n", "\r\n"), version), patched)
                    for mode in (None, 0, 1):
                        defines = ["ESP32_PLATFORM"]
                        if mode is not None:
                            defines += [("ARDUINO_USB_MODE", mode), ("ARDUINO_USB_CDC_ON_BOOT", 1)]
                        env = Env(defines, temp / (str(version) + str(mode)))
                        result = Path(FIX.replace_vfs_source(env, node))
                        self.assertEqual(result.read_text(encoding="ascii"), patched)
                        self.assertEqual(source.read_text(encoding="utf-8"), raw)
                        self.assertEqual(header.read_bytes(), original_header)
                        before = result.stat().st_mtime_ns
                        self.assertEqual(Path(FIX.replace_vfs_source(env, node)).stat().st_mtime_ns, before)
                    for changed in (raw + "\n", patched + "\n",
                                    raw.replace("return res == 0;", "return true;", 1)):
                        with self.assertRaises(RuntimeError): FIX.patched_vfs_source(changed, version)
                    for other_version, other_raw, _, _ in SDK_VARIANTS:
                        if other_version != version and other_raw != raw:
                            with self.assertRaises(RuntimeError): FIX.patched_vfs_source(raw, other_version)
                    header.write_bytes(original_header + b"\n#define ESP_ARDUINO_VERSION_MAJOR 9\n")
                    with self.assertRaises(RuntimeError): FIX.replace_vfs_source(env, node)
            with self.assertRaises(RuntimeError): FIX.patched_vfs_source(RAW, (3, 3, 13))

    def test_exact_fingerprints_shape_normalization_and_idempotence(self):
        self.assertEqual(hashlib.sha256((FIXTURES / "vfs_api.cpp").read_bytes()).hexdigest(), FIX.PINNED_VFS_SHA256)
        self.assertEqual(FIX.patched_vfs_source(RAW.replace("\n", "\r\n")), PATCHED)
        self.assertEqual(FIX.patched_vfs_source(PATCHED), PATCHED)
        self.assertEqual(PATCHED, RAW.replace(FIX.INCLUDE, FIX.INCLUDE + FIX.LAYOUT_GUARD, 1).replace(
            FIX.ORIGINAL_METHOD, FIX.STRICT_METHOD, 1))
        for changed in (RAW + "\n", PATCHED + "\n", RAW.replace("return res == 0;", "return true;", 1)):
            with self.assertRaises(RuntimeError):
                FIX.patched_vfs_source(changed)
        expected = {
            "esp_arduino_version.h": "b63e57731a24289b06eb96ded9e481ea719f4cde2ce7c41bd83c7646e0a6fe51",
            "newlib_sys_reent.h": "33391d61a83ebbfb122e80514fbf7ed4d770556d8e8d46e029addbbf6077cd79",
            "_newlib_version.h": "93a397574a25b9c143cee7fcb86d8fc9a0c9ba418e4710a30d2d19ba1e4120db"}
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), digest)

    def test_build_local_copy_all_usb_modes_guards_and_shared_bytes(self):
        with tempfile.TemporaryDirectory(prefix="mesh-fs-copy-") as directory:
            temp = Path(directory)
            source = temp / "sdk/libraries/FS/src/vfs_api.cpp"
            source.parent.mkdir(parents=True)
            source.write_bytes((FIXTURES / "vfs_api.cpp").read_bytes())
            version = temp / "sdk/cores/esp32/esp_arduino_version.h"
            version.parent.mkdir(parents=True)
            version.write_bytes((FIXTURES / "esp_arduino_version.h").read_bytes())
            node = Node(source)
            for defines in ([], ["RP2040_PLATFORM"], [("ESP32_PLATFORM", 0)]):
                env = Env(defines, temp / "untouched")
                self.assertIs(FIX.replace_vfs_source(env, node), node)
                self.assertFalse(env.build.exists())
            for index, usb_mode in enumerate((0, 1, None)):
                defines = [("ESP32_PLATFORM", 1)]
                if usb_mode is not None:
                    defines += [("ARDUINO_USB_MODE", usb_mode), ("ARDUINO_USB_CDC_ON_BOOT", 1)]
                env = Env(defines, temp / ("build" + str(index)))
                result = Path(FIX.replace_vfs_source(env, node))
                self.assertEqual(result.read_text(), PATCHED)
                self.assertEqual(env.paths, [str(source.parent)])
                self.assertEqual(source.read_bytes(), (FIXTURES / "vfs_api.cpp").read_bytes())
                before = result.stat().st_mtime_ns
                self.assertEqual(Path(FIX.replace_vfs_source(env, node)).stat().st_mtime_ns, before)
            env = Env(["ESP32"], temp / "strict")
            unrelated = Node(temp / "unrelated.cpp")
            self.assertIs(FIX.replace_vfs_source(env, unrelated), unrelated)
            FIX.install(env)
            self.assertEqual(env.middleware, [(FIX.replace_vfs_source, "*libraries*FS*src*vfs_api.cpp")])
            for header in ("", version.read_text().replace("VERSION_PATCH   17", "VERSION_PATCH   18"),
                           version.read_text() + "\n#define ESP_ARDUINO_VERSION_MAJOR 2\n"):
                version.write_text(header)
                with self.assertRaises(RuntimeError): FIX.replace_vfs_source(env, node)
                self.assertFalse(env.build.exists())
            version.unlink()
            with self.assertRaises(RuntimeError): FIX.replace_vfs_source(env, node)
            version.write_bytes((FIXTURES / "esp_arduino_version.h").read_bytes())
            source.write_text(RAW + "\n")
            with self.assertRaises(RuntimeError): FIX.replace_vfs_source(env, node)
            self.assertFalse(env.build.exists())


if __name__ == "__main__":
    unittest.main()
