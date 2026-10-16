#include "RAK13800EthernetInterface.h"
#include "../../nrf52/EthernetMac.h"
#include "../../nrf52/TerminalPeripheralShutdown.h"
#if defined(OTA_RAK15002_SD_STORE)
  #include "../../ota/OtaSdSpiBusNrf52.h"
#endif

SPIClass ETHERNET_SPI_PORT(NRF_SPIM1, 29, 3, 30);
#define PIN_ETHERNET_RESET 21
#define PIN_ETHERNET_SS 26
#ifndef RAK4631_ETHERNET_TASK_STACK_WORDS
  #define RAK4631_ETHERNET_TASK_STACK_WORDS 1024
#endif
static_assert(RAK4631_ETHERNET_TASK_STACK_WORDS >= 1024 && RAK4631_ETHERNET_TASK_STACK_WORDS <= 65535,
              "Ethernet worker stack budget");
static_assert(sizeof(StaticTask_t) + 32 <= 256, "Ethernet task metadata budget");

bool RAK13800EthernetInterface::begin() {
  if (!mesh::nrf52::registerTerminalEthernetShutdown(terminalShutdown, this)) return false;
#if defined(OTA_RAK15002_SD_STORE)
  return true; // Shared CS26 stays untouched until explicit ownership admission.
#else
  return beginHardware();
#endif
}

bool RAK13800EthernetInterface::hardwareActive() const {
  return _task != nullptr
#if defined(OTA_RAK15002_SD_STORE)
      || _ownsSharedSpi || _releasePending
#endif
      ;
}

const char* RAK13800EthernetInterface::status() const {
  static const char* const names[] = {
    "off", "starting", "retrying", "online", "stopping", "faulted", "no hardware", "start failed"
  };
  return names[static_cast<uint8_t>(_state.load())];
}

void RAK13800EthernetInterface::notifyWorker() {
  taskENTER_CRITICAL();
  if (_task != nullptr) xTaskNotifyGive(_task);
  taskEXIT_CRITICAL();
}

bool RAK13800EthernetInterface::storageConflict() {
#if defined(OTA_RAK15002_SD_STORE)
  if (_storageFault.load()) return true;
  if (_ownsSharedSpi && (mesh::ota::ota_rak15002_sd_card_present()
      || !mesh::ota::rak4631_ethernet_owns_spi())) {
    // A card inserted during a vendor call cannot cancel that call. Exclude
    // every subsequent transaction and retain the bus claim until safe OFF.
    _storageFault.store(true);
    _state.store(State::Faulted);
    notifyWorker();
    return true;
  }
#endif
  return false;
}

bool RAK13800EthernetInterface::tryEnable() {
  if (_terminalStopping) { _spiError = "terminal shutdown"; return false; }
  if (_storageFault.load()) { _spiError = "SD inserted; set eth off first"; return false; }
  const State state = _state.load();
  if (state == State::Stopping) { _spiError = "Ethernet is stopping"; return false; }
  if (_task && !_stopped.load()) {
    if (!_wanted.load() || (state != State::Starting && state != State::Retrying && state != State::Online)) {
      _spiError = status(); return false;
    }
    SerialEthernetInterface::enable(); return true;
  }
  if (!finishRelease()) { _spiError = "OTA storage release pending"; return false; }
#if defined(OTA_RAK15002_SD_STORE)
  if (!_prepareSpi || !_canReleaseSpi || !_releasedSpi) {
    _spiError = "OTA ownership hooks unavailable"; return false;
  }
  if (mesh::ota::ota_rak15002_sd_card_present()) {
    _spiError = "remove SD card: Ethernet and SD share CS26"; return false;
  }
  if (!_prepareSpi(_spiContext)) { _spiError = "OTA storage busy"; return false; }
  if (!mesh::ota::rak4631_try_acquire_spi(mesh::ota::RakSharedSpiOwner::Ethernet)) {
    _releasePending = !_releasedSpi(_spiContext);
    _spiError = "WisBlock SPI busy"; return false;
  }
  _ownsSharedSpi = true;
#endif
  return beginHardware();
}

