"""Align reviewed CustomLFS QSPI buffers in private build copies.

The nRF52840 QSPI driver rejects non-word-aligned EasyDMA addresses. uint8_t
static arrays only guarantee byte alignment; unrelated code/layout changes
can silently break mounting. Retain all three original pools and their size,
allocation lifetime, callbacks and filesystem behavior. Never edit libdeps.
"""
import hashlib
from pathlib import Path

SOURCE_SHA256 = 'b36b21e52480d1c4e56a235d0d9e3d72c4f2141a9d6c46e1781d64dc08180540'
PATCHED_SHA256 = '1d68cf61699e3988c59d9c7e87efb5479caf5391a847bab6c0e502b5593e35dd'
HEADERS = {
    'CustomLFS_QSPIFlash.h': '941596581f9f745b546e60e77e5738548b891d38f1a2830160a50646d6cf7486',
    'CustomLFS.h': '65a2efcf699d6069dd68fd4f19b856a9e0573bda61fa3fc20568951b8d31f52d',
}
ORIGINAL_BUFFERS = '''  static uint8_t read_buffer[256];
  static uint8_t prog_buffer[256];
  static uint8_t lookahead_buffer[32];'''
PATCHED_BUFFERS = '''  // QSPI EasyDMA and the LittleFS word bitmap require word alignment.
  alignas(uint32_t) static uint8_t read_buffer[256];
  alignas(uint32_t) static uint8_t prog_buffer[256];
  alignas(uint32_t) static uint8_t lookahead_buffer[32];'''

# Muzi's current Uno/Duo ship with 2/8 MB, while the published Rev01 schematic
# specifies W25Q128JV (16 MB). Match JEDEC, never the selected radio or a guessed
# capacity. These Winbond JV parts share W25Q16JV's documented status/QE and
# 24-bit address commands. Keep the reviewed descriptor's conservative timings.
MUZI_PATCHED_SHA256 = 'f695801147883452fddfa633afb84ab688548641cce361fb6f6c0a7106a5facf'
MUZI_TABLE_ANCHOR = '  // PUYA P25Q16H - Nordic boards\n'
MUZI_LOOKUP_ANCHOR = '  // Look for known chip\n'
MUZI_MOUNT_BLOCK = '''    Serial.println("Mount failed, formatting...");
    if (!format()) {
      Serial.println("Format failed!");
      return false;
    }
    if (!Adafruit_LittleFS::begin(&_lfs_config)) {
      Serial.println("Mount failed after format!");
      return false;
    }
    Serial.println("Formatted and mounted");'''
MUZI_SAFE_MOUNT_BLOCK = '''    // Older images mounted unrecognized parts with a 1 MB fallback geometry.
    // A successful mount verifies that old layout; retain it without erasing
    // or enlarging a live filesystem. Physical capacity remains JEDEC-derived.
    _lfs_config.block_count = 1048576UL / _sector_size;
    _flash_total_size = 1048576UL;
    if (Adafruit_LittleFS::begin(&_lfs_config)) {
      Serial.println("Muzi QSPI: retained existing 1 MB filesystem");
      return true;
    }
    _configure_lfs();

    // Fresh boards may initialize their full capacity, but only after every
    // byte is proven erased. Any stored byte, failed read, or elapsed budget
    // preserves the media. Aligned chunks meet EasyDMA's RAM contract.
    alignas(uint32_t) uint32_t erased[256];
    const uint32_t scan_started = millis();
    for (uint32_t address = 0; address < _total_size; address += sizeof(erased)) {
      if (uint32_t(millis() - scan_started) >= 60000UL ||
          !qspiRead(address, erased, sizeof(erased))) {
        Serial.println("Muzi QSPI: erase check failed; preserving stored data");
        return false;
      }
      for (uint32_t word : erased) {
        if (word != 0xffffffffUL) {
          Serial.println("Muzi QSPI: unmounted data preserved");
          return false;
        }
      }
      if ((address & 4095UL) == 0) delay(1);
    }
    if (uint32_t(millis() - scan_started) >= 60000UL) return false;
    if (!format() || !Adafruit_LittleFS::begin(&_lfs_config)) {
      Serial.println("Muzi QSPI: blank filesystem initialization failed");
      return false;
    }
    Serial.println("Muzi QSPI: initialized blank filesystem");'''


