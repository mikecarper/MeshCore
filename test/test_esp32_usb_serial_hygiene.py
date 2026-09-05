#!/usr/bin/env python3

import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text()


class Esp32UsbSerialHygieneTest(unittest.TestCase):
    def test_operational_wifi_diagnostics_use_runtime_logging_port(self):
        for relative in (
            "src/helpers/ESP32Board.cpp",
            "src/helpers/CompanionMqttSetupPortal.cpp",
            "src/helpers/JWTHelper.cpp",
            "src/helpers/bridges/MQTTBridge.cpp",
            "src/helpers/esp32/WiFiOtaSeeder.cpp",
            "src/helpers/esp32/WebConfigServer.cpp",
            "src/helpers/WiFiSetupPortal.cpp",
        ):
            text = source(relative)
            self.assertIn("UsbLogging.h", text, relative)
            self.assertNotRegex(
                text,
                r"\bSerial\.(?:print|println|printf|write)\s*\(",
                relative,
            )
            self.assertIn("mesh::usbLoggingPort()", text, relative)

    def test_v4_companion_uses_usb_serial_jtag_mode(self):
        platformio = source("variants/heltec_v4/platformio.ini")
        base = platformio[
            platformio.index("[Heltec_lora32_v4]") :
            platformio.index("[heltec_v4_oled]")
        ]
        self.assertIn("-D ARDUINO_USB_MODE=1", base)
        self.assertNotIn("ARDUINO_USB_MODE=0", base)

        board = source("boards/heltec_v4.json")
        self.assertIn('"-DARDUINO_USB_CDC_ON_BOOT=1"', board)
        self.assertIn('"-DARDUINO_USB_MODE=1"', board)

    def test_hwcdc_reply_route_has_completed_frame_grace(self):
        main = source("examples/companion_radio/main.cpp")
        start = main.index(
            "// The ESP32 USB-Serial-JTAG peripheral (HWCDC)"
        )
        hwcdc = main[start : main.index("#elif", start)]

        serial_begin = main.index("Serial.begin(115200);")
        prepare = main.index("mesh::prepareUsbLoggingPort();")
        logging_begin = main.index("mesh::beginUsbLoggingPort();")
        self.assertLess(prepare, serial_begin)
        self.assertLess(serial_begin, logging_begin)

        self.assertIn("board.isUsbHostConnected()", hwcdc)
        self.assertIn("usb_serial_interface.hasReceivedFrame()", hwcdc)
        self.assertIn("USB_FRAME_REPLY_GRACE_MS", hwcdc)
        self.assertIn("USB_HOST_LOSS_EDGE_MS", hwcdc)
        self.assertIn("USB_HOST_LOSS_GRACE_MS", hwcdc)
        self.assertIn("USB_CLIENT_IDLE_TIMEOUT", hwcdc)
        self.assertIn("getCompletedFrameCount()", hwcdc)
        self.assertIn(
            "the_mesh.hasFiniteDelayedReplyForRoute(&usb_serial_interface)",
            hwcdc,
        )
        self.assertNotIn("hasSerialOperationForRoute", hwcdc)
        self.assertNotIn("isReplyRouteLockedFor", hwcdc)
        self.assertNotIn("hasPendingWork()", hwcdc)
        self.assertNotIn("_iter_started", hwcdc)
        self.assertNotRegex(hwcdc, r"\(bool\)\s*Serial")

    def test_finite_usb_lease_covers_only_bounded_reply_families(self):
        mesh = source("examples/companion_radio/MyMesh.cpp")
        start = mesh.index("bool MyMesh::hasFiniteDelayedReplyForRoute(")
        body = mesh[start : mesh.index("void MyMesh::servicePendingSerialReply()", start)]
        self.assertIn("pending_serial_reply_route == route", body)
        self.assertIn("command_radio_reply_route == route", body)
        self.assertIn("binary_trace_reply_route == route", body)
        self.assertIn("expected_ack_table[i].reply_route == route", body)
        self.assertNotIn("_iter_started", body)
        self.assertNotIn("lockReplyRoute", body)
        self.assertNotIn("hasPendingWork", body)
        self.assertNotIn("sign_data_reply_route", body)

    def test_hwcdc_sustained_loss_resets_session_state(self):
        main = source("examples/companion_radio/main.cpp")
        start = main.index("static void serviceUsbTerminalHostSessionReset()")
        service = main[start : main.index("static void serviceUsbTerminal()", start)]
        reset_start = main.index("static void resetUsbTerminalHostSession(")
        reset = main[reset_start:start]

        self.assertIn("takeSustainedHostLoss()", service)
        self.assertIn("takeHostLossEdge()", service)
        self.assertIn("mesh::takeUsbTerminalSessionReset()", service)
        self.assertIn(
            "hardware_bus_reset || physical_host_loss || sustained_host_loss",
            service,
        )
        self.assertIn("usb_terminal_host_reset_completion_pending", service)
        self.assertIn("USB_TRANSPORT_RESET_RETRY_MS", service)
        self.assertIn("resetUsbTerminalHostSession(true);", service)
        self.assertIn("cancelUsbSerialOperations();", reset)
        self.assertIn("usb_serial_interface.resetSessionState();", reset)
        self.assertIn("if (!mesh::resetUsbCompanionTransport())", reset)
        self.assertIn("usb_terminal_host_reset_completion_pending = true", reset)
        self.assertNotIn("board.reboot()", reset)
        self.assertIn("if (preserve_ascii_terminal)", reset)
        self.assertIn("the_mesh.resetTerminalSession();", reset)

        logging = source("src/helpers/UsbLogging.cpp")
        helper_start = logging.index("static bool detachEsp32HwcdcPads()")
        purge_start = logging.index("bool resetUsbCompanionTransport()")
        helper = logging[helper_start:purge_start]
        purge = logging[purge_start : logging.index(
            "Stream& usbLoggingPort()", purge_start
        )]
        self.assertIn(
            "esp32_hwcdc_access_generation.fetch_add(", purge
        )
        self.assertIn("setPlatformDebugOutputEnabled(false);", purge)
        self.assertIn("detachEsp32HwcdcPads()", purge)
        self.assertIn("delay(10);", purge)
        self.assertIn("tryRunExclusive(", purge)
        self.assertIn("Serial.flush();", helper)
        self.assertIn("flush_attempts >= 2", helper)
        self.assertIn("Serial.availableForWrite()", helper)
        self.assertIn("esp32_hwcdc_tx_buffer_capacity.load(", helper)
        self.assertNotIn("Serial.setTxBufferSize(", helper)
        self.assertIn("while (Serial.read() >= 0)", helper)
        self.assertIn(
            "setPlatformDebugOutputEnabled(isUsbLoggingEnabled());", purge
        )
        self.assertIn(
            "esp32_hwcdc_self_reset_guard.expectSelfResetBurst()", purge
        )
        self.assertLess(
            purge.index("esp32_hwcdc_self_reset_guard.expectSelfResetBurst()"),
            purge.index("restoreEsp32HwcdcPads("),
        )
        self.assertIn(
            "esp32_hwcdc_allowed_generation.store(", purge
        )
        self.assertNotIn("setUsbLoggingEnabled(false);", purge)
        self.assertNotIn("Serial.end();", purge)
        self.assertNotIn("Serial.begin(", purge)

        setup = main[main.index("void setup()") : main.index("board.begin();")]
        self.assertIn("mesh::prepareUsbLoggingPort();", setup)
        prepare_start = logging.index("void prepareUsbLoggingPort()")
        prepare = logging[prepare_start : logging.index(
            "void beginUsbLoggingPort()", prepare_start
        )]
        self.assertIn("static const size_t usb_tx_sizes[]", prepare)
        self.assertIn("Serial.setTxBufferSize(candidate)", prepare)
        self.assertIn("Serial.setTxTimeoutMs(5);", prepare)
        begin_start = logging.index("void beginUsbLoggingPort()")
        begin = logging[begin_start : logging.index(
            "void serviceUsbLoggingPort()", begin_start
        )]
        self.assertIn("Serial.availableForWrite()", begin)
        self.assertIn("setUsbCompanionTxBufferCapacity(", begin)

        debug_start = logging.index(
            "static void setPlatformDebugOutputEnabled(bool enabled)"
        )
        debug = logging[debug_start : logging.index(
            "#if defined(MESH_DUAL_CDC_LOGGING)", debug_start
        )]
        self.assertIn("MESH_ESP32_HWCDC_SESSION_GUARD", debug)
        self.assertIn("Serial.setDebugOutput(false);", debug)
        self.assertNotIn("&& canAccessEsp32Hwcdc", debug)

        companion_start = logging.index("Stream& usbCompanionPort()")
        companion = logging[companion_start : logging.index(
            "Stream& usbMotaPort()", companion_start
        )]
        self.assertIn("return guarded_esp32_hwcdc_port;", companion)
        mota_port_start = logging.index("Stream& usbMotaPort()")
        mota_port = logging[mota_port_start : logging.index(
            "Stream& usbTerminalPort()", mota_port_start
        )]
        self.assertIn(
            "return guarded_esp32_hwcdc_mota_port;", mota_port
        )
        self.assertIn(
            "AtomicWholeRecordNonBlockingStream<11>", logging
        )

        ota_context = source("src/helpers/ota/OtaContext.h")
        ota_stream = ota_context[
            ota_context.index("#ifndef OTA_FOLDER_SERIAL_STREAM") :
            ota_context.index("#ifndef OTA_FOLDER_SERIAL_BAUD")
        ]
        self.assertIn("MESH_ESP32_USB_CONSOLE_COOPERATIVE", ota_stream)
        self.assertIn("::mesh::usbMotaPort()", ota_stream)

        self.assertIn(
            "Serial.onEvent(ARDUINO_HW_CDC_ANY_EVENT,", logging
        )
        self.assertIn(
            "esp32_hwcdc_bus_reset_generation.fetch_add(", logging
        )
        self.assertIn(
            "esp32_hwcdc_access_generation.fetch_add(", logging
        )
        self.assertIn(
            "esp32_hwcdc_self_reset_guard.shouldIgnoreBusReset()", logging
        )
        self.assertNotIn("esp32_hwcdc_expected_self_reset.exchange(", logging)
        self.assertIn(
            "esp32_hwcdc_self_reset_guard.notePostCleanActivity()", logging
        )
        take_start = logging.index("bool takeUsbTerminalSessionReset()")
        take = logging[take_start : logging.index(
            "bool tryCompleteUsbTerminalSessionReset()", take_start
        )]
        self.assertIn("MESH_ESP32_HWCDC_SESSION_GUARD", take)
        self.assertIn("esp32_hwcdc_bus_reset_generation.load(", take)

        mota_start = main.index("static void serviceUsbMota()")
        mota_end = main.index(
            "static bool usb_terminal_host_reset_completion_pending", mota_start
        )
        mota = main[mota_start:mota_end]
        self.assertIn("#if !(defined(ESP32)", mota)
        self.assertIn("isUsbTerminalDataConnected()", mota)

    def test_hwcdc_pad_detach_supports_idf4_and_idf5(self):
        logging = source("src/helpers/UsbLogging.cpp")
        wrappers = logging[
            logging.index("static bool detachEsp32HwcdcPads()") :
            logging.index("static void purgeEsp32HwcdcQueues(")
        ]
        self.assertIn('#include "esp_idf_version.h"', logging)
        self.assertEqual(wrappers.count("#if ESP_IDF_VERSION_MAJOR >= 5"), 2)
        self.assertIn("usb_serial_jtag_ll_phy_is_pad_enabled()", wrappers)
        self.assertIn("usb_serial_jtag_ll_phy_enable_pad(false)", wrappers)
        self.assertIn("usb_serial_jtag_ll_phy_enable_pad(enabled)", wrappers)
        self.assertIn("usb_serial_jtag_ll_pad_backup_and_disable()", wrappers)
        self.assertIn("usb_serial_jtag_ll_enable_pad(enabled)", wrappers)

        reset_start = logging.index("bool resetUsbCompanionTransport()")
        reset = logging[reset_start : logging.index(
            "Stream& usbLoggingPort()", reset_start
        )]
        self.assertNotIn("usb_serial_jtag_ll_pad_backup_and_disable()", reset)
        self.assertNotIn("usb_serial_jtag_ll_phy_enable_pad(", reset)

        c6 = source("variants/m5stack_unit_c6l/platformio.ini")
        usb = c6[c6.index("[env:M5Stack_Unit_C6L_companion_radio_usb]") :]
        self.assertIn("-D ARDUINO_USB_MODE=1", usb)
        self.assertIn("-D ENABLE_USB_INTERFACE", usb)

    def test_hwcdc_host_presence_uses_sof_signal(self):
        board = source("src/helpers/ESP32Board.h")
        start = board.index("bool isUsbHostConnected() override")
        host_check = board[start : board.index("void setInhibitSleep", start)]

        self.assertIn("ARDUINO_USB_MODE", host_check)
        self.assertIn("return Serial.isPlugged();", host_check)

    def test_hwcdc_retries_tx_kick_after_transient_sof_loss(self):
        logging = source("src/helpers/UsbLogging.cpp")
        self.assertIn(
            "static std::atomic<bool> esp32_hwcdc_tx_kick_pending{false};",
            logging,
        )
        write_start = logging.index(
            "size_t write(const uint8_t* data, size_t size) override"
        )
        write_end = logging.index("private:", write_start)
        guarded_write = logging[write_start:write_end]
        self.assertIn(
            "esp32_hwcdc_tx_kick_pending.store(true", guarded_write
        )
        self.assertIn("return Serial.write(data, size);", guarded_write)

        kick_start = logging.index(
            "static void serviceEsp32HwcdcTxKickExclusive"
        )
        kick_end = logging.index("#endif", kick_start)
        kick = logging[kick_start:kick_end]
        self.assertIn("Serial.availableForWrite()", kick)
        self.assertIn("esp32_hwcdc_tx_buffer_capacity.load(", kick)
        self.assertIn("canAccessEsp32Hwcdc(nullptr)", kick)
        self.assertIn("Serial.isPlugged()", kick)
        self.assertIn("usb_serial_jtag_ll_txfifo_flush();", kick)
        self.assertIn("USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY", kick)
        self.assertIn("portENTER_CRITICAL(&esp32_hwcdc_session_mux)", kick)
        self.assertIn("tryRunExclusive(", kick)

        event_start = logging.index("static void handleEsp32HwcdcEvent")
        event_end = logging.index(
            "class Esp32HwcdcSessionStream", event_start
        )
        event = logging[event_start:event_end]
        self.assertIn(
            "esp32_hwcdc_tx_kick_pending.store(false", event
        )
        self.assertIn("usb_serial_jtag_ll_disable_intr_mask(", event)
        self.assertNotIn(
            "if (event_id == ARDUINO_HW_CDC_TX_EVENT) {", event
        )

        service_start = logging.index("void serviceUsbTerminalPort()")
        service_end = logging.index(
            "void discardUsbTerminalOutput()", service_start
        )
        service = logging[service_start:service_end]
        self.assertIn("MESH_ESP32_HWCDC_SESSION_GUARD", service)
        self.assertIn("serviceEsp32HwcdcTxKick();", service)

    def test_mqtt_ntp_detail_never_bypasses_logging_mode(self):
        text = source("src/helpers/bridges/MQTTBridge.cpp")
        self.assertIn(
            "if (verbose && mesh::isUsbLoggingEnabled())", text
        )
        self.assertIn("Stream& output = mesh::usbLoggingPort();", text)

    def test_framework_diagnostics_follow_same_runtime_gate(self):
        text = source("src/helpers/UsbLogging.cpp")
        self.assertIn("Serial.setDebugOutput(enabled);", text)

        setter = text[
            text.index("void setUsbLoggingEnabled(") :
            text.index("bool saveUsbLoggingBootPreference(")
        ]
        self.assertIn("setPlatformDebugOutputEnabled(enabled);", setter)

        begin = text[
            text.index("void beginUsbLoggingPort(") :
            text.index("void serviceUsbLoggingPort(")
        ]
        self.assertIn(
            "setPlatformDebugOutputEnabled(isUsbLoggingEnabled());", begin
        )

    def test_expected_fresh_nvs_state_is_silent(self):
        wifi_setup = source("src/helpers/WiFiSetupPortal.cpp")
        webconfig = source("src/helpers/esp32/WebConfigServer.cpp")
        radio_policy = source("src/helpers/esp32/WiFiRadioPolicy.h")
        mqtt_setup = source("src/helpers/CompanionMqttSetupPortal.cpp")

        for text in (wifi_setup, webconfig, radio_policy, mqtt_setup):
            self.assertNotRegex(
                text,
                r'\.begin\("mesh-(?:wifi|webui|mqtt)",\s*true\)',
            )
        self.assertNotIn("nvs.begin(NVS_NAMESPACE, true)", mqtt_setup)

        for text in (wifi_setup, webconfig):
            self.assertIn('isKey("ssid")', text)
            self.assertIn('isKey("password")', text)
        self.assertIn('isKey("enabled")', webconfig)
        self.assertIn('isKey("cli")', webconfig)
        self.assertIn('isKey("powersave")', webconfig)
        self.assertIn('isKey("espnow_ch")', radio_policy)
        self.assertIn("nvs.isKey(NVS_VERSION_KEY)", mqtt_setup)
        self.assertIn("nvs.isKey(NVS_PREFS_KEY)", mqtt_setup)

    def test_indicator_reports_specific_hardware(self):
        header = source("variants/sensecap_indicator-espnow/target.h")
        implementation = source("variants/sensecap_indicator-espnow/target.cpp")
        self.assertIn(
            "class SenseCapIndicatorBoard : public ESP32Board", header
        )
        self.assertIn('return "Seeed SenseCAP Indicator";', header)
        self.assertIn("extern SenseCapIndicatorBoard board;", header)
        self.assertIn("SenseCapIndicatorBoard board;", implementation)


if __name__ == "__main__":
    unittest.main()
