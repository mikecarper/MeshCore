// Runs the actual opt-in production Ethernet helper on a fake RTOS and SPI
// device. Threads deliberately hold DHCP calls while the mesh loop continues.
#include <Arduino.h>
#include <RAK13800_W5100S.h>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <memory>
#include <thread>
#include <vector>

std::atomic<uint32_t> mock_millis{0};
MockFicr mock_ficr;
std::recursive_mutex mock_scheduler_mutex;
std::atomic<bool> mock_socket_connected[4];
std::atomic<int> mock_stop_count[4];
std::atomic<bool> mock_accept_client{false};
std::string mock_ethernet_input;
std::string mock_ethernet_output;
std::atomic<int> mock_write_capacity{2048};
MockEthernet Ethernet;
SPIClass* mock_w5100_spi = nullptr;
std::atomic<bool> mock_ack_send{true};
std::atomic<bool> mock_stuck_command{false};
std::atomic<bool> mock_hold_terminal_recv{false};
std::atomic<bool> mock_replace_during_parser{false};
static std::atomic<int> worker_access_waits{0};
std::mutex mock_output_mutex;
static const std::thread::id main_thread = std::this_thread::get_id();
static std::atomic<bool> bus_owner{false};
static std::atomic<bool> spi_active{false};
static std::atomic<int> hardware_operations{0};
static std::atomic<int> main_hardware_operations{0};
static std::atomic<int> live_task_allocations{0};
static std::atomic<int> task_deletions{0};
static bool fail_task_creation = false;
static std::atomic<bool> defer_task_start{false};
static std::mutex library_mutex;
static std::condition_variable library_changed;

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

#include <helpers/nrf52/EthernetCLI.h>
MockW5100 W5100;

