#!/usr/bin/env python3

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ESP32_BOARD = ROOT / "src/helpers/ESP32Board.cpp"
INDICATOR_FONT = ROOT / "variants/sensecap_indicator-espnow/IndicatorFontClient.cpp"
OBSERVER_CLI = ROOT / "src/helpers/CommonCLI_Observer.cpp"
TLS_POLICY = ROOT / "src/helpers/esp32/TlsClockValidity.h"
SNTP_COORDINATOR = ROOT / "src/helpers/esp32/SntpOperationCoordinator.h"
MQTT_BRIDGE = ROOT / "src/helpers/bridges/MQTTBridge.h"
MQTT_BRIDGE_IMPL = ROOT / "src/helpers/bridges/MQTTBridge.cpp"
MQTT_POLICY = ROOT / "src/helpers/MQTTConnectionPolicy.h"


def source(path: Path) -> str:
    return path.read_text()


def section(text: str, start: str, end: str) -> str:
    first = text.index(start)
    return text[first:text.index(end, first)]


class TlsDownloadClockGateTest(unittest.TestCase):
    def test_outbound_secure_socket_inventory_has_no_unreviewed_path(self):
        # Keep this inventory deliberately small and explicit.  A new direct
        # WiFiClientSecure connect is a new certificate-validating network
        # boundary; adding one must also add its clock/proof contract here.
        runtime_roots = (ROOT / "src", ROOT / "examples", ROOT / "variants")
        secure_connect_files = set()
        for runtime_root in runtime_roots:
            for path in runtime_root.rglob("*.cpp"):
                implementation = source(path)
                if "WiFiClientSecure" in implementation and ".connect(" in implementation:
                    secure_connect_files.add(path.relative_to(ROOT).as_posix())

        self.assertEqual(
            secure_connect_files,
            {
                "src/helpers/CommonCLI_Observer.cpp",  # connect-only CA diagnostic
                "src/helpers/ESP32Board.cpp",  # manifest + firmware pull OTA
                "variants/sensecap_indicator-espnow/IndicatorFontClient.cpp",
            },
        )

        # These higher-level outbound HTTP clients can hide their TLS connect
        # from the inventory above.  None is currently used by device runtime
        # code; fail visibly if one is introduced without a matching audit.
        combined_runtime = "\n".join(
            source(path)
            for runtime_root in runtime_roots
            for path in runtime_root.rglob("*.cpp")
        )
        for helper in ("HTTPClient", "esp_https_ota", "esp_http_client"):
            self.assertNotIn(helper, combined_runtime)

    def test_shared_policy_uses_signed_time_and_requires_every_input(self):
        policy = source(TLS_POLICY)
        self.assertIn("constexpr bool timeIsValid(time_t now)", policy)
        self.assertIn("now >= kMinimumValidEpoch", policy)
        self.assertIn("fresh_proof && wifi_connected && timeIsValid(now)", policy)
        self.assertNotIn("timeIsValid(uint32_t now", policy)
        self.assertNotIn("proofIsValid(bool fresh_proof, bool wifi_connected,\n                            uint32_t now", policy)

    def test_pull_ota_awaits_fresh_ntp_before_the_https_sequence(self):
        board = source(ESP32_BOARD)
        prepare = section(
            board, "static bool ota_prepareTlsClock", "struct OtaHttpResponse"
        )
        implementation = section(
            board,
            "bool ESP32Board::otaFromManifestImpl",
            "#else\nbool ESP32Board::otaFromManifest",
        )

        self.assertIn(
            "ota_tls_fresh_ntp_received.store(false, std::memory_order_release)",
            prepare,
        )
        self.assertIn("sntp_set_time_sync_notification_cb(ota_noteNtpTime)", prepare)
        self.assertIn("sntp_set_sync_status(SNTP_SYNC_STATUS_RESET)", prepare)
        self.assertIn('configTime(0, 0, "time.cloudflare.com"', prepare)
        self.assertIn("const time_t now = time(nullptr)", prepare)
        self.assertIn("ota_tlsClockProofValid()", prepare)
        self.assertIn("OTA_NTP_SYNC_WAIT_MS", prepare)
        self.assertNotIn("(uint32_t)time(nullptr)", prepare)
        self.assertIn("OperationLease sntp_operation", prepare)
        self.assertIn("processWideCoordinator()", prepare)
        self.assertIn("if (!sntp_operation.tryAcquire())", prepare)
        self.assertNotIn("sntp_set_time_sync_notification_cb(nullptr)", prepare)

        gate = implementation.index(
            "if (!dry_run && !ota_prepareTlsClock(reply))"
        )
        manifest_client = implementation.index("WiFiClientSecure mclient")
        manifest_log = implementation.index("OTA: downloading manifest")
        manifest_fetch = implementation.index(
            "ota_fetchManifest(mclient, murl, true, doc, reply)"
        )
        self.assertLess(gate, manifest_client)
        self.assertLess(gate, manifest_log)
        self.assertLess(manifest_log, manifest_fetch)
        self.assertIn(
            "ota_openHttp(uclient, file_url, true, response, reply)",
            implementation,
        )

    def test_every_pull_ota_tls_connect_revalidates_the_bounded_proof(self):
        board = source(ESP32_BOARD)
        proof = section(
            board, "static bool ota_tlsClockProofValid", "static bool ota_prepareTlsClock"
        )
        request = section(board, "static bool ota_openHttp", "static bool ota_fetchManifest")

        self.assertIn("ota_tls_fresh_ntp_received.load", proof)
        self.assertIn("WiFi.status() == WL_CONNECTED", proof)
        self.assertIn("time(nullptr)", proof)
        self.assertIn("OTA_TLS_PROOF_MAX_AGE_MS", proof)
        self.assertIn("mesh::tls_clock::proofAgeIsValid", proof)
        self.assertIn("mesh::tls_clock::proofGenerationIsValid", proof)
        guard = request.index("require_tls && !ota_tlsClockProofValid()")
        connect = request.index("client.connect(host, port)")
        self.assertLess(guard, connect)
        self.assertEqual(request.count("client.connect("), 1)

    def test_all_sntp_callback_owners_share_one_nonblocking_generation_lease(self):
        board = source(ESP32_BOARD)
        indicator = source(INDICATOR_FONT)
        coordinator = source(SNTP_COORDINATOR)

        self.assertIn("compare_exchange_strong", coordinator)
        self.assertIn("if (!owns(generation)) return false", coordinator)
        cleanup = coordinator.index("if (cleanup != nullptr) cleanup()")
        publish_free = coordinator.index("expected, 0", cleanup)
        self.assertLess(cleanup, publish_free)
        self.assertIn("~OperationLease() { release(); }", coordinator)

        for implementation, callback, cleanup_name in (
            (board, "ota_noteNtpTime", "ota_clearNtpCallback"),
            (indicator, "noteFontNtpTime", "clearFontNtpCallback"),
        ):
            self.assertIn("processWideCoordinator()", implementation)
            self.assertIn("OperationLease sntp_operation", implementation)
            self.assertIn("processWideCoordinator().owns(generation)", implementation)
            cleanup_body = section(
                implementation,
                f"void {cleanup_name}",
                f"void {callback}",
            )
            self.assertIn("sntp_set_time_sync_notification_cb(nullptr)", cleanup_body)
            self.assertIn(".store(0, std::memory_order_release)", cleanup_body)

    def test_tls_bundle_diagnostic_rejects_an_invalid_clock_before_connect(self):
        cli = source(OBSERVER_CLI)
        diagnostic = section(
            cli,
            'if (memcmp(command, "tls.bundletest ", 15) == 0)',
            '} else if (memcmp(command, "ota check", 9)',
        )
        signed_clock = diagnostic.index("const time_t tls_now = time(nullptr)")
        gate = diagnostic.index("mesh::tls_clock::timeIsValid(tls_now)")
        connect = diagnostic.index("client.connect(host, port)")
        self.assertLess(signed_clock, gate)
        self.assertLess(gate, connect)

    def test_mqtt_clock_flag_is_not_documented_as_fresh_ntp_proof(self):
        header = source(MQTT_BRIDGE)
        self.assertIn("plausible retained RTC/system clock", header)
        self.assertNotIn(
            "True after this bridge has successfully set the RTC from NTP",
            header,
        )

    def test_every_mqtt_sntp_mutation_is_coordinator_leased(self):
        header = source(MQTT_BRIDGE)
        implementation = source(MQTT_BRIDGE_IMPL)
        refresh = section(
            implementation,
            "void MQTTBridge::refreshNTP()",
            "bool MQTTBridge::syncTimeWithNTP",
        )
        sync = section(
            implementation,
            "bool MQTTBridge::syncTimeWithNTP",
            "bool MQTTBridge::requestForcedNtpSync",
        )

        self.assertIn("SntpOperationCoordinator.h", implementation)
        self.assertEqual(len(re.findall(r"(?m)^\s*configTime\(", implementation)), 2)
        self.assertEqual(
            len(re.findall(r"(?m)^\s*sntp_set_sync_status\(", implementation)),
            2,
        )
        self.assertEqual(
            len(re.findall(r"sntp_get_sync_status\(", implementation)),
            2,
        )
        self.assertNotIn("esp_sntp_set_sync_interval(", implementation)
        self.assertIn("OperationLease _ntp_refresh_operation", header)
        cleanup = section(
            implementation,
            "static void stopMqttSntpOperation()",
            "static void formatRadioInfo",
        )
        self.assertIn("esp_sntp_stop();", cleanup)
        self.assertIn(
            "_ntp_refresh_operation(mesh::sntp_coord::processWideCoordinator(),",
            implementation,
        )
        self.assertIn("stopMqttSntpOperation)", implementation)

        refresh_acquire = refresh.index(
            "if (!_ntp_refresh_operation.tryAcquire())"
        )
        refresh_configure = refresh.index("configTime(")
        self.assertLess(refresh_acquire, refresh_configure)
        poll = section(
            implementation,
            "void MQTTBridge::pollNtpRefresh",
            "bool MQTTBridge::syncTimeWithNTP",
        )
        owns = poll.index("_ntp_refresh_operation.owns()")
        completed = poll.index("SNTP_SYNC_STATUS_COMPLETED", owns)
        self.assertLess(owns, completed)
        cancel = section(
            implementation,
            "void MQTTBridge::cancelNtpRefresh()",
            "bool MQTTBridge::servicePendingClockCorrection()",
        )
        self.assertIn("_ntp_refresh_operation.release()", cancel)

        fallback_start = sync.index(
            "// Fallback: use ESP32 built-in SNTP (configTime)"
        )
        fallback_end = sync.index("\n  #endif\n\n  if (!ntp_ok) {", fallback_start)
        fallback = sync[fallback_start:fallback_end]
        fallback_lease = fallback.index("OperationLease sntp_operation")
        fallback_acquire = fallback.index("if (!sntp_operation.tryAcquire())")
        reset = fallback.index("sntp_set_sync_status(SNTP_SYNC_STATUS_RESET)")
        configure = fallback.index("configTime(0, 0, server)")
        completed = fallback.index(
            "sntp_get_sync_status() != SNTP_SYNC_STATUS_COMPLETED"
        )
        self.assertLess(fallback_lease, fallback_acquire)
        self.assertLess(fallback_acquire, reset)
        self.assertLess(reset, configure)
        self.assertLess(configure, completed)
        self.assertEqual(
            len(re.findall(r"(?m)^\s*configTime\(", sync)), 1
        )

    def test_mqtt_async_sntp_contention_uses_a_wrap_safe_bounded_retry(self):
        header = source(MQTT_BRIDGE)
        implementation = source(MQTT_BRIDGE_IMPL)
        policy = source(MQTT_POLICY)
        refresh = section(
            implementation,
            "void MQTTBridge::refreshNTP()",
            "bool MQTTBridge::syncTimeWithNTP",
        )

        self.assertIn("unsigned long _ntp_refresh_retry_at", header)
        self.assertIn("kNtpRetryMs = 5000UL", policy)
        self.assertIn(
            "static_cast<int32_t>(now - retry_at) >= 0", policy
        )
        self.assertIn("return retry_at == 0 ? 1 : retry_at", policy)
        self.assertIn("kNtpRefreshIntervalMs = 86400000UL", policy)
        self.assertIn(
            "elapsedMs(now, last_sync) >= kNtpRefreshIntervalMs", policy
        )
        self.assertNotIn("ntpReconnectRefreshAt", policy)
        self.assertNotIn("ntpReconnectRefreshAt", implementation)
        self.assertGreaterEqual(implementation.count("mqttNtpRefreshDue("), 4)
        busy = refresh.index("if (!_ntp_refresh_operation.tryAcquire())")
        retry = refresh.index("mqttNtpRetryAt(now)", busy)
        leave = refresh.index("return;", retry)
        configure = refresh.index(
            "configTime(0, 0, effectiveNtpPrimary(_obs))"
        )
        self.assertLess(busy, retry)
        self.assertLess(retry, leave)
        self.assertLess(leave, configure)
        self.assertIn("_ntp_refresh_retry_at = 0", refresh[configure:])

        sync = section(
            implementation,
            "bool MQTTBridge::syncTimeWithNTP",
            "bool MQTTBridge::requestForcedNtpSync",
        )
        self.assertIn(
            "fresh_ntp ? 0 : mqttNtpRetryAt(_last_ntp_sync)",
            sync,
        )

    def test_mqtt_periodic_refresh_updates_rtc_only_after_sntp_completion(self):
        header = source(MQTT_BRIDGE)
        implementation = source(MQTT_BRIDGE_IMPL)
        refresh = section(
            implementation,
            "void MQTTBridge::refreshNTP()",
            "void MQTTBridge::pollNtpRefresh",
        )
        poll = section(
            implementation,
            "void MQTTBridge::pollNtpRefresh",
            "bool MQTTBridge::syncTimeWithNTP",
        )

        self.assertIn("unsigned long _ntp_refresh_started_at", header)
        self.assertIn("bool _ntp_refresh_pending", header)
        self.assertIn("std::atomic<bool> _ntp_synced", header)
        self.assertIn("OperationLease _ntp_refresh_operation", header)
        self.assertIn(
            "MQTTConnectionPolicy::NtpReconnectLatch "
            "_ntp_reconnect_latch",
            header,
        )
        self.assertIn("MQTT_NTP_REFRESH_TIMEOUT_MS = 15000UL", implementation)
        self.assertEqual(implementation.count("pollNtpRefresh("), 3)
        self.assertGreaterEqual(
            implementation.count("&& !_ntp_refresh_pending"), 2
        )

        reset = refresh.index("sntp_set_sync_status(SNTP_SYNC_STATUS_RESET)")
        configure = refresh.index(
            "configTime(0, 0, effectiveNtpPrimary(_obs))"
        )
        pending = refresh.index("_ntp_refresh_pending = true", configure)
        self.assertLess(reset, configure)
        self.assertLess(configure, pending)
        self.assertNotIn("_last_ntp_sync =", refresh)

        completed = poll.index("sntp_get_sync_status() == SNTP_SYNC_STATUS_COMPLETED")
        system_clock = poll.index("time(nullptr)", completed)
        valid = poll.index(
            "MQTTConnectionPolicy::checkedRtcEpoch(", system_clock
        )
        self.assertIn("static_cast<int64_t>(raw_system_time)", poll[valid:])
        rtc = poll.index("_rtc->setCurrentTime(system_time)", valid)
        success = poll.index("_last_ntp_sync = now", rtc)
        self.assertLess(completed, system_clock)
        self.assertLess(system_clock, valid)
        self.assertLess(valid, rtc)
        self.assertLess(rtc, success)
        self.assertEqual(poll.count("_last_ntp_sync ="), 1)
        self.assertIn("_ntp_refresh_operation.owns()", poll)

        timeout = poll.index("elapsed >= MQTT_NTP_REFRESH_TIMEOUT_MS")
        retry = poll.index("mqttNtpRetryAt(now)", timeout)
        self.assertLess(timeout, retry)
        self.assertNotIn("_last_ntp_sync =", poll[timeout:])

        got_ip = section(
            implementation,
            "case ARDUINO_EVENT_WIFI_STA_GOT_IP:",
            "case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:",
        )
        self.assertIn("_ntp_reconnect_latch.noteGotIp()", got_ip)
        self.assertNotIn("_ntp_synced", got_ip)
        self.assertNotIn("_ntp_sync_pending", got_ip)
        task_loop = section(
            implementation,
            "void MQTTBridge::mqttTaskLoop()",
            "bool MQTTBridge::isSlotReady",
        )
        connected = task_loop.index(
            "const bool wifi_connected = WiFi.status() == WL_CONNECTED"
        )
        consume = task_loop.index(
            "_ntp_reconnect_latch.consumeIfConnected(wifi_connected)"
        )
        self.assertLess(connected, consume)
        self.assertNotIn("ntpReconnectRefreshAt", task_loop)
        begin = section(
            implementation,
            "void MQTTBridge::begin()",
            "void MQTTBridge::end()",
        )
        self.assertNotIn("_ntp_reconnect_latch.", begin)


if __name__ == "__main__":
    unittest.main()
