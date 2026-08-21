#!/usr/bin/env python3
"""
Tests for motalib - run with the meshcore venv:

    ./meshcore/bin/python tools/mota/test_mota.py

(Also pytest-compatible: functions are named test_*.)
"""

from __future__ import annotations

import io
import os
import random
import struct

import motalib as ml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _fw(seed, size):
    random.seed(seed)
    return bytes(random.getrandbits(8) for _ in range(size))


# --- multihash / version ---------------------------------------------------

def test_version_pack_roundtrip():
    assert ml.pack_version("1.16.0") == (1 << 24) | (16 << 16)
    assert ml.unpack_version(ml.pack_version("1.16.0.2")) == "1.16.0.2"
    assert ml.pack_version(0x01100000) == 0x01100000


def test_target_id_for_env():
    import hashlib
    env = "RAK_4631_companion_radio_usb"
    expect = int.from_bytes(hashlib.sha256(env.encode()).digest()[:4], "little")
    assert ml.target_id_for_env(env) == expect
    # distinct envs (same board, different role) get distinct ids
    assert ml.target_id_for_env("RAK_4631_repeater") != ml.target_id_for_env("RAK_4631_companion_radio_usb")


def test_ota_target_generation_honors_explicit_disable():
    from gen_targets import ota_envs

    cfg = [
        ["env:enabled", [["build_flags", ["-D ENABLE_OTA=1"]]]],
        ["env:disabled", [["build_flags", ["-D ENABLE_OTA=1", "-D DISABLE_LORA_OTA=1"]]]],
        ["env:unrelated", [["build_flags", ["-D NRF52_PLATFORM"]]]],
    ]
    assert ota_envs(cfg) == ["enabled"]


def test_hardware_id_for_env():
    assert ml.hardware_id_for_env("RAK_4631_repeater") == "RAK4631"
    assert ml.hardware_id_for_env("RAK_4631_companion_radio_usb") == "RAK4631"
    assert (
        ml.hardware_id_for_env("RAK_4631_repeater_rak15001_slot_c_lora_ota")
        == "RAK4631_RAK15001_C"
    )
    assert (
        ml.hardware_id_for_env("RAK_3401_repeater_rak15001_slot_c_lora_ota")
        == "RAK_3401"
    )
    assert (
        ml.hardware_id_for_env("Heltec_t114_without_display_repeater")
        == "Heltec_t114"
    )
    assert ml.hardware_id_for_env("ThinkNode_M2_Repeater_bridge_espnow") == "ThinkNode_M2"
    assert ml.hardware_id_for_env("wio-e5-repeater_bridge_rs232") == "wio-e5"
    long_env = "ikoka_handheld_nrf_e22_30dbm_096_rotated_room_server"
    tag = ml.hardware_id_for_env(long_env)
    assert len(tag) <= 32 and tag.startswith("ikoka_handheld_nrf_e22")
    assert tag == ml.hardware_id_for_env(long_env.replace("room_server", "companion_radio_usb"))
    assert tag != ml.hardware_id_for_env("ikoka_handheld_nrf_e22_22dbm_096_rotated_room_server")


# --- EndF ------------------------------------------------------------------

def test_endf_roundtrip_and_idempotent():
    body = _fw(1, 5000)
    img, h8 = ml.ensure_endf(body)
    assert len(img) == 5000 + ml.ENDF_LEN
    assert ml.has_endf(img)
    pbody, ph8 = ml.parse_endf(img)
    assert pbody == body and ph8 == h8 == ml.mh8(body)
    # idempotent: feeding an already-EndF'd image returns it unchanged
    img2, h82 = ml.ensure_endf(img)
    assert img2 == img and h82 == h8


def test_endf_rejects_garbage_tail():
    assert not ml.has_endf(b"too short")
    body = _fw(2, 1000)
    img = body + ml.ENDF_MAGIC + struct.pack("<I", 999) + ml.mh8(body)  # wrong body_len
    assert not ml.has_endf(img)


