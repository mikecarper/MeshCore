"""Exercise actual CustomLFS buffer declarations, DMA admission and build isolation."""
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nrf52_qspi_dma_fix', ROOT / 'scripts/nrf52_qspi_dma_fix.py')
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)
FIXTURE = ROOT / 'test/fixtures/customlfs_qspi_dma'
RAW = (FIXTURE / 'CustomLFS_QSPIFlash.cpp').read_text(encoding='ascii')


def configure_method(source):
    marker = 'void CustomLFS_QSPIFlash::_configure_lfs()'
    start = source.index(marker)
    opening = source.index('{', start)
    depth = 1
    for end in range(opening + 1, len(source)):
        if source[end] == '{': depth += 1
        elif source[end] == '}': depth -= 1
        if depth == 0: return source[start:end + 1]
    raise AssertionError('Unclosed reviewed configure method')


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#define HEX 16
struct SerialStub {
 template<class T> void print(T) {}
 template<class T> void println(T) {}
 template<class T> void println(T, int) {}
} Serial;
struct Config {
 void* context;
 void (*read)(); void (*prog)(); void (*erase)(); void (*sync)();
 uint32_t read_size, prog_size, block_size, block_count, lookahead;
 void *read_buffer, *prog_buffer, *lookahead_buffer, *file_buffer;
};
struct CustomLFS_QSPIFlash {
 Config _lfs_config{};
 bool _operation_pending = false, _qspi_initialized = true;
 uint32_t _sector_size = 4096, _total_size = 2097152;
 uint32_t _flash_addr = 0, _flash_total_size = 0, _block_size = 0;
 static void _qspi_read() {} static void _qspi_prog() {}
 static void _qspi_erase() {} static void _qspi_sync() {}
 void _configure_lfs();
};
@METHOD@
// Exact nrfx contract under test: RAM buffer must be word aligned. This
// adapter records admission and copies only after the alignment gate.
static unsigned admissions;
static bool dma(void* destination, const void* source, size_t bytes) {
 if ((reinterpret_cast<uintptr_t>(destination) & 3u) != 0 ||
     (reinterpret_cast<uintptr_t>(source) & 3u) != 0) return false;
 ++admissions; std::memcpy(destination, source, bytes); return true;
}
int main() {
 CustomLFS_QSPIFlash fs; fs._configure_lfs();
 auto saved = fs._lfs_config;
 assert(saved.context == &fs && saved.block_size == 4096 && saved.block_count == 512);
 assert(saved.read_size == 256 && saved.prog_size == 256 && saved.lookahead == 32);
 assert(saved.file_buffer == nullptr);
 for (void* buffer : {saved.read_buffer, saved.prog_buffer, saved.lookahead_buffer})
   assert((reinterpret_cast<uintptr_t>(buffer) & 3u) == 0);
 alignas(uint32_t) uint8_t pattern[256], media[256], bad[260];
 for (unsigned i=0; i<256; ++i) pattern[i] = static_cast<uint8_t>(i ^ 0xa5);
 std::memset(media, 0, sizeof(media)); std::memset(bad, 0, sizeof(bad));
 assert(!dma(bad + 1, pattern, 256) && !dma(media, bad + 1, 256));
 assert(admissions == 0 && bad[1] == 0 && media[0] == 0);
 assert(dma(saved.prog_buffer, pattern, 256));
 assert(dma(media, saved.prog_buffer, 256));
 assert(dma(saved.read_buffer, media, 256));
 assert(std::memcmp(saved.read_buffer, pattern, 256) == 0 && admissions == 3);
 fs._configure_lfs();
 assert(fs._lfs_config.read_buffer == saved.read_buffer);
 assert(fs._lfs_config.prog_buffer == saved.prog_buffer);
 assert(fs._lfs_config.lookahead_buffer == saved.lookahead_buffer);
 assert(std::memcmp(saved.read_buffer, pattern, 256) == 0);
}
'''


class QspiDmaFixTest(unittest.TestCase):
    def test_exact_source_transform_idempotence_and_unknown_refusal(self):
        fixed = fix.patched_source(RAW)
        self.assertEqual(fix.digest(RAW), fix.SOURCE_SHA256)
        self.assertEqual(fix.digest(fixed), fix.PATCHED_SHA256)
        self.assertEqual(fix.patched_source(fixed), fixed)
        self.assertEqual(fix.patched_source(RAW.replace('\n', '\r\n')), fixed)
        self.assertEqual(fixed.replace(fix.PATCHED_BUFFERS, fix.ORIGINAL_BUFFERS), RAW)
        for changed in (RAW + '// future dependency\n', RAW.replace('read_buffer[256]', 'read_buffer[128]'),
                        RAW.replace('static uint8_t read_buffer', 'alignas(uint32_t) static uint8_t read_buffer')):
            with self.subTest(changed=changed[-30:]), self.assertRaises(RuntimeError):
                fix.patched_source(changed)

    def test_compiled_actual_configure_method_retains_pools_and_dma_admission(self):
        compiler = shutil.which('c++') or shutil.which('g++')
        if not compiler: self.skipTest('Host C++ compiler required')
        code = HARNESS.replace('#include <cstring>', '#include <cstring>\n#include <initializer_list>')
        code = code.replace('@METHOD@', configure_method(fix.patched_source(RAW)))
        with tempfile.TemporaryDirectory(prefix='qspi-dma-native-') as temp:
            cpp = Path(temp) / 'check.cpp'; binary = Path(temp) / 'check'
            cpp.write_text(code, encoding='ascii')
            result = subprocess.run([compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                '-fsanitize=address,undefined', '-fno-pie', '-no-pie', str(cpp), '-o', str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_arm_declared_alignment_survives_odd_preceding_section(self):
        core = Path(os.environ.get('PLATFORMIO_CORE_DIR', Path.home() / '.platformio'))
        choices = [os.environ.get('MESHCORE_ARM_GXX', ''),
            str(core / 'packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-g++'),
            shutil.which('arm-none-eabi-g++') or '']
        compiler = next((p for p in choices if p and Path(p).is_file()), None)
        if not compiler: self.skipTest('ARM C++ compiler required for linked ABI check')
        # Keep the exact dependency declarations. The linker places a retained
        # one-byte object first; only declared alignment may insert padding.
        script = '''SECTIONS { .text 0x10000 : { *(.text*) }
 .bss 0x20000000 (NOLOAD) : { *(.bss.prefix) *(.bss.*read_buffer*)
 *(.bss.*prog_buffer*) *(.bss.*lookahead_buffer*) *(.bss*) } }'''
        for fixed in (False, True):
            with self.subTest(fixed=fixed), tempfile.TemporaryDirectory(prefix='qspi-dma-arm-') as temp:
                work = Path(temp); cpp = work / 'check.cpp'; elf = work / 'check.elf'
                declarations = fix.PATCHED_BUFFERS if fixed else fix.ORIGINAL_BUFFERS
                # Function-local static storage has the original lifetime and
                # symbol form, with no attributes added to the declarations.
                cpp.write_text('''#include <stdint.h>
