#!/usr/bin/env python3
"""Interactive, direct LoRa updates through one USB repeater or Full Companion."""
from __future__ import annotations

import argparse
import asyncio
from copy import copy
import getpass
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import venv

import lora_ota as ota
from ota_wizard_catalog import GitHubCatalog, ImageCache, download_asset, load_application
from ota_wizard_packages import (find_builder, find_running_image, install_builder,
                                 prepare_application, prepare_bootloader, small_host)
from ota_wizard_session import DirectSession, validate_package
from ota_wizard_bundle import (load_bundle, load_ready_application, load_request,
                               save_bundle, save_request, VerificationDependencyError)
from ota_wizard_usb import open_serial_session

ROOT = Path(__file__).resolve().parents[2]


def cache_directory() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "MeshCore/ota"
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches/MeshCore/ota"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "meshcore/ota"


def ask(prompt: str, default: str | None = None) -> str:
    if not sys.stdin.isatty():
        raise ota.OtaError("This choice needs an interactive terminal; supply the corresponding command-line option.")
    value = input(prompt + (f" [{default}]" if default is not None else "") + ": ").strip()
    return value or (default if default is not None else "")


def select(prompt: str, choices: list[str], default: int = 0) -> int:
    for index, value in enumerate(choices, 1):
        print(f"  {index}. {value}")
    while True:
        value = ask(prompt, str(default + 1))
        if value.isdigit() and 1 <= int(value) <= len(choices):
            return int(value) - 1
        print("Enter one of the numbers above.")


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     epilog="No passwords are saved. All updater transmissions use a direct, zero-hop route.")
    parser.add_argument("--port", help="USB port; selected automatically when only one radio is connected")
    parser.add_argument("--mode", choices=("auto", "repeater", "companion"), default="auto")
    parser.add_argument("--target", help="complete destination public key; otherwise discover nearby repeaters")
    parser.add_argument("--target-type", choices=("repeater",),
                        help="with --target and a repeater source, authenticate that known repeater directly without discovery")
    parser.add_argument("--firmware", type=Path, help="custom application DFU ZIP, BIN, HEX, full mOTA, or prepared OTA bundle")
    parser.add_argument("--base", type=Path, help="original running firmware, checked against the destination hash")
    parser.add_argument("--full", action="store_true", help="use a full update when the destination supports it")
    parser.add_argument("--build", action="store_true", help="build custom Full firmware from this source checkout")
    parser.add_argument("--source-tree", type=Path, default=ROOT)
    parser.add_argument("--version", help="version for a custom build; default is newer than the running version")
    parser.add_argument("--motatool", help="existing differential builder executable")
    parser.add_argument("--cache", type=Path, default=cache_directory())
    parser.add_argument("--no-bootloader", action="store_true", help="update only application firmware")
    parser.add_argument("--no-setup", action="store_true", help="do not create a private Python dependency environment")
    parser.add_argument("--recover", action="store_true", help="restore settings from an interrupted update, then exit")
    parser.add_argument("--yes", action="store_true", help="accept the displayed update plan; does not guess missing credentials")
    parser.add_argument("--runtime-ready", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--menu-action", choices=("update", "check", "prepare", "recover"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--check", action="store_true", help="inspect source and destination without preparing or installing an update")
    parser.add_argument("--prepare-only", action="store_true", help="prepare a portable update bundle on a computer without opening radios")
    parser.add_argument("--request", type=Path, help="destination preparation request saved by a previous live inspection")
    parser.add_argument("--output", type=Path, help="output .zip for --prepare-only")
    return parser.parse_args(argv)


class MenuCancelled(Exception):
    """Return to the main menu without changing a radio."""


def file_prompt(prompt: str, default: str | None = None) -> Path:
    if default and Path(default).expanduser().is_file():
        selected = select(prompt, ["Use " + default, "Choose another file", "Back"])
        if selected == 0:
            return Path(default).expanduser()
        if selected == 2:
            raise MenuCancelled
    while True:
        value = ask(prompt + " (blank to go back)")
        if not value:
            raise MenuCancelled
        path = Path(value.strip().strip('"').strip("'")).expanduser()
        if path.is_file():
            return path
        print("That file was not found. Enter its path, or leave it blank to go back.")


