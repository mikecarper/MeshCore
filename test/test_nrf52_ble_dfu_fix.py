"""Compile and execute the Bluetooth DFU stack-transition regression."""

from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check_nrf52_ble_dfu_handoff import (HELPER_CODE, HELPER_NAME, check_firmware,
                                       writes_control)
from firmware_elf import FirmwareElf

spec = spec_from_file_location("nrf52_ble_dfu_fix", ROOT / "scripts/nrf52_ble_dfu_fix.py")
patch = module_from_spec(spec)
spec.loader.exec_module(patch)

try:
    import unicorn
    from unicorn import arm_const
except ImportError:
    unicorn = None


FIXTURE_BODY = r'''
extern "C" void bledfu_control_wr_authorize_cb_fixture(uint32_t address, const void* peer) {
  volatile uint32_t frame[26];
  // The real callback saves these registers while preparing peer/bond data.
  // Keep that C epilogue so the old path reproduces its invalid MSP pop.
  __asm volatile ("" ::: "r4", "r5", "r6", "r7", "r8");
  frame[0] = address;
  uint32_t bootloader = frame[0];
  HANDOFF
}
'''
BROKEN_HELPER = r'''
extern "C" __attribute__((noinline, used))
void bootloader_util_app_start(uint32_t address) {
  __asm volatile ("ldr r1, [%0]\n"
                  "msr msp, r1\n"
                  "ldr r1, [%0, #4]\n"
                  "bx r1\n" :: "r" (address) : "r1");
}
'''


def arm_compiler():
    core = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio"))
    candidates = [os.environ.get("MESHCORE_ARM_GXX", ""),
                  str(core / "packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-g++"),
                  str(Path.home() / ".local/opt/arm-gnu-toolchain-14.2.rel1-x86_64-arm-none-eabi/bin/arm-none-eabi-g++"),
                  shutil.which("arm-none-eabi-g++") or ""]
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)


class DfuSourcePatchTest(unittest.TestCase):
    def fixture(self):
        return (patch.OLD_HELPER + "// retained peer preparation and B1 stay here\n"
                + patch.OLD_START + "\n" + patch.OLD_PEER
                + patch.OLD_INTERRUPTS
                + "      VERIFY_STATUS( sd_softdevice_vector_table_base_set(NRF_UICR->NRFFW[0]), );\n"
                + patch.OLD_JUMP)

    def test_patch_is_idempotent_and_preserves_handoff_protocol(self):
        fixed = patch.patched_source(self.fixture())
        self.assertEqual(patch.patched_source(fixed), fixed)
        self.assertIn("retained peer preparation and B1 stay here", fixed)
        self.assertNotIn("__set_CONTROL(0)", fixed)
        self.assertNotIn("(0x20007F80UL)", fixed)
        self.assertIn("peer_data_t* peer_data = &staged_peer;", fixed)
        self.assertIn("request->len >= 1", fixed)
        self.assertIn("sd_softdevice_vector_table_base_set", fixed)
        self.assertLess(fixed.index("NVIC->ICER[1]"),
                        fixed.index("sd_softdevice_vector_table_base_set"))
        self.assertLess(fixed.index("SCB_ICSR_PENDSVCLR_Msk"),
                        fixed.index("sd_softdevice_vector_table_base_set"))
        self.assertLess(fixed.index("sd_softdevice_vector_table_base_set"),
                        fixed.index("mesh_nrf52_dfu_jump(NRF_UICR"))

    def test_unknown_or_partially_patched_framework_fails_closed(self):
        for original in ("unrecognized framework", self.fixture().replace(
                patch.OLD_JUMP, patch.JUMP), self.fixture().replace(
                "NVIC->ICPR[0]=0xFFFFFFFF;", "NVIC->ICPR[0]=0;")):
            with self.subTest(original=original), self.assertRaises(RuntimeError):
                patch.patched_source(original)

    def test_every_nrf52_build_uses_patch_and_machine_gate(self):
        ini = (ROOT / "platformio.ini").read_text()
        base = ini.split("[nrf52_base]", 1)[1].split("\n[", 1)[0]
        self.assertIn("pre:scripts/nrf52_ble_dfu_fix.py", base)
        self.assertIn("post:scripts/check_nrf52_ble_dfu_handoff.py", base)


class DfuCompiledHandoffTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = arm_compiler()
        if cls.compiler is None:
            if os.environ.get("MESHCORE_REQUIRE_ARM_COMPILER") == "1":
                raise RuntimeError("ARM compiler required for the DFU regression")
            raise unittest.SkipTest("ARM compiler required for the DFU regression")
        cls.folder = tempfile.TemporaryDirectory(prefix="meshcore-dfu-handoff-")
        cls.path = Path(cls.folder.name)
        cls.images = {}
        for optimize, lto in (("-Os", False), ("-Oz", False), ("-Oz", True)):
            for fixed in (False, True):
                name = f"{'fixed' if fixed else 'broken'}-{optimize[1:]}-{'lto' if lto else 'plain'}"
                source, elf = cls.path / (name + ".cpp"), cls.path / (name + ".elf")
                handoff = ("mesh_nrf52_dfu_jump(bootloader, peer);" if fixed else
                           '__asm volatile ("movs r1, #0\\nmsr control, r1\\n" ::: "r1");\n'
                           "  bootloader_util_app_start(bootloader);")
                source.write_text("#include <stdint.h>\n" + (patch.HELPER if fixed else BROKEN_HELPER)
                                  + FIXTURE_BODY.replace("HANDOFF", handoff))
                command = [cls.compiler, "-mcpu=cortex-m4", "-mthumb", optimize,
                           "-ffunction-sections", "-fdata-sections", "-nostdlib",
                           "-Wl,--entry=bledfu_control_wr_authorize_cb_fixture,-Ttext=0x26000",
                           str(source), "-o", str(elf)]
                if lto:
                    command[1:1] = ["-flto", "-fipa-pta", "-fno-inline-small-functions"]
                subprocess.run(command, check=True, capture_output=True, text=True)
                cls.images[(optimize, lto, fixed)] = elf

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_machine_gate_accepts_fixed_and_rejects_old_release_sequence(self):
        for key, path in self.images.items():
            with self.subTest(build=key):
                if key[2]:
                    self.assertTrue(check_firmware(path))
                else:
                    with self.assertRaisesRegex(ValueError, "safe stack-transition helper"):
                        check_firmware(path)
                    elf = FirmwareElf(path)
                    address, size, _ = elf.symbols["bledfu_control_wr_authorize_cb_fixture"]
                    self.assertTrue(writes_control(elf.read(address & ~1, size)))

    def test_machine_gate_rejects_helper_prologue_or_modified_instruction(self):
        path = self.images[("-Oz", True, True)]
        original = path.read_bytes()
        self.assertEqual(original.count(HELPER_CODE), 1)
        damaged = self.path / "changed-helper.elf"
        damaged.write_bytes(original.replace(HELPER_CODE, b"\x00\xb5" + HELPER_CODE[2:]))
        with self.assertRaisesRegex(ValueError, "qualified assembly"):
            check_firmware(damaged)

    def test_machine_gate_rejects_control_write_in_callback_with_valid_helper(self):
        path = self.images[("-Oz", True, True)]
        elf = FirmwareElf(path)
        address, size, _ = elf.symbols["bledfu_control_wr_authorize_cb_fixture"]
        code = elf.read(address & ~1, size)
        original = path.read_bytes()
        self.assertEqual(original.count(code), 1)
        damaged = self.path / "changed-callback.elf"
        damaged.write_bytes(original.replace(code, b"\x80\xf3\x14\x88" + code[4:]))
        with self.assertRaisesRegex(ValueError, "inside a C stack frame"):
            check_firmware(damaged)

    def test_machine_gate_rejects_callback_which_bypasses_valid_helper(self):
        path = self.images[("-Oz", True, True)]
        elf = FirmwareElf(path)
        address, size, _ = elf.symbols["bledfu_control_wr_authorize_cb_fixture"]
        code = elf.read(address & ~1, size)
        original = path.read_bytes()
        self.assertEqual(original.count(code), 1)
        damaged = self.path / "bypassed-helper.elf"
        # BL to the following instruction replaces the final nonreturning call.
        damaged.write_bytes(original.replace(code, code[:-4] + b"\x00\xf0\x00\xf8"))
        with self.assertRaisesRegex(ValueError, "does not branch"):
            check_firmware(damaged)

    def emulate(self, path, psp=True, handler=False):
        if unicorn is None:
            if os.environ.get("MESHCORE_REQUIRE_DFU_EMULATOR") == "1":
                self.fail("Unicorn required for the Cortex-M DFU regression")
            self.skipTest("Unicorn required for the Cortex-M DFU regression")
        elf = FirmwareElf(path)
        machine = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
        machine.mem_map(0x20000, 0xE0000)
        machine.mem_map(0x20000000, 0x40000)
        for section in elf.sections:
            if section[1] != 8 and section[2] & 4 and section[5]:
                machine.mem_write(section[3], elf.section_bytes(section))
        machine.mem_write(0xF4000, struct.pack("<II", 0x20030000, 0xF4101))
        peer = bytes(range(62))
        # This address really overlaps shared_flash_lock_storage in the Tower
        # standard BLE ELF. Leave the live mutex untouched until the last jump.
        old_mutex = bytes([0xA5]) * 62
        machine.mem_write(0x20007F80, old_mutex)
        machine.mem_write(0x20001000, peer)
        machine.mem_write(0x20007F7C, b"safe")
        machine.mem_write(0x20007FBE, b"ok")
        reg = arm_const
        machine.reg_write(reg.UC_ARM_REG_MSP, 0x20040000)
        machine.reg_write(reg.UC_ARM_REG_PSP, 0x20002000)
        machine.reg_write(reg.UC_ARM_REG_CONTROL, 2 if psp else 0)
        machine.reg_write(reg.UC_ARM_REG_BASEPRI, 0x80)
        machine.reg_write(reg.UC_ARM_REG_FAULTMASK, 1)
        machine.reg_write(reg.UC_ARM_REG_R0, 0xF4000)
        machine.reg_write(reg.UC_ARM_REG_R1, 0x20001000)
        if handler:
            machine.reg_write(reg.UC_ARM_REG_IPSR, 16)
            entry = elf.address(HELPER_NAME)
        else:
            entry = elf.address("bledfu_control_wr_authorize_cb_fixture")
        stop = ((elf.address(HELPER_NAME) & ~1) + len(HELPER_CODE) - 2
                if handler else 0xF4100)
        helper_entry = elf.address(HELPER_NAME) & ~1 if HELPER_NAME in elf.symbols else None
        def stop_at_branch(uc, address, size, context):
            if address == helper_entry:
                self.assertEqual(bytes(uc.mem_read(0x20007F80, 62)), old_mutex)
            if address == stop:
                uc.emu_stop()
        peer_writes = []
        def check_peer_write(uc, access, address, size, value, context):
            if 0x20007F80 <= address < 0x20007FBE:
                self.assertEqual(uc.reg_read(reg.UC_ARM_REG_PRIMASK), 1)
                peer_writes.append(address)
        machine.hook_add(unicorn.UC_HOOK_CODE, stop_at_branch)
        machine.hook_add(unicorn.UC_HOOK_MEM_WRITE, check_peer_write)
        machine.emu_start(entry | 1, 0, count=1024)
        self.assertEqual(machine.reg_read(reg.UC_ARM_REG_PC), stop)
        self.assertEqual(machine.reg_read(reg.UC_ARM_REG_CONTROL), 0)
        self.assertEqual(machine.reg_read(reg.UC_ARM_REG_PRIMASK), 0)
        self.assertEqual(machine.reg_read(reg.UC_ARM_REG_BASEPRI), 0)
        self.assertEqual(machine.reg_read(reg.UC_ARM_REG_FAULTMASK), 0)
        self.assertEqual(bytes(machine.mem_read(0x20007F80, 62)), peer)
        self.assertEqual(peer_writes, list(range(0x20007F80, 0x20007FBE)))
        self.assertEqual(bytes(machine.mem_read(0x20007F7C, 4)), b"safe")
        self.assertEqual(bytes(machine.mem_read(0x20007FBE, 2)), b"ok")
        return machine

    def test_freertos_psp_and_msp_callers_reach_bootloader_without_fault(self):
        for key, path in self.images.items():
            if not key[2]:
                continue
            for psp in (False, True):
                with self.subTest(build=key, psp=psp):
                    machine = self.emulate(path, psp=psp)
                    self.assertEqual(machine.reg_read(arm_const.UC_ARM_REG_MSP), 0x20030000)

    def test_original_callback_faults_outside_sram_before_bootloader(self):
        if unicorn is None:
            self.skipTest("Unicorn required for the Cortex-M DFU regression")
        for key, path in self.images.items():
            if not key[2]:
                with self.subTest(build=key), self.assertRaises(unicorn.UcError):
                    self.emulate(path)

    def test_handler_path_constructs_valid_exception_return_frame(self):
        machine = self.emulate(self.images[("-Oz", True, True)], handler=True)
        reg = arm_const
        self.assertEqual(machine.reg_read(reg.UC_ARM_REG_MSP), 0x20030000 - 32)
        self.assertEqual(machine.reg_read(reg.UC_ARM_REG_LR), 0xFFFFFFF9)
        self.assertEqual(struct.unpack("<8I", machine.mem_read(0x20030000 - 32, 32)),
                         (0, 0, 0, 0, 0, 0xFFFFFFFF, 0xF4101, 0x21000000))


if __name__ == "__main__":
    unittest.main()
