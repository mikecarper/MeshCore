"""Run actual RAK terminal shutdown hooks; no RF/current measurement or PIO."""
from pathlib import Path
import unittest

from test_nrf52_shutdown_leds import compile_source, lifecycle_source
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
BOARDS = (("rak4631", "RAK4631Board", 37), ("rak3401", "RAK3401Board", 21))
POE_MACROS = ("ETHERNET_ENABLED", "ETHERNET_USE_RAK13800", "RAK4631_COMBINED_ETHERNET")


def source_for(variant, board, enable_pin, *, mutation=None):
    cpp = (ROOT / f"variants/{variant}/{board}.cpp").read_text()
    header = (ROOT / f"variants/{variant}/{board}.h").read_text()
    if "void shutdownPeripherals() override;" not in header:
        raise AssertionError("RAK must retain the inherited power-off dispatch hook")
    shutdown = extract_braced(cpp, f"void {board}::shutdownPeripherals()")
    if mutation == "no-board-override":
        shutdown = f"void {board}::shutdownPeripherals() {{ NRF52Board::shutdownPeripherals(); }}"
    elif mutation == "ethernet-keeps-rail-on":
        rail = "PIN_3V3_EN" if variant == "rak3401" else "WB_IO2"
        shutdown = shutdown.replace(f"digitalWrite({rail}, LOW);",
                                    f"#ifndef ETHERNET_ENABLED\n  digitalWrite({rail}, LOW);\n#endif")
    elif mutation == "wire-left-active":
        shutdown = shutdown.replace("Wire.end();", "// negative control")
    elif mutation == "spi1-ended-unstarted":
        shutdown = shutdown.replace("SPI1.begin();", "// negative control")
    elif mutation == "external-radio-pad-left-driven":
        shutdown = shutdown.replace("pinMode(P_LORA_NSS, INPUT);", "// negative control")
    elif mutation == "ignore-storage-hold":
        shutdown = shutdown.replace("sensors.canRemoveSharedStoragePowerForDeepSleep()", "true")
    protective = extract_braced(cpp, f"void {board}::initiateShutdown(uint8_t reason)")
    # Reuse the real base shutdown, deferred-LED-owner model and Arduino stub.
    # Remove its main only; the RAK cases exercise inherited virtual dispatch.
    source = lifecycle_source().split("int main() {", 1)[0]
    if variant == "rak3401":
        # The real external-radio nets alias RAK13800 SPI/CS/reset. Synthetic
        # private-radio pins would conceal unsafe timeout fallback operations.
        for name, old, actual in (("RESET",40,4),("BUSY",41,9),("NSS",42,26),
                                  ("DIO_1",43,10),("SCLK",44,3),("MISO",45,29),("MOSI",46,30)):
            source = source.replace(f"#define P_LORA_{name} {old}", f"#define P_LORA_{name} {actual}")
    source = source.replace("#define PIN_3V3_EN 17", "#define PIN_3V3_EN 34")
    source = source.replace(
        "struct SensorsMock { LocationMock* getLocationProvider() { return &location; } } sensors;",
        """struct SensorsMock {
  bool storage_safe=true;
  bool canRemoveSharedStoragePowerForDeepSleep() const { return storage_safe; }
  LocationMock* getLocationProvider() { return &location; }
} sensors;""")
    source = source.replace(
        'struct SpiMock { void setPins(int,int,int) {} void begin() { event("spi"); } } SPI;',
        '''struct SpiMock {
  bool begun=false;
  void setPins(int,int,int) {}
  void begin() { event("spi");begun=true; }
  void end() { assert(begun);event("spi-end");begun=false; }
} SPI;''')
    source += f"\n#define SX126X_POWER_EN {enable_pin}\n#define WB_IO2 PIN_3V3_EN\n"
    if variant == "rak3401":
        source += "#define RAK_EXTERNAL_RADIO 1\n"
    source += r'''
#include <helpers/nrf52/TerminalPeripheralShutdown.h>
#define PIN_GPS_TX 15
#define PIN_GPS_RX 16
#define PIN_WIRE_SDA 13
#define PIN_WIRE_SCL 14
#define SHUTDOWN_REASON_LOW_VOLTAGE 0x4c
#define SHUTDOWN_REASON_BOOT_PROTECT 0x42
struct { uint8_t lpcomp_ain_channel=3,lpcomp_refsel=4; } power_config;
struct UartMock {
  bool begun=false;
  explicit operator bool() const { return begun; }
  void end() {
    assert(begun); // the real Adafruit end() is unsafe before begin()
    event("gps-uart-end");begun=false;
  }
} Serial1;
struct {
  bool begun=false;
  void begin() { event("spi1-begin");begun=true; }
  void end() { assert(begun);event("spi1-end");begun=false; }
} SPI1;
struct WireMock { void end() { event("wire-end"); } } Wire;
bool ethernet_can_stop=true;
bool stopEthernet(void*,uint32_t timeout) {
  assert(timeout==500);event("ethernet-stop");return ethernet_can_stop;
}
'''
    source += f'''
class {board} : public NRF52Board {{
public:
  void shutdownPeripherals() override;
  void initiateShutdown(uint8_t reason);
  void configureVoltageWake(uint8_t,uint8_t) {{ event("voltage-wake"); }}
}};
'''
    source += shutdown + "\n" + protective
    source += r'''
size_t lastMode(int pin) {
  size_t found=events.size();
  for(size_t i=0;i<events.size();++i)
    if(events[i].name=="mode"&&events[i].pin==pin) found=i;
  return found;
}
static void verifyRakShutdown(bool uart_started,const char* exit_event) {
  assert(first("ethernet-stop")<first("radio-off"));
  assert(first("radio-off")<first("gps-off"));
  assert(first("gps-off")<lastWrite(SX126X_POWER_EN));
  assert(levels[SX126X_POWER_EN]==LOW);
  assert(events[lastWrite(SX126X_POWER_EN)].value==LOW);
  assert(events[lastMode(SX126X_POWER_EN)].value==OUTPUT);
#if ENV_INCLUDE_GPS
  assert((first("gps-uart-end")<events.size())==uart_started);
  if(uart_started) assert(first("gps-off")<first("gps-uart-end"));
  for(int pin:{PIN_GPS_TX,PIN_GPS_RX}) {
    assert(lastMode(pin)<events.size());
    assert(events[lastMode(pin)].value==INPUT);
    assert(first("gps-off")<lastMode(pin));
  }
#else
  (void)uart_started;
  assert(first("gps-uart-end")==events.size());
  assert(lastMode(PIN_GPS_TX)==events.size());
  assert(lastMode(PIN_GPS_RX)==events.size());
#endif
  assert(first("gps-off")<first("wire-end"));
  for(int pin:{PIN_WIRE_SDA,PIN_WIRE_SCL}) {
    assert(first("wire-end")<lastMode(pin));
    assert(events[lastMode(pin)].value==INPUT);
    assert(lastMode(pin)<lastWrite(WB_IO2));
  }
#ifdef RAK_EXTERNAL_RADIO
  assert(!SPI.begun&&!SPI1.begun);
  assert(first("radio-off")<first("spi-end"));
  assert(first("spi1-begin")<first("spi1-end"));
  for(int pin:{P_LORA_NSS,P_LORA_RESET,P_LORA_SCLK,P_LORA_MOSI,P_LORA_MISO}) {
    assert(first("spi-end")<lastMode(pin));
    assert(first("spi1-end")<lastMode(pin));
    assert(events[lastMode(pin)].value==INPUT);
    assert(lastMode(pin)<lastWrite(WB_IO2));
  }
#else
  assert(first("spi-end")==events.size());
  assert(first("spi1-begin")==events.size());
#endif
  // RAK19007/RAK13800 schematics: PoE 5V enters VCC_IN independently of Q7's
  // 3V3_S switch. A compiled Ethernet feature must not hold that switch on.
  assert(levels[WB_IO2]==LOW);
  assert(first("gps-off")<lastWrite(WB_IO2));
  assert(lastWrite(SX126X_POWER_EN)<lastWrite(WB_IO2));
#if ENV_INCLUDE_GPS
  assert(lastMode(PIN_GPS_TX)<lastWrite(WB_IO2));
  assert(lastMode(PIN_GPS_RX)<lastWrite(WB_IO2));
#endif
  verify(false,true,exit_event);
}
int main() {
  @BOARD@ board;
  assert(mesh::nrf52::registerTerminalEthernetShutdown(stopEthernet,nullptr));
  for(bool ble_started:{false,true}) for(bool connected:{false,true})
  for(bool softdevice:{false,true}) for(bool uart_started:{false,true}) {
    for(unsigned mode=0;mode<4;++mode) {
      prepare(ble_started,connected,softdevice);
      // Start with both power gates asserted, matching a fully running RAK.
      levels[WB_IO2]=levels[SX126X_POWER_EN]=HIGH;
      Serial1.begun=uart_started;SPI.begun=false;SPI1.begun=false;
      try {
        if(mode==0) board.shutdownPeripherals();
        if(mode==1) board.powerOff();
        if(mode==2) board.enterDeepSleep(0);
        if(mode==3) board.enterDeepSleep(10);
      } catch(const Exit&) {}
      const char* exit_event=mode==0?nullptr:(mode==3?"timed-sleep":"systemoff");
      verifyRakShutdown(uart_started,exit_event);
      // Repeated terminal shutdown must not end an already stopped UART.
      if(mode==0) { board.shutdownPeripherals();verifyRakShutdown(uart_started,nullptr); }
    }
  }
  // A vendor DHCP call may outlive the bounded Ethernet stop. Do not remove
  // the still-live bus supply (or remap RAK3401's shared SPI pins) underneath it.
  prepare(false,false,true);Serial1.begun=false;
  levels[WB_IO2]=HIGH;ethernet_can_stop=false;
  board.shutdownPeripherals();
  assert(levels[WB_IO2]==HIGH&&lastWrite(WB_IO2)==events.size());
  assert(first("ethernet-stop")<first("radio-off"));
  assert(first("spi-end")==events.size()&&first("spi1-begin")==events.size());
#ifdef RAK_EXTERNAL_RADIO
  assert(first("spi")==events.size()&&first("radio-off")==events.size());
  for(int pin:{3,29,30,26,21,4}) {
    assert(lastMode(pin)==events.size()&&lastWrite(pin)==events.size());
  }
#endif
  ethernet_can_stop=true;
  // Frozen storage/unknown discovery retains power and the NOR's driven
  // CS31/header15/16/17 pads; do not reinterpret those pads as a GPS UART.
  prepare(false,false,true);Serial1.begun=true;sensors.storage_safe=false;
  levels[WB_IO2]=levels[31]=levels[15]=levels[16]=levels[17]=HIGH;
  board.shutdownPeripherals();
  assert(levels[WB_IO2]==HIGH&&lastWrite(WB_IO2)==events.size());
  assert(first("gps-uart-end")==events.size()&&Serial1.begun);
  for(int pin:{15,16,17,31}) {
    assert(levels[pin]==HIGH&&lastMode(pin)==events.size()&&lastWrite(pin)==events.size());
  }
  assert(first("spi-end")==events.size()&&first("spi1-begin")==events.size());
  sensors.storage_safe=true;
  // Boot protection intentionally bypasses normal SPI/GPS shutdown: the
  // application peripherals may not have begun. Preserve that established path.
  for(uint8_t reason:{SHUTDOWN_REASON_LOW_VOLTAGE,SHUTDOWN_REASON_BOOT_PROTECT}) {
    prepare(false,false,true);Serial1.begun=false;
    try { board.initiateShutdown(reason); } catch(const Exit&) {}
    assert(first("spi")==events.size()&&first("radio-off")==events.size());
    assert(first("gps-uart-end")==events.size());
    assert(first("voltage-wake")<first("systemoff"));
    assert(levels[SX126X_POWER_EN]==LOW);
  }
}
'''.replace("@BOARD@", board)
    return source


