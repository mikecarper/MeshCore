#include <helpers/esp32/SerialBLEInterface.h>
#include <host/ble_store.h>
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdio>

using mesh::companion::BluetoothPeerIdentity;
using namespace fake_nimble;

static void reset() {
  NimBLEDevice::deinit(true);
  failure.clear(); calls.clear(); transmitted.clear();
  connection_updates.clear();
  persisted = true; accept_notification = true; disconnects = 0; notification_attempts = 0;
  test_millis = 100;
}

static void test_identity_modes() {
  // The common MAC policy supplies human-order addresses for all four
  // custom/random modes. The actual adapter must hand the controller exactly
  // those bytes in NimBLE order before any advertisement can start.
  const std::array<std::array<uint8_t, 6>, 4> addresses = {{
      {{0xc2, 0x12, 0x34, 0x56, 0x78, 0x9a}},
      {{0xd3, 0xff, 0x00, 0x7e, 0x01, 0x02}},
      {{0xe4, 0x10, 0x20, 0x30, 0x40, 0x50}},
      {{0xf5, 0x11, 0x22, 0x33, 0x44, 0x55}},
  }};
  for (const auto& chosen : addresses) {
    reset();
    SerialBLEInterface interface;
    assert(interface.begin("MeshCore-", "trial", 246810, chosen.data(), true));
    assert(own_type == BLE_OWN_ADDR_RANDOM);
    for (size_t i = 0; i < 6; ++i) assert(address[i] == chosen[5 - i]);
    auto* server = NimBLEDevice::server.get();
    assert(!server->owns_callbacks && !server->auto_advertise);
    assert(!server->advertising.active);
    assert(pin == 123456 && bonding && mitm && sc && io_cap == BLE_HS_IO_DISPLAY_ONLY);
    interface.enable();
    assert(server->advertising.active);
    assert(std::find(calls.begin(), calls.end(), "address-type") <
           std::find(calls.begin(), calls.end(), "advertise"));
    assert(server->advertising.name == "MeshCore-trial");
    assert(server->advertising.service == "6E400001-B5A3-F393-E0A9-E50E24DCCA9E");
    assert(server->displayPairingPasskey() == 246810);
    assert(interface.takePairingRequest());
    assert(!interface.takePairingRequest());
    NimBLEDevice::deinit(true);  // Must never delete the application interface.
  }

  reset();
  own_type = BLE_OWN_ADDR_RANDOM;
  SerialBLEInterface interface;
  assert(interface.begin("MeshCore-", "default", 123456));
  assert(own_type == BLE_OWN_ADDR_PUBLIC);
  assert(std::find(calls.begin(), calls.end(), "address") == calls.end());
  NimBLEDevice::deinit(true);
}

static void test_pairing_wake_for_random_and_saved_pins() {
  for (uint32_t expected : {100000u, 246810u, 123456u, 999999u}) {
    reset();
    SerialBLEInterface interface;
    assert(interface.begin("MeshCore-", "pairing", expected));
    interface.enable();
    assert(!interface.takePairingRequest()); // Scanning alone is not pairing.
    auto* server = NimBLEDevice::server.get();
    assert(server->displayPairingPasskey() == expected);
    assert(interface.takePairingRequest());
    assert(!interface.takePairingRequest());
    assert(bonding && mitm && sc && io_cap == BLE_HS_IO_DISPLAY_ONLY);
    NimBLEDevice::deinit(true);
  }
}

static void test_startup_failures() {
  const uint8_t custom[] = {0xc2, 0x12, 0x34, 0x56, 0x78, 0x9a};
  for (const char* step : {"clear-bonds", "address", "address-type", "server", "service", "tx", "rx"}) {
    reset(); failure = step;
    SerialBLEInterface interface;
    assert(!interface.begin("MeshCore-", "trial", 123456, custom, true));
    assert(!initialized && !NimBLEDevice::server);
    assert(std::find(calls.begin(), calls.end(), "advertise") == calls.end());
    failure.clear();
    assert(interface.begin("MeshCore-", "trial", 123456, custom, true));
    for (size_t i = 0; i < 6; ++i) assert(address[i] == custom[5 - i]);
    NimBLEDevice::deinit(true);
  }
  reset();
  SerialBLEInterface interface;
  const uint8_t invalid[] = {0x44, 1, 2, 3, 4, 5};
  assert(!interface.begin("MeshCore-", "trial", 123456, invalid));
  assert(calls.empty());
}

