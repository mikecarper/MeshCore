// Complete production Companion driver + framing on a deterministic fake RTOS.
// Vendor waits block only their worker; the main-thread checks keep running.
#include <Arduino.h>
#include <helpers/ethernet/RAK13800/RAK13800EthernetInterface.h>
#include <helpers/MultiSerialInterface.h>
#include <helpers/nrf52/TerminalPeripheralShutdown.h>
#include <helpers/nrf52/Rak4631SharedSpi.h>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <functional>
#include <memory>
#include <string>
#include <thread>
#include <vector>

using Bytes = std::vector<uint8_t>;
static const auto main_thread = std::this_thread::get_id();
std::atomic<uint32_t> mock_millis{100};
MockFicr mock_ficr;
std::recursive_mutex mock_scheduler_mutex;
MockEthernet Ethernet;
MockW5100 W5100;
SPIClass SPI(NRF_SPIM3, 45, 43, 44), SPI1(NRF_SPIM2, 9, 10, 11);
static SPIClass* ethernet_spi = nullptr;
static std::atomic<bool> spi_active{false}, card_fitted{false}, defer_start{false};
static std::atomic<int> transactions{0}, hardware_ops{0}, spi_begins{0}, spi_ends{0};
static std::atomic<int> main_hardware_ops{0};
static std::atomic<int> live_tasks{0}, task_deletions{0};
static std::atomic<int> parked_tasks{0};
static std::atomic<int> pad_releases{0};
static std::atomic<bool> fail_create{false}, ack_send{true}, stuck_command{false};
static std::atomic<uint8_t> open_status{SnSR::INIT}, listen_status{SnSR::LISTEN}, close_status{SnSR::CLOSED};
static std::array<std::atomic<int>, 64> pin_modes{}, pin_values{};
static std::mutex library_mutex;
static std::condition_variable library_changed;
struct Socket {
  std::mutex mutex;
  std::atomic<bool> connected{false};
  std::atomic<int> capacity{2048}, closes{0};
  std::atomic<uint32_t> ip{0x1401a8c0};
  std::atomic<uint8_t> status{SnSR::CLOSED};
  Bytes input, output;
};
static Socket sockets[4];
static std::deque<uint8_t> accepted;
struct TaskDeleted {};
struct MockTask {
  std::thread thread;
  std::mutex mutex;
  std::condition_variable changed;
  unsigned notifications = 0;
  bool deleted = false;
};
static thread_local MockTask* current_task = nullptr;
static std::vector<std::unique_ptr<MockTask>> tasks;

