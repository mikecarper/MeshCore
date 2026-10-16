#include "RAK13800EthernetInterface.h"
#include "../../nrf52/EthernetMac.h"
#include "../../nrf52/TerminalPeripheralShutdown.h"
#include "../../nrf52/EthernetCliTransmit.h"
#include <SPI.h>
#include <EthernetUdp.h>

#define PIN_SPI1_MISO (29) // (0 + 29)
#define PIN_SPI1_MOSI (30) // (0 + 30)
#define PIN_SPI1_SCK (3)   // (0 + 3)

SPIClass ETHERNET_SPI_PORT(NRF_SPIM1, PIN_SPI1_MISO, PIN_SPI1_SCK, PIN_SPI1_MOSI);

#define PIN_ETHERNET_POWER_EN WB_IO2    // output, high to enable
#define PIN_ETHERNET_RESET 21
#define PIN_ETHERNET_SS 26

bool RAK13800EthernetInterface::begin() {
  if (_terminalStopping) return false;
  if (!mesh::nrf52::registerTerminalEthernetShutdown(terminalShutdown, this)) return false;
  // Preserve existing startup/link timing. PoE supplies separate 5V through
  // the base's VCC_IN; WB_IO2 only supplies the W5100S's switched 3V3_S.
#ifdef PIN_ETHERNET_RESET
        pinMode(PIN_ETHERNET_RESET, OUTPUT);
        digitalWrite(PIN_ETHERNET_RESET, HIGH);
#endif

  // generate mac address
  uint8_t mac[6];
  generateEthernetMac(mac);
  ETHERNET_DEBUG_PRINTLN(
      "Ethernet MAC: %02X:%02X:%02X:%02X:%02X:%02X",
      mac[0],
      mac[1],
      mac[2],
      mac[3],
      mac[4],
      mac[5]);
  ETHERNET_SPI_PORT.begin();
  _spiStarted = true;
  Ethernet.init(ETHERNET_SPI_PORT, PIN_ETHERNET_SS);

  // Use static IP if build flags are defined, otherwise DHCP
  #if defined(ETHERNET_STATIC_IP) && defined(ETHERNET_STATIC_GATEWAY) && defined(ETHERNET_STATIC_SUBNET) && defined(ETHERNET_STATIC_DNS)
  IPAddress ip(ETHERNET_STATIC_IP);
  IPAddress gateway(ETHERNET_STATIC_GATEWAY);
  IPAddress subnet(ETHERNET_STATIC_SUBNET);
  IPAddress dns(ETHERNET_STATIC_DNS);
  Ethernet.begin(mac, ip, dns, gateway, subnet);
  #else
  if (Ethernet.begin(mac) == 0) {
    ETHERNET_DEBUG_PRINTLN("Failed to initialize RAK13800 hardware.");
    if (Ethernet.hardwareStatus() == EthernetNoHardware) {
      ETHERNET_DEBUG_PRINTLN("Ethernet hardware not found.");
    } else if (Ethernet.linkStatus() == LinkOFF) {
      ETHERNET_DEBUG_PRINTLN("Ethernet cable not connected.");
    } else {
      ETHERNET_DEBUG_PRINTLN("DHCP failed for unknown reason.");
    }
    return false;
  }
  #endif

  ETHERNET_DEBUG_PRINTLN("Ethernet begin complete");
  ETHERNET_DEBUG_PRINT_IP("IP Address", Ethernet.localIP());
  ETHERNET_DEBUG_PRINT_IP("Subnet Mask", Ethernet.subnetMask());
  ETHERNET_DEBUG_PRINT_IP("Gateway", Ethernet.gatewayIP());
  ETHERNET_DEBUG_PRINT_IP("DNS", Ethernet.dnsServerIP());

  server.begin();
  ETHERNET_DEBUG_PRINTLN("listening on TCP port: %d", ETHERNET_TCP_PORT);

  return true;
}

bool RAK13800EthernetInterface::terminalShutdown(void* context, uint32_t timeout_ms) {
  return static_cast<RAK13800EthernetInterface*>(context)->shutdownForPowerOff(timeout_ms);
}

bool RAK13800EthernetInterface::shutdownForPowerOff(uint32_t timeout_ms) {
  (void)timeout_ms; // All operations below are finite register transfers.
  _terminalStopping = true;
  // disable() cancels framed/session producers, but disconnectClient() must
  // not enter the vendor socket-stop/ACK wait during terminal shutdown.
  SerialEthernetInterface::disable();
  if (!_spiStarted) return true;
  for (uint8_t socket = 0; socket < 4; ++socket) {
    (void)mesh::nrf52::EthernetCliTransmit::close(socket);
  }
  client = EthernetClient();
  ETHERNET_SPI_PORT.end();
  _spiStarted = false;
  mesh::nrf52::releaseRakEthernetBusPads();
  return true;
}

int RAK13800EthernetInterface::available() {
  if (!_spiStarted || _terminalStopping) return 0;
  return client.available();
}

int RAK13800EthernetInterface::read() {
  if (!_spiStarted || _terminalStopping) return -1;
  return client.read();
}

size_t RAK13800EthernetInterface::write(const uint8_t *buf, size_t size) {
  if (!_spiStarted || _terminalStopping) return 0;
  return client.write(buf, size);
}

bool RAK13800EthernetInterface::isConnected() const {
  return isEnabled() && _isConnected;
}

void RAK13800EthernetInterface::disconnectClient() {
  _isConnected = false;
  if (_spiStarted && !_terminalStopping) client.stop();
}

void RAK13800EthernetInterface::loop() {
  if (!_spiStarted || _terminalStopping) return;
  Ethernet.maintain();
  if (!isEnabled()) return;
  if (_isConnected && !client.connected()) {
    _isConnected = false;
    onClientDisconnected();
  }
  
  auto newClient = server.accept();
  if (newClient) {
    IPAddress remoteIp = newClient.remoteIP();
    uint16_t remotePort = newClient.remotePort();
    ETHERNET_DEBUG_PRINTLN("New client accepted %u.%u.%u.%u:%u", remoteIp[0], remoteIp[1], remoteIp[2], remoteIp[3], remotePort);
    disconnectClient();
    onClientConnected((uint32_t)remoteIp);
    // Finish old-owner cancellation before publishing the replacement socket.
    client = newClient;
  }

  _isConnected = client.connected();
  if (!_isConnected) onClientDisconnected();

}
