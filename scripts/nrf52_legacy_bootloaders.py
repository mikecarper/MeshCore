"""Reviewed stock bootloader byte identities for application-preserving MBR copies.

These are exact 40 KiB images, not version-string, USB-ID or board-family rules.
A source image is admitted only for the listed physical hardware, replacement
profile and *running* S140 layout. Runtime protection/MBR/vector/persistence gates
remain mandatory. This inventory does not claim live power-cut qualification.

Sources and hashes are cross-checked against the independent stock reader audit.
Representative SDK images are not propagated to other products sharing a chip.
"""
from __future__ import annotations

import re

from nrf52_fork_bootloaders import reviewed_fork_bootloaders

# Bind each physical target to its replacement, not a Cartesian product of
# shared factory images. In particular RAK3401 never selects a 4631 successor.
_PHYSICAL_PROFILES = {
    "RAK_3401": "wiscore_rak3401_auto",
    "RAK_4631": "wiscore_rak4631_auto",
    "R1Neo": "wiscore_rak4631_auto",
    "Heltec_t114": "heltec_t114",
    "Heltec_t114_without_display": "heltec_t114",
    "Heltec_mesh_solar": "heltec_mesh_solar",
    "Heltec_t096": "heltec_t096",
    "Heltec_t1": "heltec_t1",
    "LilyGo_T-Echo": "lilygo_techo",
    "LilyGo_T-Echo-Lite": "lilygo_techo_lite",
    "LilyGo_T-Echo-Lite_non_shell": "lilygo_techo_lite",
    "LilyGo_T-Echo_Card": "lilygo_techo_card",
    "LilyGo_T_Impulse_Plus": "lilygo_t_impulse_plus",
    "MeshTracker_X1": "meshtracker_x1",
    "Nano_G2_Ultra": "nano_g2_ultra",
    "t1000e": "t1000_e",
    "SenseCap_Solar": "sensecap_solar_p1",
    "Minewsemi_me25ls01": "minewsemi_mx25le01",
    "ProMicro": "promicro_nrf52840",
    "ThinkNode_M1": "thinknode_m1",
    "ThinkNode_M3": "thinknode_m3",
    "ThinkNode_M6": "thinknode_m6",
    "RAK_WisMesh_Tag": "wismesh_tag",
    "WioTrackerL1": "wio_tracker_l1",
    "WioTrackerL1-1W": "wio_tracker_l1",
    "WioTrackerL1Eink": "wio_tracker_l1",
    "Xiao_nrf52": "xiao_nrf52840_ble",
    "Xiao_nrf52_Sense": "xiao_nrf52840_ble_sense",
}

# Plus factory bootloaders are documented separately, but compatibility with
# the non-Plus OTAFIX successors has not been qualified. Keep them in the
# provenance inventory while refusing to authorize automatic replacement.
_UNQUALIFIED_XIAO_PLUS_IMAGES = frozenset({
    "caf05e859193001ef745d21ae71b218f2f23a0ed995a4adc69debcdb31e35ba8",
    "b19bd74a743a8311b12a07ae5cc17f28966d6e61b66f93f2afb2ceb52c5240d2",
})