static bool reserved() {
#if defined(OTA_RAK15002_SD_STORE)
  return mesh::ota::rak4631_ethernet_owns_spi();
#else
  return true; // Dedicated Ethernet legacy profiles have no shared-bus arbiter.
#endif
}
static void on_main() { assert(std::this_thread::get_id() == main_thread); }
static void worker_only() {
  assert(current_task != nullptr && "blocking vendor operation entered the mesh thread");
  assert(std::this_thread::get_id() != main_thread);
  assert(reserved() && spi_active.load());
  ++hardware_ops;
}
static void transfer() {
  assert(spi_active.load() && reserved());
  assert(transactions.load() == 1 && "register IO requires an exclusive SPI transaction");
  ++hardware_ops;
  if (!current_task) ++main_hardware_ops;
}
static void wait_until(const std::function<bool()>& predicate,
                       const std::function<void()>& pump = {}) {
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(4);
  while (!predicate()) {
    assert(std::chrono::steady_clock::now() < deadline && "worker did not reach expected state");
    if (pump) pump();
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
}
static void unblock(std::atomic<bool>& blocked) {
  std::lock_guard<std::mutex> lock(library_mutex);
  blocked.store(false);
  library_changed.notify_all();
}
void pinMode(int pin, int mode) {
  assert(pin >= 0 && pin < 64);
  if (pin == 26 && mode == OUTPUT) {
    assert(reserved());
    assert(pin_values[26].load() == HIGH && "CS must be preloaded before driving it");
  }
  if (mode == INPUT && pin != 10) { assert(!spi_active.load()); ++pad_releases; }
  pin_modes[pin].store(mode);
}
void digitalWrite(int pin, int value) { pin_values[pin].store(value); }
int digitalRead(int pin) { assert(pin == 10); return card_fitted.load() ? LOW : HIGH; }
void delay(uint32_t ticks) { vTaskDelay(ticks); }
void delayMicroseconds(uint32_t us) { assert(current_task && us == 250); }
void SPIClass::begin() {
  assert(current_task != nullptr && "SPI startup entered the mesh thread");
  assert(reserved() && !spi_active.exchange(true));
  assert(pin_modes[26].load() == OUTPUT && pin_values[26].load() == HIGH);
  ++spi_begins;
}
void SPIClass::end() {
  assert(reserved() && spi_active.exchange(false));
  assert(transactions.load() == 0);
  ++spi_ends;
}
void SPIClass::beginTransaction(const SPISettings&) {
  assert(spi_active.load() && transactions.fetch_add(1) == 0);
}
void SPIClass::endTransaction() { assert(transactions.fetch_sub(1) == 1); }
BaseType_t xTaskCreate(void (*fn)(void*), const char*, unsigned words,
                      void* argument, int priority, TaskHandle_t* handle) {
  on_main(); assert(words == 1024 && priority == 1);
  if (fail_create.load()) return 0;
  auto task = std::make_unique<MockTask>();
  MockTask* raw = task.get(); *handle = raw; ++live_tasks;
  raw->thread = std::thread([raw, fn, argument] {
    current_task = raw;
    try {
      { std::lock_guard<std::recursive_mutex> lock(mock_scheduler_mutex); }
      {
        std::unique_lock<std::mutex> lock(library_mutex);
        library_changed.wait(lock, [] { return !defer_start.load(); });
      }
      fn(argument);
      assert(false && "worker must park for synchronous main-thread reclamation");
    } catch (const TaskDeleted&) {}
  });
  tasks.push_back(std::move(task));
  return pdPASS;
}
void xTaskNotifyGive(TaskHandle_t task) {
  assert(task);
  std::lock_guard<std::mutex> lock(task->mutex);
  assert(!task->deleted && "notification used a deleted worker handle");
  ++task->notifications; task->changed.notify_all();
}
uint32_t ulTaskNotifyTake(int clear, uint32_t ticks) {
  assert(current_task && clear == pdTRUE);
  std::unique_lock<std::mutex> lock(current_task->mutex);
  const auto ready = [] { return current_task->notifications || current_task->deleted; };
  if (ticks == portMAX_DELAY) current_task->changed.wait(lock, ready);
  else current_task->changed.wait_for(lock, std::chrono::milliseconds(ticks), ready);
  if (current_task->deleted) throw TaskDeleted{};
  const auto count = current_task->notifications; current_task->notifications = 0;
  return count;
}
void vTaskDelay(uint32_t ticks) {
  if (!current_task) {
    mock_millis.fetch_add(ticks);
    std::this_thread::yield();
    return;
  }
  if (ticks == portMAX_DELAY) {
    ++parked_tasks;
    std::unique_lock<std::mutex> lock(current_task->mutex);
    current_task->changed.wait(lock, [] { return current_task->deleted; });
    --parked_tasks;
    throw TaskDeleted{};
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(ticks));
}
void vTaskDelete(TaskHandle_t task) {
  on_main(); assert(task && !spi_active.load());
  assert(reserved());
  {
    std::lock_guard<std::mutex> lock(task->mutex);
    assert(!task->deleted); task->deleted = true; task->changed.notify_all();
  }
  task->thread.join(); --live_tasks; ++task_deletions;
}
void MockEthernet::init(SPIClass& bus, int cs) { worker_only(); assert(cs == 26); ethernet_spi = &bus; }
int MockEthernet::begin(uint8_t* mac, unsigned long timeout, unsigned long response) {
  worker_only(); assert(mac[0] == 2 && mac[3] == 0x12 && mac[4] == 0x34 && mac[5] == 0x56);
  assert(timeout == 10000 && response == 2000);
  ++begin_count;
  std::unique_lock<std::mutex> lock(library_mutex);
  library_changed.wait(lock, [this] { return !begin_blocked.load(); });
  return begin_result.load();
}
int MockEthernet::maintain() {
  worker_only(); ++maintain_count;
  std::unique_lock<std::mutex> lock(library_mutex);
  library_changed.wait(lock, [this] { return !maintain_blocked.load(); });
  return maintain_result.load();
}
void MockEthernet::begin(uint8_t* mac, IPAddress ip, IPAddress dns, IPAddress gateway, IPAddress subnet) {
  worker_only(); assert(mac[0] == 2 && mac[5] == 0x56);
  assert(ip[0] == 192 && ip[3] == 42 && dns[0] == 8 && dns[3] == 8);
  assert(gateway[0] == 192 && gateway[3] == 1 && subnet[0] == 255 && subnet[3] == 0);
  ++begin_count;
  std::unique_lock<std::mutex> lock(library_mutex);
  library_changed.wait(lock, [this] { return !begin_blocked.load(); });
}
int MockEthernet::hardwareStatus() const { worker_only(); return hardware.load(); }
IPAddress MockEthernet::localIP() const { worker_only(); return IPAddress{}; }
bool EthernetClient::connected() const {
  worker_only(); return socket_ < 4 && sockets[socket_].connected.load();
}
IPAddress EthernetClient::remoteIP() const {
  worker_only(); assert(socket_ < 4); return IPAddress(sockets[socket_].ip.load());
}
void EthernetServer::begin() { worker_only(); ++Ethernet.listen_count; }
EthernetClient EthernetServer::accept() {
  worker_only(); ++Ethernet.accept_count;
  std::unique_lock<std::mutex> lock(library_mutex);
  library_changed.wait(lock, [] { return !Ethernet.accept_blocked.load(); });
  if (accepted.empty()) return {};
  const auto next = accepted.front(); accepted.pop_front();
  return EthernetClient(next);
}
SPIClass* MockW5100::getSPI() const { return ethernet_spi; }
uint8_t MockW5100::readSnSR(uint8_t s) const {
  transfer();
  if (sockets[s].connected) { sockets[s].status.store(SnSR::ESTABLISHED); return SnSR::ESTABLISHED; }
  const uint8_t status = sockets[s].status.load();
  return status == SnSR::ESTABLISHED ? uint8_t(SnSR::CLOSED) : status;
}
void MockW5100::readSnDIPR(uint8_t s, uint8_t* ip) {
  worker_only(); transfer(); const uint32_t value = sockets[s].ip.load();
  for (unsigned i = 0; i < 4; ++i) ip[i] = value >> (i * 8);
}
void MockW5100::writeSnMR(uint8_t, uint8_t protocol) { worker_only(); transfer(); assert(protocol == SnMR::TCP); }
void MockW5100::writeSnPORT(uint8_t, uint16_t port) { worker_only(); transfer(); assert(port == 5000); }
uint8_t MockW5100::readSnIR(uint8_t s) {
  transfer(); if (ack_send.load() && committed[s] == tx_wr[s]) interrupt[s] |= SnIR::SEND_OK;
  return interrupt[s];
}
uint8_t MockW5100::readSnCR(uint8_t s) {
  transfer();
  if (!stuck_command.load() && (!(command[s] == Sock_OPEN || command[s] == Sock_LISTEN)
                              || !Ethernet.accept_blocked.load())) {
    if (command[s] == Sock_OPEN) sockets[s].status.store(open_status.load());
    if (command[s] == Sock_LISTEN) sockets[s].status.store(listen_status.load());
    command[s] = 0;
  }
  return command[s];
}
uint16_t MockW5100::readSnTX_FSR(uint8_t s) const { transfer(); return sockets[s].capacity.load(); }
uint16_t MockW5100::readSnTX_WR(uint8_t s) const { transfer(); return tx_wr[s]; }
uint16_t MockW5100::readSnRX_RSR(uint8_t s) const {
  transfer(); std::lock_guard<std::mutex> lock(sockets[s].mutex); return sockets[s].input.size();
}
uint16_t MockW5100::readSnRX_RD(uint8_t s) const { transfer(); return rx_rd[s]; }
void MockW5100::writeSnRX_RD(uint8_t s, uint16_t n) { transfer(); rx_rd[s] = n; }
void MockW5100::writeSnTX_WR(uint8_t s, uint16_t n) { transfer(); tx_wr[s] = n; }
void MockW5100::writeSnIR(uint8_t s, uint8_t n) { transfer(); interrupt[s] &= ~n; }
void MockW5100::writeSnCR(uint8_t s, uint8_t n) {
  transfer(); assert(command[s] == 0 && "a pending controller command was overwritten");
  if (n == Sock_CLOSE) {
    ++sockets[s].closes; sockets[s].connected.store(false); sockets[s].status.store(close_status.load());
  } else if (n == Sock_OPEN || n == Sock_LISTEN) {
    worker_only();
    if (n == Sock_OPEN) ++Ethernet.accept_count;
    else ++Ethernet.listen_count;
    command[s] = n;
    return;
  } else if (n == Sock_RECV) {
    std::lock_guard<std::mutex> lock(sockets[s].mutex);
    assert(!sockets[s].input.empty()); sockets[s].input.erase(sockets[s].input.begin());
  } else {
    assert(n == Sock_SEND);
    std::lock_guard<std::mutex> lock(sockets[s].mutex);
    for (uint16_t p = committed[s]; p != tx_wr[s]; ++p) sockets[s].output.push_back(memory[s][p & SMASK]);
    committed[s] = tx_wr[s]; interrupt[s] = ack_send.load() ? SnIR::SEND_OK : 0;
  }
  command[s] = stuck_command.load() ? n : 0;
}
uint16_t MockW5100::write(uint16_t address, const uint8_t* bytes, uint16_t count) {
  transfer(); const auto relative = address - 0x4000;
  const auto s = relative / SSIZE, offset = relative % SSIZE;
  assert(s < 4 && offset + count <= SSIZE); memcpy(memory[s].data() + offset, bytes, count); return count;
}
uint16_t MockW5100::read(uint16_t address, uint8_t* bytes, uint16_t count) {
  transfer(); const auto s = (address - 0x6000) / SSIZE;
  std::lock_guard<std::mutex> lock(sockets[s].mutex);
  assert(s < 4 && count == 1 && !sockets[s].input.empty()); bytes[0] = sockets[s].input.front(); return count;
}

static constexpr uint32_t A = 0x1401a8c0, B = 0x1e01a8c0;
static Bytes request(const Bytes& bytes) {
  Bytes result{'<', uint8_t(bytes.size()), uint8_t(bytes.size() >> 8)};
  result.insert(result.end(), bytes.begin(), bytes.end()); return result;
}
static Bytes response(const Bytes& bytes) {
  auto result = request(bytes); result[0] = '>'; return result;
}
static void feed(uint8_t s, const Bytes& bytes) {
  std::lock_guard<std::mutex> lock(sockets[s].mutex);
  sockets[s].input.insert(sockets[s].input.end(), bytes.begin(), bytes.end());
}
static Bytes output(uint8_t s) { std::lock_guard<std::mutex> lock(sockets[s].mutex); return sockets[s].output; }
static bool other_owner(void*, uint32_t) { return true; }
struct Fixture {
  RAK13800EthernetInterface owner;
  MultiSerialInterface routes;
  uint8_t received[MAX_FRAME_SIZE] = {};
  bool prepare_allowed = true, off_allowed = true, release_allowed = true, pending = false;
  unsigned prepares = 0, releases = 0, callbacks = 0, polls = 0;
  explicit Fixture(bool hooks = true) {
#if defined(OTA_RAK15002_SD_STORE)
    if (hooks) {
      owner.setSpiOwnershipHooks([](void* p) {
        on_main(); auto& f = *static_cast<Fixture*>(p); ++f.prepares;
        return f.prepare_allowed;
      }, [](void* p) { on_main(); return static_cast<Fixture*>(p)->off_allowed; }, [](void* p) {
        on_main(); auto& f = *static_cast<Fixture*>(p); ++f.releases;
        assert(!spi_active.load() && !live_tasks.load()); return f.release_allowed;
      }, this);
    }
#else
    (void)hooks;
#endif
    owner.setSessionChangedCallback([](void* p) {
      on_main(); auto& f = *static_cast<Fixture*>(p);
      assert(!f.owner.isConnected()); ++f.callbacks; f.pending = false;
      f.routes.forgetReplyRouteForDisconnected(&f.owner);
    }, this);
    assert(owner.begin()); assert(routes.addInterface(InterfaceType::Ethernet, &owner)); routes.enable();
#if defined(OTA_RAK15002_SD_STORE)
    assert(!owner.hardwareActive() && !owner.isConnected() && !spi_active && !live_tasks);
#else
    assert(owner.hardwareActive() && live_tasks == 1); // Legacy begins automatically.
#endif
  }
  size_t tick(uint32_t elapsed = 1) {
    mock_millis.fetch_add(elapsed); ++polls; owner.loop(); return routes.checkRecvFrame(received);
  }
  void pump(unsigned count) { while (count--) tick(); }
  void online() {
    assert(owner.tryEnable());
    wait_until([&] { return std::strcmp(owner.status(), "online") == 0; }, [&] { tick(); });
  }
  void connect(uint8_t s = 0, uint32_t ip = A) {
    wait_until([&] { return sockets[s].status == SnSR::LISTEN; }, [&] { tick(); });
    sockets[s].connected.store(true); sockets[s].ip.store(ip);
    { std::lock_guard<std::mutex> lock(library_mutex); accepted.push_back(s); }
    wait_until([&] { return owner.isConnected(); }, [&] { tick(); });
  }
  void replace(uint8_t s, uint32_t ip) {
    wait_until([&] { return sockets[s].status == SnSR::LISTEN; }, [&] { tick(); });
    const auto before = callbacks;
    sockets[s].connected.store(true); sockets[s].ip.store(ip);
    { std::lock_guard<std::mutex> lock(library_mutex); accepted.push_back(s); }
    wait_until([&] { return ip == A ? !output(s).empty() : callbacks > before; }, [&] { tick(); });
    pump(10);
  }
  void off() {
    assert(owner.tryDisable());
    wait_until([&] {
      bool released = live_tasks.load() == 0 && !spi_active.load();
#if defined(OTA_RAK15002_SD_STORE)
      released = released && !mesh::ota::rak4631_ethernet_owns_spi();
#endif
      return released;
    }, [&] { tick(); });
    assert(pin_modes[26].load() == INPUT);
  }
  void drain(uint8_t s, const Bytes& expected) {
    wait_until([&] { return output(s) == expected; }, [&] { tick(); });
    pump(5);
  }
};

int main(int argc, char** argv) {
  assert(argc == 2); const auto scenario = std::atoi(argv[1]);
  if (scenario == 22 || scenario == 23) {
    if (scenario == 23) assert(mesh::nrf52::registerTerminalEthernetShutdown(other_owner, nullptr));
    RAK13800EthernetInterface owner; assert(!owner.begin());
    assert(!spi_begins && !spi_ends && !pad_releases && !live_tasks);
    std::printf("PASS: RAK Companion Ethernet worker scenario %d\n", scenario); return 0;
  }
  if (scenario == 20) Ethernet.begin_blocked.store(true);
  Fixture f(scenario != 21);
  const Bytes reply{1, 0x21, 0x22, 0x23};
  switch (scenario) {
#if defined(OTA_RAK15002_SD_STORE)
    case 0: { // Admission and task-allocation failures cannot touch SPI.
      card_fitted.store(true); assert(!f.owner.tryEnable() && !f.prepares);
      card_fitted.store(false); f.prepare_allowed = false; assert(!f.owner.tryEnable());
      f.prepare_allowed = true;
      assert(mesh::ota::rak4631_try_acquire_spi(mesh::ota::RakSharedSpiOwner::Sd));
      assert(!f.owner.tryEnable()); mesh::ota::rak4631_release_spi(mesh::ota::RakSharedSpiOwner::Sd);
      fail_create.store(true); assert(!f.owner.tryEnable());
      assert(!spi_begins && !live_tasks && !mesh::ota::rak4631_ethernet_owns_spi());
      break;
    }
    case 1: { // Slow startup never blocks the main loop, and OFF waits for its entered call.
      Ethernet.begin_blocked.store(true); assert(f.owner.tryEnable());
      wait_until([] { return Ethernet.begin_count.load() == 1; });
      const auto ops = hardware_ops.load(); f.pump(1000);
      assert(f.polls == 1000 && hardware_ops.load() == ops && !f.owner.isConnected());
      assert(f.owner.tryDisable()); f.pump(100);
      assert(live_tasks == 1 && spi_active && mesh::ota::rak4631_ethernet_owns_spi());
      unblock(Ethernet.begin_blocked); f.off();
      assert(f.releases == 1 && task_deletions == 1); break;
    }
    case 2: { // OFF before worker scheduling performs no hardware initialization.
      defer_start.store(true); assert(f.owner.tryEnable()); assert(f.owner.tryDisable());
      unblock(defer_start); f.off(); assert(spi_begins == 0 && Ethernet.begin_count == 0); break;
    }
    case 3: { // Production framing retains partial writes and delivers a command.
      f.online(); f.connect(); sockets[0].capacity.store(2);
      assert(f.owner.writeFrame(reply.data(), reply.size()) == reply.size()); f.tick();
      assert(output(0) == Bytes({'>', uint8_t(reply.size())}));
      sockets[0].capacity.store(0); f.pump(5); assert(output(0).size() == 2);
      sockets[0].capacity.store(2048); f.drain(0, response(reply));
      feed(0, request(Bytes{4, 5}));
      wait_until([&] { return f.tick() == 2; }); assert(f.received[0] == 4 && f.received[1] == 5);
      assert(f.routes.captureReplyRoute() == &f.owner); f.off(); break;
    }
    case 4: { // Renewal is backpressure: no false disconnect, no leaked reply, no parser timeout.
      f.online(); f.connect(); feed(0, request(Bytes{4}));
      wait_until([&] { return f.tick() == 1; }); f.routes.lockReplyRoute(); f.pending = true;
      feed(0, Bytes{'<', 2, 0, 0x41}); f.tick(); assert(f.owner.isReadBusy());
      Ethernet.maintain_blocked.store(true); const auto before = Ethernet.maintain_count.load();
      f.tick(1001); wait_until([&] { return Ethernet.maintain_count.load() > before; }, [&] { f.tick(); });
      const auto ops = hardware_ops.load(); f.pump(10000);
      assert(f.owner.isConnected() && f.routes.isReplyRouteAvailable(&f.owner));
      assert(!f.callbacks && f.pending && hardware_ops.load() == ops);
      assert(f.routes.writeFrame(reply.data(), reply.size()) == reply.size());
      assert(output(0).empty()); unblock(Ethernet.maintain_blocked);
      f.drain(0, response(reply)); feed(0, Bytes{0x42});
      wait_until([&] { return f.tick() == 2; });
      assert(f.received[0] == 0x41 && f.received[1] == 0x42 && !f.callbacks); f.off(); break;
    }
    case 5: { // Bounded listener transitions preserve the old session until their fault deadline.
      f.online(); f.connect();
      wait_until([] { return sockets[1].status == SnSR::LISTEN; }, [&] { f.tick(); });
      Ethernet.accept_blocked.store(true); sockets[1].connected.store(true); sockets[1].ip.store(A);
      const auto before = Ethernet.accept_count.load();
      wait_until([&] { return Ethernet.accept_count.load() > before; }, [&] { f.tick(); });
      const auto ops = main_hardware_ops.load();
      for (unsigned i = 0; i < 10000; ++i) f.tick(0);
      assert(f.owner.isConnected() && !f.callbacks && main_hardware_ops.load() == ops);
      f.tick(251); wait_until([&] { return !f.owner.isConnected(); }, [&] { f.tick(); });
      assert(f.callbacks == 1); unblock(Ethernet.accept_blocked); f.off(); break;
    }
    case 6: case 7: { // Preserve same-IP replay, but cancel different-IP reply ownership first.
      f.online(); f.connect(); feed(0, request(Bytes{4}));
      wait_until([&] { return f.tick() == 1; }); f.routes.lockReplyRoute(); f.pending = true;
      sockets[0].capacity.store(2); assert(f.owner.writeFrame(reply.data(), reply.size()) == reply.size()); f.tick();
      f.replace(1, scenario == 6 ? A : B);
      assert(f.callbacks == unsigned(scenario == 7) && f.pending == (scenario == 6));
      f.drain(1, scenario == 6 ? response(reply) : Bytes{});
      if (scenario == 7) assert(f.routes.captureReplyRoute() == nullptr);
      f.off(); break;
    }
    case 8: case 9: { // Missing ACK and stuck SEND cannot hang radio/USB service.
      f.online(); f.connect(); ack_send.store(false); stuck_command.store(scenario == 9);
      assert(f.owner.writeFrame(reply.data(), reply.size()) == reply.size()); f.tick();
      f.pending = true; f.pump(300);
      wait_until([&] { return !f.owner.isConnected(); }, [&] { f.tick(); });
      assert(f.callbacks == 1 && !f.pending); stuck_command.store(false); f.off(); break;
    }
    case 10: { // A stuck RECV command defers consumption and faults with a finite deadline.
      f.online(); f.connect(); stuck_command.store(true); feed(0, Bytes{'<'});
      f.tick(); f.pump(300);
      wait_until([&] { return !f.owner.isConnected(); }, [&] { f.tick(); });
      assert(f.callbacks == 1); stuck_command.store(false); f.off(); break;
    }
    case 11: { // SD insertion during an entered DHCP call permits no new vendor operation.
      Ethernet.begin_blocked.store(true); assert(f.owner.tryEnable());
      wait_until([] { return Ethernet.begin_count == 1; }); card_fitted.store(true); f.pump(5);
      unblock(Ethernet.begin_blocked);
      wait_until([] { return !spi_active.load(); }, [&] { f.tick(); });
      assert(mesh::ota::rak4631_ethernet_owns_spi() && live_tasks == 1);
      assert(!Ethernet.listen_count && !Ethernet.accept_count && !Ethernet.maintain_count);
      f.off(); assert(!f.owner.tryEnable()); card_fitted.store(false); f.online(); f.off(); break;
    }
    case 12: { // Online insertion revokes the session, retains its claim, and gates release.
      f.online(); f.connect(); f.pending = true; card_fitted.store(true); f.tick();
      wait_until([] { return !spi_active.load(); }, [&] { f.tick(); });
      assert(!f.owner.isConnected() && f.callbacks == 1 && !f.pending);
      f.off_allowed = false; assert(!f.owner.tryDisable());
      assert(mesh::ota::rak4631_ethernet_owns_spi());
      f.off_allowed = true; f.off(); card_fitted.store(false); f.online(); f.off(); break;
    }
    case 13: { // Repeated acquisitions restore CS and reclaim each worker synchronously.
      for (unsigned i = 0; i < 5; ++i) { f.online(); f.off(); assert(task_deletions == int(i + 1)); }
      assert(spi_begins == 5 && spi_ends == 5 && f.releases == 5); break;
    }
    case 14: { // Terminal timeout never deletes a worker inside a vendor call.
      Ethernet.begin_blocked.store(true); assert(f.owner.tryEnable());
      wait_until([] { return Ethernet.begin_count == 1; });
      assert(!mesh::nrf52::terminalEthernetShutdown(0));
      assert(live_tasks == 1 && spi_active && !task_deletions);
      unblock(Ethernet.begin_blocked);
      wait_until([&] { return mesh::nrf52::terminalEthernetShutdown(0); }, [&] { f.tick(); });
      assert(!live_tasks && !spi_active && !mesh::ota::rak4631_ethernet_owns_spi()); break;
    }
    case 15: { // OTA release races keep ownership until the main-thread hook succeeds.
      f.online(); f.release_allowed = false; assert(f.owner.tryDisable());
      wait_until([] { return live_tasks == 0 && !spi_active.load(); }, [&] { f.tick(); });
      assert(mesh::ota::rak4631_ethernet_owns_spi());
      f.release_allowed = true; f.off(); break;
    }
    case 16: { // Missing hardware stops cleanly without polling a non-existent controller.
      Ethernet.hardware.store(EthernetNoHardware); Ethernet.begin_result.store(0);
      assert(f.owner.tryEnable());
      wait_until([&] { return std::strcmp(f.owner.status(), "no hardware") == 0 && !spi_active; }, [&] { f.tick(); });
      const auto calls = Ethernet.begin_count.load(); f.pump(1000);
      assert(calls == 1 && Ethernet.begin_count == calls && !f.owner.isConnected()); f.off(); break;
    }
    case 17: { // OFF wakes the 30-second DHCP retry without waiting for its timer.
      Ethernet.begin_result.store(0); assert(f.owner.tryEnable());
      wait_until([&] { return std::strcmp(f.owner.status(), "retrying") == 0; }, [&] { f.tick(); });
      f.off(); assert(Ethernet.begin_count == 1); break;
    }
    case 18: { // An occupied OTA owner rejects OFF without cancelling the connected session.
      f.online(); f.connect(); f.off_allowed = false; f.pending = true;
      assert(!f.owner.tryDisable()); f.pump(50);
      assert(f.owner.isConnected() && !f.callbacks && f.pending && spi_active && live_tasks == 1);
      f.off_allowed = true; f.off(); break;
    }
    case 19: { // A failed ACK retains the current complete frame for same-IP reconnect.
      f.online(); f.connect(); ack_send.store(false);
      assert(f.owner.writeFrame(reply.data(), reply.size()) == reply.size()); f.tick();
      wait_until([&] { return output(0) == response(reply); }, [&] { f.tick(); });
      f.pump(300);
      wait_until([&] { return !f.owner.isConnected(); }, [&] { f.tick(); });
      assert(f.callbacks == 1); ack_send.store(true); f.connect(1, A);
      f.drain(1, response(reply)); f.pump(30); assert(output(1) == response(reply)); f.off(); break;
    }
    case 21: { // Missing safety hooks must refuse startup before driving shared pads.
      assert(!f.owner.tryEnable() && !f.prepares && !spi_begins && !live_tasks);
      assert(!mesh::ota::rak4631_ethernet_owns_spi() && pin_modes[26] == INPUT); break;
    }
    case 24: { // Never-started terminal shutdown leaves optional storage pins untouched.
      assert(mesh::nrf52::terminalEthernetShutdown(0));
      assert(!spi_begins && !spi_ends && !pad_releases && !live_tasks && !f.releases);
      assert(mesh::nrf52::terminalEthernetShutdown(0) && !f.owner.tryEnable()); break;
    }
    case 25: { // Online terminal shutdown reclaims once, releases pads, and cancels once.
      f.online(); f.connect(); f.pending = true;
      wait_until([&] { return mesh::nrf52::terminalEthernetShutdown(0); }, [&] { f.tick(); });
      assert(f.callbacks == 1 && !f.pending && pad_releases == 5 && task_deletions == 1);
      assert(f.releases == 1 && !live_tasks && !spi_active && !reserved());
      assert(mesh::nrf52::terminalEthernetShutdown(0));
      assert(pad_releases == 5 && task_deletions == 1 && f.releases == 1); break;
    }
    case 26: { // A refused OTA handoff remains safe after terminal hardware shutdown.
      f.online(); f.release_allowed = false;
      assert(!mesh::nrf52::terminalEthernetShutdown(0));
      wait_until([] { return !spi_active.load() && !live_tasks; }, [&] { (void)mesh::nrf52::terminalEthernetShutdown(0); });
      assert(!mesh::nrf52::terminalEthernetShutdown(0));
      assert(reserved() && !live_tasks && pad_releases == 5);
      f.release_allowed = true; assert(mesh::nrf52::terminalEthernetShutdown(0) && !reserved()); break;
    }
    case 27: { // The shutdown deadline is bounded across millis rollover.
      Ethernet.begin_blocked.store(true); assert(f.owner.tryEnable());
      wait_until([] { return Ethernet.begin_count == 1; }); mock_millis.store(UINT32_MAX - 3);
      assert(!mesh::nrf52::terminalEthernetShutdown(5));
      assert(mock_millis < 5 && live_tasks == 1 && spi_active && !task_deletions);
      unblock(Ethernet.begin_blocked);
      wait_until([&] { return mesh::nrf52::terminalEthernetShutdown(0); }, [&] { f.tick(); }); break;
    }
    case 28: case 30: { // A cleared command register is not proof of the requested state transition.
      if (scenario == 28) open_status.store(SnSR::CLOSED);
      else listen_status.store(SnSR::INIT);
      f.online(); f.pump(300);
      wait_until([&] { return std::strcmp(f.owner.status(), "faulted") == 0
          || Ethernet.accept_count > 1 || Ethernet.listen_count > 1; }, [&] { f.tick(); });
      assert(std::strcmp(f.owner.status(), "faulted") == 0 && "wrong listener status must fault once");
      assert(Ethernet.accept_count == 1 && Ethernet.listen_count == int(scenario == 30));
      assert(!f.owner.isConnected()); f.off(); break;
    }
    case 29: { // A fast SYNRECV after LISTEN is healthy, not a missed LISTEN response.
      listen_status.store(SnSR::SYNRECV); f.online();
      wait_until([] { return sockets[0].status == SnSR::SYNRECV; }, [&] { f.tick(); });
      f.pump(300); assert(std::strcmp(f.owner.status(), "online") == 0);
      assert(Ethernet.accept_count == 1 && Ethernet.listen_count == 1);
      sockets[0].connected.store(true); sockets[0].ip.store(A);
      wait_until([&] { return f.owner.isConnected(); }, [&] { f.tick(); });
      assert(f.owner.writeFrame(reply.data(), reply.size()) == reply.size());
      f.drain(0, response(reply)); f.off(); break;
    }
    case 31: case 32: { // Reclaim one closing socket, never a DHCP socket or live peer.
      sockets[0].status.store(SnSR::UDP);
      sockets[1].connected.store(true); sockets[1].status.store(SnSR::ESTABLISHED);
      sockets[2].status.store(SnSR::SYNRECV); sockets[3].status.store(SnSR::FIN_WAIT);
      if (scenario == 32) close_status.store(SnSR::TIME_WAIT);
      f.online();
      if (scenario == 31) {
        f.connect(3); assert(sockets[3].closes == 1 && Ethernet.accept_count == 1);
        assert(sockets[0].closes == 0 && sockets[1].closes == 0 && sockets[2].closes == 0);
        assert(f.owner.writeFrame(reply.data(), reply.size()) == reply.size()); f.drain(3, response(reply));
      } else {
        f.pump(300);
        wait_until([&] { return std::strcmp(f.owner.status(), "faulted") == 0; }, [&] { f.tick(); });
        // One fallback CLOSE, plus at most the normal terminal cleanup CLOSE.
        assert(sockets[3].closes >= 1 && sockets[3].closes <= 2 && Ethernet.accept_count == 0);
        close_status.store(SnSR::CLOSED);
      }
      f.off(); break;
    }
    case 33: { // Direct restart must cancel an unobserved stopped-fault session before clearing its event.
      f.online(); f.connect(); feed(0, request(Bytes{4}));
      wait_until([&] { return f.tick() == 1; }); f.routes.lockReplyRoute(); f.pending = true;
      stuck_command.store(true); ack_send.store(false);
      assert(f.owner.writeFrame(reply.data(), reply.size()) == reply.size());
      wait_until([&] { return output(0) == response(reply); }, [&] { f.tick(); });
      mock_millis.fetch_add(300); xTaskNotifyGive(tasks.back().get());
      wait_until([] { return parked_tasks == 1; }); // Deliberately no driver.loop().
      assert(f.callbacks == 0 && f.pending && f.owner.isConnected());
      stuck_command.store(false); ack_send.store(true); assert(f.owner.tryEnable());
      assert(f.callbacks == 1 && !f.pending && !f.owner.isConnected());
      assert(f.routes.captureReplyRoute() == nullptr && task_deletions == 1);
      f.online(); f.off(); break;
    }
#if defined(MESH_TEST_PRIVATE_ACCESS)
    case 34: { // Hold the real worker before entry and reproduce atomic-publication gaps deterministically.
      defer_start.store(true); assert(f.owner.tryEnable());
      const auto prepares = f.prepares;
      f.owner._state.store(RAK13800EthernetInterface::State::Faulted);
      assert(!f.owner.tryEnable() && f.prepares == prepares && live_tasks == 1);
      f.owner._state.store(RAK13800EthernetInterface::State::Online); f.owner._wanted.store(false);
      assert(!f.owner.tryEnable() && f.prepares == prepares && live_tasks == 1);
      unblock(defer_start); f.off(); assert(!spi_begins); break;
    }
#endif
    case 35: case 36: case 37: { // A SEND awaiting ACK must pin its frame before send_offset advances.
      const Bytes head{0x80, 0x41, 0x42}, best_effort{0x81, 0x51}, required{0x82, 0x61}, second{2, 0x31};
      f.online(); f.connect(); ack_send.store(false);
      assert(f.owner.writeFrame(head.data(), head.size()) == head.size());
      wait_until([&] { return output(0) == response(head); }, [&] { f.tick(); });
      if (scenario == 37) {
        ack_send.store(true);
        wait_until([&] { f.owner.loop(); return !f.owner.isWriteBusy(); }, [&] { mock_millis.fetch_add(1); });
        // No base checkRecvFrame(): ACK has arrived, but the head's credit is not retired.
      }
      assert(f.owner.writeFrame(best_effort.data(), best_effort.size()) == best_effort.size());
      assert(f.owner.writeFrame(required.data(), required.size()) == required.size());
      assert(f.owner.writeFrame(reply.data(), reply.size()) == reply.size());
      assert(f.owner.writeFrame(second.data(), second.size()) == second.size());
      assert(f.owner.writeFrame(required.data(), required.size()) == 0); // Never evict the pinned head.
      Bytes expected;
      for (const auto& bytes : {head, reply, second, required}) {
        const auto frame = response(bytes); expected.insert(expected.end(), frame.begin(), frame.end());
      }
      if (scenario == 36) {
        mock_millis.fetch_add(300); xTaskNotifyGive(tasks.back().get());
        wait_until([&] { return !f.owner.isConnected(); }, [&] { f.tick(); });
        assert(f.callbacks == 1); ack_send.store(true); f.connect(1, A); f.drain(1, expected);
      } else {
        ack_send.store(true); f.drain(0, expected);
      }
      f.off(); break;
    }
#else
    case 20: { // Legacy boot automatically starts the worker, never requiring SD hooks.
      wait_until([] { return Ethernet.begin_count.load() == 1; });
      const auto ops = hardware_ops.load(); f.pump(1000);
      assert(f.polls == 1000 && hardware_ops.load() == ops && !f.owner.isConnected());
      unblock(Ethernet.begin_blocked); f.online(); f.connect();
      feed(0, request(Bytes{4, 5})); wait_until([&] { return f.tick() == 2; });
      assert(f.received[0] == 4 && f.received[1] == 5 && f.routes.captureReplyRoute() == &f.owner);
      assert(f.owner.writeFrame(reply.data(), reply.size()) == reply.size());
      f.drain(0, response(reply)); f.off(); assert(f.prepares == 0 && f.releases == 0); break;
    }
#endif
    default: assert(false);
  }
  for (auto& task : tasks) assert(!task->thread.joinable());
  assert(live_tasks == 0 && !spi_active.load());
  std::printf("PASS: RAK Companion Ethernet worker scenario %d\n", scenario);
}