bool RAK13800EthernetInterface::beginHardware() {
  if (_terminalStopping) return false;
  _stopped.store(false);
  _storageFault.store(false);
  _closeRequested.store(false);
  _eventPending.store(false);
  _ready.store(false);
  _socket = 0xFF;
  _listener = 0xFF;
  _listenerCommand = 0;
  _ip.store(0);
  _checkCommands = false;
  _txAccepted = 0;
  _transmit.reset();
  _access.store(Access::Worker);
  _state.store(State::Starting);
  _wanted.store(true);
  // Handle publication, notifications and deletion share this scheduler guard.
  taskENTER_CRITICAL();
  const BaseType_t created = xTaskCreate(workerTask, "eth_comp", RAK4631_ETHERNET_TASK_STACK_WORDS,
                                       this, 1, &_task);
  taskEXIT_CRITICAL();
  if (created != pdPASS) {
    _task = nullptr;
    _wanted.store(false);
    (void)finishRelease();
    _state.store(State::StartFailed);
    _spiError = "Ethernet task allocation failed";
    return false;
  }
  SerialEthernetInterface::enable();
  _spiError = "starting";
  return true;
}

bool RAK13800EthernetInterface::tryDisable() {
  if (_terminalStopping) { _spiError = "terminal shutdown"; return false; }
#if defined(OTA_RAK15002_SD_STORE)
  if ((_ownsSharedSpi || _releasePending) && (!_canReleaseSpi || !_canReleaseSpi(_spiContext))) {
    _spiError = "OTA storage busy"; return false;
  }
#endif
  SerialEthernetInterface::disable();
  _wanted.store(false);
  _state.store(State::Stopping);
  notifyWorker();
  if (finishRelease()) _state.store(State::Off);
  _spiError = status();
  return true; // Accepted; an entered vendor call cooperatively finishes first.
}

bool RAK13800EthernetInterface::finishRelease() {
  if (_task) {
    if (!_stopped.load()) return false;
    taskENTER_CRITICAL();
    TaskHandle_t task = _task;
    _task = nullptr;
    taskEXIT_CRITICAL();
    // Reclaim synchronously, before the OTA release hook can allocate storage.
    vTaskDelete(task);
    _isConnected = false;
    onClientDisconnected();
    mesh::nrf52::releaseRakEthernetBusPads();
  }
#if defined(OTA_RAK15002_SD_STORE)
  if (_ownsSharedSpi) {
    // Do not expose storage until its main-thread context accepts the handoff.
    if (!_releasedSpi || !_releasedSpi(_spiContext)) return false;
    mesh::ota::rak4631_release_spi(mesh::ota::RakSharedSpiOwner::Ethernet);
    _ownsSharedSpi = false;
  }
  if (_releasePending) {
    if (!_releasedSpi || !_releasedSpi(_spiContext)) return false;
    _releasePending = false;
  }
#endif
  _storageFault.store(false);
  _eventPending.store(false);
  return true;
}

bool RAK13800EthernetInterface::terminalShutdown(void* context, uint32_t timeout_ms) {
  return static_cast<RAK13800EthernetInterface*>(context)->shutdownForPowerOff(timeout_ms);
}

bool RAK13800EthernetInterface::shutdownForPowerOff(uint32_t timeout_ms) {
  _terminalStopping = true;
  SerialEthernetInterface::disable();
  _wanted.store(false);
  _state.store(State::Stopping);
  notifyWorker();
  const uint32_t started = millis();
  do {
    if (finishRelease()) { _state.store(State::Off); return true; }
    if (static_cast<uint32_t>(millis() - started) >= timeout_ms) return false;
    vTaskDelay(pdMS_TO_TICKS(1));
  } while (true);
}

RAK13800EthernetInterface::ClientAccess::ClientAccess(RAK13800EthernetInterface& owner)
    : _owner(owner), _acquired(false) {
  if (!owner._wanted.load() || owner.storageConflict()) return;
  Access expected = Access::Idle;
  _acquired = owner._access.compare_exchange_strong(expected, Access::Client);
  if (_acquired && (!owner._wanted.load() || owner.storageConflict())) {
    owner._access.store(Access::Idle);
    _acquired = false;
  }
}

RAK13800EthernetInterface::ClientAccess::~ClientAccess() {
  if (!_acquired) return;
  if (_owner._checkCommands || _owner._transmit.pending() || _owner._transmit.failed()) {
    _owner._access.store(Access::Worker);
    _owner.serviceIOAvailability();
    _owner.notifyWorker();
  } else {
    _owner._access.store(Access::Idle);
  }
}

bool RAK13800EthernetInterface::isIOUnavailable() const {
  return _access.load() != Access::Idle || _storageFault.load();
}

