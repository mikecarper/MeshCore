#!/usr/bin/env python3
"""Stage a completed Option 3 matrix as audited, directly downloadable releases."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
from urllib.parse import quote, urljoin


ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_SUFFIXES = {".bin", ".uf2", ".hex", ".zip"}


def is_companion(target):
    return "companion" in target.lower() or "comp_radio" in target.lower()


def validate_manifest(manifest):
    target = manifest["target"]
    if manifest.get("schema_version", 0) < 2 or not manifest.get("verified"):
        raise ValueError(f"{target}: firmware capability qualification failed")
    if not is_companion(target) and not manifest.get("ota_update_verified"):
        raise ValueError(f"{target}: infrastructure has no verified wireless updater")
    if "companion_radio_full" in target.lower():
        required = {"companion.usb_mota_source", "companion.mota_sender",
                    "companion.temp_radio", "companion.ota_cli"}
        required.add("companion.wifi_ota_seeder" if manifest["platform"] == "ESP32_PLATFORM"
                     else "companion.ble_mota_source")
        verified = {item["capability"] for item in manifest["verification"] if item["present"]}
        if not required <= verified:
            raise ValueError(f"{target}: Full Companion lacks verified MOTA sending")


def collect_artifacts(directory, version):
    records = []
    accounted = set()
    for path in sorted(directory.glob("*.capabilities.json")):
        stem = path.name.removesuffix(".capabilities.json")
        files = [candidate for candidate in directory.glob(stem + ".*")
                 if candidate.suffix in FIRMWARE_SUFFIXES]
        merged = directory / (stem + "-merged.bin")
        if merged.is_file():
            files.append(merged)
        if not files:
            continue  # A measured rejected attempt may leave only a manifest.
        if not stem.endswith("-" + version):
            raise ValueError(f"stale or mixed-version artifact: {stem}")
        manifest = json.loads(path.read_text())
        validate_manifest(manifest)
        extensions = {item.suffix for item in files}
        platform = manifest["platform"]
        if platform == "ESP32_PLATFORM" and (not merged.is_file() or not (directory / (stem + ".bin")).is_file()):
            raise ValueError(f"{stem}: ESP32 application/merged pair incomplete")
        if platform == "NRF52_PLATFORM":
            if ".uf2" not in extensions or (manifest.get("ota_update_verified") and ".zip" not in extensions):
                raise ValueError(f"{stem}: nRF52 UF2/DFU artifacts incomplete")
        if any(item.stat().st_size == 0 for item in files):
            raise ValueError(f"{stem}: empty firmware artifact")
        accounted.update(files)
        records.append({"manifest": manifest, "files": sorted(files) + [path]})
    unaccounted = {path for path in directory.iterdir() if path.suffix in FIRMWARE_SUFFIXES} - accounted
    if unaccounted:
        raise ValueError("firmware without qualification: " + ", ".join(sorted(p.name for p in unaccounted)))
    if not records:
        raise ValueError("no qualified firmware artifacts")
    return records


def category(record):
    manifest = record["manifest"]
    if is_companion(manifest["target"]):
        return "companion"
    if manifest["build_profile"] == "full":
        return "full-profiles"
    if "lora_ota" in manifest["target"].lower():
        return "lora-ota"
    if "logging" in manifest["artifact_target"].lower():
        return "logging"
    if any(role in manifest["target"].lower() for role in ("sensor", "terminal")):
        return "utility"
    return "repeater-room"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--build-status", required=True, type=Path)
    parser.add_argument("--version", default="1.17.1.5")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repo", default="mikecarper/MeshCore")
    args = parser.parse_args()
    status = dict(line.split("=", 1) for line in args.build_status.read_text().splitlines() if "=" in line)
    if status.get("state") != "completed" or status.get("exit_code") != "0":
        raise ValueError("the Option 3 matrix has not completed successfully")
    output_directory = Path(status["working_directory"]) / status["output_directory"]
    if output_directory.resolve() != args.input.resolve():
        raise ValueError("build status belongs to another output directory")
    firmware_label = f"v{args.version}-halo-keymind-cascade-dev"
    if status.get("source_commit") != args.commit or status.get("firmware_version") != firmware_label:
        raise ValueError("build status belongs to another source revision or version")
    if status.get("firmware_profile") != "cascade":
        raise ValueError("matrix did not use Cascade runtime defaults")
    radio = {key: status["radio_" + key] for key in ("frequency", "bandwidth", "sf", "cr")}
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("staging directory must be empty; existing releases are never overwritten")
    version = f"{firmware_label}-{args.commit[:8]}"
    records = collect_artifacts(args.input, version)
    base_tag = version
    groups = []
    titles = {"companion": "Companion builds", "repeater-room": "Repeater and Room Server builds",
              "utility": "Sensor and Terminal utilities", "logging": "USB packet logging builds",
              "lora-ota": "LoRa OTA builds", "full-profiles": "Expanded FULL ESP32 profiles"}
    for name in titles:
        current = []
        count = 0
        chunks = []
        for record in (r for r in records if category(r) == name):
            if count + len(record["files"]) > 900:
                chunks.append(current)
                current, count = [], 0
            current.append(record)
            count += len(record["files"])
        if current:
            chunks.append(current)
        for index, chunk in enumerate(chunks):
            key = name + (f"-{index + 1}" if index else "")
            tag = base_tag if key == "companion" else f"{key}-{base_tag}"
            title = f"MeshCore {args.version} Dev - {titles[name]}"
            if index:
                title += f" (part {index + 1})"
            groups.append({"key": key, "tag": tag, "title": title, "prerelease": True, "records": chunk})
    links = "\n".join(f"- [{g['key']}](https://github.com/{args.repo}/releases/tag/{g['tag']})" for g in groups)
    source_url = f"https://github.com/{args.repo}/blob/{args.commit}"
    rows = []
    for group in groups:
        destination = args.output / group["key"]
        destination.mkdir(parents=True)
        summaries = []
        for record in group["records"]:
            manifest = record["manifest"]
            file_links = []
            for path in record["files"]:
                shutil.copy2(path, destination / path.name)
                if path.suffix in FIRMWARE_SUFFIXES:
                    url = f"https://github.com/{args.repo}/releases/download/{group['tag']}/{quote(path.name)}"
                    label = "merged.bin (USB)" if path.name.endswith("-merged.bin") else path.suffix[1:]
                    file_links.append(f'<a href="{html.escape(url)}">{html.escape(label)}</a>')
            methods = ", ".join(manifest.get("ota_update_methods", [])) or "USB"
            summaries.append({**manifest, "files": [path.name for path in record["files"]]})
            rows.append(f"<tr><td>{html.escape(manifest['artifact_target'])}</td><td>{html.escape(manifest['build_profile'])}</td><td>{html.escape(methods)}</td><td>{' · '.join(file_links)}</td></tr>")
        (destination / "TARGET-MANIFEST.json").write_text(json.dumps(summaries, indent=2) + "\n")
        columns = ("artifact_target", "target", "platform", "build_profile",
                   "ota_update_methods", "files")
        (destination / "TARGET-MANIFEST.tsv").write_text(
            "\t".join(columns) + "\n" + "".join(
                "\t".join(",".join(row.get(key, [])) if isinstance(row.get(key), list)
                          else str(row.get(key, "")) for key in columns) + "\n"
                for row in summaries))
        guide = (ROOT / "docs/full_companion_features.md").read_text()
        guide = re.sub(r"\]\(([^)]+)\)",
                       lambda match: "](" + urljoin(source_url + "/docs/", match[1]) + ")", guide)
        (destination / "FULL-COMPANION-FEATURES.md").write_text(guide)
        exclusions = args.input / "ota-excluded-targets.txt"
        if exclusions.is_file():
            shutil.copy2(exclusions, destination / exclusions.name)
        body = (f"> **Development prerelease.** Firmware identifier: **{base_tag}**.\n\n"
                f"# MeshCore {args.version} Dev — USA Cascade\n\n"
                f"Source: `{args.commit}`. USA/Canada: **{radio['frequency']} MHz, BW{radio['bandwidth']}, SF{radio['sf']}, CR{radio['cr']}**; Cascade defaults.\n\n"
                f"This page contains **{len(summaries)} qualified firmware profiles**. Full Companions send MOTA and normally update over USB. Infrastructure has a verified wireless update path; see each capability manifest.\n\n"
                f"[Feature on/off and update directions]({source_url}/docs/full_companion_features.md) · "
                f"[Release details]({source_url}/docs/releases/{args.version}.md)\n\n"
                "Match the exact board, radio, display, and storage variant. ESP32 WiFi updates use the application `.bin`; the merged image installs boot/partition data over USB. nRF52 `.zip` files are native application DFU packages. LoRa MOTA installation requires an exact destination package; nRF52 also requires its matching OTAFIX bootloader. Full Companions do not LoRa-install onto themselves.\n\n"
                "Firmware and capability checks passed in the build matrix. Hardware update testing across every board was not performed.\n\n"
                + links + "\n")
        (args.output / (group["key"] + "-notes.md")).write_text(body)
        (destination / "BUILD-NOTES.txt").write_text(body)
    picker = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
              f"<title>MeshCore {html.escape(args.version)} USA Cascade</title>"
              "<style>body{font:16px system-ui;margin:2rem}input{font:inherit;padding:.5rem;width:90%;max-width:40rem}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:.7rem;border-bottom:1px solid #ccc}td:first-child{overflow-wrap:anywhere}a{color:#165acb}</style>"
              f"<h1>MeshCore {html.escape(args.version)} USA Cascade</h1>"
              "<p>Match your exact hardware. Full Companions can send MOTA; USB is their normal update method.</p>"
              f"<p><a href='{source_url}/docs/full_companion_features.md'>Feature switches and update instructions</a></p>"
              "<input id=search placeholder='Filter by board, profile, or update method' aria-label='Filter firmware'>"
              "<table><thead><tr><th>Target</th><th>Profile</th><th>Self-update</th><th>Downloads</th></tr></thead><tbody>"
              + "\n".join(rows) + "</tbody></table>"
              "<script>document.getElementById('search').oninput=function(){const q=this.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))}</script>")
    local_picker = picker
    for group in groups:
        local_picker = local_picker.replace(
            f"https://github.com/{args.repo}/releases/download/{group['tag']}/",
            group["key"] + "/")
    local_picker = local_picker.replace(
        f"{source_url}/docs/full_companion_features.md",
        groups[0]["key"] + "/FULL-COMPANION-FEATURES.md")
    (args.output / f"FIRMWARE-PICKER-{args.version}.html").write_text(local_picker)
    for group in groups:
        destination = args.output / group["key"]
        (destination / f"FIRMWARE-PICKER-{args.version}.html").write_text(picker)
        files = sorted(destination.iterdir())
        (destination / "SHA256SUMS.txt").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in files))
        group["asset_count"] = len(files) + 1
        group["target_count"] = len(group.pop("records"))
    (args.output / "release-plan.json").write_text(json.dumps({"source": args.commit, "version": args.version, "radio": radio, "groups": groups}, indent=2) + "\n")
    print(json.dumps({"targets": len(records), "groups": groups}, indent=2))


if __name__ == "__main__":
    main()
