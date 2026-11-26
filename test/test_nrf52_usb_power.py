#!/usr/bin/env python3
"""Exercise the actual patched framework handler and isolated build integration."""

import configparser
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from nrf5x_power_harness import exercise


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("usb_fix", ROOT / "scripts/nrf52_usb_power_fix.py")
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)
ORIGINAL = (ROOT / "test/fixtures/nrf52_usb_power_original.c").read_text()
PORT_SOURCE = """
typedef struct { unsigned USBREGSTATUS; } PowerRegisters;
static PowerRegisters registers;
#define NRF_POWER (&registers)
#define POWER_USBREGSTATUS_VBUSDETECT_Msk 1u
#define POWER_USBREGSTATUS_OUTPUTRDY_Msk 2u
enum { NRFX_POWER_USB_EVT_DETECTED = 0, NRFX_POWER_USB_EVT_READY = 2 };
static unsigned events[2], event_count;
static void tusb_hal_nrf_power_event(unsigned event) { events[event_count++] = event; }
static void usb_hardware_init(void) {
  unsigned usb_reg = NRF_POWER->USBREGSTATUS;
  if (usb_reg & POWER_USBREGSTATUS_VBUSDETECT_Msk) {
    tusb_hal_nrf_power_event(NRFX_POWER_USB_EVT_DETECTED);
  }
}
int main() {
  for (unsigned status = 0; status < 4; ++status) {
    registers.USBREGSTATUS = status;
    event_count = 0;
    usb_hardware_init();
    if (event_count != ((status & 1u) != 0u) + ((status & 2u) != 0u)) return 1;
    if (status & 1u && events[0] != NRFX_POWER_USB_EVT_DETECTED) return 2;
    if (status & 2u && events[event_count - 1] != NRFX_POWER_USB_EVT_READY) return 3;
  }
}
"""


class UsbPowerTests(unittest.TestCase):
    def test_cdc_query_tracks_armed_endpoint_not_fifo_bytes(self):
        original = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include "cdc_device.h"
