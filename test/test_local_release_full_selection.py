#!/usr/bin/env python3
"""Full selection preserves qualified transports and board capacity exceptions."""

from copy import deepcopy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_local_release import select_ordinary_full_records


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
            "Xiao_nrf52_room_server", "Xiao_nrf52_room_server_lora_ota_no_external_sensors",
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


if __name__ == "__main__":
    unittest.main()
