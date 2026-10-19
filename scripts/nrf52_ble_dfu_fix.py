"""Give the pinned Bluefruit DFU service a safe FreeRTOS bootloader handoff.

The original callback selects MSP while its C frame is still on PSP. GCC 14
then emits a stack restore on MSP before reaching the bootloader jump, outside
the nRF52840 SRAM boundary. Keep the legacy B1/retained-peer protocol, but move
the stack transition into a naked, nonreturning assembly helper. Patch a private
build copy; never modify PlatformIO's shared framework cache.
"""

from pathlib import Path


OLD_HELPER = 'extern "C" void bootloader_util_app_start(uint32_t start_addr);\n'
HELPER_NAME = "mesh_nrf52_dfu_jump"
CLEAN_RESET_DECLARATION = 'extern "C" void meshRegulatorCleanReset() __attribute__((weak));\n'
HELPER = CLEAN_RESET_DECLARATION + r'''// Keep all stack operations inside this assembly-only transition. In particular,
// the callback must return neither through its PSP frame nor through a C helper
// after CONTROL selects MSP. Preserve handler-mode entry supported by BLEDfu.
extern "C" __attribute__((naked, noinline, noreturn, used))
void mesh_nrf52_dfu_jump(uint32_t start_addr)
{
  __asm volatile (
      "cpsid i\n"
      "mrs r3, ipsr\n"
      "ldr r1, [r0]\n"
      "ldr r2, [r0, #4]\n"
      "msr msp, r1\n"
      "movs r0, #0\n"
      "msr control, r0\n"
      "msr basepri, r0\n"
      "msr faultmask, r0\n"
      "dsb\n"
      "isb\n"
      "cmp r3, #0\n"
      "bne 1f\n"
      "mvn lr, #0\n"
      "cpsie i\n"
      "bx r2\n"
      "1:\n"
      "sub sp, sp, #32\n"
      "str r0, [sp, #0]\n"
      "str r0, [sp, #4]\n"
      "str r0, [sp, #8]\n"
      "str r0, [sp, #12]\n"
      "str r0, [sp, #16]\n"
      "mvn r0, #0\n"
      "str r0, [sp, #20]\n"
      "str r2, [sp, #24]\n"
      "mov r0, #0x21000000\n"
      "str r0, [sp, #28]\n"
      "mvn lr, #6\n"
      "cpsie i\n"
      "bx lr\n"
  );
}
'''

OLD_INTERRUPTS = '''      // Disable all interrupts
      NVIC->ICER[0]=0xFFFFFFFF;
      NVIC->ICPR[0]=0xFFFFFFFF;
#if defined(__NRF_NVIC_ISER_COUNT) && __NRF_NVIC_ISER_COUNT == 2
      NVIC->ICER[1]=0xFFFFFFFF;
      NVIC->ICPR[1]=0xFFFFFFFF;
#endif
'''
INTERRUPTS = '''      // nRF52 has external IRQs in both banks. FreeRTOS system exceptions
      // need separate cleanup: they are not covered by NVIC->ICER/ICPR.
      SysTick->CTRL = 0;
      SysTick->LOAD = 0;
      SysTick->VAL = 0;
      SCB->ICSR = SCB_ICSR_PENDSTCLR_Msk | SCB_ICSR_PENDSVCLR_Msk;
      NVIC->ICER[0] = 0xFFFFFFFF;
      NVIC->ICER[1] = 0xFFFFFFFF;
      NVIC->ICPR[0] = 0xFFFFFFFF;
      NVIC->ICPR[1] = 0xFFFFFFFF;
      __DSB();
      __ISB();
'''
OLD_JUMP = '''      __set_CONTROL(0); // switch to MSP, required if using FreeRTOS
      bootloader_util_app_start(NRF_UICR->NRFFW[0]);
'''
JUMP = '''      // The vector-table SVC above must complete before the helper masks
      // interrupts. B1 and the retained peer record preserve legacy DFU entry.
      // Only a committed handoff is intentional: rejected/aborted DFU remains
      // covered by crash recovery while the application continues running.
      if (meshRegulatorCleanReset) meshRegulatorCleanReset();
      mesh_nrf52_dfu_jump(NRF_UICR->NRFFW[0]);
'''


def patched_source(source):
    source = source.replace("\r\n", "\n")
    replacements = ((OLD_HELPER, HELPER), (OLD_INTERRUPTS, INTERRUPTS),
                    (OLD_JUMP, JUMP))
    if all(new in source and old not in source for old, new in replacements):
        return source
    if any(source.count(old) != 1 for old, _ in replacements):
        raise RuntimeError(
            "nRF52 BLE DFU handoff: unrecognized framework source; "
            "review the framework update before building"
        )
    for old, new in replacements:
        source = source.replace(old, new)
    return source


def replace_framework_source(build_env, node):
    source = Path(node.srcnode().get_abspath())
    if source.name != "BLEDfu.cpp" or source.parent.name != "services":
        return node
    patched = patched_source(source.read_text(encoding="utf-8"))
    build_env.AppendUnique(CPPPATH=[str(source.parent), str(source.parent.parent)])
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-nrf52-ble" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    print("nRF52 BLE: private FreeRTOS-safe DFU handoff")
    return build_env.File(str(destination))


if "Import" in globals():
    Import("env")
    env.AddBuildMiddleware(replace_framework_source, "*BLEDfu.cpp")
