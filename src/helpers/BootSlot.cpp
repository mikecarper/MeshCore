#include "BootSlot.h"

#if defined(ESP32_PLATFORM)
#include <MeshCore.h>
#include <Update.h>
#include <esp_ota_ops.h>
#include <esp_partition.h>
#include <stdio.h>
#include <string.h>
#include "esp32/ManagementPassword.h"
#if defined(ENABLE_OTA)
#include "ota/OtaContext.h"
#endif

namespace mesh {
namespace esp32 {
namespace {

const esp_partition_t* appSlot(unsigned index) {
  const auto subtype = static_cast<esp_partition_subtype_t>(
      ESP_PARTITION_SUBTYPE_APP_OTA_0 + index);
  const char* label = index == 0 ? "app0" : "app1";
  return esp_partition_find_first(ESP_PARTITION_TYPE_APP, subtype, label);
}

bool otaBusy() {
  if (Update.isRunning()) return true;
#if defined(ENABLE_OTA)
  // Status/selection must not allocate an OTA workspace or reopen flash staging.
  const auto* context = mesh::ota::ota_context_if_active();
  if (context) {
    const auto state = context->manager.fetchState();
    if ((state != mesh::ota::OtaManager::IDLE &&
         state != mesh::ota::OtaManager::COMPLETE &&
         state != mesh::ota::OtaManager::FAILED) ||
        context->apply_pending || context->bootloader_apply_pending ||
        context->fetch_store.staged_size() != 0 || context->folder_active ||
        context->serve_expected != 0 || context->manager.pendingServeJobs() ||
        context->manager.pendingManifestJobs()) return true;
  }
#endif
  return false;
}

} // namespace

bool handleBootSlotCommand(const char* command, MainBoard& board, char* reply,
                           size_t capacity, bool update_pending) {
  if (!command) return false;
  const bool get = strncmp(command, "get boot.slot", 13) == 0 &&
      (command[13] == 0 || command[13] == ' ' || command[13] == '\t');
  const bool set = strncmp(command, "set boot.slot", 13) == 0 &&
      (command[13] == 0 || command[13] == ' ' || command[13] == '\t');
  if (!get && !set) return false;
  if (!reply || !capacity) return true;

  if (get) {
    if (command[13] != 0) {
      snprintf(reply, capacity, "Error: use get boot.slot");
      return true;
    }
    const auto* running = esp_ota_get_running_partition();
    const auto* boot = esp_ota_get_boot_partition();
    char sizes[2][24];
    for (unsigned index = 0; index < 2; ++index) {
      const auto* partition = appSlot(index);
      if (partition) snprintf(sizes[index], sizeof(sizes[index]), "%luK",
                              static_cast<unsigned long>(partition->size / 1024));
      else snprintf(sizes[index], sizeof(sizes[index]), "missing");
    }
    snprintf(reply, capacity, "> running=%s next=%s app0=%s app1=%s",
             running ? running->label : "unknown", boot ? boot->label : "unknown",
             sizes[0], sizes[1]);
    return true;
  }

  const char* value = command + 13;
  while (*value == ' ' || *value == '\t') ++value;
  if (strcmp(value, "app0") != 0 && strcmp(value, "app1") != 0) {
    snprintf(reply, capacity, "Error: use set boot.slot app0|app1");
    return true;
  }

  // HTTP upload tasks use the matching UploadGuard. Keep them excluded until
  // the SDK has verified the image and committed the next-boot metadata.
  managementPassword::ChangeGuard guard;
  if (!guard.acquired() || update_pending || board.isOTAUpdateRunning() || otaBusy()) {
    snprintf(reply, capacity, "Error: OTA busy/staged; stop uploads or use ota install / ota cancel first");
    return true;
  }
  const auto* partition = appSlot(value[3] - '0');
  if (!partition) {
    snprintf(reply, capacity, "Error: %s missing from installed partition table", value);
    return true;
  }
  esp_ota_img_states_t state;
  const esp_err_t state_error = esp_ota_get_state_partition(partition, &state);
  if (state_error != ESP_OK && state_error != ESP_ERR_NOT_FOUND) {
    snprintf(reply, capacity, "Error: cannot read %s boot state (%ld)", value,
             static_cast<long>(state_error));
    return true;
  }
  if (state_error == ESP_OK && (state == ESP_OTA_IMG_INVALID ||
                               state == ESP_OTA_IMG_ABORTED ||
                               state == ESP_OTA_IMG_PENDING_VERIFY)) {
    snprintf(reply, capacity, "Error: %s is rejected or unconfirmed", value);
    return true;
  }

  // This performs the complete SDK image/signature check before otadata is
  // changed. A header or app-description check alone would accept corruption.
  const esp_err_t result = esp_ota_set_boot_partition(partition);
  if (result != ESP_OK) {
    snprintf(reply, capacity, "Error: cannot select %s; invalid image or boot metadata (%ld)",
             value, static_cast<long>(result));
    return true;
  }
  snprintf(reply, capacity, "OK - boot.slot %s; reboot to apply", value);
  return true;
}

} // namespace esp32
} // namespace mesh
#endif
