#include <cassert>
#include <cstring>
#include "board_mock.h"
#include "helpers/findmy/FindMyNrf52.h"
#include "helpers/findmy/Nrf52FindMyMediator.h"
#include "production_board_excerpt.h"

int main() {
  NRF52Board board;
  mesh::findmy::Nrf52Beacon beacon;
  const uint8_t addr[6] = {0xc1, 2, 3, 4, 5, 6};
  uint8_t payload[29] = {0x4c, 0, 0x12, 0x19};
  char reply[160] = {};
  const uint8_t original[] = {1, 2, 3, 4, 5, 6};
  fail_tx_power = true;
  assert(!beacon.start(addr, payload, 21));
  assert(!beacon.isActive() && !memcmp(current_address.addr, original, 6));
  fail_tx_power = false;
  assert(beacon.start(addr, payload, 21));
  assert(begin_count == 1 && beacon.isActive());
  assert(tx_power_dbm == 0);
  assert(current_address.addr_type == BLE_GAP_ADDR_TYPE_RANDOM_STATIC);
  for (unsigned i = 0; i < 6; ++i) assert(current_address.addr[i] == addr[5-i]);
  assert(!memcmp(payload, captured_payload, sizeof(payload)));
  assert(Bluefruit.Advertising.interval == 34);
  assert(Bluefruit.Advertising.type == BLE_GAP_ADV_TYPE_NONCONNECTABLE_NONSCANNABLE_UNDIRECTED);

  // A failed handoff must leave the old identity and advertiser untouched.
  fail_stop = true;
  assert(!board.startOTAUpdate("", reply, false));
  assert(beacon.isActive() && current_address.addr[5] == addr[0]);
  fail_stop = false;
  assert(board.startOTAUpdate("", reply, false));
  assert(board.isOTAUpdateRunning() && !beacon.isActive() && begin_count == 1);
  assert(tx_power_dbm == 4);
  assert(!memcmp(current_address.addr, original, 6));
  assert(Bluefruit.Advertising.type == BLE_GAP_ADV_TYPE_CONNECTABLE_SCANNABLE_UNDIRECTED);

  // Stop a connected DFU: ownership remains until its callback is delivered.
  Bluefruit.Advertising.running = false;
  Bluefruit.Periph.connected_ = true;
  connect_callback(11);
  assert(board.stopOTAUpdate(reply) && !strcmp(reply, "OK - OTA disconnecting"));
  assert(disconnect_count == 1 && board.isOTAUpdateRunning());
  assert(!beacon.start(addr, payload));
  Bluefruit.Periph.connected_ = false;
  disconnect_callback(11, 0);
  assert(!board.isOTAUpdateRunning());
  assert(beacon.start(addr, payload) && begin_count == 1);
  assert(tx_power_dbm == 0); // Do not inherit DFU's +4 dBm.
  assert(beacon.stop() && !memcmp(current_address.addr, original, 6));

  // If disconnect fails and the peer later disconnects on its own, the
  // incomplete stop retains ownership and must not report a false OTA start.
  assert(board.startOTAUpdate("", reply, false));
  Bluefruit.Advertising.running = false;
  Bluefruit.Periph.connected_ = true;
  connect_callback(22);
  fail_disconnect = true;
  assert(!board.stopOTAUpdate(reply));
  Bluefruit.Periph.connected_ = false;
  disconnect_callback(22, 0);
  assert(board.isOTAUpdateRunning());
  assert(!board.startOTAUpdate("", reply, false));
  assert(!beacon.start(addr, payload));
  fail_disconnect = false;
  assert(board.stopOTAUpdate(reply) && !board.isOTAUpdateRunning());
  assert(board.startOTAUpdate("", reply, false));
  assert(board.stopOTAUpdate(reply));
}
