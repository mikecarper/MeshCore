#!/usr/bin/env python3
"""Run actual delayed reply producers and bounded UART/TCP admission."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/companion_delayed_reply_delivery/test.cpp"
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])


def production_methods(source, base, packet, dispatcher):
    methods = "\n".join(extract_braced(source, signature) for signature in (
        "void MyMesh::writeErrFrame(", "size_t MyMesh::writePendingSerialFrame(",
        "void MyMesh::onContactResponse(", "bool MyMesh::onContactPathRecv(",
        "void MyMesh::onTraceRecv(", "void MyMesh::clearPendingReqs(",
        "bool MyMesh::hasPendingReqs(", "bool MyMesh::beginPendingRequest(",
        "bool MyMesh::allowRequestTag(", "bool MyMesh::allocateRequestTag(",
        "void MyMesh::armPendingRequest(",
        "void MyMesh::finishPendingRequest(",
        "void MyMesh::abandonPendingRequest(", "void MyMesh::servicePendingSerialReply(",
        "void MyMesh::servicePendingSerialReply(uint32_t now)",
        "void MyMesh::clearBinaryTraceReply(", "void MyMesh::serviceBinaryTraceReply(",
        "void MyMesh::serviceBinaryTraceReply(uint32_t now)",
        "void MyMesh::cancelSerialOperationsForRoute(",
        "bool MyMesh::hasFiniteDelayedReplyForRoute(",
    ))
    terminal = "\n".join(extract_braced(source, signature) for signature in (
        "void MyMesh::clearTerminalLogin(", "void MyMesh::serviceTerminalLogin(",
        "void MyMesh::clearTerminalLogin(uint32_t now)",
        "void MyMesh::sendTerminalLogin(", "void MyMesh::printTerminalSendStatus(",
        "void MyMesh::serviceTerminalLogin(uint32_t now)",
        "void MyMesh::clearTerminalTrace(", "void MyMesh::serviceTerminalTrace(",
        "void MyMesh::clearTerminalTrace(uint32_t now)",
        "void MyMesh::serviceTerminalTrace(uint32_t now)",
        "void MyMesh::sendTerminalTraceRoute(",
    ))
    methods += "\n#if COMPANION_FEATURE_TEXT_TERMINAL\n" + terminal + "\n#endif\n"
    methods += "\n" + "\n".join(extract_braced(base, signature) for signature in (
        "bool BaseChatMesh::onContactPathRecv(", "int BaseChatMesh::sendContactPacket(",
        "int BaseChatMesh::sendLogin(",
        "bool BaseChatMesh::allocateRequestTag(",
        "int BaseChatMesh::sendAnonReq(",
        "int  BaseChatMesh::sendRequest(const ContactInfo& recipient, const uint8_t*",
        "int  BaseChatMesh::sendRequest(const ContactInfo& recipient, uint8_t",
    ))
    methods += "\n" + "\n".join(
        extract_braced(dispatcher, signature).replace("Dispatcher::", "MyMesh::")
        for signature in ("bool Dispatcher::millisHasNowPassed(",
                          "unsigned long Dispatcher::futureMillis(")
    )
    methods += "\nnamespace mesh {\n" + "\n".join(extract_braced(packet, signature)
        for signature in ("bool Packet::isValidPathLen(", "size_t Packet::writePath(")) + "\n}\n"
    # Execute real request guards and command branches, excluding unrelated
    # firmware features. Packet allocation/transmission are counted boundaries.
    handler = extract_braced(source, "void MyMesh::handleCmdFrame(")
    prefix = handler[handler.index("{") + 1:handler.index("  if (cmd_frame[0] == CMD_DEVICE_QUERY")]
    branches = []
    for command in ("LOGIN", "ANON_REQ", "STATUS_REQ", "PATH_DISCOVERY_REQ",
                    "TELEMETRY_REQ", "BINARY_REQ", "TRACE_PATH"):
        block = extract_braced(source, "} else if (cmd_frame[0] == CMD_SEND_" + command)
        branches.append(block.replace("} else if", "if", 1) + "\n")
    methods += "\nvoid MyMesh::handleRequestFrame(size_t len) {\n" + prefix + "\n".join(branches) + "\n}\n"
    return methods


def production_delayed_reply_inputs(work):
    source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
    base = (ROOT / "src/helpers/BaseChatMesh.cpp").read_text()
    packet = (ROOT / "src/Packet.cpp").read_text()
    dispatcher = (ROOT / "src/Dispatcher.cpp").read_text()
    native = (ROOT / "test/test_serial_mode_switch/test_serial_mode_switch.cpp").read_text()
    core = (ROOT / "src/MeshCore.h").read_text()
    constants = "\n".join(re.findall(
        r"^#define (?:PUSH_CODE_\w+|RESP_CODE_\w+|CMD_SEND_\w+|ERR_CODE_\w+|REQ_TYPE_GET_TELEMETRY_DATA)\s+.+$",
        source, re.MULTILINE))
    wanted = ("PUB_KEY_SIZE", "MAX_PACKET_PAYLOAD", "MAX_PATH_SIZE", "OUT_PATH_UNKNOWN",
              "MSG_SEND_FAILED", "MSG_SEND_SENT_DIRECT", "MSG_SEND_SENT_FLOOD",
              "PAYLOAD_TYPE_RESPONSE", "PAYLOAD_TYPE_REQ", "PAYLOAD_TYPE_ANON_REQ",
              "PAYLOAD_TYPE_ACK", "REQ_TYPE_GET_STATUS", "REQ_TYPE_GET_TELEMETRY_DATA",
              "TELEM_PERM_BASE", "ADV_TYPE_NONE", "ADV_TYPE_ROOM", "RESP_SERVER_LOGIN_OK")
    for relative in ("examples/companion_radio/MyMesh.h", "src/MeshCore.h", "src/Packet.h", "src/helpers/BaseChatMesh.h",
                     "src/helpers/ContactInfo.h", "src/helpers/AdvertDataHelpers.h",
                     "src/helpers/SensorManager.h"):
        text = (ROOT / relative).read_text()
        constants += "\n" + "\n".join(re.findall(
            r"^#define (?:" + "|".join(wanted) + r")\s+.+$", text, re.MULTILINE))
    constants += "\n#define EXPECTED_ACK_TABLE_SIZE 2\n"
    clock = "\n".join(extract_braced(core, signature) for signature in (
        "uint32_t getCurrentTimeUnique()", "void resetUniqueTime(uint32_t time)"))
    # printf is an Arduino Print operation absent from the lean shared
    # host mock. Add only this I/O boundary, leaving transport logic real.
    stream = (ROOT / "test/mocks/Stream.h").read_text()
    stream = "#include <cstdarg>\n#include <cstdio>\n" + stream.replace(
        "public:", "public:\n    size_t printf(const char* format, ...) {\n"
        "      char text[256]; va_list args; va_start(args, format);\n"
        "      int n=vsnprintf(text,sizeof(text),format,args); va_end(args);\n"
        "      return n>0 ? write((const uint8_t*)text, (size_t)n<sizeof(text) ? (size_t)n : sizeof(text)-1) : 0;\n"
        "    }", 1)
    (work / "production_constants.inc").write_text(constants, encoding="ascii")
    (work / "production_clock.inc").write_text(clock, encoding="ascii")
    (work / "production_stream.inc").write_text(
        extract_braced(native, "class BufferStream") + ";\n", encoding="ascii")
    (work / "production_replies.inc").write_text(
        production_methods(source, base, packet, dispatcher), encoding="ascii")
    (work / "Stream.h").write_text(stream, encoding="ascii")
    (work / "Arduino.h").write_text(
        (ROOT / "test/mocks/Arduino.h").read_text(), encoding="ascii")


class CompanionDelayedReplyDeliveryTests(unittest.TestCase):
    def test_actual_producers_routes_deadlines_and_required_admission(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-delayed-replies-") as directory:
            work = Path(directory)
            production_delayed_reply_inputs(work)
            for terminal in (0, 1):
                with self.subTest(terminal=terminal):
                    binary = work / f"delayed-{terminal}.exe"
                    compiled = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", "-Wno-sign-compare", "-Wno-class-memaccess", *SANITIZERS,
                        f"-DCOMPANION_FEATURE_TEXT_TERMINAL={terminal}", "-DMESH_ENABLE_ONE_KEY_DM=0",
                        f"-I{work}", f"-I{ROOT / 'test/mocks'}",
                        f"-I{ROOT / 'test/fixtures/serial_wifi_sessions/mocks'}", f"-I{ROOT / 'src'}",
                        str(FIXTURE), str(ROOT / "src/helpers/CompanionDelayedReplies.cpp"),
                        str(ROOT / "src/helpers/ArduinoSerialInterface.cpp"),
                        str(ROOT / "src/helpers/wifi/SerialWifiInterface.cpp"), "-o", str(binary),
                        str(ROOT / "src/helpers/TxtDataHelpers.cpp"),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("PASS: delayed production reply delivery", checked.stdout)


if __name__ == "__main__":
    unittest.main()
