#pragma once
#include <array>
#include <atomic>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>

class Print {
public:
  virtual ~Print() = default;
  virtual size_t write(uint8_t) = 0;
  virtual size_t write(const uint8_t* bytes, size_t count) {
    size_t used = 0;
    while (used < count && write(bytes[used])) ++used;
    return used;
  }
};
class Stream : public Print {
public:
  virtual int available() = 0;
  virtual int read() = 0;
  virtual int peek() = 0;
  virtual void flush() = 0;
  virtual int availableForWrite() { return 0; }
};

constexpr int INPUT = 0, OUTPUT = 1, INPUT_PULLUP = 2, LOW = 0, HIGH = 1;
constexpr int NRF_SPIM1 = 1, NRF_SPIM2 = 2, NRF_SPIM3 = 3;
extern std::atomic<uint32_t> mock_millis;
inline uint32_t millis() { return mock_millis.load(); }
void delay(uint32_t);
void delayMicroseconds(uint32_t);
void pinMode(int, int);
void digitalWrite(int, int);
int digitalRead(int);
struct MockFicr { uint32_t DEVICEID[2] = {0x123456, 0}; };
extern MockFicr mock_ficr;
#define NRF_FICR (&mock_ficr)

struct MockTask;
using TaskHandle_t = MockTask*;
using BaseType_t = int;
struct StaticTask_t { uint8_t bytes[192]; };
constexpr int pdPASS = 1, pdTRUE = 1, pdFALSE = 0;
constexpr uint32_t portMAX_DELAY = UINT32_MAX;
inline uint32_t pdMS_TO_TICKS(uint32_t value) { return value; }
extern std::recursive_mutex mock_scheduler_mutex;
#define taskENTER_CRITICAL() mock_scheduler_mutex.lock()
#define taskEXIT_CRITICAL() mock_scheduler_mutex.unlock()
BaseType_t xTaskCreate(void (*)(void*), const char*, unsigned, void*, int, TaskHandle_t*);
void xTaskNotifyGive(TaskHandle_t);
uint32_t ulTaskNotifyTake(int, uint32_t);
void vTaskDelay(uint32_t);
void vTaskDelete(TaskHandle_t);

struct SPISettings {};
#define SPI_ETHERNET_SETTINGS SPISettings{}
class SPIClass {
public:
  SPIClass(int, int, int, int) {}
  void begin();
  void end();
  void beginTransaction(const SPISettings&);
  void endTransaction();
};
extern SPIClass SPI, SPI1;

class IPAddress {
  uint32_t value_;
public:
  explicit IPAddress(uint32_t value = 0x2a01a8c0) : value_(value) {}
  IPAddress(uint8_t a, uint8_t b, uint8_t c, uint8_t d)
      : value_(uint32_t(a) | (uint32_t(b) << 8) | (uint32_t(c) << 16) | (uint32_t(d) << 24)) {}
  uint8_t operator[](size_t offset) const { return (value_ >> (8 * offset)) & 255; }
  explicit operator uint32_t() const { return value_; }
};
class EthernetClient {
  uint8_t socket_ = 255;
public:
  EthernetClient() = default;
  explicit EthernetClient(uint8_t value) : socket_(value) {}
  explicit operator bool() const { return socket_ < 4; }
  uint8_t getSocketNumber() const { return socket_; }
  bool connected() const;
  IPAddress remoteIP() const;
  uint16_t remotePort() const { return 12345; }
  void stop() { assert(false && "blocking vendor stop must not run"); }
  int available() { assert(false && "vendor receive must not run on main"); return 0; }
  int read() { assert(false && "vendor receive must not run on main"); return -1; }
  size_t write(const uint8_t*, size_t) {
    assert(false && "blocking vendor write must not run"); return 0;
  }
};
class EthernetServer {
public:
  explicit EthernetServer(int port) { assert(port == 5000); }
  void begin();
  EthernetClient accept();
};
enum { EthernetNoHardware = 0, EthernetPresent = 1, LinkOFF = 0, LinkON = 1 };
struct MockEthernet {
  std::atomic<int> begin_count{0}, maintain_count{0}, accept_count{0}, listen_count{0};
  std::atomic<int> begin_result{1}, maintain_result{0}, hardware{EthernetPresent};
  std::atomic<bool> begin_blocked{false}, maintain_blocked{false}, accept_blocked{false};
  void init(SPIClass&, int);
  int begin(uint8_t*, unsigned long = 60000, unsigned long = 4000);
  void begin(uint8_t*, IPAddress, IPAddress, IPAddress, IPAddress);
  int maintain();
  int hardwareStatus() const;
  int linkStatus() const { return LinkON; }
  IPAddress localIP() const;
};
extern MockEthernet Ethernet;

enum SockCMD { Sock_OPEN = 1, Sock_LISTEN = 2, Sock_CLOSE = 0x10, Sock_SEND = 0x20, Sock_RECV = 0x40 };
struct SnMR { enum { TCP = 1 }; };
struct SnSR { enum { CLOSED = 0, INIT = 0x13, LISTEN = 0x14, SYNSENT = 0x15,
  SYNRECV = 0x16, ESTABLISHED = 0x17, FIN_WAIT = 0x18, CLOSING = 0x1a,
  TIME_WAIT = 0x1b, CLOSE_WAIT = 0x1c, LAST_ACK = 0x1d, UDP = 0x22 }; };
struct SnIR { enum { SEND_OK = 0x10, TIMEOUT = 8 }; };
class MockW5100 {
public:
  static constexpr uint16_t SSIZE = 2048, SMASK = SSIZE - 1;
  std::array<std::array<uint8_t, SSIZE>, 4> memory{};
  uint16_t tx_wr[4] = {}, committed[4] = {}, rx_rd[4] = {};
  uint8_t interrupt[4] = {}, command[4] = {};
  uint8_t getChip() const { return 51; }
  SPIClass* getSPI() const;
  uint16_t SBASE(uint8_t socket) const { return 0x4000 + socket * SSIZE; }
  uint16_t RBASE(uint8_t socket) const { return 0x6000 + socket * SSIZE; }
  uint8_t readSnSR(uint8_t) const;
  void readSnDIPR(uint8_t, uint8_t*);
  void writeSnMR(uint8_t, uint8_t);
  void writeSnPORT(uint8_t, uint16_t);
  uint8_t readSnIR(uint8_t);
  uint8_t readSnCR(uint8_t);
  uint16_t readSnTX_FSR(uint8_t) const;
  uint16_t readSnTX_WR(uint8_t) const;
  uint16_t readSnRX_RSR(uint8_t) const;
  uint16_t readSnRX_RD(uint8_t) const;
  void writeSnRX_RD(uint8_t, uint16_t);
  void writeSnTX_WR(uint8_t, uint16_t);
  void writeSnIR(uint8_t, uint8_t);
  void writeSnCR(uint8_t, uint8_t);
  uint16_t read(uint16_t, uint8_t*, uint16_t);
  uint16_t write(uint16_t, const uint8_t*, uint16_t);
  static void execCmdSn(uint8_t, SockCMD); // Intentionally undefined.
};
extern MockW5100 W5100;