static void authenticate(SerialBLEInterface& interface, NimBLEConnInfo& info) {
  auto* server = NimBLEDevice::server.get();
  server->connected = true;
  server->callbacks->onConnect(server, info);
  server->callbacks->onAuthenticationComplete(info);
  uint8_t discarded[MAX_FRAME_SIZE];
  interface.checkRecvFrame(discarded);
}

static void test_advertising_restart_retries_and_disable_cancels() {
  reset(); SerialBLEInterface interface;
  assert(interface.begin("MeshCore-", "retry", 123456));
  interface.enable();
  NimBLEConnInfo info; authenticate(interface, info);
  auto* server = NimBLEDevice::server.get();
  server->connected = false;
  server->callbacks->onDisconnect(server, info, 0);
  uint8_t frame[MAX_FRAME_SIZE]; interface.checkRecvFrame(frame);
  failure = "advertise";
  const auto attempts = std::count(calls.begin(), calls.end(), "advertise");
  test_millis += 1000; interface.checkRecvFrame(frame);
  assert(!server->advertising.active);
  assert(std::count(calls.begin(), calls.end(), "advertise") == attempts + 1);
  for (unsigned i = 0; i < 100; ++i) interface.checkRecvFrame(frame);
  assert(std::count(calls.begin(), calls.end(), "advertise") == attempts + 1);
  failure.clear(); test_millis += 999; interface.checkRecvFrame(frame);
  assert(!server->advertising.active);
  ++test_millis; interface.checkRecvFrame(frame);
  assert(server->advertising.active);
  assert(std::count(calls.begin(), calls.end(), "advertise") == attempts + 2);
  authenticate(interface, info);
  server->connected = false; server->callbacks->onDisconnect(server, info, 0);
  interface.checkRecvFrame(frame); interface.disable();
  const auto before_off = std::count(calls.begin(), calls.end(), "advertise");
  test_millis += 10000; interface.checkRecvFrame(frame);
  assert(std::count(calls.begin(), calls.end(), "advertise") == before_off);
  NimBLEDevice::deinit(true);
}

static void test_authentication_and_bond_lifecycle() {
  for (unsigned missing = 0; missing < 3; ++missing) {
    reset(); SerialBLEInterface interface;
    assert(interface.begin("MeshCore-", "trial", 123456));
    NimBLEConnInfo info;
    if (missing == 0) info.encrypted = false;
    if (missing == 1) info.authenticated = false;
    if (missing == 2) info.bonded = false;
    const auto deletions = bond_deletions;
    authenticate(interface, info);
    assert(!interface.isConnected() && !interface.takeSuccessfulConnection());
    assert(disconnects == 1);
    assert(bond_deletions == deletions);
    assert(connection_updates.empty());
    NimBLEDevice::deinit(true);
  }

  reset(); SerialBLEInterface interface;
  assert(interface.begin("MeshCore-", "trial", 123456, nullptr, false, true));
  interface.enable();
  NimBLEConnInfo info;
  // Check an identity address, not a temporary over-the-air private address.
  info.identity.type = BLE_ADDR_RANDOM;
  info.identity.val[5] = 0xd6;
  authenticate(interface, info);
  assert(interface.isConnected());
  BluetoothPeerIdentity peer;
  persisted = false;
  assert(!interface.takeSuccessfulConnection(&peer));
  persisted = true;
  assert(interface.takeSuccessfulConnection(&peer));
  assert(peer.type == mesh::companion::BLUETOOTH_PEER_ADDRESS_RANDOM);
  for (size_t i = 0; i < 6; ++i) assert(peer.address[i] == info.identity.val[5 - i]);
  assert(!interface.takeSuccessfulConnection(&peer));
  assert(interface.enableBondedOnlyAdvertising(peer));
  assert(allowlisted.type == BLE_ADDR_RANDOM);
  assert(memcmp(allowlisted.val, info.identity.val, 6) == 0);
  auto* server = NimBLEDevice::server.get();
  assert(server->advertising.scan_filter && server->advertising.connect_filter);
  assert(!server->advertising.scan_response && server->advertising.name.empty());
  assert(interface.isConnected());

  // A transient failed exchange must not delete the existing bond or reopen
  // general advertising. The saved peer must be able to authenticate again.
  const auto deletions = bond_deletions;
  server->connected = false;
  server->callbacks->onDisconnect(server, info, 0);
  NimBLEConnInfo failed = info;
  failed.encrypted = false;
  authenticate(interface, failed);
  assert(!interface.isConnected() && bond_deletions == deletions);
  assert(!interface.takeBondedOnlyRecovery());
  server->connected = false;
  server->callbacks->onDisconnect(server, failed, 0);
  uint8_t discarded[MAX_FRAME_SIZE];
  test_millis += 1000;
  interface.checkRecvFrame(discarded);
  assert(server->advertising.active && server->advertising.scan_filter
         && server->advertising.connect_filter && server->advertising.name.empty());
  authenticate(interface, info);
  assert(interface.isConnected() && bond_deletions == deletions);
  assert(!interface.takeBondedOnlyRecovery());
  NimBLEDevice::deinit(true);

  reset(); SerialBLEInterface rebooted;
  persisted = false;
  assert(rebooted.begin("MeshCore-", "trial", 123456, nullptr, false, false, &peer));
  rebooted.enable();
  assert(!NimBLEDevice::server->advertising.active);
  assert(rebooted.takeBondedOnlyRecovery());
  NimBLEDevice::deinit(true);
}