static void wait_until(const std::function<bool()>& condition) {
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
  while (!condition()) {
    assert(std::chrono::steady_clock::now() < deadline && "worker did not reach expected state");
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
}
void mock_bus_touch() {
  assert(bus_owner.load() && spi_active.load());
  assert(ethernet_spi_access.load() != EthernetSpiAccess::Idle);
  ++hardware_operations;
  if (std::this_thread::get_id() == main_thread) ++main_hardware_operations;
}
void mock_worker_bus_touch() {
  assert(current_task != nullptr && std::this_thread::get_id() != main_thread);
  assert(ethernet_spi_access.load() == EthernetSpiAccess::Worker);
  mock_bus_touch();
}
void mock_spi_begin() {
  assert(current_task != nullptr && bus_owner.load());
  assert(!spi_active.exchange(true));
  ++hardware_operations;
}
void mock_spi_end() {
  assert(bus_owner.load());
  assert(spi_active.exchange(false));
  ++hardware_operations;
}
void pinMode(int pin, int mode) { assert(pin == 21 && mode == OUTPUT); }
void digitalWrite(int pin, int value) {
  assert((pin == 21 || pin == 26) && value == HIGH);
}
BaseType_t xTaskCreate(void (*function)(void*), const char* name, unsigned words,
                      void* argument, int priority, TaskHandle_t* handle) {
  assert(std::this_thread::get_id() == main_thread);
  assert(std::string(name) == "eth_init" && words == 1024 && priority == 1);
  if (fail_task_creation) return 0;
  auto created = std::make_unique<MockTask>();
  MockTask* raw = created.get();
  *handle = raw;
  ++live_task_allocations;
  raw->thread = std::thread([raw, function, argument] {
    current_task = raw;
    try {
      { std::lock_guard<std::recursive_mutex> lock(mock_scheduler_mutex); }
      {
        std::unique_lock<std::mutex> lock(library_mutex);
        library_changed.wait(lock, [] { return !defer_task_start.load(); });
      }
      function(argument);
      assert(false && "worker must park for main-thread deletion");
    } catch (const TaskDeleted&) {}
  });
  tasks.push_back(std::move(created));
  return pdPASS;
}
void xTaskNotifyGive(TaskHandle_t handle) {
  assert(handle != nullptr);
  std::lock_guard<std::mutex> lock(handle->mutex);
  assert(!handle->deleted && "stale task notification");
  ++handle->notifications;
  handle->changed.notify_all();
}
uint32_t ulTaskNotifyTake(int clear_on_exit, uint32_t ticks) {
  assert(current_task != nullptr && clear_on_exit == pdTRUE);
  std::unique_lock<std::mutex> lock(current_task->mutex);
  const auto ready = [] { return current_task->notifications || current_task->deleted; };
  if (ticks == portMAX_DELAY) current_task->changed.wait(lock, ready);
  else if (!current_task->changed.wait_for(lock, std::chrono::milliseconds(ticks), ready)) {
    mock_millis.fetch_add(ticks);
  }
  if (current_task->deleted) throw TaskDeleted{};
  const uint32_t notifications = current_task->notifications;
  current_task->notifications = 0;
  return notifications;
}
void vTaskDelay(uint32_t ticks) {
  assert(current_task != nullptr);
  if (ticks == portMAX_DELAY) {
    std::unique_lock<std::mutex> lock(current_task->mutex);
    current_task->changed.wait(lock, [] { return current_task->deleted; });
    throw TaskDeleted{};
  }
  if (ticks == 1) ++worker_access_waits;
  std::this_thread::sleep_for(std::chrono::milliseconds(ticks));
}
void vTaskDelete(TaskHandle_t handle) {
  assert(std::this_thread::get_id() == main_thread && handle != nullptr);
  assert(ethernet_worker_stopped.load() && !spi_active.load());
  assert(bus_owner.load() && "owner must remain held until stack reclaimed");
  {
    std::lock_guard<std::mutex> lock(handle->mutex);
    assert(!handle->deleted);
    handle->deleted = true;
    handle->changed.notify_all();
  }
  handle->thread.join();
  --live_task_allocations;
  ++task_deletions;
}
int MockEthernet::begin(uint8_t* mac, int timeout, int response_timeout) {
  mock_worker_bus_touch();
  assert(mac[0] == 2 && mac[3] == 0x12 && mac[4] == 0x34 && mac[5] == 0x56);
  assert(timeout == 10000 && response_timeout == 2000);
  ++begin_count;
  std::unique_lock<std::mutex> lock(library_mutex);
  library_changed.wait(lock, [this] { return !begin_blocked.load(); });
  return begin_result.load();
}
void MockEthernet::init(SPIClass& spi, int chip_select) {
  assert(chip_select == 26);
  mock_worker_bus_touch();
  mock_w5100_spi = &spi;
  W5100 = MockW5100{};
}
int MockEthernet::maintain() {
  mock_worker_bus_touch();
  ++maintain_count;
  std::unique_lock<std::mutex> lock(library_mutex);
  library_changed.wait(lock, [this] { return !maintain_blocked.load(); });
  return maintain_result.load();
}

struct Context {
  bool prepare_allowed = true;
  bool off_allowed = true;
  bool release_allowed = true;
  bool save_allowed = true;
  unsigned prepares = 0, releases = 0, saves = 0;
};
static bool prepare(void* opaque, char* error, size_t capacity) {
  assert(std::this_thread::get_id() == main_thread);
  auto& context = *static_cast<Context*>(opaque);
  ++context.prepares;
  assert(!bus_owner.load() && !spi_active.load() && !live_task_allocations.load());
  if (!context.prepare_allowed) {
    snprintf(error, capacity, "Error: flash conflict or active OTA");
    return false;
  }
  bus_owner.store(true);
  return true;
}
static bool can_release(void* opaque) {
  assert(std::this_thread::get_id() == main_thread);
  return static_cast<Context*>(opaque)->off_allowed;
}
static bool release(void* opaque) {
  assert(std::this_thread::get_id() == main_thread);
  auto& context = *static_cast<Context*>(opaque);
  ++context.releases;
  assert(bus_owner.load() && !spi_active.load());
  assert(live_task_allocations.load() == 0 && "OTA workspace cannot overlap deferred task memory");
  if (!context.release_allowed) return false;
  bus_owner.store(false);
  return true;
}
static bool save(void* opaque, bool) {
  assert(std::this_thread::get_id() == main_thread);
  auto& context = *static_cast<Context*>(opaque);
  ++context.saves;
  return context.save_allowed;
}
static void configure(Context& context, bool persisted = false, bool saved_on = false) {
  EthernetCliHooks hooks;
  hooks.prepare = prepare;
  hooks.can_release = can_release;
  hooks.released = release;
  hooks.save = persisted ? save : nullptr;
  hooks.context = &context;
  assert(ethernet_configure(hooks, saved_on));
}
static std::string command(const char* text) {
  char reply[160] = {};
  // Commands used directly by this fixture stand in for a complete input line.
  ethernet_reply_generation = ethernet_session_generation.load();
  assert(ethernet_handle_command(text, reply));
  return reply;
}
static void start_online() {
  assert(command("eth on").find("starting until restart") != std::string::npos);
  wait_until([] { return ethernet_is_running(); });
  assert(ethernet_state.load() == EthernetCliState::Online);
  assert(ethernet_worker_active.load() && live_task_allocations.load() == 1);
}
static void assert_busy_facade() {
  const int before = main_hardware_operations.load();
  assert(!ethernet_client.connected() && !static_cast<bool>(ethernet_client));
  assert(ethernet_client.available() == 0 && ethernet_client.availableForWrite() == 0);
  assert(ethernet_client.read() == -1 && ethernet_client.peek() == -1);
  assert(ethernet_client.write(uint8_t{1}) == 0);
  const uint8_t bytes[] = {1, 2, 3};
  Stream* retained_mesh_output = &ethernet_client;
  assert(retained_mesh_output->write(bytes, sizeof(bytes)) == 0);
  retained_mesh_output->flush();
  ethernet_client.stop();
  char line[32] = {};
  assert(!ethernet_read_line(line, sizeof(line)));
  ethernet_send_reply("paused");
  assert(main_hardware_operations.load() == before);
}
static void finish_off() {
  assert(command("eth off").find("until restart") != std::string::npos);
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
  while (ethernet_state.load() != EthernetCliState::Off) {
    ethernet_loop_maintain();
    assert(std::chrono::steady_clock::now() < deadline);
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  assert(!bus_owner.load() && !spi_active.load() && live_task_allocations.load() == 0);
  assert(!ethernet_get_enabled() && !ethernet_is_running());
  assert(ethernet_task_handle == nullptr);
}
static void unblock(std::atomic<bool>& flag) { flag.store(false); library_changed.notify_all(); }
static std::string output() {
  std::lock_guard<std::mutex> lock(mock_output_mutex);
  return mock_ethernet_output;
}
void mock_replace_while_parser_active() {
  const uint32_t before = ethernet_session_generation.load();
  const int previous_waits = worker_access_waits.load();
  mock_accept_client.store(true);
  wait_until([&] { return worker_access_waits.load() > previous_waits; });
  // The background worker attempted to acquire SPI during this record. It
  // must remain excluded until both input and reply-owner capture finish.
  assert(ethernet_spi_access.load() == EthernetSpiAccess::Client);
  assert(ethernet_session_generation.load() == before);
}
static void accept_connection() {
  mock_accept_client.store(true);
  ethernet_loop_maintain();
  wait_until([] { return output().find("MeshCore CLI\r\n") != std::string::npos && ethernet_is_running(); });
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const int scenario = std::stoi(argv[1]);
  Context context;
  configure(context);
  switch (scenario) {
    case 0: { // Disabled boot, strict parser and mandatory bus hooks.
      ethernet_start_task();
      assert(command("get eth") == "> off");
      assert(command("eth.status") == "ETH: off");
      assert(command("eth true").find("must be on or off") != std::string::npos);
      assert(command("set eth on extra").find("must be on or off") != std::string::npos);
      char reply[160];
      assert(!ethernet_handle_command("eth.status extra", reply));
      assert_busy_facade();
      EthernetCliHooks missing;
      assert(ethernet_configure(missing));
      assert(command("eth on").find("hooks unavailable") != std::string::npos);
      assert(hardware_operations.load() == 0 && context.prepares == 0);
      break;
    }
    case 1: { // OTA or known flash conflict refuses before any SPI operation.
      context.prepare_allowed = false;
      assert(command("set eth on").find("flash conflict") != std::string::npos);
      assert(!ethernet_get_enabled() && context.prepares == 1 && !bus_owner.load());
      assert(hardware_operations.load() == 0);
      break;
    }
    case 2: { // Allocation failure releases ownership without a deferred task.
      fail_task_creation = true;
      assert(command("eth on").find("allocation failed") != std::string::npos);
      assert(context.releases == 1 && !bus_owner.load() && !ethernet_worker_active.load());
      assert(command("eth.status") == "ETH: start failed");
      assert(hardware_operations.load() == 0);
      break;
    }
    case 3: { // OFF before worker gets scheduled never initializes hardware.
      defer_task_start.store(true);
      assert(command("eth on").find("starting") != std::string::npos);
      assert(command("eth off").find("stopping") != std::string::npos);
      assert(bus_owner.load() && context.releases == 0);
      unblock(defer_task_start);
      wait_until([] { return ethernet_worker_stopped.load(); });
      assert(hardware_operations.load() == 0 && task_deletions.load() == 0);
      ethernet_loop_maintain();
      assert(task_deletions.load() == 1 && context.releases == 1 && !bus_owner.load());
      break;
    }
    case 4: { // OFF during blocking DHCP is cooperative and cannot release SPI early.
      Ethernet.begin_blocked.store(true);
      assert(command("eth on").find("starting") != std::string::npos);
      wait_until([] { return Ethernet.begin_count.load() == 1; });
      assert_busy_facade();
      assert(command("eth off").find("stopping") != std::string::npos);
      ethernet_loop_maintain();
      assert(context.releases == 0 && bus_owner.load() && task_deletions.load() == 0);
      unblock(Ethernet.begin_blocked);
      wait_until([] { return ethernet_worker_stopped.load(); });
      ethernet_loop_maintain();
      assert(task_deletions.load() == 1 && context.releases == 1 && !bus_owner.load());
      for (const auto& count : mock_stop_count) assert(count.load() == 1);
      break;
    }
    case 5: { // OFF wakes the 30-second retry wait rather than waiting its timeout.
      Ethernet.begin_result.store(0);
      assert(command("eth on").find("starting") != std::string::npos);
      wait_until([] { return ethernet_state.load() == EthernetCliState::Retrying; });
      assert(command("eth.status") == "ETH: waiting for DHCP");
      finish_off();
      assert(Ethernet.begin_count.load() == 1 && task_deletions.load() == 1);
      break;
    }
    case 6: { // Absent hardware releases its parked task in the main loop.
      Ethernet.begin_result.store(0);
      Ethernet.hardware.store(EthernetNoHardware);
      assert(command("eth on").find("starting") != std::string::npos);
      wait_until([] { return ethernet_worker_stopped.load(); });
      assert(command("eth.status") == "ETH: hardware not found");
      assert(context.releases == 0 && live_task_allocations.load() == 1);
      ethernet_loop_maintain();
      assert(!bus_owner.load() && context.releases == 1 && live_task_allocations.load() == 0);
      for (const auto& count : mock_stop_count) assert(count.load() == 0);
      break;
    }
    case 7: { // Online framing, bounded writes and real disconnect reset the retained stream.
      start_online();
      assert(command("get eth") == "> on");
      assert(command("eth on").find("already on") != std::string::npos);
      const int before_status = hardware_operations.load();
      assert(command("eth.status") == "ETH: 192.168.1.42:23");
      assert(hardware_operations.load() == before_status);
      accept_connection();
      assert(ethernet_take_session_reset() && !ethernet_take_session_reset());
      assert(ethernet_client.connected());
      mock_ethernet_input = "eth.status\r\n";
      char line[32] = {};
      assert(ethernet_read_line(line, sizeof(line)) && std::string(line) == "eth.status");
      wait_until([] { return ethernet_is_running(); });
      assert(ethernet_client.write(reinterpret_cast<const uint8_t*>("abcde"), 5) == 5);
      wait_until([] { return output().find("abcde") != std::string::npos && ethernet_is_running(); });
      mock_socket_connected[0].store(false);
      wait_until([] { return ethernet_session_reset.load() && !ethernet_client_attached.load(); });
      wait_until([] { return ethernet_is_running(); });
      assert(ethernet_take_session_reset() && !ethernet_client.connected());
      finish_off();
      break;
    }
    case 8: { // Renewal owns SPI; every retained Stream call yields without raw IO.
      start_online();
      Ethernet.maintain_blocked.store(true);
      mock_millis.store(1000);
      const auto before = std::chrono::steady_clock::now();
      ethernet_loop_maintain();
      assert(std::chrono::steady_clock::now() - before < std::chrono::milliseconds(100));
      wait_until([] { return Ethernet.maintain_count.load() == 1; });
      assert(command("eth.status") == "ETH: renewing DHCP");
      assert_busy_facade();
      for (int index = 0; index < 20; ++index) ethernet_loop_maintain();
      assert(context.releases == 0 && !ethernet_take_session_reset());
      unblock(Ethernet.maintain_blocked);
      wait_until([] { return ethernet_is_running(); });
      assert(command("eth.status") == "ETH: 192.168.1.42:23");
      finish_off();
      break;
    }
    case 9: { // Failed lease renewal does not block mesh service or restart the worker.
      start_online();
      Ethernet.maintain_result.store(1);
      Ethernet.ip.store(0x3301a8c0);
      mock_millis.store(1000);
      ethernet_loop_maintain();
      wait_until([] { return Ethernet.maintain_count.load() == 1 && ethernet_is_running(); });
      assert(command("eth.status") == "ETH: 192.168.1.51:23");
      assert(Ethernet.begin_count.load() == 1 && live_task_allocations.load() == 1);
      finish_off();
      break;
    }
    case 10: { // Active or staged OTA refuses OFF without affecting the active transport.
      start_online();
      context.off_allowed = false;
      assert(command("eth off").find("locked by OTA") != std::string::npos);
      assert(ethernet_get_enabled() && ethernet_is_running() && bus_owner.load());
      assert(context.releases == 0 && task_deletions.load() == 0);
      context.off_allowed = true;
      finish_off();
      break;
    }
    case 11: { // Ethernet-origin OFF can acknowledge before the next loop gates the bus.
      start_online();
      accept_connection();
      const std::string reply = command("eth off");
      assert(reply.find("stopping until restart") != std::string::npos);
      ethernet_send_reply(reply.c_str());
      assert(context.releases == 0 && task_deletions.load() == 0);
      ethernet_loop_maintain();
      wait_until([] { return ethernet_worker_stopped.load(); });
      assert(output().find(reply) != std::string::npos);
      assert_busy_facade();
      assert(live_task_allocations.load() == 1 && bus_owner.load());
      ethernet_loop_maintain();
      assert(task_deletions.load() == 1 && context.releases == 1 && !bus_owner.load());
      for (const auto& count : mock_stop_count) assert(count.load() >= 1);
      assert(ethernet_take_session_reset());
      break;
    }
    case 12: { // OTA race after preflight retains owner while release retries on the main loop.
      start_online();
      context.release_allowed = false;
      assert(command("eth off").find("stopping") != std::string::npos);
      ethernet_loop_maintain();
      wait_until([] { return ethernet_worker_stopped.load(); });
      ethernet_loop_maintain();
      assert(task_deletions.load() == 1 && live_task_allocations.load() == 0);
      assert(bus_owner.load() && !spi_active.load() && context.releases == 1);
      assert(command("eth on").find("still stopping") != std::string::npos);
      ethernet_loop_maintain();
      assert(context.releases == 2 && bus_owner.load());
      context.release_allowed = true;
      ethernet_loop_maintain();
      assert(context.releases == 3 && !bus_owner.load());
      assert(ethernet_state.load() == EthernetCliState::Off);
      break;
    }
    case 13: { // Optional save failures are transactional; prototype has no save hook.
      configure(context, true);
      context.save_allowed = false;
      assert(command("eth on").find("cannot save") != std::string::npos);
      assert(!bus_owner.load() && hardware_operations.load() == 0 && context.saves == 1);
      context.save_allowed = true;
      assert(command("eth on") == "OK - Ethernet starting; use eth.status");
      wait_until([] { return ethernet_is_running(); });
      context.save_allowed = false;
      assert(command("eth off").find("cannot save") != std::string::npos);
      assert(ethernet_get_enabled() && ethernet_is_running());
      context.save_allowed = true;
      assert(command("eth off") == "OK - Ethernet stopping");
      ethernet_loop_maintain();
      wait_until([] { return ethernet_worker_stopped.load(); });
      ethernet_loop_maintain();
      assert(!bus_owner.load() && live_task_allocations.load() == 0);
      break;
    }
    case 14: { // Repeated hot switches reclaim each worker and cannot notify a stale handle.
      for (int cycle = 0; cycle < 4; ++cycle) {
        start_online();
        assert(!ethernet_configure(EthernetCliHooks{}));
        finish_off();
        ethernet_notify_worker();
      }
      assert(task_deletions.load() == 4 && context.prepares == 4 && context.releases == 4);
      assert(Ethernet.begin_count.load() == 4);
      break;
    }
    case 15: { // A future saved-ON configuration starts without writing its settings again.
      configure(context, true, true);
      ethernet_start_task();
      wait_until([] { return ethernet_is_running(); });
      assert(context.saves == 0 && context.prepares == 1);
      assert(command("eth off") == "OK - Ethernet stopping");
      ethernet_loop_maintain();
      wait_until([] { return ethernet_worker_stopped.load(); });
      ethernet_loop_maintain();
      assert(context.saves == 1 && !bus_owner.load());
      break;
    }
    case 16: { // OFF during a slow renewal leaves bus reserved until that library call returns.
      start_online();
      Ethernet.maintain_blocked.store(true);
      mock_millis.store(1000);
      ethernet_loop_maintain();
      wait_until([] { return Ethernet.maintain_count.load() == 1; });
      assert(command("eth off").find("stopping") != std::string::npos);
      for (int index = 0; index < 10; ++index) ethernet_loop_maintain();
      assert_busy_facade();
      assert(context.releases == 0 && task_deletions.load() == 0 && bus_owner.load());
      unblock(Ethernet.maintain_blocked);
      wait_until([] { return ethernet_worker_stopped.load(); });
      ethernet_loop_maintain();
      assert(context.releases == 1 && task_deletions.load() == 1 && !bus_owner.load());
      break;
    }
    case 17: { // Bounded ring queue applies backpressure and preserves wrap-around order.
      start_online();
      accept_connection();
      (void)ethernet_take_session_reset();
      mock_write_capacity.store(0);
      const std::string first(400, 'a');
      const std::string second(400, 'b');
      assert(ethernet_client.write(reinterpret_cast<const uint8_t*>(first.data()), first.size()) == first.size());
      assert(ethernet_client.write(reinterpret_cast<const uint8_t*>(second.data()), second.size()) == 112);
      assert(ethernet_client.availableForWrite() == 0);
      assert(ethernet_client.write(uint8_t{'x'}) == 0);
      mock_ethernet_input = "eth off\r";
      char line[32] = {};
      assert(!ethernet_read_line(line, sizeof(line)) && mock_ethernet_input == "eth off\r");
      mock_write_capacity.store(2048);
      wait_until([&] { return output().find(first + second.substr(0, 112)) != std::string::npos && ethernet_is_running(); });
      assert(ethernet_client.write(reinterpret_cast<const uint8_t*>(second.data() + 112), 288) == 288);
      wait_until([&] { return output().find(first + second) != std::string::npos && ethernet_is_running(); });
      assert(ethernet_read_line(line, sizeof(line)) && std::string(line) == "eth off");
      finish_off();
      break;
    }
    case 18: { // Oversized records cannot execute their truncated command prefix.
      start_online();
      accept_connection();
      (void)ethernet_take_session_reset();
      mock_ethernet_input = "eth off" + std::string(80, 'x') + "\rget eth\r";
      char line[32] = {};
      assert(!ethernet_read_line(line, sizeof(line)) && line[0] == 0);
      assert(ethernet_get_enabled() && ethernet_take_session_reset());
      wait_until([] { return ethernet_is_running(); });
      assert(ethernet_read_line(line, sizeof(line)) && std::string(line) == "get eth");
      wait_until([] { return output().find("invalid or oversized command; discarded") != std::string::npos && ethernet_is_running(); });
      line[0] = 0;
      mock_ethernet_input = std::string(31, 'm') + "\r";
      assert(ethernet_read_line(line, sizeof(line)) && std::strlen(line) == 31);
      finish_off();
      break;
    }
    case 19: { // Embedded NUL discards the entire record, including a valid-looking prefix.
      start_online();
      accept_connection();
      (void)ethernet_take_session_reset();
      mock_ethernet_input = std::string("eth off\0junk", 12) + "\rget eth\r";
      char line[32] = {};
      assert(!ethernet_read_line(line, sizeof(line)) && line[0] == 0);
      assert(ethernet_get_enabled() && ethernet_take_session_reset());
      wait_until([] { return ethernet_is_running(); });
      assert(ethernet_read_line(line, sizeof(line)) && std::string(line) == "get eth");
      finish_off();
      break;
    }
    case 20: { // Missing TCP ACK retains Worker ownership and cannot block retained streams.
      start_online();
      accept_connection();
      (void)ethernet_take_session_reset();
      mock_ack_send.store(false);
      assert(ethernet_client.write(reinterpret_cast<const uint8_t*>("payload"), 7) == 7);
      wait_until([] { return !ethernet_is_running(); });
      assert_busy_facade();
      for (int iteration = 0; iteration < 10; ++iteration) ethernet_loop_maintain();
      wait_until([] { return ethernet_is_running() && !ethernet_client_attached.load(); });
      assert(ethernet_take_session_reset() && output().find("payload") != std::string::npos);
      assert(ethernet_worker_active.load() && bus_owner.load());
      finish_off();
      break;
    }
    case 21: { // A stuck SEND register faults safely rather than exposing it to vendor RX/accept.
      start_online();
      accept_connection();
      (void)ethernet_take_session_reset();
      mock_ack_send.store(false);
      mock_stuck_command.store(true);
      assert(ethernet_client.write(uint8_t{'z'}) == 1);
      wait_until([] { return ethernet_state.load() == EthernetCliState::Faulted; });
      assert(command("eth.status").find("controller stalled") != std::string::npos);
      assert_busy_facade();
      const int before = hardware_operations.load();
      mock_accept_client.store(true);
      for (int iteration = 0; iteration < 20; ++iteration) ethernet_loop_maintain();
      assert(hardware_operations.load() == before && bus_owner.load());
      finish_off();
      assert(task_deletions.load() == 1);
      mock_stuck_command.store(false);
      mock_ack_send.store(true);
      start_online();
      finish_off();
      break;
    }
    case 22: { // OFF transmits its queued acknowledgement, then bounds waiting for missing ACK.
      start_online();
      accept_connection();
      mock_ack_send.store(false);
      const std::string reply = command("eth off");
      ethernet_send_reply(reply.c_str());
      const auto started = std::chrono::steady_clock::now();
      ethernet_loop_maintain();
      wait_until([] { return ethernet_worker_stopped.load(); });
      assert(std::chrono::steady_clock::now() - started < std::chrono::seconds(1));
      assert(output().find("  -> " + reply + "\r\n") != std::string::npos);
      ethernet_loop_maintain();
      assert(!bus_owner.load() && task_deletions.load() == 1);
      break;
    }
    case 23: { // Discard is bounded across calls and never exposes a fragmented long record.
      start_online();
      accept_connection();
      (void)ethernet_take_session_reset();
      mock_ethernet_input = std::string(220, 'x') + "\nget eth\n";
      char line[32] = {};
      assert(!ethernet_read_line(line, sizeof(line)) && line[0] == 0);
      assert(mock_ethernet_input.size() == 101 && ethernet_line_discarding);
      assert(!ethernet_read_line(line, sizeof(line)) && line[0] == 0);
      assert(!ethernet_line_discarding);
      (void)ethernet_take_session_reset();
      wait_until([] { return ethernet_is_running(); });
      assert(ethernet_read_line(line, sizeof(line)) && std::string(line) == "get eth");
      finish_off();
      break;
    }
    case 24: { // A newly stuck RECV command yields instead of entering vendor execCmdSn.
      start_online();
      accept_connection();
      (void)ethernet_take_session_reset();
      mock_stuck_command.store(true);
      mock_ethernet_input = "get eth\r";
      char line[32] = {};
      assert(!ethernet_read_line(line, sizeof(line)) && std::string(line) == "g");
      assert_busy_facade();
      wait_until([] { return ethernet_state.load() == EthernetCliState::Faulted; });
      assert(ethernet_get_enabled() && bus_owner.load());
      finish_off();
      break;
    }
    case 25: { // A delayed terminal RECV command does not lose its synchronous reply.
      start_online();
      accept_connection();
      (void)ethernet_take_session_reset();
      mock_hold_terminal_recv.store(true);
      mock_ethernet_input = "get eth\r";
      char line[32] = {};
      assert(ethernet_read_line(line, sizeof(line)) && std::string(line) == "get eth");
      assert(!ethernet_is_running());
      ethernet_send_reply("> on");
      assert(ethernet_tx_count >= std::strlen("  -> > on\r\n"));
      mock_hold_terminal_recv.store(false);
      wait_until([] { return output().find("  -> > on\r\n") != std::string::npos && ethernet_is_running(); });
      finish_off();
      break;
    }
    case 26: { // Replacement cancels retained async output and rejects the previous peer's reply.
      start_online();
      accept_connection();
      (void)ethernet_take_session_reset();
      mock_ethernet_input = "get eth\r";
      char line[32] = {};
      assert(ethernet_read_line(line, sizeof(line)));
      const uint32_t previous = ethernet_session_generation.load();
      mock_accept_client.store(true);
      wait_until([&] { return ethernet_session_generation.load() != previous && ethernet_is_running(); });
      ethernet_send_reply("previous-peer-result");
      assert(ethernet_client.write(reinterpret_cast<const uint8_t*>("old-async"), 9) == 0);
      (void)ethernet_take_session_reset();
      finish_off();
      assert(output().find("previous-peer-result") == std::string::npos);
      assert(output().find("old-async") == std::string::npos);
      break;
    }
    case 27: { // A partial line from a replaced client cannot concatenate with the next peer.
      start_online();
      accept_connection();
      (void)ethernet_take_session_reset();
      mock_ethernet_input = "eth ";
      char line[32] = {};
      assert(!ethernet_read_line(line, sizeof(line)) && std::string(line) == "eth ");
      const uint32_t before = ethernet_session_generation.load();
      mock_accept_client.store(true);
      wait_until([&] { return ethernet_session_generation.load() != before && ethernet_is_running(); });
      mock_ethernet_input = "off\r";
      assert(!ethernet_read_line(line, sizeof(line)) && line[0] == 0);
      assert(mock_ethernet_input == "off\r");
      (void)ethernet_take_session_reset();
      assert(ethernet_read_line(line, sizeof(line)) && std::string(line) == "off");
      finish_off();
      break;
    }
    case 28: { // Concurrent replacement cannot steal the record before its owner is captured.
      start_online();
      accept_connection();
      (void)ethernet_take_session_reset();
      const uint32_t before = ethernet_session_generation.load();
      mock_ethernet_input = "get eth\r";
      mock_replace_during_parser.store(true);
      char line[32] = {};
      assert(ethernet_read_line(line, sizeof(line)) && std::string(line) == "get eth");
      assert(ethernet_reply_generation == before && worker_access_waits.load() > 0);
      wait_until([&] { return ethernet_session_generation.load() != before && ethernet_is_running(); });
      ethernet_send_reply("old-record-result");
      finish_off();
      assert(output().find("old-record-result") == std::string::npos);
      break;
    }
    default: assert(false);
  }
  assert(live_task_allocations.load() == 0 && !spi_active.load() && !bus_owner.load());
  assert(ethernet_tx_queue == nullptr && ethernet_tx_count == 0);
  for (const auto& task : tasks) assert(!task->thread.joinable());
  printf("PASS: RAK4631 Ethernet runtime scenario %d\n", scenario);
}
