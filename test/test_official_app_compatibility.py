#!/usr/bin/env python3
"""Execute pinned stock app code against actual firmware ACL/radio outputs.

The public web build is the source witness, not proof of the installed iOS app.
The whole app remains outside Git. A changed upstream bundle fails its SHA gate;
an explicit source-pin update is required, never silent parser replacement.
No PlatformIO, devices, app fork, settings writes or real radio are involved.
"""

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request

from test_client_acl_response import production_acl_methods
from test_companion_primary_radio_persistence import (
    prepare_profile_metadata_boundary, production_primary_radio_harness)
from test_companion_delayed_reply_delivery import production_delayed_reply_inputs
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
APP_URL = "https://app.meshcore.nz/main.dart.js"
APP_SHA256 = "84bc39a950735aaffa93d912556852a4c22e235d0cc4e4d04790113207f13a68"
APP_BYTES = 9494192
JS = ROOT / "test/fixtures/official_app_compatibility/app_contract.js"
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])


def official_app_bundle():
    explicit = os.environ.get("MESHCORE_OFFICIAL_APP_BUNDLE")
    path = Path(explicit) if explicit else (
        Path.home() / ".cache/meshcore-tests" / APP_SHA256 / "main.dart.js")
    if not path.exists():
        if explicit:
            raise AssertionError(f"Missing explicit official app bundle: {path}")
        with urllib.request.urlopen(APP_URL, timeout=45) as response:
            data = response.read(APP_BYTES + 1)
        if len(data) != APP_BYTES or hashlib.sha256(data).hexdigest() != APP_SHA256:
            raise AssertionError("Official app changed: obtain the pinned cached build or review/update its source pin")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    data = path.read_bytes()
    if len(data) != APP_BYTES or hashlib.sha256(data).hexdigest() != APP_SHA256:
        raise AssertionError(f"Official app source SHA/size mismatch: {path}")
    return path.resolve()


def checked(command, timeout=60):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout


def acl_outputs(work, compiler, request_plaintext):
    source = production_acl_methods()
    packet = (ROOT / "src/Packet.cpp").read_text()
    source += "namespace mesh {\n" + "\n".join(extract_braced(packet, signature) for signature in (
        "size_t Packet::writePath(", "int Packet::getRawLength() const",
        "uint8_t Packet::writeTo(uint8_t dest[]) const")) + "\n}\n"
    (work / "production.inc").write_text(source, encoding="ascii")
    fixture = (ROOT / "test/fixtures/client_acl_response/test_client_acl_response.cpp").read_text()
    fixture = fixture[:fixture.index("int main()")]
    request = ",".join(str(byte) for byte in bytes.fromhex(request_plaintext))
    fixture += r'''
int main() {
  uint8_t plaintext[] = {APP_QUERY};
  static_assert(sizeof(plaintext) == 11, "Actual Companion tag plus stock seven-byte query");
  uint32_t tag; memcpy(&tag, plaintext, 4);
  for (bool flood : {false, true}) for (uint8_t path_len : {0, 0x60})
      for (unsigned clients : {0, 1, 22, 23, 24, 25, 32, 256}) {
    MyMesh target; fill(target.acl, clients);
    target.sender.last_timestamp = tag - 1;
    mesh::Packet request; request.header = flood ? ROUTE_TYPE_FLOOD : ROUTE_TYPE_DIRECT;
    request.path_len = path_len;
    uint8_t secret[PUB_KEY_SIZE] = {};
    auto* transmitted = target.createDatagram(PAYLOAD_TYPE_REQ, target.sender.id,
                                             secret, plaintext, sizeof(plaintext));
    // Radio send boundaries set the route bits after packet construction.
    assert(transmitted != nullptr); transmitted->header |= ROUTE_TYPE_DIRECT;
    assert(transmitted && transmitted->payload_len == 20 && transmitted->getRawLength() == 22);
    target.receive(&request, plaintext, sizeof(plaintext));
    assert(target.queued == 1 && target.sender.last_timestamp == tag);
    auto* reply = target.last_reply; assert(reply != nullptr);
    reply->header |= flood ? ROUTE_TYPE_FLOOD : ROUTE_TYPE_DIRECT;
    const unsigned capacity = mesh::clientACLReplyCapacity(flood, path_len);
    const unsigned entries = std::min(clients, (capacity - 4) / 7);
    const unsigned route_bytes = (path_len & 63) * ((path_len >> 6) + 1);
    const size_t body_offset = 2 * PATH_HASH_SIZE + CIPHER_MAC_SIZE
        + (flood ? 2 + route_bytes : 0);
    if (!flood && clients == 1) {
      assert(sizeof(plaintext) == 11 && reply->payload_len == 20 && reply->getRawLength() == 22);
      uint8_t wire[MAX_PACKET_PAYLOAD + MAX_PATH_SIZE + 6];
      assert(reply->writeTo(wire) == 22); // 11 plaintext -> 16 cipher -> 20 payload -> 22 wire.
    }
    print_legacy(reply->payload + body_offset, entries, reply->payload_len - body_offset);
  }
}
'''.replace("APP_QUERY", request)
    (work / "acl.cpp").write_text(fixture, encoding="ascii")
    binary = work / "acl.exe"
    checked([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
             "-Wno-unused-parameter", "-Wno-unused-function", *SANITIZERS,
             f"-I{work}", f"-I{ROOT / 'src'}", str(work / "acl.cpp"), "-o", str(binary)])
    output = checked([str(binary)], timeout=20)
    records = []
    for line in output.splitlines():
        if line.startswith("LEGACY:"):
            _, entries, plaintext = line.split(":")
            records.append({"entries": int(entries), "plaintext": plaintext})
    if len(records) != 32:
        raise AssertionError(output)
    return records


