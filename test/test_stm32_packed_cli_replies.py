#!/usr/bin/env python3
"""Qualify STM32 reply packing without changing CLI bytes or buffer contracts."""
import importlib.util
import json
import os
from pathlib import Path
import random
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get('PLATFORMIO_CORE_DIR', Path.home() / '.platformio'))
IMPLEMENTATION = ROOT / 'src/helpers/Stm32PackedCliReplies.cpp'


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BPE = load_script('stm32_test_output_bpe', 'cli_output_bpe.py')


def compile_native(source, directory, extra=()):
    compiler = shutil.which('g++')
    if not compiler:
        raise unittest.SkipTest('native G++ unavailable')
    main, binary = directory / 'check.cpp', directory / 'check'
    main.write_text(source, encoding='ascii')
    subprocess.run([compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra',
                    '-fno-omit-frame-pointer', *SANITIZER_FLAGS, *extra,
                    str(main), '-o', str(binary)], check=True, timeout=90)
    return binary


class Stm32OutputBpeTest(unittest.TestCase):
    def test_dictionary_is_deterministic_acyclic_and_bounded(self):
        texts = ['Error: radio profile unavailable', 'Error: invalid radio profile',
                 'OK: radio profile saved', 'radio profile ' * 9] * 10
        codec = BPE.train(texts)
        self.assertEqual(codec.pairs, BPE.train(texts).pairs)
        self.assertEqual(codec.pairs, BPE.train(list(reversed(texts))).pairs)
        self.assertLessEqual(codec.max_depth, BPE.MAX_DEPTH)
        self.assertLessEqual(len(codec.pairs), 128)
        for token, pair in enumerate(codec.pairs, 128):
            self.assertTrue(all(0 < child < token for child in pair))
        for text in texts:
            encoded = codec.encode(text)
            self.assertEqual(encoded[-1:], b'\0')
            self.assertNotIn(0, encoded[:-1])
            self.assertEqual(codec.encode(text, terminated=False), encoded[:-1])
            self.assertEqual(codec.decode(encoded), text.encode('ascii'))
        self.assertEqual(BPE.train([]).encode(''), b'\0')
        malformed = [[(0, 1)], [(128, 1)], [(129, 1)], [(1, 256)],
                     [(1,)], [(1, 2, 3)], [('a', 1)], [(1.5, 2)],
                     [(65, 66)] * 129,
                     [(65, 66)] + [(127 + depth, 65) for depth in range(1, 7)]]
        for pairs in malformed:
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                BPE.BytePairCodec(pairs)
        for text in ('\0', '\x80', 'caf\xe9', b'\0', b'\xff'):
            with self.subTest(text=text), self.assertRaises((ValueError, UnicodeEncodeError)):
                codec.encode(text)
        for parameters in ({'max_pairs': -1}, {'max_pairs': 129},
                           {'max_depth': 0}, {'max_depth': BPE.MAX_DEPTH + 1}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                BPE.train(texts, **parameters)
        with self.assertRaises(ValueError):
            BPE.BytePairCodec([]).decode(b'\x80\0')

    def test_generated_decoder_preserves_bytes_lengths_and_buffer_guards(self):
        codec = BPE.train(['Error: invalid radio profile', 'Error: radio unavailable',
                           'OK: radio profile saved', 'radio profile '] * 20)
        rng = random.Random(0x1847BC)
        cases = []
        for text in ('', ''.join(map(chr, range(1, 128))), 'a' * 4096, 'Error: ' * 1000):
            cases.append((codec.encode(text), text.encode('ascii')))
        for token in range(1, 128 + len(codec.pairs)):
            packed = bytes([token, 0])
            cases.append((packed, codec.decode(packed)))
        for _ in range(1000):
            text = ''.join(chr(rng.randrange(1, 128)) for _ in range(rng.randrange(513)))
            packed = codec.encode(text)
            self.assertEqual(codec.decode(packed), text.encode('ascii'))
            cases.append((packed, text.encode('ascii')))
        for _ in range(1000):
            packed = bytes(rng.randrange(1, 128 + len(codec.pairs))
                           for _ in range(rng.randrange(129))) + b'\0'
            cases.append((packed, codec.decode(packed)))
        program = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>
#include <algorithm>
namespace packed {
''' + codec.cpp_decoder() + r'''}
namespace partial {
''' + BPE.BytePairCodec([(65, 66)]).cpp_decoder() + r'''}
namespace empty {
''' + BPE.BytePairCodec([]).cpp_decoder() + r'''}
static size_t checks;
static void check(bool ok) { if (!ok) abort(); ++checks; }
static uint32_t word(FILE* file) {
  uint8_t bytes[4];check(fread(bytes,1,4,file)==4);
  return uint32_t(bytes[0]) | uint32_t(bytes[1])<<8 |
         uint32_t(bytes[2])<<16 | uint32_t(bytes[3])<<24;
}
static void run(const uint8_t* input, const uint8_t* original, size_t size) {
  check(packed::unpack(nullptr,0,input)==size);
  check(packed::unpack(nullptr,100,input)==size);
  std::vector<size_t> capacities{0,1,2,size/2,size,size+1,size+2};
  if (size<150) for(size_t cap=0;cap<size+3;++cap)capacities.push_back(cap);
  for(size_t capacity:capacities) {
    std::vector<unsigned char> output(capacity+2,0xa5);
    check(packed::unpack(reinterpret_cast<char*>(output.data()+1),capacity,input)==size);
    check(output.front()==0xa5 && output.back()==0xa5);
    if(!capacity)check(output[1]==0xa5);
    else {
      const size_t copied=std::min(size,capacity-1);
      check(memcmp(output.data()+1,original,copied)==0);
      check(output[copied+1]==0);
      for(size_t i=copied+2;i<=capacity;++i)check(output[i]==0xa5);
    }
  }
}
int main(int argc,char** argv) {
  check(argc==2);FILE* file=fopen(argv[1],"rb");check(file!=nullptr);
  const size_t count=word(file);
  for(size_t n=0;n<count;++n) {
    const size_t packedSize=word(file),originalSize=word(file);
    std::vector<uint8_t> input(packedSize),original(originalSize+1);
    check(fread(input.data(),1,packedSize,file)==packedSize);
    check(fread(original.data(),1,originalSize,file)==originalSize);
    run(input.data(),original.data(),originalSize);
  }
  check(fgetc(file)==EOF);fclose(file);
  unsigned char guard[4]={0xa5,0xa5,0xa5,0xa5};
  check(packed::unpack(reinterpret_cast<char*>(guard+1),0,nullptr)==0);
  check(guard[1]==0xa5);
  check(packed::unpack(reinterpret_cast<char*>(guard+1),1,nullptr)==0);
  check(guard[0]==0xa5 && guard[1]==0 && guard[2]==0xa5);
  const uint8_t unknown[]={129,0};guard[1]=0xa5;
  check(partial::unpack(reinterpret_cast<char*>(guard+1),2,unknown)==size_t(-1));
  check(guard[0]==0xa5 && guard[1]==0 && guard[2]==0xa5 && guard[3]==0xa5);
  check(partial::unpack(nullptr,0,unknown)==size_t(-1));
  const uint8_t first[]={128,0};
  check(empty::unpack(nullptr,0,first)==size_t(-1));
  check(packed::unpack(nullptr,0,nullptr)==0);
  printf("BPE buffer parity: %zu checks\n",checks);
}
'''
        with tempfile.TemporaryDirectory(prefix='stm32-bpe-guards-') as folder:
            folder = Path(folder)
            fixture = folder / 'cases.bin'
            with fixture.open('wb') as stream:
                stream.write(struct.pack('<I', len(cases)))
                for packed, original in cases:
                    stream.write(struct.pack('<II', len(packed), len(original)))
                    stream.write(packed)
                    stream.write(original)
            binary = compile_native(program, folder)
            subprocess.run([str(binary), str(fixture)], check=True, timeout=30)


class Stm32PackedReplyOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = load_script('stm32_test_packed_cli', 'stm32_companion_packed_cli.py')
        cls.header = cls.pack.generated_header()
        cls.codec = BPE.BytePairCodec(cls.pack.MANIFEST['pairs'])
        cls.arrays = {}
        for name, values in re.findall(r'const uint8_t\s+(\w+)\[\]\s*=\s*\{([^}]+)\}', cls.header):
            encoded = bytes(int(value.strip()) for value in values.split(',') if value.strip())
            if not encoded or encoded[-1] != 0 or 0 in encoded[:-1]:
                raise AssertionError('Generated literal is not exactly NUL terminated')
            cls.arrays[cls.codec.decode(encoded).decode('ascii')] = name
        cls.records = []
        for relative in cls.pack.MANIFEST['sources']:
            source = (ROOT / relative).read_text(encoding='utf-8')
            cls.records.extend(cls.pack.source_records(source))

    def test_model_arrays_and_source_guards_match_reviewed_sources(self):
        self.assertEqual(len(self.pack.MANIFEST['training']), 149)
        self.assertEqual(len(self.codec.pairs), 128)
        self.assertEqual(self.codec.max_depth, self.pack.MANIFEST['max_depth'])
        self.assertLessEqual(self.codec.max_depth, BPE.MAX_DEPTH)
        self.assertEqual(len(self.records), 221)
        texts = {text for record in self.records for text in record['texts']}
        self.assertEqual(len(texts), 199)
        self.assertEqual(set(self.arrays), texts)
        retrained = BPE.train(self.pack.MANIFEST['training'])
        self.assertEqual(self.codec.pairs, retrained.pairs)
        for relative in self.pack.MANIFEST['sources']:
            original = (ROOT / relative).read_text(encoding='utf-8')
            patched = self.pack.patched_source(relative, original)
            self.assertIn('Stm32PackedCliReplies.h', patched)
            self.assertIn('::mesh::cli::outputpack::', patched)
            self.assertNotEqual(original, patched)
            with self.assertRaises(ValueError):
                self.pack.patched_source(relative, original + '\n')
        with self.assertRaises(ValueError):
            self.pack.patched_source('unreviewed.cpp', 'strcpy(reply,"Error");')
        relative = next(iter(self.pack.SOURCES))
        original = (ROOT / relative).read_text(encoding='utf-8')
        expected = self.pack.SOURCES[relative]
        corrupt = [dict(expected, input_sha='0' * 64),
                   dict(expected, output_sha='0' * 64),
                   dict(expected, call_count=expected['call_count'] + 1),
                   dict(expected, function_counts={})]
        for changed in corrupt:
            with self.subTest(manifest=changed), patch.dict(self.pack.SOURCES, {relative: changed}):
                with self.assertRaises(ValueError):
                    self.pack.patched_source(relative, original)

    def test_parser_ignores_noncode_and_handles_cpp_literal_rules(self):
        expression = r'"A\101\x42\n\t\"\\" /* join */ "tail"'
        expected = 'AAB\n\t"\\tail'
        self.assertEqual(self.pack.parse_literal_expression(expression), (expected,))
        pair = 'MESH_CLI_REPLY_TEXT(' + expression + ',"short")'
        self.assertEqual(self.pack.parse_literal_expression(pair), (expected, 'short'))
        self.assertIsNone(self.pack.parse_literal_expression('dynamic_value'))
        for unsupported in (r'"bad\q"', r'"\x"', r'"\0"', r'"\x80"',
                            '"\u00e9"', 'MESH_CLI_REPLY_TEXT("a",MESH_CLI_REPLY_TEXT("b","c"))'):
            with self.subTest(unsupported=unsupported), self.assertRaises(self.pack.UnsupportedLiteral):
                self.pack.parse_literal_expression(unsupported)
        fixture = r'''
// strcpy(reply,"eligible");
/* sprintf(reply,"eligible"); */
const char* ignored="strcpy(reply,\"eligible\");";
const char* raw=R"label(strcpy(reply,"eligible");)label";
object.strcpy(reply,"eligible"); other::strcpy(reply,"eligible");
strcpy(reply,"eligible");
snprintf(reply,capacity,MESH_CLI_REPLY_TEXT("verbose","eligible"));
'''
        records = self.pack.source_records(fixture, eligible={'eligible'})
        self.assertEqual([record['function'] for record in records], ['strcpy', 'snprintf'])
        self.assertEqual([record['texts'] for record in records], [('eligible',), ('verbose', 'eligible')])
        for source in ('const char* text="truncated\\', '/* unterminated', 'R"tag(missing close'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.pack.source_records(source, eligible={'eligible'})

    def test_actual_cpp_literals_and_wrappers_preserve_both_text_modes(self):
        program = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <helpers/CLIReplyText.h>
#include <helpers/Stm32PackedCliReplies.h>
namespace packed=mesh::cli::outputpack;
static size_t checks;
static void checkLiteral(const uint8_t* input,const char* original) {
  unsigned char actual[512],reference[512];
  memset(actual,0xa5,sizeof(actual));memset(reference,0xa5,sizeof(reference));
  char* expected=strcpy(reinterpret_cast<char*>(reference)+8,original);
  char* result=packed::copyPacked(reinterpret_cast<char*>(actual)+8,input);
  assert(result==reinterpret_cast<char*>(actual)+8);
  assert(expected==reinterpret_cast<char*>(reference)+8);
  assert(memcmp(actual,reference,sizeof(actual))==0);++checks;
}
static void checkError(const uint8_t* input,const char* original) {
  unsigned char actual[512],reference[512];
  for(size_t capacity=0;capacity<strlen(original)+12;++capacity) {
    memset(actual,0xa5,sizeof(actual));memset(reference,0xa5,sizeof(reference));
    snprintf(reinterpret_cast<char*>(reference)+8,capacity,"Error: %s",original);
    packed::errorPacked(reinterpret_cast<char*>(actual)+8,capacity,input);
    assert(memcmp(actual,reference,sizeof(actual))==0);++checks;
  }
  packed::errorPacked(nullptr,0,input);
}
int main() {
static_assert(packed::FormatCapacity==97,"Reviewed format buffer changed");
'''
        for record in self.records:
            names = ['packed::' + self.arrays[text] for text in record['texts']]
            selected = names[0] if len(names) == 1 else 'MESH_CLI_REPLY_TEXT(' + ','.join(names) + ')'
            # Keep the exact original source expression for the C++ compiler.
            # Reconstructing its expected value in Python would miss escape drift.
            program += 'checkLiteral(' + selected + ',' + record['expression'] + ');\n'
        for text, name in sorted(self.arrays.items()):
            program += 'checkError(packed::' + name + ',' + json.dumps(text) + ');\n'
        formats = [('%s %% %d %u %ld %lu %.9g %.3f %c',
                    '"sample",-123,123u,-1234567L,1234567UL,-12.75,0.125,65'),
                   ('> %.9g,%u,%s,%lu', '-0.0,65535u,"tail",4294967295UL'),
                   ('%s', '""'), ('%%', '0'),
                   ('%d %d %.12f', '-2147483647,2147483647,1.0e-30'), ('', '0')]
        for text, arguments in formats:
            encoded = ','.join(str(value) for value in self.codec.encode(text))
            arguments = ',' + arguments if arguments else ''
            program += r'''{
const char* format=FORMAT;const uint8_t input[]={ENCODED};
unsigned char actual[512],reference[512];
for(size_t capacity=0;capacity<192;++capacity) {
  memset(actual,0xa5,sizeof(actual));memset(reference,0xa5,sizeof(reference));
  int expected=snprintf(reinterpret_cast<char*>(reference)+8,capacity,formatARGS);
  int result=packed::snprintfPacked(reinterpret_cast<char*>(actual)+8,capacity,inputARGS);
  assert(result==expected);assert(memcmp(actual,reference,sizeof(actual))==0);++checks;
}
for(const char* initial:{"","head:","a substantially longer initial response"}) {
  for(size_t capacity=0;capacity<192;++capacity) {
    memset(actual,0xa5,sizeof(actual));memset(reference,0xa5,sizeof(reference));
    strcpy(reinterpret_cast<char*>(actual)+8,initial);
    strcpy(reinterpret_cast<char*>(reference)+8,initial);
    const size_t used=strlen(initial);
    if(used<capacity)snprintf(reinterpret_cast<char*>(reference)+8+used,capacity-used,formatARGS);
    packed::appendPacked(reinterpret_cast<char*>(actual)+8,capacity,inputARGS);
    assert(memcmp(actual,reference,sizeof(actual))==0);++checks;
  }
}
packed::appendPacked(nullptr,0,inputARGS);
assert(packed::snprintfPacked(nullptr,0,inputARGS)==snprintf(nullptr,0,formatARGS));
memset(actual,0xa5,sizeof(actual));memset(reference,0xa5,sizeof(reference));
int expected=sprintf(reinterpret_cast<char*>(reference)+8,formatARGS);
int result=packed::printfPacked(reinterpret_cast<char*>(actual)+8,inputARGS);
assert(result==expected);assert(memcmp(actual,reference,sizeof(actual))==0);++checks;
}
'''.replace('FORMAT', json.dumps(text)).replace('ENCODED', encoded).replace('ARGS', arguments)
        program += 'printf("Packed source/wrapper parity: %zu checks\\n",checks);}\n'
        program = '#include <initializer_list>\n' + program
        with tempfile.TemporaryDirectory(prefix='stm32-packed-wrappers-') as folder:
            folder = Path(folder)
            (folder / 'Stm32PackedCliRepliesGenerated.h').write_text(self.header, encoding='ascii')
            for compact in (0, 1):
                with self.subTest(compact=compact):
                    binary = compile_native(program, folder,
                        ['-DSTM32_PLATFORM=1', '-D' + self.pack.FLAG + '=1',
                         '-DMESH_CLI_COMPACT_REPLIES=' + str(compact),
                         '-I', str(ROOT / 'src'), '-I', str(folder), str(IMPLEMENTATION)])
                    subprocess.run([str(binary)], check=True, timeout=30)

    def test_arm_format_frames_remain_bounded_and_disabled_builds_need_no_generated_header(self):
        compiler = shutil.which('arm-none-eabi-g++')
        if not compiler:
            candidates = sorted((CORE / 'packages').glob('toolchain-gccarmnoneeabi*/bin/arm-none-eabi-g++*'))
            compiler = next((str(path) for path in candidates if path.is_file()), None)
        if not compiler:
            if os.environ.get('MESHCORE_REQUIRE_ARM_COMPILER') == '1':
                self.fail('ARM compiler is required for packed CLI frame qualification')
            self.skipTest('ARM compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='stm32-packed-arm-') as folder:
            folder = Path(folder)
            object_file = folder / 'wrappers.o'
            flags = [compiler, '-std=c++11', '-Os', '-mcpu=cortex-m4', '-mthumb',
                     '-fno-exceptions', '-fno-rtti', '-fstack-usage',
                     '-I', str(ROOT / 'src/helpers'), '-I', str(folder)]
            # Off, zero and other-platform builds compile with no generated file.
            for defines in ([], ['-DSTM32_PLATFORM=1', '-D' + self.pack.FLAG + '=0'],
                            ['-DNRF52_PLATFORM=1', '-D' + self.pack.FLAG + '=1']):
                subprocess.run(flags + defines + ['-c', str(IMPLEMENTATION), '-o', str(object_file)],
                               check=True, timeout=60)
            (folder / 'Stm32PackedCliRepliesGenerated.h').write_text(self.header, encoding='ascii')
            subprocess.run(flags + ['-DSTM32_PLATFORM=1', '-D' + self.pack.FLAG + '=1',
                                   '-c', str(IMPLEMENTATION), '-o', str(object_file)],
                           check=True, timeout=60)
            frames = (folder / 'wrappers.su').read_text()
            for function in ('unpack', 'copyPacked', 'printfPacked', 'snprintfPacked',
                             'errorPacked', 'appendPacked'):
                lines = [line for line in frames.splitlines() if function + '(' in line]
                if not lines:
                    # Some ARM GCC releases render a variadic signature as ')'
                    # in .su output. Its exact source location is still stable.
                    declaration = re.compile(r'^(?:char\*|int|void)\s+' + function + r'\(')
                    locations = [number for number, line in enumerate(
                        IMPLEMENTATION.read_text().splitlines(), 1) if declaration.search(line)]
                    self.assertEqual(len(locations), 1, function)
                    prefix = str(IMPLEMENTATION) + ':' + str(locations[0]) + ':'
                    lines = [line for line in frames.splitlines() if line.startswith(prefix)]
                self.assertEqual(len(lines), 1, function + ': ' + frames)
                fields = lines[0].rsplit('\t', 2)
                self.assertEqual(fields[2], 'static', lines[0])
                self.assertLessEqual(int(fields[1]), 192, lines[0])


class PackingEnvironment(dict):
    def subst(self, value):
        return value.replace('$PROJECT_DIR', str(ROOT)).replace('$BUILD_DIR', str(self['test_build']))

    def BoardConfig(self):
        return {'build.mcu': self['test_mcu']}

    def PioPlatform(self):
        return type('Platform', (), {'name': self.get('test_platform', 'ststm32')})()

    def GetProjectOption(self, name, default=None):
        return self.get('SRC_FILTER', default) if name == 'build_src_filter' else default

    def AddBuildMiddleware(self, callback, pattern):
        self.setdefault('hooks', []).append((callback, pattern))

    def Append(self, **values):
        for key, items in values.items():
            self.setdefault(key, []).extend(items)

    def AppendUnique(self, **values):
        for key, items in values.items():
            for item in items:
                if item not in self.setdefault(key, []):
                    self[key].append(item)

    def Clone(self, **changes):
        clone = PackingEnvironment(self)
        clone.update(changes)
        self.setdefault('clones', []).append(clone)
        return clone

    def File(self, path):
        return PackingNode(path)

    def Object(self, node, **options):
        self.setdefault('objects', []).append((node, options))
        return [PackingNode(node.get_abspath() + '.o')]


class PackingNode:
    def __init__(self, path):
        self.path = str(path)

    def srcnode(self):
        return self

    def get_abspath(self):
        return self.path


class Stm32PackedReplyPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = load_script('stm32_test_packed_policy', 'stm32_companion_packed_cli.py')
        cls.roles = load_script('stm32_test_packed_roles', 'nrf52_flash_trim.py')

    def environment(self, directory, **changes):
        flags = ['-DSTM32_PLATFORM=1', '-DSTM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC=1',
                 '-DMESH_CLI_SHARE_REPLY_COPIES=1', '-D' + self.pack.FLAG + '=1']
        values = dict(PIOENV='RAK_3x72_companion_radio_usb', test_mcu='stm32wle5ccu',
                      test_build=directory, SRC_FILTER=['+<../examples/companion_radio>'],
                      CPPDEFINES=[], BUILD_FLAGS=flags, CPPPATH=[])
        values.update(changes)
        return PackingEnvironment(values)

    def test_only_four_exact_mcus_and_exclusive_usb_companions_qualify(self):
        expected = {'wio-e5-mini_companion_radio_usb': 'stm32wle5jc',
                    'wio-e5_companion_radio_usb': 'stm32wle5jc',
                    'RAK_3x72_companion_radio_usb': 'stm32wle5ccu',
                    'Tiny_Relay_companion_radio_usb': 'stm32wle5ccu'}
        self.assertEqual(self.pack.TARGETS, expected)
        with tempfile.TemporaryDirectory(prefix='stm32-packed-policy-') as folder:
            for name, mcu in expected.items():
                env = self.environment(folder, PIOENV=name, test_mcu=mcu)
                self.assertTrue(self.pack.install(env))
                self.assertTrue(env.get('hooks'))
            invalid = [dict(PIOENV='RAK_3x72_repeater'),
                       dict(PIOENV='wio-e5-mini_sensor'),
                       dict(PIOENV='RAK_3x72_companion_radio_usb_custom'),
                       dict(test_mcu='stm32wle5jc'),
                       dict(test_mcu='nrf52840'),
                       dict(test_platform='espressif32'),
                       dict(SRC_FILTER=[]),
                       dict(SRC_FILTER=['+<../examples/companion_radio/main.cpp>']),
                       dict(SRC_FILTER=['+<../examples/companion_radio/MyMesh.cpp>']),
                       dict(SRC_FILTER=['+<../examples/companion_radio>',
                                        '-<../examples/companion_radio/MyMesh.cpp>'])]
            baseline = self.environment(folder)['BUILD_FLAGS']
            for key in ('STM32_PLATFORM', 'STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC',
                        'MESH_CLI_SHARE_REPLY_COPIES'):
                invalid.append(dict(BUILD_FLAGS=[flag for flag in baseline if not flag.startswith('-D'+key+'=')]))
                invalid.append(dict(BUILD_FLAGS=baseline + ['-D' + key + '=0']))
            for source in self.roles.ROLE_SOURCES + self.roles.COMPANION_SOURCES[1:]:
                invalid.append(dict(SRC_FILTER=['+<../examples/companion_radio>', '+<' + source + '>']))
            for changes in invalid:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    self.pack.install(self.environment(folder, **changes))
            # A later role exclusion must be honored rather than scanning text.
            env = self.environment(folder, SRC_FILTER=['+<../examples/companion_radio>',
                '+<../examples/simple_repeater>', '-<../examples/simple_repeater>'])
            self.assertTrue(self.pack.install(env))

    def test_explicit_flag_off_and_normalized_overrides_are_respected(self):
        with tempfile.TemporaryDirectory(prefix='stm32-packed-override-') as folder:
            baseline = self.environment(folder)['BUILD_FLAGS']
            off = [dict(BUILD_FLAGS=baseline[:-1]),
                   dict(BUILD_FLAGS=baseline + ['-D' + self.pack.FLAG + '=0']),
                   dict(BUILD_FLAGS=baseline + ['-U' + self.pack.FLAG],
                        CPPDEFINES=[(self.pack.FLAG, 1)]),
                   dict(BUILD_FLAGS=baseline[:-1], CPPDEFINES=[(self.pack.FLAG, '(0)')])]
            for changes in off:
                env = self.environment(folder, **changes)
                self.assertFalse(self.pack.install(env))
                self.assertNotIn('hooks', env)
                self.assertEqual(env['CPPPATH'], [])
            on = [dict(BUILD_FLAGS=baseline[:-1], CPPDEFINES=[(self.pack.FLAG, 1)]),
                  dict(BUILD_FLAGS=baseline[:-1], CPPDEFINES=[self.pack.FLAG]),
                  dict(BUILD_FLAGS=' '.join(baseline[:-1]) + ' -D ' + self.pack.FLAG + "='1'"),
                  dict(BUILD_FLAGS=baseline, CPPDEFINES=[(self.pack.FLAG, 0)])]
            for changes in on:
                with self.subTest(changes=changes):
                    self.assertTrue(self.pack.install(self.environment(folder, **changes)))

    def test_middleware_uses_private_copies_and_preserves_existing_compile_options(self):
        relative = 'examples/companion_radio/MyMesh.cpp'
        original = ROOT / relative
        before = original.read_bytes()
        with tempfile.TemporaryDirectory(prefix='stm32-packed-middleware-') as folder:
            env = self.environment(folder, CXXFLAGS=['-std=gnu++17', '-fno-exceptions'],
                                   CCFLAGS=['-Os'], CPPPATH=['existing-include'])
            self.assertTrue(self.pack.install(env))
            callbacks = dict((pattern, callback) for callback, pattern in env['hooks'])
            self.assertEqual(set(callbacks), {'*MyMesh.cpp', '*RadioProfileCLI.cpp', '*CommonRadioPrefs.cpp'})
            callback = callbacks['*MyMesh.cpp']
            for path in (ROOT / 'src/helpers/CompanionHelpPacked.cpp',
                         Path(folder) / 'already-built-MyMesh.cpp.o',
                         ROOT / 'src/helpers/MyMesh.cpp'):
                node = PackingNode(path)
                self.assertIs(callback(env, node), node)
            result = callback(env, PackingNode(original))
            self.assertTrue(result.get_abspath().endswith('MyMesh.cpp.o'))
            self.assertEqual(env['CXXFLAGS'], ['-std=gnu++17', '-fno-exceptions'])
            self.assertEqual(env['CCFLAGS'], ['-Os'])
            self.assertEqual(len(env['clones']), 1)
            clone = env['clones'][0]
            self.assertEqual(clone['CXXFLAGS'], env['CXXFLAGS'])
            self.assertEqual(clone['CCFLAGS'], env['CCFLAGS'])
            self.assertEqual(clone['CPPPATH'][0], str(original.parent))
            self.assertIn('existing-include', clone['CPPPATH'])
            self.assertEqual(len(clone['objects']), 1)
            private = Path(folder) / 'stm32-packed-cli' / relative
            self.assertEqual(private.read_text(), self.pack.patched_source(relative, before.decode()))
            header = Path(folder) / 'stm32-packed-cli/Stm32PackedCliRepliesGenerated.h'
            self.assertEqual(header.read_text(), self.pack.generated_header())
            self.assertEqual(original.read_bytes(), before)

    def test_scons_entrypoint_works_without_python_file_global(self):
        script = ROOT / 'scripts/stm32_companion_packed_cli.py'
        with tempfile.TemporaryDirectory(prefix='stm32-packed-scons-') as folder:
            env = self.environment(folder)
            imports = []
            namespace = {'__name__': 'stm32_test_scons_exec', 'env': env,
                         'Import': imports.append}
            previous = Path.cwd()
            try:
                os.chdir(ROOT)
                exec(compile(script.read_text(encoding='utf-8'), str(script), 'exec'), namespace)
            finally:
                os.chdir(previous)
            self.assertNotIn('__file__', namespace)
            self.assertEqual(imports, ['env'])
            self.assertEqual(namespace['ROOT'], ROOT)
            self.assertEqual(namespace['TARGETS'], self.pack.TARGETS)
            self.assertEqual(len(env['hooks']), 3)


if __name__ == '__main__':
    unittest.main()
