#pragma once

#ifdef ETHERNET_ENABLED

#include <Arduino.h>
#include <SPI.h>
#include <RAK13800_W5100S.h>
#include <helpers/nrf52/EthernetMac.h>
#include <helpers/UsbLogging.h>
#include "TerminalPeripheralShutdown.h"
#include <atomic>

// Ethernet startup runs on a FreeRTOS task, and client acceptance runs in the
// mesh loop. Never let either path wait on a CDC host which stopped reading.
// Keep diagnostics separate from functional replies on the Ethernet client.
#define ETHERNET_CLI_LOG(F, ...) do { \
  if (mesh::isUsbDebugLoggingEnabled() && mesh::usbLoggingPort().availableForWrite() > 0) { \
    mesh::usbLoggingPort().printf("ETH: " F "\n", ##__VA_ARGS__); \
  } \
} while (0)

#define PIN_SPI1_MISO (29)
#define PIN_SPI1_MOSI (30)
#define PIN_SPI1_SCK  (3)

static SPIClass ETHERNET_SPI_PORT(NRF_SPIM1, PIN_SPI1_MISO, PIN_SPI1_SCK, PIN_SPI1_MOSI);

#define PIN_ETHERNET_POWER_EN  WB_IO2
#define PIN_ETHERNET_RESET 21
#define PIN_ETHERNET_SS    26

#ifndef ETHERNET_TCP_PORT
  #define ETHERNET_TCP_PORT 23  // telnet port for CLI access
#endif

#ifndef ETHERNET_CLI_BANNER
  #define ETHERNET_CLI_BANNER "MeshCore CLI"
#endif

#define ETHERNET_RETRY_INTERVAL_MS 30000

// New optional-SD infrastructure recipes need the same cooperative, OFF by
// default bus handoff as the combined prototype. Never start legacy DHCP on
// the shared IO-slot CS merely because Ethernet was compiled into the image.
#if defined(RAK4631_COMBINED_ETHERNET) || defined(OTA_RAK15002_SD_STORE)
  #define MESH_RAK_ETHERNET_CLI_RUNTIME 1
#else
  #define MESH_RAK_ETHERNET_CLI_RUNTIME 0
#endif

static EthernetServer ethernet_server(ETHERNET_TCP_PORT);
#if !MESH_RAK_ETHERNET_CLI_RUNTIME
static EthernetClient ethernet_client;
#endif
static std::atomic<bool> ethernet_running{false};
#if MESH_RAK_ETHERNET_CLI_RUNTIME
static std::atomic<bool> ethernet_session_reset{false};
static bool ethernet_line_discarding = false; // Main-thread framing state.
#else
static bool ethernet_session_reset = false;
#endif
static bool ethernet_take_session_reset() {
#if MESH_RAK_ETHERNET_CLI_RUNTIME
  const bool changed = ethernet_session_reset.exchange(false);
  if (changed) ethernet_line_discarding = false;
  return changed;
#else
  const bool changed = ethernet_session_reset;
  ethernet_session_reset = false;
  return changed;
#endif
}

#if MESH_RAK_ETHERNET_CLI_RUNTIME
static void ethernet_check_client();
#include "EthernetCliRuntime.h"
#else

static std::atomic<bool> ethernet_legacy_stop{false};
static std::atomic<bool> ethernet_legacy_worker_active{false};
static bool ethernet_legacy_spi_started = false; // Published by worker completion.
static TaskHandle_t ethernet_legacy_task_handle = nullptr;

static void ethernet_legacy_finish_task() {
  // No more hardware accesses after publication. This task deletes itself;
  // terminal shutdown must never force-delete it inside a vendor SPI call.
  taskENTER_CRITICAL();
  ethernet_legacy_worker_active.store(false);
  ethernet_legacy_task_handle = nullptr;
  taskEXIT_CRITICAL();
  vTaskDelete(nullptr);
}

