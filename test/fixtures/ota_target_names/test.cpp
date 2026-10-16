#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "Utils.h"
#include "helpers/ota/tinf/tinf.h"

static unsigned hash_calls;
static unsigned decoded_count;
static const void* decoded_sources[64];
static unsigned allocation_calls;
static void* active_allocation;

void target_name_sha256(uint8_t* hash, size_t size, const uint8_t* name, int length) {
  ++hash_calls;
  mesh::Utils::sha256(hash, size, name, length);
}

int target_name_uncompress_exact(void* dest, unsigned* size, const void* source, unsigned length) {
  assert(decoded_count < sizeof(decoded_sources) / sizeof(decoded_sources[0]));
  for (unsigned i = 0; i < decoded_count; ++i) assert(decoded_sources[i] != source);
  decoded_sources[decoded_count++] = source;
  return tinf_uncompress_exact(dest, size, source, length);
}

void* target_name_malloc(size_t bytes) {
  assert(bytes == 4096 && !active_allocation && ++allocation_calls == 1);
#if OTA_TEST_MALLOC_FAIL
  return nullptr;
#else
  active_allocation = malloc(bytes);
  return active_allocation;
#endif
}

void target_name_free(void* bytes) {
  if (bytes) {
    assert(bytes == active_allocation);
    active_allocation = nullptr;
  }
  free(bytes);
}

#include "helpers/ota/OtaTargets.h"

using mesh::ota::ota_target_env_name;

#if OTA_TARGET_NAME_TABLE && OTA_TARGET_NAME_FRONT_CODED
static const char* lookup(uint32_t id, char* buffer, size_t capacity, unsigned candidates) {
  assert(!active_allocation);
  hash_calls = decoded_count = allocation_calls = 0;
  const char* result = ota_target_env_name(id, buffer, capacity);
  assert(!active_allocation);
#if defined(OTA_RAK15002_SD_STORE) && defined(COMPANION_RADIO_FULL)
  assert(hash_calls <= candidates && decoded_count <= 3);
  if (result) assert(hash_calls > 0 && allocation_calls == 1);
#else
  (void)candidates;
  assert(hash_calls == 0 && decoded_count <= 1 && allocation_calls == 0);
#endif
  return result;
}
#endif

int main() {
  unsigned id, candidates;
  while (scanf("%x %u", &id, &candidates) == 2) {
#if OTA_TARGET_NAME_TABLE && OTA_TARGET_NAME_FRONT_CODED
    char buffer[mesh::ota::OTA_TARGET_ENV_NAME_CAPACITY];
    memset(buffer, 'X', sizeof buffer);
    const char* name = lookup(id, buffer, sizeof buffer, candidates);
    if (!name) assert(buffer[0] == 0);
    if (name) {
      const size_t length = strlen(name);
      char exact[mesh::ota::OTA_TARGET_ENV_NAME_CAPACITY];
      assert(lookup(id, exact, length + 1, candidates) == exact);
      assert(strcmp(name, exact) == 0);
      // A failed or unrelated lookup must not clobber another caller's result.
      char short_buffer[mesh::ota::OTA_TARGET_ENV_NAME_CAPACITY];
      const size_t short_capacities[] = {1, length / 2, length};
      for (size_t capacity : short_capacities) {
        if (capacity == 0) continue;
        memset(short_buffer, 'X', sizeof short_buffer);
        assert(lookup(id, short_buffer, capacity, candidates) == nullptr);
        assert(short_buffer[0] == 0);
        assert(short_buffer[capacity] == 'X');
      }
      assert(lookup(id, nullptr, sizeof buffer, candidates) == nullptr);
      assert(lookup(id, short_buffer, 0, candidates) == nullptr);
      assert(lookup(0, short_buffer, sizeof short_buffer, OTA_TEST_TARGET_COUNT) == nullptr);
      assert(short_buffer[0] == 0);
      assert(strcmp(name, exact) == 0);
    }
#else
    // Keep the original one-argument API, including table-disabled/local fallback.
    const char* name = ota_target_env_name(id);
    assert(ota_target_env_name(id, nullptr, 0) == name);
#endif
    puts(name ? name : "?");
  }
  return 0;
}
