"""
motalib - build/parse/verify MeshCore ``.mota`` firmware-update containers.

Pure logic, no CLI. Implements docs/ota_protocol.md (v2 application / v3 bootloader, fixed layout).

The wire format (all integers little-endian):

    container = MAGIC(4) | MOTA_TOTAL_SIZE(4) | MANIFEST | PAYLOAD | TRAILER(5)

    manifest  = format_ver(1) flags(1) hash_algo(1) target_id(4) fw_version(4)
                image_size(4) payload_size(4) block_size_log2(1) merkle_root(4)
                image_hash(32) codec_id(1) hw_id(32)
                base_hash(8) signer_pubkey(32) signature(64) approval(4)
                leaves[](4*BC)

Fixed layout: every field is always present at a constant offset (base_hash/signer_pubkey/signature are
zero-filled for a full / unsigned container), so manifest-minus-leaves is always 197 bytes and `leaves[]`
is the only variable-length field. Hashes are SHA-256, truncated per multihash (sha2-256:N = first N bytes).
"""

from __future__ import annotations

import hashlib
import io
import re
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Reference constants (must match docs/ota_protocol.md and the device code)
# ---------------------------------------------------------------------------

MAGIC = b"mOTA"           # 6D 4F 54 41
TRAILER = b"vk496"        # 76 6B 34 39 36
ENDF_MAGIC = b"EndF"      # 45 6E 64 46
# Fixed-length trailer: marker(4) + body_len(4) + body_hash8(8) + fw_version(4) + target_id(4) + hw_id(32).
ENDF_LEN = 56

FORMAT_VER = 2          # application compatibility name
APP_FORMAT_VER = 2
BOOT_FORMAT_VER = 3
HASH_ALGO_SHA256 = 0x12   # multihash code for sha2-256

FLAG_FULL = 0x01
FLAG_SIGNED = 0x02
FLAG_BOOTLOADER = 0x04
FLAG_KNOWN = FLAG_FULL | FLAG_SIGNED | FLAG_BOOTLOADER

CODEC_FULL = 0
CODEC_DETOOLS_SEQUENTIAL = 1   # detools `sequential` patch (decoded on-device by vendored detools C)
CODEC_DETOOLS_INPLACE = 2      # detools `in-place` patch (nRF52 bootloader-handoff path; TBD)
CODEC_NAMES = {CODEC_FULL: "full", CODEC_DETOOLS_SEQUENTIAL: "detools-sequential",
               CODEC_DETOOLS_INPLACE: "detools-in-place"}

APPROVAL_NOT = b"\xff\xff\xff\xff"   # erased = not approved
APPROVAL_YES = b"APRV"               # 41 50 52 56 = approved

DEFAULT_BLOCK_SIZE = 2048
MAX_APP_BLOCK_SIZE = 2048
BOOTLOADER_BLOCK_SIZE = 1024

# Conservative fallback for firmware built before the layout record below existed. Keep in sync with
# OtaFlashLayout_nrf52.h and motatool's format.rs.
NRF52_INPLACE_MEMORY = 0x00098000

NRF52_APP_BASE_S140_V6 = 0x00026000
NRF52_APP_BASE_S140_V7 = 0x00027000
NRF52_EXTRAFS_START = 0x000D4000
NRF52_BOOT_SCRATCH_START = 0x000E0000
NRF52_BOOT_SCRATCH_END = 0x000EA000
NRF52_APP_END = 0x000ED000
NRF52_BOOT_CONTAINER_SIZE = 41330
NRF52_SHARED_BOOT_STAGE_START = 0x000E2000
NRF52_FLASH_PAGE = 4096
NRF52_HYBRID_RAM_SIZE = 65536

# A validated nRF52 firmware carries this record immediately before EndF. It lets an offline packager
# derive the actual app base and staging ceiling from the built artifact, without a board-name allowlist.
# magic(8) | version(1) | flags(1) | record_len(2) | app_base(4) | linked_app_end(4) | stage_ceiling(4)
NRF52_LAYOUT_MAGIC = b"mOTALay1"
NRF52_LAYOUT_VERSION = 1
NRF52_LAYOUT_LEN = 24
NRF52_LAYOUT_FLAG_SD = 0x01
NRF52_LAYOUT_FLAG_INTERNAL_EXTRAFS = 0x02
NRF52_LAYOUT_FLAG_QSPI = 0x04
NRF52_LAYOUT_FLAG_BOOTLOADER_SCRATCH = 0x08
NRF52_LAYOUT_FLAG_HYBRID_RAM = 0x10

XIAO_BOOT_BOARD_ID_BASE = 0x28860044
XIAO_BOOT_BOARD_ID_SENSE = 0x28860045
XIAO_BOOT_IMAGE_START = 0x000F4000
XIAO_BOOT_IMAGE_SIZE = 0x0000A000
XIAO_BOOT_MANIFEST_MAGIC = b"BLMFCRC1"
XIAO_BOOT_MANIFEST_VERSION = 1
XIAO_BOOT_MANIFEST_SIZE = 44
BOOT_CONTINUITY_MAGIC = b"BLM2SOFT"
BOOT_CONTINUITY_VERSION = 2
BOOT_CONTINUITY_SIZE = 32
BOOT_CONTINUITY_FAMILY_S140 = 140
BOOT_CONTINUITY_LAYOUT_ABI = 1
BOOT_ENVELOPE_SIZE = XIAO_BOOT_MANIFEST_SIZE + BOOT_CONTINUITY_SIZE
BOOT_CANDIDATE_MANIFEST_OFFSET = XIAO_BOOT_IMAGE_SIZE - BOOT_ENVELOPE_SIZE
XIAO_BOOT_DEVICE_NAME = b"XIAO_DFU".ljust(16, b"\0")
XIAO_BOOT_CAPS_MAGIC = b"MOTABLDR"
BOOT_STORAGE_SD = 0x01
BOOT_STORAGE_STAGE_CEILING = 0x02
XIAO_BOOT_STORAGE_QSPI = 0x04
XIAO_BOOT_STORAGE_UPDATE = 0x08
BOOT_STORAGE_KNOWN = 0x0F
BOOT_STORAGE_SD_UPDATE = BOOT_STORAGE_SD | XIAO_BOOT_STORAGE_UPDATE
BOOT_STORAGE_QSPI_UPDATE = (BOOT_STORAGE_STAGE_CEILING | XIAO_BOOT_STORAGE_QSPI |
                            XIAO_BOOT_STORAGE_UPDATE)
BOOT_STORAGE_INTERNAL_UPDATE = BOOT_STORAGE_STAGE_CEILING | XIAO_BOOT_STORAGE_UPDATE
BOOT_REQUIRED_APP_CODEC_MASK = ((1 << CODEC_FULL) |
                                (1 << CODEC_DETOOLS_INPLACE))

