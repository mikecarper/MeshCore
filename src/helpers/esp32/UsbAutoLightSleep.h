#pragma once

#include <Arduino.h>
#include <esp_pm.h>

namespace mesh { namespace esp32 {

class UsbAutoLightSleep {
  esp_pm_lock_handle_t lock = nullptr;
public:
  bool prepare() {
    if (lock) return true;
    if (esp_pm_lock_create(ESP_PM_NO_LIGHT_SLEEP, 0, "mesh_usb", &lock) != ESP_OK)
      return false;
    if (esp_pm_lock_acquire(lock) == ESP_OK) return true;
    esp_pm_lock_delete(lock);
    lock = nullptr;
    return false;
  }

  bool ready() const { return lock != nullptr; }

  // Enter only after the role verified its USB, radio, and pending-work
  // guards. Keep the lock throughout all other work, including setup.
  void idle(uint32_t ms, bool usb_held) {
    const bool released = lock && !usb_held && esp_pm_lock_release(lock) == ESP_OK;
    vTaskDelay(pdMS_TO_TICKS(ms > 10 ? 10 : ms));
    if (released) ESP_ERROR_CHECK(esp_pm_lock_acquire(lock));
  }
};

} }
