#!/usr/bin/env python3
"""Decode and authenticate complete MGR1/MGR2 management reports.

Input is a JSON list of raw payload hex strings, one entry for each page (not
MeshCore packet headers/paths). A relay/collector can obtain these from RX logs
or the companion raw-data notification.
"""
import argparse
import getpass
import hashlib
import hmac
import json
import struct
from pathlib import Path

HEADER, ENTRY, PER_PAGE, TAG, MAX_KEYS = 83, 13, 6, 16, 36
CURRENT_HEADER, CURRENT_PER_PAGE = 111, 4
MAX_JSON_DEPTH = 64


def _load_input_json(text):
    # Do not rely on the interpreter's JSON scanner using recursive calls.
    # Check before decoding so excessive containers never become a deep tree.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("input JSON is nested too deeply")
        elif char in "]}":
            depth -= 1
    return json.loads(text)


def _layout(payload):
    formats = {b"MGR1": (HEADER, PER_PAGE), b"MGR2": (CURRENT_HEADER, CURRENT_PER_PAGE)}
    result = formats.get(payload[:4])
    if result is None or len(payload) < result[0] + TAG:
        raise ValueError("invalid management payload")
    return result


def _page_bounds(payload):
    header, per_page = _layout(payload)
    if header == CURRENT_HEADER and (int.from_bytes(payload[83:85], "little") & 0xf800 or payload[85] & 0xc0):
        raise ValueError("reserved USB status bits must be zero")
    if header == CURRENT_HEADER:
        _watchdog_event(payload)
    page, pages, total, first, count = payload[78:83]
    expected = max(1, (total + per_page - 1) // per_page)
    if (total > MAX_KEYS or pages != expected or page >= pages or
            first != page * per_page or first > total or
            count != min(per_page, total - first)):
        raise ValueError("invalid management page bounds")
    return header, header + count * ENTRY + TAG


def _usb_status(payload):
    if payload[:4] != b"MGR2":
        return None
    flags = int.from_bytes(payload[83:85], "little")
    names = ("supported", "logging_enabled", "watchdog_enabled", "host_connected",
             "reader_connected", "stalled", "recovering", "recovery_deferred",
             "persistence_ready", "watchdog_auto", "logger_active")
    result = {name: bool(flags & (1 << bit)) for bit, name in enumerate(names)}
    result.update(stage=payload[85] & 3, backoff_step=(payload[85] >> 2) & 15,
                  retry_seconds=int.from_bytes(payload[86:90], "little"),
                  inactive_seconds=int.from_bytes(payload[90:94], "little"),
                  auto_connected_seconds=int.from_bytes(payload[94:98], "little"))
    return result


def _watchdog_event(payload):
    if payload[:4] != b"MGR2":
        return None
    code = payload[98]
    epoch, uptime, sequence = struct.unpack_from("<III", payload, 99)
    if not sequence:
        if any(payload[98:111]):
            raise ValueError("invalid empty USB watchdog event")
        return None
    reasons, action = code & 15, (code >> 4) & 7
    if not reasons or not 1 <= action <= 4:
        raise ValueError("invalid USB watchdog event reason/action")
    names = ("host-absent", "reader-absent", "tx-stalled", "client-inactive")
    actions = (None, "soft-recovery", "reenumerate", "reboot-requested", "reboot-cancelled")
    return dict(reasons=reasons, reason_names=[name for bit, name in enumerate(names) if reasons & (1 << bit)],
                action_code=action, action=actions[action], persisted=bool(code & 128),
                epoch=epoch, uptime_seconds=uptime, sequence=sequence)


def password_key(password):
    encoded = password.encode("utf-8")
    if not 12 <= len(encoded) <= 96:
        raise ValueError("password must contain 12..96 UTF-8 bytes")
    return hashlib.sha256(b"#" + encoded).digest()


def derive(key, domain, radio):
    return hmac.digest(key, domain.encode("ascii") + radio, "sha256")


def fingerprint(password, radio, administrator):
    if len(radio) != 16 or len(administrator) != 32:
        raise ValueError("radio ID must be 16 bytes; administrator key must be 32 bytes")
    key = derive(password_key(password), "MeshCore-MGR1-ACL", radio)
    return hmac.digest(key, radio + administrator, "sha256")[:12]


def _temperature(value):
    if value == 0:
        return None
    if value == 252:
        return "below -50 C"
    if value == 253:
        return "above 200 C"
    if value > 253:
        raise ValueError("reserved temperature value")
    return value - 51


def _extrema(raw):
    voltage = int.from_bytes(raw[:2], "little")
    return dict(min_voltage_mv=voltage or None, min_temperature_c=_temperature(raw[2]),
                max_temperature_c=_temperature(raw[3]))


def decode_page(payload, password):
    from Crypto.Cipher import AES  # PyCryptodome; independent AES-SIV implementation
    header, canonical = _page_bounds(payload)
    count = payload[82]
    if len(payload) != canonical or len(payload) > 179:
        raise ValueError("invalid management page bounds")
    key = derive(password_key(password), "MeshCore-MGR1-SIV", payload[4:20])
    cipher = AES.new(key, AES.MODE_SIV)
    cipher.update(payload[:header])
    private = cipher.decrypt_and_verify(payload[header:-TAG], payload[-TAG:])
    entries = []
    seen = set()
    for i in range(count):
        token = private[i * ENTRY:i * ENTRY + 12]
        flags = private[i * ENTRY + 12]
        if not flags or flags & ~3:
            raise ValueError("invalid ACL role flags")
        if token in seen:
            raise ValueError("duplicate ACL fingerprint")
        seen.add(token)
        entries.append(dict(fingerprint=token.hex(), admin=bool(flags & 1), ota_signer=bool(flags & 2)))
    return entries


def mqtt_payload(message):
    """Extract an MGR1/MGR2 payload from the observer's MQTT PACKET/raw JSON.

    Path length is encoded, not simply a byte count. Region transport codes
    precede it. Never trust the redundant MQTT payload_len/type fields.
    """
    if not isinstance(message, dict) or message.get("direction", "rx") != "rx":
        return None
    raw = message.get("raw") if message.get("type") == "PACKET" else message.get("data")
    if not isinstance(raw, str):
        return None
    try:
        packet = bytes.fromhex(raw)
    except ValueError:
        return None
    if not 2 <= len(packet) <= 255 or packet[0] >> 6 or (packet[0] >> 2) & 15 not in (6, 15):
        return None
    position = 5 if packet[0] & 3 in (0, 3) else 1
    if position >= len(packet):
        return None
    path_len = packet[position]
    width, count = (path_len >> 6) + 1, path_len & 63
    if width > 3 or width * count > 64:
        return None
    start = position + 1 + width * count
    payload = packet[start:]
    try:
        _, canonical = _page_bounds(payload)
    except ValueError:
        return None
    if (packet[0] >> 2) & 15 == 6:
        padded = 3 + ((canonical - 3 + 15) // 16) * 16
        if padded > 179 or len(payload) != padded or any(payload[canonical:]):
            return None
        payload = payload[:canonical]
    elif len(payload) != canonical or len(payload) > 179:
        return None
    return payload


def mqtt_reports(messages, password):
    """Group a saved MQTT capture, deduplicating copies heard by many uplinks.

    A conflicting page fails closed. Only fully authenticated complete reports
    are returned.
    """
    snapshots = {}
    for message in messages:
        payload = mqtt_payload(message)
        if payload is None:
            continue
        decode_page(payload, password)
        identity = payload[4:24]
        pages = snapshots.setdefault(identity, {})
        previous = pages.get(payload[78])
        if previous is not None and previous != payload:
            raise ValueError("conflicting MQTT copies for one management page")
        pages[payload[78]] = payload
    results = []
    for pages in snapshots.values():
        first = next(iter(pages.values()))
        if len(pages) == first[79]:
            results.append(decode_report(list(pages.values()), password))
    return results


def decode_report(payloads, password):
    if not payloads or len(payloads) > 9:
        raise ValueError("one through nine pages required")
    # Check every page before indexing metadata, then reject omissions,
    # duplicates, mixed snapshots and unauthenticated public fields.
    decoded = [(p, decode_page(p, password)) for p in payloads]
    decoded.sort(key=lambda item: item[0][78])
    first = decoded[0][0]
    if len(decoded) != first[79] or [p[78] for p, _ in decoded] != list(range(first[79])):
        raise ValueError("incomplete or duplicate report pages")
    header, _ = _layout(first)
    if any(p[:78] != first[:78] or p[79:81] != first[79:81] or
           p[HEADER:header] != first[HEADER:header] for p, _ in decoded):
        raise ValueError("mixed report snapshots")
    acl = [entry for _, entries in decoded for entry in entries]
    if len({entry["fingerprint"] for entry in acl}) != len(acl):
        raise ValueError("duplicate ACL fingerprint")
    fields = struct.unpack_from("<IIIII", first, 20)
    sequence, timestamp, firmware, bootloader, target = fields
    valid = int.from_bytes(first[76:78], "little")
    radio = first[4:20]
    def version(value):
        return ".".join(str((value >> n) & 255) for n in (24, 16, 8, 0))
    result = dict(protocol=first[:4].decode("ascii"),
                  radio_id=radio.hex(), sequence=sequence, timestamp=timestamp,
                  firmware_version=version(firmware) if valid & 1 else None,
                  bootloader_version=version(bootloader) if valid & 2 else None,
                  target_id=f"{target:08x}" if valid & 4 else None,
                  base_hash=first[40:48].hex() if valid & 4 else None,
                  image_length=int.from_bytes(first[48:52], "little") if valid & 4 else None,
                  staging_capacity=int.from_bytes(first[52:56], "little") if valid & 8 else None,
                  ota_capabilities=int.from_bytes(first[56:60], "little"),
                  uptime_hours=int.from_bytes(first[60:62], "little"),
                  weekly=_extrema(first[62:66]), since_report=_extrema(first[66:70]),
                  history_hours=first[70], interval_days=first[71], role=first[72],
                  capability_bits=first[73], active_bits=first[74], known_bits=first[75],
                  partial_week=bool(valid & 16), partial_period=bool(valid & 32),
                  temperature_source="MCU" if valid & 64 else "unknown",
                  usb_logging=_usb_status(first),
                  usb_watchdog_last=_watchdog_event(first),
                  acl=acl)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pages", type=Path, help="JSON array of raw payload hex strings")
    parser.add_argument("--match-admin", help="full public key to match against private ACL fingerprints")
    parser.add_argument("--mqtt", action="store_true", help="input is JSON array or JSONL of saved MQTT uplinks")
    args = parser.parse_args()
    try:
        administrator = None
        if args.match_admin is not None:
            administrator = bytes.fromhex(args.match_admin)
            if len(administrator) != 32:
                raise ValueError("administrator key must be 32 bytes")
        text = args.pages.read_text(encoding="utf-8")
        if args.mqtt:
            messages = (_load_input_json(text) if text.lstrip().startswith("[") else
                        [_load_input_json(line) for line in text.splitlines() if line.strip()])
        else:
            payloads = [bytes.fromhex(p) for p in _load_input_json(text)]
        password = getpass.getpass("Management password: ")
        if args.mqtt:
            reports = mqtt_reports(messages, password)
        else:
            reports = [decode_report(payloads, password)]
        if administrator is not None:
            for report in reports:
                match = fingerprint(password, bytes.fromhex(report["radio_id"]), administrator).hex()
                report["matching_acl"] = [entry for entry in report["acl"] if entry["fingerprint"] == match]
        output = json.dumps(reports if args.mqtt else reports[0], indent=2)
    except EOFError:
        parser.error("management password input unavailable; run in an interactive terminal")
    except RecursionError:
        parser.error("input JSON is nested too deeply")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(output)


if __name__ == "__main__":
    main()
