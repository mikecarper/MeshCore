#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define CONFIG_IDF_TARGET_ESP32S3 1
#define CONFIG_PM_ENABLE 1
#define ARDUINO_USB_CDC_ON_BOOT 0
#define ESP_INTR_FLAG_LEVEL1 1
uint32_t millis();
