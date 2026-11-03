"""Embed an exact published OTAFIX image in an opt-in infrastructure build."""
from __future__ import annotations

import binascii
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
from urllib.request import urlopen
import zipfile

from nrf52_legacy_bootloaders import legacy_bootloaders_for

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.4.12"
BOOT_VERSION = 0x02040CFF
MANIFEST_SHA256 = "d2f62a4ec9b7c5643d8ed787d9e1710a2fabac34607a0bbb6ea5883a458e6df3"
LEGACY_SHA256 = "64bfe69d56eefc2d0fe51770902aff70ea0cc06cad261d3d219e4a3c0efac8dc"
BOOT_BASE, BOOT_SIZE = 0xF4000, 0xA000
REPOSITORY = "mikecarper/Adafruit_nRF52_Bootloader_OTAFIX"
TAG = "v0.11.0-OTAFIX" + VERSION
MAX_ZIP_SIZE = 2 * 1024 * 1024


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _json(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)


def load_manifest(project=ROOT):
    data = (Path(project) / "docs/_data/bootloader_manifest.json").read_bytes()
    if hashlib.sha256(data).hexdigest() != MANIFEST_SHA256:
        raise ValueError("embedded bootloader requires the pinned OTAFIX 2.4.12 download manifest")
    manifest = _json(data)
    if (manifest.get("schemaVersion") != 1 or manifest.get("repository") != REPOSITORY
            or manifest.get("tag") != TAG or manifest.get("version") != VERSION
            or manifest.get("prerelease") is not False):
        raise ValueError("unexpected embedded bootloader release identity")
    return manifest


# A PA/module choice does not change the MCU bootloader or its flash wiring.
# This map must never be used for application hardware_id or MOTA target IDs.
BOOTLOADER_HARDWARE = {
    "ProMicro_m33s": "ProMicro",
    "ProMicro_400m33s": "ProMicro",
    "Xiao_nrf52_m33s": "Xiao_nrf52",
    "Xiao_nrf52_400m33s": "Xiao_nrf52",
    "ikoka_handheld_nrf_e22_33dbm": "Xiao_nrf52",
    "ikoka_handheld_nrf_e22_33dbm_096": "Xiao_nrf52",
    "ikoka_handheld_nrf_e22_400m33s": "Xiao_nrf52",
    "ikoka_handheld_nrf_e22_400m33s_096": "Xiao_nrf52",
    "ikoka_nano_nrf_400m33s": "Xiao_nrf52",
    "ikoka_stick_nrf_400m33s": "Xiao_nrf52",
    "solarxiao_400M33S": "Xiao_nrf52",
}


def select_profile(manifest, target):
    from generate_mesh_america_release import target_profiles
    parsed = target_profiles([target])[target]
    if parsed["role"] not in ("repeater", "room"):
        raise ValueError("embedded bootloader is limited to repeater and room-server roles")
    names = {BOOTLOADER_HARDWARE.get(name, name)
             for name in (parsed["sourceHardware"], parsed["hardware"])}
    if any(parsed["sourceHardware"] in item["meshcoreHardware"]
           for item in manifest.get("unavailableProfiles", ())):
        raise ValueError("this exact hardware has no qualified OTAFIX download")
    matches = [item for item in manifest["profiles"]
               if names.intersection(item["meshcoreHardware"])]
    if len(matches) != 1:
        raise ValueError("hardware must map to one exact published bootloader profile")
    return matches[0], parsed


def _asset(profile):
    asset = profile["files"]["zip"]
    name, size, digest = asset["name"], asset["size"], asset["sha256"]
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.zip", name)
            or type(size) is not int or not 0 < size <= MAX_ZIP_SIZE
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or asset["url"] != f"https://github.com/{REPOSITORY}/releases/download/{TAG}/{name}"):
        raise ValueError("invalid pinned bootloader ZIP metadata")
    return asset


