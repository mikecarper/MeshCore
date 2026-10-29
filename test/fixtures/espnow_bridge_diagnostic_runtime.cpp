// Appended to the real bridge source by test_bridge_recovery.code(). Only
// SDK APIs, clocks, Packet allocation and the bounded debug sink are mocked.
namespace diag = mesh::bridge::diag;

static void startBackend(Backend& backend) {
  backend.wifi = true;
  mesh::wireless::control().begin(backend);
  sdk_initialized = sdk_started = true;
  sdk_mode = WIFI_STA;
}

static void deliver(ESPNowBridge& bridge, const uint8_t* bytes, int len) {
  const uint8_t mac[6] = {1, 2, 3, 4, 5, 6};
  mesh::in_sdk_callback = true;
#if ESP_IDF_VERSION_MAJOR >= 5
  const esp_now_recv_info_t info{mac};
  ESPNowBridge::recv_cb(&info, bytes, len);
#else
  ESPNowBridge::recv_cb(mac, bytes, len);
#endif
  mesh::in_sdk_callback = false;
  (void)bridge;
}

static void deliverCompletion(ESPNowBridge& bridge, int status) {
  mesh::in_sdk_callback = true;
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
  const uint8_t mac[6] = {255, 255, 255, 255, 255, 255};
  const esp_now_send_info_t info{mac};
  ESPNowBridge::send_cb(&info, static_cast<esp_now_send_status_t>(status));
#else
  ESPNowBridge::send_cb(nullptr, static_cast<esp_now_send_status_t>(status));
#endif
  mesh::in_sdk_callback = false;
  (void)bridge;
}

static void receiveProbes() {
  Backend backend;
  startBackend(backend);
  NodePrefs prefs;
  mesh::PacketManager mgr;
  mesh::RTCClock rtc;
  ESPNowBridge bridge(&prefs, &mgr, &rtc);
  uint8_t bytes[16];
  assert(diag::encodeProbe(bytes, sizeof(bytes), {0x12345678U, 0, 6}));
  mgr.exhausted = true;
  for (uint8_t format : {uint8_t(0), uint8_t(1)}) {
    prefs.bridge_format = format;
    bridge.begin();
    assert(bridge._initialized);
    const auto allocations = mgr.allocs;
    const auto received = bridge._stats.probe.rx_ok;
    deliver(bridge, bytes, sizeof(bytes));
    assert(bridge._stats.probe.rx_ok == received); // callback only copies
    bridge.loop();
    assert(bridge._stats.probe.rx_ok == received + 1);
    assert(bridge._stats.probe.last_rx_nonce == 0x12345678U);
    assert(bridge._rx_count == 0 && mgr.allocs == allocations);
    assert(mgr.received.empty() && bridge._seen_packets.ids.empty());
    assert(send_calls == 0 && sent.empty()); // no echo or LoRa admission
    bytes[15] ^= 1;
    deliver(bridge, bytes, sizeof(bytes));
    bridge.loop();
    bytes[15] ^= 1;
    assert(bridge._stats.probe.rx_bad == uint32_t(format + 1));
    assert(bridge._rx_count == 0 && mgr.allocs == allocations);
    bridge.end();
  }
  assert(bridge._stats.probe.rx_ok == 2 && bridge._stats.probe.rx_bad == 2);
}

