// Host qualification against the pinned Adafruit littlefs v1 library.
// Include its source directory and link lfs_util.c with -DpvPortMalloc=malloc -DvPortFree=free.
#include <stdbool.h>
#include <stdint.h>
#include <assert.h>
#include "lfs.c"
#include "../src/helpers/nrf52/LittleFsUsbLock.inc"
#include <stdio.h>
#ifdef OTAFIX_USB_LOCK_HEADER
#include OTAFIX_USB_LOCK_HEADER
#endif

static uint8_t disk[224][128];
static int read_block(const struct lfs_config *c, lfs_block_t b, lfs_off_t off, void *p, lfs_size_t n) {
  (void)c;
  assert(b < 224 && off + n <= 128);
  memcpy(p, disk[b] + off, n);
  return 0;
}
static int write_block(const struct lfs_config *c, lfs_block_t b, lfs_off_t off, const void *p, lfs_size_t n) {
  (void)c;
  assert(b < 224 && off + n <= 128);
  memcpy(disk[b] + off, p, n);
  return 0;
}
static int erase_block(const struct lfs_config *c, lfs_block_t b) {
  (void)c;
  memset(disk[b], 255, 128);
  return 0;
}
static int sync_block(const struct lfs_config *c) { (void)c; return 0; }

static bool boot_locked(void) {
#ifdef OTAFIX_USB_LOCK_HEADER
  return mesh_usb_locked((const uint8_t *)disk);
#else
  return !memcmp(disk[0]+52, "MCULOCK1", 8) || !memcmp(disk[1]+52, "MCULOCK1", 8);
#endif
}

int main(void) {
  const struct lfs_config cfg = {.read=read_block, .prog=write_block, .erase=erase_block,
    .sync=sync_block, .read_size=128, .prog_size=128, .block_size=128,
    .block_count=224, .lookahead=128};
  lfs_t lfs = {0};

  memset(disk, 255, sizeof disk);
  assert(!boot_locked());
  assert(lfs_format(&lfs, &cfg) == 0);
  assert(lfs_mount(&lfs, &cfg) == 0);
  // Force the root to split before placing the marker.
  for (unsigned i=0; i<15; ++i) {
    char path[24];
    snprintf(path, sizeof path, "/directory%02u", i);
    assert(lfs_mkdir(&lfs, path) == 0);
  }
  assert(lfs_mkdir(&lfs, "/MCULOCK1") == 0);
  assert(!boot_locked());
  for (unsigned i=0; i<3; ++i) {
    assert(mesh_lfs_set_usb_lock(&lfs, true) == 0);
    assert(!memcmp(disk[0]+52, "MCULOCK1", 8) && !memcmp(disk[1]+52, "MCULOCK1", 8));
    assert(boot_locked());
    assert(lfs_unmount(&lfs) == 0 && lfs_mount(&lfs, &cfg) == 0);
    struct lfs_info info;
    assert(lfs_stat(&lfs, "/directory14", &info) == 0);
    // Restore an older locked copy to model power loss between commits.
    uint8_t older[128];
    memcpy(older, disk[0], sizeof older);
    assert(mesh_lfs_set_usb_lock(&lfs, false) == 0);
    assert(lfs_unmount(&lfs) == 0);
    memcpy(disk[0], older, sizeof older);
    assert(lfs_mount(&lfs, &cfg) == 0);
    assert(boot_locked());
    assert(mesh_lfs_set_usb_lock(&lfs, false) == 0);
    assert(!boot_locked());
  }
  assert(lfs_unmount(&lfs) == 0);
  puts("USB lock passed blank, existing files, mirrored lock, remount, interrupted unlock and retry");
}
