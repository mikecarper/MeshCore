#pragma once

#include <helpers/WiFiConnectionDiagnostics.h>

namespace mesh {
namespace wifi {

// Register before the first station attempt. Event callbacks only copy fixed
// metadata; formatting and all connection decisions stay in the owning task.
void beginStationDiagnostics(const char* ssid);
void noteStationDiagnosticPhase(ConnectionPhase phase);
ConnectionDiagnosticsSnapshot stationDiagnosticsSnapshot();
bool formatStationDiagnostics(char* reply, size_t capacity);
void appendStationDiagnosticsHint(char* reply, size_t capacity, const char* ssid);

} // namespace wifi
} // namespace mesh
