#pragma once

#if defined(ESP32) && defined(WIFI_SSID)

// The requested state is persisted immediately. Network services transition
// from the main loop so a button callback never tears down an active server.
bool toggleCompanionWiFi();
bool isCompanionWiFiEnabled();

#endif