def test_endf_identity():
    body = _fw(3, 4096)
    ident = ml.FwIdent(fw_version=ml.pack_version("1.16.0"),
                       target_id=ml.target_id_for_env("RAK_4631_repeater"), hw_id="RAK4631")
    img, h8 = ml.ensure_endf(body, ident)
    assert len(img) == len(body) + ml.ENDF_LEN              # fixed 56-byte trailer
    assert ml.parse_endf(img) == (body, h8)                 # body + body_hash parse
    assert h8 == ml.mh8(body)                               # body_hash is over BODY only
    gi = ml.parse_endf_ident(img)
    assert gi is not None and gi.hw_id == "RAK4631"
    assert gi.target_id == ml.target_id_for_env("RAK_4631_repeater")
    assert gi.fw_version == ml.pack_version("1.16.0")
    # no identity supplied -> zero-filled (still fixed size, still self-consistent)
    z, _ = ml.ensure_endf(body)
    assert len(z) == len(body) + ml.ENDF_LEN and ml.parse_endf_ident(z) == ml.FwIdent(0, 0, "")


def test_nrf52_layout_record_roundtrip_and_policy():
    assert (ml.nrf52_stage_ceiling_for_layout(ml.NRF52_APP_END, True)
            == ml.NRF52_EXTRAFS_START)
    assert ml.nrf52_stage_ceiling_for_layout(ml.NRF52_APP_END, False) == ml.NRF52_APP_END
    assert (ml.nrf52_stage_ceiling_for_layout(ml.NRF52_EXTRAFS_START, True)
            == ml.NRF52_EXTRAFS_START)
    assert (ml.nrf52_stage_ceiling_for_layout(ml.NRF52_EXTRAFS_START, False)
            == ml.NRF52_APP_END)
    assert (ml.nrf52_stage_ceiling_for_layout(ml.NRF52_BOOT_SCRATCH_START, False)
            == ml.NRF52_APP_END)

    layout = ml.Nrf52Layout(ml.NRF52_APP_BASE_S140_V7, ml.NRF52_EXTRAFS_START,
                            ml.NRF52_APP_END, 0)
    body = ml.ensure_nrf52_layout(_fw(4, 2048), layout)
    image, _ = ml.ensure_endf(body, ml.FwIdent(hw_id="Xiao_nrf52"))
    assert ml.parse_nrf52_layout(image) == layout
    # Re-running the record step replaces the tail instead of duplicating it.
    assert ml.ensure_nrf52_layout(body, layout) == body
    assert ml.parse_nrf52_layout(ml.ensure_endf(_fw(5, 2048))[0]) is None
    internal = ml.Nrf52Layout(
        ml.NRF52_APP_BASE_S140_V7, ml.NRF52_EXTRAFS_START,
        ml.NRF52_EXTRAFS_START, ml.NRF52_LAYOUT_FLAG_INTERNAL_EXTRAFS)
    internal_image, _ = ml.ensure_endf(ml.ensure_nrf52_layout(_fw(6, 2048), internal))
    assert ml.parse_nrf52_layout(internal_image) == internal
    qspi = ml.Nrf52Layout(
        ml.NRF52_APP_BASE_S140_V7, ml.NRF52_APP_END,
        ml.NRF52_APP_END, ml.NRF52_LAYOUT_FLAG_QSPI)
    qspi_image, _ = ml.ensure_endf(ml.ensure_nrf52_layout(_fw(7, 2048), qspi))
    assert ml.parse_nrf52_layout(qspi_image) == qspi
    assert qspi.qspi_backed and qspi.external_backed and not qspi.sd_backed
    boot_qspi = ml.Nrf52Layout(
        ml.NRF52_APP_BASE_S140_V7, ml.NRF52_BOOT_SCRATCH_START,
        ml.NRF52_APP_END,
        ml.NRF52_LAYOUT_FLAG_QSPI | ml.NRF52_LAYOUT_FLAG_BOOTLOADER_SCRATCH)
    boot_qspi_image, _ = ml.ensure_endf(ml.ensure_nrf52_layout(_fw(8, 2048), boot_qspi))
    assert ml.parse_nrf52_layout(boot_qspi_image) == boot_qspi
    assert boot_qspi.bootloader_scratch
    try:
        ml.build_nrf52_layout(ml.Nrf52Layout(
            ml.NRF52_APP_BASE_S140_V7, ml.NRF52_EXTRAFS_START,
            ml.NRF52_EXTRAFS_START, 0))
        assert False, "inconsistent layout record accepted"
    except ValueError:
        pass
    for flags in (
        ml.NRF52_LAYOUT_FLAG_SD | ml.NRF52_LAYOUT_FLAG_QSPI,
        ml.NRF52_LAYOUT_FLAG_QSPI | ml.NRF52_LAYOUT_FLAG_INTERNAL_EXTRAFS,
    ):
        try:
            ml.build_nrf52_layout(ml.Nrf52Layout(
                ml.NRF52_APP_BASE_S140_V7, ml.NRF52_APP_END,
                ml.NRF52_APP_END, flags))
            assert False, "conflicting nRF52 layout flags accepted"
        except ValueError:
            pass


