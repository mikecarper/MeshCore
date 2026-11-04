#!/usr/bin/env python3
"""Check the actual PRAM tick conversion against a wide-integer reference."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'lab/pram_scanner/scan.c').read_text()
start = source.index('static u32 wait_ticks(')
opening = source.index('{', start)
depth = 1
for pos in range(opening + 1, len(source)):
    if source[pos] == '{':
        depth += 1
    elif source[pos] == '}':
        depth -= 1
        if depth == 0:
            function = source[start:pos + 1]
            break
else:
    raise AssertionError('Unterminated wait_ticks definition')

program = r'''
#include <cassert>
#include <cstdint>
using u32 = uint32_t;
''' + function + r'''
static u32 reference(u32 sf, u32 code, u32 symbols) {
  if (!symbols) return 1;
  if (sf >= 32) return 0xFFFFFFu;
  uint64_t bw = code == 2 ? 31250u : code == 3 ? 62500u :
      code == 5 ? 250000u : code == 6 ? 500000u : 125000u;
  // Test sf <= 12 here: even UINT32_MAX symbols fit before multiplication.
  uint64_t ticks = (((uint64_t(symbols) << sf) * 32768u) + bw - 1) / bw;
  return ticks > 0xFFFFFFu ? 0xFFFFFFu : u32(ticks ? ticks : 1);
}
int main() {
  assert(wait_ticks(0x75, 48) == 806);  // 32 + 16, SF7/250 kHz
  assert(wait_ticks(0x75, 112) == 1880); // 96 + 16, SF7/250 kHz
  assert(wait_ticks(0x73, 8) == 537);    // SF7/62.5 kHz
  assert(wait_ticks(0x205, 0) == 1);     // zero symbols, invalid shift
  assert(wait_ticks(0x205, 1) == 0xFFFFFFu);

  const u32 codes[] = {0, 2, 3, 4, 5, 6, 7, 15};
  for (u32 sf = 0; sf <= 12; ++sf) {
    for (u32 code : codes) {
      const u32 mod = (sf << 4) | code;
      for (u32 symbols = 0; symbols <= 512; ++symbols)
        assert(wait_ticks(mod, symbols) == reference(sf, code, symbols));
      // Probe around each band's exact 24-bit saturation boundary.
      const uint64_t bw = code == 2 ? 31250u : code == 3 ? 62500u :
          code == 5 ? 250000u : code == 6 ? 500000u : 125000u;
      const uint64_t boundary = (uint64_t(0xFFFFFFu) * bw) / (uint64_t(32768u) << sf);
      for (int delta = -2; delta <= 2; ++delta) {
        const uint64_t count = boundary + delta;
        if (count <= UINT32_MAX)
          assert(wait_ticks(mod, u32(count)) == reference(sf, code, u32(count)));
      }
      assert(wait_ticks(mod, UINT32_MAX) == reference(sf, code, UINT32_MAX));
    }
  }
  u32 seed = 0x12345678u;
  for (unsigned i = 0; i < 100000; ++i) {
    seed = seed * 1664525u + 1013904223u; const u32 symbols = seed;
    seed = seed * 1664525u + 1013904223u; const u32 sf = seed % 13;
    const u32 code = codes[(seed >> 8) % (sizeof(codes) / sizeof(codes[0]))];
    assert(wait_ticks((sf << 4) | code, symbols) == reference(sf, code, symbols));
  }
}
'''

with tempfile.TemporaryDirectory() as td:
    cpp = Path(td) / 'wait_ticks.cpp'
    exe = Path(td) / 'wait_ticks'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', '-O2', str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS actual PRAM timeout rounding and saturation')
