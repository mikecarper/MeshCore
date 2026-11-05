#pragma once
typedef void* esp_pm_lock_handle_t;
#define ESP_PM_NO_LIGHT_SLEEP 1
int esp_pm_lock_create(int, int, const char*, esp_pm_lock_handle_t*);
int esp_pm_lock_acquire(esp_pm_lock_handle_t);
int esp_pm_lock_release(esp_pm_lock_handle_t);
int esp_pm_lock_delete(esp_pm_lock_handle_t);
