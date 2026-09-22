"""Build-local reliability fixes for the nRF52 InternalFS flash backend.

GCC 14/LTO can treat the naked SVC wrapper as not writing through its pointer
argument. It then folds wait_for_async_flash_op_completion() into an immediate
success return. The framework cache also discards erase/program failures and
InternalFS always reports sync success. Patch private build copies so completion
is observed, physical readback is volatile, and LittleFS receives LFS_ERR_IO.
Do not modify PlatformIO's shared SDK.
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

OLD_FLUSH = """void flash_nrf5x_flush (void)
{
  flash_cache_flush(&_cache);
}
"""
FIXED_FLUSH = """extern bool mesh_flash_cache_take_flush_result(void);

bool mesh_flash_nrf5x_flush_checked (void)
{
  flash_cache_flush(&_cache);
  return mesh_flash_cache_take_flush_result();
}

void flash_nrf5x_flush (void)
{
  (void) mesh_flash_nrf5x_flush_checked();
}
"""

OLD_VERIFY = """static bool fal_verify (uint32_t addr, void const * buf, uint32_t len)
{
  return 0 == memcmp((void*) addr, buf, len);
}
"""
FIXED_VERIFY = """static bool fal_verify (uint32_t addr, void const * buf, uint32_t len)
{
  volatile uint8_t const * flash = (volatile uint8_t const *) addr;
  uint8_t const * expected = (uint8_t const *) buf;
  for (uint32_t i = 0; i < len; ++i) {
    if (flash[i] != expected[i]) return false;
  }
  return true;
}
"""

OLD_CACHE_FLUSH = """void flash_cache_flush (flash_cache_t* fc)
{
  if ( fc->cache_addr == FLASH_CACHE_INVALID_ADDR ) return;

  // skip erase & program if verify() exists, and memory matches
  if ( !(fc->verify && fc->verify(fc->cache_addr, fc->cache_buf, FLASH_CACHE_SIZE)) )
  {
    // indicator TODO allow to disable flash indicator
    ledOn(LED_BUILTIN);

    fc->erase(fc->cache_addr);
    fc->program(fc->cache_addr, fc->cache_buf, FLASH_CACHE_SIZE);

    ledOff(LED_BUILTIN);
  }

  fc->cache_addr = FLASH_CACHE_INVALID_ADDR;
}
"""
FIXED_CACHE_FLUSH = """static bool mesh_flash_cache_flush_ok = true;

bool mesh_flash_cache_take_flush_result(void)
{
  bool const result = mesh_flash_cache_flush_ok;
  mesh_flash_cache_flush_ok = true;
  return result;
}

void flash_cache_flush (flash_cache_t* fc)
{
  if ( fc->cache_addr == FLASH_CACHE_INVALID_ADDR ) return;

  // skip erase & program if verify() exists, and memory matches
  if ( !(fc->verify && fc->verify(fc->cache_addr, fc->cache_buf, FLASH_CACHE_SIZE)) )
  {
    // indicator TODO allow to disable flash indicator
    ledOn(LED_BUILTIN);

    bool ok = fc->erase(fc->cache_addr);
    if ( ok ) ok = fc->program(fc->cache_addr, fc->cache_buf,
                               FLASH_CACHE_SIZE) == FLASH_CACHE_SIZE;
    if ( ok && fc->verify ) {
      ok = fc->verify(fc->cache_addr, fc->cache_buf, FLASH_CACHE_SIZE);
    }
    if ( !ok ) mesh_flash_cache_flush_ok = false;

    ledOff(LED_BUILTIN);
  }

  fc->cache_addr = FLASH_CACHE_INVALID_ADDR;
}
"""

OLD_INTERNAL_READ = """  VERIFY( flash_nrf5x_read(buffer, addr, size) > 0, -1);
"""
FIXED_INTERNAL_READ = """  VERIFY( flash_nrf5x_read(buffer, addr, size) == (int) size, LFS_ERR_IO);
"""
OLD_INTERNAL_PROG = """  VERIFY( flash_nrf5x_write(addr, buffer, size), -1)
"""
FIXED_INTERNAL_PROG = """  VERIFY( flash_nrf5x_write(addr, buffer, size) == (int) size, LFS_ERR_IO)
"""
OLD_INTERNAL_ERASE_WRITE = """    flash_nrf5x_write8(addr + i, 0xFF);
"""
FIXED_INTERNAL_ERASE_WRITE = """    VERIFY( flash_nrf5x_write8(addr + i, 0xFF) == 1, LFS_ERR_IO);
"""
OLD_INTERNAL_SYNC = """static int _internal_flash_sync (const struct lfs_config *c)
{
  (void) c;
  flash_nrf5x_flush();
  return 0;
}
"""
FIXED_INTERNAL_SYNC = """extern \"C\" bool mesh_flash_nrf5x_flush_checked(void);

