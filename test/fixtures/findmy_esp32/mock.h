
#pragma once
#include <stdint.h>
#include <string.h>
#define CONFIG_BT_ENABLED 1
#define CONFIG_BLUEDROID_ENABLED 1
#define MESH_SENSOR_FINDMY 1
#define ESP32_PLATFORM 1
#define ESP_OK 0
typedef int esp_err_t;
typedef int esp_bt_controller_status_t;
#define ESP_BT_CONTROLLER_STATUS_IDLE 0
#define ESP_BT_CONTROLLER_STATUS_INITED 1
#define ESP_BT_CONTROLLER_STATUS_ENABLED 2
#define ESP_BT_MODE_BLE 1
typedef int esp_bluedroid_status_t;
#define ESP_BLUEDROID_STATUS_UNINITIALIZED 0
#define ESP_BLUEDROID_STATUS_INITIALIZED 1
#define ESP_BLUEDROID_STATUS_ENABLED 2
#define ESP_BT_STATUS_SUCCESS 0
#define ESP_GAP_BLE_SET_STATIC_RAND_ADDR_EVT 1
#define ESP_GAP_BLE_ADV_DATA_RAW_SET_COMPLETE_EVT 2
#define ESP_GAP_BLE_ADV_START_COMPLETE_EVT 3
#define ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT 4
#define ADV_TYPE_NONCONN_IND 3
#define BLE_ADDR_TYPE_RANDOM 1
#define ADV_CHNL_ALL 7
#define ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY 0
typedef int esp_gap_ble_cb_event_t;
struct esp_ble_gap_cb_param_t {
 struct { int status; } set_rand_addr_cmpl,adv_data_raw_cmpl,adv_start_cmpl,adv_stop_cmpl;
};
typedef void (*callback_t)(esp_gap_ble_cb_event_t, esp_ble_gap_cb_param_t*);
struct esp_ble_adv_params_t { int adv_int_min,adv_int_max,adv_type,own_addr_type,channel_map,adv_filter_policy; };
struct esp_bt_controller_config_t { int n; };
#define BT_CONTROLLER_INIT_CONFIG_DEFAULT() esp_bt_controller_config_t{}
static int controller=0,host=0,fail_address=0,fail_data=0,fail_start=0,init_count=0,stop_count=0;
static int fail_adv_stop=0, fail_host_disable=0, fail_host_deinit=0;
static int fail_controller_disable=0, fail_controller_deinit=0;
static int captured_interval_min=0, captured_interval_max=0;
static uint8_t captured_addr[6],captured_raw[31];
static callback_t cb=nullptr;
static uint32_t ticks=0;
static uint32_t millis(){return ticks;}
static void delay(int n){ticks+=n;}
static int esp_bt_controller_get_status(){return controller;}
static int esp_bluedroid_get_status(){return host;}
static int esp_bt_controller_init(esp_bt_controller_config_t*){++init_count;controller=1;return 0;}
static int esp_bt_controller_enable(int){controller=2;return 0;}
static int esp_bt_controller_disable(){if(fail_controller_disable)return 1;controller=1;return 0;}
static int esp_bt_controller_deinit(){if(fail_controller_deinit)return 1;controller=0;return 0;}
static int esp_bluedroid_init(){host=1;return 0;}
static int esp_bluedroid_enable(){host=2;return 0;}
static int esp_bluedroid_disable(){if(fail_host_disable)return 1;host=1;return 0;}
static int esp_bluedroid_deinit(){if(fail_host_deinit)return 1;host=0;return 0;}
static int esp_ble_gap_register_callback(callback_t f){cb=f;return 0;}
static int esp_ble_gap_set_rand_addr(uint8_t* a){
 memcpy(captured_addr,a,6); esp_ble_gap_cb_param_t p={};p.set_rand_addr_cmpl.status=fail_address;
 cb(ESP_GAP_BLE_SET_STATIC_RAND_ADDR_EVT,&p);return 0;
}
static int esp_ble_gap_config_adv_data_raw(uint8_t* a,uint32_t n){
 if(n!=31)return 1;memcpy(captured_raw,a,31);esp_ble_gap_cb_param_t p={};p.adv_data_raw_cmpl.status=fail_data;
 cb(ESP_GAP_BLE_ADV_DATA_RAW_SET_COMPLETE_EVT,&p);return 0;
}
static int esp_ble_gap_start_advertising(esp_ble_adv_params_t* a){
 if(a->adv_type!=ADV_TYPE_NONCONN_IND||a->own_addr_type!=BLE_ADDR_TYPE_RANDOM)return 1;
 captured_interval_min=a->adv_int_min;captured_interval_max=a->adv_int_max;
 esp_ble_gap_cb_param_t p={};p.adv_start_cmpl.status=fail_start;cb(ESP_GAP_BLE_ADV_START_COMPLETE_EVT,&p);return 0;
}
static int esp_ble_gap_stop_advertising(){
 ++stop_count;esp_ble_gap_cb_param_t p={};p.adv_stop_cmpl.status=fail_adv_stop;cb(ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT,&p);return 0;
}