static void probeOneShot() {
  Backend backend;
  startBackend(backend);
  NodePrefs prefs;
  mesh::PacketManager mgr;
  mesh::RTCClock rtc;
  ESPNowBridge bridge(&prefs, &mgr, &rtc);
  assert(!bridge.sendTest(1) && send_calls == 0);
  bridge.begin();
  assert(bridge.sendTest(0x01020304U));
  assert(send_calls == 0 && mgr.allocs == 0);
  bridge.loop();
  assert(send_calls == 1 && sent.size() == 1);
  diag::Probe parsed;
  assert(diag::decodeProbe(sent[0].data(), sent[0].size(), parsed));
  assert(parsed.nonce == 0x01020304U && parsed.channel == 6 && parsed.format == 1);
  deliverCompletion(bridge, ESP_NOW_SEND_FAIL);
  bridge.loop();
  assert(bridge._tx_count == 0 && bridge._stats.probe.tx_fail == 1);
  for (unsigned i = 0; i < 4; ++i) { now_ms += 20; bridge.loop(); }
  assert(send_calls == 1); // callback failure never retries a probe

  send_result = ESP_ERR_ESPNOW_NO_MEM;
  assert(bridge.sendTest(2));
  bridge.loop();
  assert(send_calls == 2 && bridge._tx_count == 0);
  assert(bridge._stats.probe.tx_fail == 2);
  for (unsigned i = 0; i < 4; ++i) { now_ms += 20; bridge.loop(); }
  assert(send_calls == 2); // ordinary data retry policy does not apply

  send_result = ESP_OK;
  assert(bridge.sendTest(3));
  bridge.loop();
  assert(send_calls == 3 && bridge._tx_waiting);
  now_ms += 1000;
  bridge.loop();
  assert(bridge._initialized && bridge._tx_count == 0);
  assert(send_calls == 3 && bridge._stats.probe.tx_timeout == 1);
  assert(bridge._stats.tx.callback_timeout == 0); // probes have separate counters
  deliverCompletion(bridge, ESP_NOW_SEND_SUCCESS);
  bridge.loop();
  assert(send_calls == 3 && bridge._stats.probe.tx_ok == 0);
  assert(bridge._seen_packets.ids.empty() && mgr.allocs == 0);
  assert(bridge.sendTest(3)); // a new explicit command may reuse its nonce
  bridge.loop();
  deliverCompletion(bridge, ESP_NOW_SEND_SUCCESS);
  bridge.loop();
  assert(send_calls == 4 && bridge._stats.probe.tx_ok == 1);
  bridge.end();
}

static void queueOwnership() {
  Backend backend;
  startBackend(backend);
  NodePrefs prefs;
  mesh::PacketManager mgr;
  mesh::RTCClock rtc;
  ESPNowBridge bridge(&prefs, &mgr, &rtc);
  bridge.begin();
  auto normal = packet(1);
  bridge.sendPacket(&normal);
  assert(bridge.sendTest(0xABCDEF01U));
  bridge.loop();
  assert(send_calls == 1 && sent[0][0] == normal.data[0]);
  bridge.loop();
  assert(send_calls == 1);
  deliverCompletion(bridge, ESP_NOW_SEND_SUCCESS);
  bridge.loop();
  assert(send_calls == 2 && diag::isProbeCandidate(sent[1].data(), sent[1].size()));
  deliverCompletion(bridge, ESP_NOW_SEND_SUCCESS);
  bridge.loop();
  assert(bridge._tx_count == 0 && bridge._stats.probe.tx_ok == 1);
  assert(bridge._seen_packets.ids == std::vector<uint8_t>{1});
  assert(mgr.allocs == 0 && mgr.received.empty());

  auto zero = packet(0);
  bridge._seen_packets.markSeen(&zero);
  assert(bridge.sendTest(9));
  bridge.end();
  assert(bridge._seen_packets.wasSeen(&zero)); // no fake-packet dedup clear
  bridge.begin();
  for (uint8_t id = 10; id < 16; ++id) {
    auto p = packet(id);
    bridge.sendPacket(&p);
  }
  assert(bridge._tx_count == 6);
  const auto seen = bridge._seen_packets.ids;
  assert(!bridge.sendTest(10));
  assert(bridge._tx_count == 6 && bridge._seen_packets.ids == seen);
  bridge.end();
}