def download_zip(profile, cache_dir):
    asset = _asset(profile)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (asset["sha256"] + "-" + asset["name"])

    def valid(data):
        return len(data) == asset["size"] and hashlib.sha256(data).hexdigest() == asset["sha256"]

    if path.is_file() and path.stat().st_size == asset["size"]:
        data = path.read_bytes()
        if valid(data):
            return data
    with urlopen(asset["url"], timeout=60) as response:
        data = response.read(asset["size"] + 1)
    if not valid(data):
        raise ValueError("downloaded bootloader ZIP size/SHA-256 mismatch")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, prefix="otafix-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return data


def _unsigned_init(packet, metadata, firmware):
    if len(packet) < 12 or len(packet) > 4096:
        raise ValueError("invalid Legacy DFU init-packet length")
    device, revision, app_version, count = struct.unpack_from("<HHIH", packet)
    if not count or count > 64 or len(packet) != 12 + count * 2:
        raise ValueError("invalid Legacy DFU SoftDevice requirements")
    requirements = list(struct.unpack_from(f"<{count}H", packet, 10))
    crc = struct.unpack_from("<H", packet, 10 + count * 2)[0]
    expected = {"device_type": device, "device_revision": revision,
                "application_version": app_version, "softdevice_req": requirements,
                "firmware_crc16": crc}
    if (metadata != expected or device != 0x52 or revision != 52840
            or crc != binascii.crc_hqx(firmware, 0xFFFF)):
        raise ValueError("Legacy DFU init metadata/firmware CRC mismatch")


def validate_candidate_zip(data, profile, project=ROOT):
    asset = _asset(profile)
    if len(data) != asset["size"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
        raise ValueError("bootloader ZIP does not match its pinned release asset")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) != 3 or len(set(names)) != 3 or "manifest.json" not in names:
            raise ValueError("bootloader ZIP must contain exactly one manifest/BIN/DAT")
        for entry in entries:
            if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", entry.filename)
                    or entry.is_dir() or entry.flag_bits & 1
                    or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                    or not 0 < entry.file_size <= (512 * 1024 if entry.filename.endswith(".bin") else 65536)):
                raise ValueError("unsupported bootloader ZIP member")
        document = _json(archive.read("manifest.json"))
        if set(document) != {"manifest"}:
            raise ValueError("invalid bootloader ZIP manifest root")
        manifest = document["manifest"]
        if set(manifest) != {"dfu_version", "softdevice_bootloader"} or manifest["dfu_version"] != 0.5:
            raise ValueError("expected an unsigned Legacy SoftDevice/bootloader package")
        entry = manifest["softdevice_bootloader"]
        if set(entry) != {"bin_file", "dat_file", "sd_size", "bl_size", "init_packet_data"}:
            raise ValueError("invalid combined bootloader manifest entry")
        if set(names) != {"manifest.json", entry["bin_file"], entry["dat_file"]}:
            raise ValueError("bootloader ZIP references do not match its members")
        if not entry["bin_file"].endswith(".bin") or not entry["dat_file"].endswith(".dat"):
            raise ValueError("bootloader ZIP has incorrect member extensions")
        firmware = archive.read(entry["bin_file"])
        packet = archive.read(entry["dat_file"])
    sd_size, bl_size = entry["sd_size"], entry["bl_size"]
    if (type(sd_size) is not int or type(bl_size) is not int or sd_size <= 0
            or sd_size % 4 or bl_size != BOOT_SIZE or sd_size + bl_size != len(firmware)):
        raise ValueError("invalid SoftDevice/bootloader payload geometry")
    _unsigned_init(packet, entry["init_packet_data"], firmware)
    image = firmware[sd_size:]
    sys.path.insert(0, str(Path(project) / "tools/mota"))
    import motalib as ml
    identity = ml.parse_bootloader_identity(image)
    sp, reset = struct.unpack_from("<II", image)
    if (identity is None or identity.manifest_offset != BOOT_SIZE - 76
            or not (0x20000000 <= sp <= 0x20040000 and sp % 8 == 0 and reset & 1
                    and BOOT_BASE <= reset & ~1 < BOOT_BASE + BOOT_SIZE)
            or identity.boot_version != BOOT_VERSION or identity.softdevice_family != 140
            or identity.layout_abi != 1 or identity.compat_flags != 0
            or identity.device_name != profile["deviceName"]):
        raise ValueError("candidate bootloader identity/version/vector/CRC mismatch")
    platforms = {"6.1.1": (0xB6, 0x26000), "7.3.0": (0x123, 0x27000)}
    match = re.search(r"_s140_([0-9.]+)\.zip$", asset["name"])
    expected = platforms.get(match[1]) if match else None
    if expected is None or (identity.softdevice_fwid, identity.app_base) != expected:
        raise ValueError("candidate SoftDevice/application layout differs from its release asset")
    actual_storage = ml.bootloader_caps_storage(image)
    expected_storage = {"internal": 0x0A, "qspi": 0x0E, "sd": 0x09, "adaptive": 0x0A}
    if actual_storage != expected_storage.get(profile["storage"]):
        raise ValueError("candidate OTA capability marker differs from its exact profile")
    if profile["storage"] == "adaptive" and ml.bootloader_optional_app_storage(image) != 0x14:
        raise ValueError("adaptive candidate lacks its exact optional storage contract")
    # The pinned asset is authoritative for newly published hardware. Keep the
    # existing signed-MOTA inventory strict; do not expand it for embedding.
    if ml.bootloader_qualified_storage_profiles(identity.board_id, identity.device_name) is not None:
        ml.validate_bootloader_image(image)
    return image, identity


