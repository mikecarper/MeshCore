#pragma once

#if MESH_RAK3401_CAD_RXPS
namespace rak3401::cadRxps {

bool start();
void stop();
bool ownsBus();
bool exhausted();
bool overdue();
bool inScanWindow();
bool interruptAsserted();

}  // namespace rak3401::cadRxps
#endif