static int _internal_flash_sync (const struct lfs_config *c)
{
  (void) c;
  return mesh_flash_nrf5x_flush_checked() ? 0 : LFS_ERR_IO;
}
"""


def _replace_once(source, old, new, description):
    if new in source and old not in source:
        return source
    if source.count(old) != 1:
        raise RuntimeError(
            "nRF52 flash fix: unrecognized " + description + "; review the "
            "framework update before building (shared SDK not modified)"
        )
    return source.replace(old, new)


def patched_driver_source(source):
    source = source.replace("\r\n", "\n")
    source = _replace_once(source, OLD_WAIT, FIXED_WAIT, "InternalFS flash driver")
    source = _replace_once(source, OLD_FLUSH, FIXED_FLUSH, "InternalFS flush API")
    return _replace_once(source, OLD_VERIFY, FIXED_VERIFY,
                         "InternalFS physical verification")


def patched_cache_source(source):
    source = source.replace("\r\n", "\n")
    return _replace_once(source, OLD_CACHE_FLUSH, FIXED_CACHE_FLUSH,
                         "InternalFS flash cache")


def patched_internal_fs_source(source):
    source = source.replace("\r\n", "\n")
    source = _replace_once(source, OLD_INTERNAL_READ, FIXED_INTERNAL_READ,
                           "InternalFS read callback")
    source = _replace_once(source, OLD_INTERNAL_PROG, FIXED_INTERNAL_PROG,
                           "InternalFS program callback")
    source = _replace_once(source, OLD_INTERNAL_ERASE_WRITE,
                           FIXED_INTERNAL_ERASE_WRITE,
                           "InternalFS erase callback")
    return _replace_once(source, OLD_INTERNAL_SYNC, FIXED_INTERNAL_SYNC,
                         "InternalFS sync callback")


# Backward-compatible name used by the regression test and any local tooling.
patched_source = patched_driver_source


def replace_framework_source(build_env, node):
    source = Path(node.srcnode().get_abspath())
    patcher = {
        "flash_nrf5x.c": patched_driver_source,
        "flash_cache.c": patched_cache_source,
        "InternalFileSystem.cpp": patched_internal_fs_source,
    }.get(source.name)
    if patcher is None:
        raise RuntimeError("nRF52 flash fix: unexpected source " + source.name)
    patched = patcher(source.read_text(encoding="utf-8"))
    build_env.AppendUnique(CPPPATH=[str(source.parent)])
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-nrf52-flash" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    print("nRF52 flash: build-local verified flush fix enabled for " + source.name)
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(
        replace_framework_source,
        "*flash_nrf5x.c",
    )
    build_env.AddBuildMiddleware(
        replace_framework_source,
        "*flash_cache.c",
    )
    build_env.AddBuildMiddleware(
        replace_framework_source,
        "*InternalFileSystem.cpp",
    )


if "Import" in globals():
    Import("env")
    install(env)
