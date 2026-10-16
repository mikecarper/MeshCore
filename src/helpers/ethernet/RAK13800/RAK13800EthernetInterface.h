#pragma once

#include "../SerialEthernetInterface.h"
#include "../../nrf52/EthernetCliTransmit.h"
#include <SPI.h>
#include <RAK13800_W5100S.h>
#include <atomic>

class RAK13800EthernetInterface : public SerialEthernetInterface {
  enum class Access : uint8_t { Idle, Client, Worker };
  enum class State : uint8_t { Off, Starting, Retrying, Online, Stopping, Faulted, NoHardware, StartFailed };
  std::atomic<Access> _access{Access::Worker};
  std::atomic<State> _state{State::Off};
  std::atomic<bool> _wanted{false}, _stopped{false};
  std::atomic<bool> _storageFault{false}, _closeRequested{false}, _eventPending{false};
  std::atomic<bool> _ready{false};
  // Stop may revoke an unacknowledged event, so the socket remains atomic.
  std::atomic<uint8_t> _socket{0xFF};
  uint8_t _listener = 0xFF; // Worker-only; no virtual TCP stream is needed.
  uint8_t _listenerCommand = 0; // Acknowledgement also verifies the expected SR.
  uint32_t _eventIp = 0; // Published by _eventPending, unchanged during stop.
  std::atomic<uint32_t> _ip{0};
  TaskHandle_t _task = nullptr;
  bool _isConnected = false, _terminalStopping = false, _spiStarted = false;
  bool _checkCommands = false;
  uint32_t _checkStarted = 0;
  uint32_t _pollAt = 0; // Main-thread scheduling; worker never takes an idle bus.
  std::atomic<size_t> _txAccepted{0}; // Also pins the framed FIFO until ACK retirement.
  mesh::nrf52::EthernetCliTransmit _transmit;

  class ClientAccess {
    RAK13800EthernetInterface& _owner;
    bool _acquired;
  public:
    explicit ClientAccess(RAK13800EthernetInterface& owner);
    ~ClientAccess();
    explicit operator bool() const { return _acquired; }
  };
  bool beginHardware();
  bool storageConflict();
  bool finishRelease();
  void notifyWorker();
  void takeWorkerAccess();
  void requireCommandCheck();
  bool commandsIdle();
  bool closeSocket(uint8_t socket);
  uint8_t acceptClient();
  void publishClient(uint8_t socket, uint32_t ip = 0);
  void runWorker();
  static void workerTask(void* context);
#if defined(OTA_RAK15002_SD_STORE)
  bool (*_prepareSpi)(void*) = nullptr;
  bool (*_canReleaseSpi)(void*) = nullptr;
  bool (*_releasedSpi)(void*) = nullptr;
  void* _spiContext = nullptr;
  bool _ownsSharedSpi = false, _releasePending = false;
#endif
  const char* _spiError = "off";

protected:
  void disconnectClient() override;
  bool isIOUnavailable() const override;
  bool hasInFlightFrame() const override { return _txAccepted.load() != 0; }
  bool shutdownForPowerOff(uint32_t timeout_ms);
  static bool terminalShutdown(void* context, uint32_t timeout_ms);

public:
  RAK13800EthernetInterface() = default;
  bool begin();
  void loop() override;
  bool tryEnable();
  bool tryDisable();
  bool hardwareActive() const;
  const char* last_error() const { return _spiError; }
  const char* status() const;
  uint32_t cachedIP() const { return _ip.load(); }
#if defined(OTA_RAK15002_SD_STORE)
  void setSpiOwnershipHooks(bool (*prepare)(void*), bool (*can_release)(void*),
                            bool (*released)(void*), void* context) {
    _prepareSpi = prepare; _canReleaseSpi = can_release;
    _releasedSpi = released; _spiContext = context;
  }
  // Logical transport registration must not acquire the optional shared bus.
  void enable() override { if (_wanted.load()) SerialEthernetInterface::enable(); }
#endif
  void disable() override { (void)tryDisable(); }
  bool isConnected() const override;
  bool hasPendingIO() const override { return hardwareActive() || SerialEthernetInterface::hasPendingIO(); }
  int available() override;
  int read() override;
  size_t write(const uint8_t *buf, size_t size) override;
};
