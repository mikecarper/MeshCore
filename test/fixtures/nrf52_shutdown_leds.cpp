#include <Arduino.h>
#include <algorithm>
#include <stdexcept>

#define LED_STATE_ON 1
#define LED_BLUE 36
#define LED_BUILTIN 35
#define LED_PIN LED_BUILTIN
#define LED_GREEN LED_BUILTIN
#define LED_RED (-1)
#define P_LORA_RESET 40
#define P_LORA_BUSY 41
#define P_LORA_NSS 42
#define P_LORA_DIO_1 43
#define P_LORA_SCLK 44
#define P_LORA_MISO 45
#define P_LORA_MOSI 46
#define DISPLAY_CLASS 1
#define HAS_GPS 1
#define GPS_VRTC_EN 10
#define GPS_RESET 11
#define GPS_SLEEP_INT 12
#define GPS_RTC_INT 13
#define GPS_EN 14
#define PIN_DRV_EN 15
#define PIN_BAT_ADC_EN 16
#define PIN_3V3_EN 17
#define MESH_DEBUG_PRINTLN(...) do {} while (0)
#define POWER_SYSTEMOFF_SYSTEMOFF_Enter 1
#define NRF_ERROR_SOFTDEVICE_NOT_ENABLED 2
#define pdMS_TO_TICKS(ms) (ms)

#include <helpers/nrf52/ShutdownLeds.h>

std::vector<Event> events;
int levels[PINS_COUNT];
bool timer_active = false, timer_stop_pending = false, automatic_owner = true;
bool ble_started = false;
uint8_t sd_enabled_mock = 0;
uint32_t now = 0;
struct Exit {};

void event(const char* name) { events.push_back({name, 0, 0}); }
struct BluefruitMock {
  void autoConnLed(bool enabled) {
    assert(!enabled);
    event("stop-auto-led");
    automatic_owner = enabled;
    // The real SDK accepts this before begin(), when the timer handle is NULL.
    if (ble_started) timer_stop_pending = true;
  }
} Bluefruit;

uint32_t millis() { return now; }
int digitalRead(int) { return LOW; }
void detachInterrupt(int) { event("detach-radio-irq"); }
void delayMicroseconds(int) {}
void delay(unsigned ms) {
  now += ms;
  if (timer_active) {
    // SDK blinky_cb does not test _led_conn: a callback already ready to run
    // can still execute before the queued timer stop is processed.
    event("pending-blinky");
    digitalWrite(LED_BLUE, !levels[LED_BLUE]);
  }
  if (timer_stop_pending) {
    timer_active = false;
    timer_stop_pending = false;
    event("timer-stopped");
  }
}
void vTaskDelay(uint32_t) { event("timed-sleep"); }
void NVIC_SystemReset() { event("reset"); throw Exit{}; }
namespace mesh_nrf52 {
int softdeviceIsEnabled(uint8_t& enabled) { enabled = sd_enabled_mock; return 0; }
}
struct Register {
  Register& operator=(uint32_t) { event("systemoff"); return *this; }
};
struct PowerRegisters { Register SYSTEMOFF; uint8_t GPREGRET2; } power_registers;
PowerRegisters* NRF_POWER = &power_registers;
int sd_power_gpregret_clr(int, int) { return 0; }
int sd_power_gpregret_set(int, int) { return 0; }
uint32_t sd_power_system_off() { event("systemoff"); throw Exit{}; }
struct SerialMock { void flush() { event("flush"); } } Serial;
struct SpiMock { void setPins(int,int,int) {} void begin() { event("spi"); } } SPI;
struct RadioMock { void powerOff() { event("radio-off"); } } radio_driver;
struct LocationMock { void stop() { event("gps-off"); } } location;
struct SensorsMock { LocationMock* getLocationProvider() { return &location; } } sensors;
struct DisplayMock {
  bool isOn() { return true; }
  void turnOff() { event("display-off"); }
} display;

