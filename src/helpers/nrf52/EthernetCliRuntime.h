#pragma once

#include <cstdlib>
#include "EthernetCliTransmit.h"

// Included by EthernetCLI.h only for the opt-in combined RAK4631 prototype.
// Legacy Ethernet profiles retain their established implementation.
struct EthernetCliHooks {
  bool (*prepare)(void*, char*, size_t) = nullptr;
  bool (*can_release)(void*) = nullptr;
  bool (*released)(void*) = nullptr;
  bool (*save)(void*, bool) = nullptr;
  void* context = nullptr;
};

enum class EthernetCliState : uint8_t {
  Off, Starting, Retrying, Online, Maintaining, Stopping, NoHardware, StartFailed, Faulted
};
enum class EthernetSpiAccess : uint8_t { Idle, Client, Worker };
static EthernetCliHooks ethernet_hooks;
static std::atomic<bool> ethernet_enabled{false};
static std::atomic<bool> ethernet_worker_active{false};
static std::atomic<bool> ethernet_worker_stopped{false};
static std::atomic<EthernetCliState> ethernet_state{EthernetCliState::Off};
static std::atomic<EthernetSpiAccess> ethernet_spi_access{EthernetSpiAccess::Worker};
static std::atomic<bool> ethernet_maintain_requested{false};
static std::atomic<uint32_t> ethernet_cached_ip{0};
static std::atomic<uint32_t> ethernet_last_maintain{0};
// Only scheduler-critical sections access the handle. OTA context callbacks
// and bus reservation changes run exclusively on the main thread.
static TaskHandle_t ethernet_task_handle = nullptr;
static bool ethernet_bus_reserved = false;
static bool ethernet_spi_started = false;
static bool ethernet_hardware_found = false;
static EthernetClient ethernet_raw_client;
static std::atomic<bool> ethernet_client_attached{false};
static std::atomic<uint32_t> ethernet_session_generation{0};
static uint32_t ethernet_reply_generation = 0; // Main-thread command owner.
static uint32_t ethernet_line_generation = 0; // Main-thread partial input owner.
static std::atomic<bool> ethernet_command_check_requested{false};
static std::atomic<uint32_t> ethernet_command_check_started{0};
static bool ethernet_controller_faulted = false;
static mesh::nrf52::EthernetCliTransmit ethernet_transmit;

#ifndef ETHERNET_CLI_TX_BUFFER_BYTES
  #define ETHERNET_CLI_TX_BUFFER_BYTES 512
#endif
static_assert(ETHERNET_CLI_TX_BUFFER_BYTES >= 512 && ETHERNET_CLI_TX_BUFFER_BYTES <= 2048,
              "Ethernet queue capacity must match its qualified RAM budget");
// This queue exists only while Ethernet owns SPI. Free it and synchronously
// reclaim the worker before external OTA allocates its mutually exclusive RAM.
static uint8_t* ethernet_tx_queue = nullptr;
static size_t ethernet_tx_head = 0;
static size_t ethernet_tx_count = 0;

#ifndef RAK4631_ETHERNET_TASK_STACK_WORDS
  #define RAK4631_ETHERNET_TASK_STACK_WORDS 1024
#endif
static_assert(RAK4631_ETHERNET_TASK_STACK_WORDS >= 1024,
              "Ethernet worker requires its qualified stack budget");
static_assert(sizeof(StaticTask_t) + 32 <= 256,
              "Ethernet task metadata exceeds its qualified RAM allowance");

static bool ethernet_is_running() { return ethernet_running.load(); }
static bool ethernet_get_enabled() { return ethernet_enabled.load(); }

static void ethernet_notify_worker() {
  taskENTER_CRITICAL();
  if (ethernet_task_handle != nullptr) xTaskNotifyGive(ethernet_task_handle);
  taskEXIT_CRITICAL();
}

static void ethernet_queue_clear() {
  taskENTER_CRITICAL();
  ethernet_tx_head = 0;
  ethernet_tx_count = 0;
  taskEXIT_CRITICAL();
}

