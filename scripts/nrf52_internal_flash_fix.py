"""Build-local fix for the nRF52 InternalFS SoftDevice flash wait.

GCC 14/LTO can treat the naked SVC wrapper as not writing through its pointer
argument. It then folds wait_for_async_flash_op_completion() into an immediate
success return, allowing LittleFS to read or rewrite a page before the async
erase/program has completed. Keep the SoftDevice state volatile and add a
compiler barrier after the SVC call. Do not modify PlatformIO's shared SDK.
"""

from pathlib import Path


OLD_WAIT = """  uint8_t sd_en = 0;
  (void) sd_softdevice_is_enabled(&sd_en);

  if (sd_en) {
"""
FIXED_WAIT = """  volatile uint8_t sd_en = 0;
  (void) sd_softdevice_is_enabled((uint8_t*) &sd_en);
  __asm volatile ("" ::: "memory");

  if (sd_en) {
"""


def patched_source(source):
    source = source.replace("\r\n", "\n")
    if FIXED_WAIT in source and OLD_WAIT not in source:
        return source
    if source.count(OLD_WAIT) != 1:
        raise RuntimeError(
            "nRF52 flash fix: unrecognized InternalFS flash driver; review "
            "the framework update before building (shared SDK not modified)"
        )
    return source.replace(OLD_WAIT, FIXED_WAIT)


def replace_driver(build_env, node):
    source = Path(node.srcnode().get_abspath())
    patched = patched_source(source.read_text(encoding="utf-8"))
    build_env.AppendUnique(CPPPATH=[str(source.parent)])
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-nrf52-flash" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    print("nRF52 flash: build-local SoftDevice completion wait fix enabled")
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(
        replace_driver,
        "*InternalFileSytem*src*flash*flash_nrf5x.c",
    )


if "Import" in globals():
    Import("env")
    install(env)