int RAK13800EthernetInterface::available() {
  ClientAccess access(*this);
  if (!access || !_isConnected) return 0;
  const uint8_t socket = _socket;
  const int result = mesh::nrf52::EthernetCliTransmit::available(socket);
  if (!mesh::nrf52::EthernetCliTransmit::commandReady(socket)) requireCommandCheck();
  return result;
}

int RAK13800EthernetInterface::read() {
  ClientAccess access(*this);
  if (!access || !_isConnected) return -1;
  const uint8_t socket = _socket;
  const int result = mesh::nrf52::EthernetCliTransmit::read(socket);
  if (!mesh::nrf52::EthernetCliTransmit::commandReady(socket)) requireCommandCheck();
  return result;
}

size_t RAK13800EthernetInterface::write(const uint8_t *buf, size_t size) {
  ClientAccess access(*this);
  if (!access || !_isConnected) return 0;
  if (_txAccepted) {
    // The base retains its frame/offset while the worker waits for ACK. A
    // replacement resets this count and replays the whole frame on the new
    // stream, matching the old synchronous vendor write's queue semantics.
    if (_transmit.pending() || _transmit.failed()) return 0;
    const size_t accepted = _txAccepted;
    _txAccepted = 0;
    return accepted;
  }
  _txAccepted = _transmit.write(_socket, buf, size, millis());
  return 0;
}

bool RAK13800EthernetInterface::isConnected() const {
  // DHCP and a stalled vendor socket call are backpressure, not disconnects.
  return isEnabled() && _isConnected && !_storageFault.load();
}

void RAK13800EthernetInterface::disconnectClient() {
  _isConnected = false;
  _closeRequested.store(true);
  notifyWorker();
}

void RAK13800EthernetInterface::loop() {
  serviceIOAvailability();
  (void)storageConflict();
  if (_storageFault.load()) {
    _spiError = "SD inserted; set eth off before SD access";
    SerialEthernetInterface::disable();
  }
  if (_eventPending.load()) {
    const uint8_t socket = _socket;
    _isConnected = false;
    if (_wanted.load() && isEnabled() && socket < 4 && !_storageFault.load()) {
      onClientConnected(_eventIp);
      _isConnected = true;
    } else {
      onClientDisconnected();
    }
    // Until this acknowledgement, worker retains SPI and no frame can reach
    // a replacement owner before main-thread response cancellation/replay.
    _eventPending.store(false);
    notifyWorker();
  }
  if (!_wanted.load() && finishRelease()) _state.store(State::Off);
  if (_wanted.load() && !_storageFault.load() && _state.load() == State::Online) {
    if (_ready.load()) {
      // Both edges are observed on main, even if MultiSerial has temporarily
      // pinned another reply route and skips Ethernet frame polling.
      _access.store(Access::Idle);
      _ready.store(false);
      _pollAt = millis() + 20;
    } else if (static_cast<int32_t>(static_cast<uint32_t>(millis()) - _pollAt) >= 0) {
      Access expected = Access::Idle;
      if (_access.compare_exchange_strong(expected, Access::Worker)) notifyWorker();
    }
  }
  serviceIOAvailability();
}