# Exact embedded manifests for the qualified shared-internal nRF52840
# bootloaders. Parsing/inspection remains generic, but package creation is
# deliberately limited to this collision-audited release inventory. Carrier
# aliases use the same installed bootloader identity and therefore do not add
# rows here.
INTERNAL_BOOTLOADER_IDENTITIES = (
    (0x239A0029, "GAT562_DFU"),
    (0x239A0071, "TOWER_V2_OTA"),
    (0x239A0071, "T096_DFU"),
    (0x239A0071, "T1_DFU"),
    (0x239A0071, "T114_DFU"),
    (0x239A0071, "MESH_POCKET_OTA"),
    (0x239A00B3, "KeepteenLT1_OTA"),
    (0x239A0029, "MX25_DFU"),
    (0x239A00B3, "PROM_DFU"),
    (0x28860057, "T1KE_DFU"),
    (0x239A00DA, "TNM3_DFU"),
    (0x239A0029, "3401_DFU"),
    (0x239A0029, "4631_DFU"),
    (0x239A0029, "RTAG_DFU"),
)

# MeshTower V2 has both a lean internal-store application and an exact SD-store
# application. They intentionally retain the same embedded bootloader identity
# and signed wire target; the candidate capability marker selects the matching
# storage contract and is checked again by the device before approval.
SD_BOOTLOADER_IDENTITIES = (
    (0x239A0071, "TOWER_V2_OTA"),
)

# Exact runtime continuity profiles for signed, remotely installable
# bootloaders. A CRC-valid BLM2 envelope is not sufficient for package
# creation: signing the wrong SoftDevice/application layout only produces a
# package every qualified device will refuse. XIAO/Sense, Minew MX25LE01, and
# T1000-E run S140 v7; the remaining inventory (including both Tower storage
# profiles) runs S140 v6.
BOOTLOADER_S140_V7_IDENTITIES = (
    (XIAO_BOOT_BOARD_ID_BASE, "XIAO_DFU"),
    (XIAO_BOOT_BOARD_ID_SENSE, "XIAO_DFU"),
    (0x239A0029, "MX25_DFU"),
    (0x28860057, "T1KE_DFU"),
)
BOOTLOADER_S140_V6_FWID = 0x00B6
BOOTLOADER_S140_V7_FWID = 0x0123

# MeshTower V2's SD-backed OTA target keeps the staged .mota off-chip, so the application may use the
# complete S140 v6 application region up to InternalFS instead of leaving room for internal staging.
# This is deliberately target-specific: other nRF52 OTA builds still need NRF52_INPLACE_MEMORY above.
NRF52_SD_APP_MEMORY = 0x000C7000


# ---------------------------------------------------------------------------
# Multihash helpers (sha2-256 truncations)
# ---------------------------------------------------------------------------