static size_t ethernet_queue_capacity() {
  taskENTER_CRITICAL();
  const size_t free = ethernet_tx_queue ? ETHERNET_CLI_TX_BUFFER_BYTES - ethernet_tx_count : 0;
  taskEXIT_CRITICAL();
  return free;
}

static size_t ethernet_queue_write(const uint8_t* data, size_t size,
                                  bool bind_session = false, uint32_t generation = 0) {
  taskENTER_CRITICAL();
  if (!ethernet_tx_queue || !ethernet_client_attached.load() ||
      (bind_session && generation != ethernet_session_generation.load())) {
    taskEXIT_CRITICAL();
    return 0;
  }
  const size_t free = ETHERNET_CLI_TX_BUFFER_BYTES - ethernet_tx_count;
  if (size > free) size = free;
  const size_t tail = (ethernet_tx_head + ethernet_tx_count) % ETHERNET_CLI_TX_BUFFER_BYTES;
  const size_t first = size < ETHERNET_CLI_TX_BUFFER_BYTES - tail ? size : ETHERNET_CLI_TX_BUFFER_BYTES - tail;
  memcpy(ethernet_tx_queue + tail, data, first);
  memcpy(ethernet_tx_queue, data + first, size - first);
  ethernet_tx_count += size;
  taskEXIT_CRITICAL();
  if (size) ethernet_notify_worker();
  return size;
}

static void ethernet_require_command_check() {
  ethernet_running.store(false);
  ethernet_command_check_started.store(millis());
  ethernet_command_check_requested.store(true);
  ethernet_notify_worker();
}

class EthernetClientAccess {
  bool acquired = false;
public:
  EthernetClientAccess() {
    if (!ethernet_running.load()) return;
    EthernetSpiAccess expected = EthernetSpiAccess::Idle;
    acquired = ethernet_spi_access.compare_exchange_strong(expected, EthernetSpiAccess::Client);
    if (acquired && !ethernet_running.load()) {
      ethernet_spi_access.store(EthernetSpiAccess::Idle);
      acquired = false;
    }
  }
  ~EthernetClientAccess() {
    if (!acquired) return;
    ethernet_spi_access.store(EthernetSpiAccess::Idle);
  }
  explicit operator bool() const { return acquired; }
};

// MyMesh retains this Stream for asynchronous replies. It must never expose
// the raw client while DHCP initialization/renewal owns the SPI bus. Busy
// methods yield immediately so radio, USB and UART service can continue.
class EthernetCliClient : public Stream {
public:
  int available() override {
    EthernetClientAccess access;
    if (!access || !ethernet_client_attached.load()) return 0;
    const uint8_t socket = ethernet_raw_client.getSocketNumber();
    const int available = mesh::nrf52::EthernetCliTransmit::available(socket);
    if (!mesh::nrf52::EthernetCliTransmit::commandReady(socket)) ethernet_require_command_check();
    return available;
  }
  int read() override {
    EthernetClientAccess access;
    if (!access || !ethernet_client_attached.load()) return -1;
    const uint8_t socket = ethernet_raw_client.getSocketNumber();
    const int value = mesh::nrf52::EthernetCliTransmit::read(socket);
    if (!mesh::nrf52::EthernetCliTransmit::commandReady(socket)) ethernet_require_command_check();
    return value;
  }
  int peek() override {
    EthernetClientAccess access;
    if (!access || !ethernet_client_attached.load()) return -1;
    const uint8_t socket = ethernet_raw_client.getSocketNumber();
    const int value = mesh::nrf52::EthernetCliTransmit::peek(socket);
    if (!mesh::nrf52::EthernetCliTransmit::commandReady(socket)) ethernet_require_command_check();
    return value;
  }
  int availableForWrite() override {
    EthernetClientAccess access;
    return access && ethernet_client_attached.load() ? static_cast<int>(ethernet_queue_capacity()) : 0;
  }
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* data, size_t size) override {
    EthernetClientAccess access;
    if (!access || ethernet_session_reset.load()) return 0;
    return ethernet_queue_write(data, size);
  }
  void flush() override {} // Never wait for a remote TCP host.
  bool connected() {
    EthernetClientAccess access;
    return access && ethernet_client_attached.load() &&
        mesh::nrf52::EthernetCliTransmit::connected(ethernet_raw_client.getSocketNumber());
  }
  explicit operator bool() { return connected(); }
  void stop() {
    EthernetClientAccess access;
    if (!access) return;
    if (ethernet_raw_client) {
      (void)mesh::nrf52::EthernetCliTransmit::close(ethernet_raw_client.getSocketNumber());
      ethernet_require_command_check();
    }
    ethernet_raw_client = EthernetClient();
    ethernet_client_attached.store(false);
    ethernet_session_generation.fetch_add(1);
    ethernet_queue_clear();
    ethernet_transmit.reset();
    ethernet_line_discarding = false;
    ethernet_session_reset.store(true);
  }
  using Print::write;
};
static EthernetCliClient ethernet_client;

