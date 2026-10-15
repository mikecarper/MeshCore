#include <helpers/esp32/WiFiRadioPolicy.h>
#include <cassert>
#include <cstring>
#include <string>

using namespace mesh::wifi;

static void load() {
  assert(loadStationProtocolForBoot());
  assert(loadCompanionModeForBoot());
}
static void reboot() {
  compatibility_detail::protocolPreference() = {};
  compatibility_detail::modePreference() = {};
  load();
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string test = argv[1];
  if (test == "defaults") {
    assert(activeStationProtocol() == StationProtocol::Bgn);
    assert(configuredStationProtocol() == StationProtocol::Bgn);
    assert(activeCompanionMode() == CompanionMode::Normal);
    assert(!stationProtocolRestartPending() && !companionModeRestartPending());
    assert(fake_nvs::opens == 0);
    load();
    assert(fake_nvs::opens == 2 && fake_nvs::closes == 2);
    assert(fake_nvs::writes == 0 && fake_nvs::bytes.empty());
    const int opens = fake_nvs::opens, reads = fake_nvs::reads;
    for (int i = 0; i < 1000; ++i) {
      load();
      assert(activeStationProtocol() == StationProtocol::Bgn);
      assert(activeCompanionMode() == CompanionMode::Normal);
      assert(applyProtocolMask(WIFI_IF_STA) == ESP_OK);
    }
    assert(fake_nvs::opens == opens && fake_nvs::reads == reads);
  } else if (test == "parse") {
    StationProtocol protocol = StationProtocol::Bg;
    CompanionMode mode = CompanionMode::WiFiOnly;
    assert(parseStationProtocol("bgn", &protocol) && protocol == StationProtocol::Bgn);
    assert(parseStationProtocol("bg", &protocol) && protocol == StationProtocol::Bg);
    assert(!parseStationProtocol("n", &protocol) && protocol == StationProtocol::Bg);
    assert(!parseStationProtocol("bg extra", &protocol));
    assert(!parseStationProtocol("BG", &protocol));
    assert(!parseStationProtocol(nullptr, &protocol));
    assert(!parseStationProtocol("bg", nullptr));
    assert(parseCompanionMode("normal", &mode) && mode == CompanionMode::Normal);
    assert(parseCompanionMode("wifi-only", &mode) && mode == CompanionMode::WiFiOnly);
    assert(!parseCompanionMode("wifi", &mode) && mode == CompanionMode::WiFiOnly);
    assert(!parseCompanionMode("wifi-only extra", &mode));
    assert(!parseCompanionMode(nullptr, &mode));
    assert(!parseCompanionMode("normal", nullptr));
    assert(std::strcmp(stationProtocolName(StationProtocol::Bg), "bg") == 0);
    assert(std::strcmp(stationProtocolName(StationProtocol::Bgn), "bgn") == 0);
    assert(std::strcmp(companionModeName(CompanionMode::WiFiOnly), "wifi-only") == 0);
    assert(std::strcmp(companionModeName(CompanionMode::Normal), "normal") == 0);
    assert(fake_nvs::opens == 0);
  } else if (test == "protocol_reboot") {
    load();
    assert(saveStationProtocol(StationProtocol::Bg) == CompatibilitySaveResult::Saved);
    assert(fake_nvs::bytes.at("sta_protocol") == 1);
    assert(configuredStationProtocol() == StationProtocol::Bg);
    assert(activeStationProtocol() == StationProtocol::Bgn && stationProtocolRestartPending());
    assert(fake_wifi::station_writes == 0);
    assert(applyProtocolMask(WIFI_IF_STA) == ESP_OK && fake_wifi::station == kProtocolMask);
    load();
    assert(activeStationProtocol() == StationProtocol::Bgn);
    reboot();
    assert(activeStationProtocol() == StationProtocol::Bg && !stationProtocolRestartPending());
    const int storage_writes = fake_wifi::storage_writes;
    assert(applyProtocolMask(WIFI_IF_STA) == ESP_OK && fake_wifi::station == 3);
    assert(fake_wifi::storage_writes == storage_writes);
    assert(saveStationProtocol(StationProtocol::Bgn) == CompatibilitySaveResult::Saved);
    assert(activeStationProtocol() == StationProtocol::Bg && stationProtocolRestartPending());
    assert(applyProtocolMask(WIFI_IF_STA) == ESP_OK && fake_wifi::station == 3);
    reboot();
    assert(activeStationProtocol() == StationProtocol::Bgn && !stationProtocolRestartPending());
    assert(applyProtocolMask(WIFI_IF_STA) == ESP_OK && fake_wifi::station == kProtocolMask);
  } else if (test == "mode_reboot") {
    load();
    assert(saveCompanionMode(CompanionMode::WiFiOnly) == CompatibilitySaveResult::Saved);
    assert(fake_nvs::bytes.at("wifi_only") == 1);
    assert(configuredCompanionMode() == CompanionMode::WiFiOnly);
    assert(activeCompanionMode() == CompanionMode::Normal && companionModeRestartPending());
    assert(fake_wifi::station_writes == 0);
    load(); assert(activeCompanionMode() == CompanionMode::Normal);
    reboot();
    assert(activeCompanionMode() == CompanionMode::WiFiOnly && !companionModeRestartPending());
    assert(saveCompanionMode(CompanionMode::Normal) == CompatibilitySaveResult::Saved);
    assert(activeCompanionMode() == CompanionMode::WiFiOnly && companionModeRestartPending());
    reboot();
    assert(activeCompanionMode() == CompanionMode::Normal && !companionModeRestartPending());
    assert(!stationProtocolRestartPending());
  } else if (test == "corrupt") {
    fake_nvs::bytes["sta_protocol"] = 254;
    fake_nvs::bytes["wifi_only"] = 2;
    load();
    assert(activeStationProtocol() == StationProtocol::Bgn);
    assert(activeCompanionMode() == CompanionMode::Normal);
    assert(fake_nvs::writes == 0);
    assert(!stationProtocolRestartPending() && !companionModeRestartPending());
  } else if (test == "invalid") {
    load(); const int opens = fake_nvs::opens;
    assert(saveStationProtocol(static_cast<StationProtocol>(2)) == CompatibilitySaveResult::InvalidValue);
    assert(saveCompanionMode(static_cast<CompanionMode>(255)) == CompatibilitySaveResult::InvalidValue);
    assert(fake_nvs::opens == opens && fake_nvs::writes == 0);
    assert(!stationProtocolRestartPending() && !companionModeRestartPending());
  } else if (test == "unloaded") {
    assert(saveStationProtocol(StationProtocol::Bgn) == CompatibilitySaveResult::StorageError);
    assert(saveCompanionMode(CompanionMode::WiFiOnly) == CompatibilitySaveResult::StorageError);
    assert(fake_nvs::opens == 0);
  } else if (test == "open_failure") {
    fake_nvs::open_fails = true;
    assert(!loadStationProtocolForBoot() && !loadCompanionModeForBoot());
    assert(activeStationProtocol() == StationProtocol::Bgn);
    assert(activeCompanionMode() == CompanionMode::Normal);
    assert(!loadStationProtocolForBoot() && !loadCompanionModeForBoot());
    assert(fake_nvs::opens == 2 && fake_nvs::closes == 0);
    assert(saveCompanionMode(CompanionMode::WiFiOnly) == CompatibilitySaveResult::StorageError);
    assert(saveStationProtocol(StationProtocol::Bgn) == CompatibilitySaveResult::StorageError);
    assert(!stationProtocolRestartPending() && !companionModeRestartPending());
    fake_nvs::open_fails = false;
    assert(saveCompanionMode(CompanionMode::WiFiOnly) == CompatibilitySaveResult::Saved);
    assert(activeCompanionMode() == CompanionMode::Normal && companionModeRestartPending());
  } else if (test == "write_failure" || test == "verify_failure") {
    load();
    fake_nvs::write_fails = test == "write_failure";
    fake_nvs::verify_fails = test == "verify_failure";
    assert(saveCompanionMode(CompanionMode::WiFiOnly) == CompatibilitySaveResult::StorageError);
    assert(saveStationProtocol(StationProtocol::Bgn) == CompatibilitySaveResult::StorageError);
    assert(activeCompanionMode() == CompanionMode::Normal);
    assert(configuredCompanionMode() == CompanionMode::Normal);
    if (test == "verify_failure") assert(fake_nvs::bytes.at("wifi_only") == 0);
    assert(!stationProtocolRestartPending() && !companionModeRestartPending());
    assert(fake_nvs::opens == fake_nvs::closes);
  } else if (test == "independent") {
    fake_nvs::bytes["sta_protocol"] = 1;
    fake_nvs::bytes["wifi_only"] = 1;
    fake_nvs::bytes["powersave"] = 2;
    load();
    assert(activeCompanionMode() == CompanionMode::WiFiOnly);
    assert(activeStationProtocol() == StationProtocol::Bg);
    assert(saveCompanionMode(CompanionMode::Normal) == CompatibilitySaveResult::Saved);
    assert(fake_nvs::bytes.at("sta_protocol") == 1 && fake_nvs::bytes.at("powersave") == 2);
    assert(saveStationProtocol(StationProtocol::Bgn) == CompatibilitySaveResult::Saved);
    assert(fake_nvs::bytes.at("wifi_only") == 0 && fake_nvs::bytes.at("powersave") == 2);
  } else if (test == "bg_ap") {
    fake_nvs::bytes["sta_protocol"] = 1; load();
    fake_wifi::mode = WIFI_MODE_STA | WIFI_MODE_AP;
    assert(applyProtocolMask(WIFI_IF_STA) == ESP_OK);
    assert(fake_wifi::station == 3 && fake_wifi::access_point == 7);
    assert(applyAccessPointProtocolMask() == ESP_OK && fake_wifi::access_point == 7);
    assert(checkLongRangeRadioStart() == ESP_ERR_INVALID_STATE);
  } else if (test == "bg_ap_race") {
    fake_nvs::bytes["sta_protocol"] = 1; load();
    fake_wifi::ap_appears = true;
    assert(applyProtocolMask(WIFI_IF_STA) == ESP_OK);
    assert(fake_wifi::station == 3 && fake_wifi::access_point == 7);
  } else if (test == "bg_lr_start") {
    fake_nvs::bytes["sta_protocol"] = 1; load();
    assert(checkLongRangeRadioStart() == ESP_ERR_INVALID_STATE);
    assert(applyStationProtocolMask(15, true) == ESP_ERR_INVALID_STATE);
    assert(applyStationProtocolMask(8, true) == ESP_ERR_INVALID_STATE);
    assert(fake_wifi::station_writes == 0 && fake_wifi::storage_writes == 0);
    setLongRangeOwner(kLongRangeBridgeOwner, true);
    assert(applyProtocolMask(WIFI_IF_STA) == ESP_ERR_INVALID_STATE);
    setLongRangeOwner(kLongRangeBridgeOwner, false);
    assert(applyProtocolMask(WIFI_IF_STA) == ESP_OK && fake_wifi::station == 3);
  } else if (test == "owner_conflict") {
    load(); const int opens = fake_nvs::opens;
    setLongRangeOwner(kLongRangeBridgeOwner, true);
    assert(saveStationProtocol(StationProtocol::Bg) == CompatibilitySaveResult::RadioConflict);
    assert(fake_nvs::opens == opens && fake_nvs::writes == 0);
    setLongRangeOwner(kLongRangeBridgeOwner, false);
    assert(saveStationProtocol(StationProtocol::Bg) == CompatibilitySaveResult::Saved);
    assert(activeStationProtocol() == StationProtocol::Bgn);
  } else if (test == "primary_conflict") {
    fake_nvs::bytes["sta_protocol"] = 1; load();
    assert(kPrimaryEspNowRadio);
    assert(activeStationProtocol() == StationProtocol::Bgn && !stationProtocolRestartPending());
    const int opens = fake_nvs::opens;
    assert(saveStationProtocol(StationProtocol::Bg) == CompatibilitySaveResult::RadioConflict);
    assert(fake_nvs::opens == opens && fake_nvs::writes == 0);
    assert(applyProtocolMask(WIFI_IF_STA) == ESP_OK && fake_wifi::station == kProtocolMask);
    assert(saveStationProtocol(StationProtocol::Bgn) == CompatibilitySaveResult::Saved);
  } else if (test == "driver_failure") {
    fake_nvs::bytes["sta_protocol"] = 1; load();
    fake_wifi::mode_fails = true;
    assert(applyProtocolMask(WIFI_IF_STA) != ESP_OK && fake_wifi::station_writes == 0);
    fake_wifi::mode_fails = false; fake_wifi::station_fails = true;
    assert(applyProtocolMask(WIFI_IF_STA) != ESP_OK);
    fake_wifi::station_fails = false; fake_wifi::ap_fails = true;
    fake_wifi::mode |= WIFI_MODE_AP;
    assert(applyProtocolMask(WIFI_IF_STA) != ESP_OK);
    assert(fake_wifi::station == 3);
    fake_wifi::ap_fails = false;
    fake_wifi::mode_reads = 0;
    fake_wifi::mode_read_failure = 2;
    assert(applyProtocolMask(WIFI_IF_STA) != ESP_OK);
    assert(fake_wifi::station == 3 && fake_wifi::access_point == 7);
  } else return 2;
}
