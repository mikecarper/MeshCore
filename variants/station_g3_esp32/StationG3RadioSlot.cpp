#include "StationG3RadioSlot.h"
#include <nvs.h>

namespace station_g3 {
namespace {
constexpr const char* SettingsNamespace = "g3-radio";

bool saveByte(const char* key, uint8_t value) {
  nvs_handle_t handle;
  if (nvs_open(SettingsNamespace, NVS_READWRITE, &handle) != ESP_OK) return false;
  const bool ok = nvs_set_u8(handle, key, value) == ESP_OK && nvs_commit(handle) == ESP_OK;
  nvs_close(handle);
  return ok;
}
} // namespace

void loadSlotSettings() {
  SlotState& state = slotState();
  if (state.loaded) return;
  state.loaded = true;
  nvs_handle_t handle;
  const esp_err_t opened = nvs_open(SettingsNamespace, NVS_READONLY, &handle);
  if (opened == ESP_ERR_NVS_NOT_FOUND) return; // new installation: auto
  if (opened != ESP_OK) { state.settingsHealthy = false; return; }
  uint8_t value = 0;
  esp_err_t result = nvs_get_u8(handle, "mode", &value);
  if (result == ESP_OK && value <= 2) state.bootMode = state.savedMode = value;
  else if (result != ESP_ERR_NVS_NOT_FOUND) state.settingsHealthy = false;
  value = 0;
  result = nvs_get_u8(handle, "last", &value);
  if (result == ESP_OK && value <= 2) state.last = value;
  else if (result != ESP_ERR_NVS_NOT_FOUND) state.settingsHealthy = false;
  nvs_close(handle);
}

bool saveSlotMode(uint8_t mode) {
  if (mode > 2 || !saveByte("mode", mode)) return false;
  slotState().savedMode = mode;
  // A mode write does not repair an invalid/unreadable "last" key. Preserve
  // any boot warning until the next boot revalidates both stored settings.
  return true;
}

void rememberActiveSlot(uint8_t slot) {
  SlotState& state = slotState();
  if (slot < 1 || slot > 2 || slot == state.last) return;
  if (saveByte("last", slot)) state.last = slot;
  else state.settingsHealthy = false;
}

const char* slotModeName(uint8_t mode) {
  return mode == 1 ? "1" : mode == 2 ? "2" : "auto";
}
} // namespace station_g3
