"""Package preparation for the interactive updater; no radio I/O."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import zipfile

import lora_ota as ota
from ota_wizard_catalog import GitHubCatalog, ImageCache, ReleaseAsset, _open, download_asset, load_application
from ota_wizard_session import Destination, Package, full_package, validate_package

OFFICIAL_BOOT_KEY = "272564CC588D3D122285A15E6E2566D2ABE7177BB7EA1D1E41B23B29F0F85D2D"
BOOT_REPOSITORY = "mikecarper/Adafruit_nRF52_Bootloader_OTAFIX"
MOTATOOL_REV = "5d9646cb08105245fb96c28450669c58763224bb"


def small_host() -> bool:
    # ARM also includes Apple Silicon and Windows workstations. Only Linux
    # exposes the board/resource evidence used for this low-power default.
    if platform.system() != "Linux":
        return False
    try:
        if "raspberry pi" in Path("/proc/device-tree/model").read_text().lower():
            return True
    except OSError:
        pass
    if not platform.machine().lower().startswith(("arm", "aarch")):
        return False
    try:
        memory = re.search(r"^MemTotal:\s+(\d+)\s+kB\s*$",
                           Path("/proc/meminfo").read_text(), re.M)
        cores = os.cpu_count()
        return bool(memory and int(memory[1]) < 4 * 1024 * 1024
                    and cores is not None and cores <= 4)
    except OSError:
        return False


def find_builder(cache: Path, explicit: str | None = None) -> str | None:
    if explicit:
        found = shutil.which(explicit) or (explicit if Path(explicit).is_file() else None)
        if not found:
            raise ota.OtaError("The selected differential builder does not exist.")
        return str(found)
    found = shutil.which("motatool")
    cached = cache / "motatool" / MOTATOOL_REV / "bin" / ("motatool.exe" if os.name == "nt" else "motatool")
    return found or (str(cached) if cached.is_file() else None)


def install_builder(cache: Path, progress=print) -> str | None:
    """A workstation can prepare the pinned tool in a private cache once."""
    if small_host() or not shutil.which("cargo") or not shutil.which("rustc"):
        return None
    try:
        for tool in ("cargo", "rustc"):
            reply = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=10)
            version = re.search(r"\b(\d+)\.(\d+)\.(\d+)", reply.stdout)
            if reply.returncode or not version or tuple(map(int, version.groups())) < (1, 86, 0):
                return None
    except (OSError, subprocess.TimeoutExpired):
        return None
    prefix = cache / "motatool" / MOTATOOL_REV
    prefix.mkdir(parents=True, exist_ok=True)
    progress("Preparing the differential builder once. This downloads dependencies and can take several minutes.")
    log = prefix / "build.log"
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("MESHCORE_ADMIN_PASSWORD", "MESHCORE_OTA_PASSWORD")}
    try:
        with log.open("w", encoding="utf-8") as output:
            result = subprocess.run(["cargo", "install", "--git", "https://github.com/mikecarper/motatool.git",
                                     "--rev", MOTATOOL_REV, "--locked", "--bin", "motatool",
                                     "--root", str(prefix)], env=environment,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=3600)
    except (OSError, subprocess.TimeoutExpired):
        progress(f"The differential builder was unavailable; setup details are in {log}.")
        return None
    if result.returncode:
        progress(f"The differential builder was unavailable; setup details are in {log}.")
        return None
    return find_builder(cache)


def find_running_image(destination: Destination, cache: Path, catalog: GitHubCatalog | None,
                       *, supplied: Path | None = None, progress=print,
                       candidate_limit: int = 4) -> Path | None:
    images = ImageCache(cache)
    if supplied:
        identity = load_application(supplied, target_id=destination.target_id, hw_id=destination.hw_id)
        if identity.body_hash != destination.base_hash or identity.fw_version != destination.version:
            raise ota.OtaError("The supplied base is not the exact running firmware.")
        return images.remember(identity)
    found = images.find(destination.base_hash, destination.target_id, destination.hw_id,
                        fw_version=destination.version)
    if found:
        progress("Found the exact running image in the local cache.")
        return found
    if catalog:
        for asset in catalog.base_candidates(target_id=destination.target_id,
                                             fw_version=destination.version, limit=candidate_limit):
            try:
                candidate = download_asset(asset, cache / "downloads")
                identity = load_application(candidate, target_id=destination.target_id,
                                            hw_id=destination.hw_id)
            except (ota.OtaError, OSError):
                continue
            if identity.body_hash == destination.base_hash and identity.fw_version == destination.version:
                progress("Found and hash-checked the published running image.")
                return images.remember(identity)
    return None


def prepare_application(image: ota.EndFInfo, destination: Destination, cache: Path,
                        *, base: Path | None = None, builder: str | None = None,
                        full: bool = False, progress=print) -> Package:
    block = min(2048, destination.max_block_size)
    result = full_package(image, block)
    if base and builder and not full:
        identity = load_application(base, target_id=destination.target_id, hw_id=destination.hw_id)
        if identity.body_hash != destination.base_hash or identity.fw_version != destination.version:
            raise ota.OtaError("The differential base is not the exact running firmware.")
        inputs = cache / "packages" / f"{destination.target_id:08X}"
        inputs.mkdir(parents=True, exist_ok=True)
        new_path = ImageCache(cache).remember(image)
        output = inputs / f"{destination.base_hash.hex()}-{image.body_hash.hex()}-delta.mota"
        command = [builder, "build", "--base", str(base), "--fw", str(new_path),
                   "--patch-type", "in-place" if destination.platform == "nrf52" else "sequential",
                   "--block-size", str(block), "--out", str(output)]
        progress("Preparing a differential update from the exact running image…")
        # Never accept a previous invocation's output after a failed build.
        output.unlink(missing_ok=True)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=3600)
            if not completed.returncode and output.is_file():
                blob = ota.read_bounded_file(output, 64 * 1024 * 1024, "differential package")
                differential = Package(blob, image.body_hash)
                validate_package(differential, destination)
                info = differential.info
                if (info.is_full or info.is_bootloader or info.fw_version != image.fw_version
                        or info.image_size != len(image.image)
                        or info.image_hash != hashlib.sha256(image.image).digest()):
                    raise ota.OtaError("The differential builder returned a different new image.")
                if len(blob) < len(result.blob) or not destination.external_storage and destination.platform == "nrf52":
                    result = differential
        except (ota.OtaError, OSError, ValueError, subprocess.TimeoutExpired):
            # Full images are the portable fallback. validate_package below
            # refuses it on internal-flash nRF52, where a delta is mandatory.
            pass
        if result.info.is_full:
            progress("A full update is smaller or the differential builder could not prepare a compatible delta.")
    validate_package(result, destination)
    return result


def prepare_bootloader(destination: Destination, cache: Path) -> Package | None:
    """Select by installed boot identity, then verify against the pinned publisher key."""
    boot = destination.bootloader
    if not boot or boot.apply_abi < 3 or not boot.storage_flags & 0x08:
        return None
    with _open(f"https://api.github.com/repos/{BOOT_REPOSITORY}/releases/latest", api=True) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ota.OtaError("The bootloader release catalog is too large.")
    release = json.loads(raw)
    tag = release.get("tag_name", "")
    if not re.fullmatch(r"v\d+\.\d+\.\d+-OTAFIX\d+\.\d+\.\d+", tag):
        raise ota.OtaError("The current bootloader release has an unsupported identity.")
    assets = [row for row in release.get("assets", [])
              if re.fullmatch(r"OTAFIX-\d+\.\d+\.\d+-bootloader-mota\.zip", row.get("name", ""))]
    if len(assets) != 1:
        raise ota.OtaError("The official bootloader release does not have one normal signed bundle.")
    row = assets[0]
    archive_path = download_asset(ReleaseAsset(row["name"], row["browser_download_url"], row["size"],
                                             tag, 0, "bootloader", "standard", "zip", row.get("digest")),
                                  cache / "downloads")
    with zipfile.ZipFile(archive_path) as archive:
        inventory = json.loads(ota.read_zip_member(archive, archive.getinfo("manifest.json")))
        key = ota.read_zip_member(archive, archive.getinfo("OTAFIX_MOTA_SIGNING_PUBLIC_KEY.txt")).decode().strip()
        if key != OFFICIAL_BOOT_KEY or inventory.get("signing_public_key") != OFFICIAL_BOOT_KEY:
            raise ota.OtaError("The bootloader release signing key does not match the pinned publisher.")
        matches = [entry for entry in inventory.get("packages", [])
                   if str(entry.get("hardware_id")) == boot.hw_id
                   and int(str(entry.get("target_id", "0")), 0) == boot.target_id]
        if len(matches) != 1:
            raise ota.OtaError("No normal bootloader package matches this exact installed identity.")
        row = matches[0]
        filename = row.get("file", "")
        if Path(filename).name != filename:
            raise ota.OtaError("Invalid bootloader bundle member.")
        blob = ota.read_zip_member(archive, archive.getinfo("mota/" + filename))
    if hashlib.sha256(blob).hexdigest().lower() != str(row.get("sha256", "")).lower():
        raise ota.OtaError("Bootloader package checksum does not match its release inventory.")
    lib = ota.bootloader_library()
    problems = lib.verify(lib.parse_container(blob), expect_pub=bytes.fromhex(OFFICIAL_BOOT_KEY))
    if problems:
        raise ota.OtaError("Bootloader signature or integrity check failed: " + "; ".join(problems))
    package = Package(blob, signer=OFFICIAL_BOOT_KEY)
    if destination.boot_version is not None and package.info.fw_version <= destination.boot_version:
        return None
    if destination.boot_version is None:
        # Unknown versions require an operator's explicit diagnosis rather than
        # guessing whether a protected/newer bootloader should be replaced.
        return None
    validate_package(package, destination)
    return package
