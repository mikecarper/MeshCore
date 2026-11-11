#pragma once
#define BLE_HS_ESTORE_CAP 27
#define BLE_STORE_EVENT_OVERFLOW 1
#define BLE_STORE_EVENT_FULL 2
#define BLE_STORE_OBJ_TYPE_OUR_SEC 1
#define BLE_STORE_OBJ_TYPE_PEER_SEC 2
struct ble_store_key_sec { ble_addr_t peer_addr; uint8_t idx; };
struct ble_store_value_sec { uint8_t key[16]; };
struct ble_gap_conn_desc { ble_addr_t peer_id_addr; };
struct ble_store_status_event { int event_code; union { struct { int obj_type; uint16_t conn_handle; } full; }; };
inline int ble_store_util_count(int, int* count) { *count=fake_nimble::stored_count; return fake_nimble::store_error; }
inline int ble_gap_conn_find(uint16_t, ble_gap_conn_desc* desc) { desc->peer_id_addr.type=fake_nimble::store_peer_type; return fake_nimble::link_error; }
inline int ble_store_read_our_sec(const ble_store_key_sec*, ble_store_value_sec*) { return fake_nimble::key_error; }
inline int ble_store_read_peer_sec(const ble_store_key_sec*, ble_store_value_sec*) { return fake_nimble::key_error; }