def _xiao_bootloader_image(board_id=ml.XIAO_BOOT_BOARD_ID_BASE):
    import zlib
    image = bytearray(b"\xff" * ml.XIAO_BOOT_IMAGE_SIZE)
    struct.pack_into("<II", image, 0, 0x20040000, ml.XIAO_BOOT_IMAGE_START + 0x101)
    struct.pack_into("<8sHHB3x", image, 0x80, ml.XIAO_BOOT_CAPS_MAGIC,
                     ml.BOOT_FORMAT_VER, 1 << ml.CODEC_FULL,
                     ml.XIAO_BOOT_STORAGE_QSPI | ml.XIAO_BOOT_STORAGE_UPDATE)
    name = ml.XIAO_BOOT_DEVICE_NAME
    struct.pack_into("<8sHHIII16sI", image, 0x100, ml.XIAO_BOOT_MANIFEST_MAGIC,
                     ml.XIAO_BOOT_MANIFEST_VERSION, ml.XIAO_BOOT_MANIFEST_SIZE,
                     ml.XIAO_BOOT_IMAGE_START, ml.XIAO_BOOT_IMAGE_SIZE, board_id, name, 0)
    crc = zlib.crc32(image) & 0xFFFFFFFF
    struct.pack_into("<I", image, 0x100 + 40, crc)
    return bytes(image)


def _xiao_manifest_crc(image, offset):
    import zlib
    trial = bytearray(image)
    trial[offset + 40:offset + 44] = b"\0" * 4
    return zlib.crc32(trial) & 0xFFFFFFFF


def _set_two_valid_xiao_manifest_crcs(image, first, second):
    """Solve the two coupled whole-image CRC fields over GF(2)."""
    first_crc, second_crc = first + 40, second + 40
    image[first_crc:first_crc + 4] = b"\0" * 4
    image[second_crc:second_crc + 4] = b"\0" * 4

    def residual(value):
        trial = bytearray(image)
        struct.pack_into("<I", trial, first_crc, value & 0xFFFFFFFF)
        struct.pack_into("<I", trial, second_crc, value >> 32)
        first_error = struct.unpack_from("<I", trial, first_crc)[0] ^ _xiao_manifest_crc(trial, first)
        second_error = struct.unpack_from("<I", trial, second_crc)[0] ^ _xiao_manifest_crc(trial, second)
        return first_error | (second_error << 32)

    affine = residual(0)
    columns = [residual(1 << bit) ^ affine for bit in range(64)]
    rows = []
    for output_bit in range(64):
        row = sum(1 << input_bit for input_bit, column in enumerate(columns)
                  if (column >> output_bit) & 1)
        rows.append(row | (((affine >> output_bit) & 1) << 64))

    pivot_columns = []
    pivot_row = 0
    for column in range(64):
        found = next((row for row in range(pivot_row, 64)
                      if (rows[row] >> column) & 1), None)
        if found is None:
            continue
        rows[pivot_row], rows[found] = rows[found], rows[pivot_row]
        for row in range(64):
            if row != pivot_row and (rows[row] >> column) & 1:
                rows[row] ^= rows[pivot_row]
        pivot_columns.append(column)
        pivot_row += 1

    assert all((row & ((1 << 64) - 1)) or not ((row >> 64) & 1) for row in rows)
    solution = 0
    for row, column in enumerate(pivot_columns):
        if (rows[row] >> 64) & 1:
            solution |= 1 << column
    assert residual(solution) == 0
    struct.pack_into("<I", image, first_crc, solution & 0xFFFFFFFF)
    struct.pack_into("<I", image, second_crc, solution >> 32)


