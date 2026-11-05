#!/usr/bin/env python3
"""Exercise the real PRAM timer-repair helper against simulated IRQ races."""
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
        if source[pos] == '{': depth += 1
        elif source[pos] == '}':
            depth -= 1
            if depth == 0: return source[start:pos + 1]
    raise AssertionError(signature)


program = r'''
#include <cassert>
#include <cstdint>
using u32=uint32_t; using u8=uint8_t;
struct chan { u32 freq; u8 mod0,mod1,peak,pre; };
static chan table[1];
#define TAB table
#define RUNNING 0x5CA90001u
constexpr u32 S_RX=3;
#define IRQ_HDR (1u<<6)
#define IRQ_RX_DONE (1u<<18)
#define IRQ_TIMEOUT (1u<<21)
#define IRQ_HDR_ERR (1u<<9)
#define IRQ_CRC (1u<<22)
#define IRQ_HEADER_TERMINAL (IRQ_HDR|IRQ_RX_DONE|IRQ_TIMEOUT|IRQ_HDR_ERR)
#define IRQ_HEADER_BLOCKED (IRQ_HEADER_TERMINAL|IRQ_CRC|(1u<<23))
static u32 irq=0, pending=0, armed=0, arm_calls=0, stop_calls=0, last_ticks=0, irq_during_arm=0;
#define IRQ irq
#define EV_PENDING pending
#define RX_TIMEOUT_ARMED armed
static u32 arm(u32 ticks) { ++arm_calls; last_ticks=ticks; armed=1; irq|=irq_during_arm; return 0; }
static u32 stop() { ++stop_calls; armed=0; return 0; }
#define ROM_RX_TIMEOUT_ARM arm
#define ROM_RX_TIMEOUT_STOP stop
''' + definition('struct bgs {') + ';\n' + definition('static u32 wait_ticks(') + '\n' + definition('static u32 arm_header_timer(') + r'''
static bgs ready() {
  bgs b{}; b.running=RUNNING; b.state=S_RX;
  table[0]={904850000u,0x75,0x10,60,96};
  irq=1u<<5; pending=armed=arm_calls=stop_calls=last_ticks=irq_during_arm=0;
  return b;
}
int main() {
  auto b=ready(); assert(arm_header_timer(&b,1,1)==1);
  assert(arm_calls==1 && last_ticks==1880 && armed && b.header_repairs==1);
  assert(arm_header_timer(&b,1,1)==0 && arm_calls==1); // never reset an armed timer
  armed=0; assert(arm_header_timer(&b,1,1)==1 && b.header_repairs==2);
  armed=0; assert(arm_header_timer(&b,1,1)==0 && arm_calls==2); // bounded
  const u32 terminal[]={IRQ_HDR,IRQ_RX_DONE,IRQ_TIMEOUT,IRQ_HDR_ERR,IRQ_CRC,1u<<23};
  for (u32 flag: terminal) { b=ready(); irq|=flag; assert(!arm_header_timer(&b,1,1) && !arm_calls); }
  b=ready(); pending=2; assert(!arm_header_timer(&b,1,1) && !arm_calls);
  b=ready(); pending=1; assert(arm_header_timer(&b,1,1)); // current host command only
  b=ready(); assert(!arm_header_timer(&b,0,1) && !arm_calls); // lost ownership
  b=ready(); b.state=4; assert(!arm_header_timer(&b,1,1)); // parked
  b=ready(); irq_during_arm=IRQ_RX_DONE;
  assert(arm_header_timer(&b,1,1) && !armed && stop_calls==1 && (irq&IRQ_RX_DONE));
  b=ready(); irq_during_arm=IRQ_HDR_ERR;
  assert(arm_header_timer(&b,1,1) && !armed && stop_calls==1 && (irq&IRQ_HDR_ERR));
  b=ready(); table[0].pre=255;
  assert(arm_header_timer(&b,1,1) && last_ticks==wait_ticks(0x75,271));
}
'''
with tempfile.TemporaryDirectory() as td:
    cpp=Path(td)/'header.cpp'; exe=Path(td)/'header'
    cpp.write_text(program)
    subprocess.run(['g++','-std=c++17',str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
print('PASS bounded PRAM header-timer repairs preserve IRQs and long preambles')