def menu_settings_path() -> Path:
    return cache_directory() / "menu-settings.json"


def load_menu_settings(args) -> None:
    try:
        values = json.loads(ota.read_bounded_file(menu_settings_path(), 8192, "menu settings"))
    except (OSError, ValueError, ota.OtaError):
        return
    if not isinstance(values, dict):
        return
    for field in ("full", "no_bootloader"):
        if type(values.get(field)) is bool:
            setattr(args, field, values[field])
    if values.get("mode") in ("auto", "repeater", "companion"):
        args.mode = values["mode"]
    for field in ("motatool", "version", "port"):
        value = values.get(field)
        if value is None or isinstance(value, str) and len(value) <= 4096:
            setattr(args, field, value)
    for field in ("source_tree", "cache"):
        value = values.get(field)
        if isinstance(value, str) and value and len(value) <= 4096:
            setattr(args, field, Path(value).expanduser())


def settings_menu(args) -> None:
    while True:
        print("\nSettings")
        options = [
            "Transfer: " + ("Full" if args.full else "Automatic (prefer differential)"),
            "Check bootloader: " + ("Off" if args.no_bootloader else "On"),
            "USB source type: " + args.mode.capitalize(),
            "USB port: " + (args.port or "Automatic"),
            "Differential builder: " + (args.motatool or "Automatic"),
            "Custom source folder: " + str(args.source_tree),
            "Custom version: " + (args.version or "Automatic"),
            "Download/cache folder: " + str(args.cache),
            "Back",
        ]
        choice = select("Setting", options, len(options)-1)
        if choice == 8:
            break
        if choice == 0:
            args.full = bool(select("Transfer", ["Automatic (prefer differential)", "Full"], int(args.full)))
        elif choice == 1:
            args.no_bootloader = bool(select("Check bootloader", ["On", "Off"], int(args.no_bootloader)))
        elif choice == 2:
            modes = ("auto", "repeater", "companion")
            args.mode = modes[select("USB source type", ["Automatic", "Repeater", "Companion"], modes.index(args.mode))]
        elif choice == 3:
            args.port = ask("USB port (blank for automatic)") or None
        elif choice == 4:
            args.motatool = ask("Differential builder path (blank for automatic)") or None
        elif choice == 5:
            args.source_tree = Path(ask("MeshCore source folder", str(args.source_tree))).expanduser()
        elif choice == 6:
            args.version = ask("Custom version (blank for automatic)") or None
        elif choice == 7:
            args.cache = Path(ask("Download/cache folder", str(args.cache))).expanduser()
    save_preferences(menu_settings_path(), **{
        field: str(getattr(args, field)) if field in ("cache", "source_tree") else getattr(args, field)
        for field in ("full", "no_bootloader", "mode", "port", "motatool", "source_tree", "version", "cache")})


def choose_request(args):
    records = []
    for path in sorted((args.cache / "requests").glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:50]:
        try:
            records.append((path, load_request(path)))
        except (ota.OtaError, OSError):
            continue
    if not records:
        return load_request(file_prompt("Preparation request JSON"))
    choices = [f"{record.hw_id} — {record.public_key[:12].upper()} ({record.version_text})"
               for _, record in records] + ["Open another preparation request file", "Back"]
    chosen = select("Preparation request", choices)
    if chosen == len(records) + 1:
        raise MenuCancelled
    return (records[chosen][1] if chosen < len(records)
            else load_request(file_prompt("Preparation request JSON")))


def choose_recovery(args) -> str:
    records = []
    for path in sorted((args.cache / "sessions").glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:50]:
        try:
            record = json.loads(ota.read_bounded_file(path, 64 * 1024, "recovery record"))
            key = record.get("target_key") if isinstance(record, dict) else None
            if (record.get("format") == 1 and isinstance(key, str)
                    and re.fullmatch(r"[0-9A-Fa-f]{64}", key)
                    and record.get("phase") not in ("finished", "prepared", "inspecting")):
                records.append((key, record.get("phase", "unfinished")))
        except (ota.OtaError, OSError, ValueError, AttributeError):
            continue
    if not records:
        print("No interrupted updates were found in this cache.")
        raise MenuCancelled
    chosen = select("Interrupted update", [f"{key[:12].upper()} — {phase}" for key, phase in records] + ["Back"])
    if chosen == len(records):
        raise MenuCancelled
    return records[chosen][0]