def companion_pipeline(work, compiler, command):
    production_delayed_reply_inputs(work)
    fixture = (ROOT / "test/fixtures/companion_delayed_reply_delivery/test.cpp").read_text()
    fixture = fixture[:fixture.index("int main()")]
    fixture += r'''
static Bytes fromHex(const char* text) {
  Bytes bytes;
  for (size_t at = 0; text[at]; at += 2) {
    unsigned byte; assert(text[at + 1]); assert(sscanf(text + at, "%2x", &byte) == 1);
    bytes.push_back(uint8_t(byte));
  }
  return bytes;
}
static void printHex(const char* prefix, const Bytes& bytes) {
  printf("%s:", prefix); for (uint8_t byte : bytes) printf("%02x", byte); printf("\n");
}
int main(int argc, char** argv) {
  assert(argc == 2 || argc == 4);
  const Bytes command = fromHex(argv[1]); assert(command.size() == 40);
  Fixture f; f.mesh.direct_timeout = 2000;
  memcpy(f.mesh.recipient.id.pub_key, command.data() + 1, 32);
  // Access Control is opened after login. A synchronized repeater can return
  // the timestamp the Companion would otherwise choose for this app query.
  f.command(Tracker::Login);
  const uint32_t server_tag = f.mesh.rtc.wall + 1;
  reply(f, Tracker::Login, nullptr, server_tag); f.drain();
  assert(count(f.wire(), PUSH_CODE_LOGIN_SUCCESS) == 1);
  f.usb_stream.output.clear();
  memcpy(f.mesh.cmd_frame, command.data(), command.size());
  f.mesh.handleRequestFrame(command.size());
  const auto& payload = f.mesh.transmitted_payload;
  assert(payload.size() == 11 && !memcmp(payload.data() + 4, command.data() + 33, 7));
  uint32_t tag; memcpy(&tag, payload.data(), 4);
  assert(tag == slot(f, Tracker::Binary).tag && !slot(f, Tracker::Binary).sent_pending);
  assert(tag > server_tag && f.mesh.rtc.wall == 1000);
  reply(f, Tracker::Login, nullptr, server_tag);
  assert(slot(f, Tracker::Binary).phase == Tracker::AwaitRadio);
  if (argc == 2) { printHex("REQUEST", payload); return 0; }
  Bytes radio = fromHex(argv[2]);
  if (atoi(argv[3]) == 0) {
    // The old 23-row direct reply was 165 bytes before zero padding. The
    // decrypted 176 bytes exceed Companion's 174-byte callback envelope.
    radio.assign(176, 0); memcpy(radio.data(), &tag, 4);
    for (unsigned entry = 0; entry < 23; ++entry) {
      for (unsigned byte = 0; byte < 6; ++byte) radio[4 + entry * 7 + byte] = entry + byte + 1;
      radio[4 + entry * 7 + 6] = 3;
    }
  }
  assert(radio.size() <= 255 && !memcmp(radio.data(), &tag, 4));
  f.otherRequest(); g_mock_millis += 3000;
  f.mesh.onContactResponse(f.mesh.recipient, radio.data(), uint8_t(radio.size())); f.drain();
  const bool admitted = atoi(argv[3]) != 0;
  assert(count(f.wire(), PUSH_CODE_BINARY_RESPONSE) == (admitted ? 1U : 0U));
  assert(count(f.wire(), RESP_CODE_SENT) == 1 && f.other_stream.output.empty());
  if (admitted) {
    assertSentBeforeFinal(f.wire(), Tracker::Binary, tag);
    assert(frameWith(f.wire(), PUSH_CODE_BINARY_RESPONSE).size() <= MAX_FRAME_SIZE);
  } else assert(radio.size() == 176);
  for (const auto& frame : frames(f.wire())) printHex("FRAME", frame);
}
'''
    (work / "companion.cpp").write_text(fixture, encoding="ascii")
    binary = work / "companion.exe"
    checked([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-sign-compare", "-Wno-class-memaccess",
        *SANITIZERS, "-DCOMPANION_FEATURE_TEXT_TERMINAL=0", "-DMESH_ENABLE_ONE_KEY_DM=0",
        f"-I{work}", f"-I{ROOT / 'test/mocks'}",
        f"-I{ROOT / 'test/fixtures/serial_wifi_sessions/mocks'}", f"-I{ROOT / 'src'}",
        str(work / "companion.cpp"), *[str(ROOT / "src/helpers" / name) for name in (
            "CompanionDelayedReplies.cpp", "ArduinoSerialInterface.cpp",
            "wifi/SerialWifiInterface.cpp", "TxtDataHelpers.cpp")], "-o", str(binary)])
    output = checked([str(binary), command], timeout=20)
    plaintext = output.strip().removeprefix("REQUEST:")
    if len(bytes.fromhex(plaintext)) != 11:
        raise AssertionError(output)

    def deliver(records):
        forwarded = []
        for record in records:
            output = checked([str(binary), command, record["plaintext"], "1"], timeout=20)
            frames = [line.removeprefix("FRAME:") for line in output.splitlines() if line.startswith("FRAME:")]
            if len(frames) != 2:
                raise AssertionError(output)
            forwarded.append({"entries": record["entries"], "sent": frames[0], "frame": frames[1],
                              "body": record["plaintext"][8:]})
        # The unmodified production callback must reject the historical padded
        # 23-entry reply. The app therefore cannot falsely decode this fixture.
        output = checked([str(binary), command, plaintext, "0"], timeout=20)
        rejected = [line.removeprefix("FRAME:") for line in output.splitlines() if line.startswith("FRAME:")]
        if len(rejected) != 1 or bytes.fromhex(rejected[0])[0] != 6:
            raise AssertionError(output)
        return forwarded

    return plaintext, deliver


