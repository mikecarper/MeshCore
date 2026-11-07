#pragma once

// Pinned NimBLE-Arduino API: its default callback deletes an old peer on
// overflow. A full store must never evict a previously authenticated bond.
#include <NimBLEDevice.h>
#include <stddef.h>
#ifdef USING_NIMBLE_ARDUINO_HEADERS
#include "nimble/nimble/host/include/host/ble_store.h"
#else
#include "host/ble_store.h"
#endif

class NimbleNonEvictingStore : public NimBLEDeviceCallbacks {
 public:
  int onStoreStatus(ble_store_status_event* event, void*) override {
    if (!event) return BLE_HS_ESTORE_CAP;
    if (event->event_code != BLE_STORE_EVENT_FULL) return BLE_HS_ESTORE_CAP;
    const int type = event->full.obj_type;
    if (type != BLE_STORE_OBJ_TYPE_OUR_SEC && type != BLE_STORE_OBJ_TYPE_PEER_SEC)
      return BLE_HS_ESTORE_CAP;

    int count = 0;
    if (ble_store_util_count(type, &count) != 0 || count < 0)
      return BLE_HS_ESTORE_CAP;
    // A preflight includes the in-progress connection. The final free slot
    // remains safe to use; an eventual overflow is still rejected above.
    if (count < CONFIG_BT_NIMBLE_MAX_BONDS) return 0;

    // Re-pairing an already stored peer need not consume another slot. The
    // ID is just a lookup key; the normal PIN-authenticated pairing remains
    // mandatory and the old key is not deleted.
    ble_gap_conn_desc desc{};
    if (ble_gap_conn_find(event->full.conn_handle, &desc) != 0 ||
        (desc.peer_id_addr.type != BLE_ADDR_PUBLIC &&
         desc.peer_id_addr.type != BLE_ADDR_RANDOM)) return BLE_HS_ESTORE_CAP;
    ble_store_key_sec key{}; key.peer_addr = desc.peer_id_addr;
    ble_store_value_sec value{};
    const int result = type == BLE_STORE_OBJ_TYPE_OUR_SEC
        ? ble_store_read_our_sec(&key, &value) : ble_store_read_peer_sec(&key, &value);
    volatile uint8_t* bytes = reinterpret_cast<volatile uint8_t*>(&value);
    for (size_t i=0; i<sizeof value; ++i) bytes[i]=0;
    return result == 0 ? 0 : BLE_HS_ESTORE_CAP;
  }
};