static bool ethernet_configure(const EthernetCliHooks& hooks, bool saved_enabled = false) {
  if (ethernet_bus_reserved || ethernet_worker_active.load()) return false;
  ethernet_hooks = hooks;
  ethernet_enabled.store(saved_enabled);
  ethernet_state.store(EthernetCliState::Off);
  return true;
}

static void ethernet_stop_hardware() {
  // The W5100S has four sockets; there is no EthernetServer::end() in the
  // library. Stop listener, accepted-client and DHCP sockets before SPI.end.
  // Never toggle the PHY reset or shared WisBlock supply during shutdown.
  if (ethernet_spi_started && ethernet_hardware_found) {
    for (uint8_t index = 0; index < 4; ++index) {
      (void)mesh::nrf52::EthernetCliTransmit::close(index);
    }
  }
  ethernet_raw_client = EthernetClient();
  ethernet_client_attached.store(false);
  ethernet_session_generation.fetch_add(1);
  ethernet_running.store(false);
  if (ethernet_spi_started) {
    ETHERNET_SPI_PORT.end();
    digitalWrite(PIN_ETHERNET_SS, HIGH);
    ethernet_spi_started = false;
  }
  ethernet_hardware_found = false;
  ethernet_transmit.reset();
  ethernet_queue_clear();
  ethernet_command_check_requested.store(false);
}

static bool ethernet_complete_release() {
  // Main-thread only. A stopped worker parks with its handle intact. Deleting
  // that other task reclaims its stack synchronously; self-deletion would
  // defer reclamation to the idle task and overlap the external OTA workspace.
  if (ethernet_worker_active.load()) {
    if (!ethernet_worker_stopped.load()) return false;
    taskENTER_CRITICAL();
    TaskHandle_t stopped = ethernet_task_handle;
    ethernet_task_handle = nullptr;
    taskEXIT_CRITICAL();
    if (stopped != nullptr) vTaskDelete(stopped);
    ethernet_worker_active.store(false);
    ethernet_worker_stopped.store(false);
  }
  ethernet_stop_hardware();
  free(ethernet_tx_queue);
  ethernet_tx_queue = nullptr;
  ethernet_line_discarding = false;
  if (ethernet_bus_reserved) {
    if (!ethernet_hooks.released(ethernet_hooks.context)) return false;
    ethernet_bus_reserved = false;
    ethernet_session_reset = true;
  }
  return true;
}

