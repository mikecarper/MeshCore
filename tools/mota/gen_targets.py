#!/usr/bin/env python3
"""Generate src/helpers/ota/OtaTargets.h - the target_id -> env-name table for OTA-capable builds.

A target_id is sha2-256:4(pio_env_name); it travels in beacons / the .mota manifest as 4 bytes. This
table lets a node (and motatool) print the human-readable env name for a target seen over the air, WITHOUT
ever transmitting the string. The set is the union of PlatformIO environments whose resolved build flags
enable LoRa OTA and qualified release-only aliases composed by build.sh. Keeping those aliases in the
table prevents a valid release target from being displayed as unknown merely because it is not a literal
``[env:...]`` section.

Run:  ./meshcore/bin/python tools/mota/gen_targets.py            # resolves via `pio project config`
      ./meshcore/bin/python tools/mota/gen_targets.py cfg.json   # or reuse a cached config dump (faster)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import motalib as ml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "src" / "helpers" / "ota" / "OtaTargets.h"
INTERNAL_BOOTLOADER_TARGETS = ROOT / "tools" / "mota" / "nrf52_internal_bootloader_targets.txt"
NAME_DECODE_CHUNK_SIZE = 512
RAK_FULL_NAME_DECODE_CHUNK_SIZE = 4096


def resolved_config():
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        return json.load(open(sys.argv[1]))
    pio = os.environ.get("PIO", "pio")
    out = subprocess.run([pio, "project", "config", "--json-output"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def ota_envs(cfg):
    envs = []
    for section, opts in cfg:
        if not section.startswith("env:"):
            continue
        for k, v in opts:
            if k == "build_flags":
                flags = " ".join(v) if isinstance(v, list) else str(v)
                if "ENABLE_OTA" in flags and "DISABLE_LORA_OTA" not in flags:
                    envs.append(section[4:])
                break
    return sorted(set(envs))


def release_aliases():
    """Return qualified build.sh aliases that carry their own OTA target ID."""
    return sorted({
        line.strip()
        for line in INTERNAL_BOOTLOADER_TARGETS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    })


def target_rows(envs):
    rows = [(ml.target_id_for_env(e), e) for e in sorted(set(envs))]
    seen = {}
    for tid, e in rows:                       # sha2-256:4 over all names - verify no collisions
        if tid in seen and seen[tid] != e:
            raise RuntimeError(
                f"target_id collision 0x{tid:08x}: {seen[tid]!r} vs {e!r}"
            )
        seen[tid] = e
    rows.sort(key=lambda r: r[1].lower())     # by env name (readable, deterministic)
    return rows


def compressed_name_chunks(rows, max_chunk_size=NAME_DECODE_CHUNK_SIZE):
    """Front-code all names into bounded, independent Zopfli-1000 streams.

    No OTA wire format or target ID changes. Each record is a one-byte common
    prefix length followed by a NUL-terminated UTF-8 suffix. Chunk boundaries
    reset the prefix, so lookup needs at most one small tinf output buffer.
    """
    return _compressed_name_chunks(tuple(rows), max_chunk_size)


@lru_cache(maxsize=8)
def _compressed_name_chunks(rows, max_chunk_size):
    sys.path.insert(0, str(ROOT / "scripts"))
    from zopfli_compress import MAXIMUM_ITERATIONS, raw_deflate_compress

    if not rows or max_chunk_size < 2 or max_chunk_size > 65535:
        raise ValueError("OTA target-name chunk size or inventory is invalid")
    raw_chunks = []
    firsts = [0]
    current = bytearray()
    previous = b""
    for index, (_, name) in enumerate(rows):
        encoded = name.encode("utf-8")
        if b"\0" in encoded or len(encoded) > 255:
            raise ValueError(f"OTA target name is not encodable: {name!r}")
        prefix = 0
        while (prefix < min(len(previous), len(encoded)) and
               previous[prefix] == encoded[prefix]):
            prefix += 1
        record = bytes([prefix]) + encoded[prefix:] + b"\0"
        if len(current) + len(record) > max_chunk_size:
            raw_chunks.append(bytes(current))
            firsts.append(index)
            current.clear()
            prefix = 0
            record = b"\0" + encoded + b"\0"
        if len(record) > max_chunk_size:
            raise ValueError(f"OTA target name exceeds the decode chunk: {name!r}")
        current.extend(record)
        previous = encoded
    raw_chunks.append(bytes(current))
    firsts.append(len(rows))
    data = bytearray()
    offsets = [0]
    for raw in raw_chunks:
        data.extend(raw_deflate_compress(raw, numiterations=MAXIMUM_ITERATIONS))
        offsets.append(len(data))
    if len(data) > 65535 or len(rows) > 65535:
        raise ValueError("OTA target-name data exceeds uint16_t restart offsets")
    return bytes(data), tuple(offsets), tuple(len(raw) for raw in raw_chunks), tuple(firsts)


def generate_header(rows):
    capacity = max((len(name.encode("utf-8")) + 1 for _, name in rows), default=1)

    def chunk_tables(chunk_size):
        data, offsets, raw_lengths, firsts = compressed_name_chunks(rows, chunk_size)
        lines = [f"  enum {{ DECODE_CHUNK_BYTES = {chunk_size} }};"]
        for name, values in (("OFFSETS", offsets), ("RAW_LENGTHS", raw_lengths), ("FIRSTS", firsts)):
            lines += [f"  static const uint16_t {name}[] = {{"]
            lines += ["    " + ", ".join(str(value) for value in values[i:i + 12]) + ","
                      for i in range(0, len(values), 12)]
            lines += ["  };"]
        lines += [f"  // {len(data)} encoded bytes; {len(raw_lengths)} independent chunks, at most {chunk_size} decoded bytes.",
                  "  static const uint8_t NAMES[] = {"]
        lines += ["    " + ", ".join(f"0x{byte:02x}" for byte in data[i:i + 16]) + ","
                  for i in range(0, len(data), 16)]
        return lines + ["  };"]

    out = [
        "#pragma once",
        "#include <stdint.h>",
        "#include <stddef.h>",
        "",
        "// AUTO-GENERATED by tools/mota/gen_targets.py - do not edit by hand.",
        f"// {len(rows)} OTA-capable build targets. Maps target_id (= sha2-256:4 of the target name, LE uint32)",
        "// to the human-readable env name, so a node/tool can name a target seen over the air WITHOUT",
        "// transmitting the string in the .mota / LoRa protocol. Regenerate when the OTA env set changes.",
        "// Size-constrained receivers can set OTA_TARGET_NAME_TABLE=0. They still match targets by ID;",
        "// OTA_LOCAL_TARGET_ID + OTA_LOCAL_TARGET_NAME optionally retain the local human-readable name.",
        "",
        "#ifndef OTA_TARGET_NAME_TABLE",
        "  #define OTA_TARGET_NAME_TABLE 1",
        "#endif",
        "",
        "#ifndef OTA_TARGET_NAME_FRONT_CODED",
        "  #define OTA_TARGET_NAME_FRONT_CODED 0",
        "#endif",
        "",
        "#if OTA_TARGET_NAME_TABLE && OTA_TARGET_NAME_FRONT_CODED",
        '  #include "tinf/tinf.h"',
        "  #if defined(OTA_RAK15002_SD_STORE) && defined(COMPANION_RADIO_FULL)",
        "    #include <stdlib.h>",
        "  #endif",
        "#endif",
        "",
        "namespace mesh { namespace ota {",
        "",
        "#if OTA_TARGET_NAME_TABLE && OTA_TARGET_NAME_FRONT_CODED",
        "// All names retained. Front coding + Google Zopfli (1,000 iterations), raw DEFLATE.",
        "// Reuses the existing OTA tinf decoder; local chunk/caller buffers, never the live OTA context.",
        "// This is firmware-only storage compression: target IDs and the LoRa protocol are unchanged.",
        f"enum {{ OTA_TARGET_ENV_NAME_CAPACITY = {capacity} }};",
        "inline const char* ota_target_env_name(uint32_t target_id, char* buffer, size_t capacity) {",
        "  if (!buffer || !capacity) return nullptr;",
        "  buffer[0] = 0;",
        "  static const uint32_t IDS[] = {",
    ]
    out += ["    " + ", ".join(f"0x{tid:08x}" for tid, _ in rows[i:i + 8]) + ","
            for i in range(0, len(rows), 8)]
    out += ["  };", "#if defined(OTA_RAK15002_SD_STORE) && defined(COMPANION_RADIO_FULL)"]
    out += chunk_tables(RAK_FULL_NAME_DECODE_CHUNK_SIZE)
    out += ["#else"] + chunk_tables(NAME_DECODE_CHUNK_SIZE) + ["#endif"]
    out += [
        "  for (unsigned i = 0; i < sizeof(IDS) / sizeof(IDS[0]); i++) {",
        "    if (IDS[i] != target_id) continue;",
        "    unsigned group = 0;",
        "    while (i >= FIRSTS[group + 1]) group++;",
        "#if defined(OTA_RAK15002_SD_STORE) && defined(COMPANION_RADIO_FULL)",
        "    // Rare display lookup: larger chunks save flash without growing the loop stack.",
        "    uint8_t* raw = (uint8_t*)malloc(DECODE_CHUNK_BYTES);",
        "    if (!raw) return nullptr;",
        "    struct Scratch { uint8_t* bytes; ~Scratch() { free(bytes); } } scratch = {raw};",
        "#else",
        "    uint8_t raw[DECODE_CHUNK_BYTES];",
        "#endif",
        "    unsigned int produced = DECODE_CHUNK_BYTES;",
        "    if (tinf_uncompress_exact(raw, &produced, NAMES + OFFSETS[group],",
        "                              OFFSETS[group + 1] - OFFSETS[group]) != TINF_OK ||",
        "        produced != RAW_LENGTHS[group]) return nullptr;",
        "    const uint8_t* record = raw;",
        "    const uint8_t* end = raw + produced;",
        "    size_t previous_length = 0;",
        "    for (unsigned row = FIRSTS[group]; row <= i; row++) {",
        "      if (record == end) { buffer[0] = 0; return nullptr; }",
        "      size_t length = *record++;",
        "      if (length > previous_length) { buffer[0] = 0; return nullptr; }",
        "      while (record < end && *record) {",
        "        if (length + 1 < capacity) buffer[length] = (char)*record;",
        "        length++; record++;",
        "      }",
        "      if (record == end) { buffer[0] = 0; return nullptr; }",
        "      record++;",
        "      buffer[length < capacity ? length : capacity - 1] = 0;",
        "      if (row == i && length >= capacity) { buffer[0] = 0; return nullptr; }",
        "      previous_length = length;",
        "    }",
        "    return buffer;",
        "  }",
        "  return nullptr;",
        "}",
        "#else",
        "// target_id -> env name, or nullptr if unknown. Linear scan; lookups are rare.",
        "inline const char* ota_target_env_name(uint32_t target_id) {",
        "#if OTA_TARGET_NAME_TABLE",
        "  static const struct { uint32_t id; const char* env; } T[] = {",
    ]
    out += [f'    {{ 0x{tid:08x}, "{e}" }},' for tid, e in rows]
    out += [
        "  };",
        "  for (unsigned i = 0; i < sizeof(T) / sizeof(T[0]); i++)",
        "    if (T[i].id == target_id) return T[i].env;",
        "#elif defined(OTA_LOCAL_TARGET_ID) && defined(OTA_LOCAL_TARGET_NAME)",
        "  if (target_id == (uint32_t)(OTA_LOCAL_TARGET_ID)) return OTA_LOCAL_TARGET_NAME;",
        "#else",
        "  (void)target_id;",
        "#endif",
        "  return nullptr;",
        "}",
        "// The original flash-string API remains unchanged when compression is disabled.",
        "inline const char* ota_target_env_name(uint32_t target_id, char*, size_t) {",
        "  return ota_target_env_name(target_id);",
        "}",
        "#endif",
        "",
        "} } // namespace mesh::ota",
    ]
    return "\n".join(out) + "\n"


def main():
    envs = sorted(set(ota_envs(resolved_config())) | set(release_aliases()))
    rows = target_rows(envs)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(generate_header(rows), encoding="utf-8")
    print(f"wrote {OUT}  ({len(rows)} OTA targets)")


if __name__ == "__main__":
    main()