#define CFG_TUD_CDC 2
struct { uint8_t ep_in; unsigned tx_ff; } _cdcd_itf[2];
static bool armed[256];
static bool usbd_edpt_busy(uint8_t port, uint8_t endpoint) {
  assert(port == 0); return armed[endpoint];
}
static bool tu_fifo_clear(unsigned* fifo) { *fifo = 0; return true; }
'''+ FIX.CDC_WRITE_CLEAR + r'''
int main(void) {
  _cdcd_itf[0].ep_in = 0x81;
  _cdcd_itf[1].ep_in = 0x83;
  _cdcd_itf[0].tx_ff = 16;
  armed[0x81] = true;
  assert(mesh_tud_cdc_n_tx_pending(0));
  tud_cdc_n_write_clear(0);
  assert(_cdcd_itf[0].tx_ff == 0 && mesh_tud_cdc_n_tx_pending(0));
  assert(!mesh_tud_cdc_n_tx_pending(1));
  assert(!mesh_tud_cdc_n_tx_pending(2));
  armed[0x81] = false;
  assert(!mesh_tud_cdc_n_tx_pending(0));
  _cdcd_itf[1].ep_in = 0;
  armed[0] = true; // unopened CDC must not query control endpoint
  assert(!mesh_tud_cdc_n_tx_pending(1));
}
'''
        patched = FIX.patched_cdc_source(original)
        self.assertEqual(patched, FIX.patched_cdc_source(patched))
        self.assertEqual(patched, FIX.patched_cdc_source(original.replace("\n", "\r\n")))
        compiler = shutil.which("gcc")
        if compiler is None:
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cdc.c"
            binary = Path(directory) / "cdc"
            header = Path(directory) / "class/cdc/cdc_device.h"
            header.parent.mkdir(parents=True)
            header.write_text("// Only the isolated session-query API is tested here.\n")
            source.write_text(patched)
            subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", directory, str(source), "-o", str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)

    def test_relocated_cdc_driver_compiles_against_real_tinyusb_headers(self):
        # Exercise the middleware's actual relocated file, not just an API
        # extraction: cdc_device.h is originally a sibling of cdc_device.c.
        # CI without this hardware framework can still run the isolated API
        # and relocation guards; a local hardware build runs this full check.
        sdk = (Path.home() / ".platformio/packages/framework-arduinoadafruitnrf52/"
               "libraries/Adafruit_TinyUSB_Arduino/src")
        original = sdk / "class/cdc/cdc_device.c"
        compiler = shutil.which("gcc")
        if compiler is None or not original.is_file():
            self.skipTest("native compiler/nRF52 TinyUSB framework unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "tusb_config.h"
            config.write_text("""
#define CFG_TUSB_MCU OPT_MCU_NONE
#define CFG_TUSB_OS OPT_OS_NONE
#define CFG_TUSB_RHPORT0_MODE OPT_MODE_DEVICE
#define CFG_TUD_ENABLED 1
#define CFG_TUD_CDC 2
#define CFG_TUD_CDC_RX_BUFSIZE 256
#define CFG_TUD_CDC_TX_BUFSIZE 256
#define CFG_TUSB_DEBUG 0
""")
            class Node:
                def srcnode(self): return self
                def get_abspath(self): return str(original)
            class Env:
                def subst(self, value):
                    assert value == "$BUILD_DIR"
                    return str(root / "build")
                def File(self, value): return Path(value)
            patched = FIX.replace_driver(Env(), Node())
            self.assertNotEqual(patched.parent, original.parent)
            self.assertIn(FIX.CDC_ROOT_INCLUDE, patched.read_text())
            result = subprocess.run(
                [compiler, "-std=c11", '-DCFG_TUSB_CONFIG_FILE="' + config.as_posix() + '"',
                 "-I", str(root), "-I", str(sdk),
                 "-c", str(patched), "-o", str(root / "cdc.o")],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_changed_cdc_driver_fails_closed(self):
        prefix = FIX.CDC_LOCAL_INCLUDE + "\n"
        for source in ("", prefix + FIX.CDC_WRITE_CLEAR.replace("tx_ff", "tx_fifo"),
                       prefix + FIX.CDC_WRITE_CLEAR * 2,
                       prefix + FIX.CDC_WRITE_CLEAR + FIX.CDC_SESSION_QUERY,
                       FIX.CDC_ROOT_INCLUDE + "\n" + FIX.CDC_WRITE_CLEAR):
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                FIX.patched_cdc_source(source)

    def test_cdc_query_is_built_from_local_copy(self):
        hooks = []
        class Env:
            def AddBuildMiddleware(self, handler, pattern):
                hooks.append((handler, pattern))
        FIX.install(Env())
        self.assertEqual(len(hooks), 3)
        self.assertTrue(any("class*cdc*cdc_device.c" in pattern
                            for _, pattern in hooks))

    def test_actual_handler_and_inherited_hang(self):
        exercise(FIX.patched_source(ORIGINAL))

    def test_crlf_and_idempotence(self):
        fixed = FIX.patched_source(ORIGINAL)
        self.assertEqual(fixed, FIX.patched_source(ORIGINAL.replace("\n", "\r\n")))
        self.assertEqual(fixed, FIX.patched_source(fixed))

    def test_initial_ready_is_replayed_after_detected(self):
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("C++ compiler unavailable")
        patched = FIX.patched_port_source(PORT_SOURCE)
        self.assertEqual(patched, FIX.patched_port_source(patched))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "usb_port.cpp"
            program = Path(directory) / "usb_port"
            source.write_text(patched)
            subprocess.run([compiler, "-std=c++11", "-o", str(program), str(source)],
                           check=True, capture_output=True)
            subprocess.run([str(program)], check=True, capture_output=True)

    def test_unrecognized_usb_port_fails_closed(self):
        for source in ("", PORT_SOURCE.replace("USBREGSTATUS", "STATUS"),
                       PORT_SOURCE + PORT_SOURCE):
            with self.subTest(source=source[:40]), self.assertRaises(RuntimeError):
                FIX.patched_port_source(source)

    def test_unrecognized_or_partly_fixed_driver_fails_closed(self):
        for source in ("", ORIGINAL.replace("hfclk_running()", "clock_running()"),
                       ORIGINAL + ORIGINAL, ORIGINAL.replace(FIX.OLD_READY, FIX.READY),
                       ORIGINAL.replace("dcd_event_bus_signal(0, DCD_EVENT_UNPLUGGED, false);",
                                        "// removed local disconnect event")):
            with self.subTest(source=source[:40]), self.assertRaises(RuntimeError):
                FIX.patched_source(source)

    def test_build_local_copy_does_not_modify_shared_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "shared-sdk/dcd_nrf5x.c"
            original.parent.mkdir()
            original.write_text(ORIGINAL)

            class Node:
                def get_abspath(self):
                    return str(original)

            class VariantNode:
                def srcnode(self):
                    return Node()

                def get_abspath(self):
                    raise AssertionError("VariantDir file does not exist yet; use srcnode()")

            class Env:
                def subst(self, value):
                    assert value == "$BUILD_DIR"
                    return str(root / "build")

                def File(self, value):
                    return Path(value)

            output = FIX.replace_driver(Env(), VariantNode())
            self.assertEqual(original.read_text(), ORIGINAL)
            self.assertEqual(output, root / "build/patched-nrf52-usb/dcd_nrf5x.c")
            patched = output.read_text()
            hook_declaration = "extern bool meshNrfUsbPowerLocked(void) __attribute__((weak));\n"
            lock_guard = "  if (meshNrfUsbPowerLocked && meshNrfUsbPowerLocked()) return;\n"
            self.assertEqual(patched.count(hook_declaration), 1)
            self.assertEqual(patched.count(lock_guard), 1)
            self.assertEqual(patched.replace(hook_declaration, "").replace(lock_guard, ""),
                             FIX.patched_source(ORIGINAL))
            modified = output.stat().st_mtime_ns
            FIX.replace_driver(Env(), VariantNode())
            self.assertEqual(output.stat().st_mtime_ns, modified)

    def test_all_nrf52_environments_inherit_pre_hook(self):
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read([str(ROOT / "platformio.ini"), *map(str, (ROOT / "variants").glob("*/platformio.ini"))])

        def option(section, key, seen=()):
            if section in seen:
                raise AssertionError("configuration inheritance cycle")
            if config.has_option(section, key):
                value = config.get(section, key)
                import re
                return re.sub(r"\$\{([^}.]+)\.([^}]+)\}",
                              lambda m: option(m[1], m[2], seen + (section,)), value)
            for parent in config.get(section, "extends", fallback="").split(","):
                parent = parent.strip()
                if parent and config.has_section(parent):
                    value = option(parent, key, seen + (section,))
                    if value:
                        return value
            return ""

        checked = 0
        for section in config.sections():
            if section.startswith("env:") and option(section, "platform") == "nordicnrf52":
                with self.subTest(environment=section):
                    self.assertIn("pre:scripts/nrf52_usb_power_fix.py", option(section, "extra_scripts"))
                checked += 1
        self.assertGreater(checked, 30)
        print("nRF52 USB hook inherited by %d environments" % checked)


if __name__ == "__main__":
    unittest.main()
