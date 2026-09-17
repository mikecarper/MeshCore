#pragma once

#include "ManagementReport.h"
#include "IdentityStore.h"
#include <Mesh.h>

class NodePrefs;
class CommonCLI;
class CommonCLICallbacks;
class ClientACL;
class SensorManager;

namespace mesh {
class ManagementReporter {
  struct Working;
  Mesh& mesh;
  MainBoard& board;
  SensorManager& sensors;
  ClientACL& acl;
  NodePrefs& prefs;
  CommonCLICallbacks& callbacks;
  CommonCLI& common_cli;
  FILESYSTEM* fs;
  Working* work = nullptr;
  uint8_t key[32] = {};
  uint8_t direct_days = 5, flood_days = 21;
  bool enabled = false, keyed = false, healthy = true;
  management::Schedule schedule;
  uint32_t sequence = 0, last_ms = 0, fraction_ms = 0, checkpoint = 0;
  uint64_t uptime = 0;
  bool save();
  bool load();
  bool allocate();
  void cancel();
  void snapshot();
  void start(bool flood);
  void sendPage();
  uint32_t jitter() const;
public:
  ManagementReporter(Mesh& mesh, MainBoard& board, SensorManager& sensors,
                     ClientACL& acl, NodePrefs& prefs,
                     CommonCLICallbacks& callbacks, CommonCLI& common_cli,
                     FILESYSTEM* fs);
  ~ManagementReporter();
  void loop(uint64_t node_uptime_seconds = UINT64_MAX);
  bool command(char* command, char* reply, size_t size);
};
}
