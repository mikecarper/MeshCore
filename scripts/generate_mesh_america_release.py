#!/usr/bin/env python3
"""Generate Mesh America catalogs from exact, staged release assets."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote
from package_cascade_release import embedded_bootloader_variant


ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".bin", ".zip", ".uf2", ".hex"}
PLATFORMS = {"ESP32_PLATFORM": "esp32", "NRF52_PLATFORM": "nrf52",
             "RP2040_PLATFORM": "noflash", "STM32_PLATFORM": "noflash"}
ADDITIONAL_HARDWARE_CARDS = {
    ("esp32", "Waveshare_S3_LR1121"): {
        "maker": "waveshare", "name": "Waveshare ESP32-S3 LR1121", "type": "esp32"},
    ("esp32", "Wio_Tracker_L2"): {
        "maker": "seeed", "name": "Seeed Wio Tracker L2", "type": "esp32"},
}


def identity(name):
    stem = Path(name).stem.removesuffix("-merged")
    match = re.fullmatch(r"(.+?)-v\d+(?:\.\d+){2,3}-.+", stem)
    if not match:
        raise ValueError(f"firmware filename has no release identity: {name}")
    return match[1]


def target_profiles(targets):
    # Use the same exact hardware/role parser as the online firmware picker.
    program = """const fs = require('fs');
const picker = require(process.argv[1]);
const targets = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(Object.fromEntries(
  targets.map(t => [t, picker.parseTargetProfile(t)]))));