static void test_connection_parameters_only_on_current_authenticated_transition() {
  reset(); SerialBLEInterface interface;
  assert(interface.begin("MeshCore-", "connection", 123456));
  interface.enable();
  auto* server = NimBLEDevice::server.get();
  NimBLEConnInfo info;
  info.handle = 29;
  // A callback after the physical link has closed must not request an update.
  server->callbacks->onAuthenticationComplete(info);
  assert(connection_updates.empty() && !interface.isConnected());
  server->connected = true;
  server->callbacks->onConnect(server, info);
  assert(connection_updates.empty() && !interface.isConnected());
  NimBLEConnInfo stale = info;
  stale.handle = 7;
  server->callbacks->onAuthenticationComplete(stale);
  assert(connection_updates.empty() && !interface.isConnected());
  server->callbacks->onAuthenticationComplete(info);
  assert(connection_updates.size() == 1 && interface.isConnected());
  const auto& first = connection_updates[0];
  assert(first.handle == 29 && first.minimum == 12 && first.maximum == 24
         && first.latency == 0 && first.timeout == 400);
  server->callbacks->onAuthenticationComplete(info);
  assert(connection_updates.size() == 1); // No repeated update on the same link.
  server->connected = false;
  server->callbacks->onDisconnect(server, info, 0);
  server->callbacks->onAuthenticationComplete(info);
  assert(connection_updates.size() == 1 && !interface.isConnected());
  NimBLEConnInfo next = info;
  next.handle = 31;
  server->connected = true;
  server->callbacks->onConnect(server, next);
  server->callbacks->onAuthenticationComplete(info); // Prior connection's callback.
  assert(connection_updates.size() == 1 && !interface.isConnected());
  server->callbacks->onAuthenticationComplete(next);
  assert(connection_updates.size() == 2 && connection_updates[1].handle == 31);
  server->callbacks->onAuthenticationComplete(next);
  assert(connection_updates.size() == 2);
  NimBLEDevice::deinit(true);
}