_RECORDS = [
    {
        "name": "HT-n5262_bootloader-0.9.0_s140_6.1.1.hex",
        "version": "0.9.0-2-g836c8dc-dirty",
        "source_url": "https://raw.githubusercontent.com/HelTecAutomation/Heltec_nRF52/main/bootloader/HT-n5262/HT-n5262_bootloader-0.9.0_s140_6.1.1.hex",
        "artifact_sha256": "b0eb140afac57633208ace8a98908e8c90149a38c1b0b62cf65e0ec48acea1b7",
        "image_sha256": "8618c2d91d855afab573d4e9e7efcdc61d80242faa7b9e74f403a81e744abe16",
        "profiles": [
            "heltec_t114", "heltec_mesh_solar"
        ],
        "hardware": [
            "Heltec_t114",
            "Heltec_t114_without_display", "Heltec_mesh_solar"
        ],
        "app_base": 155648,
        "softdevice_fwid": 182
    },
    {
        "name": "t_echo_card_nrf52840_bootloader-0.9.2-dirty_s140_6.1.1_202608131146.hex",
        "version": "0.9.2-dirty",
        "source_url": "https://raw.githubusercontent.com/Xinyuan-LilyGO/T-Echo-Card/main/bootloader/T-Echo-Card%20bootloader/t_echo_card_nrf52840_bootloader-0.9.2-dirty_s140_6.1.1_202608131146.hex",
        "artifact_sha256": "c6b8025cb855d15fb06cbaba24d460a12bfc87b22df10667fb88d9ff13c5941e",
        "image_sha256": "efd403e7d211534a019467e9c39781bf49d4252fbb4850e8bb21fdc7895dab12",
        "profiles": ["lilygo_techo_card"], "hardware": ["LilyGo_T-Echo_Card"],
        "app_base": 155648, "softdevice_fwid": 182
    },
    {
        "name": "t_impulse_plus_nrf52840_bootloader-0.9.2-dirty_s140_6.1.1_202508251059.hex",
        "version": "0.9.2-dirty",
        "source_url": "https://raw.githubusercontent.com/Xinyuan-LilyGO/T-Impulse-Plus/main/bootloader/%28%E4%BF%AE%E6%94%B9%E5%90%AF%E5%8A%A8%E6%97%B6%E9%97%B4%E4%B8%BA700ms%29t_impulse_plus_nrf52840_bootloader-0.9.2-dirty_s140_6.1.1_202508251059.hex",
        "artifact_sha256": "b370eea5b238fecf4dd162a190a35e18129a3b23adbf27450ab1147c5d3b5572",
        "image_sha256": "3bd7d638548bfc08d20898521aa0c2647ecb226d07296ad4c6b63bb7914aa6aa",
        "profiles": ["lilygo_t_impulse_plus"], "hardware": ["LilyGo_T_Impulse_Plus"],
        "app_base": 155648, "softdevice_fwid": 182
    },
    {
        "name": "meshtracker_x1_factory_bootloader.zip", "version": "0.10.0-18-gb93789f",
        "source_url": "https://files.seeedstudio.com/wiki/SenseCAP/Meshcore/MeshTrackerX1/Bootloader.zip",
        "artifact_sha256": "69c246a8f29bc2ce7d1448581076be97a7232e8b548279ee6e1ec3a0d77f9e0c",
        "image_sha256": "082eb4cd2cd3db971b9bf4b659c220473cdf13b9400de6fac84b0b1fd62dc005",
        "profiles": ["meshtracker_x1"], "hardware": ["MeshTracker_X1"],
        "app_base": 159744, "softdevice_fwid": 291
    },
    {
        "name": "nano_g2_nrf52840_bootloader-0.6.4_s140_6.1.1.hex.zip",
        "version": "0.6.4-dirty",
        "source_url": "https://wiki.bqvoy.com/meshtastic/nano_g2_ultra/nano_g2_nrf52840_bootloader-0.6.4_s140_6.1.1.hex.zip",
        "artifact_sha256": "8f10154689f8e922062ace1fe2177513ca846c166b9a3eaa48e76387dc459f5d",
        "image_sha256": "c0607db64762d2b3203fcb13f3abed9adde1ec0f6ce2f5f204291f940511c5a5",
        "profiles": ["nano_g2_ultra"], "hardware": ["Nano_G2_Ultra"],
        "app_base": 155648, "softdevice_fwid": 182
    },
    {
        "name": "t-echo_lite_nrf52840_bootloader-0.9.2-dirty_s140_6.1.1_202411271344.hex",
        "version": "0.9.2-dirty",
        "source_url": "https://raw.githubusercontent.com/Xinyuan-LilyGO/T-Echo-Lite/main/bootloader/t-echo_lite_nrf52840_bootloader-0.9.2-dirty_s140_6.1.1_202411271344.hex",
        "artifact_sha256": "278a059c0288d919a166373da6241f36a53b33518c915f380878daee7b0a09e8",
        "image_sha256": "911bd9a317c3b69c8db1406dae2857410d8e608b256a9c06b5d351d90da63995",
        "profiles": [
            "lilygo_techo_lite"
        ],
        "hardware": [
            "LilyGo_T-Echo-Lite",
            "LilyGo_T-Echo-Lite_non_shell"
        ],
        "app_base": 155648,
        "softdevice_fwid": 182
    },
    {
        "name": "wiscore_rak4631_board_bootloader-0.4.3.hex",
        "version": "0.4.3",
        "source_url": "https://raw.githubusercontent.com/RAKWireless/WisBlock/master/bootloader/RAK4630/Latest/WisCore_RAK4631_Bootloader/wiscore_rak4631_board_bootloader-0.4.3.hex",
        "artifact_sha256": "e537340f695523b80c36c3cba6b74ae9b25c2d5047cf195eea19535b7171c356",
        "image_sha256": "6cbfa569a05cbd232dabe4a654abe9c04b7a03ab457615028d28f2d1329e58d2",
        "profiles": [
            "wiscore_rak4631_auto",
            "wiscore_rak3401_auto"
        ],
        "hardware": [
            "RAK_4631",
            "RAK_3401"
        ],
        "app_base": 155648,
        "softdevice_fwid": 182
    },
    {
        "name": "wiscore_rak4631_board_bootloader-0.4.4.hex",
        "version": "0.4.4",
        "source_url": "https://raw.githubusercontent.com/RAKWireless/WisBlock/master/bootloader/RAK4630/Latest/wiscore_rak4631_board_bootloader-0.4.4.hex",
        "artifact_sha256": "29635d90eccd181ce206bfa10aa0ca92b9cff61735a6205321efe241e0f1298a",
        "image_sha256": "34d59ed3c584b973ced76917cc9c7a7bc979f0b71865055729dc9a8326dc326b",
        "profiles": [
            "wiscore_rak4631_auto",
            "wiscore_rak3401_auto"
        ],
        "hardware": [
            "RAK_4631",
            "RAK_3401"
        ],
        "app_base": 155648,
        "softdevice_fwid": 182
    },
    {
        "name": "RAK4630_bootloader.hex",
        "version": "0.3.2-109-gd6b28e6",
        "source_url": "https://raw.githubusercontent.com/RAKWireless/WisBlock/master/bootloader/RAK4630/Old/V0.3.2/RAK4630_bootloader.hex",
        "artifact_sha256": "5c44ea2eb151c20c2cb5a63ca3ff4c4d10ae11b29f97812fb7a21167de7acb44",
        "image_sha256": "f345e1b2b85c88c32b3b1ad95d3330fd1d9bcf2ee0df25289c3e2f0d5fb3228d",
        "profiles": [
            "wiscore_rak4631_auto",
            "wiscore_rak3401_auto"
        ],
        "hardware": [
            "RAK_4631",
            "RAK_3401"
        ],
        "app_base": 155648,
        "softdevice_fwid": 182
    },
    {
        "name": "RAK4630_bootloader-0.4.1.hex",
        "version": "0.4.1-13-g5e6690e-dirty",
        "source_url": "https://raw.githubusercontent.com/RAKWireless/WisBlock/master/bootloader/RAK4630/Old/V0.4.1/RAK4630_bootloader-0.4.1.hex",
        "artifact_sha256": "3f317b2d05c8c65a98a5de306c7055078426ed5398dabb99ed1d2af93c270579",
        "image_sha256": "15003850514750f2606c9edb91ed81bf636d75a47d0961f3a8644847be2a9ef7",
        "profiles": [
            "wiscore_rak4631_auto",
            "wiscore_rak3401_auto"
        ],
        "hardware": [
            "RAK_4631",
            "RAK_3401"
        ],
        "app_base": 155648,
        "softdevice_fwid": 182
    },
    {
        "name": "WisCore_RAK4631_Board_Bootloader.hex",
        "version": "0.4.2",
        "source_url": "https://raw.githubusercontent.com/RAKWireless/WisBlock/master/bootloader/RAK4630/Old/V0.4.2/WisCore_RAK4631_Board_Bootloader.hex",
        "artifact_sha256": "44013dd02aa5335cbeb85a6b838408dc34b267e8d732965ab4c62730fa81b9a1",
        "image_sha256": "c77b8a4de1c33bedcb96d28cfa68d5a1c510e7cd886cfc62e6d07fbf2126ccea",
        "profiles": [
            "wiscore_rak4631_auto",
            "wiscore_rak3401_auto"
        ],
        "hardware": [
            "RAK_4631",
            "RAK_3401"
        ],
        "app_base": 155648,
        "softdevice_fwid": 182
    },
    {
        "name": "lilygo_techo_bootloader-0.6.1_s140_6.1.1.hex",
        "version": "0.6.1-2-g1224915",
        "source_url": "https://raw.githubusercontent.com/Xinyuan-LilyGO/T-Echo/main/bootloader/lilygo_techo_bootloader-0.6.1_s140_6.1.1.hex",
        "artifact_sha256": "0f19e2a83ab76ead26e729f9c6ee5e5db6b8c6e45e6ed47b01d648d531f2418b",
        "image_sha256": "f5f3a732241590d49a355b78d12aee4952115d773796f8e40e86ff1cf0fbcc50",
        "profiles": [
            "lilygo_techo"
        ],
        "hardware": [
            "LilyGo_T-Echo"
        ],
        "app_base": 155648,
        "softdevice_fwid": 182
    },
    {
        "name": "Seeed_XIAO_nRF52840_bootloader-0.6.2_s140_7.3.0.hex",
        "version": "0.6.1",
        "source_url": "https://raw.githubusercontent.com/Seeed-Studio/Adafruit_nRF52_Arduino/master/bootloader/Seeed_XIAO_nRF52840/Seeed_XIAO_nRF52840_bootloader-0.6.2_s140_7.3.0.hex",
        "artifact_sha256": "c79c8cf75ebb7abfa53b02fd3584aa9a5aeb8dc1b073f674f4fa162225630c6c",
        "image_sha256": "77941feb9ea9d99acaeb1f815a905e23f618381390d40f38374a894505e6798f",
        "profiles": [
            "xiao_nrf52840_ble"
        ],
        "hardware": [
            "Xiao_nrf52"
        ],
        "app_base": 159744,
        "softdevice_fwid": 291
    },
    {
        "name": "Seeed_XIAO_nRF52840_Plus_bootloader-0.6.2_s140_7.3.0.hex",
        "version": "0.9.2-29-g6a9a6a3",
        "source_url": "https://raw.githubusercontent.com/Seeed-Studio/Adafruit_nRF52_Arduino/master/bootloader/Seeed_XIAO_nRF52840_Plus/Seeed_XIAO_nRF52840_Plus_bootloader-0.6.2_s140_7.3.0.hex",
        "artifact_sha256": "6bd76f49e1dfa839d0084b9af5563115df2ee438f1980327dd4364792475048b",
        "image_sha256": "caf05e859193001ef745d21ae71b218f2f23a0ed995a4adc69debcdb31e35ba8",
        "profiles": [
            "xiao_nrf52840_ble"
        ],
        "hardware": [
            "Xiao_nrf52"
        ],
        "app_base": 159744,
        "softdevice_fwid": 291
    },
    {
        "name": "Seeed_XIAO_nRF52840_Sense_bootloader-0.6.2_s140_7.3.0.hex",
        "version": "0.6.1",
        "source_url": "https://raw.githubusercontent.com/Seeed-Studio/Adafruit_nRF52_Arduino/master/bootloader/Seeed_XIAO_nRF52840_Sense/Seeed_XIAO_nRF52840_Sense_bootloader-0.6.2_s140_7.3.0.hex",
        "artifact_sha256": "ac654c6cab225a933278c8be09b92b41bc4c044d064bd8c9c3314c1c4a0cc8c8",
        "image_sha256": "894f2e6df7663fcc5c398e210db7f2e7f21d3da53036e0a11576909c849f78fb",
        "profiles": [
            "xiao_nrf52840_ble_sense"
        ],
        "hardware": [
            "Xiao_nrf52_Sense"
        ],
        "app_base": 159744,
        "softdevice_fwid": 291
    },
    {
        "name": "Seeed_XIAO_nRF52840_Sense_Plus_bootloader-0.6.2_s140_7.3.0.hex",
        "version": "0.9.2-29-g6a9a6a3",
        "source_url": "https://raw.githubusercontent.com/Seeed-Studio/Adafruit_nRF52_Arduino/master/bootloader/Seeed_XIAO_nRF52840_Sense_Plus/Seeed_XIAO_nRF52840_Sense_Plus_bootloader-0.6.2_s140_7.3.0.hex",
        "artifact_sha256": "b401776e6586f6065c46187d190dfa4acf9f7897a7d7c5f12a34662dff405e64",
        "image_sha256": "b19bd74a743a8311b12a07ae5cc17f28966d6e61b66f93f2afb2ceb52c5240d2",
        "profiles": [
            "xiao_nrf52840_ble_sense"
        ],
        "hardware": [
            "Xiao_nrf52_Sense"
        ],
        "app_base": 159744,
        "softdevice_fwid": 291
    },
    {
        "name": "wio_tracker_l1_bootloader-0.10.0_s140_7.3.0.zip",
        "version": "0.7.0-22-g277a0c8",
        "source_url": "https://files.seeedstudio.com/wiki/SenseCAP/Meshtastic/adafruit/wio_tracker_l1_bootloader-0.10.0_s140_7.3.0.zip",
        "artifact_sha256": "6d8fab06f8fd146653b93196ebb86203e336d74bb7f06d1dabc89f31d23783d1",
        "image_sha256": "4e9c4b9a9d610ccb7c7e9f16757eb51bedeeb2b30b438d5c1ad07a852f2a24b4",
        "profiles": [
            "wio_tracker_l1"
        ],
        "hardware": [
            "WioTrackerL1"
        ],
        "app_base": 159744,
        "softdevice_fwid": 291
    },
    {
        "name": "t1000_e_bootloader-0.9.1-5-g488711a_s140_7.3.0.zip",
        "version": "0.9.1-5-g488711a",
        "source_url": "https://files.seeedstudio.com/wiki/SenseCAP/lorahub/t1000_e_bootloader-0.9.1-5-g488711a_s140_7.3.0.zip",
        "artifact_sha256": "87c4ba0721be16cca9b784ccb70ea41103100767978431ba47c692d2644bdccc",
        "image_sha256": "9f286a5c8c634a65e4f6d5b63c35eeb1466f900de9abb58ae109e67a82092562",
        "profiles": [
            "t1000_e"
        ],
        "hardware": [
            "t1000e"
        ],
        "app_base": 159744,
        "softdevice_fwid": 291
    },
    {
        "name": "r1_neo_board_bootloader-0.9.2-31-g990aa7f-dirty_s140_6.1.1.hex",
        "version": "0.9.2-31-g990aa7f-dirty",
        "source_url": "https://github.com/muzi-works/Adafruit_nRF52_Bootloader/releases/download/0.9.2/r1_neo_board_bootloader-0.9.2-31-g990aa7f-dirty_s140_6.1.1.hex",
        "artifact_sha256": "54f77f1e2c6cf5675aec9730cff27087f3f838d7f7075dba5a266e6e0ac355ff",
        "image_sha256": "8bfe28094a824f08bf703bf3ccc929e0b21b0b37f94ccee3fbd89413b4eedfeb",
        "profiles": [
            "wiscore_rak4631_auto"
        ],
        "hardware": [
            "R1Neo"
        ],
        "app_base": 155648,
        "softdevice_fwid": 182
    },
    {
        "name": "HT-n5262-bootloader-20240709.hex",
        "version": "0.9.0-2-g836c8dc-dirty",
        "source_url": "https://resource.heltec.cn/download/Mesh_Node_T114/firmware/HT-n5262-bootloader-20240709.hex",
        "artifact_sha256": "e950057bebc4beddafdf30955b2d473c4cc26ed008858cf114c613ba0c6c8f88",
        "image_sha256": "906fbbe787e18b789a695ae8bec6a5c31320f6948a656344c30bde0eaca1afbd",
        "profiles": [
            "heltec_t114"
        ],
        "hardware": [
            "Heltec_t114",
            "Heltec_t114_without_display"
        ],
        "app_base": 155648,
        "softdevice_fwid": 182
    }
]


def legacy_bootloaders_for(profile_id, source_hardware, app_base, softdevice_fwid):
    """Return fresh provenance records for this exact build and running layout."""
    if _PHYSICAL_PROFILES.get(source_hardware) != profile_id:
        return []
    result = []
    seen = set()
    for record in _RECORDS + reviewed_fork_bootloaders():
        if (profile_id not in record["profiles"]
                or source_hardware not in record["hardware"]
                or app_base != record["app_base"]
                or softdevice_fwid != record["softdevice_fwid"]):
            continue
        digest = record["image_sha256"]
        if (not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not re.fullmatch(r"[0-9a-f]{64}", record["artifact_sha256"])
                or not record["source_url"].startswith("https://")
                or digest in seen):
            raise ValueError("invalid or duplicate reviewed stock bootloader")
        seen.add(digest)
        if digest in _UNQUALIFIED_XIAO_PLUS_IMAGES:
            continue
        result.append({**record, "profiles": list(record["profiles"]),
                       "hardware": list(record["hardware"])})
    if len(result) > 32:
        raise ValueError("reviewed bootloader inventory exceeds the runtime limit")
    return result
