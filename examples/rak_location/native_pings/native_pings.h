#pragma once
#include <zephyr/bluetooth/gatt.h>

enum { RAK_SIDEWALK_ID = 1, RAK_ANDROID_ID = 2, RAK_APPLE_ID = 3 };
int rak_host_ready(void);
uint32_t rak_ping_interval_units(void);
int rak_register_android_gatt(const struct bt_gatt_authorization_cb *callbacks);
void rak_apple_motion_start(void);
bool rak_apple_motion_detected(void);
void rak_apple_motion_stop(void);
void rak_android_motion_start(void);
bool rak_android_motion_detected(void);
void rak_android_motion_stop(void);
int rak_apple_buzzer(bool active);
int rak_android_buzzer(bool active);
int rak_board_prepare(void);
int rak_apple_indicator(bool active);
int rak_android_indicator(bool active);