static void ethernet_worker_take_access() {
  while (ethernet_spi_access.load() != EthernetSpiAccess::Worker) {
    EthernetSpiAccess expected = EthernetSpiAccess::Idle;
    if (ethernet_spi_access.compare_exchange_strong(expected, EthernetSpiAccess::Worker)) break;
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  ethernet_running.store(false);
}

static void ethernet_finish_task(EthernetCliState state) {
  // No hardware operation follows this publication. Keep the handle and park
  // until the main thread deletes this task before handing SPI back to OTA.
  taskENTER_CRITICAL();
  ethernet_state.store(state);
  ethernet_running.store(false);
  ethernet_worker_stopped.store(true);
  taskEXIT_CRITICAL();
  for (;;) vTaskDelay(portMAX_DELAY);
}

static bool ethernet_publish_online() {
  taskENTER_CRITICAL();
  const bool enabled = ethernet_enabled.load();
  if (enabled) {
    ethernet_state.store(EthernetCliState::Online);
    ethernet_spi_access.store(EthernetSpiAccess::Idle);
    ethernet_running.store(true);
  }
  taskEXIT_CRITICAL();
  return enabled;
}

static void ethernet_cache_ip() {
  const IPAddress ip = Ethernet.localIP();
  ethernet_cached_ip.store(static_cast<uint32_t>(ip[0]) |
      (static_cast<uint32_t>(ip[1]) << 8) |
      (static_cast<uint32_t>(ip[2]) << 16) |
      (static_cast<uint32_t>(ip[3]) << 24));
}

static bool ethernet_has_tx_work() {
  taskENTER_CRITICAL();
  const bool queued = ethernet_tx_count != 0;
  taskEXIT_CRITICAL();
  return queued || ethernet_transmit.pending();
}

static bool ethernet_controller_commands_idle() {
  SPIClass* spi = W5100.getSPI();
  if (!spi) return false;
  spi->beginTransaction(SPI_ETHERNET_SETTINGS);
  bool idle = true;
  for (uint8_t socket = 0; socket < 4; ++socket) {
    if (W5100.readSnCR(socket) != 0) idle = false;
  }
  spi->endTransaction();
  return idle;
}

static void ethernet_poll_command_check() {
  if (!ethernet_command_check_requested.load()) return;
  if (ethernet_controller_commands_idle()) {
    ethernet_command_check_requested.store(false);
  } else if (static_cast<uint32_t>(millis() - ethernet_command_check_started.load()) >= 250) {
    ethernet_controller_faulted = true;
    ethernet_state.store(EthernetCliState::Faulted);
    ethernet_session_reset.store(true);
  }
}

// Worker-only, while SPI access is Worker. No library socketSend/execCmdSn
// calls: stalled TCP acknowledgement must never hold the mesh loop.
static void ethernet_pump_tx() {
  ethernet_transmit.poll(millis());
  if (ethernet_transmit.failed()) {
    if (ethernet_raw_client) {
      if (!mesh::nrf52::EthernetCliTransmit::close(ethernet_raw_client.getSocketNumber())) {
        ethernet_controller_faulted = true;
        ethernet_state.store(EthernetCliState::Faulted);
      } else {
        ethernet_require_command_check();
      }
    }
    ethernet_raw_client = EthernetClient();
    ethernet_client_attached.store(false);
    ethernet_session_generation.fetch_add(1);
    ethernet_queue_clear();
    ethernet_transmit.reset();
    ethernet_session_reset.store(true);
    return;
  }
  if (!ethernet_raw_client) { ethernet_queue_clear(); return; }
  // Main-loop accesses are excluded by the SPI gate while this worker owns it.
  taskENTER_CRITICAL();
  const size_t head = ethernet_tx_head;
  const size_t count = ethernet_tx_count;
  taskEXIT_CRITICAL();
  if (count == 0) return;
  const size_t contiguous = count < ETHERNET_CLI_TX_BUFFER_BYTES - head
      ? count : ETHERNET_CLI_TX_BUFFER_BYTES - head;
  const size_t written = ethernet_transmit.write(ethernet_raw_client.getSocketNumber(),
      ethernet_tx_queue + head, contiguous, millis());
  taskENTER_CRITICAL();
  ethernet_tx_head = (ethernet_tx_head + written) % ETHERNET_CLI_TX_BUFFER_BYTES;
  ethernet_tx_count -= written;
  taskEXIT_CRITICAL();
}

static void ethernet_drain_before_stop() {
  if (!ethernet_spi_started || !ethernet_hardware_found) return;
  const uint32_t started = millis();
  while (ethernet_has_tx_work() && static_cast<uint32_t>(millis() - started) < 250) {
    ethernet_pump_tx();
    if (!ethernet_has_tx_work()) break;
    (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10));
  }
}