def ensure_dependencies(args, argv) -> None:
    required = ("serial", "meshcore", "cryptography")
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if "meshcore" not in missing:
        try:
            if importlib.metadata.version("meshcore") != "2.3.9.1":
                missing.append("tested meshcore version")
        except importlib.metadata.PackageNotFoundError:
            missing.append("meshcore package metadata")
    if not missing:
        return
    if args.no_setup or args.runtime_ready:
        raise ota.OtaError("Install the USB updater dependencies: python -m pip install -r "
                           + str(Path(__file__).with_name("requirements-wizard.txt")))
    print("First run: preparing USB and update-verification support in a private Python environment.")
    environment = args.cache / "python" / f"{sys.version_info.major}.{sys.version_info.minor}"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        try:
            venv.EnvBuilder(with_pip=True).create(environment)
        except Exception as exc:
            raise ota.OtaError("Python's venv support is required. On Raspberry Pi OS, install python3-venv.") from exc
    log = environment / "setup.log"
    with log.open("w", encoding="utf-8") as output:
        completed = subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                                    "--only-binary=:all:", "-r",
                                    str(Path(__file__).with_name("requirements-wizard.txt"))],
                                   stdout=output, stderr=subprocess.STDOUT, timeout=600)
    if completed.returncode:
        raise ota.OtaError(f"USB support could not be installed without compiling dependencies. Details: {log}")
    os.execv(str(python), [str(python), str(Path(__file__).resolve()), "--runtime-ready", *argv])


def choose_port(explicit: str | None, remembered: dict | None = None) -> str:
    if explicit:
        return explicit
    from serial.tools import list_ports
    ports = [port for port in list_ports.comports() if port.vid is not None]
    if not ports:
        raise ota.OtaError("Connect the source repeater or Full Companion by USB, then run this command again.")
    if len(ports) == 1:
        return ports[0].device
    default_port = next((index for index, port in enumerate(ports)
                         if port.device == (remembered or {}).get("port")), 0)
    index = select("USB radio", [f"{port.description} — {port.device}" for port in ports], default_port)
    return ports[index].device


