#!/usr/bin/env python3
"""Release integration checks for exact asset routing and stable staging."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_mesh_america_release as catalogs
import package_cascade_release as package


SOURCE = "a" * 40
VERSION = "v1.17.1.9-halo-keymind-cascade-dev"
FAMILY = VERSION + "-" + SOURCE[:8]


class ReleasePublicationTest(unittest.TestCase):
    def record(self, directory, target, platform="ESP32_PLATFORM", sensor="", *,
               publication_target=None, build_profile="auto"):
        directory.mkdir(parents=True, exist_ok=True)
        stem = (publication_target or target) + "-" + FAMILY
        names = [stem + suffix for suffix in (
            [".uf2", ".zip"] if platform == "NRF52_PLATFORM" else [".bin", "-merged.bin"])]
        names += [stem + ".capabilities.json", stem + ".memory.json"]
        for name in names:
            (directory / name).write_bytes((name + "\n").encode("ascii"))
        row = {"artifact_target": target, "target": target.removesuffix("-" + sensor + "-ota") if sensor else target,
               "platform": platform, "build_profile": build_profile, "verified": True,
               "sensor_profile": sensor, "files": names, "reductions": [],
               "source_commit": SOURCE, "ota_update_methods": ["lora"]}
        return row, [directory / name for name in names]

    def stage(self, directory):
        definitions = [("companion", "Station_G2_companion_radio_full", "ESP32_PLATFORM", ""),
                       ("repeater-room", "Station_G2_repeater", "ESP32_PLATFORM", ""),
                       ("lora-ota", "RAK_4631_repeater-full-ota", "NRF52_PLATFORM", "full"),
                       ("lora-ota", "RAK_4631_repeater-reduced-ota", "NRF52_PLATFORM", "reduced")]
        groups = {}
        for group, target, platform, sensor in definitions:
            row, files = self.record(directory / group, target, platform, sensor)
            groups.setdefault(group, []).append(row)
        plan = {"source": SOURCE, "version": "1.17.1.9", "groups": []}
        for group, rows in groups.items():
            path = directory / group
            (path / "TARGET-MANIFEST.json").write_text(json.dumps(rows))
            (path / "SHA256SUMS.txt").write_text("".join(
                hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
                for p in sorted(path.iterdir())))
            plan["groups"].append({"key": group, "tag": FAMILY if group == "companion" else group + "-" + FAMILY,
                                   "target_count": len(rows)})
        (directory / "release-plan.json").write_text(json.dumps(plan))
        return plan

    def template(self):
        devices = []
        for hardware, platform, targets in (
                ("Station G2", "esp32", ["Station_G2_companion_radio_full", "Station_G2_repeater"]),
                ("RAK4631", "nrf52", ["RAK_4631_repeater"])):
            devices.append({"name": hardware, "type": platform, "maker": "test", "firmware": [
                {"role": "repeater", "version": {"old": {"notes": "stale old URLs",
                    "files": [{"name": target + "-v1.17.1.8-dev-12345678.bin"}]}}}
                for target in targets]})
        return {"maker": {"test": {"name": "Test"}}, "device": devices}

    def test_catalog_routes_each_asset_to_its_manifest_group_and_keeps_sensor_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.stage(directory)
            plan, family, records = catalogs.read_stage(directory)
            template = self.template()
            targets = {row["artifact_target"] for row, _, _ in records} | {
                "Station_G2_companion_radio_full", "Station_G2_repeater", "RAK_4631_repeater"}
            result = catalogs.generate(template, plan, family, records,
                                       catalogs.target_profiles(targets), "mikecarper/MeshCore")
            self.assertEqual([len(d["firmware"]) for d in result["device"]], [2, 2])
            for device in result["device"]:
                for firmware in device["firmware"]:
                    version = firmware["version"][FAMILY]
                    self.assertNotIn("stale", version["notes"])
                    for file in version["files"]:
                        target = catalogs.identity(file["name"])
                        expected = next(tag for row, tag, _ in records if row["artifact_target"] == target)
                        self.assertIn("/download/" + expected + "/", file["url"])
            self.assertEqual(template, self.template(), "input catalog was mutated")

    def test_catalog_rejects_corrupt_assets_and_cross_family_tags(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plan = self.stage(directory)
            binary = next((directory / "companion").glob("*.bin"))
            original = binary.read_bytes()
            binary.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "checksum"):
                catalogs.read_stage(directory)
            binary.write_bytes(original)
            plan["groups"][1]["tag"] = FAMILY
            (directory / "release-plan.json").write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, "different family"):
                catalogs.read_stage(directory)

    def test_catalog_distinguishes_qualified_bootloader_firmware_from_ordinary_images(self):
        from nrf52_embedded_bootloader import (BOOT_SIZE, MANIFEST_SHA256, VERSION as BL_VERSION,
                                              load_manifest, select_profile)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plan = self.stage(directory)
            target = "RAK_4631_repeater"
            artifact = target + "-with-bootloader-" + BL_VERSION + "-full-ota"
            row, _ = self.record(directory / "lora-ota", artifact, "NRF52_PLATFORM", "full")
            row["target"] = target
            profile, _ = select_profile(load_manifest(), target)
            row["capabilities"] = ["bootloader.embedded"]
            row["verification"] = [{"capability": "bootloader.embedded", "present": True,
                                     "source": "packaged application"}]
            row["embedded_bootloader"] = {
                "release": BL_VERSION, "profile": profile["id"],
                "candidate_sha256": "a" * 64,
                "asset_sha256": profile["files"]["zip"]["sha256"],
                "manifest_sha256": MANIFEST_SHA256, "legacy_sha256": None,
                "app_offset": 4, "image_bytes": BOOT_SIZE,
                "activation": "ota bootloader update", "automatic_delay_seconds": 70}
            folder = directory / "lora-ota"
            rows = json.loads((folder / "TARGET-MANIFEST.json").read_text()) + [row]
            def inventory():
                (folder / "TARGET-MANIFEST.json").write_text(json.dumps(rows))
                (folder / "SHA256SUMS.txt").write_text("".join(
                    hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
                    for p in sorted(folder.iterdir()) if p.name != "SHA256SUMS.txt"))
            inventory()
            next(group for group in plan["groups"] if group["key"] == "lora-ota")["target_count"] = 3
            (directory / "release-plan.json").write_text(json.dumps(plan))
            plan, family, records = catalogs.read_stage(directory)
            targets = {r["publication_target"] for r, _, _ in records} | {
                "Station_G2_companion_radio_full", "Station_G2_repeater", target}
            result = catalogs.generate(self.template(), plan, family, records,
                                       catalogs.target_profiles(targets), "mikecarper/MeshCore")
            entries = result["device"][1]["firmware"]
            self.assertEqual(len(entries), 3)
            embedded = next(item for item in entries if artifact in item["subTitle"])
            self.assertIn("OTAFIX " + BL_VERSION + " bootloader", embedded["subTitle"])
            self.assertIn("70 seconds", embedded["version"][FAMILY]["notes"])
            for ordinary in (item for item in entries if item is not embedded):
                self.assertIn("preserve the installed bootloader", ordinary["version"][FAMILY]["notes"])
            # A publication name cannot opt into automatic bootloader changes.
            row.pop("embedded_bootloader")
            inventory()
            with self.assertRaisesRegex(ValueError, "embedded bootloader lacks qualified metadata"):
                catalogs.read_stage(directory)

    def test_catalog_does_not_guess_an_unknown_hardware_card(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            row, files = self.record(directory, "Unknown_repeater")
            targets = {"Unknown_repeater", "Station_G2_companion_radio_full",
                       "Station_G2_repeater", "RAK_4631_repeater"}
            with self.assertRaisesRegex(ValueError, "exact hardware card"):
                catalogs.generate(self.template(), {"version": "1.17.1.9", "source": SOURCE, "groups": []},
                                  FAMILY, [(row, FAMILY, files)], catalogs.target_profiles(targets), "mikecarper/MeshCore")

    def test_catalog_retains_download_only_cards_for_new_sensor_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.stage(directory)
            plan, family, records = catalogs.read_stage(directory)
            template = self.template()
            template["device"][1]["type"] = "noflash"
            targets = {row["artifact_target"] for row, _, _ in records} | {
                "Station_G2_companion_radio_full", "Station_G2_repeater", "RAK_4631_repeater"}
            result = catalogs.generate(template, plan, family, records,
                                       catalogs.target_profiles(targets), "mikecarper/MeshCore")
            device = result["device"][1]
            self.assertEqual(device["type"], "noflash")
            self.assertEqual(len(device["firmware"]), 2)
            for firmware in device["firmware"]:
                version = firmware["version"][FAMILY]
                self.assertIn("Downloads only", version["notes"])
                self.assertTrue(all(file["type"] == "download" for file in version["files"]))
            # Even a merged ESP32 image must not enable a download-only card.
            for name in ("example-merged.bin", "example.bin", "example.zip", "example.uf2"):
                self.assertEqual(catalogs.file_entry(Path(name), "noflash", FAMILY,
                                                    "mikecarper/MeshCore")["type"], "download")
            # Real flasher/chip mismatches still fail instead of being guessed.
            template["device"][1]["type"] = "esp32"
            with self.assertRaisesRegex(ValueError, "exact hardware card|platform differs"):
                catalogs.generate(template, plan, family, records,
                                  catalogs.target_profiles(targets), "mikecarper/MeshCore")

    def test_catalog_accepts_standard_and_full_with_the_same_logical_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plan = self.stage(directory)
            target = "Station_G2_repeater"
            published = target + "-full-usb-wifi-ota"
            row, _ = self.record(directory / "full-profiles", target,
                                 publication_target=published, build_profile="full")
            folder = directory / "full-profiles"
            (folder / "TARGET-MANIFEST.json").write_text(json.dumps([row]))
            (folder / "SHA256SUMS.txt").write_text("".join(
                hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
                for p in sorted(folder.iterdir())))
            plan["groups"].append({"key": "full-profiles", "tag": "full-profiles-" + FAMILY,
                                   "target_count": 1})
            (directory / "release-plan.json").write_text(json.dumps(plan))
            plan, family, records = catalogs.read_stage(directory)
            self.assertEqual([row["artifact_target"] for row, _, _ in records].count(target), 2)
            targets = {row["publication_target"] for row, _, _ in records} | {
                "Station_G2_companion_radio_full", target, "RAK_4631_repeater"}
            result = catalogs.generate(self.template(), plan, family, records,
                                       catalogs.target_profiles(targets), "mikecarper/MeshCore")
            entries = result["device"][0]["firmware"]
            self.assertEqual(len(entries), 3)
            full = next(entry for entry in entries if entry["subTitle"].startswith(published))
            self.assertIn("Full profile", full["subTitle"])
            self.assertTrue(all("/full-profiles-" + FAMILY + "/" in file["url"]
                                for file in full["version"][FAMILY]["files"]))
            # Matching filenames do not authorize a profile for another board
            # or a fabricated Full infix on a standard qualified build.
            for change in ({"artifact_target": "Other_repeater"}, {"build_profile": "auto"},
                           {"source_commit": "b" * 40}):
                (folder / "TARGET-MANIFEST.json").write_text(json.dumps([{**row, **change}]))
                (folder / "SHA256SUMS.txt").write_text("".join(
                    hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
                    for p in sorted(folder.iterdir()) if p.name != "SHA256SUMS.txt"))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    catalogs.read_stage(directory)
            # A second source suffix cannot hide inside an otherwise familiar
            # family filename while keeping the original cap/memory stems.
            wrong = {**row, "files": list(row["files"])}
            for index, name in enumerate(wrong["files"]):
                if name.endswith(".bin"):
                    extra = name.replace(FAMILY, FAMILY + "-bbbbbbbb")
                    (folder / name).rename(folder / extra)
                    wrong["files"][index] = extra
            (folder / "TARGET-MANIFEST.json").write_text(json.dumps([wrong]))
            (folder / "SHA256SUMS.txt").write_text("".join(
                hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
                for p in sorted(folder.iterdir()) if p.name != "SHA256SUMS.txt"))
            with self.assertRaisesRegex(ValueError, "exact release source/version"):
                catalogs.read_stage(directory)

    def test_local_release_distinguishes_standard_and_full_by_exact_qualified_files(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            target = "Station_G2_companion_radio_usb"
            folder = directory / "firmware/companion"
            standard, standard_files = self.record(folder, target)
            full, full_files = self.record(folder, target,
                publication_target=target + "-full-usb-wifi-ota", build_profile="full")
            records = [{"manifest": row, "files": files} for row, files in
                       ((standard, standard_files), (full, full_files))]
            manifest = {"format": "meshcore-local-release-v1", "source_commit": SOURCE,
                        "firmware_version": VERSION, "profile": "cascade", "publication": "local-only",
                        "radio": {"frequency_mhz": 910.525, "bandwidth_khz": 62.5,
                                  "spreading_factor": 7, "coding_rate": 5},
                        "firmware_target_count": 2, "migration_board_role_count": 1,
                        "firmware": [{"target": row["target"], "artifact_target": row["artifact_target"],
                            "profile": row["build_profile"], "platform": row["platform"],
                            "files": [str(p.relative_to(directory)) for p in files]}
                            for row, files in ((standard, standard_files), (full, full_files))]}
            migrations = directory / "esp32-partition-migration"
            migrations.mkdir()
            archive = migrations / ("example-" + FAMILY + "-migration.zip")
            archive.write_bytes(b"qualified migration fixture")
            bundle = directory / ("esp32-partition-migration-" + FAMILY + "-release.zip")
            with zipfile.ZipFile(bundle, "w") as z:
                z.writestr("release-manifest.json", "{}")
                z.writestr(archive.name, archive.read_bytes())
            def write_inventory():
                (directory / "manifest.json").write_text(json.dumps(manifest))
                (directory / "SHA256SUMS.txt").write_text("".join(
                    hashlib.sha256(p.read_bytes()).hexdigest() + "  " + str(p.relative_to(directory)) + "\n"
                    for p in sorted(directory.rglob("*")) if p.is_file() and p.name != "SHA256SUMS.txt"))
            with patch.object(package, "collect_artifacts", return_value=records), \
                 patch("package_esp32_partition_migration.BOARDS", {"example": {"target": target}}), \
                 patch("build_esp32_partition_migration.verify_archive") as verify_migration:
                write_inventory()
                self.assertEqual(len(package.collect_local_release(directory, VERSION, SOURCE)[0]), 2)
                self.assertEqual(verify_migration.call_args.kwargs["full_image"],
                                 next(path for path in full_files if path.suffix == ".bin"
                                      and not path.name.endswith("-merged.bin")))
                for field, value in (("artifact_target", "Other_companion_radio_usb"),
                                     ("profile", "auto")):
                    original = manifest["firmware"][1][field]
                    manifest["firmware"][1][field] = value
                    write_inventory()
                    with self.subTest(field=field), self.assertRaisesRegex(ValueError, "manifest disagrees"):
                        package.collect_local_release(directory, VERSION, SOURCE)
                    manifest["firmware"][1][field] = original
                manifest["firmware"][1]["files"] = manifest["firmware"][0]["files"]
                write_inventory()
                with self.assertRaisesRegex(ValueError, "inventory disagrees"):
                    package.collect_local_release(directory, VERSION, SOURCE)

    def test_stable_publication_notes_and_assets_are_ascii(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            row, files = self.record(base / "input", "Station_G2_companion_radio_usb")
            row.pop("source_commit")  # Actual older cap manifests inherit validated release provenance.
            output = base / "stage"
            argv = ["package", "--input", str(base / "input"), "--output", str(output),
                    "--local-release", "--stable", "--version", "1.17.1.9", "--commit", SOURCE]
            with patch.object(sys, "argv", argv), patch.object(package, "collect_local_release",
                    return_value=([{"manifest": row, "files": files}], [],
                                  {"frequency": "910.525", "bandwidth": "62.5", "sf": "7", "cr": "5"})):
                package.main()
            plan = json.loads((output / "release-plan.json").read_text())
            self.assertFalse(plan["groups"][0]["prerelease"])
            staged_rows = json.loads((output / "companion/TARGET-MANIFEST.json").read_text())
            self.assertEqual(staged_rows[0]["source_commit"], SOURCE)
            self.assertNotIn("source_commit", row, "raw capability record was relabeled")
            notes = (output / "companion-notes.md").read_text(encoding="ascii")
            self.assertIn("Stable release", notes)
            self.assertNotIn("Development prerelease", notes)
            for path in output.rglob("*"):
                if path.is_file():
                    path.read_text(encoding="ascii")
            bad = {**row, "source_commit": "b" * 40}
            bad_argv = list(argv)
            bad_argv[bad_argv.index(str(output))] = str(base / "wrong-source-stage")
            with patch.object(sys, "argv", bad_argv), patch.object(package, "collect_local_release",
                    return_value=([{"manifest": bad, "files": files}], [],
                                  {"frequency": "910.525", "bandwidth": "62.5", "sf": "7", "cr": "5"})), \
                    self.assertRaisesRegex(ValueError, "another source"):
                package.main()

    def test_page_descriptions_follow_actual_platforms_roles_and_tool_types(self):
        def record(target, platform="ESP32_PLATFORM", methods=("lora",)):
            return {"manifest": {"target": target, "platform": platform,
                                 "ota_update_methods": list(methods)}}

        nrf = [record("sample_repeater_lora_ota", "NRF52_PLATFORM"),
               record("sample_room_server_lora_ota", "NRF52_PLATFORM"),
               record("sample_sensor_lora_ota", "NRF52_PLATFORM")]
        label, purpose = package.release_group_description("lora-ota", nrf)
        self.assertEqual(label, "nRF52 Repeater, Room Server and Sensor firmware")
        self.assertIn("LoRa OTA reception", purpose)
        # Historical matrices also had ESP32 and mixed-platform LoRa pages.
        esp = [record("legacy_repeater_lora_ota")]
        self.assertEqual(package.release_group_description("lora-ota", esp)[0], "ESP32 Repeater firmware")
        self.assertEqual(package.release_group_description("lora-ota", esp + nrf)[0], "LoRa OTA firmware")
        self.assertNotIn("LoRa", package.release_group_description("lora-ota", [
            record("legacy_repeater_lora_ota", methods=("wifi",))])[1])
        full = [record("sample_repeater"), record("sample_room_server"), record("sample_sensor")]
        self.assertEqual(package.release_group_description("full-profiles", full)[0],
                         "ESP32 Repeater, Room Server and Sensor firmware")
        for files, title in (([Path("board-migration.zip")], "Migration tools"),
                             ([Path("board_bootloader.hex")], "Bootloader tools"),
                             ([Path("board-migration.zip"), Path("board_bootloader.hex")],
                              "Migration and bootloader tools")):
            with self.subTest(title=title):
                self.assertEqual(package.release_group_description("utility", [], files)[0], title)
        utility = [record("sample_sensor"), record("sample_terminal_chat")]
        self.assertEqual(package.release_group_description("utility", utility)[0], "Sensor and Terminal firmware")
        self.assertEqual(package.release_group_description("utility", utility, [Path("board-migration.zip")])[0],
                         "Sensor and Terminal firmware and upgrade tools")

    def test_staged_notes_lead_with_picker_and_link_human_role_choices(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            companion, companion_files = self.record(base / "input", "Station_G2_companion_radio_full")
            repeater, repeater_files = self.record(base / "input", "Station_G2_repeater", build_profile="full")
            records = [{"manifest": companion, "files": companion_files},
                       {"manifest": repeater, "files": repeater_files}]
            extras = [base / "board-migration.zip", base / "board_bootloader.hex"]
            for path in extras:
                path.write_bytes(b"exact-board tool fixture\n")
            before = {path: path.read_bytes() for path in companion_files + repeater_files + extras}
            output = base / "stage"
            argv = ["package", "--input", str(base / "input"), "--output", str(output),
                    "--local-release", "--stable", "--version", "1.17.1.9", "--commit", SOURCE]
            with patch.object(sys, "argv", argv), patch.object(package, "collect_local_release",
                    return_value=(records, extras,
                                  {"frequency": "910.525", "bandwidth": "62.5", "sf": "7", "cr": "5"})):
                package.main()
            plan = json.loads((output / "release-plan.json").read_text())
            self.assertEqual([group["key"] for group in plan["groups"]], ["companion", "full-profiles", "utility"])
            guide = "https://mikecarper.github.io/MeshCore/releases/1.17.1.9/"
            for group in plan["groups"]:
                notes = (output / (group["key"] + "-notes.md")).read_text(encoding="ascii")
                self.assertTrue(notes.startswith("[Open the firmware picker](https://mikecarper.github.io/MeshCore/firmware_picker/)"))
                self.assertIn("[What's new in 1.17.1.9](" + guide + ")", notes)
                self.assertIn("[Installation and upgrade guide](" + guide + ")", notes)
                self.assertIn("[Companion firmware](https://github.com/mikecarper/MeshCore/releases/tag/" + FAMILY + ")", notes)
                self.assertIn("[ESP32 Repeater firmware](https://github.com/mikecarper/MeshCore/releases/tag/full-profiles-" + FAMILY + ")", notes)
                self.assertIn("[Migration and bootloader tools](https://github.com/mikecarper/MeshCore/releases/tag/utility-" + FAMILY + ")", notes)
                self.assertNotIn("[full-profiles]", notes)
                self.assertNotIn("qualified firmware profiles", notes)
                self.assertIn("**Testing:**", notes)
                self.assertIn("Physical coverage varies by board and update route", notes)
                self.assertIn("Saved radio settings take precedence", notes)
                self.assertIn(SOURCE, notes)
                self.assertIn(FAMILY, notes)
                self.assertEqual((output / group["key"] / "BUILD-NOTES.txt").read_text(), notes)
                for line in (output / group["key"] / "SHA256SUMS.txt").read_text().splitlines():
                    expected, name = line.split("  ", 1)
                    self.assertEqual(hashlib.sha256((output / group["key"] / name).read_bytes()).hexdigest(), expected)
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_split_pages_keep_tags_and_explain_one_version_with_human_links(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            records = []
            for index in range(3):
                target = f"board_{index}_repeater_lora_ota"
                row, files = self.record(base / "input", target, platform="NRF52_PLATFORM")
                # Exercise the real 900-file split with many tiny report assets.
                for number in range(447):
                    path = base / "input" / f"{target}-report-{number}.txt"
                    path.write_bytes(b"qualification report fixture\n")
                    files.append(path)
                self.assertEqual(len(files), 451)
                records.append({"manifest": row, "files": files})
            output = base / "stage"
            argv = ["package", "--input", str(base / "input"), "--output", str(output),
                    "--local-release", "--stable", "--version", "1.17.1.9", "--commit", SOURCE]
            with patch.object(sys, "argv", argv), patch.object(package, "collect_local_release",
                    return_value=(records, [],
                                  {"frequency": "910.525", "bandwidth": "62.5", "sf": "7", "cr": "5"})):
                package.main()
            plan = json.loads((output / "release-plan.json").read_text())
            self.assertEqual([group["key"] for group in plan["groups"]], ["lora-ota", "lora-ota-2", "lora-ota-3"])
            for number, group in enumerate(plan["groups"], 1):
                self.assertEqual(group["tag"], group["key"] + "-" + FAMILY)
                self.assertEqual(group["title"], f"MeshCore 1.17.1.9 - nRF52 Repeater firmware (part {number} of 3)")
                notes = (output / (group["key"] + "-notes.md")).read_text(encoding="ascii")
                self.assertIn("split for download limits and contain the same firmware version", notes)
                for other in range(1, 4):
                    self.assertIn(f"[nRF52 Repeater firmware (part {other} of 3)]", notes)
                self.assertEqual(group["asset_count"], 457)

    def test_local_release_preserves_selected_profiles_and_verifies_complete_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            row, files = self.record(directory / "firmware/companion", "Station_G2_companion_radio_usb")
            manifest = {"format": "meshcore-local-release-v1", "source_commit": SOURCE,
                        "firmware_version": VERSION, "profile": "cascade", "publication": "local-only",
                        "radio": {"frequency_mhz": 910.525, "bandwidth_khz": 62.5,
                                  "spreading_factor": 7, "coding_rate": 5},
                        "firmware_target_count": 1, "migration_board_role_count": 1,
                        "firmware": [{"target": row["target"], "artifact_target": row["artifact_target"],
                                      "profile": "auto", "platform": "ESP32_PLATFORM",
                                      "files": [str(p.relative_to(directory)) for p in files]}]}
            (directory / "manifest.json").write_text(json.dumps(manifest))
            migrations = directory / "esp32-partition-migration"
            migrations.mkdir()
            archive = migrations / ("example-" + FAMILY + "-migration.zip")
            archive.write_bytes(b"qualified migration fixture")
            bundle = directory / ("esp32-partition-migration-" + FAMILY + "-release.zip")
            with zipfile.ZipFile(bundle, "w") as z:
                z.writestr("release-manifest.json", "{}")
                z.writestr(archive.name, archive.read_bytes())
            checksum = directory / "SHA256SUMS.txt"
            checksum.write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " +
                                        str(p.relative_to(directory)) + "\n"
                                        for p in sorted(directory.rglob("*")) if p.is_file()))
            with patch.object(package, "collect_artifacts", return_value=[{"manifest": row, "files": files}]), \
                    patch("package_esp32_partition_migration.BOARDS", {"example": {}}), \
                    patch("build_esp32_partition_migration.verify_archive"):
                records, extras, _ = package.collect_local_release(directory, VERSION, SOURCE)
                self.assertEqual(len(records), 1)
                self.assertEqual(extras, [archive, bundle])
                with self.assertRaisesRegex(ValueError, "another source"):
                    package.collect_local_release(directory, VERSION, "b" * 40)
                (directory / "unqualified.bin").write_bytes(b"unexpected")
                with self.assertRaisesRegex(ValueError, "inventory is incomplete"):
                    package.collect_local_release(directory, VERSION, SOURCE)


if __name__ == "__main__":
    unittest.main()
