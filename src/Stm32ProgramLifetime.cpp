#if defined(STM32_PLATFORM) && STM32_PLATFORM && defined(MESH_STM32_MINI_NO_PROGRAM_EXIT) && MESH_STM32_MINI_NO_PROGRAM_EXIT

#if (!defined(WIO_E5_MINI_COMPACT_UNSUPPORTED_AGC) || !WIO_E5_MINI_COMPACT_UNSUPPORTED_AGC) \
    && (!defined(STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC) || !STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC)
#error "The STM32 no-exit ABI policy requires an exact qualified Mini or USB Companion"
#endif

// The pinned, hash-qualified Arduino STM32 main calls setup() and then loops
// forever. Firmware reset/update paths reset the MCU; none terminate a C++
// process. Global process-exit callbacks can therefore never run. This does
// not affect automatic RAII destructors, explicit shutdown, close or delete.
// stm32_mini_program_lifetime.py enables this only for that verified main.
extern "C" __attribute__((visibility("hidden"))) int __aeabi_atexit(
    void*, void (*)(void*), void*) {
  return 0;
}

#endif