def remembered_choices(cache: Path) -> dict:
    try:
        value = json.loads(ota.read_bounded_file(cache / "choices.json", 8192, "remembered choices"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, ota.OtaError):
        return {}


def detect_mode(port: str) -> str:
    """A harmless text probe avoids feeding Binary commands to a repeater CLI."""
    import serial
    connection = serial.Serial(port=None, baudrate=115200, timeout=.1, write_timeout=2)
    try:
        open_serial_session(connection, port)
        time.sleep(.75)
        connection.write(b"\rver\rota status\r")
        deadline = time.monotonic() + 3
        data = bytearray()
        while time.monotonic() < deadline and len(data) < 16384:
            data.extend(connection.read(1024))
        text = data.decode("utf-8", "replace")
        if "companion" in text.lower() or "ota seeder" in text.lower():
            return "companion"
        if re.search(r"(?m)(?:->\s*|^\s*>?\s*)v\d+\.\d+", text):
            return "repeater"
        return "companion"  # A Binary Companion does not answer text probes.
    finally:
        connection.close()


def save_preferences(path: Path, **values) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def user_progress(message: str) -> None:
    """Keep raw wire diagnostics in recovery records, not the normal UI."""
    if message.startswith("OTA "):
        count = re.search(r"\b(\d+)/(\d+)\b", message)
        if "ready to install" in message.lower():
            print("Download complete; checking the package before installation.")
        elif count:
            have, total = map(int, count.groups())
            print(f"Downloading: {100 * have // total if total else 0}% ({have}/{total} blocks)")
        else:
            print("Waiting for the destination's download status…")
    else:
        print(message)


def custom_build(args, destination, target_env: str) -> Path:
    source = args.source_tree.resolve()
    if not (source / "build.sh").is_file():
        raise ota.OtaError("Custom builds need a MeshCore source checkout; set --source-tree to that directory.")
    if not re.fullmatch(r"[A-Za-z0-9_]+", target_env):
        raise ota.OtaError("The detected firmware target cannot be used as a build environment.")
    version = args.version or ota.format_version(destination.version + 1) + "-custom"
    packed_version = ota.extract_reply_version(version)
    if packed_version is None or packed_version <= destination.version:
        raise ota.OtaError("The custom build version must be newer than the running version.")
    output = args.cache.resolve() / "custom" / (time.strftime("%Y%m%d-%H%M%S") + "-" + target_env)
    output.mkdir(parents=True, exist_ok=False)
    # build.sh holds the repository's build lock and runs one PlatformIO process.
    command = ["bash", "build.sh", "build-firmware", target_env,
               "--build-profile", "full" if destination.platform == "esp32" else "auto",
               "--radio-preset", "target",
               "--profile", "cascade", "--firmware-version", version]
    if destination.platform == "esp32":
        command += ["--full-exact"]
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("MESHCORE_ADMIN_PASSWORD", "MESHCORE_OTA_PASSWORD")}
    environment["OUTPUT_DIR"] = output.as_posix()
    if os.name == "nt" and shutil.which("wsl.exe"):
        def linux_path(path: Path) -> str:
            mapped = subprocess.run(["wsl.exe", "-e", "wslpath", "-a", str(path)],
                                    capture_output=True, text=True, timeout=15)
            if mapped.returncode or not mapped.stdout.strip().startswith("/"):
                raise ota.OtaError("Custom builds need a working WSL installation. Run this updater inside WSL, or choose a firmware file.")
            return mapped.stdout.strip()
        command = ["wsl.exe", "--cd", linux_path(source), "--exec", "env",
                   "-u", "MESHCORE_ADMIN_PASSWORD", "-u", "MESHCORE_OTA_PASSWORD",
                   "OUTPUT_DIR=" + linux_path(output), *command]
    elif not shutil.which("bash"):
        raise ota.OtaError("Custom builds need Bash and the firmware build tools. Use a published or local firmware file instead.")
    log = output / "build.log"
    print(f"Building custom Full firmware. This may download board tools; progress is saved to {log}.")
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(command, cwd=source, env=environment,
                                   stdout=stream, stderr=subprocess.STDOUT, timeout=7200)
    if completed.returncode:
        raise ota.OtaError(f"The custom build failed; its full log is in {log}.")
    candidates = list(output.glob("*.zip")) + [path for path in output.glob("*.bin") if "merged" not in path.name]
    for candidate in candidates:
        try:
            load_application(candidate, target_id=destination.target_id, hw_id=destination.hw_id)
            return candidate
        except ota.OtaError:
            continue
    raise ota.OtaError("The custom build did not produce a matching application image.")


async def latest_asset(catalog, destination):
    print("Checking GitHub releases for this exact board and role…")
    try:
        return await asyncio.to_thread(catalog.choose_asset, target_id=destination.target_id)
    except ota.OtaError as exc:
        print(str(exc))
        return None


async def choose_release(catalog, destination):
    pages = 3
    while True:
        choices = await asyncio.to_thread(catalog.release_choices, target_id=destination.target_id,
                                          include_prerelease=True, max_pages=pages)
        if not choices:
            print("No compatible firmware was found on these release pages.")
        labels = [choice.label for choice in choices]
        more = pages < 10 and catalog.has_more_releases
        if more:
            labels.append("Load older GitHub releases")
        labels.append("Back")
        selected = select("GitHub release", labels)
        if selected < len(choices):
            return choices[selected].asset
        if more and selected == len(choices):
            pages = min(10, pages + 3)
            continue
        raise MenuCancelled