// Persistent background worker: both initial DHCP and renewal may wait for
// network responses. Main-loop Stream operations yield while it owns SPI.
// OFF interrupts retry waits, then lets an in-progress library call finish;
// never delete another task in the middle of its SPI transaction.
static void ethernet_task(void*) {
  if (ethernet_enabled.load()) {
    pinMode(PIN_ETHERNET_RESET, OUTPUT);
    digitalWrite(PIN_ETHERNET_RESET, HIGH);
    ETHERNET_SPI_PORT.begin();
    ethernet_spi_started = true;
    Ethernet.init(ETHERNET_SPI_PORT, PIN_ETHERNET_SS);
    uint8_t mac[6];
    generateEthernetMac(mac);
    while (ethernet_enabled.load()) {
      const bool connected = Ethernet.begin(mac, 10000, 2000) != 0;
      ethernet_hardware_found = Ethernet.hardwareStatus() != EthernetNoHardware;
      if (!ethernet_enabled.load()) break;
      if (!ethernet_hardware_found) {
        ethernet_stop_hardware();
        ethernet_finish_task(EthernetCliState::NoHardware);
        return;
      }
      if (connected) {
        ethernet_server.begin();
        ethernet_cache_ip();
        ethernet_last_maintain.store(millis());
        if (!ethernet_publish_online()) break;
        for (;;) {
          // An online OFF is notified by the next mesh-loop maintain call,
          // allowing a command received over Ethernet to send its reply first.
          ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(20));
          if (!ethernet_enabled.load()) break;
          if (ethernet_controller_faulted) continue;
          const bool maintain = ethernet_maintain_requested.exchange(false);
          ethernet_worker_take_access();
          ethernet_poll_command_check();
          if (ethernet_controller_faulted || ethernet_command_check_requested.load()) {
            // Preserve the maintenance request while command checking yields.
            if (maintain) ethernet_maintain_requested.store(true);
            continue;
          }
          if (maintain) (void)Ethernet.maintain();
          if (!ethernet_enabled.load()) break;
          if (maintain) ethernet_last_maintain.store(millis());
          // Do not enter vendor accept/relisten while SEND/RECV is pending.
          if (!ethernet_transmit.pending()) ethernet_check_client();
          ethernet_pump_tx();
          if (ethernet_controller_faulted || ethernet_transmit.pending() ||
              ethernet_command_check_requested.load()) continue;
          ethernet_cache_ip();
          if (!ethernet_publish_online()) break;
        }
        break;
      }
      ethernet_state.store(EthernetCliState::Retrying);
      ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(ETHERNET_RETRY_INTERVAL_MS));
    }
  }
  ethernet_worker_take_access();
  ethernet_drain_before_stop();
  ethernet_stop_hardware();
  ethernet_finish_task(EthernetCliState::Stopping);
}