class RakShutdownPowerTests(unittest.TestCase):
    def test_explicit_poweroff_and_deep_sleep_stop_rails_after_common_shutdown(self):
        for variant, board, enable_pin in BOARDS:
            with self.subTest(board=board):
                compile_source(self, source_for(variant, board, enable_pin), ("ENV_INCLUDE_GPS=1",))

    def test_ethernet_build_aliases_do_not_hold_the_switched_rail_on(self):
        for variant, board, enable_pin in BOARDS:
            for macro in POE_MACROS:
                with self.subTest(board=board, macro=macro):
                    compile_source(self, source_for(variant, board, enable_pin),
                                   ("ENV_INCLUDE_GPS=1", macro+"=1"))

    def test_no_gps_builds_do_not_touch_a_possible_serial1_owner(self):
        for variant, board, enable_pin in BOARDS:
            with self.subTest(board=board):
                compile_source(self, source_for(variant, board, enable_pin), ("ENV_INCLUDE_GPS=0",))

    def test_original_inherited_shutdown_negative_control_keeps_power_on(self):
        for variant, board, enable_pin in BOARDS:
            with self.subTest(board=board):
                compile_source(self, source_for(variant, board, enable_pin, mutation="no-board-override"),
                               ("ENV_INCLUDE_GPS=1",), expect_success=False)

    def test_ethernet_compile_guard_negative_control_leaves_sensors_powered(self):
        for variant, board, enable_pin in BOARDS:
            with self.subTest(board=board):
                compile_source(self, source_for(variant, board, enable_pin, mutation="ethernet-keeps-rail-on"),
                               ("ENV_INCLUDE_GPS=1", "ETHERNET_ENABLED=1"), expect_success=False)

    def test_i2c_shutdown_negative_control_leaves_internal_pulls_and_controller(self):
        for variant, board, enable_pin in BOARDS:
            with self.subTest(board=board):
                compile_source(self, source_for(variant, board, enable_pin, mutation="wire-left-active"),
                               ("ENV_INCLUDE_GPS=1",), expect_success=False)

    def test_external_radio_shutdown_must_release_spi_owner_and_chip_select(self):
        variant, board, enable_pin = BOARDS[1]
        for mutation in ("spi1-ended-unstarted", "external-radio-pad-left-driven"):
            with self.subTest(mutation=mutation):
                compile_source(self, source_for(variant, board, enable_pin, mutation=mutation),
                               ("ENV_INCLUDE_GPS=1",), expect_success=False)

    def test_storage_hold_negative_control_cuts_recovery_power_and_header_pads(self):
        for variant, board, enable_pin in BOARDS:
            with self.subTest(board=board):
                compile_source(self, source_for(variant, board, enable_pin, mutation="ignore-storage-hold"),
                               ("ENV_INCLUDE_GPS=1",), expect_success=False)

    def test_no_ordinary_sleep_or_runtime_shared_power_policy_override(self):
        for variant, board, _ in BOARDS:
            header = (ROOT/f"variants/{variant}/{board}.h").read_text()
            self.assertNotIn("void sleep(", header)
            self.assertNotIn("void enterDeepSleep(", header)
            source = (ROOT/f"variants/{variant}/{board}.cpp").read_text()
            protective = extract_braced(source, f"void {board}::initiateShutdown(uint8_t reason)")
            self.assertNotIn("shutdownPeripherals", protective)
            self.assertNotIn("Serial1", protective)


if __name__ == "__main__":
    unittest.main()
