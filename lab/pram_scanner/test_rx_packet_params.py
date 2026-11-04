#!/usr/bin/env python3
"""Run the actual scanner paths against ROM stubs and inspect packet bytes."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'lab/pram_scanner/scan.c').read_text()


def definition(signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    for pos in range(opening + 1, len(source)):
        if source[pos] == '{':
            depth += 1
        elif source[pos] == '}':
            depth -= 1
            if depth == 0:
                return source[start:pos + 1]
    raise AssertionError(f'Unterminated definition: {signature}')


program = r'''
#include <cassert>
#include <cstdint>
#include <vector>
using u32 = uint32_t;
using u8 = uint8_t;
struct resp { u32 len; u8* buf; };
struct chan { u32 freq; u8 mod0, mod1, peak, pre; };
static chan table[1];
#define TAB table
static u32 irq;
#define IRQ irq
#define IRQ_CAD_DET (1u << 7)
#define IRQ_CAD_DONE (1u << 20)
#define IRQ_PREAMBLE (1u << 5)
#define IRQ_HDR (1u << 6)
#define IRQ_RX_DONE (1u << 18)
#define IRQ_TIMEOUT (1u << 21)
#define IRQ_SCAN (IRQ_PREAMBLE | IRQ_HDR | IRQ_CAD_DET | IRQ_RX_DONE | IRQ_CAD_DONE | IRQ_TIMEOUT)
#define IRQ_OWN (IRQ_PREAMBLE | IRQ_HDR | IRQ_CAD_DET | IRQ_CAD_DONE | IRQ_TIMEOUT)
#define POLL_MAX 200000u
constexpr u32 S_CAD = 1;
static std::vector<std::vector<u8>> packet_calls;
static int ordinary(u32, const u8*, resp*) { return 2; }
static int packet(u32 n, const u8* p, resp*) {
  packet_calls.emplace_back(p, p + n); return 2;
}
static int cad(u32, const u8*, resp*) { irq = IRQ_CAD_DONE; return 2; }
#define H_SET_RF_FREQ ordinary
#define H_LORA_MOD ordinary
#define H_CAD_PARAMS ordinary
#define H_SET_CAD cad
#define H_LORA_PKT packet
static u32 wait_ticks(u32, u32) { return 1; }
static void yield() {}
''' + definition('struct bgs {') + r''';
static void hook_in(struct bgs*) {}
''' + definition('int scan_cmd(') + '\n' + definition('static void start_cad(') + '\n' + definition('int bench_cmd(') + r'''
int main() {
  table[0] = {910525000u, 0x73, 0x10, 60, 32};
  u8 out[40] = {};
  resp response{0, out};
  const u8 scan[] = {1, 1, 1, 8, 0x10, 0, 0, 1};
  assert(scan_cmd(sizeof(scan), scan, &response) == 3);
  assert(packet_calls.size() == 1);
  assert((packet_calls.back() == std::vector<u8>{0, 32, 255, 2}));

  packet_calls.clear();
  bgs state{};
  state.last_mod = 0xFFFFFFFFu;
  state.last_pre = 0x100;
  start_cad(&state);
  assert(packet_calls.size() == 1);
  assert((packet_calls.back() == std::vector<u8>{0, 32, 255, 2}));

  packet_calls.clear();
  u8 bench[] = {1, 0, 0, 0, 0, 0x73, 0x10, 72};
  assert(bench_cmd(sizeof(bench), bench, &response) == 3);
  assert(packet_calls.size() == 1);
  assert((packet_calls.back() == std::vector<u8>{0, 72, 255, 2}));
  bench[0] = 0;
  assert(bench_cmd(sizeof(bench), bench, &response) == 3);
  assert(packet_calls.size() == 1); // control mode does not configure radio
}
'''

with tempfile.TemporaryDirectory() as td:
    cpp = Path(td) / 'packet_params.cpp'
    exe = Path(td) / 'packet_params'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS scanner paths program maximum explicit-header RX length')