async def choose_firmware(args, destination, catalog, asset, remembered):
    if remembered.get("target") != destination.public_key.lower():
        remembered = {}
    if args.firmware:
        return args.firmware, "local"
    preferred = remembered.get("firmware_source", "latest")
    while True:
        source = "build" if args.build else "latest"
        if not args.build and not args.yes:
            sources = ["latest", "release", "local"]
            labels = ["Latest compatible GitHub release (recommended)",
                      "Choose another GitHub release", "Local firmware file or prepared bundle"]
            if not small_host():
                sources.append("build")
                labels.append("Build custom Full firmware from source")
            default = sources.index(preferred) if preferred in sources else 0
            selected = select("Firmware", labels + ["Back"], default)
            if selected == len(sources):
                raise MenuCancelled
            source = sources[selected]
        if source == "local":
            try:
                return file_prompt("Firmware ZIP, BIN, HEX, full mOTA, or prepared bundle",
                                   remembered.get("firmware_file")), source
            except MenuCancelled:
                continue
        if source == "release":
            try:
                selected_asset = await choose_release(catalog, destination)
            except MenuCancelled:
                continue
        else:
            if asset is None:
                asset = await latest_asset(catalog, destination)
            if asset is None:
                if args.yes or args.build:
                    raise ota.OtaError("No GitHub application matched this board. Choose another release or a local firmware file.")
                preferred = "local"
                continue
            selected_asset = asset
        if source in ("latest", "release"):
            print(f"GitHub release: {selected_asset.release_tag}\n  {selected_asset.name}")
            return await asyncio.to_thread(download_asset, selected_asset, args.cache / "downloads"), source
        if not args.version:
            args.version = ota.format_version(max(destination.version, selected_asset.version) + 1) + "-custom"
        return await asyncio.to_thread(custom_build, args, destination, selected_asset.target_env), source


async def choose_missing_base(args, destination, catalog):
    """Offer the useful fallbacks only after the automatic exact-base search."""
    pages = 3
    while True:
        choices, actions = [], []
        if pages < 10:
            choices.append("Search older GitHub releases for the running firmware")
            actions.append("github")
        choices.append("Choose a local copy of the exact running firmware")
        actions.append("local")
        if destination.platform == "esp32" or destination.external_storage:
            choices.append("Use a full update instead")
            actions.append("full")
        choices.append("Back")
        actions.append("back")
        action = actions[select("The exact running image was not found", choices)]
        if action == "back":
            raise MenuCancelled
        if action == "full":
            return None
        if action == "local":
            supplied = file_prompt("Exact running firmware ZIP, BIN, HEX, or full mOTA")
            try:
                return await asyncio.to_thread(find_running_image, destination, args.cache, catalog, supplied=supplied)
            except ota.OtaError as exc:
                print(str(exc))
                continue
        pages = min(10, pages + 3)
        try:
            await asyncio.to_thread(catalog.release_choices, target_id=destination.target_id,
                                    include_prerelease=True, max_pages=pages)
            base = await asyncio.to_thread(find_running_image, destination, args.cache, catalog,
                                           candidate_limit=16 if pages < 10 else 32)
            if base:
                return base
            if not catalog.has_more_releases:
                pages = 10
            print("No downloaded GitHub candidate matched the exact running image hash.")
        except ota.OtaError as exc:
            print(str(exc))


async def prepare_selected_firmware(args, destination, firmware, catalog):
    prepared = await asyncio.to_thread(load_bundle, firmware, destination)
    if prepared:
        image, application = prepared
        if args.full and not application.info.is_full:
            if application.signer:
                raise ota.OtaError("This signed delta cannot be converted without losing its signature. "
                                   "Choose Automatic or provide a signed full package.")
            application = await asyncio.to_thread(prepare_application, image, destination, args.cache, full=True)
    else:
        image = await asyncio.to_thread(load_application, firmware,
                                        target_id=destination.target_id, hw_id=destination.hw_id)
        application = await asyncio.to_thread(load_ready_application, firmware, image, destination,
                                              validate_receiver=False)
        if application and not application.signer and not args.full:
            application = None  # Automatic also considers deltas for unsigned full mOTA inputs.
    ImageCache(args.cache).remember(image)
    if image.fw_version <= destination.version:
        print("The selected firmware is already installed or older; keeping the running firmware.")
        return image, None
    if application:
        validate_package(application, destination)
        return image, application
    builder = None if args.full else find_builder(args.cache, args.motatool)
    if not args.full and not builder and not small_host():
        builder = await asyncio.to_thread(install_builder, args.cache)
    base = None
    if builder:
        base = await asyncio.to_thread(find_running_image, destination, args.cache, catalog, supplied=args.base)
        if base is None and catalog is None and args.base is None:
            catalog = GitHubCatalog()
            base = await asyncio.to_thread(find_running_image, destination, args.cache, catalog)
        if base is None and getattr(args, "interactive_menu", False):
            base = await choose_missing_base(args, destination, catalog)
    if not builder and not args.full:
        print("Using a full update: no differential compiler setup is needed on this host.")
    if destination.platform == "nrf52" and not destination.external_storage and not (base and builder):
        raise ota.OtaError(
            "This radio needs an exact-base differential update. On a computer, choose "
            "Prepare an update bundle and select its saved preparation request. "
            "A compatible differential builder must be available; its path can be set under Settings.")
    application = await asyncio.to_thread(prepare_application, image, destination, args.cache,
                                          base=base, builder=builder, full=args.full)
    return image, application


