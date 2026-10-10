#!/usr/bin/env python3
"""Run actual Sensor response handlers, history aggregation and packet builders."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/sensor_reply_bounds/test_sensor_reply_bounds.cpp"


def production_methods(transform=None, room_transform=None):
    source = (ROOT / "examples/simple_sensor/SensorMesh.cpp").read_text()
    if transform:
        source = transform(source)
    handler = extract_braced(source, "uint8_t SensorMesh::handleRequest(")
    begin = handler.index("  if (req_type == REQ_TYPE_GET_TELEMETRY_DATA")
    generated = extract_braced(source, "static uint8_t getTelemetryPermissions(") + "\n"
    if "static size_t boundedTelemPrefix(" in source:
        generated += extract_braced(source, "static size_t boundedTelemPrefix(") + "\n"
    generated += handler[:begin]
    for signature in ("if (req_type == REQ_TYPE_GET_TELEMETRY_DATA", "if (req_type == REQ_TYPE_GET_AVG_MIN_MAX"):
        generated += extract_braced(handler, signature) + "\n"
    generated += "return 0;\n}\n"
    room = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
    if room_transform:
        room = room_transform(room)
    room_handler = extract_braced(room, "int MyMesh::handleRequest(")
    room_begin = room_handler.index("  if (payload[0] == REQ_TYPE_GET_STATUS)")
    generated += "#undef REQ_TYPE_GET_TELEMETRY_DATA\n#define REQ_TYPE_GET_TELEMETRY_DATA ROOM_REQ_TYPE_GET_TELEMETRY_DATA\n"
    generated += room_handler[:room_begin]
    generated += extract_braced(room_handler, "if (payload[0] == REQ_TYPE_GET_TELEMETRY_DATA)") + "\nreturn 0;\n}\n"
    generated += "#undef REQ_TYPE_GET_TELEMETRY_DATA\n#define REQ_TYPE_GET_TELEMETRY_DATA SENSOR_REQ_TYPE_GET_TELEMETRY_DATA\n"
    history = (ROOT / "examples/simple_sensor/TimeSeriesData.cpp").read_text()
    generated += extract_braced(history, "void TimeSeriesData::calcMinMaxAvg(") + "\n"
    mesh = (ROOT / "src/Mesh.cpp").read_text()
    generated += "namespace mesh {\n#define MAX_COMBINED_PATH (MAX_PACKET_PAYLOAD - 2 - CIPHER_BLOCK_SIZE)\n"
    for signature in ("Packet* Mesh::createPathReturn(const Identity& dest,",
                      "Packet* Mesh::createPathReturn(const uint8_t* dest_hash,", "Packet* Mesh::createDatagram("):
        generated += extract_braced(mesh, signature) + "\n"
    packet = (ROOT / "src/Packet.cpp").read_text()
    for signature in ("Packet::Packet()", "bool Packet::isValidPathLen("):
        generated += extract_braced(packet, signature) + "\n"
    return generated + "}\n"


def production_capacities(sensor_transform=None, room_transform=None):
    definitions = []
    for role, path, transform in (
        ("SENSOR", "examples/simple_sensor/SensorMesh.cpp", sensor_transform),
        ("ROOM", "examples/simple_room_server/MyMesh.cpp", room_transform),
    ):
        source = (ROOT / path).read_text()
        if transform:
            source = transform(source)
        matches = re.findall(r"\btelemetry\(([^)\n]+)\)", source)
        if len(matches) != 1:
            raise AssertionError("one actual telemetry constructor required: " + path)
        definitions.append("#define " + role + "_TELEMETRY_CAPACITY (" + matches[0] + ")")
        opcode = re.search(r"#define REQ_TYPE_GET_TELEMETRY_DATA\s+(0x[0-9a-fA-F]+)", source).group(1)
        definitions.append("#define " + role + "_REQ_TYPE_GET_TELEMETRY_DATA " + opcode)
    return "\n".join(definitions) + "\n"


def production_telemetry_access():
    source = (ROOT / "src/helpers/CommonCLI.h").read_text()
    definitions = []
    for mode in ("ALL", "ACL"):
        matches = re.findall(r"^#define TELEMETRY_ACCESS_" + mode + r"\s+[0-9]+\s*$", source, re.M)
        if len(matches) != 1:
            raise AssertionError("one production telemetry access mode required: " + mode)
        definitions.append(matches[0].strip())
    return "\n".join(definitions) + "\n"


class SensorReplyBoundsTest(unittest.TestCase):
    def execute(self, case, transform=None, room_transform=None):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++17 compiler required")
        with tempfile.TemporaryDirectory(prefix="sensor-reply-bounds-") as directory:
            work = Path(directory)
            (work / "telemetry_capacity.inc").write_text(production_capacities(transform, room_transform), encoding="ascii")
            (work / "telemetry_access.inc").write_text(production_telemetry_access(), encoding="ascii")
            (work / "production.inc").write_text(production_methods(transform, room_transform), encoding="ascii")
            history_types = (ROOT / "examples/simple_sensor/TimeSeriesData.h").read_text()
            history_types = "\n".join(line for line in history_types.splitlines() if not line.startswith("#"))
            (work / "history_types.inc").write_text(history_types, encoding="ascii")
            binary = work / "sensor.exe"
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-reorder",
                       "-I" + str(work), "-I" + str(ROOT / "src"), str(FIXTURE), "-o", str(binary)]
            if sys.platform.startswith("linux"):
                command[1:1] = ["-fsanitize=address,undefined,float-cast-overflow", "-fno-sanitize-recover=all",
                                "-fno-pie", "-no-pie"]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            return subprocess.run([str(binary), case], capture_output=True, text=True, timeout=30)

    def check_case(self, case):
        run = self.execute(case)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Sensor response bounds and packet admission passed", run.stdout)

    def test_room_telemetry_entries_fit_all_routes_and_preserve_composites(self):
        self.check_case("room")

    def test_room_board_short_response_never_returns_a_tag_only_reply(self):
        self.check_case("room.board")

    def test_old_room_board_tag_only_response_is_rejected(self):
        def old(source):
            return source.replace("return length ? 4 + length : 0;", "return 4 + length;", 1)
        self.assertNotEqual(self.execute("room.board", room_transform=old).returncode, 0)

    def test_old_room_unbounded_copy_is_rejected(self):
        def old(source):
            begin = source.index("    const uint8_t* tbuf = telemetry.getBuffer();")
            end = source.index("    return 4 + tlen; // reply_len", begin) + len("    return 4 + tlen; // reply_len")
            return source[:begin] + """    uint8_t tlen = telemetry.getSize();
    memcpy(&reply_data[4], telemetry.getBuffer(), tlen);
    return 4 + tlen; // reply_len""" + source[end:]
        self.assertNotEqual(self.execute("room", room_transform=old).returncode, 0)

    def test_telemetry_entries_fit_every_valid_direct_and_flood_route(self):
        self.check_case("telemetry")

    def test_history_entries_types_counts_and_empty_query_are_safe(self):
        self.check_case("history")

    def test_packet_builder_accepts_maximum_telemetry_collection(self):
        self.check_case("packet")

    def test_empty_history_query_has_no_synthetic_zero_values(self):
        self.check_case("empty")

    def test_telemetry_permission_modes_match_current_prefs(self):
        self.check_case("permissions")

    def test_old_telemetry_copy_exceeds_route_admission(self):
        def old(source):
            begin = source.index("    const uint8_t* tbuf = telemetry.getBuffer();")
            end = source.index("    return 4 + tlen;  // reply_len", begin) + len("    return 4 + tlen;  // reply_len")
            return source[:begin] + """    uint8_t tlen = telemetry.getSize();
    memcpy(&reply_data[4], telemetry.getBuffer(), tlen);
    return 4 + tlen;  // reply_len""" + source[end:]
        self.assertNotEqual(self.execute("telemetry", old).returncode, 0)

    def test_old_history_route_bounds_are_rejected(self):
        def old(source):
            return source.replace("      if (item_len > reply_capacity - ofs) break;", "      (void)item_len;", 1)
        self.assertNotEqual(self.execute("history", old).returncode, 0)

    def test_old_history_composite_types_are_rejected(self):
        def old(source):
            return source.replace("!LPPData::isScalarType(d->_lpp_type)\n          || ", "", 1)
        self.assertNotEqual(self.execute("history", old).returncode, 0)

    def test_old_history_provider_count_is_rejected(self):
        def old(source):
            return source.replace("    if (n < 0 || n > int(sizeof(data) / sizeof(data[0]))) return 0;", "", 1)
        self.assertNotEqual(self.execute("history", old).returncode, 0)

    def test_old_empty_history_nonfinite_conversion_is_rejected(self):
        def old(source):
            return source.replace("\n          || !isfinite(d->_min) || !isfinite(d->_max) || !isfinite(d->_avg)", "", 1)
        self.assertNotEqual(self.execute("history", old).returncode, 0)

    def test_old_history_short_response_header_is_rejected(self):
        def old(source):
            return source.replace("    if (reply_capacity < 8) return 0;  // tag and current timestamp", "", 1)
        self.assertNotEqual(self.execute("history", old).returncode, 0)


if __name__ == "__main__":
    unittest.main()