static void test_real_transport_frames() {
  reset(); SerialBLEInterface interface;
  assert(interface.begin("MeshCore-", "trial", 123456));
  interface.enable();
  NimBLEConnInfo info;
  authenticate(interface, info);
  auto* server = NimBLEDevice::server.get();
  auto* tx = server->service->characteristics[0].get();
  auto* rx = server->service->characteristics[1].get();
  assert(tx->properties & NIMBLE_PROPERTY::READ_AUTHEN);
  assert(rx->properties & NIMBLE_PROPERTY::WRITE_AUTHEN);
  uint8_t frame[MAX_FRAME_SIZE];
  for (size_t i = 0; i < sizeof(frame); ++i) frame[i] = i;
  frame[0] = 0x0d;
  uint8_t received[MAX_FRAME_SIZE] = {};
  assert(interface.writeFrame(frame, sizeof(frame)) == sizeof(frame));
  interface.checkRecvFrame(received);
  assert(transmitted.empty()); // Subscription is still pending.
  tx->callbacks->onSubscribe(tx, info, 1);
  mtu = 23;
  interface.checkRecvFrame(received);
  assert(transmitted.empty()); // Never truncate a full protocol frame.
  mtu = 179;
  accept_notification = false;
  interface.checkRecvFrame(received);
  assert(interface.hasPendingIO() && transmitted.empty());
  test_millis += 61;
  accept_notification = true;
  interface.checkRecvFrame(received);
  assert(transmitted.size() == 1 && transmitted[0].size() == sizeof(frame));
  assert(memcmp(transmitted[0].data(), frame, sizeof(frame)) == 0);
  test_millis += 61;
  interface.checkRecvFrame(received);
  assert(transmitted.size() == 1); // No late-status duplicate.

  rx->setValue(frame, sizeof(frame));
  info.authenticated = false;
  rx->callbacks->onWrite(rx, info);
  assert(interface.checkRecvFrame(received) == 0);
  info.authenticated = true;
  rx->callbacks->onWrite(rx, info);
  assert(interface.checkRecvFrame(received) == sizeof(frame));
  assert(memcmp(frame, received, sizeof(frame)) == 0);
  uint8_t oversized[MAX_FRAME_SIZE + 1] = {};
  rx->setValue(oversized, sizeof(oversized));
  rx->callbacks->onWrite(rx, info);
  assert(interface.checkRecvFrame(received) == 0);
  NimBLEDevice::deinit(true);
}

static void test_required_reply_backpressure_services_tx_before_rx() {
  reset(); SerialBLEInterface interface;
  assert(interface.begin("MeshCore-", "backpressure", 123456));
  interface.enable();
  NimBLEConnInfo info;
  authenticate(interface, info);
  auto* server = NimBLEDevice::server.get();
  auto* tx = server->service->characteristics[0].get();
  auto* rx = server->service->characteristics[1].get();
  tx->callbacks->onSubscribe(tx, info, 1);
  mtu = 179;
  for (uint8_t i = 0; i < 4; ++i) {
    const uint8_t reply[] = {0x0d, i};
    assert(interface.writeFrame(reply, sizeof(reply)) == sizeof(reply));
  }
  const uint8_t command[] = {66, 'A', '7', '|', 'g', 'e', 't', ' ', 'l', 'a', 'b', '.', 't', 'x'};
  rx->setValue(command, sizeof(command));
  rx->callbacks->onWrite(rx, info);
  uint8_t received[MAX_FRAME_SIZE] = {};
  accept_notification = false;
  // A command must retain its RX slot until its required reply can fit.
  assert(interface.checkRecvFrame(received) == 0);
  assert(interface.isReadBusy() && transmitted.empty());
  test_millis += 61;
  assert(interface.isWriteBusy()); // Capacity also blocks while TX is ready.
  assert(interface.checkRecvFrame(received) == 0);
  assert(interface.isReadBusy() && transmitted.empty());

  accept_notification = true;
  test_millis += 61;
  // TX must be serviced BEFORE the full-queue RX gate, or this deadlocks.
  assert(interface.checkRecvFrame(received) == sizeof(command));
  assert(memcmp(received, command, sizeof(command)) == 0);
  assert(!interface.isReadBusy() && transmitted.size() == 1);
  const uint8_t cli_reply[] = {29, 'A', '7', '|', 'O', 'K'};
  assert(interface.writeFrame(cli_reply, sizeof(cli_reply)) == sizeof(cli_reply));
  for (unsigned i = 0; i < 6; ++i) {
    test_millis += 61;
    assert(interface.checkRecvFrame(received) == 0);
  }
  assert(transmitted.size() == 5);
  for (uint8_t i = 0; i < 4; ++i) {
    assert((transmitted[i] == std::vector<uint8_t>{0x0d, i}));
  }
  assert(transmitted[4] == std::vector<uint8_t>(cli_reply, cli_reply + sizeof(cli_reply)));
  assert(!interface.hasPendingIO());
  NimBLEDevice::deinit(true);
}