async def prepare_interactively(args) -> int:
    destination = choose_request(args)
    print(f"Preparing for {destination.hw_id} — {destination.public_key[:12].upper()} ({destination.version_text})")
    catalog = GitHubCatalog()
    args.interactive_menu = True
    firmware, _ = await choose_firmware(args, destination, catalog, None, {})
    verification_cache = ota.cryptography_repair_root()
    if (verification_cache / "cryptography").is_dir():
        try:
            ota.activate_cached_cryptography(verification_cache)
        except ota.OtaError:
            pass  # A working system verifier may already be loaded.
    try:
        image, package = await prepare_selected_firmware(args, destination, firmware, catalog)
    except (ImportError, VerificationDependencyError):
        # Unsigned full packaging stays dependency-free. Signed inputs request
        # their verifier only when it is actually needed; no USB SDK is loaded.
        await asyncio.to_thread(ensure_signature_support)
        image, package = await prepare_selected_firmware(args, destination, firmware, catalog)
    if package is None:
        print("Choose a newer firmware release to prepare an update.")
        return 0
    folder = args.cache / "bundles"
    default = folder / f"{destination.hw_id}-{ota.format_version(image.fw_version)}.ota.zip"
    index = 1
    while default.exists():
        default = folder / f"{destination.hw_id}-{ota.format_version(image.fw_version)}-{index}.ota.zip"
        index += 1
    output = Path(ask("Save update bundle", str(default)).strip().strip('"').strip("'")).expanduser()
    await asyncio.to_thread(save_bundle, output, image, package)
    print(f"Prepared {package.info.kind}: {output} ({len(package.blob):,} transfer bytes)")
    print("Copy this ZIP to the Pi. Choose Update a nearby radio, then Local firmware file or prepared bundle.")
    return 0


def ensure_signature_support() -> None:
    root = ota.cryptography_repair_root()
    if root.is_dir():
        try:
            ota.activate_cached_cryptography(root)
            return
        except ota.OtaError:
            pass
    print("Preparing signature verification support. This is a one-time download.")
    root.mkdir(parents=True, exist_ok=True)
    log = root / "setup.log"
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("MESHCORE_ADMIN_PASSWORD", "MESHCORE_OTA_PASSWORD")}
    with log.open("w", encoding="utf-8") as output:
        completed = subprocess.run(ota.cryptography_repair_command(root), env=environment,
                                   stdout=output, stderr=subprocess.STDOUT, timeout=600)
    if completed.returncode:
        raise ota.OtaError(f"Signature verification support could not be installed. Details: {log}")
    ota.activate_cached_cryptography(root)


