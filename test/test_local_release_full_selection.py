#!/usr/bin/env python3
"""Full selection preserves qualified transports and board capacity exceptions."""

from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_local_release import select_ordinary_full_records
import build_local_release as release


def record(target: str, profile: str = "full", platform: str = "ESP32_PLATFORM",
           capabilities: tuple[str, ...] = ()) -> dict:
    return {"manifest": {"target": target, "build_profile": profile, "platform": platform,
                         "verified": True, "capabilities": list(capabilities),
                         "verification": [{"capability": capability, "present": True,
                                           "source": "linked image", "evidence": capability}
                                          for capability in capabilities]},
            "files": []}


def sensor_pair(target: str, storage: str = "external_qspi") -> list[dict]:
    variants = []
    for sensor in ("full", "reduced"):
        item = record(target, "auto", "NRF52_PLATFORM",
                      ("sensor.profile." + sensor, "ota.update.lora"))
        item["manifest"].update(
            artifact_target=target + "-" + sensor + "-ota",
            ota_update_verified=True, ota_update_methods=["lora"],
            ota_update_requirements={"lora": {"storage": storage}})
        item["files"] = [Path(item["manifest"]["artifact_target"] + ".uf2"),
                         Path(item["manifest"]["artifact_target"] + ".zip")]
        variants.append(item)
    return variants


def onboard_qspi_retirements() -> list[tuple[str, str]]:
    rooms = ("solarxiao_30S_room_server", "solarxiao_33S_room_server", "Xiao_nrf52_room_server",
             "ikoka_nano_nrf_22dbm_room_server", "ikoka_nano_nrf_30dbm_room_server",
             "ikoka_nano_nrf_33dbm_room_server", "ikoka_stick_nrf_22dbm_room_server",
             "ikoka_stick_nrf_30dbm_room_server", "ikoka_stick_nrf_33dbm_room_server",
             "ikoka_handheld_nrf_e22_30dbm_room_server", "LilyGo_T-Echo_room_server",
             "SenseCap_Solar_room_server", "ThinkNode_M1_room_server", "ThinkNode_M6_room_server",
             "WioTrackerL1_room_server", "LilyGo_T-Echo-Lite_room_server")
    cases = [(legacy, base + "_lora_ota")
             for base in rooms + ("LilyGo_T-Echo-Lite_repeater",)
             for legacy in (base, base + "_lora_ota_no_external_sensors")]
    cases += [(target + "_lora_ota_no_external_sensors", target) for target in (
        "LilyGo_T-Echo_repeater", "SenseCap_Solar_repeater", "ThinkNode_M1_repeater",
        "ThinkNode_M6_repeater", "WioTrackerL1_repeater")]
    cases += [(target, "Xiao_nrf52_repeater") for target in (
        "Xiao_nrf52_repeater_bridge_rs232",
        "Xiao_nrf52_repeater_bridge_rs232_lora_ota_no_external_sensors")]
    return cases


def prove_runtime_rs232(variants: list[dict]) -> list[dict]:
    for item in variants:
        manifest = item["manifest"]
        for capability, source in (("bridge.rs232", "linked image"),
                                   ("bridge.rs232.merged", "packaged application")):
            manifest["capabilities"].append(capability)
            manifest["verification"].append(dict(capability=capability, source=source,
                                                  present=True, evidence=capability))
    return variants