def test_xiao_bootloader_identity_skips_bad_decoy_and_rejects_two_valid_manifests():
    real_offset, decoy_offset, second_offset = 0x100, 0x20, 0x200
    image = bytearray(_xiao_bootloader_image())
    image[decoy_offset:decoy_offset + ml.XIAO_BOOT_MANIFEST_SIZE] = \
        image[real_offset:real_offset + ml.XIAO_BOOT_MANIFEST_SIZE]
    struct.pack_into("<I", image, decoy_offset + 40, 0xA5A5A5A5)
    struct.pack_into("<I", image, real_offset + 40, _xiao_manifest_crc(image, real_offset))
    assert _xiao_manifest_crc(image, decoy_offset) != 0xA5A5A5A5
    identity = ml.parse_xiao_bootloader_identity(bytes(image))
    assert identity is not None and identity.manifest_offset == real_offset

    duplicate = bytearray(_xiao_bootloader_image())
    duplicate[second_offset:second_offset + ml.XIAO_BOOT_MANIFEST_SIZE] = \
        duplicate[real_offset:real_offset + ml.XIAO_BOOT_MANIFEST_SIZE]
    _set_two_valid_xiao_manifest_crcs(duplicate, real_offset, second_offset)
    assert struct.unpack_from("<I", duplicate, real_offset + 40)[0] == \
        _xiao_manifest_crc(duplicate, real_offset)
    assert struct.unpack_from("<I", duplicate, second_offset + 40)[0] == \
        _xiao_manifest_crc(duplicate, second_offset)
    assert ml.parse_xiao_bootloader_identity(bytes(duplicate)) is None
    try:
        ml.validate_xiao_bootloader_image(bytes(duplicate))
        assert False, "two CRC-valid embedded identities accepted"
    except ValueError:
        pass


def test_xiao_bootloader_caps_rejects_malformed_or_unaligned_markers():
    def caps(*, offset=0, abi=ml.BOOT_FORMAT_VER,
             codecs=1 << ml.CODEC_FULL,
             storage=ml.XIAO_BOOT_STORAGE_QSPI | ml.XIAO_BOOT_STORAGE_UPDATE,
             reserved=b"\0\0\0"):
        image = bytearray(b"\xff" * 64)
        struct.pack_into("<8sHHB3s", image, offset, ml.XIAO_BOOT_CAPS_MAGIC,
                         abi, codecs, storage, reserved)
        return bytes(image)

    assert ml.xiao_bootloader_caps_ok(caps())
    assert not ml.xiao_bootloader_caps_ok(caps(offset=1))
    assert not ml.xiao_bootloader_caps_ok(caps(abi=0xFFFF))
    assert not ml.xiao_bootloader_caps_ok(caps(codecs=0))
    assert not ml.xiao_bootloader_caps_ok(caps(storage=0x1C))
    assert not ml.xiao_bootloader_caps_ok(caps(reserved=b"\0\x01\0"))


def test_bootloader_v3_build_parse_and_strict_contract():
    priv = Ed25519PrivateKey.generate()
    image = _xiao_bootloader_image()
    identity = ml.validate_xiao_bootloader_image(image, ml.XIAO_BOOT_BOARD_ID_BASE)
    assert identity.board_id == ml.XIAO_BOOT_BOARD_ID_BASE
    m = ml.build_manifest(
        target_id=identity.board_id, fw_version=ml.pack_version("1.0.0"),
        image_size=len(image), payload=image, block_size=1024,
        image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL, is_full=True,
        sign_priv=priv, bootloader=True)
    blob = ml.build_container(m, image)
    parsed = ml.parse_container(blob)
    assert parsed.manifest.format_ver == ml.BOOT_FORMAT_VER
    assert parsed.manifest.flags == ml.FLAG_FULL | ml.FLAG_SIGNED | ml.FLAG_BOOTLOADER
    assert parsed.manifest.hw_id == ml.hw_id_bytes("XIAO_BL_28860044")
    assert ml.verify(parsed, expect_pub=priv.public_key().public_bytes_raw()) == []

    invalid_payload = bytearray(parsed.payload)
    invalid_payload[4] &= 0xFE
    parsed.payload = bytes(invalid_payload)
    assert any(problem.startswith("bootloader image contract:")
               for problem in ml.verify(parsed))

    bad = bytearray(blob)
    bad[8] = ml.APP_FORMAT_VER
    try:
        ml.parse_container(bytes(bad))
        assert False, "v2 bootloader flag accepted"
    except ValueError:
        pass
    bad = bytearray(blob)
    bad[9] &= ~ml.FLAG_BOOTLOADER
    try:
        ml.parse_container(bytes(bad))
        assert False, "v3 non-bootloader flags accepted"
    except ValueError:
        pass