class NRF52Board {
public:
  virtual ~NRF52Board() {}
  virtual void shutdownPeripherals();
  virtual void powerOff();
  void enterDeepSleep(uint32_t secs);
  void enterSystemOff(uint8_t reason);
  void reboot() { NVIC_SystemReset(); }
};
class MeshTrackerX1Board : public NRF52Board {
public:
  void shutdownPeripherals() override;
};

@METHODS@
@X1_HOOK@

size_t first(const char* name) {
  for (size_t i = 0; i < events.size(); ++i) {
    if (events[i].name == name) return i;
  }
  return events.size();
}
size_t lastWrite(int pin) {
  size_t result = events.size();
  for (size_t i = 0; i < events.size(); ++i) {
    if (events[i].name == "write" && events[i].pin == pin) result = i;
  }
  return result;
}
void prepare(bool started, bool connected, bool sd) {
  events.clear();
  for (int& level : levels) level = LED_STATE_ON;
  now = 0;
  ble_started = started;
  // Advertising uses a timer; a connected central leaves a solid LED on.
  timer_active = started && !connected;
  timer_stop_pending = false;
  automatic_owner = true;
  sd_enabled_mock = sd;
}
void verify(bool had_timer, bool peripherals, const char* exit_event) {
  assert(!automatic_owner && !timer_active && !timer_stop_pending);
  assert(levels[LED_BLUE] == !LED_STATE_ON);
  assert(levels[LED_BUILTIN] == !LED_STATE_ON);
  assert(first("stop-auto-led") < lastWrite(LED_BLUE));
  if (had_timer) {
    assert(first("stop-auto-led") < first("pending-blinky"));
    assert(first("timer-stopped") < lastWrite(LED_BLUE));
  }
  if (peripherals) {
    assert(first("radio-off") < first("gps-off"));
    assert(first("gps-off") < lastWrite(LED_BLUE));
  } else {
    assert(first("radio-off") == events.size());
    assert(first("gps-off") == events.size());
  }
  if (exit_event) assert(lastWrite(LED_BLUE) < first(exit_event));
  // Subsequent BLE event writes are gated by the same _led_conn flag as SDK.
  if (automatic_owner) digitalWrite(LED_BLUE, LED_STATE_ON);
  assert(levels[LED_BLUE] == !LED_STATE_ON);
}

int main() {
  NRF52Board base;
  MeshTrackerX1Board tracker;
  for (bool started : {false, true}) {
    for (bool connected : {false, true}) {
      for (bool sd : {false, true}) {
        const bool had_timer = started && !connected;
        prepare(started, connected, sd);
        base.shutdownPeripherals();
        verify(had_timer, true, nullptr);
        // Repeating shutdown is safe, including a never-initialized BLE stack.
        base.shutdownPeripherals();
        verify(had_timer, true, nullptr);

        prepare(started, connected, sd);
        try { base.powerOff(); } catch (const Exit&) {}
        verify(had_timer, true, "systemoff");
        prepare(started, connected, sd);
        try { base.enterDeepSleep(0); } catch (const Exit&) {}
        verify(had_timer, true, "systemoff");
        prepare(started, connected, sd);
        try { base.enterDeepSleep(10); } catch (const Exit&) {}
        verify(had_timer, true, "timed-sleep");

        // Early low-voltage boot shutdown must not start GPS/radio peripherals.
        prepare(started, connected, sd);
        try { base.enterSystemOff(42); } catch (const Exit&) {}
        verify(had_timer, false, "systemoff");

        // Regression for #3567: inherited powerOff must dispatch the X1 hook.
        prepare(started, connected, sd);
        try { tracker.powerOff(); } catch (const Exit&) {}
        verify(had_timer, true, "systemoff");
        for (int pin : {GPS_VRTC_EN, GPS_RESET, GPS_SLEEP_INT, GPS_RTC_INT,
                        GPS_EN, PIN_DRV_EN, PIN_BAT_ADC_EN, PIN_3V3_EN}) {
          assert(levels[pin] == LOW);
          assert(lastWrite(pin) < first("systemoff"));
        }
      }
    }
  }
}
