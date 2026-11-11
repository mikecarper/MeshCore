"""Pinned, exact published OTAFIX fork images for the MBR migration gate.

Each ZIP's installed bootloader bytes were checked against its manifest, CRC
and vectors. Separately, the published UF2 flash bytes were reconstructed and
compared to its matching ZIP. Their 40 KiB region hashes differ where UF2
blocks write padding, so both exact fingerprints are listed. A different live
tail fails closed.
These are provenance records, not evidence of hardware-qualified migration.
Runtime MCU, SoftDevice, flash geometry and write-protection gates still apply.
"""
import json
from pathlib import Path
import re

# Exact release profile, successor profile, and approved physical targets. Do
# not infer compatibility from a shared USB ID, MCU, or a similarly named board.
_PROFILES = {
    "heltec_t096": ("heltec_t096", ("Heltec_t096",), "6.1.1"),
    "heltec_t1": ("heltec_t1", ("Heltec_t1",), "6.1.1"),
    "heltec_t114": ("heltec_t114", ("Heltec_t114", "Heltec_t114_without_display"), "6.1.1"),
    "lilygo_techo": ("lilygo_techo", ("LilyGo_T-Echo",), "6.1.1"),
    "minewsemi_mx25le01": ("minewsemi_mx25le01", ("Minewsemi_me25ls01",), "7.3.0"),
    "promicro_nrf52840": ("promicro_nrf52840", ("ProMicro",), "6.1.1"),
    "sensecap_solar_p1": ("sensecap_solar_p1", ("SenseCap_Solar",), "7.3.0"),
    "t1000_e": ("t1000_e", ("t1000e",), "7.3.0"),
    "thinknode_m1": ("thinknode_m1", ("ThinkNode_M1",), "6.1.1"),
    "thinknode_m3": ("thinknode_m3", ("ThinkNode_M3",), "6.1.1"),
    "thinknode_m6": ("thinknode_m6", ("ThinkNode_M6",), "6.1.1"),
    "wio_tracker_l1": ("wio_tracker_l1", ("WioTrackerL1", "WioTrackerL1-1W", "WioTrackerL1Eink"), "7.3.0"),
    "wiscore_rak3401": ("wiscore_rak3401_auto", ("RAK_3401",), "6.1.1"),
    "wiscore_rak4631_board": ("wiscore_rak4631_auto", ("RAK_4631",), "6.1.1"),
    "wismesh_tag": ("wismesh_tag", ("RAK_WisMesh_Tag",), "6.1.1"),
    "xiao_nrf52840_ble": ("xiao_nrf52840_ble", ("Xiao_nrf52",), "7.3.0"),
    "xiao_nrf52840_ble_sense": ("xiao_nrf52840_ble_sense", ("Xiao_nrf52_Sense",), "7.3.0"),
}


def reviewed_fork_bootloaders():
    data = json.loads((Path(__file__).with_name("nrf52_fork_bootloader_inventory.json")).read_text(encoding="utf-8"))
    records = []
    for owner, tag, name, artifact_hash, image_hash in data:
        match = re.fullmatch(r"([a-z0-9_]+)_bootloader-([A-Za-z0-9.\-]+)_s140_(6\.1\.1|7\.3\.0)\.zip", name)
        uf2 = re.fullmatch(r"update-([a-z0-9_]+)_bootloader-([A-Za-z0-9.\-]+)_nosd\.uf2", name)
        if uf2:
            source, version = uf2.groups()
            softdevice = _PROFILES[source][2] if source in _PROFILES else None
        elif match:
            source, version, softdevice = match.groups()
        else:
            raise ValueError("invalid reviewed fork asset name")
        if (owner not in ("oltaco", "vk496") or
                tag != ("dev-latest" if owner == "vk496" else version)):
            raise ValueError("invalid reviewed fork release entry")
        if source not in _PROFILES or softdevice != _PROFILES[source][2]:
            raise ValueError("unreviewed fork bootloader profile or SoftDevice")
        profile, hardware, _ = _PROFILES[source]
        app_base, fwid = (0x26000, 0xB6) if softdevice == "6.1.1" else (0x27000, 0x123)
        records.append({
            "name": name,
            "version": version,
            "source_url": f"https://github.com/{owner}/Adafruit_nRF52_Bootloader_OTAFIX/releases/download/{tag}/{name}",
            "artifact_sha256": artifact_hash,
            "image_sha256": image_hash,
            "profiles": [profile],
            "hardware": list(hardware),
            "app_base": app_base,
            "softdevice_fwid": fwid,
        })
    return records