void RAK13800EthernetInterface::takeWorkerAccess() {
  while (_access.load() != Access::Worker) {
    Access expected = Access::Idle;
    if (_access.compare_exchange_strong(expected, Access::Worker)) break;
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

void RAK13800EthernetInterface::requireCommandCheck() {
  if (!_checkCommands) { _checkCommands = true; _checkStarted = millis(); }
}

bool RAK13800EthernetInterface::commandsIdle() {
  if (storageConflict()) return false;
  SPIClass* spi = W5100.getSPI();
  if (!spi) return false;
  spi->beginTransaction(SPI_ETHERNET_SETTINGS);
  bool idle = true;
  for (uint8_t socket = 0; socket < 4; ++socket) {
    if (W5100.readSnCR(socket) != 0) idle = false;
  }
  if (idle && _listenerCommand) {
    const uint8_t status = W5100.readSnSR(_listener);
    if (_listenerCommand == Sock_OPEN) idle = status == SnSR::INIT;
    else if (_listenerCommand == Sock_CLOSE) idle = status == SnSR::CLOSED;
    else idle = status == SnSR::LISTEN || status == SnSR::SYNRECV ||
        status == SnSR::ESTABLISHED || status == SnSR::CLOSE_WAIT;
    if (idle) _listenerCommand = 0;
  }
  spi->endTransaction();
  return idle;
}

bool RAK13800EthernetInterface::closeSocket(uint8_t socket) {
  if (socket >= 4) return true;
  if (!mesh::nrf52::EthernetCliTransmit::close(socket)) return false;
  requireCommandCheck();
  return true;
}

void RAK13800EthernetInterface::publishClient(uint8_t socket, uint32_t ip) {
  _socket = socket;
  _eventIp = ip;
  _eventPending.store(true);
  while (_eventPending.load() && _wanted.load() && !storageConflict()) {
    (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(20));
  }
}

uint8_t RAK13800EthernetInterface::acceptClient() {
  // OPEN/LISTEN are controller commands, not network waits. Issue each only
  // once and reuse the finite command-ack watchdog before its next phase.
  // Avoid virtual EthernetServer/Client objects: their unused Stream methods
  // otherwise retain the blocking TCP implementation in small Full images.
  if (storageConflict()) return 0xFF;
  SPIClass* spi = W5100.getSPI();
  if (!spi || W5100.getChip() != 51) { _state.store(State::Faulted); return 0xFF; }
  spi->beginTransaction(SPI_ETHERNET_SETTINGS);
  uint8_t accepted = 0xFF;
  if (_listener < 4) {
    const uint8_t status = W5100.readSnSR(_listener);
    if (status == SnSR::ESTABLISHED || status == SnSR::CLOSE_WAIT) {
      accepted = _listener;
      _listener = 0xFF;
    } else if (status == SnSR::CLOSED) {
      _listener = 0xFF;
    } else if (status == SnSR::INIT) {
      if (W5100.readSnCR(_listener) == 0) {
        W5100.writeSnCR(_listener, Sock_LISTEN);
        _listenerCommand = Sock_LISTEN;
      }
      requireCommandCheck();
    }
  }
  if (_listener >= 4) {
    uint8_t closing = 0xFF;
    for (uint8_t socket = 0; socket < 4; ++socket) {
      if (socket == accepted || socket == _socket) continue;
      const uint8_t status = W5100.readSnSR(socket);
      if (status == SnSR::CLOSED) {
        if (W5100.readSnCR(socket) != 0) { requireCommandCheck(); break; }
        _listener = socket;
        delayMicroseconds(250); // Preserve the pinned vendor's pre-OPEN settling guard.
        W5100.writeSnMR(socket, SnMR::TCP);
        W5100.writeSnIR(socket, 0xFF);
        W5100.writeSnPORT(socket, ETHERNET_TCP_PORT);
        W5100.writeSnCR(socket, Sock_OPEN);
        _listenerCommand = Sock_OPEN;
        requireCommandCheck();
        break;
      }
      if (status == SnSR::LAST_ACK || status == SnSR::TIME_WAIT ||
          status == SnSR::FIN_WAIT || status == SnSR::CLOSING) closing = socket;
    }
    if (_listener >= 4 && closing < 4) {
      // Match the vendor allocator's fallback without overwriting a live
      // client or DHCP UDP socket. OPEN follows on a later acknowledged turn.
      if (W5100.readSnCR(closing) == 0) {
        W5100.writeSnCR(closing, Sock_CLOSE);
        _listener = closing;
        _listenerCommand = Sock_CLOSE;
      }
      requireCommandCheck();
    }
  }
  spi->endTransaction();
  return accepted;
}

void RAK13800EthernetInterface::workerTask(void* context) {
  static_cast<RAK13800EthernetInterface*>(context)->runWorker();
}

void RAK13800EthernetInterface::runWorker() {
  bool fault = false, hardware = false;
  if (_wanted.load() && !storageConflict()) {
    pinMode(PIN_ETHERNET_RESET, OUTPUT);
    digitalWrite(PIN_ETHERNET_RESET, HIGH);
    // The pinned W5100 init is cached and does not restore CS after OFF.
    digitalWrite(PIN_ETHERNET_SS, HIGH);
    pinMode(PIN_ETHERNET_SS, OUTPUT);
    ETHERNET_SPI_PORT.begin();
    _spiStarted = true;
    Ethernet.init(ETHERNET_SPI_PORT, PIN_ETHERNET_SS);
    uint8_t mac[6];
    generateEthernetMac(mac);
    while (_wanted.load() && !storageConflict()) {
#if defined(ETHERNET_STATIC_IP) && defined(ETHERNET_STATIC_GATEWAY) && defined(ETHERNET_STATIC_SUBNET) && defined(ETHERNET_STATIC_DNS)
      Ethernet.begin(mac, IPAddress(ETHERNET_STATIC_IP), IPAddress(ETHERNET_STATIC_DNS),
                     IPAddress(ETHERNET_STATIC_GATEWAY), IPAddress(ETHERNET_STATIC_SUBNET));
      const bool online = true;
#else
      const bool online = Ethernet.begin(mac, 10000, 2000) != 0;
#endif
      if (storageConflict() || !_wanted.load()) break;
      hardware = Ethernet.hardwareStatus() != EthernetNoHardware;
      if (!hardware) { _state.store(State::NoHardware); break; }
      if (online) {
        (void)acceptClient();
        if (storageConflict() || !_wanted.load()) break;
        _ip.store(static_cast<uint32_t>(Ethernet.localIP()));
        _state.store(State::Online);
        break;
      }
      _state.store(State::Retrying);
      (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(30000));
    }
  }
  uint32_t lastMaintain = millis();
  while (_wanted.load() && _state.load() == State::Online && !storageConflict()) {
    if (storageConflict() || !_wanted.load()) break;
    if (_checkCommands) {
      if (commandsIdle()) _checkCommands = false;
      else if (static_cast<uint32_t>(millis() - _checkStarted) >= 250) { fault = true; break; }
      else { (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10)); continue; }
    }
    _transmit.poll(millis());
    if (_closeRequested.exchange(false) || _transmit.failed()) {
      if (!closeSocket(_socket)) { fault = true; break; }
      _transmit.reset();
      _txAccepted = 0;
      publishClient(0xFF);
      continue;
    }
    if (_transmit.pending()) { (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10)); continue; }
    if (static_cast<uint32_t>(millis() - lastMaintain) >= 1000) {
      (void)Ethernet.maintain();
      lastMaintain = millis();
      if (storageConflict() || !_wanted.load()) break;
      _ip.store(static_cast<uint32_t>(Ethernet.localIP()));
    }
    // All listener commands are bounded and run only behind the worker gate.
    const uint8_t socket = acceptClient();
    if (storageConflict() || !_wanted.load()) break;
    if (socket < 4) {
      uint8_t address[4];
      SPIClass* spi = W5100.getSPI();
      spi->beginTransaction(SPI_ETHERNET_SETTINGS);
      W5100.readSnDIPR(socket, address);
      spi->endTransaction();
      const uint32_t remote = static_cast<uint32_t>(address[0]) |
          (static_cast<uint32_t>(address[1]) << 8) |
          (static_cast<uint32_t>(address[2]) << 16) |
          (static_cast<uint32_t>(address[3]) << 24);
      if (_socket != socket && !closeSocket(_socket)) { fault = true; break; }
      _transmit.reset();
      _txAccepted = 0;
      // A close/reject requested during blocking accept must also cancel the
      // just-accepted socket rather than briefly resurrecting the session.
      if (_closeRequested.exchange(false)) {
        if (!closeSocket(socket)) { fault = true; break; }
        publishClient(0xFF);
      } else {
        publishClient(socket, remote);
      }
    } else if (_socket < 4 && !mesh::nrf52::EthernetCliTransmit::connected(_socket)) {
      if (!closeSocket(_socket)) { fault = true; break; }
      _transmit.reset();
      _txAccepted = 0;
      publishClient(0xFF);
    }
    if (_checkCommands) continue;
    _ready.store(true);
    while (_ready.load() && _wanted.load() && !storageConflict()) {
      (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(20));
    }
    while (_access.load() != Access::Worker && _wanted.load() && !storageConflict()) {
      (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(20));
    }
  }
  takeWorkerAccess();
  if (_spiStarted) {
    if (!storageConflict() && hardware) {
      for (uint8_t socket = 0; socket < 4; ++socket) (void)closeSocket(socket);
    }
    ETHERNET_SPI_PORT.end();
    digitalWrite(PIN_ETHERNET_SS, HIGH);
    _spiStarted = false;
  }
  _socket = 0xFF;
  _eventPending.store(true);
  if (fault || _storageFault.load()) _state.store(State::Faulted);
  // No controller access follows this flag. Main reclaims the parked task.
  _stopped.store(true);
  for (;;) vTaskDelay(portMAX_DELAY);
}