def application_base(project, linker):
    if not linker:
        return None
    path = Path(linker)
    path = path if path.is_absolute() else Path(project) / path
    if path.is_file():
        match = re.search(r"FLASH\s*\([^)]*\)\s*:\s*ORIGIN\s*=\s*(0x[0-9A-Fa-f]+)", path.read_text())
        if match:
            return int(match[1], 16)
    match = re.fullmatch(r"nrf52840_s140_v([67])(?:_[A-Za-z0-9_]+)?\.ld", Path(str(linker).replace("\\", "/")).name)
    if match:
        return 0x26000 if match[1] == "6" else 0x27000
    raise ValueError("cannot verify application base from the board linker")


def load_legacy(path):
    path = Path(path)
    if path.suffix.lower() == ".hex":
        from intelhex import IntelHex
        image = bytes(IntelHex(str(path)).tobinarray(start=BOOT_BASE, size=BOOT_SIZE))
    else:
        image = path.read_bytes()
    if len(image) != BOOT_SIZE or hashlib.sha256(image).hexdigest() != LEGACY_SHA256:
        raise ValueError("legacy override must match the qualified MeshEnvy RAK4631 full bootloader hash")
    return image


def emit_resource(project, target, output, *, flags=(), cppdefines=(), src_filter=(),
                  linker=None, legacy_path=None, cache_dir=None):
    from nrf52_flash_trim import flag_defines, source_selected
    definitions = flag_defines(flags, cppdefines)
    def enabled(key):
        value = definitions.get(key)
        return value is not None and str(value).strip("\"'() ") not in ("", "0")
    if (not enabled("NRF52_PLATFORM") or not enabled("ENABLE_OTA")
            or definitions.get("DISABLE_LORA_OTA") is not None
            or definitions.get("OTA_SEEDER_ONLY") is not None):
        raise ValueError("embedded bootloader requires an nRF52 OTA receiver")
    manifest = load_manifest(project)
    profile, parsed = select_profile(manifest, target)
    role_sources = {"repeater": "../examples/simple_repeater/main.cpp",
                    "room": "../examples/simple_room_server/main.cpp"}
    if (not source_selected(src_filter, role_sources[parsed["role"]])
            or source_selected(src_filter, role_sources[
                "room" if parsed["role"] == "repeater" else "repeater"])
            or any(source_selected(src_filter, source) for source in (
                "../examples/companion_radio/MyMesh.cpp", "../examples/simple_secure_chat/main.cpp",
                "../examples/simple_sensor/main.cpp", "../examples/kiss_modem/main.cpp"))):
        raise ValueError("embedded bootloader requires its exact repeater or room-server source tree")
    if legacy_path and (parsed["sourceHardware"] != "RAK_4631"
                        or profile["id"] != "wiscore_rak4631_auto"):
        raise ValueError("the qualified legacy bridge is restricted to RAK4631")
    image, identity = validate_candidate_zip(download_zip(
        profile, cache_dir or Path(project) / ".pio/otafix-cache"), profile, project)
    linked_base = application_base(project, linker)
    if linked_base is not None and linked_base != identity.app_base:
        raise ValueError("candidate SoftDevice base does not match the application linker")
    legacy = load_legacy(legacy_path) if legacy_path else None
    stock_hardware = BOOTLOADER_HARDWARE.get(parsed["sourceHardware"], parsed["sourceHardware"])
    stock_legacy = legacy_bootloaders_for(profile["id"], stock_hardware,
                                         identity.app_base, identity.softdevice_fwid)
    if len(stock_legacy) > 32:
        raise ValueError("too many reviewed stock bootloader identities")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    arrays = {"migration_candidate": image,
              "migration_candidate_sha256": hashlib.sha256(image).digest(),
              "migration_legacy_sha256": hashlib.sha256(legacy).digest() if legacy else bytes(32)}
    header = "#pragma once\n#include <stdint.h>\n"
    source = '#include "BootloaderMigrationResource.h"\n'
    for name, data in arrays.items():
        header += f"extern const uint8_t {name}[{len(data)}];\n"
        source += f"alignas(4) const uint8_t {name}[{len(data)}] = {{\n"
        source += "".join("  " + ", ".join(f"0x{value:02X}" for value in data[index:index + 16]) + ",\n"
                          for index in range(0, len(data), 16)) + "};\n"
    # A zero-count resource retains one inert row so C++ never needs a
    # non-standard zero-length array. The count, not the sentinel, authorizes it.
    stock_hashes = [bytes.fromhex(item["image_sha256"]) for item in stock_legacy]
    header += f"extern const uint8_t migration_stock_sha256[{max(1, len(stock_hashes))}][32];\n"
    source += f"alignas(4) const uint8_t migration_stock_sha256[{max(1, len(stock_hashes))}][32] = {{\n"
    source += "".join("  {" + ", ".join(f"0x{value:02X}" for value in digest) + "},\n"
                      for digest in (stock_hashes or [bytes(32)])) + "};\n"
    values = {"migration_has_legacy": ("bool", "true" if legacy else "false"),
              "migration_stock_count": ("uint32_t", str(len(stock_hashes))),
              "migration_app_base": ("uint32_t", hex(identity.app_base)),
              "migration_boot_version": ("uint32_t", hex(identity.boot_version)),
              "migration_board_id": ("uint32_t", hex(identity.board_id)),
              "migration_softdevice_fwid": ("uint16_t", hex(identity.softdevice_fwid))}
    for name, (kind, value) in values.items():
        header += f"extern const {kind} {name};\n"
        source += f"const {kind} {name} = {value};\n"
    header += "extern const char migration_device_name[16];\n"
    source += "const char migration_device_name[16] = " + json.dumps(identity.device_name) + ";\n"
    (output / "BootloaderMigrationResource.h").write_text(header, encoding="ascii")
    (output / "BootloaderMigrationResource.cpp").write_text(source, encoding="ascii")
    (output / "BootloaderMigrationResource.bin").write_bytes(image)
    metadata = {"target": target, "hardware": parsed["sourceHardware"], "role": parsed["role"],
                "profile": profile["id"], "release": VERSION, "version": VERSION,
                "manifest_sha256": MANIFEST_SHA256,
                "asset_sha256": profile["files"]["zip"]["sha256"],
                "source_zip_sha256": profile["files"]["zip"]["sha256"],
                "candidate_sha256": hashlib.sha256(image).hexdigest(),
                "legacy_sha256": hashlib.sha256(legacy).hexdigest() if legacy else None,
                "stock_legacy": stock_legacy,
                "image_size": BOOT_SIZE, "app_base": identity.app_base,
                "boot_version": identity.boot_version, "board_id": identity.board_id,
                "softdevice_fwid": identity.softdevice_fwid, "device_name": identity.device_name}
    (output / "BootloaderMigrationResource.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="ascii")
    return metadata