static void admissionCounters() {
  Backend backend;
  startBackend(backend);
  NodePrefs prefs;
  mesh::PacketManager mgr;
  mesh::RTCClock rtc;
  ESPNowBridge bridge(&prefs, &mgr, &rtc);
  bridge.begin();
  auto good = packet(1), filtered = packet(2), invalid = packet(0xEE);
  deliver(bridge, good.data, good.length);
  bridge.loop();
  deliver(bridge, good.data, good.length);
  bridge.loop();
  bridge.allowed = false;
  deliver(bridge, filtered.data, filtered.length);
  bridge.loop();
  bridge.allowed = true;
  deliver(bridge, invalid.data, invalid.length);
  bridge.loop();
  assert(bridge._stats.rx.delivered == 1 && bridge._stats.rx.duplicate == 1);
  assert(bridge._stats.rx.filtered == 1 && bridge._stats.rx.packet_bad == 1);
  assert(bridge._stats.rx.packet_ok == 3);
  assert(mgr.received == std::vector<uint8_t>{1} && mgr.allocs == mgr.frees);
  bridge.resetStats();
  auto tx = packet(3), oversize = packet(4, 256);
  bridge.sendPacket(&tx);
  bridge.sendPacket(&tx);
  bridge.allowed = false;
  bridge.sendPacket(&filtered);
  bridge.allowed = true;
  bridge.sendPacket(&oversize);
  assert(bridge._stats.tx.requested == 4 && bridge._stats.tx.queued == 1);
  assert(bridge._stats.tx.duplicate == 1 && bridge._stats.tx.filtered == 1);
  assert(bridge._stats.tx.oversize == 1);
  bridge.loop();
  deliverCompletion(bridge, ESP_NOW_SEND_SUCCESS);
  bridge.loop();
  assert(bridge._stats.tx.start_ok == 1 && bridge._stats.tx.callback_ok == 1);
  bridge.end();
}

static void overflowAndReset() {
  Backend backend;
  startBackend(backend);
  NodePrefs prefs;
  mesh::PacketManager mgr;
  mesh::RTCClock rtc;
  ESPNowBridge bridge(&prefs, &mgr, &rtc);
  bridge.begin();
  uint8_t probe[16];
  assert(diag::encodeProbe(probe, sizeof(probe), {7, 1, 6}));
  for (unsigned i = 0; i < 5; ++i) deliver(bridge, probe, sizeof(probe));
  assert(bridge._rx_count == 4 && bridge._stats.rx.queue_drop == 1);
  bridge.loop();
  assert(bridge._stats.probe.rx_ok == 4 && bridge._rx_count == 0);
  bridge.end();
  bridge.begin();
  assert(bridge._stats.probe.rx_ok == 4 && bridge._stats.rx.queue_drop == 1);
  assert(bridge.sendTest(8));
  bridge.loop();
  assert(bridge._tx_count == 1 && bridge._tx_waiting);
  const int writes = send_calls;
  bridge.resetStats();
  assert(bridge._initialized && bridge._tx_count == 1 && bridge._tx_waiting);
  assert(send_calls == writes && prefs.bridge_channel == 6 && prefs.bridge_format == 1);
  assert(bridge._stats.rx.frames == 0 && bridge._stats.probe.rx_ok == 0);
  deliverCompletion(bridge, ESP_NOW_SEND_SUCCESS);
  bridge.loop();
  assert(bridge._tx_count == 0 && bridge._stats.probe.tx_ok == 1);
  bridge.end();
}

static void startupErrors() {
  Backend backend;
  startBackend(backend);
  NodePrefs prefs;
  mesh::PacketManager mgr;
  mesh::RTCClock rtc;
  ESPNowBridge bridge(&prefs, &mgr, &rtc);
  struct Expected { int failure; diag::Stage stage; };
  for (const auto& row : {
      Expected{2, diag::Stage::WiFiProtocol}, {4, diag::Stage::EspNowInit},
      {8, diag::Stage::RecvCallback}, {7, diag::Stage::SendCallback},
      {9, diag::Stage::PeerAdd}, {10, diag::Stage::PeerRate}}) {
    failure = row.failure;
    bridge.begin();
    assert(!bridge._initialized && bridge._stats.error.stage == row.stage);
    assert(bridge._stats.error.code == -1 && !espnow_active);
  }
  failure = 0;
  wifi_channel = 3;
  bridge.begin();
  assert(!bridge._initialized && bridge._stats.error.stage == diag::Stage::ChannelConflict);
  assert(sdk_started); // failed bridge setup cannot shut down shared WiFi
  wifi_channel = 6;
  bridge.begin();
  assert(bridge._initialized);
  bridge.end();
}

