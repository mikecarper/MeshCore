#!/usr/bin/env python3
"""Static safety contracts for nRF52840 variants with incompatible layouts."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def ini_section(source: str, name: str) -> str:
    """Return one PlatformIO section without resolving its inheritance."""
    match = re.search(
        rf"^\[{re.escape(name)}\]\s*$\n(?P<body>.*?)(?=^\[|\Z)",
        source,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing PlatformIO section [{name}]")
    return match.group("body")


def hex_define(source: str, name: str) -> int:
    """Read a simple hexadecimal preprocessor address definition."""
    match = re.search(
        rf"^\s*#define\s+{re.escape(name)}\s+(0x[0-9A-Fa-f]+)\b",
        source,
        re.MULTILINE,
    )
    if match is None:
        raise AssertionError(f"missing hexadecimal definition for {name}")
    return int(match.group(1), 16)


class Nrf52VariantContractsTest(unittest.TestCase):
    def test_rak3401_wisblock_i2c_aliases_are_complete(self) -> None:
        variant = (ROOT / "variants/rak3401/variant.h").read_text()

        self.assertRegex(variant, r"#define\s+WB_I2C1_SDA\s+\(13\)")
        self.assertRegex(variant, r"#define\s+WB_I2C1_SCL\s+\(14\)")
        self.assertRegex(
            variant, r"#define\s+PIN_WIRE_SDA\s+\(WB_I2C1_SDA\)"
        )
        self.assertRegex(
            variant, r"#define\s+PIN_WIRE_SCL\s+\(WB_I2C1_SCL\)"
        )
        self.assertRegex(variant, r"#define\s+PIN_BOARD_SDA\s+PIN_WIRE_SDA")
        self.assertRegex(variant, r"#define\s+PIN_BOARD_SCL\s+PIN_WIRE_SCL")

    def test_rak3401_gps_uart_is_cross_connected(self) -> None:
        variant = (ROOT / "variants/rak3401/variant.h").read_text()

        self.assertRegex(variant, r"#define\s+PIN_SERIAL1_RX\s+\(15\)")
        self.assertRegex(variant, r"#define\s+PIN_SERIAL1_TX\s+\(16\)")
        self.assertRegex(variant, r"#define\s+PIN_GPS_RX\s+PIN_SERIAL1_TX")
        self.assertRegex(variant, r"#define\s+PIN_GPS_TX\s+PIN_SERIAL1_RX")

    def test_rak3401_bsec_opt_in_requires_hard_float_recipe(self) -> None:
        recipe = (ROOT / "variants/rak3401/platformio.ini").read_text()
        shared = (ROOT / "platformio.ini").read_text()
        sensor_base = re.search(
            r"^\[sensor_base\]\s*$\n(?P<body>.*?)(?=^\[|\Z)",
            shared,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(sensor_base)
        effective_recipe = sensor_base.group("body") + recipe

        if re.search(
            r"^\s*-D\s*ENV_INCLUDE_BME680_BSEC(?:=1)?\s*$",
            effective_recipe,
            re.MULTILINE,
        ):
            self.assertIn("fix_bsec_lib.py", recipe)
            self.assertIn("BSEC Software Library", recipe)

    def test_rak3401_reduced_profile_names_its_narrow_sensor_trim(self) -> None:
        recipe = (ROOT / "variants/rak3401/platformio.ini").read_text()
        shared = (ROOT / "platformio.ini").read_text()
        build_script = (ROOT / "build.sh").read_text()

        expected_ina = {
            "ENV_INCLUDE_INA219",
            "ENV_INCLUDE_INA226",
            "ENV_INCLUDE_INA260",
            "ENV_INCLUDE_INA3221",
        }
        voltage_monitor_section = ini_section(shared, "i2c_voltage_monitor_base")
        enabled_monitors = set(
            re.findall(
                r"-D\s+(ENV_INCLUDE_[A-Z0-9_]+)=1\b",
                voltage_monitor_section,
            )
        )
        self.assertEqual(enabled_monitors, expected_ina)

        reduced_sensor_section = ini_section(
            shared, "nrf52_reduced_sensors_keep_ina_gps"
        )
        self.assertIn(
            "${nrf52_external_environmental_sensor_trim.build_flags}",
            reduced_sensor_section,
        )
        self.assertIn(
            "${i2c_voltage_monitor_base.build_flags}", reduced_sensor_section
        )
        self.assertNotRegex(reduced_sensor_section, r"-U\s*ENV_INCLUDE_GPS\b")

        reduced_target = ini_section(
            recipe, "env:RAK_3401_repeater_lora_ota_no_external_sensors"
        )
        self.assertIn(
            "${nrf52_reduced_sensors_keep_ina_gps.build_flags}", reduced_target
        )

        monitor_flags = re.search(
            r'^\s*local voltage_monitor_flags="(?P<flags>[^"]+)"',
            build_script,
            re.MULTILINE,
        )
        self.assertIsNotNone(monitor_flags)
        self.assertEqual(set(monitor_flags.group("flags").split()), expected_ina)

        self.assertIn(
            "selected optional environmental/ranging drivers omitted",
            build_script,
        )
        self.assertIn("board display/RTC/GPS retained", build_script)
        self.assertIn("GPS conflicts with RS-232 on Serial1", build_script)
        self.assertNotIn("sensors.external omitted except", build_script)
        self.assertIn("environmental telemetry sensors", recipe)
        self.assertIn("both RAK12500 I2C and RAK12501 UART GPS paths", recipe)

    def test_rak3401_reduced_profile_retains_non_ina_i2c_peripherals(self) -> None:
        recipe = (ROOT / "variants/rak3401/platformio.ini").read_text()
        target = (ROOT / "variants/rak3401/target.cpp").read_text()
        sensors = (
            ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp"
        ).read_text()
        rtc = (ROOT / "src/helpers/AutoDiscoverRTCClock.cpp").read_text()
        display = (ROOT / "src/helpers/ui/SSD1306Display.h").read_text()

        reduced_target = ini_section(
            recipe, "env:RAK_3401_repeater_lora_ota_no_external_sensors"
        )
        self.assertRegex(reduced_target, r"-D\s+DISPLAY_CLASS=SSD1306Display\b")
        self.assertIn("SparkFun u-blox GNSS Arduino Library", reduced_target)
        self.assertIn("AutoDiscoverRTCClock rtc_clock", target)
        self.assertIn("rtc_clock.begin(Wire)", target)
        self.assertIn("MicroNMEALocationProvider(Serial1, &rtc_clock)", target)
        self.assertIn(
            "ublox_GNSS.begin(Wire, gps_i2c_address)", sensors
        )
        self.assertIn(
            "shouldSkipSensorAtClaimedGpsAddress(", sensors
        )
        self.assertIn("i2cGPSFlag, TELEM_WIRE == &Wire", sensors)
        self.assertIn(
            "probeIna3221Identity(&Wire, gps_i2c_address)", sensors
        )
        self.assertIn("static_cast<uint32_t>(TELEM_RAK12500_ADDRESS)", sensors)
        self.assertIn("I2cRegisterProbeStatus::Inconclusive", sensors)
        self.assertIn("serialHasValidGpsSentence(Serial1", sensors)
        self.assertNotIn("else if (Serial1.available())", sensors)

        self.assertEqual(hex_define(display, "DISPLAY_ADDRESS"), 0x3C)
        self.assertEqual(
            {
                "DS3231": hex_define(rtc, "DS3231_ADDRESS"),
                "RV3028": hex_define(rtc, "RV3028_ADDRESS"),
                "PCF8563": hex_define(rtc, "PCF8563_ADDRESS"),
                "RX8130CE": hex_define(rtc, "RX8130CE_ADDRESS"),
            },
            {
                "DS3231": 0x68,
                "RV3028": 0x52,
                "PCF8563": 0x51,
                "RX8130CE": 0x32,
            },
        )

    def test_i2c_recovery_uses_board_remapped_primary_pins(self) -> None:
        recipe = (ROOT / "variants/promicro/platformio.ini").read_text()
        board = (ROOT / "variants/promicro/PromicroBoard.cpp").read_text()
        sensors = (
            ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp"
        ).read_text()

        self.assertRegex(recipe, r"-D\s+PIN_BOARD_SDA=8\b")
        self.assertRegex(recipe, r"-D\s+PIN_BOARD_SCL=7\b")
        self.assertIn("Wire.setPins(PIN_BOARD_SDA, PIN_BOARD_SCL)", board)
        board_pin_branch = sensors.index(
            "defined(PIN_BOARD_SDA) && defined(PIN_BOARD_SCL)"
        )
        variant_pin_branch = sensors.index(
            "defined(PIN_WIRE_SDA) && defined(PIN_WIRE_SCL)"
        )
        self.assertLess(board_pin_branch, variant_pin_branch)
        self.assertIn("Wire.setPins(static_cast<uint8_t>(sda)", sensors)

    def test_rak3401_rak12500_ina3221_address_collision_is_explicit(self) -> None:
        recipe = (ROOT / "variants/rak3401/platformio.ini").read_text()
        sensors = (
            ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp"
        ).read_text()

        gps_address = hex_define(sensors, "TELEM_RAK12500_ADDRESS")
        ina3221_address = hex_define(sensors, "TELEM_INA3221_ADDRESS")
        self.assertEqual(gps_address, 0x42)
        self.assertEqual(ina3221_address, 0x42)
        self.assertEqual(gps_address, ina3221_address)
        self.assertIn(
            "RAK12500 and the configured INA3221 both use I2C address 0x42",
            recipe,
        )
        self.assertIn("cannot be installed", recipe)
        self.assertIn("strap INA3221 A0 to SCL for 0x43", recipe)
        self.assertIn("-DTELEM_INA3221_ADDRESS=0x43", recipe)

    def test_rak4631_shared_sensor_rail_stays_enabled(self) -> None:
        recipe = (ROOT / "variants/rak4631/platformio.ini").read_text()
        board = (ROOT / "variants/rak4631/RAK4631Board.cpp").read_text()
        sensors = (
            ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp"
        ).read_text()

        self.assertRegex(recipe, r"-D\s+FORCE_GPS_ALIVE\b")
        self.assertIn("pinMode(WB_IO2, OUTPUT)", board)
        self.assertIn("digitalWrite(WB_IO2, HIGH)", board)
        self.assertIn("setRakGpsControl(ioPin, shared_power_rail, true)", sensors)
        self.assertIn("setRakGpsControl(ioPin, shared_power_rail, false)", sensors)

    def test_rak4631_ethernet_does_not_suppress_shared_rail_gps(self) -> None:
        recipe = (ROOT / "variants/rak4631/platformio.ini").read_text()
        sensors = (
            ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp"
        ).read_text()

        ethernet = ini_section(recipe, "env:RAK_4631_repeater_ethernet")
        self.assertRegex(ethernet, r"-D\s+ETHERNET_ENABLED=1\b")
        self.assertNotIn(
            "#if defined(ETHERNET_ENABLED) && defined(RAK_BOARD)", sensors
        )
        self.assertNotIn("if (ioPin == WB_IO2)", sensors)
        self.assertIn(
            "setRakGpsControl(ioPin, shared_power_rail, true)", sensors
        )

    def test_rak4631_runtime_bridge_arbitrates_only_uart_gps(self) -> None:
        recipe = (ROOT / "variants/rak4631/platformio.ini").read_text()
        mesh_header = (
            ROOT / "examples/simple_repeater/MyMesh.h"
        ).read_text()
        sensor_header = (
            ROOT / "src/helpers/sensors/EnvironmentSensorManager.h"
        ).read_text()
        sensors = (
            ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp"
        ).read_text()
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()

        begin = mesh_header[
            mesh_header.index("bool beginRS232Bridge()"):
            mesh_header.index("bool endRS232Bridge()")
        ]
        end = mesh_header[
            mesh_header.index("bool endRS232Bridge()"):
            mesh_header.index("bool setBridgeState(bool enable)")
        ]
        can_yield = sensors[
            sensors.index("bool EnvironmentSensorManager::gpsSerialTransportCanYield"):
            sensors.index("bool EnvironmentSensorManager::setGpsSerialTransportBlocked")
        ]
        block = sensors[
            sensors.index("bool EnvironmentSensorManager::setGpsSerialTransportBlocked"):
            sensors.index("#if ENV_INCLUDE_GPS || defined(ENV_INCLUDE_BME680_BSEC)")
        ]
        self.assertLess(
            begin.index("setGpsSerialTransportBlocked(selected_uart, true)"),
            begin.index("bridge->begin()"),
        )
        self.assertLess(
            begin.index("active_rs232_bridge_uart = selected_uart"),
            begin.index("bridge->begin()"),
        )
        failed_start = begin[
            begin.index("if (!bridge->isRunning())"):
            begin.index(
                "active_rs232_bridge_uart = selected_uart",
                begin.index("bridge->begin()"),
            )
        ]
        self.assertIn(
            "if (sensors.setGpsSerialTransportBlocked(selected_uart, false))",
            failed_start,
        )
        self.assertIn("active_rs232_bridge_uart = 0", failed_start)
        self.assertLess(
            begin.index("gpsSerialTransportMayConflict(selected_uart)"),
            begin.index("bridge->begin()"),
        )
        self.assertLess(
            end.index("if (bridge && bridge->isRunning())"),
            end.index("bridge->end()"),
        )
        self.assertLess(
            end.index("bridge->end()"),
            end.index("setGpsSerialTransportBlocked(released_uart, false)"),
        )
        self.assertIn("gpsUsesSerialUart(uint8_t uart) const override", sensor_header)
        self.assertIn(
            "gpsSerialTransportMayConflict(uint8_t uart) const override",
            sensor_header,
        )
        self.assertIn("-D WITH_RS232_BRIDGE_GPS_CONFLICT_UART=1", recipe)
        self.assertIn(
            "if (uart == WITH_RS232_BRIDGE_GPS_CONFLICT_UART) return true;",
            sensors,
        )
        self.assertIn(
            "return uart == 1 && gps_detected && gps_serial_transport", sensors
        )
        self.assertIn(
            "#if defined(RAK_WISBLOCK_GPS) && defined(FORCE_GPS_ALIVE)",
            can_yield,
        )
        forced_rak = can_yield[
            can_yield.index(
                "#if defined(RAK_WISBLOCK_GPS) && defined(FORCE_GPS_ALIVE)"
            ):
            can_yield.index("#elif defined(RAK_WISBLOCK_GPS)")
        ]
        self.assertIn("return false;", forced_rak)
        refusal = block.index(
            "if (blocked && !gpsSerialTransportCanYield(uart)) return false;"
        )
        for mutation in (
            "setGpsTelemetryTransportAvailable(false)",
            "gps_serial_transport_blocked = true",
            "stop_gps()",
            "Serial1.end()",
        ):
            self.assertLess(refusal, block.index(mutation))
        self.assertIn("setGpsTelemetryTransportAvailable(false)", sensors)
        self.assertIn("setGpsTelemetryTransportAvailable(true)", sensors)
        self.assertNotIn(
            "turn the RS232 bridge off or select another UART first", cli
        )
        self.assertNotIn("Error: turn GPS off or select another UART first", cli)

    def test_bridge_reports_runtime_state_and_rolls_back_failed_changes(self) -> None:
        implementation = (
            ROOT / "examples/simple_repeater/MyMesh.cpp"
        ).read_text()
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()

        self.assertIn("bool isBridgeRunning() const override", header)
        self.assertIn("if (isBridgeRunning()) reply_data[8] |= 0x01", implementation)
        self.assertIn("if (isBridgeRunning()) reply_data[8] |= 0x03", implementation)
        boot_failure = implementation[
            implementation.index("if (!bridge || !beginRS232Bridge())"):
            implementation.index("#else", implementation.index(
                "if (!bridge || !beginRS232Bridge())"
            ))
        ]
        self.assertIn("setBridgeState(false)", boot_failure)
        self.assertNotIn("_prefs.bridge_enabled", boot_failure)
        self.assertIn("parseOnOffStrict(&config[15], enable)", cli)
        self.assertIn("parseUnsignedIntegerStrict(&config[12], baud)", cli)
        self.assertIn("_prefs->bridge_baud = previous_baud", cli)
        self.assertIn("_prefs->bridge_uart = previous_uart", cli)
        self.assertIn("bridge state change failed; setting unchanged", cli)
        self.assertIn('configKeyEquals(config, "bridge.running")', cli)
        self.assertIn('_prefs->bridge_pkt_src ? "rx" : "tx"', cli)
        self.assertIn("!defined(RS232_BRIDGE_DEFAULT_ON)", cli)

    def test_default_on_rs232_profile_skips_default_off_tail_migration(self) -> None:
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        xiao = (ROOT / "variants/xiao_nrf52/platformio.ini").read_text()
        dedicated = ini_section(
            xiao, "env:solarxiao_30S_repeater_bridge_rs232"
        )

        self.assertIn("extends = env:solarxiao_30S_repeater", dedicated)
        self.assertRegex(dedicated, r"-D\s+RS232_BRIDGE_DEFAULT_ON=1\b")
        guard = (
            "#if defined(WITH_RS232_BRIDGE) && defined(RS232_BRIDGE_MERGED) \\\n"
            "    && !defined(RS232_BRIDGE_DEFAULT_ON)"
        )
        # Declaration, persisted-tail detection, and migration must share the
        # same guard. A broader use breaks dedicated default-on bridge builds.
        self.assertEqual(cli.count(guard), 3)
        self.assertEqual(cli.count("has_runtime_bridge_uart"), 3)

    def test_gat562_30s_gps_enable_and_buzzer_pins_are_distinct(self) -> None:
        recipe = (
            ROOT / "variants/gat562_30s_mesh_kit/platformio.ini"
        ).read_text()
        variant = (ROOT / "variants/gat562_30s_mesh_kit/variant.h").read_text()

        # GAT562 30S Mesh KIT V1.1 schematic: IO2 is nRF P1.01 (Arduino
        # GPIO 33) and drives the GPS supply; BEE_EN is P1.02 (GPIO 34).
        gps_pin = re.search(
            r"#define\s+PIN_GPS_EN\s+\((\d+)\)", variant
        )
        buzzer_pin = re.search(r"-D\s+PIN_BUZZER=(\d+)\b", recipe)
        self.assertIsNotNone(gps_pin)
        self.assertIsNotNone(buzzer_pin)
        self.assertEqual(int(gps_pin.group(1)), 33)
        self.assertEqual(int(buzzer_pin.group(1)), 34)
        self.assertNotEqual(gps_pin.group(1), buzzer_pin.group(1))

    def test_xiao_and_mesh_pocket_softdevice_layouts_are_not_interchangeable(
        self,
    ) -> None:
        xiao = (ROOT / "variants/xiao_nrf52/platformio.ini").read_text()
        mesh_pocket = (ROOT / "variants/mesh_pocket/platformio.ini").read_text()

        self.assertRegex(
            xiao,
            re.compile(
                r"^\[Xiao_nrf52\].*?^board_build\.ldscript\s*=\s*"
                r"boards/nrf52840_s140_v7\.ld$",
                re.MULTILINE | re.DOTALL,
            ),
        )
        self.assertRegex(
            mesh_pocket,
            re.compile(
                r"^\[Mesh_pocket\].*?^board_build\.ldscript\s*=\s*"
                r"boards/nrf52840_s140_v6\.ld$",
                re.MULTILINE | re.DOTALL,
            ),
        )


if __name__ == "__main__":
    unittest.main()