def test_bootloader_builder_rejects_wrong_identity_geometry_and_continuity():
    priv = Ed25519PrivateKey.generate()
    image = _xiao_bootloader_image()
    kwargs = dict(target_id=ml.XIAO_BOOT_BOARD_ID_BASE, fw_version=1,
                  image_size=len(image), payload=image, block_size=1024,
                  image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL,
                  is_full=True, sign_priv=priv, bootloader=True)
    for change in (
        {"sign_priv": None},
        {"block_size": 512},
        {"fw_version": 0},
        {"target_id": ml.XIAO_BOOT_BOARD_ID_SENSE},
        {"hw_id": "wrong"},
    ):
        try:
            ml.build_manifest(**(kwargs | change))
            assert False, f"invalid bootloader build accepted: {change}"
        except ValueError:
            pass
    damaged = bytearray(image); damaged[0x200] ^= 1
    try:
        ml.validate_xiao_bootloader_image(bytes(damaged))
        assert False, "bad embedded CRC accepted"
    except ValueError:
        pass
    import zlib
    wrong_name = bytearray(image)
    wrong_name[0x100 + 24:0x100 + 40] = b"FRIENDLY NAME".ljust(16, b"\0")
    wrong_name[0x100 + 40:0x100 + 44] = b"\0" * 4
    struct.pack_into("<I", wrong_name, 0x100 + 40, zlib.crc32(wrong_name) & 0xFFFFFFFF)
    try:
        ml.validate_xiao_bootloader_image(bytes(wrong_name))
        assert False, "noncanonical embedded device name accepted"
    except ValueError:
        pass


# --- merkle ----------------------------------------------------------------

def test_merkle_single_block():
    leaves = [ml.mh4(b"x")]
    assert ml.merkle_root(leaves) == leaves[0]


def test_merkle_proofs_all_indices_various_counts():
    for count in [1, 2, 3, 4, 5, 7, 8, 9, 16, 17, 100]:
        payload = _fw(count, count * 1024 - 13)  # last block short, no padding
        leaves = ml.leaf_hashes(payload, 1024)
        assert len(leaves) == count
        root = ml.merkle_root(leaves)
        for i in range(count):
            proof = ml.merkle_proof(leaves, i)
            assert ml.verify_proof(leaves[i], i, proof, root, count), (count, i)
        # a tampered leaf must fail its proof
        bad = bytes([leaves[0][0] ^ 0xFF]) + leaves[0][1:]
        assert not ml.verify_proof(bad, 0, ml.merkle_proof(leaves, 0), root, count)


# --- full container --------------------------------------------------------

def test_full_build_parse_verify():
    fw = _fw(10, 33 * 1024 + 7)
    image, _ = ml.ensure_endf(fw)
    m = ml.build_manifest(
        target_id=0xDEADBEEF, fw_version=ml.pack_version("1.16.0"),
        image_size=len(image), payload=image, block_size=1024,
        image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL, is_full=True)
    blob = ml.build_container(m, image)

    parsed = ml.parse_container(blob)
    assert parsed.manifest.target_id == 0xDEADBEEF
    assert parsed.manifest.is_full and not parsed.manifest.is_signed
    assert parsed.manifest.image_hash == ml.mh32(image)
    assert parsed.payload == image
    assert ml.verify(parsed) == []


def test_hw_id_roundtrip_and_signed():
    # the v2 hw_id is a 32-byte NUL-padded ASCII tag in the SIGNED head; it must round-trip + be covered
    # by the signature (tampering it breaks verification).
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    fw = _fw(77, 4 * 1024)
    image, _ = ml.ensure_endf(fw)
    priv = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    m = ml.build_manifest(
        target_id=0xABCD, fw_version=ml.pack_version("2.0.0"),
        image_size=len(image), payload=image, block_size=1024,
        image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL, is_full=True,
        sign_priv=priv, hw_id="RAK4631")
    blob = ml.build_container(m, image)
    parsed = ml.parse_container(blob)
    assert parsed.manifest.format_ver == 2
    assert parsed.manifest.hw_id == b"RAK4631" + b"\0" * (32 - 7)
    assert parsed.manifest.hw_id.rstrip(b"\0").decode() == "RAK4631"
    assert ml.verify(parsed) == []
    # flip a byte of the on-wire hw_id -> signature must fail (it's in the signed region)
    bad = bytearray(blob)
    hw_off = 8 + 57            # MAGIC(4)+total(4) + fixed head up to codec(57) = start of hw_id
    bad[hw_off] ^= 0xFF
    assert ml.verify(ml.parse_container(bytes(bad))) != []


