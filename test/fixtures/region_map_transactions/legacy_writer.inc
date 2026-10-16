// Exact pre-fix openWrite()/save() bodies from RegionMap.cpp. Keep the old
// destructive writer as an executable negative control for the durable-policy
// assertions, including STM32's removal before opening the replacement.
// Source RegionMap.cpp SHA256 before the fix:
// c6a1ec995dcd072462410476e04756c3ede95c28964244b76b84dafb6b1c963f
#if !defined(NRF52_PLATFORM)
static File openWrite(FILESYSTEM* _fs, const char* filename) {
  #if defined(STM32_PLATFORM)
    _fs->remove(filename);
    return _fs->open(filename, FILE_O_WRITE);
  #elif defined(RP2040_PLATFORM)
    return _fs->open(filename, "w");
  #else
    return _fs->open(filename, "w", true);
  #endif
}
#endif

bool RegionMap::save(FILESYSTEM* _fs, const char* path) {
  const char* target_path = path ? path : "/regions2";
#if defined(NRF52_PLATFORM)
  mesh::AtomicFileWriter file(_fs, target_path);
#else
  File file = openWrite(_fs, target_path);
#endif
  if (file) {
    uint8_t pad[128];
    memset(pad, 0, sizeof(pad));

    bool success = file.write(pad, 3) == 3;  // reserved header
    success = success && file.write((uint8_t *) &default_id, sizeof(default_id)) == sizeof(default_id);
    success = success && file.write((uint8_t *) &home_id, sizeof(home_id)) == sizeof(home_id);
    success = success && file.write((uint8_t *) &wildcard.flags, sizeof(wildcard.flags)) == sizeof(wildcard.flags);
    success = success && file.write((uint8_t *) &next_id, sizeof(next_id)) == sizeof(next_id);

    if (success) {
      for (int i = 0; i < num_regions; i++) {
        auto r = &regions[i];

        success = file.write((uint8_t *) &r->id, sizeof(r->id)) == sizeof(r->id);
        success = success && file.write((uint8_t *) &r->parent, sizeof(r->parent)) == sizeof(r->parent);
        success = success && file.write((uint8_t *) r->name, sizeof(r->name)) == sizeof(r->name);
        success = success && file.write((uint8_t *) &r->flags, sizeof(r->flags)) == sizeof(r->flags);
        success = success && file.write(pad, sizeof(pad)) == sizeof(pad);
        if (!success) break; // write failed
      }
    }
#if defined(NRF52_PLATFORM)
    success = file.commit(success);
#else
    file.close();
#endif
    return success;
  }
  return false;  // failed
}