def radio_outputs(work, compiler):
    harness = production_primary_radio_harness()
    # Execute the same production Companion dispatch, shared preferences parser
    # and CommonCLI infrastructure branch with automatic/explicit preambles.
    begin = harness.index("int main()")
    harness = harness[:begin] + r'''
int main() {
  for (uint32_t timestamp : {0U, 1700000000U}) for (uint16_t preamble : {0U, 48U}) {
    MyMesh node; assert(node._radio_profiles.savePrimaryPreamble(preamble));
    node.radio.p.primary_temporary = true;
    node.radio.p.primary.freq = 915; node.radio.p.primary.preamble = 96;
    node.radio.p.secondary.params = {916, 125, 64, 8, 6};
    node.radio.p.secondary.mode = mesh::RadioProfileMode::RxTx;
    printf("RADIO:%s\n", node.command("get radio", timestamp));
    Infrastructure repeater; char reply[160] = {};
    repeater.radioCommand(reply); printf("RADIO:%s\n", reply);
  }
}
'''
    utils = (ROOT / "test/mocks/Utils.h").read_text().replace("class Utils {\npublic:", """class Utils {
public:
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
""")
    (work / "Utils.h").write_text("#include <Arduino.h>\n#include <cassert>\n" + utils, encoding="ascii")
    (work / "Identity.h").write_text((ROOT / "test/mocks/Identity.h").read_text(), encoding="ascii")
    transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text()
    (work / "ContactFileTransaction.h").write_text(transaction.replace(
        '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'), encoding="ascii")
    prepare_profile_metadata_boundary(work)
    (work / "radio.cpp").write_text(harness, encoding="ascii")
    binary = work / "radio.exe"
    checked([compiler, "-std=c++17", "-Wall", "-Wextra", "-Wno-unused-function",
             "-Wno-unused-parameter", "-Wno-sign-compare", "-Wno-reorder",
             "-DESP32_PLATFORM=1", *SANITIZERS,
             f"-I{work}", f"-I{ROOT / 'test/fixtures'}",
             f"-I{ROOT / 'test/fixtures/radio_profiles/mocks'}",
             f"-I{ROOT / 'test/mocks'}", f"-I{ROOT / 'src'}", f"-I{ROOT / 'src/helpers'}", f"-I{ROOT}",
             str(work / "radio.cpp"), *[str(ROOT / "src/helpers" / name) for name in (
                 "ConfigSerializer.cpp", "DynamicConfigSerializer.cpp", "CommonRadioPrefs.cpp",
                 "TxtDataHelpers.cpp", "RadioProfileCLI.cpp")], "-o", str(binary)])
    output = checked([str(binary)], timeout=20)
    replies = [line.removeprefix("RADIO:") for line in output.splitlines() if line.startswith("RADIO:")]
    if len(replies) != 8:
        raise AssertionError(output)
    return replies


