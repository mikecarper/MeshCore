#!/usr/bin/env python3
"""Execute production contact mutations and ACK ownership under sanitizers."""

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
FIXTURE = ROOT / "test/fixtures/companion_contact_lifetime"
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])


class CompanionContactLifetimeTests(unittest.TestCase):
    def test_production_mutations_and_owned_ack(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        base = (ROOT / "src/helpers/BaseChatMesh.cpp").read_text()
        base_header = (ROOT / "src/helpers/BaseChatMesh.h").read_text()
        constructor = extract_braced(base_header, "BaseChatMesh(mesh::Radio&")
        constructor_body = constructor[constructor.index("{") + 1:-1]
        packet = (ROOT / "src/Packet.cpp").read_text()
        identity = (ROOT / "src/Identity.cpp").read_text()
        dispatcher = (ROOT / "src/Dispatcher.cpp").read_text()
        native = (ROOT / "test/test_serial_mode_switch/test_serial_mode_switch.cpp").read_text()
        functions = "\n".join(extract_braced(base, signature) for signature in (
            "static ContactInfo* findContactByPubKey(",
            "void BaseChatMesh::resetContactValue(",
            "bool BaseChatMesh::initializeContactStorage(",
            "void BaseChatMesh::populateContactFromAdvert(",
            "void BaseChatMesh::onAdvertRecv(",
            "ContactInfo* BaseChatMesh::lookupContactByPubKey(",
            "ContactInfo* BaseChatMesh::lookupTransientContactByPubKey(",
            "ContactInfo* BaseChatMesh::lookupPersistentContactByPubKey(",
            "bool BaseChatMesh::isTransientContact(",
            "bool BaseChatMesh::clearTransientContact(",
            "bool BaseChatMesh::addContact(",
            "bool BaseChatMesh::removeContact(",
            "bool BaseChatMesh::checkConnectionsAck(",
            "bool BaseChatMesh::onContactPathRecv(",
            "void BaseChatMesh::onAckRecv(",
        ))
        # Both preprocessor alternatives contain the same opening brace. Keep
        # the complete real definition, rather than selecting a copied branch.
        functions += "\n" + base[base.index("ContactInfo* BaseChatMesh::allocateContactSlot("):
                                  base.index("void BaseChatMesh::populateContactFromAdvert(")]
        functions += "\nvoid BaseChatMesh::" + extract_braced(base_header, "void resetContacts() {")[5:]
        functions += "\n" + extract_braced(
            (ROOT / "src/helpers/TxtDataHelpers.cpp").read_text(), "void StrHelper::strncpy(")
        functions += "\n" + "\n".join(extract_braced(companion, signature) for signature in (
            "void MyMesh::clearExpectedAck(", "void MyMesh::expireExpectedAcks(",
            "bool MyMesh::processAck(", "bool MyMesh::onContactOverwrite(",
            "bool MyMesh::updateContactFromFrame(",
        ))
        functions += "\n" + extract_braced(companion, "void MyMesh::onContactReferenceChanged(").replace(
            "MyMesh::onContactReferenceChanged(", "MyMesh::applyContactReferenceChanged(", 1)
        functions += "\n" + "\n".join(extract_braced(dispatcher, signature).replace(
            "Dispatcher::", "BaseChatMesh::") for signature in (
                "bool Dispatcher::millisHasNowPassed(", "unsigned long Dispatcher::futureMillis("))
        functions += "\nvoid MyMesh::service() {\n" + extract_braced(companion, "if (has_next_ack_expiry\n") + "\n}\n"
        # Execute the actual admin branches. Framing/authentication is already
        # covered separately; the real parser and mutation/rollback are retained.
        functions += "\nvoid MyMesh::handleContactCommand(int len) { if (false) {}\n"
        functions += extract_braced(companion, "else if (cmd_frame[0] == CMD_ADD_UPDATE_CONTACT)")
        functions += "\n" + extract_braced(companion, "else if (cmd_frame[0] == CMD_REMOVE_CONTACT &&") + "\n}\n"
        verified = extract_braced(companion, "void MyMesh::onAnonDataRecv(")
        tail_start = verified.index("  if (!canMutateContacts()) return;")
        tail_end = verified.rindex("\n}")
        functions += "\nvoid MyMesh::acceptVerifiedOneKey(const mesh::Identity& sender) {\n"
        functions += "  ContactInfo contact = makeContact(sender.pub_key[0]);\n"
        functions += verified[tail_start:tail_end] + "\n}\n"
        constants = "\n".join(re.findall(
            r"^#define (?:EXPECTED_ACK_[A-Z_]+|PUSH_CODE_SEND_CONFIRMED|PUSH_CODE_CONTACT_DELETED|"
            r"CMD_ADD_UPDATE_CONTACT|CMD_REMOVE_CONTACT|ERR_CODE_[A-Z_]+)\s+.+$", companion, re.MULTILINE))
        constants += "\n#define EXPECTED_ACK_TABLE_SIZE 8\n"
        first = companion.index("static constexpr int CONTACT_UPDATE_FRAME_MIN_LEN")
        last = companion.index("bool MyMesh::updateContactFromFrame(", first)
        constants += "\n" + companion[first:last]
        constructors = "namespace mesh {\n" + "\n".join(
            extract_braced(identity, signature) for signature in (
                "Identity::Identity()", "LocalIdentity::LocalIdentity()")) + "\n"
        constructors += "\n".join(extract_braced(packet, signature) for signature in (
            "Packet::Packet()", "bool Packet::isValidPathLen(",
            "size_t Packet::writePath(", "uint8_t Packet::copyPath(",
            "uint8_t Packet::writeTo(")) + "\n}\n"
        variants = (("generic", 0, 0, 0), ("generic", 1, 1, 1),
                    ("nrf52", 0, 1, 0), ("nrf52", 1, 1, 1),
                    ("psram", 0, 0, 0), ("psram", 0, 1, 1),
                    ("psram", 1, 0, 1), ("psram", 1, 1, 0))
        with tempfile.TemporaryDirectory(prefix="meshcore-contact-lifetime-") as directory:
            work = Path(directory)
            (work / "production_constants.inc").write_text(constants, encoding="ascii")
            (work / "production_types.inc").write_text(
                base_header[base_header.index("#if MAX_CONTACTS + MAX_ANON_CONTACTS <= 65535"):
                            base_header.index("#ifndef MAX_CONNECTIONS")]
                + "namespace mesh {\n" + extract_braced((ROOT / "src/Mesh.h").read_text(), "class GroupChannel")
                + ";\n}\n" + extract_braced((ROOT / "src/helpers/ChannelDetails.h").read_text(), "struct ChannelDetails")
                + ";\n" + extract_braced(base_header, "struct ConnectionInfo") + ";\n", encoding="ascii")
            (work / "production_entry.inc").write_text(
                extract_braced(header, "struct AckTableEntry") + ";\n", encoding="ascii")
            (work / "production_stream.inc").write_text(
                extract_braced(native, "class BufferStream") + ";\n", encoding="ascii")
            (work / "production_functions.inc").write_text(functions, encoding="ascii")
            (work / "production_constructors.inc").write_text(constructors, encoding="ascii")
            (work / "production_base_constructor.inc").write_text(constructor_body, encoding="ascii")
            for platform, cache, terminal, one_key in variants:
                with self.subTest(platform=platform, cache=cache, terminal=terminal, one_key=one_key):
                    defines = [f"-DMESH_CONTACT_CACHE={cache}",
                               f"-DCOMPANION_FEATURE_TEXT_TERMINAL={terminal}",
                               f"-DMESH_ENABLE_ONE_KEY_DM={one_key}"]
                    if terminal:
                        defines.append("-DMAX_GROUP_CHANNELS=4")
                    if platform == "nrf52":
                        defines.append("-DNRF52_PLATFORM=1")
                    elif platform == "psram":
                        defines += ["-DESP32_PLATFORM=1", "-DBOARD_HAS_PSRAM=1"]
                    binary = work / f"lifetime-{platform}-{cache}-{terminal}-{one_key}.exe"
                    compiled = subprocess.run([
                        compiler, "-std=c++17", "-Werror", *SANITIZERS, *defines,
                        f"-I{work}", f"-I{FIXTURE / 'mocks'}", f"-I{ROOT / 'test/mocks'}",
                        f"-I{ROOT / 'src'}", str(FIXTURE / "test.cpp"),
                        str(ROOT / "src/helpers/ArduinoSerialInterface.cpp"),
                        str(ROOT / "src/helpers/AdvertDataHelpers.cpp"),
                        "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    expected_checks = 37 if platform == "psram" else 34
                    self.assertIn(f"PASS: {expected_checks} production contact lifetime checks", checked.stdout)


if __name__ == "__main__":
    unittest.main()