static bool ethernet_terminal_shutdown(void*, uint32_t timeout_ms) {
  ethernet_legacy_stop.store(true);
  ethernet_running = false;
  taskENTER_CRITICAL();
  if (ethernet_legacy_task_handle) xTaskNotifyGive(ethernet_legacy_task_handle);
  taskEXIT_CRITICAL();
  const uint32_t started = millis();
  while (ethernet_legacy_worker_active.load()) {
    if (static_cast<uint32_t>(millis() - started) >= timeout_ms) return false;
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  ethernet_running = false;
  // Main-loop client/maintain calls are now gated and the worker has no more
  // hardware work. Do not call client.stop(): vendor ACK waits are unbounded.
  ethernet_client = EthernetClient();
  ethernet_session_reset = true;
  if (ethernet_legacy_spi_started) {
    ETHERNET_SPI_PORT.end();
    ethernet_legacy_spi_started = false;
    mesh::nrf52::releaseRakEthernetBusPads();
  }
  return true;
}

// FreeRTOS task: handles hw init, DHCP, and retries in the background
static void ethernet_task(void* param) {
  (void)param;
  if (ethernet_legacy_stop.load()) { ethernet_legacy_finish_task(); return; }

  ETHERNET_CLI_LOG("Initializing hardware");
  // WB_IO2 supplies the Ethernet controller, not PoE's separate 5V input.
  // Retain the existing no-reset startup so the PHY link is not disturbed.
  pinMode(PIN_ETHERNET_RESET, OUTPUT);
  digitalWrite(PIN_ETHERNET_RESET, HIGH);

  ETHERNET_SPI_PORT.begin();
  ethernet_legacy_spi_started = true;
  Ethernet.init(ETHERNET_SPI_PORT, PIN_ETHERNET_SS);

  uint8_t mac[6];
  generateEthernetMac(mac);
  ETHERNET_CLI_LOG("MAC: %02X:%02X:%02X:%02X:%02X:%02X",
      mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

  // Retry loop: keep trying until we get an IP
  while (!ethernet_running && !ethernet_legacy_stop.load()) {
    ETHERNET_CLI_LOG("Attempting DHCP...");
    const bool connected = Ethernet.begin(mac, 10000, 2000) != 0;
    if (ethernet_legacy_stop.load()) break;
    if (!connected) {
      if (Ethernet.hardwareStatus() == EthernetNoHardware) {
        ETHERNET_CLI_LOG("Hardware not found, giving up");
        ethernet_legacy_finish_task();
        return;
      }
      if (Ethernet.linkStatus() == LinkOFF) {
        ETHERNET_CLI_LOG("Cable not connected, will retry");
      } else {
        ETHERNET_CLI_LOG("DHCP failed, will retry");
      }
      (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(ETHERNET_RETRY_INTERVAL_MS));
      continue;
    }

    IPAddress ip = Ethernet.localIP();
    ETHERNET_CLI_LOG("IP: %u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
    ETHERNET_CLI_LOG("Listening on TCP port %d", ETHERNET_TCP_PORT);
    ethernet_server.begin();
    taskENTER_CRITICAL();
    if (!ethernet_legacy_stop.load()) ethernet_running = true;
    taskEXIT_CRITICAL();
  }

  // DHCP succeeded, task is done
  ethernet_legacy_finish_task();
}

static void ethernet_start_task() {
  if (ethernet_legacy_worker_active.load() || ethernet_running || ethernet_legacy_stop.load()) return;
  if (!mesh::nrf52::registerTerminalEthernetShutdown(ethernet_terminal_shutdown, nullptr)) return;
  taskENTER_CRITICAL();
  ethernet_legacy_worker_active.store(true);
  if (xTaskCreate(ethernet_task, "eth_init", 1024, nullptr, 1, &ethernet_legacy_task_handle) != pdPASS) {
    ethernet_legacy_worker_active.store(false);
    ethernet_legacy_task_handle = nullptr;
  }
  taskEXIT_CRITICAL();
}
#endif

// Format ethernet status into reply buffer. Returns true if command was handled.
static bool ethernet_handle_command(const char* command, char* reply) {
#if MESH_RAK_ETHERNET_CLI_RUNTIME
  if (strcmp(command, "eth on") == 0 || strcmp(command, "set eth on") == 0) {
    (void)ethernet_request_start(reply, true);
    return true;
  }
  if (strcmp(command, "eth off") == 0 || strcmp(command, "set eth off") == 0) {
    (void)ethernet_request_stop(reply);
    return true;
  }
  if (strcmp(command, "get eth") == 0) {
    strcpy(reply, ethernet_enabled.load() ? "> on" : "> off");
    return true;
  }
  if (strncmp(command, "eth ", 4) == 0 || strncmp(command, "set eth ", 8) == 0) {
    strcpy(reply, "Error: Ethernet mode must be on or off");
    return true;
  }
#endif
  if (strcmp(command, "eth.status") == 0) {
#if MESH_RAK_ETHERNET_CLI_RUNTIME
    const EthernetCliState state = ethernet_state.load();
    if (state != EthernetCliState::Online) {
      const char* status = state == EthernetCliState::Off ? "off" :
          state == EthernetCliState::Starting ? "starting" :
          state == EthernetCliState::Retrying ? "waiting for DHCP" :
          state == EthernetCliState::Maintaining ? "renewing DHCP" :
          state == EthernetCliState::Stopping ? "stopping" :
          state == EthernetCliState::NoHardware ? "hardware not found" :
          state == EthernetCliState::Faulted ? "controller stalled; turn Ethernet off/on" : "start failed";
      snprintf(reply, 160, "ETH: %s", status);
      return true;
    }
    const uint32_t ip = ethernet_cached_ip.load();
    snprintf(reply, 160, "ETH: %u.%u.%u.%u:%d", (unsigned)(ip & 255),
        (unsigned)((ip >> 8) & 255), (unsigned)((ip >> 16) & 255),
        (unsigned)((ip >> 24) & 255), ETHERNET_TCP_PORT);
    return true;
#endif
    if (!ethernet_running) {
      strcpy(reply, "ETH: not connected");
    } else {
      IPAddress ip = Ethernet.localIP();
      sprintf(reply, "ETH: %u.%u.%u.%u:%d", ip[0], ip[1], ip[2], ip[3], ETHERNET_TCP_PORT);
    }
    return true;
  }
  return false;
}

// Check for new TCP client connections, replacing any existing connection.
// Use accept() (not available()) so we only see newly-accepted sockets;
// available() also returns existing connected sockets that have data, which
// would force us to disambiguate every inbound packet from a real new client.
static void ethernet_check_client() {
#if MESH_RAK_ETHERNET_CLI_RUNTIME
  if (ethernet_storage_conflict()) return;
  // Combined accept/relisten can call vendor controller commands; run only
  // in the background worker while the mesh-facing Stream is gated.
  auto newClient = ethernet_server.accept();
  if (newClient) {
    ethernet_session_generation.fetch_add(1);
    if (ethernet_raw_client && ethernet_raw_client.getSocketNumber() != newClient.getSocketNumber()) {
      (void)mesh::nrf52::EthernetCliTransmit::close(ethernet_raw_client.getSocketNumber());
      ethernet_require_command_check();
    }
    ethernet_queue_clear();
    ethernet_transmit.reset();
    ethernet_raw_client = newClient;
    ethernet_client_attached.store(true);
    ethernet_session_reset = true;
    static const char banner[] = ETHERNET_CLI_BANNER "\r\n";
    (void)ethernet_queue_write(reinterpret_cast<const uint8_t*>(banner), sizeof(banner) - 1);
  }
  if (ethernet_raw_client &&
      !mesh::nrf52::EthernetCliTransmit::connected(ethernet_raw_client.getSocketNumber())) {
    ethernet_session_generation.fetch_add(1);
    (void)mesh::nrf52::EthernetCliTransmit::close(ethernet_raw_client.getSocketNumber());
    ethernet_require_command_check();
    ethernet_raw_client = EthernetClient();
    ethernet_client_attached.store(false);
    ethernet_queue_clear();
    ethernet_transmit.reset();
    ethernet_session_reset = true;
  }
#else
  auto newClient = ethernet_server.accept();
  if (newClient) {
    if (ethernet_client) ethernet_client.stop();
    ethernet_client = newClient;
    ethernet_session_reset = true;
    IPAddress ip = ethernet_client.remoteIP();
    ETHERNET_CLI_LOG("Client connected from %u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
    ethernet_client.println(ETHERNET_CLI_BANNER);
  }
#endif
}

// Call from loop() to maintain DHCP and check for new clients
static void ethernet_loop_maintain() {
#if MESH_RAK_ETHERNET_CLI_RUNTIME
#if defined(OTA_RAK15002_SD_STORE)
  if (ethernet_enabled.load() && ethernet_storage_conflict()) return;
#endif
  if (ethernet_state.load() == EthernetCliState::Stopping) {
    if (ethernet_worker_active.load() && !ethernet_worker_stopped.load()) {
      EthernetSpiAccess expected = EthernetSpiAccess::Idle;
      if (ethernet_spi_access.compare_exchange_strong(expected, EthernetSpiAccess::Worker)) {
        ethernet_running.store(false);
      }
      if (ethernet_spi_access.load() == EthernetSpiAccess::Worker) ethernet_notify_worker();
      return;
    }
    if (ethernet_complete_release()) {
      ethernet_state.store(EthernetCliState::Off);
    }
    return;
  }
  if (!ethernet_running.load() && ethernet_bus_reserved &&
      (!ethernet_worker_active.load() || ethernet_worker_stopped.load())) {
    (void)ethernet_complete_release();
    return;
  }
  if (ethernet_running.load()) {
    if (static_cast<uint32_t>(millis() - ethernet_last_maintain.load()) >= 1000) {
      ethernet_request_maintenance();
    }
  }
#else
  if (ethernet_running) {
    ethernet_check_client();
    Ethernet.maintain();
  }
#endif
}

// Read a line from the Ethernet client into the command buffer.
// Returns true when a complete line is ready to process (command is null-terminated).
// The caller should process the command and then reset ethernet_command[0] = 0.
static bool ethernet_read_line(char* ethernet_command, size_t buf_size) {
#if MESH_RAK_ETHERNET_CLI_RUNTIME
  if (buf_size < 2) return false;
  EthernetClientAccess access;
  if (!access || !ethernet_client_attached.load()) return false;
  const uint32_t generation = ethernet_session_generation.load();
  if (ethernet_session_reset.load() ||
      ((ethernet_command[0] || ethernet_line_discarding) && ethernet_line_generation != generation)) {
    ethernet_command[0] = 0;
    ethernet_line_discarding = false;
    return false;
  }
  ethernet_line_generation = generation;
  const uint8_t socket = ethernet_raw_client.getSocketNumber();
  if (!mesh::nrf52::EthernetCliTransmit::connected(socket)) return false;
  // Reserve one complete synchronous CLI reply before dispatching a command;
  // Print's component writes cannot otherwise retain a partially queued reply.
  if (ethernet_queue_capacity() < 170) return false;
  size_t length = strlen(ethernet_command);
  // One gate covers this bounded turn, so a worker cannot replace the peer
  // between bytes or between the terminal byte and reply-owner publication.
  for (unsigned consumed = 0; consumed < 128 &&
       mesh::nrf52::EthernetCliTransmit::available(socket); ++consumed) {
    const int next = mesh::nrf52::EthernetCliTransmit::read(socket);
    if (!mesh::nrf52::EthernetCliTransmit::commandReady(socket)) ethernet_require_command_check();
    if (next < 0) break;
    const char c = static_cast<char>(next);
    if (c == '\r' || c == '\n') {
      if (ethernet_line_discarding) {
        ethernet_line_discarding = false;
        ethernet_command[0] = 0;
        static const char error[] = "Error: invalid or oversized command; discarded\r\n";
        (void)ethernet_queue_write(reinterpret_cast<const uint8_t*>(error), sizeof(error) - 1);
        ethernet_session_reset.store(true);
        return false;
      }
      if (length == 0) continue;
      ethernet_command[length] = 0;
      ethernet_reply_generation = generation;
      static const uint8_t newline[] = {'\r', '\n'};
      (void)ethernet_queue_write(newline, sizeof(newline));
      return true;
    }
    if (ethernet_line_discarding) continue;
    if (c == '\0' || length == buf_size - 1) {
      ethernet_line_discarding = true;
      ethernet_command[0] = 0;
      length = 0;
      continue;
    }
    ethernet_command[length++] = c;
    ethernet_command[length] = 0;
  }
  return false;
#else
  if (!ethernet_running || !ethernet_client || !ethernet_client.connected()) return false;

  int elen = strlen(ethernet_command);
  while (ethernet_client.available() && elen < (int)buf_size - 1) {
    char c = ethernet_client.read();
    if (c == '\n' && elen == 0) continue;  // ignore leading LF (from CR+LF)
    if (c == '\r' || c == '\n') { ethernet_command[elen++] = '\r'; break; }
    ethernet_command[elen++] = c;
    ethernet_command[elen] = 0;
  }
  if (elen == (int)buf_size - 1) {
    ethernet_command[buf_size - 1] = '\r';
  }

  if (elen > 0 && ethernet_command[elen - 1] == '\r') {
    ethernet_command[elen - 1] = 0;
    ethernet_client.println();
    return true;
  }
  return false;
#endif
}

// Send a reply to the Ethernet client
static void ethernet_send_reply(const char* reply) {
  if (reply[0]) {
#if MESH_RAK_ETHERNET_CLI_RUNTIME
    // One RAM-only enqueue keeps a synchronous command's complete reply even
    // if the worker begins renewal between command dispatch and this call.
    char frame[170];
    const int length = snprintf(frame, sizeof(frame), "  -> %s\r\n", reply);
    if (length > 0 && static_cast<size_t>(length) < sizeof(frame)) {
      (void)ethernet_queue_write(reinterpret_cast<const uint8_t*>(frame), static_cast<size_t>(length),
                                 true, ethernet_reply_generation);
    }
#else
    ethernet_client.print("  -> "); ethernet_client.println(reply);
#endif
  }
}

#undef ETHERNET_CLI_LOG

#endif // ETHERNET_ENABLED