extern "C" { __attribute__((used,section(".bss.prefix"))) unsigned char prefix[1]; }
extern "C" void* selected(unsigned index) {
''' + declarations + '''
 return index == 0 ? read_buffer : index == 1 ? prog_buffer : lookahead_buffer;
}
''', encoding='ascii')
                (work / 'layout.ld').write_text(script, encoding='ascii')
                result = subprocess.run([compiler, '-std=c++17', '-mcpu=cortex-m4', '-mthumb', '-Oz',
                    '-fdata-sections', '-ffunction-sections', '-nostdlib', '-Wl,--build-id=none',
                    '-Wl,-T,' + str(work / 'layout.ld'), str(cpp), '-o', str(elf)],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                sys.path.insert(0, str(ROOT / 'scripts'))
                from firmware_elf import FirmwareElf
                image = FirmwareElf(elf)
                buffers = {name: symbol[0] for name, symbol in image.symbols.items()
                           if any(buffer in name for buffer in ('read_buffer', 'prog_buffer', 'lookahead_buffer'))}
                self.assertEqual(len(buffers), 3)
                expected = 0 if fixed else 1
                self.assertTrue(all(address % 4 == expected for address in buffers.values()), buffers)

    def test_middleware_uses_reviewed_private_copies_and_leaves_dependency_untouched(self):
        class Node:
            def __init__(self, path): self.path = path
            def srcnode(self): return self
            def get_abspath(self): return str(self.path)
        class Env:
            def __init__(self, build): self.build = build; self.middleware = []
            def get(self, key, default=None): return default
            def subst(self, value):
                self_test.assertEqual(value, '$BUILD_DIR'); return str(self.build)
            def File(self, path): return Path(path)
            def AddBuildMiddleware(self, callback, pattern): self.middleware.append((callback, pattern))
        self_test = self
        with tempfile.TemporaryDirectory(prefix='qspi-dma-build-') as temp:
            root = Path(temp); source = root / 'libdeps/CustomLFS/src'; source.mkdir(parents=True)
            for path in FIXTURE.iterdir(): shutil.copyfile(path, source / path.name)
            originals = {p.name: p.read_bytes() for p in source.iterdir()}
            env = Env(root / 'build'); fix.install(env)
            self.assertEqual(env.middleware, [(fix.replace_qspi_source, '*CustomLFS_QSPIFlash.cpp')])
            node = Node(source / 'CustomLFS_QSPIFlash.cpp')
            target = fix.replace_qspi_source(env, node)
            self.assertEqual(target, env.build / 'patched-nrf52-qspi/CustomLFS_QSPIFlash.cpp')
            self.assertEqual(target.read_text(), fix.patched_source(RAW))
            self.assertEqual({p.name: p.read_bytes() for p in source.iterdir()}, originals)
            before = {p.name: p.stat().st_mtime_ns for p in target.parent.iterdir()}
            self.assertEqual(fix.replace_qspi_source(env, node), target)
            self.assertEqual({p.name: p.stat().st_mtime_ns for p in target.parent.iterdir()}, before)
            unrelated = Node(source / 'Other.cpp')
            self.assertIs(fix.replace_qspi_source(env, unrelated), unrelated)
            for name in fix.HEADERS:
                self.assertEqual((target.parent / name).read_bytes(), originals[name])
                (source / name).write_text('changed header', encoding='ascii')
                with self.assertRaises(RuntimeError): fix.replace_qspi_source(env, node)
                (source / name).write_bytes(originals[name])
            (source / 'CustomLFS.h').unlink()
            with self.assertRaises(RuntimeError): fix.replace_qspi_source(env, node)

    def test_resolved_qspi_recipes_retain_one_hook(self):
        from platformio.project.config import ProjectConfig
        config = ProjectConfig(str(ROOT / 'platformio.ini'), parse_extra=False)
        for path in sorted((ROOT / 'variants').glob('*/platformio.ini')):
            config.read(str(path), parse_extra=False)
        hook = 'pre:scripts/nrf52_qspi_dma_fix.py'
        selected = []
        for section in config.sections():
            if not section.startswith('env:'): continue
            dependencies = config.get(section, 'lib_deps', [])
            if not any('oltaco/CustomLFS' in item for item in dependencies): continue
            with self.subTest(target=section):
                self.assertEqual(config.get(section, 'extra_scripts', []).count(hook), 1)
            selected.append(section)
        self.assertGreater(len(selected), 100)
        for target in ('Xiao_nrf52_companion_radio_ble', 'Xiao_nrf52_companion_radio_usb',
                       'Xiao_nrf52_repeater', 'Mesh_pocket_companion_radio_usb'):
            self.assertIn('env:' + target, selected)

    def test_nrf52_base_and_ci_load_fix_once(self):
        config = (ROOT / 'platformio.ini').read_text(encoding='ascii')
        hook = 'pre:scripts/nrf52_qspi_dma_fix.py'
        nrf = config.split('[nrf52_base]', 1)[1].split('\n[', 1)[0]
        self.assertEqual(config.count(hook), 1)
        self.assertEqual(nrf.count(hook), 1)
        workflow = (ROOT / '.github/workflows/run-unit-tests.yml').read_text(encoding='ascii')
        # The required storage matrix now delegates its invocations to the
        # shard runner. Verify the real runner with fake Python, keeping this
        # guard tied to an actual once-only call rather than YAML text layout.
        self.assertIn('run: bash tools/ci/run_companion_routing_tests.sh "${{ matrix.suite }}"', workflow)
        self.assertIn('suite: [transport, preferences, storage]', workflow)
        import test_companion_routing_ci_split as routing_ci
        result, calls = routing_ci.CompanionRoutingCiSplitTest().run_shard(['storage'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.count(
            ['-B', 'test/test_nrf52_qspi_dma_fix.py', '-v']), 1)
        self.assertIn('bash build.sh build-firmware Xiao_nrf52_companion_radio_full', workflow)


if __name__ == '__main__': unittest.main()