def test_tampered_payload_detected():
    fw = _fw(11, 10 * 1024)
    image, _ = ml.ensure_endf(fw)
    m = ml.build_manifest(target_id=1, fw_version=1, image_size=len(image), payload=image,
                          block_size=1024, image_hash=ml.mh32(image),
                          codec_id=ml.CODEC_FULL, is_full=True)
    blob = bytearray(ml.build_container(m, image))
    # flip a byte inside the payload region
    payload_off = blob.index(image)
    blob[payload_off + 50] ^= 0xFF
    problems = ml.verify(ml.parse_container(bytes(blob)))
    assert any("leaves" in p or "merkle" in p or "image_hash" in p for p in problems), problems


# --- signing ---------------------------------------------------------------

def test_signed_build_and_verify():
    priv = Ed25519PrivateKey.generate()
    fw = _fw(12, 20 * 1024)
    image, _ = ml.ensure_endf(fw)
    m = ml.build_manifest(target_id=7, fw_version=ml.pack_version("2.0.0"),
                          image_size=len(image), payload=image, block_size=1024,
                          image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL,
                          is_full=True, sign_priv=priv)
    parsed = ml.parse_container(ml.build_container(m, image))
    assert parsed.manifest.is_signed
    assert ml.verify(parsed, expect_pub=priv.public_key().public_bytes_raw()) == []
    # wrong expected key -> flagged
    other = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    assert any("signer_pubkey" in p for p in ml.verify(parsed, expect_pub=other))


def test_tampered_signature_detected():
    priv = Ed25519PrivateKey.generate()
    fw = _fw(13, 8 * 1024)
    image, _ = ml.ensure_endf(fw)
    m = ml.build_manifest(target_id=7, fw_version=1, image_size=len(image), payload=image,
                          block_size=1024, image_hash=ml.mh32(image),
                          codec_id=ml.CODEC_FULL, is_full=True, sign_priv=priv)
    blob = bytearray(ml.build_container(m, image))
    # flip a byte of target_id (inside signed region) without re-signing
    blob[10] ^= 0xFF
    problems = ml.verify(ml.parse_container(bytes(blob)))
    assert any("signature INVALID" in p for p in problems), problems


# --- approval enforcement --------------------------------------------------

def test_approval_default_and_flagged_if_preapproved():
    fw = _fw(14, 4 * 1024)
    image, _ = ml.ensure_endf(fw)
    m = ml.build_manifest(target_id=1, fw_version=1, image_size=len(image), payload=image,
                          block_size=1024, image_hash=ml.mh32(image),
                          codec_id=ml.CODEC_FULL, is_full=True)
    assert m.approval == ml.APPROVAL_NOT
    # simulate a malicious pre-approved container -> verify must flag it
    m.approval = ml.APPROVAL_YES
    parsed = ml.parse_container(ml.build_container(m, image))
    assert any("approval" in p for p in ml.verify(parsed))


# --- delta -----------------------------------------------------------------

def test_delta_build_apply_verify():
    old_body = _fw(20, 40 * 1024)
    # new = old with a chunk changed + appended -> a real, small-ish delta
    new_body = bytearray(old_body)
    for i in range(1000, 1500):
        new_body[i] = (new_body[i] + 1) & 0xFF
    new_body += _fw(21, 2048)
    old_image, base_hash = ml.ensure_endf(bytes(old_body))
    new_image, _ = ml.ensure_endf(bytes(new_body))

    import detools
    fp = io.BytesIO()
    detools.create_patch(io.BytesIO(old_image), io.BytesIO(new_image), fp,
                         patch_type="sequential", compression="crle")
    delta = fp.getvalue()
    # with compression a near-identical-base delta is a fraction of the full image
    assert len(delta) < len(new_image) // 2, (len(delta), len(new_image))

    m = ml.build_manifest(target_id=0xABCD, fw_version=ml.pack_version("1.2.0"),
                          image_size=len(new_image), payload=delta, block_size=1024,
                          image_hash=ml.mh32(new_image), codec_id=ml.CODEC_DETOOLS_SEQUENTIAL,
                          is_full=False, base_hash=base_hash)
    parsed = ml.parse_container(ml.build_container(m, delta))
    assert parsed.manifest.base_hash == base_hash == ml.mh8(bytes(old_body))
    # full verify incl. applying the delta to the base and checking image_hash
    assert ml.verify(parsed, base_image=old_image) == []
    # wrong base must fail the delta->image_hash check
    wrong = ml.verify(parsed, base_image=_fw(99, 40 * 1024))
    assert wrong, "delta verify against a wrong base should fail"


# --- runner ----------------------------------------------------------------

def _run():
    tests = {k: v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)}
    failed = 0
    for name, fn in tests.items():
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL {name}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