static bool ethernet_request_start(char* reply, bool persist) {
  if (ethernet_state.load() == EthernetCliState::Stopping) {
    strcpy(reply, "Error: Ethernet is still stopping");
    return false;
  }
  if (ethernet_worker_stopped.load() && !ethernet_complete_release()) {
    strcpy(reply, "Error: Ethernet bus release is still pending");
    return false;
  }
  if (ethernet_worker_active.load() || ethernet_running.load()) {
    strcpy(reply, "OK - Ethernet already on");
    return true;
  }
  if (ethernet_bus_reserved && !ethernet_complete_release()) {
    strcpy(reply, "Error: Ethernet bus release is still pending");
    return false;
  }
  if (!ethernet_hooks.prepare || !ethernet_hooks.can_release || !ethernet_hooks.released) {
    strcpy(reply, "Error: Ethernet bus safety hooks unavailable");
    return false;
  }
  reply[0] = 0;
  if (!ethernet_hooks.prepare(ethernet_hooks.context, reply, 160)) {
    if (!reply[0]) strcpy(reply, "Error: Ethernet bus is busy");
    return false;
  }
  ethernet_bus_reserved = true;
  ethernet_tx_queue = static_cast<uint8_t*>(malloc(ETHERNET_CLI_TX_BUFFER_BYTES));
  if (!ethernet_tx_queue) {
    (void)ethernet_complete_release();
    ethernet_state.store(EthernetCliState::StartFailed);
    strcpy(reply, "Error: Ethernet queue allocation failed");
    return false;
  }
  ethernet_queue_clear();
  if (persist && ethernet_hooks.save && !ethernet_hooks.save(ethernet_hooks.context, true)) {
    (void)ethernet_complete_release();
    strcpy(reply, "Error: cannot save Ethernet mode");
    return false;
  }
  ethernet_enabled.store(true);
  ethernet_controller_faulted = false;
  ethernet_command_check_requested.store(false);
  ethernet_maintain_requested.store(false);
  ethernet_spi_access.store(EthernetSpiAccess::Worker);
  ethernet_state.store(EthernetCliState::Starting);
  ethernet_worker_stopped.store(false);
  ethernet_worker_active.store(true);
  // Ensure a fast worker cannot clear the output handle before creation has
  // finished publishing it. Handle notification/clearing use the same guard.
  taskENTER_CRITICAL();
  const BaseType_t created = xTaskCreate(ethernet_task, "eth_init",
      RAK4631_ETHERNET_TASK_STACK_WORDS, nullptr, 1, &ethernet_task_handle);
  taskEXIT_CRITICAL();
  if (created != pdPASS) {
    ethernet_task_handle = nullptr;
    ethernet_enabled.store(false);
    ethernet_worker_active.store(false);
    (void)ethernet_complete_release();
    ethernet_state.store(EthernetCliState::StartFailed);
    strcpy(reply, "Error: Ethernet task allocation failed");
    return false;
  }
  strcpy(reply, ethernet_hooks.save
      ? "OK - Ethernet starting; use eth.status"
      : "OK - Ethernet starting until restart; use eth.status");
  return true;
}

static bool ethernet_request_stop(char* reply) {
  if (ethernet_bus_reserved && !ethernet_hooks.can_release(ethernet_hooks.context)) {
    strcpy(reply, "Error: Ethernet mode is locked by OTA activity or staged data");
    return false;
  }
  if (ethernet_hooks.save && !ethernet_hooks.save(ethernet_hooks.context, false)) {
    strcpy(reply, "Error: cannot save Ethernet mode");
    return false;
  }
  taskENTER_CRITICAL();
  ethernet_enabled.store(false);
  const bool active = ethernet_worker_active.load() || ethernet_bus_reserved;
  ethernet_state.store(active ? EthernetCliState::Stopping : EthernetCliState::Off);
  taskEXIT_CRITICAL();
  // Startup/renewal already owns SPI, so wake cancellation immediately.
  // Online OFF waits for the next main-loop maintain to preserve its reply.
  if (ethernet_spi_access.load() == EthernetSpiAccess::Worker) ethernet_notify_worker();
  strcpy(reply, active
      ? (ethernet_hooks.save ? "OK - Ethernet stopping" : "OK - Ethernet stopping until restart")
      : (ethernet_hooks.save ? "OK - Ethernet off" : "OK - Ethernet off until restart"));
  return true;
}

static void ethernet_start_task() {
  if (!ethernet_enabled.load()) return;
  char reply[160];
  (void)ethernet_request_start(reply, false);
}

static void ethernet_request_maintenance() {
  EthernetSpiAccess expected = EthernetSpiAccess::Idle;
  if (!ethernet_spi_access.compare_exchange_strong(expected, EthernetSpiAccess::Worker)) return;
  ethernet_running.store(false);
  ethernet_state.store(EthernetCliState::Maintaining);
  ethernet_last_maintain.store(millis());
  ethernet_maintain_requested.store(true);
  ethernet_notify_worker();
}