static void test_full_queue_best_effort_eviction_still_admits_rx() {
  reset(); SerialBLEInterface interface;
  assert(interface.begin("MeshCore-", "eviction", 123456));
  interface.enable();
  NimBLEConnInfo info;
  authenticate(interface, info);
  auto* server = NimBLEDevice::server.get();
  auto* tx = server->service->characteristics[0].get();
  auto* rx = server->service->characteristics[1].get();
  tx->callbacks->onSubscribe(tx, info, 1);
  mtu = 179;
  for (uint8_t i = 0; i < 3; ++i) {
    const uint8_t log[] = {0x88, i};
    assert(interface.writeFrame(log, sizeof(log)) == sizeof(log));
  }
  const uint8_t extra_log[] = {0x88, 9};
  assert(interface.writeFrame(extra_log, sizeof(extra_log)) == 0);
  const uint8_t previous_reply[] = {0x0d, 7};
  assert(interface.writeFrame(previous_reply, sizeof(previous_reply)) == sizeof(previous_reply));
  const uint8_t command[] = {66, 'B', '8', '|', 'v', 'e', 'r'};
  rx->setValue(command, sizeof(command));
  rx->callbacks->onWrite(rx, info);
  uint8_t received[MAX_FRAME_SIZE] = {};
  accept_notification = false;
  // Full is not blocked when a required response can evict a packet log.
  assert(!interface.isWriteBusy());
  assert(interface.checkRecvFrame(received) == sizeof(command));
  assert(memcmp(received, command, sizeof(command)) == 0);
  const uint8_t cli_reply[] = {29, 'B', '8', '|', 'O', 'K'};
  assert(interface.writeFrame(cli_reply, sizeof(cli_reply)) == sizeof(cli_reply));
  assert(transmitted.empty());
  accept_notification = true;
  for (unsigned i = 0; i < 6; ++i) {
    test_millis += 61;
    assert(interface.checkRecvFrame(received) == 0);
  }
  assert(transmitted.size() == 4);
  assert(transmitted[0] == std::vector<uint8_t>(previous_reply, previous_reply + sizeof(previous_reply)));
  assert(transmitted[1] == std::vector<uint8_t>(cli_reply, cli_reply + sizeof(cli_reply)));
  assert((transmitted[2] == std::vector<uint8_t>{0x88, 0}));
  assert((transmitted[3] == std::vector<uint8_t>{0x88, 1}));
  assert(!interface.hasPendingIO());
  NimBLEDevice::deinit(true);
}

