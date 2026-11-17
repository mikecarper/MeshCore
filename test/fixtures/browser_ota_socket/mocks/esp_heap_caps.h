#pragma once
#include <cstdlib>
#include <cstddef>
constexpr unsigned MALLOC_CAP_INTERNAL = 1, MALLOC_CAP_8BIT = 2, MALLOC_CAP_SPIRAM = 4;
inline size_t mock_heap_free = 65536, mock_heap_largest = 65536;
inline bool mock_heap_failure = false;
inline unsigned mock_heap_allocations = 0, mock_heap_releases = 0;
inline size_t heap_caps_get_free_size(unsigned) { return mock_heap_free; }
inline size_t heap_caps_get_largest_free_block(unsigned) { return mock_heap_largest; }
inline void* heap_caps_calloc(size_t count, size_t size, unsigned caps) {
  if (caps & MALLOC_CAP_SPIRAM || mock_heap_failure) return nullptr;
  void* p = std::calloc(count, size);
  if (p) ++mock_heap_allocations;
  return p;
}
inline void heap_caps_free(void* p) { if (p) ++mock_heap_releases;std::free(p); }
