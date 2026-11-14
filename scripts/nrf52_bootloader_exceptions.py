"""Render the qualified local release's exact nRF52 bootloader exceptions."""

from __future__ import annotations

import re


DISPLAY_NAMES = {
    "Heltec_mesh_solar": "Heltec Mesh Solar",
    "Heltec_tower_v2": "Heltec MeshTower V2",
    "LilyGo_T-Echo_Card": "LilyGo T-Echo Card",
    "LilyGo_T_Impulse_Plus": "LilyGo T-Impulse Plus",
    "MeshTracker_X1": "MeshTracker X1",
    "Nano_G2_Ultra": "Nano G2 Ultra",
    "ThinkNode_M4": "ThinkNode M4",
    "ThinkNode_M8": "ThinkNode M8",
    "muzi_base_uno": "Muzi Base Uno",
    "muzi_base_duo": "Muzi Base Duo",
    "wio_wm1110": "Wio WM1110 Dev Kit",
}


def render_bootloader_exceptions(variants: dict | None) -> str:
    """Describe manifest exclusions without inventing unsupported board aliases."""
    if variants is None:
        return ""
    if not isinstance(variants, dict) or not isinstance(variants.get("exclusions"), list):
        raise ValueError("invalid nRF52 bootloader exception inventory")
    version = variants.get("release")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", version):
        raise ValueError("invalid nRF52 bootloader release version")

    entries = {}
    targets = set()
    for item in variants["exclusions"]:
        if not isinstance(item, dict):
            raise ValueError("invalid nRF52 bootloader exception")
        hardware, target = item.get("source_hardware"), item.get("target")
        status, reason = item.get("status"), item.get("reason")
        if (not isinstance(hardware, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", hardware)
                or not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", target)
                or not isinstance(status, str) or status not in ("source-pending", "source-blocked", "retired")
                or not isinstance(reason, str) or not reason.strip() or not reason.isascii()
                or any(char in reason for char in "\r\n") or target in targets):
            raise ValueError("invalid nRF52 bootloader exception")
        targets.add(target)
        entry = entries.setdefault(hardware, {"status": status, "reason": reason, "targets": []})
        if entry["status"] != status or entry["reason"] != reason:
            raise ValueError("inconsistent nRF52 bootloader exception for board")
        entry["targets"].append(target)

    if not entries:
        return ""
    lines = [
        "# nRF52 bootloader exceptions", "",
        f"Boards listed under pending qualification have no verified bundled or standalone "
        f"OTAFIX {version} bootloader in this release. Their application-only firmware "
        "preserves the installed bootloader; check that the application and installed "
        "bootloader are compatible. Do not use a bootloader intended for a different board. "
        "Retired profiles are listed separately and do not imply that all profiles for that "
        "board are unavailable.", "",
    ]
    for title, statuses in (
        ("Pending qualification", {"source-pending", "source-blocked"}),
        ("Retired profile", {"retired"}),
    ):
        matches = [(hardware, entry) for hardware, entry in sorted(entries.items())
                   if entry["status"] in statuses]
        if not matches:
            continue
        lines.extend(["## " + title, ""])
        for hardware, entry in matches:
            name = DISPLAY_NAMES.get(hardware, hardware.replace("_", " "))
            targets_text = ", ".join(f"`{target}`" for target in sorted(entry["targets"]))
            lines.extend([f"- **{name}:** {entry['reason']}", f"  Affected targets: {targets_text}."])
        lines.append("")
    lines.extend([
        "[OTAFIX board coverage and qualification details]"
        "(https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/blob/"
        "feature/ota-delta-apply/docs/meshcore-hardware-coverage.md)", "",
    ])
    return "\n".join(lines)