async def run(args) -> int:
    if args.target and not re.fullmatch(r"[0-9A-Fa-f]{64}", args.target):
        raise ota.OtaError("--target needs the complete destination public key.")
    known_repeater = args.target if getattr(args, "target_type", None) == "repeater" else None
    if getattr(args, "target_type", None) == "repeater" and not args.target:
        raise ota.OtaError("--target-type repeater requires --target with the complete destination public key.")
    args.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    remembered = remembered_choices(args.cache)
    port = choose_port(args.port, remembered)
    mode = args.mode if args.mode != "auto" else await asyncio.to_thread(detect_mode, port)
    if known_repeater is not None and mode != "repeater":
        raise ota.OtaError("--target-type repeater requires a USB repeater source (--mode repeater).")
    if mode == "companion":
        from ota_wizard_companion import CompanionTransport
        link = CompanionTransport(port)
    else:
        from ota_wizard_repeater import RepeaterTransport
        link = (RepeaterTransport(port, known_repeater=known_repeater)
                if known_repeater is not None else RepeaterTransport(port))
    print(f"Connecting to the USB {mode} on {port}…")
    await link.open()
    session = None
    try:
        key = args.target
        if known_repeater is not None:
            print("Authenticating the supplied repeater key directly without discovery…")
        elif not args.recover:
            print("Looking for directly reachable repeaters and rooms…")
            nearby = await link.discover()
            if not nearby:
                raise ota.OtaError("No direct destinations replied. Check both radios use the same channel, then retry.")
            if key:
                if not any(item["public_key"].lower() == key.lower() for item in nearby):
                    raise ota.OtaError("The selected public key did not answer direct discovery.")
            else:
                default_target = next((index for index, item in enumerate(nearby)
                                       if item["public_key"].lower() == remembered.get("target")), 0)
                selected = 0 if len(nearby) == 1 else select("Destination", [
                    f"{'Room' if item.get('type') == 3 else 'Repeater'} — {item['public_key'][:12].upper()}"
                    for item in nearby], default_target)
                key = nearby[selected]["public_key"]
        elif not key:
            key = remembered.get("target") or ask("Destination public key from the recovery record")
        print(f"Destination: {key[:12].upper()} (zero-hop direct)")
        password = os.environ.get("MESHCORE_ADMIN_PASSWORD")
        if password is None:
            if not sys.stdin.isatty():
                raise ota.OtaError("Set MESHCORE_ADMIN_PASSWORD for a noninteractive run.")
            password = getpass.getpass("Destination admin password [Enter for default]: ") or "password"
        journal = args.cache / "sessions" / (key.lower() + ".json")
        previous = None
        if journal.is_file():
            previous = json.loads(ota.read_bounded_file(journal, 64 * 1024, "recovery record"))
            if not isinstance(previous, dict) or previous.get("format") != 1:
                raise ota.OtaError("The interrupted update record has an unsupported format.")
            if previous.get("phase") not in ("finished", "prepared", "inspecting"):
                source_key = previous.get("source_key")
                if (previous.get("target_key") != key.lower() or not isinstance(source_key, str)
                        or not isinstance(link.source_key, str) or source_key.lower() != link.source_key.lower()):
                    raise ota.OtaError("The unfinished recovery record belongs to a different source or destination.")
                saved = previous.get("settings")
                if not isinstance(saved, dict) or set(saved) != {"source", "target"}:
                    raise ota.OtaError("The interrupted update record has incomplete saved settings.")
                for settings in saved.values():
                    if (not isinstance(settings, dict) or not all(
                            isinstance(settings.get(field), str) and len(settings[field]) <= 2048
                            for field in ("radio", "rxps", "ota"))):
                        raise ota.OtaError("The interrupted update record has invalid saved settings.")
        session = DirectSession(link, key, password, journal, progress=user_progress)
        if previous and previous.get("phase") not in ("finished", "prepared", "inspecting"):
            print("Restoring the settings saved by the interrupted update first…")
            session.saved = previous["settings"]
            session.active = True
            session._state = previous
            failures = await session.cleanup()
            if failures:
                raise ota.OtaError("Some recovery settings could not be restored; see " + str(journal))
            session = DirectSession(link, key, password, journal, progress=user_progress)
        if args.recover:
            print("Recovery is complete.")
            return 0
        destination = await session.inspect()
        request_file = args.cache / "requests" / (key.lower() + ".json")
        save_request(request_file, destination)
        print(f"{destination.hw_id}: {destination.version_text}")
        if args.check:
            print("Direct authentication and OTA capability checks passed. No update was installed.")
            print(f"Preparation request: {request_file}")
            return 0
        await session.snapshot()
        catalog = GitHubCatalog()
        firmware, choice = await choose_firmware(args, destination, catalog, None, remembered)
        image, application = await prepare_selected_firmware(args, destination, firmware, catalog)
        bootloader = None
        if not args.no_bootloader and destination.platform == "nrf52":
            print("Checking the exact-board bootloader update…")
            bootloader = await asyncio.to_thread(prepare_bootloader, destination, args.cache)
        if not application and not bootloader:
            print("No newer compatible update is needed.")
            return 0
        print("\nReady to update:")
        print(f"  Destination: {destination.hw_id} — {key[:12].upper()}")
        if bootloader:
            print(f"  Bootloader: {bootloader.info.version}, signed exact-board image")
        if application:
            print(f"  Firmware: {application.info.version}, {application.info.kind}, {len(application.blob):,} bytes")
        if bootloader or application and application.signer:
            signer = bootloader.signer if bootloader else application.signer
            print(f"  Trusted update signer: {signer[:16]}")
        print("  Direct link only. Original radio and power-saving settings will be restored.")
        if not args.yes and ask("Start", "Y").lower() not in ("y", "yes"):
            print("Update cancelled.")
            return 0
        for package in (bootloader, application):
            if package:
                destination = await session.install(package, destination)
                print(f"Verified {package.info.kind} installation.")
        save_preferences(args.cache / "choices.json", port=port, mode=mode,
                         target=key.lower(), hw=destination.hw_id, firmware_source=choice,
                         firmware_file=str(firmware) if choice == "local" else None)
        print("Update verified successfully.")
        return 0
    finally:
        cleanup_failed = False
        if session:
            failures = await session.cleanup()
            if failures:
                cleanup_failed = True
                print(f"Some settings still need recovery. Record: {session.journal}", file=sys.stderr)
                for command in failures:
                    print("  " + command, file=sys.stderr)
        await link.close()
        if cleanup_failed and sys.exc_info()[0] is None:
            raise ota.OtaError("The update completed but settings recovery is still needed; see the saved record.")


