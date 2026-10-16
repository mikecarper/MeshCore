#pragma once

#include "../SerialEthernetInterface.h"
#include <SPI.h>
#include <RAK13800_W5100S.h>

class RAK13800EthernetInterface : public SerialEthernetInterface {
  
  bool _isConnected;
  bool _spiStarted = false;
  bool _terminalStopping = false;
  EthernetServer server;
  EthernetClient client;
  bool beginHardware();
#if defined(OTA_RAK15002_SD_STORE)
  bool (*_prepareSpi)(void*) = nullptr;
  bool (*_canReleaseSpi)(void*) = nullptr;
  bool (*_releasedSpi)(void*) = nullptr;
  void* _spiContext = nullptr;
  bool _ownsSharedSpi = false;
  const char* _spiError = "off";
#endif

  protected:
    void disconnectClient() override;
    bool shutdownForPowerOff(uint32_t timeout_ms);
    static bool terminalShutdown(void* context, uint32_t timeout_ms);

  public:
    RAK13800EthernetInterface() : server(EthernetServer(ETHERNET_TCP_PORT)) {
      _isConnected = false;
    }
    
    bool begin();
    void loop() override;
#if defined(OTA_RAK15002_SD_STORE)
    void setSpiOwnershipHooks(bool (*prepare)(void*), bool (*can_release)(void*),
                              bool (*released)(void*), void* context) {
      _prepareSpi = prepare; _canReleaseSpi = can_release;
      _releasedSpi = released; _spiContext = context;
    }
    bool tryEnable();
    bool tryDisable();
    bool hardwareActive() const { return _spiStarted; }
    const char* last_error() const { return _spiError; }
    // The transport manager enables every registered interface at boot. Do
    // not turn that logical operation into a physical shared-bus acquisition.
    void enable() override { if (_spiStarted) SerialEthernetInterface::enable(); }
    void disable() override { (void)tryDisable(); }
#endif

    // BaseSerialInterface methods
    bool isConnected() const override;

    int available() override;
    int read() override;
    size_t write(const uint8_t *buf, size_t size) override;
};
