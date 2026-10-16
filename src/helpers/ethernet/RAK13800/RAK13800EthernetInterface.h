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

    // BaseSerialInterface methods
    bool isConnected() const override;

    int available() override;
    int read() override;
    size_t write(const uint8_t *buf, size_t size) override;
};
