// The Home fields, refresh method, and input block come from production.
// Only clock, board, sensor/LPP transport, and task boundaries are mocked.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#define TELEM_CHANNEL_SELF 0
#define UI_RECENT_LIST_SIZE 3
#define KEY_ENTER '\r'

static uint32_t clock_ms = 0;
uint32_t millis() { return clock_ms; }

static const char* active_case = "initialization";
static unsigned checks = 0;
static unsigned scenarios = 0;
#define CHECK(condition) do { \
  ++checks; \
  if (!(condition)) { \
    std::fprintf(stderr, "FAIL %s: %s (line %d)\n", active_case, #condition, __LINE__); \
    std::exit(1); \
  } \
} while (0)

struct CayenneLPP {
  uint8_t bytes[200] = {};
  uint8_t size = 0;
  bool allocated = true;
  unsigned writes = 0;
  uint8_t* getBuffer() { return allocated ? bytes : nullptr; }
  uint8_t getSize() { return size; }
  void reset() { size = 0; }
  void addVoltage(int channel, float voltage) {
    CHECK(getBuffer() != nullptr);
    CHECK(channel == TELEM_CHANNEL_SELF && voltage == 4.0f);
    ++writes;
    bytes[size++] = 4;
    size += 3;
  }
  void addSample() {
    CHECK(getBuffer() != nullptr && size < sizeof(bytes) - 4);
    ++writes;
    bytes[size++] = 4;
    size += 3;
  }
};

struct LPPReader {
  unsigned remaining;
  LPPReader(uint8_t* buffer, uint8_t size) : remaining(buffer ? size / 4 : 0) {}
  bool readHeader(uint8_t& channel, uint8_t& type) {
    if (!remaining) return false;
    --remaining;
    channel = 0;
    type = 1;
    return true;
  }
  void skipData(uint8_t) {}
};

struct Board {
  unsigned reads = 0;
  int getBattMilliVolts() { ++reads; return 4000; }
} board;

struct Sensors {
  unsigned calls = 0;
  unsigned samples = 0;
  uint32_t duration_ms = 0;
  void querySensors(int mask, CayenneLPP& lpp) {
    CHECK(mask == 0xFF);
    ++calls;
    for (unsigned i = 0; i < samples; ++i) lpp.addSample();
    clock_ms += duration_ms;
  }
} sensors;

struct Task {
  unsigned toggles = 0;
  void toggleGPS() { ++toggles; }
};

enum class HomePage { OTHER, SENSORS };

struct Home {
  @FIELDS@
  Task task;
  Task* _task = &task;
  HomePage _page = HomePage::SENSORS;

  @REFRESH@

  bool input(char @KEY@) {
    @INPUT@
    return false;
  }
};

static constexpr uint32_t interval_ms = AUTO_OFF_MILLIS > 0 ? 5000u : 60000u;

static void begin_case(const char* name, uint32_t initial_ms) {
  active_case = name;
  ++scenarios;
  clock_ms = initial_ms;
  sensors = Sensors{};
  board = Board{};
}

static void initial_at(uint32_t at) {
  begin_case("fresh page", at);
  Home home;
  CHECK(home.sensors_lpp.getSize() == 0);
  home.refresh_sensors();
  CHECK(sensors.calls == 1 && board.reads == 1);
  CHECK(home.sensors_lpp.getSize() == 4);
  CHECK(home.sensors_nb == 1 && !home.sensors_scroll);
  home.refresh_sensors();
  CHECK(sensors.calls == 1 && board.reads == 1);
}

static void cadence_and_records() {
  begin_case("normal cadence and records", 100);
  Home home;
  sensors.samples = 4;
  home.refresh_sensors();
  CHECK(sensors.calls == 1 && home.sensors_nb == 5 && home.sensors_scroll);
  clock_ms += interval_ms - 1;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  ++clock_ms;
  sensors.samples = 0;
  home.refresh_sensors();
  CHECK(sensors.calls == 2 && home.sensors_nb == 1 && !home.sensors_scroll);
  CHECK(home.sensors_lpp.getSize() == 4);
  home.refresh_sensors();
  CHECK(sensors.calls == 2);
}

static void wrap_visible() {
  begin_case("visible across wrap", UINT32_MAX - interval_ms / 2);
  Home home;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  ++clock_ms;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  clock_ms += interval_ms - 2;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  ++clock_ms;
  home.refresh_sensors();
  CHECK(sensors.calls == 2);
}