class ReleaseFullSelectionTest(unittest.TestCase):
    def test_resume_discards_everyday_esp32_standard_profiles_with_or_without_full(self):
        for target in ("heltec_v4_repeater", "heltec_v4_room_server", "heltec_v4_sensor",
                       "Station_G3_ESP32_r2_repeater", "Ebyte_EoRa-S3_Repeater",
                       "Station_G2_companion_radio_full"):
            primary = record(target)
            for profile in ("standard", "auto", "reduced"):
                stale = record(target, profile)
                with self.subTest(target=target, profile=profile):
                    self.assertEqual(select_ordinary_full_records([stale]), [])
                    for inputs in ([primary, stale], [stale, primary]):
                        selected = select_ordinary_full_records(inputs)
                        self.assertEqual(selected, [primary])
                        self.assertIs(selected[0], primary)

    def test_reduced_esp32_identities_are_not_relabelled_as_full_releases(self):
        for base in ("heltec_v4_repeater", "Ebyte_EoRa-S3_Repeater",
                     "LilyGo_Tlora_C6_room_server_"):
            primary = record(base)
            alias = base.rstrip("_") + "_lora_ota_no_external_sensors"
            for profile in ("standard", "auto", "full"):
                legacy = record(alias, profile)
                with self.subTest(base=base, profile=profile):
                    self.assertEqual(select_ordinary_full_records([legacy]), [])
                    self.assertEqual(select_ordinary_full_records([legacy, primary]), [primary])
                    self.assertEqual(primary["manifest"]["target"], base)
                    self.assertEqual(legacy["manifest"]["target"], alias)

    def test_legacy_companion_transport_names_do_not_survive_resumed_releases(self):
        primary = record("Station_G2_companion_radio_full")
        for transport in ("usb", "ble", "wifi"):
            for profile in ("standard", "full"):
                legacy = record("Station_G2_companion_radio_" + transport, profile)
                with self.subTest(transport=transport, profile=profile):
                    self.assertEqual(select_ordinary_full_records([legacy]), [])
                    self.assertEqual(select_ordinary_full_records([legacy, primary]), [primary])
        terminal = record("Station_G2_terminal_chat")
        self.assertEqual(select_ordinary_full_records([terminal, primary]), [primary])

    def test_migration_and_local_hil_utilities_stay_out_of_ordinary_release(self):
        primary = record("heltec_v4_repeater")
        for target in ("heltec_v4_partition_migrator", "Xiao_S3_WIO_partition_expander",
                       "xiao_s3_partition_legacy_seed", "profile_switch_heltec_v4",
                       "profile_fixed_esp32_tx", "profile_four_tx_v4_rx",
                       "heltec_v4_repeater_legacy_partition_test",
                       "Heltec_v3_repeater_observer_mqtt_sim"):
            for profile in ("standard", "full"):
                utility = record(target, profile)
                with self.subTest(target=target, profile=profile):
                    self.assertEqual(select_ordinary_full_records([utility]), [])
                    self.assertEqual(select_ordinary_full_records([utility, primary]), [primary])

    def test_kiss_is_a_distinct_host_modem_role_and_not_a_reduced_node(self):
        companion = record("Station_G2_companion_radio_full")
        for target in ("Station_G2_kiss_modem", "nibble_screen_connect_kiss_modem_"):
            for profile in ("standard", "auto", "full"):
                modem = record(target, profile)
                with self.subTest(target=target, profile=profile):
                    self.assertEqual(select_ordinary_full_records([modem]), [modem])
                    self.assertCountEqual(select_ordinary_full_records([companion, modem]),
                                          [companion, modem])

    def test_optional_sd_source_is_not_published_as_an_ordinary_receiver(self):
        primary = record("RAK_4631_repeater_unified_lora_ota", platform="NRF52_PLATFORM")
        for profile in ("standard", "auto", "full", "reduced"):
            source = record("RAK_4631_repeater_rak15002_sd_seeder_only", profile,
                            platform="NRF52_PLATFORM")
            with self.subTest(profile=profile):
                self.assertEqual(select_ordinary_full_records([source]), [])
                self.assertEqual(select_ordinary_full_records([source, primary]), [primary])

    def test_unverified_selected_esp32_full_image_cannot_enter_release(self):
        for target in ("heltec_v4_room_server", "Station_G2_companion_radio_full"):
            for verified in (False, None):
                image = record(target)
                image["manifest"]["verified"] = verified
                with self.subTest(target=target, verified=verified):
                    with self.assertRaisesRegex(ValueError, "verified"):
                        select_ordinary_full_records([image])

    def test_exact_hardware_and_application_roles_remain_distinct_full_records(self):
        images = [record(target) for target in (
            "heltec_v4_repeater", "heltec_v4_tft_repeater", "heltec_v4_r8_repeater",
            "heltec_v4_room_server", "heltec_v4_sensor",
            "heltec_v4_2_v4_3_companion_radio_full_femon",
            "heltec_v4_tft_companion_radio_full_femon",
        )]
        for inputs in (images, list(reversed(images))):
            selected = select_ordinary_full_records(inputs)
            self.assertCountEqual(selected, images)
            self.assertEqual(len(selected), len(images))

    def test_other_platform_profiles_and_reduced_identities_are_unchanged(self):
        for platform in ("NRF52_PLATFORM", "RP2040_PLATFORM", "STM32_PLATFORM"):
            for profile in ("standard", "auto", "full"):
                for suffix in ("", "_lora_ota_no_external_sensors"):
                    image = record("other_repeater" + suffix, profile, platform)
                    with self.subTest(platform=platform, profile=profile, suffix=suffix):
                        self.assertEqual(select_ordinary_full_records([image]), [image])

    def test_full_uart_source_substitution_requires_linked_driver_proof(self):
        for base in ("Heltec_v3_repeater", "Heltec_WSL3_repeater", "RAK_3112_repeater",
                     "LilyGo_TLora_V2_1_1_6_repeater"):
            suffixes = ("",) if base.startswith("LilyGo_TLora_") else (
                "", "_observer_mqtt", "_observer_mqtt_")
            for suffix in suffixes:
                for fault in ("missing_capability", "missing_proof", "false_proof", "unverified"):
                    image = record(base + suffix, capabilities=("bridge.rs232", "bridge.espnow"))
                    manifest = image["manifest"]
                    if fault == "missing_capability":
                        manifest["capabilities"].remove("bridge.rs232")
                    elif fault == "missing_proof":
                        manifest["verification"] = []
                    elif fault == "false_proof":
                        manifest["verification"][0]["present"] = False
                    else:
                        manifest["verified"] = False
                    with self.subTest(base=base, suffix=suffix, fault=fault):
                        with self.assertRaisesRegex(ValueError, "verified RS232 bridge"):
                            select_ordinary_full_records([image])

    def test_new_combined_full_displaces_stale_plain_and_both_dedicated_bridges(self):
        for base in ("Heltec_v3_repeater", "Heltec_WSL3_repeater", "RAK_3112_repeater"):
            stale = record(base, capabilities=("bridge.espnow",))
            primary = record(base + "_observer_mqtt_",
                             capabilities=("bridge.rs232", "bridge.espnow"))
            for profile in ("standard", "full"):
                old = [record(base + "_bridge_" + bridge, profile)
                       for bridge in ("rs232", "espnow")]
                for inputs in ([stale, primary] + old, old + [primary, stale]):
                    with self.subTest(base=base, profile=profile):
                        self.assertEqual(select_ordinary_full_records(inputs), [primary])

    def test_tlora_ram_exception_keeps_uart_and_mqtt_full_images(self):
        base = "LilyGo_TLora_V2_1_1_6_repeater"
        normal = record(base, capabilities=("bridge.rs232", "bridge.espnow"))
        observer = record(base + "_observer_mqtt_", capabilities=("bridge.espnow",))
        for profile in ("standard", "full"):
            legacy = [record(base + "_bridge_" + bridge, profile)
                      for bridge in ("rs232", "espnow")]
            for inputs in ([normal, observer] + legacy, legacy + [observer, normal],
                           [observer] + legacy + [normal]):
                with self.subTest(profile=profile, first=inputs[0]["manifest"]["target"]):
                    selected = select_ordinary_full_records(inputs)
                    self.assertCountEqual(selected, [normal, observer])
                    self.assertEqual(len(selected), 2)
                    self.assertTrue(all(item is normal or item is observer for item in selected))

    def test_tlora_observer_cannot_retire_uart_without_normal_driver_proof(self):
        base = "LilyGo_TLora_V2_1_1_6_repeater"
        observer = record(base + "_observer_mqtt_", capabilities=("bridge.espnow",))
        for profile in ("standard", "full"):
            uart = record(base + "_bridge_rs232", profile, capabilities=("bridge.rs232",))
            wireless = record(base + "_bridge_espnow", profile, capabilities=("bridge.espnow",))
            for inputs in ([observer, uart, wireless], [wireless, uart, observer]):
                with self.subTest(profile=profile):
                    expected = [observer, uart] if profile == "full" else [observer]
                    self.assertCountEqual(select_ordinary_full_records(inputs), expected)
        for fault in ("missing_capability", "missing_proof", "false_proof", "unverified"):
            normal = record(base, capabilities=("bridge.rs232", "bridge.espnow"))
            manifest = normal["manifest"]
            if fault == "missing_capability":
                manifest["capabilities"].remove("bridge.rs232")
            elif fault == "missing_proof":
                manifest["verification"] = []
            elif fault == "false_proof":
                manifest["verification"][0]["present"] = False
            else:
                manifest["verified"] = False
            for inputs in ([normal, observer], [observer, normal]):
                with self.subTest(fault=fault):
                    with self.assertRaisesRegex(ValueError, "verified RS232 bridge"):
                        select_ordinary_full_records(inputs)

    def test_tlora_legacy_espnow_needs_verified_replacement(self):
        base = "LilyGo_TLora_V2_1_1_6_repeater"
        for fault in ("missing_capability", "missing_proof", "false_proof", "unverified"):
            observer = record(base + "_observer_mqtt_", capabilities=("bridge.espnow",))
            manifest = observer["manifest"]
            if fault == "missing_capability":
                manifest["capabilities"] = []
            elif fault == "missing_proof":
                manifest["verification"] = []
            elif fault == "false_proof":
                manifest["verification"][0]["present"] = False
            else:
                manifest["verified"] = False
            for profile in ("standard", "full"):
                legacy = record(base + "_bridge_espnow", profile, capabilities=("bridge.espnow",))
                with self.subTest(fault=fault, profile=profile):
                    if fault == "unverified":
                        with self.assertRaisesRegex(ValueError, "verified"):
                            select_ordinary_full_records([observer, legacy])
                    else:
                        expected = [observer, legacy] if profile == "full" else [observer]
                        self.assertCountEqual(select_ordinary_full_records([observer, legacy]), expected)

    def test_meshtower_sd_primary_filters_stale_internal_images_without_relabeling(self):
        primary = "Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors"
        variants = []
        for sensor in ("full", "reduced"):
            item = record(primary, "auto", "NRF52_PLATFORM")
            item["manifest"].update(artifact_target=primary + "-" + sensor + "-ota",
                                    capabilities=["sensor.profile." + sensor])
            variants.append(item)
        companion = record("Heltec_tower_v2_companion_radio_full", platform="NRF52_PLATFORM")
        room = record("Heltec_tower_v2_room_server_lora_ota_no_external_sensors",
                      platform="NRF52_PLATFORM")
        legacy = [record(target, "auto", "NRF52_PLATFORM") for target in (
            "Heltec_tower_v2_repeater",
            "Heltec_tower_v2_repeater_lora_ota_no_external_sensors",
        )]
        selected = select_ordinary_full_records(legacy + variants + [companion, room])
        self.assertEqual(selected, variants + [companion, room])
        for before, after in zip(variants, selected):
            self.assertIs(before, after)
            self.assertEqual(after["manifest"]["target"], primary)

    def test_meshtower_internal_images_cannot_substitute_for_missing_primary(self):
        for target in ("Heltec_tower_v2_repeater",
                       "Heltec_tower_v2_repeater_lora_ota_no_external_sensors"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "requires the SD primary"):
                    select_ordinary_full_records([record(target, "auto", "NRF52_PLATFORM")])

    def test_solar_qspi_room_pairs_keep_both_profiles_and_original_identities(self):
        variants = []
        for board in ("30S", "33S"):
            variants.extend(sensor_pair("solarxiao_" + board + "_room_server_lora_ota"))
        for inputs in (variants, list(reversed(variants))):
            before = deepcopy(inputs)
            selected = select_ordinary_full_records(inputs)
            self.assertEqual(selected, inputs)
            self.assertEqual(inputs, before)
            self.assertTrue(all(actual is original for actual, original in zip(selected, inputs)))

    def test_solar_resume_filters_exact_internal_room_targets_without_mutating_files(self):
        legacy = []
        primaries = []
        for board in ("30S", "33S"):
            # Target matching is case-insensitive; publication identities stay
            # exactly as they appeared in the qualified manifest.
            target = "SOLARXIAO_" + board + "_ROOM_SERVER"
            legacy.extend(sensor_pair(target, "internal_flash_and_retained_ram"))
            legacy.append(record(target, "auto", "NRF52_PLATFORM"))
            primaries.extend(sensor_pair(target + "_LORA_OTA"))
        unrelated = [record(target, "auto", "NRF52_PLATFORM") for target in (
            "muzi_base_uno_room_server", "Nano_G2_Ultra_room_server",
            "LilyGo_T-Echo_Card_room_server", "R1Neo_sensor",
            "RAK_3401_room_server", "RAK_4631_room_server_lora_ota_no_external_sensors",
            "solarxiao_30S_repeater", "solarxiao_33S_sensor",
        )]
        for inputs in (legacy + primaries + unrelated, unrelated + primaries + legacy,
                       list(reversed(legacy + primaries + unrelated))):
            before = deepcopy(inputs)
            expected = [item for item in inputs if all(item is not old for old in legacy)]
            selected = select_ordinary_full_records(inputs)
            self.assertEqual(selected, expected)
            self.assertEqual(inputs, before)
            self.assertTrue(all(actual is original for actual, original in zip(selected, expected)))

    def test_solar_internal_rooms_cannot_replace_missing_matching_qspi_pair(self):
        for board, other_board in (("30S", "33S"), ("33S", "30S")):
            target = "solarxiao_" + board + "_room_server"
            other_pair = sensor_pair("solarxiao_" + other_board + "_room_server_lora_ota")
            for stale in ([record(target, "auto", "NRF52_PLATFORM")],
                          sensor_pair(target, "internal_flash_and_retained_ram")):
                for inputs in (stale, stale + other_pair, other_pair + stale):
                    before = deepcopy(inputs)
                    with self.subTest(board=board, count=len(inputs)):
                        with self.assertRaisesRegex(ValueError, "qualified external-QSPI Full/Reduced pair"):
                            select_ordinary_full_records(inputs)
                        self.assertEqual(inputs, before)

    def test_solar_resume_requires_complete_qualified_external_qspi_pair(self):
        for board in ("30S", "33S"):
            target = "solarxiao_" + board + "_room_server"
            for fault in ("missing_full", "missing_reduced", "duplicate_profile", "unverified",
                          "no_ota", "no_lora", "unproven_lora", "wrong_platform",
                          "internal_storage", "missing_storage", "missing_sensor_profile"):
                pair = sensor_pair(target + "_lora_ota")
                manifest = pair[0]["manifest"]
                if fault == "missing_full":
                    pair.pop(0)
                elif fault == "missing_reduced":
                    pair.pop()
                elif fault == "duplicate_profile":
                    pair.append(deepcopy(pair[0]))
                elif fault == "unverified":
                    manifest["verified"] = False
                elif fault == "no_ota":
                    manifest["ota_update_verified"] = False
                elif fault == "no_lora":
                    manifest["ota_update_methods"] = []
                elif fault == "unproven_lora":
                    manifest["verification"] = []
                elif fault == "wrong_platform":
                    manifest["platform"] = "ESP32_PLATFORM"
                elif fault == "internal_storage":
                    manifest["ota_update_requirements"]["lora"]["storage"] = "internal_flash"
                elif fault == "missing_storage":
                    del manifest["ota_update_requirements"]
                else:
                    manifest["capabilities"].remove("sensor.profile.full")
                stale = sensor_pair(target, "internal_flash_and_retained_ram")
                for inputs in (stale + pair, pair + stale):
                    before = deepcopy(inputs)
                    with self.subTest(board=board, fault=fault):
                        with self.assertRaisesRegex(ValueError, "qualified external-QSPI Full/Reduced pair"):
                            select_ordinary_full_records(inputs)
                        self.assertEqual(inputs, before)

    def test_onboard_qspi_retirement_preserves_pairs_files_sources_and_original_identities(self):
        legacy = []
        primaries = {}
        for old, primary in onboard_qspi_retirements():
            legacy.extend(sensor_pair(old.upper(), "internal_flash"))
            if primary not in primaries:
                pair = sensor_pair(primary)
                if primary == "Xiao_nrf52_repeater":
                    prove_runtime_rs232(pair)
                primaries[primary] = pair
        for item in legacy:
            item["manifest"].update(source_commit="original-recovery-source", target_id=1234)
        current = [item for pair in primaries.values() for item in pair]
        for item in current:
            item["manifest"].update(source_commit="qualified-normal-source", target_id=5678)
        unrelated = [record(target, "auto", "NRF52_PLATFORM") for target in (
            "muzi_base_duo_repeater", "muzi_base_uno_room_server", "Nano_G2_Ultra_room_server",
            "LilyGo_T-Echo_Card_room_server", "RAK_3401_room_server", "R1Neo_sensor")]
        for inputs in (legacy + current + unrelated, unrelated + current + legacy,
                       list(reversed(legacy + current + unrelated))):
            before = deepcopy(inputs)
            expected = [item for item in inputs if all(item is not old for old in legacy)]
            selected = select_ordinary_full_records(inputs)
            self.assertEqual(selected, expected)
            self.assertTrue(all(actual is original for actual, original in zip(selected, expected)))
            self.assertEqual(inputs, before)

    def test_all_onboard_qspi_retirements_require_the_exact_complete_matching_pair(self):
        for old, primary in onboard_qspi_retirements():
            for fault in ("missing", "wrong_board", "full_only", "reduced_only", "duplicate",
                          "source_mismatch", "pending_hardware"):
                pair = sensor_pair(primary)
                if primary == "Xiao_nrf52_repeater":
                    prove_runtime_rs232(pair)
                if fault == "missing":
                    pair = []
                elif fault == "wrong_board":
                    pair = sensor_pair("unrelated_room_server_lora_ota")
                elif fault == "full_only":
                    pair = pair[:1]
                elif fault == "reduced_only":
                    pair = pair[1:]
                elif fault == "duplicate":
                    pair.append(deepcopy(pair[0]))
                elif fault == "source_mismatch":
                    pair[0]["manifest"]["source_commit"] = "different-source"
                else:
                    pair[0]["manifest"]["capabilities"].append("hardware.qualification.pending")
                stale = sensor_pair(old, "internal_flash")
                inputs = stale + pair
                before = deepcopy(inputs)
                with self.subTest(old=old, fault=fault):
                    with self.assertRaisesRegex(ValueError, "qualified external-QSPI Full/Reduced pair"):
                        select_ordinary_full_records(inputs)
                    self.assertEqual(inputs, before)

    def test_xiao_bridge_retirement_requires_packaged_runtime_and_linked_driver_proof_in_both_profiles(self):
        for old in ("Xiao_nrf52_repeater_bridge_rs232",
                    "Xiao_nrf52_repeater_bridge_rs232_lora_ota_no_external_sensors"):
            for index in (0, 1):
                for capability in ("bridge.rs232", "bridge.rs232.merged"):
                    for fault in ("missing_capability", "missing_proof", "false_proof",
                                  "wrong_source", "empty_evidence"):
                        pair = prove_runtime_rs232(sensor_pair("Xiao_nrf52_repeater"))
                        manifest = pair[index]["manifest"]
                        check = next(item for item in manifest["verification"]
                                     if item["capability"] == capability)
                        if fault == "missing_capability":
                            manifest["capabilities"].remove(capability)
                        elif fault == "missing_proof":
                            manifest["verification"].remove(check)
                        elif fault == "false_proof":
                            check["present"] = False
                        elif fault == "wrong_source":
                            check["source"] = "filename"
                        else:
                            check["evidence"] = ""
                        inputs = sensor_pair(old, "internal_flash") + pair
                        before = deepcopy(inputs)
                        with self.subTest(old=old, profile=index, capability=capability, fault=fault):
                            with self.assertRaisesRegex(ValueError, "verified runtime RS232 bridge"):
                                select_ordinary_full_records(inputs)
                            self.assertEqual(inputs, before)

    def test_nrf52_full_and_reduced_ota_pair_is_never_collapsed(self):
        for role in ('repeater', 'room_server', 'sensor'):
            target = 'RAK_3401_' + role
            variants = []
            for sensor in ('full', 'reduced'):
                item = record(target, 'auto', 'NRF52_PLATFORM')
                item['manifest'].update(artifact_target=target + '-' + sensor + '-ota',
                                        capabilities=['sensor.profile.' + sensor])
                variants.append(item)
            self.assertEqual(select_ordinary_full_records(variants), variants)

    def test_resume_filters_standard_and_sibling_full_images(self):
        inputs = [
            record("Heltec_v3_repeater_observer_mqtt"),
            record("Heltec_v3_repeater_bridge_espnow"),
            record("Heltec_v3_repeater", capabilities=("bridge.espnow", "bridge.rs232")),
            record("Heltec_v3_repeater", "standard"),
            record("Heltec_v3_room_server"),
            record("Station_G2_repeater"),
            record("Station_G2_repeater_observer_mqtt"),
            record("Tbeam_SX1262_repeater_bridge_espnow"),
            record("Tbeam_SX1262_repeater_observer_mqtt", capabilities=("bridge.espnow",)),
            record("LilyGo_TLora_V2_1_1_6_repeater", capabilities=("bridge.rs232", "bridge.espnow")),
            record("LilyGo_TLora_V2_1_1_6_repeater_observer_mqtt_", capabilities=("bridge.espnow",)),
            record("Meshadventurer_sx1262_repeater"),
            record("Meshadventurer_sx1262_repeater_bridge_espnow"),
            record("RAK_4631_repeater", platform="NRF52_PLATFORM"),
        ]
        selected = select_ordinary_full_records(inputs)
        self.assertEqual({item["manifest"]["target"] for item in selected}, {
            "Heltec_v3_repeater", "Heltec_v3_room_server",
            "Station_G2_repeater_observer_mqtt",
            "Tbeam_SX1262_repeater_observer_mqtt",
            "LilyGo_TLora_V2_1_1_6_repeater",
            "LilyGo_TLora_V2_1_1_6_repeater_observer_mqtt_",
            "Meshadventurer_sx1262_repeater_bridge_espnow",
            "RAK_4631_repeater",
        })
        self.assertEqual(len(selected), 8)  # both TLora Full transport profiles remain

    def test_mke_combined_repeater_filters_stale_bridge_identities_without_relabeling(self):
        primary = record("MKE_s3_repeater", capabilities=("bridge.rs232", "bridge.espnow"))
        legacy = [record("MKE_s3_repeater_bridge_" + bridge, profile)
                  for bridge in ("rs232", "espnow") for profile in ("full", "standard")]
        for inputs in ([primary], legacy + [primary], [primary] + legacy):
            with self.subTest(primary_first=inputs[0] is primary):
                selected = select_ordinary_full_records(inputs)
                self.assertEqual(selected, [primary])
                self.assertIs(selected[0], primary)
                self.assertEqual(selected[0]["manifest"]["target"], "MKE_s3_repeater")

    def test_mke_legacy_images_require_a_qualified_combined_primary(self):
        incomplete = []
        incomplete.append([])
        incomplete.append([record("MKE_s3_repeater", "standard",
                                  capabilities=("bridge.rs232", "bridge.espnow"))])
        for capability in ("bridge.rs232", "bridge.espnow"):
            incomplete.append([record("MKE_s3_repeater", capabilities=(capability,))])
        unqualified = record("MKE_s3_repeater", capabilities=("bridge.rs232", "bridge.espnow"))
        unqualified["manifest"]["verified"] = False
        incomplete.append([unqualified])
        for capability in ("bridge.rs232", "bridge.espnow"):
            missing = record("MKE_s3_repeater", capabilities=("bridge.rs232", "bridge.espnow"))
            next(check for check in missing["manifest"]["verification"]
                 if check["capability"] == capability)["present"] = False
            incomplete.append([missing])
        unproven = record("MKE_s3_repeater", capabilities=("bridge.rs232", "bridge.espnow"))
        unproven["manifest"]["verification"] = []
        incomplete.append([unproven])
        for bridge in ("rs232", "espnow"):
            for normal in incomplete:
                with self.subTest(bridge=bridge, normal=normal):
                    with self.assertRaisesRegex(ValueError, "qualified combined repeater"):
                        select_ordinary_full_records(normal + [record("MKE_s3_repeater_bridge_" + bridge)])

    def test_mke_full_primary_requires_verified_bridges_without_legacy_images(self):
        incomplete = [record("MKE_s3_repeater", capabilities=capabilities)
                      for capabilities in ((), ("bridge.rs232",), ("bridge.espnow",))]
        unqualified = record("MKE_s3_repeater", capabilities=("bridge.rs232", "bridge.espnow"))
        unqualified["manifest"]["verified"] = False
        incomplete.append(unqualified)
        unproven = record("MKE_s3_repeater", capabilities=("bridge.rs232", "bridge.espnow"))
        unproven["manifest"]["verification"] = []
        incomplete.append(unproven)
        wrong_platform = record("MKE_s3_repeater", platform="NRF52_PLATFORM",
                                capabilities=("bridge.rs232", "bridge.espnow"))
        incomplete.append(wrong_platform)
        for capability in ("bridge.rs232", "bridge.espnow"):
            missing = record("MKE_s3_repeater", capabilities=("bridge.rs232", "bridge.espnow"))
            next(check for check in missing["manifest"]["verification"]
                 if check["capability"] == capability)["present"] = False
            incomplete.append(missing)
        for primary in incomplete:
            with self.subTest(primary=primary):
                with self.assertRaisesRegex(ValueError, "qualified combined repeater"):
                    select_ordinary_full_records([primary])

    def test_other_platform_passthrough_and_standard_mke_is_not_a_release(self):
        other_platform = record("RAK_4631_repeater", platform="NRF52_PLATFORM")
        other_platform["manifest"].update(verified=None, capabilities=None, verification=None)
        standard_mke = record("MKE_s3_repeater", "standard")
        inputs = [other_platform, standard_mke]
        self.assertEqual(select_ordinary_full_records(inputs), [other_platform])

    def test_dedicated_espnow_survives_when_plain_image_lacks_the_capability(self):
        inputs = [record("heltec_v4_tft_repeater"),
                  record("heltec_v4_tft_repeater_bridge_espnow", capabilities=("bridge.espnow",))]
        for ordered in (inputs, list(reversed(inputs))):
            self.assertEqual(select_ordinary_full_records(ordered), ordered)

    def test_qualified_combined_plain_image_replaces_only_its_matching_espnow_image(self):
        primary = record("Heltec_v3_repeater", capabilities=("bridge.espnow", "bridge.rs232"))
        other_board = record("heltec_v4_tft_repeater_bridge_espnow", capabilities=("bridge.espnow",))
        for profile in ("full", "standard"):
            legacy = record("Heltec_v3_repeater_bridge_espnow", profile, capabilities=("bridge.espnow",))
            for inputs in ([primary, legacy, other_board], [legacy, primary, other_board]):
                with self.subTest(profile=profile, primary_first=inputs[0] is primary):
                    self.assertEqual(select_ordinary_full_records(inputs), [primary, other_board])

    def test_matching_dedicated_images_survive_all_missing_driver_proof_cases(self):
        for profile in ("full", "standard"):
            for fault in ("missing", "false", "wrong_capability", "false_verified"):
                primary = record("heltec_v4_tft_repeater", capabilities=("bridge.espnow",))
                if fault == "missing":
                    primary["manifest"]["verification"] = []
                elif fault == "false":
                    primary["manifest"]["verification"][0]["present"] = False
                elif fault == "wrong_capability":
                    primary["manifest"]["verification"][0]["capability"] = "bridge.rs232"
                else:
                    primary["manifest"]["verified"] = False
                legacy = record("heltec_v4_tft_repeater_bridge_espnow", profile,
                                capabilities=("bridge.espnow",))
                for inputs in ([primary, legacy], [legacy, primary]):
                    with self.subTest(profile=profile, fault=fault, primary_first=inputs[0] is primary):
                        if fault == "false_verified":
                            with self.assertRaisesRegex(ValueError, "verified"):
                                select_ordinary_full_records(inputs)
                        else:
                            expected = inputs if profile == "full" else [primary]
                            self.assertCountEqual(select_ordinary_full_records(inputs), expected)

    def test_stale_plain_cannot_displace_the_observer_proving_the_combined_driver(self):
        for profile in ("full", "standard"):
            for fault in ("no_capability", "no_proof", "failed_proof", "unverified"):
                stale = record("heltec_v4_tft_repeater", capabilities=("bridge.espnow",))
                if fault == "no_capability":
                    stale["manifest"]["capabilities"] = []
                elif fault == "no_proof":
                    stale["manifest"]["verification"] = []
                elif fault == "failed_proof":
                    stale["manifest"]["verification"][0]["present"] = False
                else:
                    stale["manifest"]["verified"] = False
                observer = record("heltec_v4_tft_repeater_observer_mqtt",
                                  capabilities=("bridge.espnow",))
                legacy = record("heltec_v4_tft_repeater_bridge_espnow", profile,
                                capabilities=("bridge.espnow",))
                for inputs in ([stale, observer, legacy], [legacy, observer, stale],
                               [observer, legacy, stale], [legacy, stale, observer]):
                    with self.subTest(profile=profile, fault=fault, first=inputs[0]["manifest"]["target"]):
                        self.assertEqual(select_ordinary_full_records(inputs), [observer])

    def test_new_non_mqtt_full_repeater_replaces_case_and_underscore_legacy_names(self):
        for primary_name, legacy_name in (
            ("heltec_v4_tft_repeater", "heltec_v4_tft_repeater_bridge_espnow"),
            ("ThinkNode_M2_repeater", "ThinkNode_M2_Repeater_bridge_espnow"),
            ("nibble_zero_connect_repeater_", "nibble_zero_connect_repeater_bridge_espnow_"),
            ("Meshadventurer_sx1262_repeater", "Meshadventurer_sx1262_repeater_bridge_espnow"),
        ):
            primary = record(primary_name, capabilities=("bridge.espnow",))
            for profile in ("full", "standard"):
                legacy = record(legacy_name, profile, capabilities=("bridge.espnow",))
                for inputs in ([primary, legacy], [legacy, primary]):
                    with self.subTest(primary=primary_name, profile=profile):
                        self.assertEqual(select_ordinary_full_records(inputs), [primary])

    def test_combined_observer_with_trailing_underscore_replaces_matching_espnow(self):
        observer = record("LilyGo_TLora_V2_1_1_6_repeater_observer_mqtt_",
                          capabilities=("bridge.espnow",))
        legacy = record("LilyGo_TLora_V2_1_1_6_repeater_bridge_espnow",
                        capabilities=("bridge.espnow",))
        for inputs in ([observer, legacy], [legacy, observer]):
            self.assertEqual(select_ordinary_full_records(inputs), [observer])

    def test_unqualified_plain_image_cannot_replace_dedicated_espnow(self):
        primary = record("heltec_v4_tft_repeater", capabilities=("bridge.espnow",))
        primary["manifest"]["verified"] = False
        legacy = record("heltec_v4_tft_repeater_bridge_espnow", capabilities=("bridge.espnow",))
        with self.assertRaisesRegex(ValueError, "verified"):
            select_ordinary_full_records([primary, legacy])