class OfficialAppCompatibilityTests(unittest.TestCase):
    def test_pinned_app_functions_with_production_request_and_outputs(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        node = shutil.which("node")
        self.assertIsNotNone(compiler, "native C++ compiler required")
        self.assertIsNotNone(node, "Node.js required for actual stock app functions")
        bundle = official_app_bundle()
        with tempfile.TemporaryDirectory(prefix="meshcore-official-app-") as directory:
            work = Path(directory)
            config = work / "input.json"
            config.write_text(json.dumps({"bundle": str(bundle), "mode": "build"}), encoding="ascii")
            generated = json.loads(checked([node, str(JS), str(config)], timeout=20))
            plaintext, deliver = companion_pipeline(work, compiler, generated["command"])
            acl = deliver(acl_outputs(work, compiler, plaintext))
            config.write_text(json.dumps({"bundle": str(bundle), "mode": "check",
                "acl": acl,
                "radio": radio_outputs(work, compiler)}), encoding="ascii")
            result = json.loads(checked([node, str(JS), str(config)], timeout=20))
            self.assertEqual(result["acl_cases"], 32)
            self.assertEqual(result["radio_screens"], 3)
            self.assertTrue(result["stock_sent_race_witness"])
            self.assertFalse(result["actual_ios_cause_proven"])


if __name__ == "__main__":
    unittest.main()