def prepare_without_radios(args) -> int:
    if not args.request or not args.firmware or not args.output:
        raise ota.OtaError("Use --prepare-only --request DEVICE.json --firmware FILE --output update.ota.zip.")
    destination = load_request(args.request)
    image, package = asyncio.run(prepare_selected_firmware(args, destination, args.firmware, None))
    if package is None:
        raise ota.OtaError("Choose firmware newer than the running version.")
    save_bundle(args.output, image, package)
    print(f"Prepared {package.info.kind}: {args.output} ({len(package.blob):,} transfer bytes)")
    print("Copy this ZIP to the Pi and select it as the custom firmware file.")
    return 0


def menu_main(args) -> int:
    load_menu_settings(args)
    action = args.menu_action
    while True:
        if action is None:
            print("\nMeshCore LoRa updater")
            actions = ("update", "check", "prepare", "recover", "settings", "exit")
            action = actions[select("Choose", [
                "Update a nearby radio", "Check radios / save a preparation request",
                "Prepare an update bundle on this computer", "Recover an interrupted update",
                "Settings", "Exit"])]
        current = copy(args)
        current.interactive_menu = True
        current.check, current.recover = action == "check", action == "recover"
        try:
            if action == "exit":
                return 0
            if action == "settings":
                settings_menu(args)
            elif action == "prepare":
                asyncio.run(prepare_interactively(current))
            else:
                # Preserve the chosen menu action if first-run dependency
                # setup reexecutes inside the private Python environment.
                ensure_dependencies(current, ["--menu-action", action, "--cache", str(args.cache)])
                if action == "recover":
                    current.target = choose_recovery(current)
                asyncio.run(run(current))
        except MenuCancelled:
            pass
        except (ota.OtaError, RuntimeError, OSError, ValueError, ImportError, subprocess.TimeoutExpired) as exc:
            print(f"Update stopped: {exc}", file=sys.stderr)
        action = None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = arguments(argv)
    try:
        if args.menu_action or not any(value != "--runtime-ready" for value in argv):
            return menu_main(args)
        if args.prepare_only:
            return prepare_without_radios(args)
        ensure_dependencies(args, argv)
        return asyncio.run(run(args))
    except MenuCancelled:
        return 0
    except EOFError:
        return 0
    except KeyboardInterrupt:
        print("\nStopped. Check the recovery record if the destination was unreachable during cleanup.", file=sys.stderr)
        return 130
    except (ota.OtaError, RuntimeError, OSError, ValueError, ImportError, subprocess.TimeoutExpired) as exc:
        print(f"Update stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