class BootloaderReleaseOrchestrationTest(unittest.TestCase):
    PROFILE = {"id": "test_board", "files": {"zip": {"sha256": "a" * 64}}}

    def catalog(self):
        return mock.patch.multiple(release, load_bootloader_manifest=mock.Mock(return_value={}),
            select_bootloader_profile=mock.Mock(return_value=(self.PROFILE, {})),
            target_profiles=mock.Mock(side_effect=lambda targets: {target: {
                "role": "room" if "room_" in target else "repeater",
                "sourceHardware": "source_" + target, "hardware": "hardware_" + target}
                for target in targets}))

    def embedded(self, ordinary):
        result = deepcopy(ordinary)
        for item in result:
            manifest = item["manifest"]
            sensor = release.nrf52_sensor_profile(manifest)
            manifest["artifact_target"] = (manifest["target"] + "-with-bootloader-2.4.12-"
                                            + sensor + "-ota")
            manifest["embedded_bootloader"] = dict(release="2.4.12", profile=self.PROFILE["id"],
                manifest_sha256=release.BOOTLOADER_MANIFEST_SHA256, asset_sha256="a" * 64,
                legacy_sha256=None)
        return result

    def test_target_selection_is_exact_role_platform_sorted_and_unique(self):
        eligible = sensor_pair("z_repeater") + sensor_pair("a_room_server")
        excluded = [record("n_companion_radio_full", platform="NRF52_PLATFORM"),
                    record("n_sensor", platform="NRF52_PLATFORM"), record("esp_repeater"),
                    record("rp_room_server", platform="RP2040_PLATFORM")]
        with self.catalog():
            self.assertEqual(release.embedded_bootloader_targets(eligible + excluded),
                             ["a_room_server", "z_repeater"])
            self.assertEqual([call.args[1] for call in release.select_bootloader_profile.call_args_list],
                             ["a_room_server", "z_repeater"])
        with mock.patch.object(release, "load_bootloader_manifest") as load:
            self.assertEqual(release.embedded_bootloader_targets(excluded), [])
            load.assert_not_called()

    def test_unsupported_requested_infrastructure_fails_instead_of_being_omitted(self):
        with self.catalog(), mock.patch.object(release, "select_bootloader_profile",
                side_effect=ValueError("unsupported exact board")):
            with self.assertRaisesRegex(ValueError, "unsupported exact board"):
                release.embedded_bootloader_targets(sensor_pair("missing_repeater"))

    def test_only_unique_exact_source_hardware_catalog_exclusions_are_allowed(self):
        target = "pending_repeater"
        records = sensor_pair(target)
        unavailable = dict(id="pending_board", meshcoreHardware=["source_" + target],
            qualification={"status": "source-pending"}, reason="Exact hardware is not qualified.")
        catalog = {"profiles": [], "unavailableProfiles": [unavailable]}
        expected = dict(target=target, profile="pending_board", source_hardware="source_" + target,
                        status="source-pending", reason=unavailable["reason"])
        with self.catalog(), mock.patch.object(release, "load_bootloader_manifest", return_value=catalog):
            self.assertEqual(release.embedded_bootloader_selection(records), ([], [expected]))
            release.select_bootloader_profile.assert_not_called()
            receipt = release.validate_embedded_bootloader_records(records, [])
            self.assertEqual(receipt["targets"], [])
            self.assertEqual(receipt["firmware_count"], 0)
            self.assertEqual(receipt["exclusions"], [expected])
        for fault in ("alias_only", "duplicate", "available_conflict", "unknown_status", "empty_reason"):
            invalid = deepcopy(catalog)
            if fault == "alias_only":
                invalid["unavailableProfiles"][0]["meshcoreHardware"] = ["hardware_" + target]
            elif fault == "duplicate":
                invalid["unavailableProfiles"].append(deepcopy(unavailable))
            elif fault == "available_conflict":
                invalid["profiles"] = [{"meshcoreHardware": ["hardware_" + target]}]
            elif fault == "unknown_status":
                invalid["unavailableProfiles"][0]["qualification"]["status"] = "unknown"
            else:
                invalid["unavailableProfiles"][0]["reason"] = " "
            with self.subTest(fault=fault), self.catalog(), \
                    mock.patch.object(release, "load_bootloader_manifest", return_value=invalid), \
                    mock.patch.object(release, "select_bootloader_profile", side_effect=ValueError("unmapped")):
                with self.assertRaises(ValueError):
                    release.embedded_bootloader_selection(records)

    def test_embedded_builds_are_sequential_resumable_and_in_separate_scrubbed_output(self):
        ordinary = sensor_pair("z_repeater") + sensor_pair("a_room_server")
        environment = {"OUTPUT_DIR": "old", "OTHER": "keep", "PLATFORMIO_BUILD_DIR": "/pinned",
                       "MESHCORE_NRF52_EMBED_BOOTLOADER": "wrong",
                       "MESHCORE_NRF52_LEGACY_BOOTLOADER": "/unrelated"}
        previous = environment.copy()
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def run(command, log, child):
                self.assertFalse(calls and calls[-1][0] == "running")
                calls.append(("running", command, log, child.copy()))
                self.assertEqual(child["OUTPUT_DIR"], str(root / "embedded"))
                self.assertEqual(child["PLATFORMIO_BUILD_DIR"], "/pinned")
                self.assertEqual(child["OTHER"], "keep")
                self.assertFalse(set(release.BOOTLOADER_ENVIRONMENT) & child.keys())
                calls[-1] = ("finished", command, log, child.copy())
            with self.catalog(), mock.patch.object(release, "collect_artifacts", return_value=ordinary) as collect, \
                    mock.patch.object(release, "run_logged", side_effect=run):
                release.build_embedded_bootloader_artifacts(root / "ordinary", root / "embedded",
                    release.DEFAULT_VERSION, "deadbeef", environment)
            collect.assert_called_once_with(root / "ordinary", release.DEFAULT_VERSION + "-deadbeef")
            self.assertEqual([item[1][3] for item in calls], ["a_room_server", "z_repeater"])
            for _, command, log, _ in calls:
                self.assertEqual(command, ["bash", "build_legacy.sh", "build-firmware", command[3],
                    "--with-bootloader", "2.4.12", "--firmware-version", release.DEFAULT_VERSION,
                    "--radio-preset", "usa-cascade-fixed", "--profile", "cascade",
                    "--require-ota", "--resume"])
                self.assertEqual(log.parent, root / "embedded/build-logs")
            self.assertEqual(environment, previous)
            for ordinary_dir, embedded_dir in ((root, root / "."), (root, root / "child"),
                                               (root / "child", root)):
                with mock.patch.object(release, "collect_artifacts") as collect:
                    with self.assertRaisesRegex(ValueError, "separate output"):
                        release.build_embedded_bootloader_artifacts(ordinary_dir, embedded_dir, "v", "sha", {})
                    collect.assert_not_called()

    def test_embedded_build_failure_stops_the_next_target_and_variant_contamination_rejected(self):
        ordinary = sensor_pair("a_repeater") + sensor_pair("z_repeater")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.catalog(), mock.patch.object(release, "collect_artifacts", return_value=ordinary), \
                    mock.patch.object(release, "run_logged", side_effect=RuntimeError("capacity rejected")) as run:
                with self.assertRaisesRegex(RuntimeError, "capacity rejected"):
                    release.build_embedded_bootloader_artifacts(root / "ordinary", root / "embedded", "v", "sha", {})
                self.assertEqual(run.call_count, 1)
            for contaminant in self.embedded(sensor_pair("a_repeater")):
                contaminant["manifest"].pop("embedded_bootloader")
            with mock.patch.object(release, "collect_artifacts", return_value=[contaminant]), \
                    mock.patch.object(release, "run_logged") as run:
                with self.assertRaisesRegex(ValueError, "ordinary firmware directory"):
                    release.build_embedded_bootloader_artifacts(root / "ordinary", root / "embedded", "v", "sha", {})
                run.assert_not_called()
            # A resumed stale Standard image would be discarded by ordinary
            # selection. It must not hide an embedded image in the ordinary root.
            filtered = record("heltec_v4_repeater", "standard")
            filtered["manifest"]["artifact_target"] = "heltec_v4_repeater-with-bootloader-2.4.12"
            with mock.patch.object(release, "collect_artifacts", return_value=ordinary + [filtered]), \
                    mock.patch.object(release, "run_logged") as run, \
                    mock.patch.object(release, "resolve_pio_build_dir", return_value=root / "pinned"), \
                    mock.patch.object(release, "git", return_value="a" * 40):
                with self.assertRaisesRegex(ValueError, "ordinary firmware directory"):
                    release.build_embedded_bootloader_artifacts(root / "ordinary", root / "embedded", "v", "sha", {})
                with self.assertRaisesRegex(ValueError, "ordinary firmware directory"):
                    release.stage_release(root / "ordinary", root / "migration", root / "release", "v", "a" * 40,
                                          root / "pinned", embedded_work=root / "embedded")
                run.assert_not_called()

    def test_embedded_receipt_requires_each_ordinary_profile_exactly_once(self):
        ordinary = sensor_pair("board_repeater")
        embedded = self.embedded(ordinary)
        before = deepcopy(ordinary + embedded)
        with self.catalog():
            receipt = release.validate_embedded_bootloader_records(ordinary, list(reversed(embedded)))
            self.assertEqual(receipt, dict(release="2.4.12", manifest_sha256=release.BOOTLOADER_MANIFEST_SHA256,
                targets=["board_repeater"], firmware_count=2, exclusions=[]))
            for wrong in ([], embedded[:1], embedded + embedded[:1], self.embedded(sensor_pair("other_repeater"))):
                with self.subTest(artifacts=[item["manifest"]["artifact_target"] for item in wrong]):
                    with self.assertRaises(ValueError):
                        release.validate_embedded_bootloader_records(ordinary, wrong)
            for field, value in (("release", "2.4.11"), ("profile", "other"),
                    ("manifest_sha256", "b" * 64), ("asset_sha256", "c" * 64), ("legacy_sha256", "d" * 64)):
                wrong = deepcopy(embedded)
                wrong[0]["manifest"]["embedded_bootloader"][field] = value
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "qualification differs"):
                    release.validate_embedded_bootloader_records(ordinary, wrong)
            for field, value in (("platform", "ESP32_PLATFORM"), ("target", "other_repeater"),
                                 ("build_profile", "standard")):
                wrong = deepcopy(embedded)
                wrong[0]["manifest"][field] = value
                with self.subTest(field=field), self.assertRaises(ValueError):
                    release.validate_embedded_bootloader_records(ordinary, wrong)
        self.assertEqual(ordinary + embedded, before)

    def test_main_default_and_explicit_version_keep_all_four_phases_isolated(self):
        for version_arg in ([], ["--firmware-version", "v1.18.0.2-halo-keymind-cascade-dev"]):
            with self.subTest(argv=version_arg), tempfile.TemporaryDirectory() as temp:
                root, source = Path(temp), "deadbeef" + "a" * 32
                version = version_arg[-1] if version_arg else "v1.18.0.1-halo-keymind-cascade-dev"
                label = version + "-deadbeef"
                phases = []
                environment = {"OTHER": "keep", "MESHCORE_NRF52_EMBED_BOOTLOADER": "2.4.11",
                               "MESHCORE_NRF52_LEGACY_BOOTLOADER": "wrong"}
                def capture(name, env):
                    self.assertEqual(env["OTHER"], "keep")
                    self.assertFalse(set(release.BOOTLOADER_ENVIRONMENT) & env.keys())
                    self.assertEqual(env["PLATFORMIO_BUILD_DIR"], str(root / "pinned"))
                    phases.append(name)
                with mock.patch.object(release, "ROOT", root), \
                        mock.patch.object(release, "git", side_effect=lambda *args: "" if args[0] == "status" else source), \
                        mock.patch.dict(os.environ, environment, clear=True), \
                        mock.patch.object(release, "resolve_pio_build_dir", return_value=root / "pinned"), \
                        mock.patch.object(release, "run_logged", side_effect=lambda command, log, env: capture("matrix", env)) as matrix, \
                        mock.patch.object(release, "build_embedded_bootloader_artifacts", side_effect=lambda *args: capture("embedded", args[4])) as embed, \
                        mock.patch.object(release, "build_migration_artifacts", side_effect=lambda *args: capture("migration", args[4])) as migration, \
                        mock.patch.object(release, "stage_release", side_effect=lambda *args, **kwargs: phases.append("stage")) as stage, \
                        mock.patch.object(sys, "argv", ["release"] + version_arg), redirect_stdout(io.StringIO()):
                    release.main()
                self.assertEqual(phases, ["matrix", "embedded", "migration", "stage"])
                ordinary = root / ".releases" / (".build-" + label)
                embedded = root / ".releases" / (".bootloader-build-" + label)
                migrated = root / ".releases" / (".migration-build-" + label)
                self.assertEqual(matrix.call_args.args[2]["OUTPUT_DIR"], str(ordinary))
                self.assertEqual(embed.call_args.args[:4], (ordinary, embedded, version, "deadbeef"))
                self.assertEqual(migration.call_args.args[:3], (migrated, version, "deadbeef"))
                self.assertEqual(stage.call_args.args[:5], (ordinary, migrated, root / ".releases" / label, version, source))
                self.assertEqual(stage.call_args.kwargs, {"embedded_work": embedded})
                self.assertEqual(environment["MESHCORE_NRF52_EMBED_BOOTLOADER"], "2.4.11")

    def test_two_workers_join_before_one_migration_and_staging_pass(self):
        import parallel_release_workers as workers
        with tempfile.TemporaryDirectory() as temp:
            root, source = Path(temp), "deadbeef" + "a" * 32
            phases = []
            with mock.patch.object(release, "ROOT", root), \
                    mock.patch.object(release, "git", side_effect=lambda *args: "" if args[0] == "status" else source), \
                    mock.patch.object(release, "resolve_pio_build_dir", return_value=root / "pinned"), \
                    mock.patch.object(workers, "build_parallel_matrix", side_effect=lambda *args: phases.append("matrix")) as matrix, \
                    mock.patch.object(workers, "build_parallel_embedded", side_effect=lambda *args: phases.append("embedded")) as embedded, \
                    mock.patch.object(release, "run_logged") as serial, \
                    mock.patch.object(release, "build_embedded_bootloader_artifacts") as serial_embed, \
                    mock.patch.object(release, "build_migration_artifacts", side_effect=lambda *args: phases.append("migration")) as migration, \
                    mock.patch.object(release, "stage_release", side_effect=lambda *args, **kwargs: phases.append("stage")) as stage, \
                    mock.patch.object(sys, "argv", ["release", "--workers", "2", "--pio-jobs", "2"]), redirect_stdout(io.StringIO()):
                release.main()
            self.assertEqual(phases, ["matrix", "embedded", "migration", "stage"])
            self.assertEqual(matrix.call_args.args[:5], (root, release.DEFAULT_VERSION, source, 2, 2))
            self.assertEqual(embedded.call_args.args[:5], (root, release.DEFAULT_VERSION, source, 2, 2))
            serial.assert_not_called()
            serial_embed.assert_not_called()
            migration.assert_called_once()
            stage.assert_called_once()

    def test_nonempty_embedded_resume_requires_opt_in_and_dry_run_never_builds(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source = Path(temp), "deadbeef" + "a" * 32
            work = root / ".releases" / (".bootloader-build-" + release.DEFAULT_VERSION + "-deadbeef")
            work.mkdir(parents=True)
            proof = work / "prior-artifact"
            proof.write_bytes(b"preserve")
            with mock.patch.object(release, "ROOT", root), \
                    mock.patch.object(release, "git", side_effect=lambda *args: "" if args[0] == "status" else source), \
                    mock.patch.object(release, "run_logged") as run, \
                    mock.patch.object(release, "resolve_pio_build_dir") as resolve, \
                    mock.patch.object(sys, "argv", ["release"]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    release.main()
                run.assert_not_called()
                resolve.assert_not_called()
                with mock.patch.object(sys, "argv", ["release", "--dry-run"]):
                    release.main()
                run.assert_not_called()
                resolve.assert_not_called()
            self.assertEqual(proof.read_bytes(), b"preserve")

    def test_stage_keeps_both_variants_and_auditable_standalone_metadata(self):
        ordinary = sensor_pair("board_repeater")
        embedded = self.embedded(ordinary)
        source = "deadbeef" + "a" * 32
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work, embedded_work = root / "ordinary", root / "embedded"
            work.mkdir(); embedded_work.mkdir()
            for item in ordinary + embedded:
                stem = item["manifest"]["artifact_target"]
                path = (embedded_work if "embedded_bootloader" in item["manifest"] else work) / (stem + ".zip")
                path.write_bytes(stem.encode("ascii"))
                item["files"] = [path]
            catalog = {"version": "2.4.12", "inventory_path": "inventory.json", "profile_count": 23}
            def stage_catalog(destination, *, project_root):
                destination.mkdir()
                (destination / "inventory.json").write_text(json.dumps(catalog))
                return catalog
            with self.catalog(), mock.patch.object(release, "git", return_value=source), \
                    mock.patch.object(release, "resolve_pio_build_dir", return_value=root / "pinned"), \
                    mock.patch.object(release, "collect_artifacts", side_effect=[ordinary, embedded]) as collect, \
                    mock.patch.object(release, "BOARDS", {}), mock.patch.object(release, "bundle_release"), \
                    mock.patch.object(release.subprocess, "run", return_value=mock.Mock(returncode=0)), \
                    mock.patch("package_nrf52_bootloaders.stage_bootloader_catalog", side_effect=stage_catalog), \
                    redirect_stdout(io.StringIO()):
                release.stage_release(work, root / "migration", root / "release", release.DEFAULT_VERSION,
                                      source, root / "pinned", embedded_work=embedded_work)
            self.assertEqual([call.args[0] for call in collect.call_args_list], [work, embedded_work])
            manifest = json.loads((root / "release/manifest.json").read_text())
            self.assertEqual(manifest["firmware_target_count"], 4)
            self.assertEqual([item["embedded_bootloader"] is not None for item in manifest["firmware"]],
                             [False, False, True, True])
            self.assertEqual(manifest["nrf52_bootloader_variants"]["firmware_count"], 2)
            self.assertEqual(manifest["nrf52_bootloaders"], catalog)
            for entry in manifest["firmware"]:
                self.assertEqual(len(entry["files"]), 1)
                path = root / "release" / entry["files"][0]
                self.assertEqual(path.read_bytes(), entry["artifact_target"].encode("ascii"))
            sums = (root / "release/SHA256SUMS.txt").read_text()
            self.assertIn("nrf52-bootloaders/inventory.json", sums)
            self.assertIn("70 seconds", (root / "release/README.md").read_text())


if __name__ == "__main__":
    unittest.main()
