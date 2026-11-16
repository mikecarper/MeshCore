#pragma once

namespace mesh { namespace companion {

// The display asks for a few recent entries. Select only that many instead of
// sorting the whole cache on every redraw; the source stays in place.
template <typename Path>
int copyRecentAdvertPaths(const Path* paths, int capacity, Path* dest, int max_num) {
  if (!paths || !dest || capacity <= 0 || max_num <= 0) return 0;
  if (max_num > capacity) max_num = capacity;
  int count = 0;
  for (int i = 0; i < max_num; ++i) dest[i] = Path{};
  for (int i = 0; i < capacity; ++i) {
    if (!paths[i].name[0]) continue;
    int position = 0;
    while (position < count && dest[position].recv_timestamp >= paths[i].recv_timestamp) ++position;
    if (position >= max_num) continue;
    if (count < max_num) ++count;
    for (int j = count - 1; j > position; --j) dest[j] = dest[j - 1];
    dest[position] = paths[i];
  }
  return count;
}

} }  // namespace mesh::companion