static void wake_across_wrap() {
  begin_case("wake across wrap", UINT32_MAX - 2 * interval_ms);
  Home home;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  // The display is not rendered while asleep; wake after millis wraps.
  clock_ms = 1000;
  home.refresh_sensors();
  CHECK(sensors.calls == 2);
  home.refresh_sensors();
  CHECK(sensors.calls == 2);
}

static void exact_wrap_deadline() {
  begin_case("deadline at zero", uint32_t(0u - interval_ms));
  Home home;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  ++clock_ms;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  clock_ms = UINT32_MAX;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  clock_ms = 0;
  home.refresh_sensors();
  CHECK(sensors.calls == 2);
  home.refresh_sensors();
  CHECK(sensors.calls == 2);
}

static void long_idle(uint32_t elapsed) {
  begin_case("long inactive page", 100);
  Home home;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  clock_ms += elapsed;
  home.refresh_sensors();
  CHECK(sensors.calls == 2);
  home.refresh_sensors();
  CHECK(sensors.calls == 2);
}

static void force_at(uint32_t initial, uint32_t forced) {
  begin_case("forced input", initial);
  Home home;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  clock_ms = forced;
  CHECK(!home.input('x'));
  CHECK(home.task.toggles == 0);
  home._page = HomePage::OTHER;
  CHECK(!home.input(KEY_ENTER));
  CHECK(home.task.toggles == 0);
  home._page = HomePage::SENSORS;
  CHECK(home.input(KEY_ENTER));
  CHECK(home.task.toggles == 1);
  CHECK(sensors.calls == 1);
  home.refresh_sensors();
  CHECK(sensors.calls == 2 && home.sensors_nb == 1);
  home.refresh_sensors();
  CHECK(sensors.calls == 2);
}

static void completion_anchor(bool wrap) {
  begin_case("completion-relative cadence", wrap ? UINT32_MAX - 10 : 100);
  Home home;
  sensors.duration_ms = 37;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  const uint32_t completion = clock_ms;
  clock_ms = completion + interval_ms - 1;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
  ++clock_ms;
  home.refresh_sensors();
  CHECK(sensors.calls == 2);
  CHECK(clock_ms == uint32_t(completion + interval_ms + sensors.duration_ms));
}

static void empty_query_keeps_base_voltage() {
  begin_case("empty sensor query", 100);
  Home home;
  CHECK(sensors.samples == 0);
  home.refresh_sensors();
  CHECK(home.sensors_lpp.getSize() == 4 && home.sensors_nb == 1);
  ++clock_ms;
  home.refresh_sensors();
  CHECK(sensors.calls == 1);
}

#if TEST_NEW_UI
static void allocation_guard() {
  begin_case("allocation guard", 0);
  Home home;
  home.sensors_lpp.allocated = false;
  home.sensors_nb = 5;
  home.sensors_scroll = true;
  home.sensors_scroll_offset = 4;
  home.refresh_sensors();
  CHECK(sensors.calls == 0 && board.reads == 0 && home.sensors_lpp.writes == 0);
  CHECK(home.sensors_nb == 0 && !home.sensors_scroll && home.sensors_scroll_offset == 0);
  home.sensors_lpp.allocated = true;
  home.refresh_sensors();
  CHECK(sensors.calls == 1 && home.sensors_nb == 1);
}
#endif

int main(int argc, char** argv) {
  if (argc == 2 && std::strcmp(argv[1], "force") == 0) {
    force_at(100, 101);
  } else {
    initial_at(0);
    initial_at(0x80000000u);
    initial_at(UINT32_MAX);
    cadence_and_records();
    wrap_visible();
    wake_across_wrap();
    exact_wrap_deadline();
    long_idle(26u * 24u * 60u * 60u * 1000u);
    long_idle(UINT32_MAX - 1000);
    force_at(100, 101);
    force_at(0x80000000u, 0x80000001u);
    force_at(UINT32_MAX - 10, 0);
    completion_anchor(false);
    completion_anchor(true);
    empty_query_keeps_base_voltage();
#if TEST_NEW_UI
    allocation_guard();
#endif
  }
  std::printf("PASS: %u scenarios; %u checks\n", scenarios, checks);
}