"""
    return json.loads(subprocess.check_output(
        ["node", "-e", program, str(ROOT / "docs/_javascript/firmware_picker.js")],
        input=json.dumps(sorted(targets)), text=True))


def file_entry(path, platform, tag, repo):
    if platform == "noflash":
        kind, title = "download", path.suffix[1:].upper() + " download"
    elif path.name.endswith("-merged.bin"):
        kind, title = "flash-wipe", "USB full install (bootloader + partitions + app)"
    elif path.suffix == ".bin" and platform == "esp32":
        kind, title = "flash-update", "App update (matching partition layout only)"
    elif path.suffix == ".zip" and platform == "nrf52":
        kind, title = "flash", "Serial DFU package"
    else:
        kind, title = "download", path.suffix[1:].upper() + " download"
    return {"type": kind, "name": path.name, "title": title,
            "url": f"https://github.com/{repo}/releases/download/{quote(tag, safe='')}/{quote(path.name, safe='')}"}


def read_stage(stage):
    plan = json.loads((stage / "release-plan.json").read_text())
    source = plan["source"]
    if not re.fullmatch(r"[a-f0-9]{40}", source):
        raise ValueError("release source must be a full commit")
    family = f"v{plan['version']}-halo-keymind-cascade-dev-{source[:8]}"
    records = []
    seen = set()
    for group in plan["groups"]:
        expected_tag = family if group["key"] == "companion" else group["key"] + "-" + family
        if group["tag"] != expected_tag:
            raise ValueError("release group belongs to a different family")
        directory = stage / group["key"]
        checksums = {}
        for line in (directory / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
            digest, name = line.split("  ", 1)
            if name in checksums or Path(name).name != name:
                raise ValueError("duplicate or unsafe release asset")
            checksums[name] = digest
        manifest_path = directory / "TARGET-MANIFEST.json"
        if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != checksums.get(manifest_path.name):
            raise ValueError("release target manifest checksum failed")
        manifests = json.loads(manifest_path.read_text())
        if len(manifests) != group["target_count"]:
            raise ValueError("release target count differs from manifest")
        for row in manifests:
            logical_target = row["artifact_target"]
            if row.get("verified") is not True or row.get("source_commit", source) != source:
                raise ValueError("unqualified or wrong-source release profile")
            embedded_bootloader_variant(row)
            files = []
            for name in row["files"]:
                if Path(name).name != name or name not in checksums:
                    raise ValueError("manifest asset is outside the release checksum inventory")
                path = directory / name
                if hashlib.sha256(path.read_bytes()).hexdigest() != checksums[name]:
                    raise ValueError(f"release asset checksum failed: {name}")
                if path.suffix in SUFFIXES:
                    if family not in name:
                        raise ValueError("firmware filename belongs to a different family")
                    files.append(path)
            if not files:
                raise ValueError("qualified profile contains no firmware")
            identities = {identity(path.name) for path in files}
            if len(identities) != 1:
                raise ValueError("firmware filenames disagree on publication profile")
            published_target = identities.pop()
            # Full images keep their OTA identity while the filename adds its
            # qualified build profile. Do not relabel the logical target.
            infix = published_target.removeprefix(logical_target)
            valid_infix = (published_target == logical_target
                or (published_target.startswith(logical_target + "-") and (
                    (infix == "-ota" and "lora" in row.get("ota_update_methods", []))
                    or (row["build_profile"] == "full" and
                        re.fullmatch(r"-full(?:-usb-wifi|-logging)?(?:-ota)?", infix))
                    or (row["build_profile"] == "logging" and
                        re.fullmatch(r"-logging(?:-ota)?", infix)))))
            qualified_stem = published_target + "-" + family
            if any(path.stem.removesuffix("-merged") != qualified_stem for path in files):
                raise ValueError("firmware filename differs from exact release source/version")
            if (not valid_infix or any(qualified_stem + suffix not in row["files"]
                                      for suffix in (".capabilities.json", ".memory.json"))):
                raise ValueError("firmware filename differs from qualified profile")
            if published_target in seen:
                raise ValueError("duplicate release publication profile")
            seen.add(published_target)
            records.append(({**row, "publication_target": published_target}, group["tag"], files))
    return plan, family, records


def generate(catalog, plan, family, records, profiles, repo):
    result = copy.deepcopy(catalog)
    by_identity, by_hardware, existing_roles = {}, {}, {}
    for index, device in enumerate(catalog["device"]):
        for firmware in device["firmware"]:
            for version in firmware["version"].values():
                for file in version["files"]:
                    key = identity(file["name"])
                    by_identity.setdefault(key, set()).add(index)
                    existing_roles.setdefault(key, set()).add((firmware["role"], firmware.get("title", firmware["role"])))
                    hardware = profiles[key]["hardware"]
                    by_hardware.setdefault((device["type"], hardware), set()).add(index)
        result["device"][index]["firmware"] = []
    for row, tag, files in records:
        target = row.get("publication_target", row["artifact_target"])
        parsed = profiles[target]
        platform = PLATFORMS[row["platform"]]
        indexes = by_identity.get(target) or by_identity.get(row["target"])
        if not indexes:
            indexes = set(by_hardware.get((platform, parsed["hardware"]), set()))
            # A download-only card is an installer restriction, not a chip.
            # Retain that restriction for new profiles of the same hardware.
            indexes.update(by_hardware.get(("noflash", parsed["hardware"]), set()))
            card_key = (platform, parsed["hardware"])
            if not indexes and card_key in ADDITIONAL_HARDWARE_CARDS:
                card = ADDITIONAL_HARDWARE_CARDS[card_key]
                if card["maker"] not in catalog["maker"]:
                    raise ValueError("new hardware card has no curated maker")
                indexes = {len(result["device"])}
                result["device"].append({**card, "firmware": []})
                by_hardware[card_key] = indexes
            if len(indexes) != 1:
                raise ValueError(f"new profile needs an exact hardware card mapping: {target}")
        if any(result["device"][i]["type"] not in (platform, "noflash") for i in indexes):
            raise ValueError("firmware platform differs from its hardware card")
        role, title = {"repeater": ("repeater", "Repeater"),
                       "room": ("roomServer", "Room Server"),
                       "sensor": ("sensor", "Sensor"),
                       "terminal": ("terminalChat", "Terminal Chat")}.get(parsed["role"], (None, None))
        if parsed["role"] == "companion":
            role, title = {"full": ("companionFull", "Full Companion"),
                           "ble": ("companionBle", "Companion Bluetooth"),
                           "usb": ("companionUsb", "Companion USB"),
                           "wifi": ("companionWifi", "Companion WiFi"),
                           "ethernet": ("companionEthernet", "Companion Ethernet"),
                           "usb-ble": ("companionBle", "Companion USB + Bluetooth")}.get(parsed["mode"], (None, None))
        if role is None and len(existing_roles.get(target, set())) == 1:
            # Some older exact recipes (PicoW_companion_radio) omit a
            # transport suffix. Preserve their curated role without guessing.
            role, title = next(iter(existing_roles[target]))
        if role is None:
            raise ValueError(f"unsupported firmware role or transport: {target}")
        sensor = row.get("sensor_profile", "")
        profile = ("Full supported sensors + LoRa OTA" if sensor == "full" else
                   "Reduced optional sensors + LoRa OTA" if sensor == "reduced" else
                   "Full profile" if row["build_profile"] == "full" or parsed["mode"] == "full" else
                   "Standard profile")
        bootloader = row.get("embedded_bootloader")
        if bootloader:
            profile += " + OTAFIX " + bootloader["release"] + " bootloader"
        url = f"https://github.com/{repo}/releases/download/{tag}/"
        cap = next(name for name in row["files"] if name.endswith(".capabilities.json"))
        memory = next(name for name in row["files"] if name.endswith(".memory.json"))
        methods = ", ".join(row.get("ota_update_methods", [])) or "USB or board-specific wired programming"
        low_band = "_400m33s_" in row["target"].lower()
        auto_m33 = parsed["hardware"] in ("Generic_E22_m33s", "Meshadventurer_m33s")
        radio_defaults = ("433 MHz / BW62.5 / SF7 / CR5 (E22-400M33S)" if low_band
                          else "910.525 MHz for SX1262 / 433 MHz for SX1268, BW62.5 / SF7 / CR5" if auto_m33
                          else "USA Cascade: 910.525 MHz / BW62.5 / SF7 / CR5")
        notes = [f"Keymind Cascade MeshCore {plan['version']}. {radio_defaults}. Saved radio settings take precedence.",
                 f"EXACT TARGET - {row['target']}. Match the exact board, radio, screen, and storage hardware.",
                 f"PROFILE - {profile}. See the qualified capabilities and memory reports for this image.",
                 f"SELF-UPDATE - Verified methods: {methods}."]
        if platform == "esp32":
            notes.append("INSTALL - The merged .bin installs the bootloader, partitions and application over USB. An app-only .bin requires a compatible existing partition layout. Use the exact board and role migration ZIP from the utility release when expanding partitions over Wi-Fi or LoRa; follow its README and install its supplied Full application.")
        elif platform == "nrf52":
            if bootloader:
                notes.append("INSTALL - This application carries the exact-board OTAFIX " + bootloader["release"] + " bootloader and checks it for installation 70 seconds after startup. Match the board and storage profile; recovery and unrecognized bootloaders require the documented migration route.")
            else:
                notes.append("INSTALL - Ordinary UF2 and Serial DFU ZIP files contain application firmware and preserve the installed bootloader. LoRa updates require the matching board and storage OTAFIX bootloader. Use the firmware picker's exact bootloader mapping.")
        if row.get("reductions"):
            notes.append("CAPACITY - " + "; ".join(row["reductions"]))
        if "-full-usb-wifi" in target:
            notes.append("OUTPUT - Select saved output with set logging.output off|usb|wifi|both. USB-connected MQTT uses the serial packet log. Direct Wi-Fi MQTT uses the saved broker configuration.")
        elif parsed["mode"] == "full" and parsed["role"] == "companion":
            notes.append("TRANSPORT - Full Companion starts its primary USB interface in the ASCII terminal; a complete framed app command selects Binary Companion. USB logging is off by default. Use the picker directions for this image's supported transports and runtime logging controls.")
        notes += [f"SETUP - https://mikecarper.github.io/MeshCore/firmware_picker/?hardware={quote(parsed['hardware'])}",
                  "CAPABILITIES - " + url + quote(cap), "MEMORY - " + url + quote(memory),
                  f"SOURCE - https://github.com/{repo}/tree/{row.get('source_commit', plan['source'])}",
                  f"RELEASE - https://github.com/{repo}/releases/tag/{tag}"]
        for index in sorted(indexes):
            install_platform = result["device"][index]["type"]
            card_notes = list(notes)
            if install_platform == "noflash":
                card_notes.append("MESH AMERICA - Downloads only. In-app flashing is not qualified for this hardware card.")
            entry = {"role": role, "title": title, "subTitle": target + " - " + profile,
                     "version": {family: {"notes": "\n\n".join(card_notes),
                         "files": [file_entry(path, install_platform, tag, repo) for path in sorted(files,
                              key=lambda p: (not p.name.endswith('-merged.bin'), p.suffix != '.bin', p.name))]}}}
            result["device"][index]["firmware"].append(entry)
    result["device"] = [device for device in result["device"] if device["firmware"]]
    for device in result["device"]:
        device["firmware"].sort(key=lambda entry: (entry["role"], entry["subTitle"]))
    result["description"] = (f"Keymind Cascade MeshCore {plan['version']} USA Cascade. "
        f"910.525 MHz / BW62.5 / SF7 / CR5. {len(records)} qualified firmware profiles "
        f"across {len(plan['groups'])} GitHub release pages. See each exact image's capability and memory reports.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", default="mikecarper/MeshCore")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text())
    plan, family, records = read_stage(args.stage)
    targets = {row.get("publication_target", row["artifact_target"]) for row, _, _ in records}
    targets.update(identity(file["name"]) for device in catalog["device"]
                   for firmware in device["firmware"] for version in firmware["version"].values()
                   for file in version["files"])
    result = generate(catalog, plan, family, records, target_profiles(targets), args.repo)
    args.output.write_text(json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="ascii")
    print(json.dumps({"profiles": len(records), "hardware_cards": len(result["device"]),
                      "firmware_entries": sum(len(d["firmware"]) for d in result["device"])}))


if __name__ == "__main__":
    main()