static void test_exact_notify_interval_failure_retention_and_rollover() {
#if defined(MESH_HIL_BLE_INTERVAL_MS)
  constexpr uint32_t interval = MESH_HIL_BLE_INTERVAL_MS;
#else
  constexpr uint32_t interval = 7;
#endif
  reset(); SerialBLEInterface interface;
  assert(interface.begin("MeshCore-", "interval", 123456));
  interface.enable();
  NimBLEConnInfo info; authenticate(interface, info);
  auto* tx = NimBLEDevice::server->service->characteristics[0].get();
  uint8_t first[MAX_FRAME_SIZE] = {0x70, 1}, second[MAX_FRAME_SIZE] = {0x70, 2};
  uint8_t received[MAX_FRAME_SIZE] = {};
  assert(interface.writeFrame(first, sizeof(first)) == sizeof(first));
  interface.checkRecvFrame(received);
  assert(notification_attempts == 0); // CCCD is not ready.
  tx->callbacks->onSubscribe(tx, info, 1);
  mtu = 23; interface.checkRecvFrame(received);
  assert(notification_attempts == 0); // Never truncate to fit a smaller MTU.
  mtu = 179; accept_notification = false;
  interface.checkRecvFrame(received);
  assert(notification_attempts == 1 && transmitted.empty() && interface.hasPendingIO());
  test_millis += interval - 1;
  assert(interface.isWriteBusy());
  interface.checkRecvFrame(received);
  assert(notification_attempts == 1); // Failed attempts must also be paced.
  ++test_millis;
  assert(!interface.isWriteBusy());
  accept_notification = true; interface.checkRecvFrame(received);
  assert(notification_attempts == 2 && transmitted.size() == 1);
  assert(transmitted[0] == std::vector<uint8_t>(first, first + sizeof(first)));
  assert(!interface.hasPendingIO()); // One accepted copy, no retry/late-status duplicate.
  assert(interface.writeFrame(second, sizeof(second)) == sizeof(second));
  test_millis += interval - 1; interface.checkRecvFrame(received);
  assert(notification_attempts == 2 && transmitted.size() == 1);
  ++test_millis; interface.checkRecvFrame(received);
  assert(notification_attempts == 3 && transmitted.size() == 2);
  assert(transmitted[1] == std::vector<uint8_t>(second, second + sizeof(second)));
  // Real adapter arithmetic must remain correct when millis wraps at UINT32.
  test_millis = UINT32_MAX - (interval / 2);
  assert(interface.writeFrame(first, sizeof(first)) == sizeof(first));
  interface.checkRecvFrame(received);
  assert(notification_attempts == 4);
  assert(interface.writeFrame(second, sizeof(second)) == sizeof(second));
  test_millis += interval - 1;
  assert(interface.isWriteBusy()); interface.checkRecvFrame(received);
  assert(notification_attempts == 4);
  ++test_millis;
  assert(!interface.isWriteBusy()); interface.checkRecvFrame(received);
  assert(notification_attempts == 5 && transmitted.size() == 4);
  interface.checkRecvFrame(received);
  assert(notification_attempts == 5 && !interface.hasPendingIO());
  // A faster lab pace must not shorten or remove the existing stall recovery.
  assert(interface.writeFrame(first, sizeof(first)) == sizeof(first));
  accept_notification = false; test_millis += interval;
  interface.checkRecvFrame(received);
  test_millis += 9999; interface.checkRecvFrame(received);
  assert(disconnects == 0 && interface.hasPendingIO());
  test_millis += interval; interface.checkRecvFrame(received);
  assert(disconnects == 1 && !interface.isConnected());
  NimBLEDevice::deinit(true);
}