def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def mh4(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()[:4]


def mh8(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()[:8]


def mh32(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def ed25519_public_bytes(key) -> bytes:
    """Return a raw Ed25519 public key across supported cryptography versions."""
    public_key = key.public_key() if hasattr(key, "public_key") else key
    public_bytes_raw = getattr(public_key, "public_bytes_raw", None)
    if public_bytes_raw is not None:
        return public_bytes_raw()

    # public_bytes_raw() was added after the stable serialization API. Keep
    # the OTA builder usable on distro-provided cryptography releases too.
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)


# ---------------------------------------------------------------------------
# fw_version packing
# ---------------------------------------------------------------------------

def pack_version(s) -> int:
    """'1.16.0' or '1.16.0.2' -> uint32 (MAJOR<<24|MINOR<<16|PATCH<<8|pre). Ints pass through."""
    if isinstance(s, int):
        return s & 0xFFFFFFFF
    s = s.strip().lstrip("vV")
    parts = [int(p) for p in s.split(".")]
    parts += [0] * (4 - len(parts))
    maj, mnr, pat, pre = parts[:4]
    return ((maj & 0xFF) << 24) | ((mnr & 0xFF) << 16) | ((pat & 0xFF) << 8) | (pre & 0xFF)


def unpack_version(v: int) -> str:
    return f"{(v >> 24) & 0xFF}.{(v >> 16) & 0xFF}.{(v >> 8) & 0xFF}.{v & 0xFF}"


# ---------------------------------------------------------------------------
# target_id
# ---------------------------------------------------------------------------

def target_id_for_env(env_name: str) -> int:
    """4-byte build-target id = sha2-256:4(env_name), little-endian uint32.

    The PlatformIO env name (e.g. 'RAK_4631_companion_radio_usb') uniquely captures hardware AND
    role/partition layout. build.sh injects the same value as -D MOTA_TARGET_ID so the device's
    getOtaTargetId() matches what the packager stamps into the manifest. (Must match build.sh.)
    """
    d = hashlib.sha256(env_name.encode()).digest()[:4]
    return int.from_bytes(d, "little")


def hardware_id_for_env(env_name: str) -> str:
    """Derive a stable hardware-family tag from a PlatformIO environment name.

    Role/profile suffixes are removed so a deliberate role switch on the same physical board remains
    possible, while a cross-board install is rejected. Long family names retain a short hash suffix to
    avoid collisions inside EndF's fixed 32-byte field.
    """
    # Optional storage that requires a different bootloader/application pair is
    # a distinct hardware class even though it uses the same WisBlock Core.
    exact_storage_hardware = {
        "RAK_4631_repeater_rak15001_slot_c_lora_ota": "RAK4631_RAK15001_C",
        "RAK_4631_repeater_w25q16_lora_ota": "RAK4631_W25Q16",
        "RAK_3401_repeater_rak13302_w25q16_lora_ota": "RAK3401_RAK13302_W25Q16",
    }
    for exact_env, hardware_id in exact_storage_hardware.items():
        if env_name.casefold() == exact_env.casefold():
            return hardware_id

    role = re.search(
        r"[_-](?:repeater|repeatr|room_server|room_svr|sensor|terminal_chat|kiss_modem|"
        r"companion_radio|companion|comp_radio)(?=[_-]|$)", env_name, re.IGNORECASE)
    family = (env_name[:role.start()] if role else env_name).strip("_-") or env_name.strip("_-")
    # Match the explicit MOTA_HW_ID shared by every role in these variant
    # families. The environment spelling is not always the physical-board tag.
    family = {
        "Heltec_t114_without_display": "Heltec_t114",
        "RAK_4631": "RAK4631",
    }.get(family, family)
    if len(family) <= 32:
        return family
    suffix = hashlib.sha256(family.encode()).hexdigest()[:8]
    return f"{family[:23].rstrip('_-')}-{suffix}"


# ---------------------------------------------------------------------------
# EndF trailer
# ---------------------------------------------------------------------------

@dataclass
class FwIdent:
    """Self-describing firmware identity carried in the EndF trailer (docs/ota_protocol.md Section 2) so a node /
    the packaging tool reads it straight from the firmware instead of relying on build flags or filenames."""
    fw_version: int = 0      # packed MAJOR<<24 | MINOR<<16 | PATCH<<8 | pre
    target_id: int = 0       # sha2-256:4(pio_env) as uint32 LE - hw + role + partition (fetch routing)
    hw_id: str = ""          # readable hardware tag (brick-safety), e.g. "RAK4631"


@dataclass(frozen=True)
class Nrf52Layout:
    """Resolved flash geometry embedded immediately before an nRF52 firmware's EndF trailer."""
    app_base: int
    linked_app_end: int
    stage_ceiling: int
    flags: int = 0

    @property
    def sd_backed(self) -> bool:
        return bool(self.flags & NRF52_LAYOUT_FLAG_SD)

    @property
    def qspi_backed(self) -> bool:
        return bool(self.flags & NRF52_LAYOUT_FLAG_QSPI)

    @property
    def external_backed(self) -> bool:
        return self.sd_backed or self.qspi_backed

    @property
    def bootloader_scratch(self) -> bool:
        return bool(self.flags & NRF52_LAYOUT_FLAG_BOOTLOADER_SCRATCH)

    @property
    def hybrid_ram(self) -> bool:
        return bool(self.flags & NRF52_LAYOUT_FLAG_HYBRID_RAM)

def nrf52_stage_ceiling_for_layout(linked_app_end: int, uses_internal_extrafs: bool) -> int:
    """Select a safe staging ceiling from linker geometry and actual secondary-storage type."""
    if uses_internal_extrafs:
        return NRF52_EXTRAFS_START
    if linked_app_end in (NRF52_EXTRAFS_START, NRF52_BOOT_SCRATCH_START, NRF52_APP_END):
        return NRF52_APP_END
    return NRF52_EXTRAFS_START


def nrf52_hybrid_stage_plan(total_size: int, app_base: int, app_end: int,
                            stage_ceiling: int) -> Optional[Tuple[int, int, int]]:
    """Return the frozen (flash_start, flash_len, ram_len) split, or None.

    Keep this byte-for-byte equivalent to
    mota_nrf52_hybrid_stage_plan() in OtaFlashLayout_nrf52.h. Hybrid is an
    expanded ED000-only profile; at least one complete flash page contains the
    header, manifest, leaves, and APRV word.
    """
    if (stage_ceiling != NRF52_APP_END or
            app_base not in (NRF52_APP_BASE_S140_V6,
                             NRF52_APP_BASE_S140_V7) or
            not (app_base <= app_end <= stage_ceiling) or
            total_size < 8 + 197 + 5 or total_size > 0xFFFFFFFF):
        return None
    flash_needed = max(NRF52_FLASH_PAGE,
                       max(0, total_size - NRF52_HYBRID_RAM_SIZE))
    flash_len = ((flash_needed + NRF52_FLASH_PAGE - 1) //
                 NRF52_FLASH_PAGE) * NRF52_FLASH_PAGE
    if flash_len >= total_size or flash_len > stage_ceiling - app_end:
        return None
    ram_len = total_size - flash_len
    if not (0 < ram_len <= NRF52_HYBRID_RAM_SIZE):
        return None
    return stage_ceiling - flash_len, flash_len, ram_len


def build_nrf52_layout(layout: Nrf52Layout) -> bytes:
    if layout.app_base not in (NRF52_APP_BASE_S140_V6, NRF52_APP_BASE_S140_V7):
        raise ValueError(f"unsupported nRF52 app base 0x{layout.app_base:X}")
    if layout.linked_app_end not in (NRF52_EXTRAFS_START,
                                     NRF52_BOOT_SCRATCH_START, NRF52_APP_END):
        raise ValueError(f"unsupported nRF52 linked app end 0x{layout.linked_app_end:X}")
    if layout.stage_ceiling not in (NRF52_EXTRAFS_START, NRF52_APP_END):
        raise ValueError(f"unsupported nRF52 staging ceiling 0x{layout.stage_ceiling:X}")
    if not (layout.app_base < layout.linked_app_end <= NRF52_APP_END):
        raise ValueError("invalid nRF52 app region")
    known_flags = (NRF52_LAYOUT_FLAG_SD | NRF52_LAYOUT_FLAG_INTERNAL_EXTRAFS |
                   NRF52_LAYOUT_FLAG_QSPI | NRF52_LAYOUT_FLAG_BOOTLOADER_SCRATCH |
                   NRF52_LAYOUT_FLAG_HYBRID_RAM)
    if layout.flags & ~known_flags:
        raise ValueError(f"unsupported nRF52 layout flags 0x{layout.flags:X}")
    if layout.sd_backed and layout.qspi_backed:
        raise ValueError("nRF52 layout cannot use both SD and QSPI staging")
    if layout.external_backed and layout.flags & NRF52_LAYOUT_FLAG_INTERNAL_EXTRAFS:
        raise ValueError("nRF52 external staging cannot also reserve internal ExtraFS")
    if layout.hybrid_ram:
        if (layout.external_backed or
                layout.flags & NRF52_LAYOUT_FLAG_INTERNAL_EXTRAFS):
            raise ValueError("nRF52 hybrid RAM staging requires internal flash without ExtraFS")
        if (layout.linked_app_end != NRF52_APP_END or
                layout.stage_ceiling != NRF52_APP_END):
            raise ValueError(
                "nRF52 hybrid RAM staging requires the exact 0xED000 internal-only profile")
    if layout.bootloader_scratch:
        qspi_shape = (layout.qspi_backed and not layout.sd_backed and
                      layout.linked_app_end == NRF52_BOOT_SCRATCH_START)
        if not qspi_shape:
            raise ValueError("nRF52 bootloader scratch requires the XIAO QSPI layout")
    elif layout.linked_app_end == NRF52_BOOT_SCRATCH_START:
        raise ValueError("reserved bootloader geometry requires the bootloader-scratch flag")
    expected_ceiling = (NRF52_APP_END if layout.external_backed else
                        nrf52_stage_ceiling_for_layout(
                            layout.linked_app_end,
                            bool(layout.flags & NRF52_LAYOUT_FLAG_INTERNAL_EXTRAFS)))
    if layout.stage_ceiling != expected_ceiling:
        raise ValueError(f"nRF52 staging ceiling 0x{layout.stage_ceiling:X} is inconsistent with layout flags")
    return struct.pack("<8sBBHIII", NRF52_LAYOUT_MAGIC, NRF52_LAYOUT_VERSION, layout.flags,
                       NRF52_LAYOUT_LEN, layout.app_base, layout.linked_app_end,
                       layout.stage_ceiling)


def ensure_nrf52_layout(body: bytes, layout: Nrf52Layout) -> bytes:
    """Append or replace the fixed layout record on an EndF-free firmware body."""
    record = build_nrf52_layout(layout)
    if len(body) >= NRF52_LAYOUT_LEN and body[-NRF52_LAYOUT_LEN:-NRF52_LAYOUT_LEN + 8] == NRF52_LAYOUT_MAGIC:
        body = body[:-NRF52_LAYOUT_LEN]
    return body + record


def parse_nrf52_layout(image: bytes) -> Optional[Nrf52Layout]:
    """Read a validated layout record from an EndF-trailed firmware; old firmware returns None."""
    if not has_endf(image) or len(image) < ENDF_LEN + NRF52_LAYOUT_LEN:
        return None
    record = image[-ENDF_LEN - NRF52_LAYOUT_LEN:-ENDF_LEN]
    try:
        magic, version, flags, record_len, app_base, linked_end, ceiling = struct.unpack(
            "<8sBBHIII", record)
        if magic != NRF52_LAYOUT_MAGIC or version != NRF52_LAYOUT_VERSION or record_len != NRF52_LAYOUT_LEN:
            return None
        layout = Nrf52Layout(app_base, linked_end, ceiling, flags)
        if build_nrf52_layout(layout) != record:
            return None
        return layout
    except (ValueError, struct.error):
        return None


def build_endf(body: bytes, ident: Optional["FwIdent"] = None) -> bytes:
    """The fixed 56-byte EndF trailer for a firmware BODY (identity zero-filled if not given)."""
    ident = ident or FwIdent()
    hw = ident.hw_id.encode("ascii", "replace")[:32].ljust(32, b"\0")
    return (ENDF_MAGIC + struct.pack("<I", len(body)) + mh8(body)
            + struct.pack("<II", ident.fw_version & 0xFFFFFFFF, ident.target_id & 0xFFFFFFFF) + hw)


def has_endf(image: bytes) -> bool:
    """True iff `image` ends with a self-consistent (fixed 56-byte) EndF trailer (image == BODY || EndF)."""
    if len(image) < ENDF_LEN:
        return False
    t = image[-ENDF_LEN:]
    return (t[:4] == ENDF_MAGIC and struct.unpack("<I", t[4:8])[0] == len(image) - ENDF_LEN
            and t[8:16] == mh8(image[:-ENDF_LEN]))


def parse_endf(image: bytes) -> Tuple[bytes, bytes]:
    """Return (body, body_hash8) for an image that ends with a valid EndF. Raises otherwise."""
    if not has_endf(image):
        raise ValueError("image has no valid EndF trailer")
    return image[:-ENDF_LEN], image[-ENDF_LEN + 8:-ENDF_LEN + 16]


def parse_endf_ident(image: bytes) -> Optional["FwIdent"]:
    """The self-describing identity from a valid EndF trailer, or None if there is no valid trailer."""
    if not has_endf(image):
        return None
    t = image[-ENDF_LEN:]
    fw, tgt = struct.unpack("<II", t[16:24])
    return FwIdent(fw, tgt, t[24:56].rstrip(b"\0").decode("ascii", "replace"))


def ensure_endf(image: bytes, ident: Optional["FwIdent"] = None) -> Tuple[bytes, bytes]:
    """Return (image_with_endf, body_hash8). Appends EndF (with `ident` if given) if not already present."""
    if has_endf(image):
        _, h8 = parse_endf(image)
        return image, h8
    body_hash8 = mh8(image)
    return image + build_endf(image, ident), body_hash8


# ---------------------------------------------------------------------------
# Merkle tree (sha2-256:4 leaves/nodes, promote-odd, no padding)
# ---------------------------------------------------------------------------

def block_count(payload_size: int, block_size: int) -> int:
    return (payload_size + block_size - 1) // block_size


def leaf_hashes(payload: bytes, block_size: int) -> List[bytes]:
    return [mh4(payload[i:i + block_size]) for i in range(0, len(payload), block_size)]


def merkle_root(leaves: List[bytes]) -> bytes:
    if not leaves:
        raise ValueError("empty payload / no leaves")
    level = list(leaves)
    while len(level) > 1:
        nxt = []
        n = len(level)
        for i in range(0, n, 2):
            if i + 1 < n:
                nxt.append(mh4(level[i] + level[i + 1]))
            else:
                nxt.append(level[i])  # promote lone last node unchanged
        level = nxt
    return level[0]


def merkle_proof(leaves: List[bytes], index: int) -> List[Tuple[bytes, bool]]:
    """Proof for block `index`: list of (sibling_digest, sibling_is_left)."""
    proof: List[Tuple[bytes, bool]] = []
    level = list(leaves)
    idx = index
    while len(level) > 1:
        n = len(level)
        is_last_odd = (n % 2 == 1) and (idx == n - 1)
        if not is_last_odd:
            if idx % 2 == 0:
                proof.append((level[idx + 1], False))   # sibling on the right
            else:
                proof.append((level[idx - 1], True))     # sibling on the left
        nxt = [mh4(level[i] + level[i + 1]) if i + 1 < n else level[i]
               for i in range(0, n, 2)]
        idx //= 2
        level = nxt
    return proof


def proof_siblings(leaves: List[bytes], index: int) -> bytes:
    """Wire form of a proof: just the ordered sibling digests, concatenated.

    The left/right direction is derived by the verifier from the block index + count
    (sibling is on the left iff the current index is odd), so no direction bits are sent.
    """
    return b"".join(sib for sib, _ in merkle_proof(leaves, index))


def verify_proof(leaf: bytes, index: int, proof: List[Tuple[bytes, bool]],
                 root: bytes, count: int) -> bool:
    h = leaf
    idx = index
    n = count
    p = 0
    while n > 1:
        is_last_odd = (n % 2 == 1) and (idx == n - 1)
        if is_last_odd:
            pass  # promoted, no proof element
        else:
            if p >= len(proof):
                return False
            sib, is_left = proof[p]
            p += 1
            h = mh4(sib + h) if is_left else mh4(h + sib)
        idx //= 2
        n = (n + 1) // 2
    return h == root and p == len(proof)


# ---------------------------------------------------------------------------
# Manifest + container
# ---------------------------------------------------------------------------

@dataclass
class Manifest:
    format_ver: int = FORMAT_VER
    flags: int = 0
    hash_algo: int = HASH_ALGO_SHA256
    target_id: int = 0
    fw_version: int = 0
    image_size: int = 0
    payload_size: int = 0
    block_size_log2: int = 11
    merkle_root: bytes = b"\0\0\0\0"
    image_hash: bytes = b"\0" * 32
    codec_id: int = CODEC_FULL
    hw_id: bytes = b"\0" * 32                  # 32-byte NUL-padded ASCII hardware tag (signed)
    # Fixed-layout: these are ALWAYS present (zero-filled when not applicable), so the manifest has a
    # constant size and a trivial offset-based parser; only leaves[] is variable.
    base_hash: bytes = b"\0" * 8               # 8 bytes; zero for a full image (meaningful iff !FULL)
    signer_pubkey: bytes = b"\0" * 32          # 32 bytes; zero when unsigned (meaningful iff SIGNED)
    signature: bytes = b"\0" * 64              # 64 bytes; zero when unsigned (meaningful iff SIGNED)
    approval: bytes = APPROVAL_NOT
    leaves: List[bytes] = field(default_factory=list)

    @property
    def is_full(self) -> bool:
        return bool(self.flags & FLAG_FULL)

    @property
    def is_signed(self) -> bool:
        return bool(self.flags & FLAG_SIGNED)

    @property
    def is_bootloader(self) -> bool:
        return bool(self.flags & FLAG_BOOTLOADER)

    @property
    def block_size(self) -> int:
        return 1 << self.block_size_log2

    @property
    def block_count(self) -> int:
        return block_count(self.payload_size, self.block_size)

    def signed_region(self) -> bytes:
        """Bytes the Ed25519 signature covers: format_ver .. signer_pubkey (fixed 129 bytes)."""
        out = bytearray()
        out += bytes([self.format_ver, self.flags, self.hash_algo])
        out += struct.pack("<IIII", self.target_id, self.fw_version,
                           self.image_size, self.payload_size)
        out += bytes([self.block_size_log2])
        out += self.merkle_root
        out += self.image_hash
        out += bytes([self.codec_id])
        out += self.hw_id                      # 32-byte hardware tag (part of the signed head)
        out += self.base_hash                  # always present (zero for a full image)
        out += self.signer_pubkey              # always present (zero when unsigned)
        return bytes(out)

    def serialize(self) -> bytes:
        """Fixed layout: signed_region(129) + signature(64) + approval(4) + leaves[4*BC]."""
        out = bytearray(self.signed_region())
        out += self.signature                  # always present (zero when unsigned)
        out += self.approval
        for lf in self.leaves:
            out += lf
        return bytes(out)


def hw_id_bytes(s) -> bytes:
    """Pack a hardware tag (str or bytes) into the fixed 32-byte NUL-padded field."""
    if s is None:
        return b"\0" * 32
    raw = s.encode("ascii") if isinstance(s, str) else bytes(s)
    if len(raw) > 32:
        raise ValueError("hw_id must be <= 32 bytes")
    return raw + b"\0" * (32 - len(raw))


def xiao_bootloader_hw_id(board_id: int) -> str:
    if board_id == XIAO_BOOT_BOARD_ID_BASE:
        return "XIAO_BL_28860044"
    if board_id == XIAO_BOOT_BOARD_ID_SENSE:
        return "XIAO_BL_28860045"
    raise ValueError(f"unsupported XIAO bootloader board ID 0x{board_id:08X}")


@dataclass(frozen=True)
class BootloaderIdentity:
    manifest_offset: int
    image_start: int
    image_size: int
    board_id: int
    device_name: str
    crc32: int
    boot_version: Optional[int] = None
    softdevice_family: Optional[int] = None
    softdevice_fwid: Optional[int] = None
    app_base: Optional[int] = None
    layout_abi: Optional[int] = None
    compat_flags: Optional[int] = None


XiaoBootloaderIdentity = BootloaderIdentity  # compatibility for callers of the original reference API


def bootloader_version_valid(version: int) -> bool:
    return version not in (0, 0xFFFFFFFF) and version & 0xFF != 0


def _bootloader_device_name(board_id: int, name_raw: bytes) -> Optional[str]:
    if len(name_raw) != 16 or board_id in (0, 0xFFFFFFFF):
        return None
    if board_id in (XIAO_BOOT_BOARD_ID_BASE, XIAO_BOOT_BOARD_ID_SENSE):
        return "XIAO_DFU" if name_raw == XIAO_BOOT_DEVICE_NAME else None
    try:
        end = name_raw.index(0)
    except ValueError:
        return None
    if end == 0 or end > 15 or any(name_raw[end:]) or any(b < 0x21 or b > 0x7E for b in name_raw[:end]):
        return None
    return name_raw[:end].decode("ascii", "strict")


def bootloader_hw_id(board_id: int, device_name: str) -> bytes:
    name_raw = device_name.encode("ascii", "strict")
    if len(name_raw) > 15 or _bootloader_device_name(board_id, name_raw.ljust(16, b"\0")) is None:
        raise ValueError("invalid embedded bootloader board/name identity")
    if board_id in (XIAO_BOOT_BOARD_ID_BASE, XIAO_BOOT_BOARD_ID_SENSE):
        return hw_id_bytes(xiao_bootloader_hw_id(board_id))
    return hw_id_bytes(f"NRF_BL_{board_id:08X}_{device_name}")


def bootloader_target_id(board_id: int, device_name: str) -> int:
    if board_id in (XIAO_BOOT_BOARD_ID_BASE, XIAO_BOOT_BOARD_ID_SENSE):
        return board_id
    return int.from_bytes(sha256(bootloader_hw_id(board_id, device_name))[:4], "little")


def audit_bootloader_target_inventory(application_target_ids=()) -> dict:
    """Return target->identity after rejecting boot/app target collisions.

    The generic image parser intentionally accepts future canonical embedded
    identities for diagnostics. Only the curated identities below are allowed
    to enter a newly built signed package, and release tests pass the generated
    application target table here to keep the two wire namespaces disjoint.
    """
    identities = (
        (XIAO_BOOT_BOARD_ID_BASE, "XIAO_DFU"),
        (XIAO_BOOT_BOARD_ID_SENSE, "XIAO_DFU"),
    ) + INTERNAL_BOOTLOADER_IDENTITIES
    app_ids = set(application_target_ids)
    targets = {}
    for identity in identities:
        target = bootloader_target_id(*identity)
        if target in targets:
            raise ValueError(
                f"bootloader target collision 0x{target:08X}: "
                f"{targets[target]!r} vs {identity!r}")
        if target in app_ids:
            raise ValueError(
                f"bootloader target 0x{target:08X} collides with an application target")
        targets[target] = identity
    return targets


def bootloader_identity_is_buildable(board_id: int, device_name: str) -> bool:
    return ((board_id, device_name) in INTERNAL_BOOTLOADER_IDENTITIES or
            (board_id, device_name) in (
                (XIAO_BOOT_BOARD_ID_BASE, "XIAO_DFU"),
                (XIAO_BOOT_BOARD_ID_SENSE, "XIAO_DFU")))


def bootloader_qualified_platform_profile(board_id: int, device_name: str):
    """Return exact (family, FWID, app_base, layout ABI), or None if unqualified."""
    identity = (board_id, device_name)
    if not bootloader_identity_is_buildable(*identity):
        return None
    if identity in BOOTLOADER_S140_V7_IDENTITIES:
        return (BOOT_CONTINUITY_FAMILY_S140, BOOTLOADER_S140_V7_FWID,
                NRF52_APP_BASE_S140_V7, BOOT_CONTINUITY_LAYOUT_ABI)
    return (BOOT_CONTINUITY_FAMILY_S140, BOOTLOADER_S140_V6_FWID,
            NRF52_APP_BASE_S140_V6, BOOT_CONTINUITY_LAYOUT_ABI)


def bootloader_qualified_storage_profiles(board_id: int, device_name: str):
    """Return the exact allowed capability-marker profiles for one identity."""
    identity = (board_id, device_name)
    if identity in ((XIAO_BOOT_BOARD_ID_BASE, "XIAO_DFU"),
                    (XIAO_BOOT_BOARD_ID_SENSE, "XIAO_DFU")):
        return (BOOT_STORAGE_QSPI_UPDATE,)
    if identity in SD_BOOTLOADER_IDENTITIES:
        # One embedded Tower identity supports two distinct application
        # targets; its marker selects either the shared internal slot or SD.
        return (BOOT_STORAGE_INTERNAL_UPDATE, BOOT_STORAGE_SD_UPDATE)
    if identity in INTERNAL_BOOTLOADER_IDENTITIES:
        return (BOOT_STORAGE_INTERNAL_UPDATE,)
    return None


def _bootloader_vector_sane(image: bytes) -> bool:
    if len(image) < 8:
        return False
    sp, reset = struct.unpack_from("<II", image)
    entry = reset & ~1
    return (sp & 7) == 0 and 0x20000000 <= sp <= 0x20040000 and (reset & 1) != 0 \
        and XIAO_BOOT_IMAGE_START <= entry < XIAO_BOOT_IMAGE_START + XIAO_BOOT_IMAGE_SIZE


def parse_bootloader_identity(image: bytes) -> Optional[BootloaderIdentity]:
    """Find one CRC-valid base identity, then validate its optional BLM2 extension.

    Duplicate accounting intentionally mirrors deployed legacy bootloaders: a
    CRC-valid BLMF-v1 record counts even when adjacent claimed BLM2 metadata is
    malformed. Otherwise a corrupt extension could hide a second privileged
    identity from a new packager while an installed legacy updater rejects it.
    """
    if len(image) != XIAO_BOOT_IMAGE_SIZE:
        return None
    found = None
    for off in range(0, len(image) - XIAO_BOOT_MANIFEST_SIZE + 1, 4):
        if image[off:off + 8] != XIAO_BOOT_MANIFEST_MAGIC:
            continue
        version, size = struct.unpack_from("<HH", image, off + 8)
        start, image_size, board_id = struct.unpack_from("<III", image, off + 12)
        name_raw = image[off + 24:off + 40]
        stored_crc = struct.unpack_from("<I", image, off + 40)[0]
        device_name = _bootloader_device_name(board_id, name_raw)
        if (version != XIAO_BOOT_MANIFEST_VERSION or size != XIAO_BOOT_MANIFEST_SIZE or
                start != XIAO_BOOT_IMAGE_START or image_size != XIAO_BOOT_IMAGE_SIZE or
                device_name is None):
            continue
        crc_image = bytearray(image)
        crc_image[off + 40:off + 44] = b"\0" * 4
        import zlib
        if zlib.crc32(crc_image) & 0xFFFFFFFF != stored_crc:
            continue
        candidate = BootloaderIdentity(off, start, image_size, board_id, device_name,
                                       stored_crc)
        if found is not None:
            return None
        found = candidate
    if found is None:
        return None

    ext = image[found.manifest_offset + XIAO_BOOT_MANIFEST_SIZE:
                found.manifest_offset + BOOT_ENVELOPE_SIZE]
    magic0 = len(ext) >= 4 and ext[:4] == BOOT_CONTINUITY_MAGIC[:4]
    magic1 = len(ext) >= 8 and ext[4:8] == BOOT_CONTINUITY_MAGIC[4:8]
    if not magic0 and not magic1:
        return found
    if len(ext) != BOOT_CONTINUITY_SIZE or not magic0 or not magic1:
        return None
    ext_version, ext_size = struct.unpack_from("<HH", ext, 8)
    boot_version, family, fwid, app_base, layout_abi, compat, reserved = \
        struct.unpack_from("<IHHIHHI", ext, 12)
    if (ext_version != BOOT_CONTINUITY_VERSION or ext_size != BOOT_CONTINUITY_SIZE or
            not bootloader_version_valid(boot_version) or family == 0 or fwid == 0 or
            app_base == 0 or layout_abi == 0 or compat != 0 or reserved != 0):
        return None
    return BootloaderIdentity(found.manifest_offset, found.image_start, found.image_size,
                              found.board_id, found.device_name, found.crc32,
                              boot_version, family, fwid, app_base, layout_abi, compat)


def parse_xiao_bootloader_identity(image: bytes) -> Optional[XiaoBootloaderIdentity]:
    """Compatibility wrapper restricted to the two deployed XIAO identities."""
    identity = parse_bootloader_identity(image)
    return identity if identity and identity.board_id in (
        XIAO_BOOT_BOARD_ID_BASE, XIAO_BOOT_BOARD_ID_SENSE) else None


def bootloader_caps_storage(image: bytes) -> Optional[int]:
    """Return the one exact supported self-update storage marker, or None."""
    found = None
    valid_count = 0
    for off in range(0, len(image) - 16 + 1, 4):
        if image[off:off + 8] != XIAO_BOOT_CAPS_MAGIC:
            continue
        abi, codecs = struct.unpack_from("<HH", image, off + 8)
        storage = image[off + 12]
        if (abi < BOOT_FORMAT_VER or abi == 0xFFFF or
                codecs & BOOT_REQUIRED_APP_CODEC_MASK != BOOT_REQUIRED_APP_CODEC_MASK or
                storage & ~BOOT_STORAGE_KNOWN or image[off + 13:off + 16] != b"\0\0\0" or
                not storage & XIAO_BOOT_STORAGE_UPDATE):
            continue
        valid_count += 1
        if valid_count != 1:
            return None
        if storage in (BOOT_STORAGE_SD_UPDATE, BOOT_STORAGE_QSPI_UPDATE,
                       BOOT_STORAGE_INTERNAL_UPDATE):
            found = storage
    return found if valid_count == 1 else None


def xiao_bootloader_caps_ok(image: bytes) -> bool:
    return bootloader_caps_storage(image) == BOOT_STORAGE_QSPI_UPDATE


def validate_bootloader_image(image: bytes, target_id: Optional[int] = None,
                              signed_hw_id: Optional[bytes] = None) -> BootloaderIdentity:
    if len(image) != XIAO_BOOT_IMAGE_SIZE:
        raise ValueError(f"bootloader image must be exactly {XIAO_BOOT_IMAGE_SIZE} bytes")
    if not _bootloader_vector_sane(image):
        raise ValueError("bootloader vector table is invalid")
    identity = parse_bootloader_identity(image)
    if identity is None:
        raise ValueError("bootloader embedded manifest/CRC is invalid or ambiguous")
    if identity.boot_version is None:
        raise ValueError("bootloader lacks the BLM2/SOFT continuity extension")
    if identity.manifest_offset != BOOT_CANDIDATE_MANIFEST_OFFSET:
        raise ValueError(
            f"bootloader continuity envelope must be at exact offset "
            f"0x{BOOT_CANDIDATE_MANIFEST_OFFSET:04X}")
    expected_target = bootloader_target_id(identity.board_id, identity.device_name)
    if target_id is not None and target_id != expected_target:
        raise ValueError("bootloader target ID does not match embedded identity")
    expected_hw = bootloader_hw_id(identity.board_id, identity.device_name)
    if signed_hw_id is not None and bytes(signed_hw_id) != expected_hw:
        raise ValueError("bootloader signed hw_id does not match embedded identity")
    expected_storage = bootloader_qualified_storage_profiles(
        identity.board_id, identity.device_name)
    if expected_storage is None:
        # Keep generic parsing/inspection useful for future canonical images;
        # only the signing/build path below admits the curated inventory.
        expected_storage = (BOOT_STORAGE_INTERNAL_UPDATE,)
    actual_storage = bootloader_caps_storage(image)
    if actual_storage not in expected_storage:
        expected = "/".join(f"0x{value:02X}" for value in expected_storage)
        raise ValueError(f"bootloader lacks exact ABI 3 self-update capabilities {expected}")
    expected_platform = bootloader_qualified_platform_profile(
        identity.board_id, identity.device_name)
    if expected_platform is not None:
        actual_platform = (identity.softdevice_family, identity.softdevice_fwid,
                           identity.app_base, identity.layout_abi)
        if actual_platform != expected_platform:
            family, fwid, app_base, layout_abi = expected_platform
            raise ValueError(
                "bootloader continuity platform does not match qualified "
                f"profile S{family}/0x{fwid:04X}/0x{app_base:X}/ABI{layout_abi}")
    return identity


def validate_xiao_bootloader_image(image: bytes, board_id: Optional[int] = None) -> XiaoBootloaderIdentity:
    identity = validate_bootloader_image(image)
    if identity.board_id not in (XIAO_BOOT_BOARD_ID_BASE, XIAO_BOOT_BOARD_ID_SENSE):
        raise ValueError("bootloader is not a deployed XIAO identity")
    if board_id is not None and identity.board_id != board_id:
        raise ValueError("bootloader embedded board ID does not match package target")
    return identity


def _validate_lengths(m: Manifest):
    assert len(m.merkle_root) == 4
    assert len(m.image_hash) == 32
    assert len(m.hw_id) == 32
    assert len(m.approval) == 4
    assert len(m.base_hash) == 8               # fixed layout: always present (zero for full)
    assert len(m.signer_pubkey) == 32
    assert len(m.signature) == 64


def build_manifest(*, target_id: int, fw_version: int, image_size: int, payload: bytes,
                   block_size: int, image_hash: bytes, codec_id: int, is_full: bool,
                   base_hash: Optional[bytes] = None, sign_priv=None, hw_id=None,
                   bootloader: bool = False) -> Manifest:
    assert (block_size & (block_size - 1)) == 0, "block_size must be a power of two"
    if not 1 < block_size <= MAX_APP_BLOCK_SIZE:
        raise ValueError(
            f"application block size must be at most {MAX_APP_BLOCK_SIZE} bytes")
    leaves = leaf_hashes(payload, block_size)
    if bootloader:
        identity = validate_bootloader_image(payload, target_id)
        audit_bootloader_target_inventory()
        if not bootloader_identity_is_buildable(identity.board_id, identity.device_name):
            raise ValueError("bootloader embedded identity is not in the qualified build inventory")
        if (not bootloader_version_valid(fw_version) or
                fw_version != identity.boot_version or not is_full or codec_id != CODEC_FULL or
                image_size != XIAO_BOOT_IMAGE_SIZE or
                block_size != BOOTLOADER_BLOCK_SIZE or base_hash not in (None, b"\0" * 8)):
            raise ValueError("bootloader package version must equal BLM2 metadata and use a 40 KiB CODEC_FULL image with 1 KiB blocks")
        if sign_priv is None:
            raise ValueError("bootloader package must be signed")
        expected_hw = bootloader_hw_id(identity.board_id, identity.device_name)
        if hw_id is not None and hw_id_bytes(hw_id) != expected_hw:
            raise ValueError("bootloader package hw_id must be canonical for its embedded identity")
        hw_id = expected_hw
    m = Manifest(
        format_ver=BOOT_FORMAT_VER if bootloader else APP_FORMAT_VER,
        flags=((FLAG_FULL if is_full else 0) | (FLAG_SIGNED if sign_priv is not None else 0) |
               (FLAG_BOOTLOADER if bootloader else 0)),
        target_id=target_id,
        fw_version=fw_version,
        image_size=image_size,
        payload_size=len(payload),
        block_size_log2=block_size.bit_length() - 1,
        merkle_root=merkle_root(leaves),
        image_hash=image_hash,
        codec_id=codec_id,
        hw_id=hw_id_bytes(hw_id),
        base_hash=(b"\0" * 8 if is_full else base_hash),   # always 8 bytes; zero for a full image
        leaves=leaves,
    )
    if not is_full and (base_hash is None or len(base_hash) != 8):
        raise ValueError("delta requires an 8-byte base_hash")
    if sign_priv is not None:
        m.signer_pubkey = ed25519_public_bytes(sign_priv)
        m.signature = sign_priv.sign(m.signed_region())    # else signer_pubkey/signature stay zero
    _validate_lengths(m)
    return m


def build_container(manifest: Manifest, payload: bytes) -> bytes:
    assert len(payload) == manifest.payload_size
    mser = manifest.serialize()
    total = 4 + 4 + len(mser) + len(payload) + len(TRAILER)
    return MAGIC + struct.pack("<I", total) + mser + payload + TRAILER


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class Parsed:
    manifest: Manifest
    payload: bytes
    total_size: int


def parse_container(blob: bytes) -> Parsed:
    if blob[:4] != MAGIC:
        raise ValueError("bad MAGIC")
    if blob[-5:] != TRAILER:
        raise ValueError("bad TRAILER")
    total = struct.unpack("<I", blob[4:8])[0]
    if total != len(blob):
        raise ValueError(f"MOTA_TOTAL_SIZE {total} != actual {len(blob)}")

    r = io.BytesIO(blob[8:-5])  # manifest + payload (trailer already validated/stripped)

    def take(n):
        b = r.read(n)
        if len(b) != n:
            raise ValueError("truncated manifest")
        return b

    m = Manifest()
    m.format_ver = take(1)[0]
    m.flags = take(1)[0]
    if m.format_ver == APP_FORMAT_VER:
        if m.flags & FLAG_BOOTLOADER or m.flags & ~FLAG_KNOWN:
            raise ValueError("v2 application manifest has invalid flags")
    elif m.format_ver == BOOT_FORMAT_VER:
        if m.flags != FLAG_FULL | FLAG_SIGNED | FLAG_BOOTLOADER:
            raise ValueError("v3 bootloader manifest requires exact FULL|SIGNED|BOOTLOADER flags")
    else:
        raise ValueError(f"unsupported format_ver {m.format_ver}")
    m.hash_algo = take(1)[0]
    m.target_id, m.fw_version, m.image_size, m.payload_size = struct.unpack("<IIII", take(16))
    m.block_size_log2 = take(1)[0]
    if m.format_ver == APP_FORMAT_VER:
        if not 1 <= m.block_size_log2 <= MAX_APP_BLOCK_SIZE.bit_length() - 1:
            raise ValueError(
                f"v2 application block size must be at most {MAX_APP_BLOCK_SIZE} bytes")
    elif m.block_size_log2 != BOOTLOADER_BLOCK_SIZE.bit_length() - 1:
        raise ValueError("v3 bootloader manifest requires 1 KiB blocks")
    m.merkle_root = take(4)
    m.image_hash = take(32)
    m.codec_id = take(1)[0]
    m.hw_id = take(32)
    m.base_hash = take(8)               # fixed layout: always present (zero for full)
    m.signer_pubkey = take(32)
    m.signature = take(64)
    m.approval = take(4)
    bc = m.block_count
    m.leaves = [take(4) for _ in range(bc)]

    payload = r.read(m.payload_size)
    if len(payload) != m.payload_size:
        raise ValueError("truncated payload")
    rest = r.read()  # trailer was stripped above, so nothing should remain
    if rest != b"":
        raise ValueError("trailing bytes after payload")
    if m.is_bootloader:
        if (not bootloader_version_valid(m.fw_version) or m.codec_id != CODEC_FULL or
                m.image_size != XIAO_BOOT_IMAGE_SIZE or
                m.payload_size != XIAO_BOOT_IMAGE_SIZE or
                m.block_size_log2 != 10 or m.block_count != 40 or m.base_hash != b"\0" * 8):
            raise ValueError("v3 bootloader manifest geometry/identity is invalid")
        try:
            identity = validate_bootloader_image(payload, m.target_id, m.hw_id)
            if m.fw_version != identity.boot_version:
                raise ValueError("outer bootloader version does not match BLM2 metadata")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"v3 bootloader image identity/capability is invalid: {exc}") from exc
    return Parsed(manifest=m, payload=payload, total_size=total)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify(parsed: Parsed, *, expect_pub: Optional[bytes] = None,
           base_image: Optional[bytes] = None) -> List[str]:
    """Return a list of problem strings (empty == fully valid for what could be checked)."""
    problems: List[str] = []
    m, payload = parsed.manifest, parsed.payload

    if m.is_bootloader:
        try:
            identity = validate_bootloader_image(payload, m.target_id, m.hw_id)
            if m.fw_version != identity.boot_version:
                problems.append("outer bootloader version does not match BLM2 metadata")
        except (UnicodeDecodeError, ValueError) as exc:
            problems.append(f"bootloader image contract: {exc}")

    # block_count / leaves
    if len(m.leaves) != m.block_count:
        problems.append(f"leaves count {len(m.leaves)} != block_count {m.block_count}")

    # merkle root must match recomputation from the actual payload blocks
    recomputed_leaves = leaf_hashes(payload, m.block_size)
    if recomputed_leaves != m.leaves:
        problems.append("stored leaves[] do not match payload blocks")
    try:
        if merkle_root(recomputed_leaves) != m.merkle_root:
            problems.append("merkle_root does not match payload")
    except ValueError as e:
        problems.append(f"merkle: {e}")

    # spot-check a proof round-trips (block 0 and last)
    if recomputed_leaves:
        for idx in {0, len(recomputed_leaves) - 1}:
            pr = merkle_proof(recomputed_leaves, idx)
            if not verify_proof(recomputed_leaves[idx], idx, pr, m.merkle_root, len(recomputed_leaves)):
                problems.append(f"merkle proof failed for block {idx}")

    # approval must be 'not approved' in a distributed container
    if m.approval != APPROVAL_NOT:
        problems.append(f"approval is not the erased sentinel (got {m.approval.hex()})")

    # signature
    if m.is_signed:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
        pub = Ed25519PublicKey.from_public_bytes(m.signer_pubkey)
        try:
            pub.verify(m.signature, m.signed_region())
        except InvalidSignature:
            problems.append("Ed25519 signature INVALID")
        if expect_pub is not None and m.signer_pubkey != expect_pub:
            problems.append("signer_pubkey != expected key")

    # image_hash: directly checkable only for full images (payload IS the image)
    if m.is_full:
        if mh32(payload) != m.image_hash:
            problems.append("image_hash does not match full payload")
    elif base_image is not None:
        # delta: optionally apply against a provided base to confirm image_hash
        try:
            rebuilt = _apply_detools_delta(m.codec_id, payload, _ensure_base(base_image))
            if mh32(rebuilt) != m.image_hash:
                problems.append("delta applied to base does not match image_hash")
            if len(rebuilt) != m.image_size:
                problems.append("delta result size != image_size")
        except Exception as e:  # noqa: BLE001
            problems.append(f"delta apply check failed: {e}")
    return problems


def _apply_detools_delta(codec_id: int, payload: bytes, base_image: bytes) -> bytes:
    """Apply a detools delta using the decoder selected by its manifest codec."""
    import detools

    if codec_id == CODEC_DETOOLS_SEQUENTIAL:
        out = io.BytesIO()
        detools.apply_patch(io.BytesIO(base_image), io.BytesIO(payload), out)
        return out.getvalue()

    if codec_id == CODEC_DETOOLS_INPLACE:
        patch_type, patch_info = detools.patch_info(io.BytesIO(payload))
        if patch_type != "in-place":
            raise ValueError(f"in-place codec contains a {patch_type} patch")

        # detools embeds the required flash-memory geometry in every in-place
        # patch.  apply_patch_in_place() requires a mutable image of at least
        # that size and writes the reconstructed image at offset zero.
        memory_size = patch_info[3]
        memory = io.BytesIO(base_image.ljust(memory_size, b"\xff"))
        rebuilt_size = detools.apply_patch_in_place(memory, io.BytesIO(payload))
        memory.seek(0)
        return memory.read(rebuilt_size)

    raise ValueError(f"unsupported detools codec_id {codec_id}")


def _ensure_base(base_image: bytes) -> bytes:
    img, _ = ensure_endf(base_image)
    return img
