import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN = ROOT / "examples/companion_radio/main.cpp"
POLICY = ROOT / "src/helpers/CompanionWiFiNtpPolicy.h"
MESH_CLOCK = ROOT / "src/helpers/MeshClockSync.cpp"
MQTT = ROOT / "src/helpers/bridges/MQTTBridge.cpp"
MQTT_HEADER = ROOT / "src/helpers/bridges/MQTTBridge.h"
ESP32_BOARD = ROOT / "src/helpers/ESP32Board.cpp"
ESP32_BOARD_HEADER = ROOT / "src/helpers/ESP32Board.h"
BASE_CHAT = ROOT / "src/helpers/BaseChatMesh.cpp"
ROOM_SERVER = ROOT / "examples/simple_room_server/MyMesh.h"
ROOM_SERVER_IMPL = ROOT / "examples/simple_room_server/MyMesh.cpp"
REPEATER = ROOT / "examples/simple_repeater/MyMesh.cpp"
COMPANION_MESH = ROOT / "examples/companion_radio/MyMesh.cpp"


def source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


class CompanionWiFiNtpIntegrationTests(unittest.TestCase):
    def test_policy_is_boot_immediate_and_daily_after_success(self):
        policy = source(POLICY)
        self.assertIn("24UL * 60UL * 60UL * 1000UL", policy)
        self.assertIn("CompanionWiFiNtpPolicy() : _scheduled(false)", policy)
        self.assertIn("noteSuccess", policy)
        self.assertIn("kCompanionNtpRefreshMillis", policy)
        self.assertIn("static_cast<int32_t>(now - _deadline) >= 0", policy)

    def test_common_wifi_path_uses_fresh_owned_sntp_completion(self):
        main = source(MAIN)
        self.assertIn("#include <helpers/CompanionWiFiNtpPolicy.h>", main)
        self.assertIn("processWideCoordinator()", main)
        self.assertIn("companion_wifi_ntp_operation_generation", main)
        self.assertIn("companion_wifi_ntp_proof_generation", main)
        self.assertIn("companion_wifi_ntp_proven_at", main)
        self.assertIn(
            "proven_at - companion_wifi_ntp_started <=", main
        )
        self.assertIn("sntp_set_sync_status(SNTP_SYNC_STATUS_RESET)", main)
        self.assertIn("configTime(0, 0, \"time.cloudflare.com\"", main)
        self.assertLess(
            main.index("sntp_set_sync_status(SNTP_SYNC_STATUS_RESET)"),
            main.index("configTime(0, 0, \"time.cloudflare.com\""),
        )

    def test_success_updates_board_rtc_and_blocks_mesh_fallback(self):
        main = source(MAIN)
        proof = main.index("const uint32_t proof_epoch")
        accepted = main.index("const time_t raw_system_time = time(nullptr)", proof)
        write_rtc = main.index("rtc_clock.setCurrentTime(epoch);", accepted)
        self.assertLess(proof, accepted)
        self.assertLess(accepted, write_rtc)
        self.assertIn("rtc_clock.setCurrentTime(epoch);", main)
        self.assertIn(
            "if (moved_backward) rtc_clock.resetUniqueTime(epoch);", main
        )
        self.assertIn("the_mesh.noteInternetClockSet();", main)
        self.assertIn("serviceCompanionWiFiNtp();", main)
        self.assertIn("void MeshClockSync::onInternetClockSet()", source(MESH_CLOCK))
        self.assertIn("suppressForBoot(SUPPRESS_INTERNET)", source(MESH_CLOCK))

    def test_configured_mqtt_remains_the_single_ntp_owner(self):
        main = source(MAIN)
        self.assertIn("companionWiFiMqttOwnsNtp", main)
        self.assertIn("const bool running = the_mesh.isMQTTRunning();", main)
        self.assertIn("the_mesh.hasFreshMQTTNtpThisBoot()", main)
        owner_check = main.index("if (companionWiFiMqttOwnsNtp())")
        generic_start = main.index("companion_wifi_ntp_operation.tryAcquire()")
        self.assertLess(owner_check, generic_start)

    def test_common_and_mqtt_requests_are_one_shot_with_explicit_daily_cadence(self):
        main = source(MAIN)
        mqtt = source(MQTT)
        header = source(MQTT_HEADER)
        cleanup = main[main.index("static void clearCompanionWiFiNtpCallback") :]
        mqtt_cleanup = mqtt[
            mqtt.index("static void stopMqttSntpOperation()") :
            mqtt.index("static void formatRadioInfo")
        ]
        self.assertIn("esp_sntp_stop();", cleanup)
        self.assertNotIn("esp_sntp_set_sync_interval(", mqtt)
        self.assertIn("static void stopMqttSntpOperation()", mqtt)
        self.assertIn("esp_sntp_stop();", mqtt_cleanup)
        self.assertIn("OperationLease _ntp_refresh_operation", header)
        self.assertIn("_ntp_refresh_operation.tryAcquire()", mqtt)
        self.assertIn("_ntp_refresh_operation.owns()", mqtt)
        self.assertIn("_ntp_refresh_operation.release()", mqtt)
        self.assertNotIn("ntpReconnectRefreshAt", mqtt)

    def test_backward_mqtt_correction_is_serviced_on_role_main_loops(self):
        mqtt = source(MQTT)
        header = source(MQTT_HEADER)
        self.assertIn("std::atomic<uint32_t> _backward_clock_reset_epoch", header)
        self.assertIn("bool servicePendingClockCorrection();", header)
        self.assertIn("_backward_clock_reset_epoch.store(", mqtt)
        self.assertIn("_rtc->resetUniqueTime(epoch);", mqtt)
        for role in (COMPANION_MESH, ROOM_SERVER_IMPL, REPEATER):
            self.assertIn("servicePendingClockCorrection()", source(role))

    def test_mqtt_boot_paths_and_fresh_success_begin_daily_cadence(self):
        mqtt = source(MQTT)
        initialize = mqtt[
            mqtt.index("void MQTTBridge::initializeWiFiInTask()") :
            mqtt.index("void MQTTBridge::mqttTaskLoop()")
        ]
        task_loop = mqtt[
            mqtt.index("void MQTTBridge::mqttTaskLoop()") :
            mqtt.index("bool MQTTBridge::isSlotReady")
        ]
        sync = mqtt[
            mqtt.index("bool MQTTBridge::syncTimeWithNTP") :
            mqtt.index("bool MQTTBridge::requestForcedNtpSync")
        ]
        self.assertIn("!_ntp_synced.load", initialize)
        self.assertIn("_ntp_sync_pending = true", initialize)
        self.assertIn("_ntp_reconnect_latch.consumeIfConnected", task_loop)
        self.assertIn("_ntp_sync_pending = true", task_loop)
        self.assertIn("syncTimeWithNTP();", task_loop)
        self.assertIn("_last_ntp_sync = millis();", sync)
        self.assertIn(
            "fresh_ntp ? 0 : mqttNtpRetryAt(_last_ntp_sync)", sync
        )

    def test_only_fresh_internet_replies_suppress_mesh_fallback(self):
        mqtt = source(MQTT)
        header = source(MQTT_HEADER)
        self.assertIn("std::atomic<bool> _fresh_ntp_this_boot", header)
        self.assertIn("bool hasFreshNtpThisBoot() const", header)
        self.assertIn("const bool fresh_ntp = ntp_server_used != nullptr;", mqtt)
        self.assertIn("if (fresh_ntp) {", mqtt)
        self.assertIn("_fresh_ntp_this_boot.store(true", mqtt)
        self.assertNotIn("bridge->hasNtpTime()", source(ROOM_SERVER))
        self.assertNotIn("mqtt_bridge->hasNtpTime()", source(REPEATER))
        self.assertIn("bridge->hasFreshNtpThisBoot()", source(ROOM_SERVER))
        self.assertIn("mqtt_bridge->hasFreshNtpThisBoot()", source(REPEATER))

    def test_esp32_retained_clock_has_one_program_wide_backup(self):
        header = source(ESP32_BOARD_HEADER)
        implementation = source(ESP32_BOARD)
        self.assertNotIn("static RTC_NOINIT_ATTR uint32_t", header)
        self.assertIn("extern uint32_t rtc_backup_time;", header)
        self.assertIn("RTC_NOINIT_ATTR uint32_t rtc_backup_time;", implementation)

    def test_contact_bootstrap_is_only_a_forward_clock_floor(self):
        implementation = source(BASE_CHAT)
        bootstrap = implementation[
            implementation.index("void BaseChatMesh::bootstrapRTCfromContacts()") :
            implementation.index("ContactInfo* BaseChatMesh::allocateContactSlot")
        ]
        self.assertIn("latest != 0 && latest != UINT32_MAX", bootstrap)
        self.assertIn("contact_floor > getRTCClock()->getCurrentTime()", bootstrap)


if __name__ == "__main__":
    unittest.main()
