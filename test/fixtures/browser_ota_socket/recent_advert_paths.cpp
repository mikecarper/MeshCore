#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include "RecentAdvertPaths.h"

struct Path {
  char name[32];
  uint32_t recv_timestamp;
};

int main() {
  Path paths[350]{};
  Path output[4]{};
  assert(mesh::companion::copyRecentAdvertPaths(paths, 350, output, 4) == 0);
  for (int i = 0; i < 350; ++i) {
    std::snprintf(paths[i].name, sizeof(paths[i].name), "node%d", i);
    paths[i].recv_timestamp = i + 1;
  }
  assert(mesh::companion::copyRecentAdvertPaths(paths, 350, output, 4) == 4);
  assert(std::strcmp(output[0].name, "node349") == 0);
  assert(std::strcmp(output[3].name, "node346") == 0);
  assert(std::strcmp(paths[0].name, "node0") == 0); // cache was not sorted
  paths[0].recv_timestamp = 1000;
  assert(mesh::companion::copyRecentAdvertPaths(paths, 350, output, 1) == 1);
  assert(std::strcmp(output[0].name, "node0") == 0);
  paths[1].name[0] = '\0';
  assert(mesh::companion::copyRecentAdvertPaths(paths, 2, output, 4) == 1);
  assert(!output[1].name[0]);
  assert(mesh::companion::copyRecentAdvertPaths(paths, 350, static_cast<Path*>(nullptr), 4) == 0);
  assert(mesh::companion::copyRecentAdvertPaths(paths, 350, output, 0) == 0);
}
