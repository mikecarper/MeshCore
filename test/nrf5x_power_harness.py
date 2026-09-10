#!/usr/bin/env python3
"""Execute the real nRF5x power handler against write-one-to-clear registers.

No board/SDK is required. The negative control restores the inherited READY
prefix and must reproduce the hang, not merely fail a source-text assertion.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>

static std::function<void(int)> on_read;
static std::function<void()> on_barrier;
static bool auto_ready = true, clock_requested, clock_ok = true, irq_enabled;
static unsigned clock_reads, clock_delay, clock_requests, ready_reads, unplugged;
static std::function<void()> on_clock;
enum { ENABLE, PULLUP, CAUSE, PLAIN };
static constexpr uint32_t USBD_EVENTCAUSE_READY_Msk = 1u << 11;
struct Register {
  int kind;
  uint32_t value = 0;
  explicit Register(int k = PLAIN): kind(k) {}
  operator uint32_t() const {
    if (kind == CAUSE) ++ready_reads;
    if (on_read) on_read(kind);
    return value;
  }
  void operator=(uint32_t v);
};
static struct {
  Register ENABLE{::ENABLE}, USBPULLUP{PULLUP}, EVENTCAUSE{CAUSE};
  Register INTENCLR, INTENSET, INTEN, ISOSPLIT;
} usbd;
void Register::operator=(uint32_t v) {
  if (kind == CAUSE) value &= ~v; // hardware event register is W1C
  else value = v;
  if (kind == ENABLE && v && auto_ready)
    usbd.EVENTCAUSE.value |= USBD_EVENTCAUSE_READY_Msk;
}
#define NRF_USBD (&usbd)
#define CFG_TUSB_DEBUG 0
static constexpr unsigned USBD_INTEN_USBEVENT_Msk = 1, USBD_INTEN_USBRESET_Msk = 2;
static constexpr unsigned USBD_ISOSPLIT_SPLIT_HalfIN = 0, USBD_IRQn = 39;
static constexpr unsigned DCD_EVENT_UNPLUGGED = 0;
static void hfclk_enable() { clock_requested = true; ++clock_requests; }
static void hfclk_disable() { clock_requested = false; }
static bool hfclk_running() {
  ++clock_reads;
  if (on_clock) on_clock();
  return clock_requested && clock_ok && clock_reads > clock_delay;
}
static void __ISB() { if (on_barrier) on_barrier(); }
static void __DSB() { if (on_barrier) on_barrier(); }
static void NVIC_ClearPendingIRQ(unsigned) {}
static void NVIC_EnableIRQ(unsigned) { irq_enabled = true; }
static void NVIC_DisableIRQ(unsigned) { irq_enabled = false; }
static bool tud_inited() { return true; }
static bool is_in_isr() { return false; }
static void dcd_event_bus_signal(unsigned, unsigned, bool) { ++unplugged; }

/* DRIVER_FUNCTION */

static void reset() {
  usbd.ENABLE.value = usbd.USBPULLUP.value = usbd.EVENTCAUSE.value = 0;
  on_read = {}; on_barrier = {}; on_clock = {};
  auto_ready = clock_ok = true;
  clock_requested = irq_enabled = false;
  clock_reads = clock_delay = clock_requests = ready_reads = unplugged = 0;
}
static void detected() { tusb_hal_nrf_power_event(0); }
static void ready() { tusb_hal_nrf_power_event(2); }
static void removed() { tusb_hal_nrf_power_event(1); }
static void bounded() { assert(clock_reads + ready_reads <= 100010); }

int main(int argc, char** argv) {
  // Exact live failure state: attached, consumed READY, clock no longer running.
  if (argc > 1 && !std::strcmp(argv[1], "consumed-ready")) {
    reset(); usbd.ENABLE = 1; usbd.USBPULLUP = 1;
    usbd.EVENTCAUSE.value = 0;
    ready();
    assert(clock_requested && usbd.USBPULLUP.value == 1); bounded();
    return 0;
  }
  reset(); detected(); ready();
  assert(usbd.USBPULLUP.value == 1 && irq_enabled);
  assert(usbd.EVENTCAUSE.value == 0 && clock_requested);
  ready(); bounded();
  removed();
  assert(!usbd.ENABLE.value && !usbd.USBPULLUP.value && !irq_enabled);
  assert(!clock_requested && unplugged == 1);

  // READY delivered after removal must not request clocks or reattach.
  reset(); ready();
  assert(!clock_requests && !usbd.USBPULLUP.value); bounded();

  // Missing peripheral READY and missing HFXO must return, never attach.
  reset(); auto_ready = false; detected(); ready();
  assert(!usbd.USBPULLUP.value); bounded();
  usbd.EVENTCAUSE.value = USBD_EVENTCAUSE_READY_Msk; ready();
  assert(usbd.USBPULLUP.value == 1); // subsequent event can retry
  reset(); clock_ok = false; detected(); ready();
  assert(!usbd.USBPULLUP.value); bounded();
  clock_ok = true; ready(); assert(usbd.USBPULLUP.value == 1);

  // Both clock and regulator may take a little time to settle.
  reset(); auto_ready = false; clock_delay = 10; detected();
  on_read = [](int kind) {
    if (kind == CAUSE && ready_reads == 10)
      usbd.EVENTCAUSE.value = USBD_EVENTCAUSE_READY_Msk;
  };
  ready(); assert(usbd.USBPULLUP.value == 1); bounded();

  // An interrupt completes another READY before the outer READY read.
  reset(); detected();
  bool injected = false;
  on_read = [&](int kind) {
    if (kind == CAUSE && !injected) { injected = true; ready(); }
  };
  ready(); assert(injected && usbd.USBPULLUP.value == 1); bounded();

  // A nested callback after W1C but before pull-up cannot block its caller.
  reset(); detected(); injected = false;
  on_barrier = [&]() {
    if (!injected && !usbd.EVENTCAUSE.value) { injected = true; ready(); }
  };
  ready(); assert(injected && usbd.USBPULLUP.value == 1); bounded();

  // Removal during either wait and immediately before attachment.
  reset(); clock_ok = false; detected(); injected = false;
  on_clock = [&]() { if (!injected) { injected = true; removed(); } };
  ready(); assert(!usbd.ENABLE.value && !usbd.USBPULLUP.value); bounded();
  reset(); auto_ready = false; detected(); injected = false;
  on_read = [&](int kind) {
    if (kind == CAUSE && !injected) { injected = true; removed(); }
  };
  ready(); assert(!usbd.ENABLE.value && !usbd.USBPULLUP.value); bounded();
  reset(); detected(); injected = false;
  on_barrier = [&]() {
    if (!injected && !usbd.EVENTCAUSE.value) { injected = true; removed(); }
  };
  ready(); assert(!usbd.ENABLE.value && !usbd.USBPULLUP.value); bounded();

  // A deliberately detached, enabled peripheral has no fresh READY event.
  reset(); detected(); ready(); usbd.USBPULLUP = 0; ready();
  assert(!usbd.USBPULLUP.value); bounded();
  puts("PASS: actual USB power handler, 13 event/clock/removal scenarios");
}
'''


def extract_handler(source):
    start = source.index("void tusb_hal_nrf_power_event")
    opening = source.index("{", start)
    depth = 1
    cursor = opening + 1
    while depth:
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
        cursor += 1
    return source[start:cursor]


def exercise(source):
    handler = extract_handler(source)
    start = handler.index("    case USB_EVT_READY:")
    stop = handler.index("      NRF_USBD->EVENTCAUSE =", start)
    inherited = """    case USB_EVT_READY:
    {
      if (NRF_USBD->USBPULLUP && hfclk_running()) break;
      while (!(USBD_EVENTCAUSE_READY_Msk & NRF_USBD->EVENTCAUSE)) {}

"""
    # The fixed case has a scope; the remainder stays identical to the driver.
    assert re.search(r"case USB_EVT_READY:\s*\{", handler)
    broken = handler[:start] + inherited + handler[stop:]
    with tempfile.TemporaryDirectory(prefix="nrf5x-power-test-") as directory:
        root = Path(directory)
        for label, function in (("fixed", handler), ("inherited", broken)):
            cpp = root / (label + ".cpp")
            binary = root / label
            cpp.write_text(HARNESS.replace("/* DRIVER_FUNCTION */", function))
            subprocess.run([
                *shlex.split(os.environ.get("CXX", "c++")), "-std=c++11", "-O2",
                "-Wall", "-Wextra", "-Werror", *shlex.split(os.environ.get("USB_TEST_CXXFLAGS", "")),
                str(cpp), "-o", str(binary),
            ], check=True)
            if label == "fixed":
                subprocess.run([str(binary)], check=True, timeout=5)
                subprocess.run([str(binary), "consumed-ready"], check=True, timeout=5)
            else:
                try:
                    subprocess.run([str(binary), "consumed-ready"], check=True, timeout=0.5)
                except subprocess.TimeoutExpired:
                    print("PASS: inherited READY loop reproduces hang (negative control)")
                else:
                    raise AssertionError("negative control did not reproduce the hang")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("driver", nargs="?", type=Path, default=(
        Path(__file__).resolve().parents[2] / "src/portable/nordic/nrf5x/dcd_nrf5x.c"))
    exercise(parser.parse_args().driver.read_text())