static void boundedReplies() {
  Backend backend;
  startBackend(backend);
  NodePrefs prefs;
  mesh::PacketManager mgr;
  mesh::RTCClock rtc;
  ESPNowBridge bridge(&prefs, &mgr, &rtc);
  bridge.begin();
  memset(&bridge._stats.rx, 0xFF, sizeof(bridge._stats.rx));
  memset(&bridge._stats.tx, 0xFF, sizeof(bridge._stats.tx));
  memset(&bridge._stats.probe, 0xFF, sizeof(bridge._stats.probe));
  bridge._stats.error = {diag::Stage::ChannelConflict, INT32_MIN, UINT32_MAX};
  for (const char* page : {"rx", "tx", "drops", "test", "mac"}) {
    char reply[160];
    memset(reply, 0xA5, sizeof(reply));
    bridge.formatStats(page, reply, 157);
    assert(memchr(reply, 0, 157) != nullptr);
    assert(static_cast<uint8_t>(reply[157]) == 0xA5);
    assert(strlen(reply) < 157);
    if (!strcmp(page, "mac")) assert(!strcmp(reply, "> mac:02:10:20:30:40:50"));
    printf("%s: %s\n", page, reply);
  }
  char status[160];
  memset(status, 0xA5, sizeof(status));
  bridge.formatStatus(status, 157);
  assert(memchr(status, 0, 157) != nullptr && strlen(status) < 157);
  assert(static_cast<uint8_t>(status[157]) == 0xA5);
  printf("status: %s\n", status);
  for (wifi_mode_t mode : {WIFI_AP, WIFI_OFF}) {
    sdk_mode = mode;
    bridge.formatStatus(status, 157);
    assert(strstr(status, "> driver-error ") == status);
    assert(strstr(status, "ch=6/6") != nullptr); // matching channel is insufficient
  }
  sdk_mode = WIFI_AP_STA;
  bridge.formatStatus(status, 157);
  assert(strstr(status, "> ready ") == status);
  sdk_mode = WIFI_STA;
  for (size_t capacity : {size_t(0), size_t(1), size_t(2), size_t(10)}) {
    char reply[160];
    memset(reply, 0xA5, sizeof(reply));
    bridge.formatStats("tx", reply, capacity);
    assert(static_cast<uint8_t>(reply[capacity]) == 0xA5);
    if (capacity) assert(memchr(reply, 0, capacity) != nullptr);
  }
  bridge.end();
}

static void ownerOnlyLogging() {
  Backend backend;
  startBackend(backend);
  NodePrefs prefs;
  mesh::PacketManager mgr;
  mesh::RTCClock rtc;
  ESPNowBridge bridge(&prefs, &mgr, &rtc);
  bridge.begin();
  uint8_t probe[16];
  assert(diag::encodeProbe(probe, sizeof(probe), {1, 1, 6}));
  mesh::master_logging = true;
  mesh::verbose_logging = false;
  deliver(bridge, probe, sizeof(probe));
  bridge.loop();
  assert(mesh::debug_output.empty());
  mesh::master_logging = false;
  mesh::verbose_logging = true;
  deliver(bridge, probe, sizeof(probe));
  bridge.loop();
  assert(mesh::debug_output.empty());
  mesh::master_logging = mesh::verbose_logging = true;
  const auto before = mesh::debug_output.size();
#if !MESH_ESP32_USB_CONSOLE_COOPERATIVE
  mesh::debug_port.capacity = 0;
  deliver(bridge, probe, sizeof(probe));
  now_ms += 2000;
  bridge.loop();
  assert(mesh::debug_output.size() == before); // full UART never blocks/writes
  mesh::debug_port.capacity = 1024;
#endif
  deliver(bridge, probe, sizeof(probe));
  assert(mesh::debug_output.size() == before);
  now_ms += 2000;
  bridge.loop();
  assert(mesh::debug_output.size() > before);
  assert(bridge.sendTest(2));
  bridge.loop();
  const auto after_start = mesh::debug_output.size();
  deliverCompletion(bridge, ESP_NOW_SEND_SUCCESS);
  assert(mesh::debug_output.size() == after_start);
  now_ms += 2000;
  bridge.loop();
  assert(mesh::debug_output.size() > after_start);
  bridge.end();
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "rx-probes")) receiveProbes();
  else if (!strcmp(argv[1], "one-shot")) probeOneShot();
  else if (!strcmp(argv[1], "ownership")) queueOwnership();
  else if (!strcmp(argv[1], "admission")) admissionCounters();
  else if (!strcmp(argv[1], "overflow-reset")) overflowAndReset();
  else if (!strcmp(argv[1], "startup")) startupErrors();
  else if (!strcmp(argv[1], "replies")) boundedReplies();
  else if (!strcmp(argv[1], "logging")) ownerOnlyLogging();
  else assert(false);
}
