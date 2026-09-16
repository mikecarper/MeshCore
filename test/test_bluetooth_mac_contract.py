#!/usr/bin/env python3
"""Static contracts for Companion Bluetooth identity configuration."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class BluetoothMacContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = source("examples/companion_radio/main.cpp")
        cls.mesh = source("examples/companion_radio/MyMesh.cpp")
        cls.nrf_header = source("src/helpers/nrf52/SerialBLEInterface.h")
        cls.nrf_source = source("src/helpers/nrf52/SerialBLEInterface.cpp")
        cls.esp_header = source("src/helpers/esp32/SerialBLEInterface.h")
        cls.esp_source = source("src/helpers/esp32/SerialBLEInterface.cpp")

    def test_both_ble_backends_accept_the_same_configuration(self):
        for header in (self.nrf_header, self.esp_header):
            self.assertIn("const uint8_t* custom_address = nullptr", header)
            self.assertIn("bool clear_bonds = false", header)
            self.assertIn("bool stealth_pair_once = false", header)
            self.assertIn("bonded_only_peer", header)

        self.assertIn("the_mesh.getBLEPin(), bluetooth_address,", self.main)
        self.assertIn("clear_bonds, stealth_pair_once,", self.main)
        self.assertIn("bonded_only_peer_ptr))", self.main)

    def test_nrf52_uses_random_static_softdevice_address(self):
        self.assertIn("BLE_GAP_ADDR_TYPE_RANDOM_STATIC", self.nrf_source)
        self.assertIn("sd_ble_gap_addr_set(&address)", self.nrf_source)
        self.assertIn(
            "mesh::companion::BLUETOOTH_MAC_BYTES - 1 - i",
            self.nrf_source,
        )

    def test_pico_rejects_unsupported_identity_policies_before_saving(self):
        pico = source("src/helpers/rp2040/SerialBLEInterface.cpp")
        self.assertIn(
            "if (custom_address || clear_bonds || stealth_pair_once || bonded_only_peer) return false;",
            pico,
        )
        mac = self.mesh[self.mesh.index("bool MyMesh::applyAndSaveBluetoothMac("):
                        self.mesh.index("void MyMesh::formatBluetoothMacStatus(")]
        self.assertLess(mac.index("#if defined(RP2040_PLATFORM)"),
                        mac.index("saveBluetoothMac(mode, address)"))
        self.assertIn("mode != mesh::companion::BLUETOOTH_MAC_DEFAULT", mac)
        stealth = self.mesh[self.mesh.index("bool MyMesh::applyAndSaveBluetoothStealth("):
                            self.mesh.index("void MyMesh::formatBluetoothStealthStatus(")]
        self.assertLess(stealth.index("#if defined(RP2040_PLATFORM)"),
                        stealth.index("setCompanionBluetoothStealth(_prefs,"))
        self.assertIn('if (strcmp(value, "on") == 0)', stealth)

    def test_esp32_supports_nimble_and_bluedroid(self):
        self.assertIn("BLEDevice::setOwnAddr(native_address)", self.esp_source)
        self.assertIn("BLEDevice::setOwnAddrType(BLE_OWN_ADDR_RANDOM)", self.esp_source)
        self.assertIn("setDeviceAddress(", self.esp_source)
        self.assertIn("BLE_ADDR_TYPE_RANDOM", self.esp_source)

    def test_every_boot_mode_is_stable_across_start_retries(self):
        self.assertIn(
            "companion_bluetooth_session_address_ready", self.main
        )
        self.assertIn(
            "BLUETOOTH_MAC_RANDOM_EVERY_BOOT", self.main
        )
        self.assertIn("clear_bonds = true;", self.main)

    def test_cli_exposes_all_address_modes(self):
        self.assertIn('"get bluetooth.mac"', self.mesh)
        self.assertIn('"set bluetooth.mac"', self.mesh)
        self.assertIn('strcmp(value, "random")', self.mesh)
        self.assertIn('strcmp(value, "random-every-boot")', self.mesh)
        self.assertIn('strcmp(value, "random everyboot")', self.mesh)
        self.assertIn('strcmp(value, "random-after-connect")', self.mesh)
        self.assertNotIn('strcmp(value, "stealth")', self.mesh)
        self.assertIn('strcmp(value, "default")', self.mesh)
        self.assertIn("parseBluetoothMac(custom_value, address)", self.mesh)

    def test_stealth_is_a_separate_idempotent_flag(self):
        self.assertIn('"get bluetooth.stealth"', self.mesh)
        self.assertIn('"set bluetooth.stealth"', self.mesh)
        self.assertIn('"get ble.stealth"', self.mesh)
        self.assertIn('"set ble.stealth"', self.mesh)
        setter = self.mesh[
            self.mesh.index("bool MyMesh::applyAndSaveBluetoothStealth("):
            self.mesh.index("void MyMesh::formatBluetoothStealthStatus(")
        ]
        self.assertIn('strcmp(value, "on")', setter)
        self.assertIn('strcmp(value, "off")', setter)
        self.assertIn("setCompanionBluetoothStealth(_prefs,", setter)
        self.assertIn("&& !savePrefs()", setter)
        self.assertIn("_prefs.bluetooth_stealth_mode = previous_mode", setter)
        self.assertNotIn("saveBluetoothMac(", setter)
        self.assertNotIn("_prefs.bluetooth_mac_mode =", setter)
        self.assertNotIn("getRNG()", setter)
        self.assertNotIn("|stealth|", self.mesh)

    def test_rotation_and_stealth_share_the_authenticated_event(self):
        self.assertIn("companion_bluetooth_session_stealth_mode", self.main)
        self.assertIn("prefs->bluetooth_stealth_mode", self.main)
        self.assertIn("bluetoothMacPoliciesMatch(", self.main)
        start = self.mesh.index("bool MyMesh::prepareBluetoothMacForBoot(")
        end = self.mesh.index("bool MyMesh::armBluetoothMacRotationAfterConnection(")
        self.assertIn("BLUETOOTH_MAC_RANDOM_EVERY_BOOT", self.mesh[start:end])
        self.assertIn("resetBluetoothStealthPairing()", self.mesh[start:end])
        start = self.mesh.index("bool MyMesh::saveBluetoothStealthPeer(")
        end = self.mesh.index("bool MyMesh::resetBluetoothStealthPairing()")
        save_peer = self.mesh[start:end]
        self.assertLess(save_peer.index("BLUETOOTH_MAC_RANDOM_AFTER_CONNECT_ARMED"),
                        save_peer.index("if (savePrefs())"))
        self.assertIn("_prefs.bluetooth_mac_mode = previous_mode", save_peer)
        start = self.mesh.index("bool MyMesh::saveBluetoothMac(")
        end = self.mesh.index("bool MyMesh::applyAndSaveBluetoothMac(")
        self.assertIn("clearCompanionBluetoothStealthPeer(_prefs)", self.mesh[start:end])
        self.assertIn("_prefs.bluetooth_stealth_mode = previous_stealth_mode",
                      self.mesh[start:end])

    def test_web_snapshot_and_dispatch_keep_flag_separate(self):
        self.assertIn('strcmp(key, "bluetooth.stealth")', self.mesh)
        self.assertIn("s.bluetooth_stealth =", self.mesh)
        self.assertNotIn('sizeof(s.bluetooth_mac), "stealth"', self.mesh)
        self.assertIn("CAP_BLUETOOTH_STEALTH", self.mesh)

    def test_random_after_connect_arms_after_authentication(self):
        self.assertIn("takeSuccessfulConnection(", self.nrf_header)
        self.assertIn("takeSuccessfulConnection(", self.esp_header)
        self.assertIn(
            "_successfulConnectionPending.store(", self.nrf_source
        )
        self.assertIn(
            "_successfulConnectionPending.store(", self.esp_source
        )
        self.assertIn("prepareBluetoothMacForBoot", self.main)
        self.assertIn("armBluetoothMacRotationAfterConnection", self.main)
        self.assertIn("bluetoothMacModeIsRandomAfterConnect", self.main)
        self.assertIn(
            "companion_bluetooth_clear_bonds_this_boot", self.main
        )

    def test_stealth_switches_to_bonded_peer_only_advertising(self):
        for header in (self.nrf_header, self.esp_header):
            self.assertIn("enableBondedOnlyAdvertising", header)
            self.assertIn("takeBondedOnlyRecovery", header)
            self.assertIn("cancelStealthPairingTransition", header)

        self.assertIn(
            "BLE_GAP_ADV_TYPE_CONNECTABLE_NONSCANNABLE_DIRECTED",
            self.nrf_source,
        )
        self.assertIn("Bluefruit.Advertising.clearData()", self.nrf_source)
        self.assertIn("Bluefruit.ScanResponse.clearData()", self.nrf_source)
        self.assertIn("setPeerAddress(native_peer)", self.nrf_source)
        self.assertIn("sd_ble_gap_device_identities_set(", self.nrf_source)
        self.assertIn("serviceBondedOnlyTransition()", self.nrf_source)
        self.assertIn("_bonded_only_configure_pending", self.nrf_source)
        self.assertIn("ble_gap_wl_set(&native_peer, 1)", self.esp_source)
        self.assertIn("esp_ble_gap_clear_whitelist()", self.esp_source)
        self.assertIn("esp_ble_gap_update_whitelist", self.esp_source)
        self.assertIn("advertising->setScanFilter(true, true)", self.esp_source)
        self.assertIn("advertising->setScanResponse(false)", self.esp_source)
        self.assertIn("saveBluetoothStealthPeer", self.main)

    def test_stealth_holds_general_advertising_during_transition(self):
        for source_text in (self.nrf_source, self.esp_source):
            self.assertIn("_advertisingSuppressed.store(", source_text)
            self.assertIn("advertisingAllowed()", source_text)
            callback_at = source_text.index(
                "void SerialBLEInterface::noteSuccessfulConnection"
            )
            callback_end = source_text.index("\n}", callback_at)
            callback = source_text[callback_at:callback_end]
            self.assertLess(
                callback.index("_advertisingSuppressed.store("),
                callback.index("_successfulConnectionPending.store("),
            )
        self.assertIn("restartOnDisconnect(false)", self.nrf_source)
        self.assertIn(
            "_conn_handle != BLE_CONN_HANDLE_INVALID",
            self.nrf_source[
                self.nrf_source.index(
                    "bool SerialBLEInterface::enableBondedOnlyAdvertising"
                ) : self.nrf_source.index(
                    "void SerialBLEInterface::serviceBondedOnlyTransition"
                )
            ],
        )
        pairing_callback = self.nrf_source.index(
            "void SerialBLEInterface::onPairingComplete"
        )
        pairing_success = self.nrf_source.index(
            "pairing successful", pairing_callback
        )
        next_callback = self.nrf_source.index(
            "void SerialBLEInterface::onBLEEvent", pairing_success
        )
        self.assertIn(
            "noteSuccessfulConnection(conn->getPeerAddr())",
            self.nrf_source[pairing_success:next_callback],
        )
        self.assertIn("resetBluetoothStealthPairing", self.main)
        for source_text in (self.nrf_source, self.esp_source):
            self.assertIn("BLE_BOND_PERSIST_TIMEOUT_MS", source_text)
            self.assertIn("new peer bond did not persist", source_text)


if __name__ == "__main__":
    unittest.main()