static void test_128_contact_stream_under_queue_and_controller_pressure() {
#if defined(MESH_HIL_BLE_INTERVAL_MS)
  constexpr uint32_t interval = MESH_HIL_BLE_INTERVAL_MS;
#else
  constexpr uint32_t interval = 7;
#endif
  reset(); SerialBLEInterface interface;
  assert(interface.begin("MeshCore-", "contacts", 123456));
  interface.enable();
  NimBLEConnInfo info; authenticate(interface, info);
  auto* server = NimBLEDevice::server.get();
  auto* tx = server->service->characteristics[0].get();
  std::vector<std::vector<uint8_t>> expected;
  for (unsigned index = 0; index < 128; ++index) {
    std::vector<uint8_t> frame(148);
    frame[0] = 3; // Contact response: required delivery, never evicted.
    for (size_t byte = 1; byte < frame.size(); ++byte)
      frame[byte] = static_cast<uint8_t>(index * 17 + byte * 3);
    frame[1] = index;
    expected.push_back(frame);
  }
  unsigned queued = 0, full_queue_rejections = 0;
  auto fill = [&]() {
    while (queued < expected.size()) {
      const auto& frame = expected[queued];
      const size_t accepted = interface.writeFrame(frame.data(), frame.size());
      if (accepted == 0) { ++full_queue_rejections; break; }
      assert(accepted == frame.size());
      ++queued;
    }
  };
  fill();
  assert(queued == 4 && full_queue_rejections == 1 && interface.isWriteBusy());
  uint8_t received[MAX_FRAME_SIZE] = {};
  auto poll = [&]() {
    test_millis += interval;
    assert(interface.checkRecvFrame(received) == 0);
    assert(transmitted.size() <= queued);
    for (size_t index = 0; index < transmitted.size(); ++index)
      assert(transmitted[index] == expected[index]);
  };
  poll();
  assert(notification_attempts == 0 && interface.hasPendingIO()); // No CCCD yet.
  tx->callbacks->onSubscribe(tx, info, 1);
  mtu = 23;
  server->callbacks->onMTUChange(247, info); // Stale event cannot override actual MTU.
  poll();
  assert(notification_attempts == 0 && interface.hasPendingIO());
  mtu = 247;
  server->callbacks->onMTUChange(23, info); // The live peer MTU permits full records.
  bool injected_failures[128] = {};
  bool cccd_paused = false, mtu_paused = false;
  for (unsigned step = 0; step < 512 && transmitted.size() < expected.size(); ++step) {
    const auto delivered = transmitted.size();
    if (delivered == 32 && !cccd_paused) {
      cccd_paused = true;
      tx->callbacks->onSubscribe(tx, info, 0);
      const auto attempts = notification_attempts;
      poll(); poll();
      assert(notification_attempts == attempts && transmitted.size() == delivered);
      tx->callbacks->onSubscribe(tx, info, 1);
    }
    if (delivered == 64 && !mtu_paused) {
      mtu_paused = true;
      mtu = 23;
      server->callbacks->onMTUChange(247, info);
      const auto attempts = notification_attempts;
      poll(); poll();
      assert(notification_attempts == attempts && transmitted.size() == delivered);
      mtu = 247;
      server->callbacks->onMTUChange(23, info);
    }
    const bool fail_this_attempt = (delivered == 0 || delivered == 17
        || delivered == 63 || delivered == 127) && !injected_failures[delivered];
    accept_notification = !fail_this_attempt;
    if (fail_this_attempt) injected_failures[delivered] = true;
    const auto attempts = notification_attempts;
    poll();
    assert(notification_attempts == attempts + 1);
    assert(transmitted.size() == delivered + (fail_this_attempt ? 0 : 1));
    fill(); // Repeatedly hit all four slots without losing the next record.
  }
  assert(queued == 128 && transmitted == expected && cccd_paused && mtu_paused);
  assert(notification_attempts == 132 && full_queue_rejections > 100);
  assert(disconnects == 0 && !interface.hasPendingIO());
  poll(); poll();
  assert(transmitted == expected && notification_attempts == 132); // No duplicates.
  NimBLEDevice::deinit(true);
}

static void test_full_bond_store_never_evicts_existing_peer() {
  reset(); SerialBLEInterface interface;
  assert(interface.begin("MeshCore-", "test", 123456)); interface.enable();
  auto* callbacks = device_callbacks; assert(callbacks);
  ble_store_status_event event{}; event.event_code = BLE_STORE_EVENT_OVERFLOW;
  const auto deletions = bond_deletions;
  assert(callbacks->onStoreStatus(&event, nullptr) == BLE_HS_ESTORE_CAP);
  assert(bond_deletions == deletions);
  event.event_code = BLE_STORE_EVENT_FULL; event.full.obj_type = BLE_STORE_OBJ_TYPE_OUR_SEC;
  stored_count = 14; assert(callbacks->onStoreStatus(&event, nullptr) == 0);
  stored_count = 15; key_error = 0; assert(callbacks->onStoreStatus(&event, nullptr) == 0);
  key_error = 5; assert(callbacks->onStoreStatus(&event, nullptr) == BLE_HS_ESTORE_CAP);
  assert(bond_deletions == deletions);
  NimBLEDevice::deinit(true);
}

int main() {
  test_full_bond_store_never_evicts_existing_peer();
  test_identity_modes();
  test_pairing_wake_for_random_and_saved_pins();
  test_startup_failures();
  test_advertising_restart_retries_and_disable_cancels();
  test_authentication_and_bond_lifecycle();
  test_connection_parameters_only_on_current_authenticated_transition();
  test_real_transport_frames();
  test_required_reply_backpressure_services_tx_before_rx();
  test_full_queue_best_effort_eviction_still_admits_rx();
  test_exact_notify_interval_failure_retention_and_rollover();
  test_128_contact_stream_under_queue_and_controller_pressure();
  puts("NimBLE adapter: identity, faults, bonding, stealth and full frames PASS");
}
