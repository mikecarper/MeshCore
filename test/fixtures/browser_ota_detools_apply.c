/* Exercise MeshCore's C detools decoder against a bounded NOR-flash model.
 * This tests the shared decoder, not the separately built Nordic bootloader. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "detools.h"

struct context {
  unsigned char *flash, *patch;
  size_t capacity, patch_size, position;
  int step;
};

static int memory_read(void *arg, void *out, uintptr_t address, size_t size) {
  struct context *c = arg;
  if (address > c->capacity || size > c->capacity - address) return -1;
  memcpy(out, c->flash + address, size);
  return 0;
}
static int memory_write(void *arg, uintptr_t address, void *data, size_t size) {
  struct context *c = arg;
  if (address > c->capacity || size > c->capacity - address) return -1;
  const unsigned char *bytes = data;
  for (size_t i = 0; i < size; ++i)
    if ((c->flash[address + i] & bytes[i]) != bytes[i]) return -1;
  memcpy(c->flash + address, bytes, size);
  return 0;
}
static int memory_erase(void *arg, uintptr_t address, size_t size) {
  struct context *c = arg;
  if ((address % 4096) != 0 || size > SIZE_MAX - 4095) return -1;
  size = (size + 4095) & ~(size_t)4095;
  if (address > c->capacity || size > c->capacity - address) return -1;
  memset(c->flash + address, 0xff, size);
  return 0;
}
static int step_set(void *arg, int step) { ((struct context *)arg)->step = step; return 0; }
static int step_get(void *arg, int *step) { *step = ((struct context *)arg)->step; return 0; }
static int patch_read(void *arg, uint8_t *out, size_t size) {
  struct context *c = arg;
  if (c->position > c->patch_size || size > c->patch_size - c->position) return -1;
  memcpy(out, c->patch + c->position, size);
  c->position += size;
  return 0;
}
static unsigned char *read_file(const char *name, size_t *size) {
  FILE *f = fopen(name, "rb");
  if (!f || fseek(f, 0, SEEK_END) || ftell(f) < 0) { if (f) fclose(f); return NULL; }
  *size = (size_t)ftell(f);
  rewind(f);
  unsigned char *data = malloc(*size ? *size : 1);
  if (!data || fread(data, 1, *size, f) != *size) { free(data); fclose(f); return NULL; }
  fclose(f);
  return data;
}
int main(int argc, char **argv) {
  if (argc != 6) return 2;
  size_t base_size, target_size, patch_size;
  unsigned char *base = read_file(argv[1], &base_size);
  unsigned char *patch = read_file(argv[2], &patch_size);
  unsigned char *target = read_file(argv[3], &target_size);
  size_t capacity = strtoul(argv[4], NULL, 0);
  int fill = (int)strtoul(argv[5], NULL, 0);
  if (!base || !patch || !target || !capacity || base_size > capacity || target_size > capacity) return 2;
  struct context c = {0};
  c.flash = malloc(capacity); c.capacity = capacity; c.patch = patch; c.patch_size = patch_size;
  if (!c.flash) return 2;
  memset(c.flash, fill, capacity); memcpy(c.flash, base, base_size);
  int result = detools_apply_patch_in_place_callbacks(memory_read, memory_write, memory_erase,
                                                       step_set, step_get, patch_read, patch_size, &c);
  int ok = result >= 0 && (size_t)result == target_size && !memcmp(c.flash, target, target_size);
  if (!ok) fprintf(stderr, "decode=%d expected=%zu patch=%zu/%zu\n", result, target_size, c.position, patch_size);
  free(c.flash); free(base); free(patch); free(target);
  return ok ? 0 : 1;
}