def patched_muzi_source(source):
    source = source.replace('\r\n', '\n')
    if digest(source) == MUZI_PATCHED_SHA256:
        return source
    source = patched_source(source)
    if any(source.count(anchor) != 1 for anchor in
           (MUZI_TABLE_ANCHOR, MUZI_LOOKUP_ANCHOR, MUZI_MOUNT_BLOCK)):
        raise RuntimeError('Muzi QSPI: changed reviewed descriptor or mount policy')
    start = source.index('  // Winbond W25Q16JV - Very common\n')
    end = source.index(MUZI_TABLE_ANCHOR)
    descriptor = source[start:end]
    additions = []
    for capacity, size, name in (('0x17', '8388608', 'W25Q64JV'),
                                 ('0x18', '16777216', 'W25Q128JV')):
        additions.append(descriptor.replace('W25Q16JV', name)
                         .replace('0xEF, 0x40, 0x15', '0xEF, 0x40, ' + capacity)
                         .replace('2097152,              // 2MB', size + ',              // bytes'))
    source = source.replace(MUZI_TABLE_ANCHOR, ''.join(additions) + MUZI_TABLE_ANCHOR)
    guard = '''  // Accept only exact reviewed Muzi flash identities. Unknown parts must
  // not mount a smaller fallback filesystem or trigger automatic formatting.
  const bool supported =
      (jedec_id[0] == 0xEF && jedec_id[1] == 0x40 &&
       (jedec_id[2] == 0x15 || jedec_id[2] == 0x17 || jedec_id[2] == 0x18)) ||
      (jedec_id[0] == 0xC8 && jedec_id[1] == 0x40 && jedec_id[2] == 0x17);
  if (!supported) {
    Serial.println("Unsupported Muzi QSPI flash; preserving stored data");
    return false;
  }

'''
    source = source.replace(MUZI_LOOKUP_ANCHOR, guard + MUZI_LOOKUP_ANCHOR)
    source = source.replace('  return true;  // Continue with defaults',
                            '  return false;  // Never guess Muzi flash geometry')
    source = source.replace(MUZI_MOUNT_BLOCK, MUZI_SAFE_MOUNT_BLOCK)
    if digest(source) != MUZI_PATCHED_SHA256:
        raise RuntimeError('Muzi QSPI: unexpected transform result')
    return source


def muzi_capacity_guard(build_env):
    defines = dict((item[0], str(item[1])) if isinstance(item, (tuple, list))
                   else (item, '1') for item in build_env.get('CPPDEFINES', []))
    enabled = defines.get('MESH_MUZI_QSPI_CAPACITY_GUARD')
    if enabled is None or enabled == '0':
        return False
    if enabled != '1' or defines.get('MUZI_BASE') != '1':
        raise RuntimeError('Muzi QSPI capacity guard requires its reviewed board recipe')
    return True


def digest(source):
    return hashlib.sha256(source.encode('ascii')).hexdigest()


def patched_source(source):
    source = source.replace('\r\n', '\n')
    fingerprint = digest(source)
    if fingerprint == PATCHED_SHA256:
        return source
    if fingerprint != SOURCE_SHA256 or source.count(ORIGINAL_BUFFERS) != 1:
        raise RuntimeError('nRF52 QSPI DMA fix: changed reviewed CustomLFS source; review dependency update')
    result = source.replace(ORIGINAL_BUFFERS, PATCHED_BUFFERS, 1)
    if digest(result) != PATCHED_SHA256:
        raise RuntimeError('nRF52 QSPI DMA fix: unexpected transform result')
    return result


def write_if_changed(path, content):
    if not path.exists() or path.read_text(encoding='ascii') != content:
        path.write_text(content, encoding='ascii', newline='\n')


def replace_qspi_source(build_env, node):
    source = Path(node.srcnode().get_abspath())
    if source.name != 'CustomLFS_QSPIFlash.cpp' or source.parent.name != 'src':
        return node
    patched = patched_source(source.read_text(encoding='ascii'))
    if muzi_capacity_guard(build_env):
        patched = patched_muzi_source(patched)
    headers = {}
    for name, expected in HEADERS.items():
        try:
            header = (source.parent / name).read_text(encoding='ascii').replace('\r\n', '\n')
        except (OSError, UnicodeError) as error:
            raise RuntimeError('nRF52 QSPI DMA fix: missing reviewed header') from error
        if digest(header) != expected:
            raise RuntimeError('nRF52 QSPI DMA fix: changed reviewed header; review dependency update')
        headers[name] = header
    destination = Path(build_env.subst('$BUILD_DIR')) / 'patched-nrf52-qspi' / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Quoted includes bind both headers to this reviewed dependency version.
    for name, header in headers.items():
        write_if_changed(destination.parent / name, header)
    write_if_changed(destination, patched)
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(replace_qspi_source, '*CustomLFS_QSPIFlash.cpp')


if 'Import' in globals():
    Import('env')
    install(env)
